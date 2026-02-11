"""UI: terminálové rozhraní pro MVP. Nahraditelné Fletem."""
import sys
import os
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.model import RawRow, VALID_TYPES
from core.validator import validate_row
from core.ledger_store import LedgerStore
from io_module.raw_loader import load_raw


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_timeline(store: LedgerStore):
    print_header("TIMELINE")
    rows = store.timeline()
    if not rows:
        print("  (prázdný ledger – 0 řádků)")
        return
    print(f"  {'TIMESTAMP':<22} {'TYPE':<10} {'ASSET':<6} {'AMOUNT':>14} {'CURRENCY':<6} {'VENUE':<12} {'NOTE'}")
    print(f"  {'-'*22} {'-'*10} {'-'*6} {'-'*14} {'-'*6} {'-'*12} {'-'*10}")
    for r in rows:
        note = r.note or ""
        if len(note) > 20:
            note = note[:17] + "..."
        print(f"  {r.timestamp.strftime('%Y-%m-%d %H:%M:%S'):<22} {r.type:<10} {r.asset:<6} {r.amount:>14} {r.currency:<6} {r.venue:<12} {note}")
    print(f"\n  Celkem: {len(rows)} řádků")


def print_asset_view(store: LedgerStore):
    print_header("ASSET VIEW")
    balances = store.asset_balances()
    if not balances:
        print("  (žádná aktiva)")
        return
    print(f"  {'ASSET':<10} {'BALANCE':>16}")
    print(f"  {'-'*10} {'-'*16}")
    for asset, bal in sorted(balances.items()):
        marker = " ⚠" if bal < 0 else ""
        print(f"  {asset:<10} {bal:>16}{marker}")


def print_venue_view(store: LedgerStore):
    print_header("VENUE VIEW")
    venue_bal = store.venue_balances()
    if not venue_bal:
        print("  (žádná data)")
        return
    for venue, assets in sorted(venue_bal.items()):
        print(f"\n  [{venue}]")
        for asset, bal in sorted(assets.items()):
            marker = " ⚠" if bal < 0 else ""
            print(f"    {asset:<10} {bal:>16}{marker}")


def print_diagnostics(store: LedgerStore):
    print_header("DIAGNOSTIKA")
    warnings = store.diagnostics()
    if not warnings:
        print("  ✓ Žádné problémy.")
        return
    for w in warnings:
        print(f"  ⚠ {w['msg']}")


def do_import(store: LedgerStore):
    print_header("IMPORT RAW SOUBORU")
    filepath = input("  Cesta k souboru (*.xlsm / *.csv): ").strip()
    if not filepath:
        print("  Zrušeno.")
        return
    if not os.path.exists(filepath):
        print(f"  Soubor nenalezen: {filepath}")
        return
    try:
        rows = load_raw(filepath)
        print(f"  Načteno {len(rows)} řádků.")
        from core.validator import validate_rows
        valid, invalid = validate_rows(rows)
        if invalid:
            print(f"  ⚠ {len(invalid)} řádků neprojde validací:")
            for idx, errs in invalid[:5]:
                print(f"    řádek {idx}: {', '.join(errs)}")
            if len(invalid) > 5:
                print(f"    ... a dalších {len(invalid)-5}")
        if valid:
            result = store.import_rows(valid)
            print(f"  ✓ Importováno: {result['inserted']}, přeskočeno (duplikáty): {result['skipped']}")
        else:
            print("  Žádné validní řádky k importu.")
    except Exception as e:
        print(f"  Chyba při importu: {e}")


def do_add_row(store: LedgerStore):
    print_header("RUČNÍ ZÁPIS TOKU")
    print(f"  Typy: {', '.join(sorted(VALID_TYPES))}")
    try:
        ts_str = input("  timestamp (YYYY-MM-DD HH:MM:SS) [nyní]: ").strip()
        if not ts_str:
            ts = datetime.now().replace(microsecond=0)
        else:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")

        type_ = input("  type: ").strip().upper()
        asset = input("  asset: ").strip().upper()
        amount_str = input("  amount (se znaménkem): ").strip()
        amount = Decimal(amount_str)
        currency = input("  currency: ").strip().upper()

        price_str = input("  price (volitelné, Enter = žádná): ").strip()
        price = Decimal(price_str) if price_str else None

        venue = input("  venue: ").strip().lower()
        note = input("  note (volitelné): ").strip() or None

        row = RawRow(
            timestamp=ts, type=type_, asset=asset, amount=amount,
            currency=currency, price=price, venue=venue, note=note
        )

        ok, errs = validate_row(row)
        if not ok:
            print(f"  ✗ Validace selhala: {', '.join(errs)}")
            return

        if store.insert(row):
            print(f"  ✓ Tok zapsán. (id: {row.id[:12]}...)")
        else:
            print("  ✗ Duplicitní řádek (row_fp již existuje).")

    except (ValueError, Exception) as e:
        print(f"  Chyba: {e}")


def main():
    db_path = "ledger.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    store = LedgerStore(db_path)
    print_header("LEDGER APP – MVP")
    print(f"  Databáze: {db_path}")
    print(f"  Řádků v ledgeru: {store.count()}")

    while True:
        print(f"\n  [1] Timeline    [2] Asset view    [3] Venue view")
        print(f"  [4] Import RAW  [5] Přidat tok    [6] Diagnostika")
        print(f"  [0] Konec")
        choice = input("\n  Volba: ").strip()

        if choice == "1":
            print_timeline(store)
        elif choice == "2":
            print_asset_view(store)
        elif choice == "3":
            print_venue_view(store)
        elif choice == "4":
            do_import(store)
        elif choice == "5":
            do_add_row(store)
        elif choice == "6":
            print_diagnostics(store)
        elif choice == "0":
            store.close()
            print("  Ukončeno.")
            break
        else:
            print("  Neplatná volba.")


if __name__ == "__main__":
    main()
