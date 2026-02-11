#!/usr/bin/env python3
"""Testy exportu: CSV, JSON, filtry, Decimal přesnost."""
import sys
import os
import csv
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from datetime import datetime
from decimal import Decimal
from core.service import LedgerService, ExportFilter

DB = "test_export.db"
EXPORT_CSV = "test_export.csv"
EXPORT_JSON = "test_export.json"


def setup():
    for f in [DB, EXPORT_CSV, EXPORT_JSON]:
        if os.path.exists(f):
            os.remove(f)
    svc = LedgerService(DB)
    svc.add_row(
        timestamp=datetime(2026, 1, 15, 10, 0, 0), type_="BUY", asset="BTC",
        amount=Decimal("0.5"), currency="EUR", venue="kraken",
    )
    svc.add_row(
        timestamp=datetime(2026, 2, 1, 12, 0, 0), type_="BUY", asset="ETH",
        amount=Decimal("10"), currency="EUR", venue="anycoin",
    )
    svc.add_row(
        timestamp=datetime(2026, 3, 1, 14, 0, 0), type_="FEE", asset="EUR",
        amount=Decimal("-5.50"), currency="EUR", venue="kraken",
    )
    return svc


def teardown(svc):
    svc.close()
    for f in [DB, EXPORT_CSV, EXPORT_JSON]:
        if os.path.exists(f):
            os.remove(f)


def test_export_csv(svc):
    """TEST 1: CSV export vytvoří soubor se správným počtem řádků."""
    count = svc.export_raw_csv(EXPORT_CSV)
    assert count == 3, f"Expected 3, got {count}"
    assert os.path.exists(EXPORT_CSV)

    with open(EXPORT_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert len(rows) == 3

    expected_fields = ["id", "timestamp", "type", "asset", "amount", "currency", "price", "venue", "note"]
    assert list(rows[0].keys()) == expected_fields
    print(f"  CSV export: {count} rows, correct fields")


def test_export_json(svc):
    """TEST 2: JSON export vytvoří soubor se správným počtem řádků."""
    count = svc.export_raw_json(EXPORT_JSON)
    assert count == 3, f"Expected 3, got {count}"
    assert os.path.exists(EXPORT_JSON)

    with open(EXPORT_JSON, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) == 3

    expected_fields = ["id", "timestamp", "type", "asset", "amount", "currency", "price", "venue", "note"]
    assert list(data[0].keys()) == expected_fields
    print(f"  JSON export: {count} rows, correct fields")


def test_decimal_preserved_csv(svc):
    """TEST 3: CSV export zachovává Decimal jako string (ne float)."""
    svc.export_raw_csv(EXPORT_CSV)
    with open(EXPORT_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    btc_row = [r for r in rows if r["asset"] == "BTC"][0]
    assert btc_row["amount"] == "0.5", f"Expected '0.5', got '{btc_row['amount']}'"

    fee_row = [r for r in rows if r["type"] == "FEE"][0]
    assert fee_row["amount"] == "-5.50", f"Expected '-5.50', got '{fee_row['amount']}'"
    print(f"  Decimal preserved in CSV: BTC={btc_row['amount']}, FEE={fee_row['amount']}")


def test_decimal_preserved_json(svc):
    """TEST 4: JSON export zachovává Decimal jako string (ne float)."""
    svc.export_raw_json(EXPORT_JSON)
    with open(EXPORT_JSON, encoding="utf-8") as f:
        data = json.load(f)

    btc_row = [r for r in data if r["asset"] == "BTC"][0]
    assert isinstance(btc_row["amount"], str), f"Amount should be string, got {type(btc_row['amount'])}"
    assert btc_row["amount"] == "0.5"

    fee_row = [r for r in data if r["type"] == "FEE"][0]
    assert isinstance(fee_row["amount"], str)
    assert fee_row["amount"] == "-5.50"
    print(f"  Decimal preserved in JSON: BTC={btc_row['amount']}, FEE={fee_row['amount']}")


def test_filter_asset(svc):
    """TEST 5: Filtr podle asset vrátí jen odpovídající řádky."""
    filters = ExportFilter(asset="BTC")
    count = svc.export_raw_csv(EXPORT_CSV, filters)
    assert count == 1, f"Expected 1 BTC row, got {count}"

    with open(EXPORT_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["asset"] == "BTC"
    print(f"  Filter asset=BTC: {count} row")


def test_filter_venue(svc):
    """TEST 6: Filtr podle venue vrátí jen odpovídající řádky."""
    filters = ExportFilter(venue="kraken")
    count = svc.export_raw_json(EXPORT_JSON, filters)
    assert count == 2, f"Expected 2 kraken rows, got {count}"

    with open(EXPORT_JSON, encoding="utf-8") as f:
        data = json.load(f)
    for row in data:
        assert row["venue"] == "kraken", f"Expected kraken, got {row['venue']}"
    print(f"  Filter venue=kraken: {count} rows")


def test_filter_time_range(svc):
    """TEST 7: Filtr podle časového rozsahu."""
    filters = ExportFilter(
        time_from=datetime(2026, 2, 1),
        time_to=datetime(2026, 2, 28, 23, 59, 59),
    )
    count = svc.export_raw_csv(EXPORT_CSV, filters)
    assert count == 1, f"Expected 1 row in Feb, got {count}"

    with open(EXPORT_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["asset"] == "ETH"
    print(f"  Filter time range (Feb): {count} row")


def test_filter_combined(svc):
    """TEST 8: Kombinace filtrů (venue + asset)."""
    filters = ExportFilter(venue="kraken", asset="BTC")
    count = svc.export_raw_csv(EXPORT_CSV, filters)
    assert count == 1, f"Expected 1 row, got {count}"

    with open(EXPORT_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["asset"] == "BTC"
    assert rows[0]["venue"] == "kraken"
    print(f"  Filter combined (kraken+BTC): {count} row")


def test_export_empty(svc):
    """TEST 9: Export prázdného ledgeru."""
    empty_db = "test_empty_export.db"
    empty_svc = LedgerService(empty_db)
    try:
        count = empty_svc.export_raw_csv(EXPORT_CSV)
        assert count == 0
        assert os.path.exists(EXPORT_CSV)

        with open(EXPORT_CSV, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 0
        print(f"  Empty export: 0 rows, header only")
    finally:
        empty_svc.close()
        if os.path.exists(empty_db):
            os.remove(empty_db)


def main():
    tests = [
        ("TEST 1: CSV export", test_export_csv),
        ("TEST 2: JSON export", test_export_json),
        ("TEST 3: Decimal preserved (CSV)", test_decimal_preserved_csv),
        ("TEST 4: Decimal preserved (JSON)", test_decimal_preserved_json),
        ("TEST 5: Filter by asset", test_filter_asset),
        ("TEST 6: Filter by venue", test_filter_venue),
        ("TEST 7: Filter by time range", test_filter_time_range),
        ("TEST 8: Filter combined", test_filter_combined),
        ("TEST 9: Export empty ledger", test_export_empty),
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
    print(f"  ALL {len(tests)} EXPORT TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
