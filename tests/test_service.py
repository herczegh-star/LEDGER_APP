#!/usr/bin/env python3
"""Integrační testy: LedgerService pipeline end-to-end."""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from core.service import LedgerService, ImportResult, OperationResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CSV = str(FIXTURES_DIR / "sample_raw.csv")
DB = "test_service.db"


def setup():
    if os.path.exists(DB):
        os.remove(DB)
    return LedgerService(DB)


def teardown(svc):
    svc.close()
    if os.path.exists(DB):
        os.remove(DB)


def test_import(svc):
    """TEST 1: import_file vrací správný ImportResult."""
    result = svc.import_file(SAMPLE_CSV)
    assert isinstance(result, ImportResult)
    assert result.inserted == 7, f"Expected 7 inserted, got {result.inserted}"
    assert result.skipped == 0
    assert result.parse_errors == []
    assert result.validation_errors == []
    assert isinstance(result.diagnostics, list)
    print(f"  Import: {result.inserted} inserted, {result.skipped} skipped")


def test_import_dedup(svc):
    """TEST 2: Re-import = 0 inserted."""
    result = svc.import_file(SAMPLE_CSV)
    assert result.inserted == 0, f"Expected 0 inserted, got {result.inserted}"
    assert result.skipped == 7
    print(f"  Dedup: 0 inserted, {result.skipped} skipped")


def test_add_row(svc):
    """TEST 3: add_row success."""
    result = svc.add_row(
        timestamp=datetime(2026, 3, 1, 10, 0, 0),
        type_="FEE", asset="EUR", amount=Decimal("-10"),
        currency="EUR", venue="anycoin",
    )
    assert isinstance(result, OperationResult)
    assert result.success is True
    assert result.rows_inserted == 1
    assert result.errors == []
    print(f"  add_row: success, {result.rows_inserted} inserted")


def test_add_row_invalid(svc):
    """TEST 4: add_row s nevalidním type → failure."""
    result = svc.add_row(
        timestamp=datetime(2026, 3, 1, 10, 0, 0),
        type_="INVALID_TYPE", asset="BTC", amount=Decimal("1"),
        currency="EUR", venue="test",
    )
    assert result.success is False
    assert len(result.errors) > 0
    assert svc.count() == 0
    print(f"  add_row invalid: rejected with {len(result.errors)} errors")


def test_add_row_duplicate(svc):
    """TEST 5: Duplicitní add_row → failure."""
    kwargs = dict(
        timestamp=datetime(2026, 3, 1, 10, 0, 0),
        type_="FEE", asset="EUR", amount=Decimal("-10"),
        currency="EUR", venue="anycoin",
    )
    first = svc.add_row(**kwargs)
    assert first.success is True
    second = svc.add_row(**kwargs)
    assert second.success is False
    assert svc.count() == 1
    print(f"  add_row duplicate: second rejected")


def test_add_trade(svc):
    """TEST 6: add_trade success (2 řádky)."""
    result = svc.add_trade(
        timestamp=datetime(2026, 3, 1, 12, 0, 0),
        type_="BUY", asset="BTC", asset_amount=Decimal("0.1"),
        currency="EUR", currency_amount=Decimal("5000"),
        venue="anycoin", price=Decimal("50000"),
    )
    assert result.success is True
    assert result.rows_inserted == 2
    assert svc.count() == 2
    print(f"  add_trade: success, {result.rows_inserted} inserted")


def test_add_trade_invalid_type(svc):
    """TEST 7: add_trade s TRANSFER → failure (jen BUY/SELL)."""
    result = svc.add_trade(
        timestamp=datetime(2026, 3, 1, 12, 0, 0),
        type_="TRANSFER", asset="BTC", asset_amount=Decimal("0.1"),
        currency="EUR", currency_amount=Decimal("5000"),
        venue="anycoin",
    )
    assert result.success is False
    assert svc.count() == 0
    print(f"  add_trade invalid: rejected")


def test_add_reversal_single(svc):
    """TEST 8: Reversal jednoho řádku → balance = 0."""
    svc.add_row(
        timestamp=datetime(2026, 3, 1, 10, 0, 0),
        type_="FEE", asset="EUR", amount=Decimal("-10"),
        currency="EUR", venue="anycoin",
    )
    recent = svc.recent_rows(1)
    pk = recent[0]["pk"]

    result = svc.add_reversal(pk, reverse_pair=False)
    assert result.success is True
    assert result.rows_inserted == 1
    assert svc.count() == 2

    balances = svc.asset_balances()
    assert balances.get("EUR", Decimal("0")) == Decimal("0")
    print(f"  reversal single: balance zeroed")


