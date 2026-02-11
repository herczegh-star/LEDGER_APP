#!/usr/bin/env python3
"""Test: MVP kritéria s self-contained fixtures."""
import sys
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from core.model import RawRow
from core.validator import validate_row, validate_rows
from core.ledger_store import LedgerStore
from io_module.raw_loader import load_raw

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_CSV = str(FIXTURES_DIR / "sample_raw.csv")
DB = "test_ledger.db"


def setup():
    if os.path.exists(DB):
        os.remove(DB)
    return LedgerStore(DB)


def teardown(store):
    store.close()
    if os.path.exists(DB):
        os.remove(DB)


def test_import(store):
    """TEST 1: Import sample CSV."""
    load_result = load_raw(SAMPLE_CSV)
    rows = load_result.rows
    assert len(rows) > 0, f"Musí načíst aspoň 1 řádek, načteno: {len(rows)}"
    assert len(load_result.errors) == 0, f"Žádné parse chyby: {load_result.errors}"

    valid, invalid = validate_rows(rows)
    assert len(invalid) == 0, f"Všechny řádky musí projít validací: {invalid}"

    result = store.import_rows(valid)
    assert result["inserted"] == len(valid), f"Všechny řádky musí být vloženy: {result}"
    print(f"  Importováno {result['inserted']} řádků")
    return valid


def test_dedup(store, valid):
    """TEST 2: Re-import = 0 duplikátů."""
    result = store.import_rows(valid)
    assert result["inserted"] == 0, f"Druhý import nesmí vložit nic: {result}"
    assert result["skipped"] == len(valid)
    print(f"  Deduplikace ověřena: 0 nových řádků při re-importu")


def test_timeline(store):
    """TEST 3: Timeline je seřazená podle timestamp."""
    timeline = store.timeline()
    assert len(timeline) > 0
    timestamps = [r.timestamp for r in timeline]
    assert timestamps == sorted(timestamps), "Timeline musí být seřazená"
    print(f"  Timeline: {len(timeline)} řádků, seřazeno")


def test_asset_view(store):
    """TEST 4: Asset balances – přesné Decimal hodnoty."""
    balances = store.asset_balances()
    assert len(balances) > 0
    assert balances.get("BTC") == Decimal("0.5"), f"BTC balance: {balances.get('BTC')}"
    assert balances.get("ETH") == Decimal("2.0"), f"ETH balance: {balances.get('ETH')}"
    expected_eur = Decimal("-25000") + Decimal("-6000") + Decimal("-5")
    assert balances.get("EUR") == expected_eur, f"EUR balance: {balances.get('EUR')} != {expected_eur}"
    print(f"  Asset balances ověřeny (BTC=0.5, ETH=2.0, EUR={expected_eur})")


def test_venue_view(store):
    """TEST 5: Venue balances."""
    venue_bal = store.venue_balances()
    assert "anycoin" in venue_bal, "anycoin musí být ve venue balances"
    assert "coldwallet" in venue_bal, "coldwallet musí být ve venue balances"
    assert venue_bal["coldwallet"]["BTC"] == Decimal("0.1")
    print(f"  Venue balances ověřeny")


def test_manual_insert(store):
    """TEST 6: Ruční vložení řádku."""
    row = RawRow(
        timestamp=datetime(2026, 2, 10, 22, 0, 0),
        type="BUY", asset="BTC", amount=Decimal("0.005"),
        currency="CZK", price=Decimal("2500000"), venue="anycoin"
    )
    ok, errs = validate_row(row)
    assert ok, f"Validace musí projít: {errs}"
    inserted = store.insert(row)
    assert inserted, "Ruční řádek musí být vložen"
    print(f"  Ruční insert ověřen")
    return row


def test_reversal(store, original_row):
    """TEST 7: REVERSAL tok."""
    reversal = RawRow(
        timestamp=datetime(2026, 2, 10, 22, 5, 0),
        type="REVERSAL", asset="BTC", amount=Decimal("-0.005"),
        currency="CZK", price=Decimal("2500000"), venue="anycoin",
        note=f"reversal of {original_row.id}"
    )
    ok, errs = validate_row(reversal)
    assert ok, f"REVERSAL validace: {errs}"
    inserted = store.insert(reversal)
    assert inserted, "REVERSAL musí být vložen"

    balances = store.asset_balances()
    assert balances.get("BTC") == Decimal("0.5"), f"BTC po reversal: {balances.get('BTC')}"
    print(f"  REVERSAL ověřen")


def test_diagnostics(store):
    """TEST 8: Diagnostika záporných zůstatků."""
    warnings = store.diagnostics()
    eur_warnings = [w for w in warnings if w["asset"] == "EUR"]
    assert len(eur_warnings) > 0, "EUR záporný zůstatek musí vyvolat warning"
    print(f"  Diagnostika: {len(warnings)} varování")


def test_empty_ledger():
    """TEST 9: Prázdný ledger je validní stav."""
    empty_db = "empty_test.db"
    if os.path.exists(empty_db):
        os.remove(empty_db)
    empty = LedgerStore(empty_db)
    assert empty.count() == 0
    assert empty.timeline() == []
    assert empty.asset_balances() == {}
    assert empty.venue_balances() == {}
    assert empty.diagnostics() == []
    empty.close()
    os.remove(empty_db)
    print(f"  Prázdný ledger ověřen")


def main():
    store = setup()
    try:
        print("TEST 1: Import")
        valid = test_import(store)
        print("  PASS")

        print("\nTEST 2: Deduplikace")
        test_dedup(store, valid)
        print("  PASS")

        print("\nTEST 3: Timeline")
        test_timeline(store)
        print("  PASS")

        print("\nTEST 4: Asset view")
        test_asset_view(store)
        print("  PASS")

        print("\nTEST 5: Venue view")
        test_venue_view(store)
        print("  PASS")

        print("\nTEST 6: Ruční insert")
        manual = test_manual_insert(store)
        print("  PASS")

        print("\nTEST 7: REVERSAL")
        test_reversal(store, manual)
        print("  PASS")

        print("\nTEST 8: Diagnostika")
        test_diagnostics(store)
        print("  PASS")

        print("\nTEST 9: Prázdný ledger")
        test_empty_ledger()
        print("  PASS")

        print("\n" + "=" * 60)
        print("  ALL 9 TESTS PASSED")
        print("=" * 60)
    finally:
        teardown(store)


if __name__ == "__main__":
    main()
