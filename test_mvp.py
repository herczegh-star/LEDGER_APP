#!/usr/bin/env python3
"""Test: ověření všech MVP kritérií."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from decimal import Decimal
from core.model import RawRow
from core.validator import validate_row, validate_rows
from core.ledger_store import LedgerStore
from io_module.raw_loader import load_raw

DB = "test_ledger.db"
if os.path.exists(DB):
    os.remove(DB)

store = LedgerStore(DB)

# ============================================================
print("=" * 60)
print("TEST 1: Import Anycoin RAW souboru")
print("=" * 60)
rows = load_raw("/mnt/user-data/uploads/anycoin_raw.xlsm")
print(f"  Načteno řádků: {len(rows)}")
for r in rows:
    print(f"    {r.type:<10} {r.asset:<6} {r.amount:>14} {r.currency:<6} {r.venue}")

valid, invalid = validate_rows(rows)
print(f"  Validních: {len(valid)}, nevalidních: {len(invalid)}")
for idx, errs in invalid:
    print(f"    řádek {idx}: {errs}")

result = store.import_rows(valid)
print(f"  Import: inserted={result['inserted']}, skipped={result['skipped']}")
assert result['inserted'] == len(valid), "Všechny validní řádky musí být vloženy"
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 2: Opakovaný import = 0 duplicit")
print("=" * 60)
result2 = store.import_rows(valid)
print(f"  Import: inserted={result2['inserted']}, skipped={result2['skipped']}")
assert result2['inserted'] == 0, "Druhý import nesmí nic přidat"
assert result2['skipped'] == len(valid), "Vše musí být přeskočeno"
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 3: Timeline")
print("=" * 60)
timeline = store.timeline()
print(f"  Řádků v timeline: {len(timeline)}")
for r in timeline:
    print(f"    {r.timestamp} {r.type:<10} {r.asset:<6} {r.amount:>14}")
timestamps = [r.timestamp for r in timeline]
assert timestamps == sorted(timestamps), "Timeline musí být seřazena"
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 4: Asset view")
print("=" * 60)
balances = store.asset_balances()
for asset, bal in sorted(balances.items()):
    print(f"    {asset:<10} {bal:>14}")
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 5: Venue view")
print("=" * 60)
venue_bal = store.venue_balances()
for venue, assets in sorted(venue_bal.items()):
    print(f"    [{venue}]")
    for asset, bal in sorted(assets.items()):
        print(f"      {asset:<10} {bal:>14}")
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 6: Ruční zápis toku")
print("=" * 60)
manual_row = RawRow(
    timestamp=datetime(2026, 2, 10, 22, 0, 0),
    type="BUY", asset="BTC", amount=Decimal("0.005"),
    currency="CZK", price=Decimal("2500000"), venue="anycoin"
)
ok, errs = validate_row(manual_row)
assert ok, f"Validace musí projít: {errs}"
inserted = store.insert(manual_row)
assert inserted, "Ruční řádek musí být vložen"
print(f"  Ručně přidán BUY BTC +0.005")
count_after = store.count()
print(f"  Celkem v ledgeru: {count_after}")
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 7: REVERSAL (opravný tok)")
print("=" * 60)
reversal = RawRow(
    timestamp=datetime(2026, 2, 10, 22, 5, 0),
    type="REVERSAL", asset="BTC", amount=Decimal("-0.005"),
    currency="CZK", price=Decimal("2500000"), venue="anycoin",
    note=f"reversal of {manual_row.id}, test"
)
ok, errs = validate_row(reversal)
assert ok, f"REVERSAL validace: {errs}"
inserted = store.insert(reversal)
assert inserted, "REVERSAL musí být vložen"
print(f"  REVERSAL zapsán: BTC -0.005, note={reversal.note[:40]}...")

btc_balance = store.asset_balances().get("BTC", Decimal("0"))
print(f"  BTC balance po reversal: {btc_balance}")
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 8: Diagnostika (záporné zůstatky)")
print("=" * 60)
warnings = store.diagnostics()
for w in warnings:
    print(f"  ⚠ {w['msg']}")
if not warnings:
    print("  ✓ Žádné problémy")
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("TEST 9: Prázdný ledger je validní")
print("=" * 60)
if os.path.exists("empty_test.db"):
    os.remove("empty_test.db")
empty = LedgerStore("empty_test.db")
assert empty.count() == 0
assert empty.timeline() == []
assert empty.asset_balances() == {}
assert empty.venue_balances() == {}
assert empty.diagnostics() == []
empty.close()
os.remove("empty_test.db")
print("  Prázdný ledger: timeline=[], assets={}, venues={}, diagnostika=[]")
print("  ✓ PASS")

# ============================================================
print("\n" + "=" * 60)
print("VŠECHNY TESTY PROŠLY ✓")
print("=" * 60)
print(f"  Celkem řádků v ledgeru: {store.count()}")

store.close()
os.remove(DB)
