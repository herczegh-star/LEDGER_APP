"""UI: terminálové rozhraní pro MVP. Nahraditelné Fletem."""
import sys
import os
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.model import VALID_TYPES
from core.config import load_config
from core.service import LedgerService, ExportFilter


def print_header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_timeline(svc: LedgerService):
    print_header("TIMELINE")
    rows = svc.timeline()
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


def print_asset_view(svc: LedgerService):
    print_header("ASSET VIEW")
    balances = svc.asset_balances()
    if not balances:
        print("  (žádná aktiva)")
        return
    print(f"  {'ASSET':<10} {'BALANCE':>16}")
    print(f"  {'-'*10} {'-'*16}")
    for asset, bal in sorted(balances.items()):
        marker = " ⚠" if bal < 0 else ""
        print(f"  {asset:<10} {bal:>16}{marker}")


def print_venue_view(svc: LedgerService):
    print_header("VENUE VIEW")
    venue_bal = svc.venue_balances()
    if not venue_bal:
        print("  (žádná data)")
        return
    for venue, assets in sorted(venue_bal.items()):
        print(f"\n  [{venue}]")
        for asset, bal in sorted(assets.items()):
            marker = " ⚠" if bal < 0 else ""
            print(f"    {asset:<10} {bal:>16}{marker}")


def print_diagnostics(svc: LedgerService):
    print_header("DIAGNOSTIKA")
    warnings = svc.diagnostics()
    if not warnings:
        print("  ✓ Žádné problémy.")
        return
    for w in warnings:
        print(f"  ⚠ {w['msg']}")


def do_import(svc: LedgerService):
    print_header("IMPORT RAW SOUBORU")
    filepath = input("  Cesta k souboru (*.xlsm / *.csv): ").strip()
    if not filepath:
        print("  Zrušeno.")
        return
    if not os.path.exists(filepath):
        print(f"  Soubor nenalezen: {filepath}")
        return
    try:
        result = svc.import_file(filepath)

        if result.parse_errors:
            print(f"  ⚠ Parse chyby v {len(result.parse_errors)} řádcích:")
            for err in result.parse_errors[:5]:
                print(f"    řádek {err['row_index']}: {', '.join(err['errors'])}")
            if len(result.parse_errors) > 5:
                print(f"    ... a dalších {len(result.parse_errors) - 5}")

        if result.validation_errors:
            print(f"  ⚠ {len(result.validation_errors)} řádků neprojde validací:")
            for idx, errs in result.validation_errors[:5]:
                print(f"    řádek {idx}: {', '.join(errs)}")
            if len(result.validation_errors) > 5:
                print(f"    ... a dalších {len(result.validation_errors) - 5}")

        if result.inserted or result.skipped:
            print(f"  ✓ Importováno: {result.inserted}, přeskočeno (duplikáty): {result.skipped}")
        else:
            print("  Žádné validní řádky k importu.")

    except Exception as e:
        print(f"  Chyba při importu: {e}")


def do_add_row(svc: LedgerService, cfg: dict):
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

        dv = cfg.get("default_venue", "")
        venue_prompt = f"  venue [{dv}]: " if dv else "  venue: "
        venue = input(venue_prompt).strip().lower() or dv
        note = input("  note (volitelné): ").strip() or None

        result = svc.add_row(
            timestamp=ts, type_=type_, asset=asset, amount=amount,
            currency=currency, price=price, venue=venue, note=note,
        )

        if result.success:
            print(f"  ✓ Tok zapsán.")
        else:
            print(f"  ✗ {', '.join(result.errors)}")

    except (ValueError, Exception) as e:
        print(f"  Chyba: {e}")


def do_add_trade(svc: LedgerService, cfg: dict):
    print_header("DOUBLE-ENTRY OBCHOD")
    print("  Vytvoří 2 řádky se sdíleným id (BUY nebo SELL)")
    try:
        ts_str = input("  timestamp (YYYY-MM-DD HH:MM:SS) [nyní]: ").strip()
        if not ts_str:
            ts = datetime.now().replace(microsecond=0)
        else:
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")

        type_ = input("  type (BUY/SELL): ").strip().upper()
        asset = input("  asset (např. BTC): ").strip().upper()
        asset_amount = Decimal(input("  asset amount (kladné): ").strip())
        currency = input("  currency (např. EUR): ").strip().upper()
        currency_amount = Decimal(input("  currency amount (kladné): ").strip())

        price_str = input("  price (volitelné, Enter = žádná): ").strip()
        price = Decimal(price_str) if price_str else None

        dv = cfg.get("default_venue", "")
        venue_prompt = f"  venue [{dv}]: " if dv else "  venue: "
        venue = input(venue_prompt).strip().lower() or dv
        note = input("  note (volitelné): ").strip() or None

        result = svc.add_trade(
            timestamp=ts, type_=type_, asset=asset,
            asset_amount=asset_amount, currency=currency,
            currency_amount=currency_amount, venue=venue,
            price=price, note=note,
        )

        if result.success:
            print(f"  ✓ Obchod zapsán ({result.rows_inserted} řádků).")
        else:
            for err in result.errors:
                print(f"  ✗ {err}")

    except (ValueError, Exception) as e:
        print(f"  Chyba: {e}")