def test_add_reversal_pair(svc):
    """TEST 9: Reversal double-entry páru → oba balances = 0."""
    svc.add_trade(
        timestamp=datetime(2026, 3, 1, 12, 0, 0),
        type_="BUY", asset="BTC", asset_amount=Decimal("0.1"),
        currency="EUR", currency_amount=Decimal("5000"),
        venue="anycoin",
    )
    recent = svc.recent_rows(2)
    pk = recent[0]["pk"]

    result = svc.add_reversal(pk, reverse_pair=True)
    assert result.success is True
    assert result.rows_inserted == 2
    assert svc.count() == 4

    balances = svc.asset_balances()
    assert balances.get("BTC", Decimal("0")) == Decimal("0")
    assert balances.get("EUR", Decimal("0")) == Decimal("0")
    print(f"  reversal pair: all balances zeroed")


def test_add_reversal_not_found(svc):
    """TEST 10: Reversal neexistujícího pk → failure."""
    result = svc.add_reversal(pk=999, reverse_pair=False)
    assert result.success is False
    assert len(result.errors) > 0
    print(f"  reversal not found: rejected")


def test_empty_queries(svc):
    """TEST 11: Prázdný ledger – všechny query vrací prázdné."""
    assert svc.count() == 0
    assert svc.timeline() == []
    assert svc.asset_balances() == {}
    assert svc.venue_balances() == {}
    assert svc.diagnostics() == []
    assert svc.recent_rows() == []
    print(f"  empty queries: all empty")


def test_diagnostics_in_result(svc):
    """TEST 12: Záporný zůstatek se vrátí v diagnostics výsledku."""
    result = svc.add_row(
        timestamp=datetime(2026, 3, 1, 10, 0, 0),
        type_="FEE", asset="EUR", amount=Decimal("-100"),
        currency="EUR", venue="anycoin",
    )
    assert result.success is True
    eur_warnings = [d for d in result.diagnostics if d["asset"] == "EUR"]
    assert len(eur_warnings) > 0, "EUR negative balance should trigger warning"
    print(f"  diagnostics in result: {len(eur_warnings)} EUR warnings")


def test_decimal_precision(svc):
    """TEST 13: Decimal přesnost přes celý pipeline."""
    svc.add_row(
        timestamp=datetime(2026, 3, 1, 10, 0, 0),
        type_="BUY", asset="BTC", amount=Decimal("0.00000001"),
        currency="EUR", venue="test",
    )
    svc.add_row(
        timestamp=datetime(2026, 3, 1, 10, 0, 1),
        type_="BUY", asset="BTC", amount=Decimal("0.00000002"),
        currency="EUR", venue="test",
    )
    balances = svc.asset_balances()
    assert balances["BTC"] == Decimal("0.00000003"), f"Expected 0.00000003, got {balances['BTC']}"
    print(f"  decimal precision: BTC = {balances['BTC']}")


def run_isolated(name, test_fn, *args):
    """Spustí test s čistým setup/teardown."""
    svc = setup()
    try:
        # Pokud jsou extra args (např. pro dedup), předpřiprav
        test_fn(svc, *args)
        print("  PASS")
    except AssertionError as e:
        print(f"  FAIL: {e}")
        raise
    finally:
        teardown(svc)


def main():
    # Testy 1-2 sdílejí stav (import + dedup)
    print("TEST 1: Import file")
    svc = setup()
    try:
        test_import(svc)
        print("  PASS")

        print("\nTEST 2: Import dedup")
        test_import_dedup(svc)
        print("  PASS")
    finally:
        teardown(svc)

    # Ostatní testy izolované
    tests = [
        ("TEST 3: Add row", test_add_row),
        ("TEST 4: Add row invalid", test_add_row_invalid),
        ("TEST 5: Add row duplicate", test_add_row_duplicate),
        ("TEST 6: Add trade", test_add_trade),
        ("TEST 7: Add trade invalid type", test_add_trade_invalid_type),
        ("TEST 8: Reversal single", test_add_reversal_single),
        ("TEST 9: Reversal pair", test_add_reversal_pair),
        ("TEST 10: Reversal not found", test_add_reversal_not_found),
        ("TEST 11: Empty queries", test_empty_queries),
        ("TEST 12: Diagnostics in result", test_diagnostics_in_result),
        ("TEST 13: Decimal precision", test_decimal_precision),
    ]

    for name, test_fn in tests:
        print(f"\n{name}")
        svc = setup()
        try:
            test_fn(svc)
            print("  PASS")
        except AssertionError as e:
            print(f"  FAIL: {e}")
            teardown(svc)
            raise
        teardown(svc)

    print("\n" + "=" * 60)
    print("  ALL 13 SERVICE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