def do_reversal(svc: LedgerService):
    print_header("REVERSAL")
    recent = svc.recent_rows(20)
    if not recent:
        print("  (žádné řádky v ledgeru)")
        return
    print("  Poslední řádky:")
    for r in recent:
        print(f"    pk={r['pk']:<4} {r['timestamp'][:19]}  {r['type']:<10} {r['asset']:<6} {r['amount']:>14}  {r['venue']}")

    pk_str = input("\n  Zadej pk pro reversal (nebo 'q' pro zrušení): ").strip()
    if pk_str.lower() == "q":
        return

    try:
        pk = int(pk_str)
    except ValueError:
        print("  Neplatné pk.")
        return

    # Zjisti jestli je to double-entry pár (UI se musí zeptat uživatele)
    original = svc.get_row_by_pk(pk)
    if original is None:
        print(f"  Řádek pk={pk} nenalezen.")
        return

    reverse_pair = False
    pair = svc.get_rows_by_id(original.id)
    if len(pair) > 1:
        print(f"  Řádek je součástí double-entry (id: {original.id[:12]}...):")
        for p in pair:
            print(f"    {p.asset} {p.amount:+}")
        choice = input("  Revertovat celý pár? (a/n): ").strip().lower()
        reverse_pair = (choice == "a")

    result = svc.add_reversal(pk, reverse_pair=reverse_pair)
    if result.success:
        print(f"  ✓ REVERSAL zapsán ({result.rows_inserted} řádků).")
    else:
        for err in result.errors:
            print(f"  ✗ {err}")


def do_export(svc: LedgerService, cfg: dict):
    print_header("EXPORT")
    if svc.count() == 0:
        print("  (prázdný ledger – nic k exportu)")
        return

    fmt = input("  Formát (csv/json) [csv]: ").strip().lower() or "csv"
    if fmt not in ("csv", "json"):
        print("  Neplatný formát.")
        return

    print("  Filtry (Enter = přeskočit):")
    asset = input("    asset: ").strip().upper() or None
    venue = input("    venue: ").strip().lower() or None

    from_str = input("    od (YYYY-MM-DD): ").strip()
    time_from = None
    if from_str:
        try:
            time_from = datetime.strptime(from_str, "%Y-%m-%d")
        except ValueError:
            print("  Neplatný formát data.")
            return

    to_str = input("    do (YYYY-MM-DD): ").strip()
    time_to = None
    if to_str:
        try:
            time_to = datetime.strptime(to_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            print("  Neplatný formát data.")
            return

    filters = None
    if any([asset, venue, time_from, time_to]):
        filters = ExportFilter(venue=venue, asset=asset, time_from=time_from, time_to=time_to)

    export_dir = cfg["export_dir"]
    os.makedirs(export_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ledger_export_{ts}.{fmt}"
    path = os.path.join(export_dir, filename)

    try:
        if fmt == "csv":
            count = svc.export_raw_csv(path, filters)
        else:
            count = svc.export_raw_json(path, filters)
        print(f"  ✓ Exportováno {count} řádků → {path}")
    except Exception as e:
        print(f"  Chyba při exportu: {e}")


def do_init(svc: LedgerService, cfg: dict):
    print_header("INIT / STATUS")
    print(f"  Databáze: {cfg['db_path']}")
    print(f"  Export dir: {cfg['export_dir']}")
    dv = cfg.get("default_venue", "")
    print(f"  Default venue: {dv if dv else '(nezadáno)'}")
    print(f"  Řádků v ledgeru: {svc.count()}")
    warnings = svc.diagnostics()
    if warnings:
        print(f"  ⚠ {len(warnings)} diagnostických varování:")
        for w in warnings[:5]:
            print(f"    {w['msg']}")
        if len(warnings) > 5:
            print(f"    ... a dalších {len(warnings) - 5}")
    else:
        print("  ✓ Žádné diagnostické problémy")


def main():
    cfg = load_config()
    if len(sys.argv) > 1:
        cfg["db_path"] = sys.argv[1]

    svc = LedgerService(cfg["db_path"])
    print_header("LEDGER APP")
    print(f"  Databáze: {cfg['db_path']}")
    print(f"  Řádků v ledgeru: {svc.count()}")

    while True:
        print(f"\n  [1] Timeline    [2] Asset view    [3] Venue view")
        print(f"  [4] Import RAW  [5] Přidat tok    [6] Diagnostika")
        print(f"  [7] Obchod      [8] Reversal      [9] Export")
        print(f"  [i] Init/Status [0] Konec")
        choice = input("\n  Volba: ").strip()

        if choice == "1":
            print_timeline(svc)
        elif choice == "2":
            print_asset_view(svc)
        elif choice == "3":
            print_venue_view(svc)
        elif choice == "4":
            do_import(svc)
        elif choice == "5":
            do_add_row(svc, cfg)
        elif choice == "6":
            print_diagnostics(svc)
        elif choice == "7":
            do_add_trade(svc, cfg)
        elif choice == "8":
            do_reversal(svc)
        elif choice == "9":
            do_export(svc, cfg)
        elif choice.lower() == "i":
            do_init(svc, cfg)
        elif choice == "0":
            svc.close()
            print("  Ukončeno.")
            break
        else:
            print("  Neplatná volba.")


if __name__ == "__main__":
    main()
