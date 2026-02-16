#!/usr/bin/env python3
"""Ledger App CLI: instalovatelný příkazový řádek nad LedgerService."""
import argparse
import sys
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config
from core.service import LedgerService, ExportFilter


# ── HELPERS ──────────────────────────────────────

def _get_service(args, cfg):
    db_path = getattr(args, "db", None) or cfg["db_path"]
    return LedgerService(db_path), db_path


def _parse_decimal(value, name):
    try:
        return Decimal(value)
    except InvalidOperation:
        print(f"Neplatná hodnota {name}: {value}", file=sys.stderr)
        sys.exit(1)


def _parse_timestamp(value):
    if not value:
        return datetime.now().replace(microsecond=0)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        print(f"Neplatný timestamp: {value}", file=sys.stderr)
        sys.exit(1)


def _require_venue(args, cfg):
    venue = getattr(args, "venue", None) or cfg.get("default_venue", "")
    if not venue:
        print("Venue je povinné (--venue nebo default_venue v configu).", file=sys.stderr)
        sys.exit(1)
    return venue.lower()


# ── COMMANDS ─────────────────────────────────────

def cmd_init(args, cfg):
    svc, db_path = _get_service(args, cfg)
    print(f"Databáze: {db_path}")
    print(f"Export dir: {cfg['export_dir']}")
    dv = cfg.get("default_venue", "")
    print(f"Default venue: {dv if dv else '(nezadáno)'}")
    print(f"Řádků v ledgeru: {svc.count()}")
    warnings = svc.diagnostics()
    if warnings:
        print(f"{len(warnings)} diagnostických varování:")
        for w in warnings:
            print(f"  {w['msg']}")
    else:
        print("Žádné diagnostické problémy.")
    svc.close()


def cmd_import(args, cfg):
    if not os.path.exists(args.file):
        print(f"Soubor nenalezen: {args.file}", file=sys.stderr)
        sys.exit(1)
    svc, _ = _get_service(args, cfg)
    result = svc.import_file(args.file)
    if result.parse_errors:
        print(f"Parse chyby: {len(result.parse_errors)}", file=sys.stderr)
    if result.validation_errors:
        print(f"Validační chyby: {len(result.validation_errors)}", file=sys.stderr)
    print(f"Importováno: {result.inserted}, přeskočeno: {result.skipped}")
    svc.close()


def cmd_add(args, cfg):
    svc, _ = _get_service(args, cfg)
    ts = _parse_timestamp(args.timestamp)
    amount = _parse_decimal(args.amount, "amount")
    price = _parse_decimal(args.price, "price") if args.price else None
    venue = _require_venue(args, cfg)

    result = svc.add_row(
        timestamp=ts, type_=args.type.upper(), asset=args.asset.upper(),
        amount=amount, currency=args.currency.upper(), price=price,
        venue=venue, note=args.note,
    )
    if result.success:
        print("Tok zapsán.")
    else:
        print(f"Chyba: {', '.join(result.errors)}", file=sys.stderr)
        svc.close()
        sys.exit(1)
    svc.close()


def cmd_trade(args, cfg):
    svc, _ = _get_service(args, cfg)
    ts = _parse_timestamp(args.timestamp)
    amount = _parse_decimal(args.amount, "amount")
    price = _parse_decimal(args.price, "price")
    venue = _require_venue(args, cfg)

    result = svc.add_trade(
        timestamp=ts, type_=args.type.upper(), asset=args.asset.upper(),
        amount=amount, currency=args.currency.upper(),
        price=price, venue=venue, note=args.note,
    )
    if result.success:
        print(f"Obchod zapsán ({result.rows_inserted} řádků).")
    else:
        for err in result.errors:
            print(f"Chyba: {err}", file=sys.stderr)
        svc.close()
        sys.exit(1)
    svc.close()


def cmd_reversal(args, cfg):
    svc, _ = _get_service(args, cfg)
    result = svc.add_reversal(args.pk, reverse_pair=args.pair)
    if result.success:
        print(f"REVERSAL zapsán ({result.rows_inserted} řádků).")
    else:
        for err in result.errors:
            print(f"Chyba: {err}", file=sys.stderr)
        svc.close()
        sys.exit(1)
    svc.close()


def cmd_diagnostics(args, cfg):
    svc, _ = _get_service(args, cfg)
    warnings = svc.diagnostics()
    if not warnings:
        print("Žádné problémy.")
    else:
        for w in warnings:
            print(w["msg"])
    svc.close()


def cmd_export(args, cfg):
    svc, _ = _get_service(args, cfg)

    time_from = None
    if args.time_from:
        time_from = _parse_timestamp(args.time_from)

    time_to = None
    if args.time_to:
        time_to = _parse_timestamp(args.time_to)
        if time_to.hour == 0 and time_to.minute == 0 and time_to.second == 0:
            time_to = time_to.replace(hour=23, minute=59, second=59)

    filters = None
    if any([args.asset, args.venue, time_from, time_to]):
        filters = ExportFilter(
            venue=args.venue.lower() if args.venue else None,
            asset=args.asset.upper() if args.asset else None,
            time_from=time_from,
            time_to=time_to,
        )

    export_dir = cfg["export_dir"]
    os.makedirs(export_dir, exist_ok=True)

    if args.output:
        path = args.output
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(export_dir, f"ledger_export_{ts}.{args.format}")

    if args.format == "csv":
        count = svc.export_raw_csv(path, filters)
    else:
        count = svc.export_raw_json(path, filters)

    print(f"Exportováno {count} řádků do {path}")
    svc.close()


def cmd_balances(args, cfg):
    svc, _ = _get_service(args, cfg)
    if args.by_venue:
        venue_bal = svc.venue_balances()
        if not venue_bal:
            print("(žádná data)")
        else:
            for venue, assets in sorted(venue_bal.items()):
                print(f"\n[{venue}]")
                for asset, bal in sorted(assets.items()):
                    marker = " !" if bal < 0 else ""
                    print(f"  {asset:<10} {bal:>16}{marker}")
    else:
        balances = svc.asset_balances()
        if not balances:
            print("(žádná aktiva)")
        else:
            print(f"{'ASSET':<10} {'BALANCE':>16}")
            print(f"{'-'*10} {'-'*16}")
            for asset, bal in sorted(balances.items()):
                marker = " !" if bal < 0 else ""
                print(f"{asset:<10} {bal:>16}{marker}")
    svc.close()


def cmd_timeline(args, cfg):
    svc, _ = _get_service(args, cfg)
    rows = svc.timeline()
    if not rows:
        print("(prázdný ledger)")
        svc.close()
        return
    print(f"{'TIMESTAMP':<22} {'TYPE':<10} {'ASSET':<6} {'AMOUNT':>14} {'CURRENCY':<6} {'VENUE':<12} {'NOTE'}")
    print(f"{'-'*22} {'-'*10} {'-'*6} {'-'*14} {'-'*6} {'-'*12} {'-'*10}")
    for r in rows:
        note = r.note or ""
        if len(note) > 20:
            note = note[:17] + "..."
        print(f"{r.timestamp.strftime('%Y-%m-%d %H:%M:%S'):<22} {r.type:<10} {r.asset:<6} {r.amount:>14} {r.currency:<6} {r.venue:<12} {note}")
    print(f"\nCelkem: {len(rows)} řádků")
    svc.close()


def cmd_interactive(args, cfg):
    sys.argv = [sys.argv[0], cfg["db_path"]]
    from ui.terminal import main as terminal_main
    terminal_main()


# ── PARSER ───────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="ledger-app",
        description="Ledger App – append-only investiční tokový ledger",
    )
    parser.add_argument("--db", help="Cesta k SQLite databázi (přepíše config)")

    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="Zobraz config a stav databáze")

    # import
    p_import = sub.add_parser("import", help="Importuj RAW soubor (xlsm/csv)")
    p_import.add_argument("file", help="Cesta k souboru")

    # add
    p_add = sub.add_parser("add", help="Přidej jeden tok")
    p_add.add_argument("--type", required=True, help="BUY/SELL/TRANSFER/FEE/REVERSAL")
    p_add.add_argument("--asset", required=True, help="Aktivum (BTC, ETH, ...)")
    p_add.add_argument("--amount", required=True, help="Množství se znaménkem")
    p_add.add_argument("--currency", required=True, help="Protistranná měna")
    p_add.add_argument("--venue", help="Místo toku (default z configu)")
    p_add.add_argument("--price", help="Jednotková cena (volitelné)")
    p_add.add_argument("--timestamp", help="ISO 8601 (default: nyní)")
    p_add.add_argument("--note", help="Poznámka")

    # trade
    p_trade = sub.add_parser("trade", help="Double-entry obchod (2 řádky)")
    p_trade.add_argument("--type", required=True, help="BUY nebo SELL")
    p_trade.add_argument("--asset", required=True, help="Aktivum")
    p_trade.add_argument("--amount", required=True, help="Množství aktiva (kladné)")
    p_trade.add_argument("--currency", required=True, help="Protistranná měna")
    p_trade.add_argument("--price", required=True, help="Jednotková cena")
    p_trade.add_argument("--venue", help="Místo toku")
    p_trade.add_argument("--timestamp", help="ISO 8601 (default: nyní)")
    p_trade.add_argument("--note", help="Poznámka")

    # reversal
    p_rev = sub.add_parser("reversal", help="Reversal existujícího řádku")
    p_rev.add_argument("--pk", type=int, required=True, help="Primary key řádku")
    p_rev.add_argument("--pair", action="store_true", help="Revertovat celý double-entry pár")

    # diagnostics
    sub.add_parser("diagnostics", help="Zobraz diagnostická varování")

    # export
    p_export = sub.add_parser("export", help="Export ledgeru do CSV/JSON")
    p_export.add_argument("--format", choices=["csv", "json"], default="csv", help="Formát (default: csv)")
    p_export.add_argument("--output", "-o", help="Výstupní soubor (default: auto)")
    p_export.add_argument("--asset", help="Filtr: pouze tento asset")
    p_export.add_argument("--venue", help="Filtr: pouze toto venue")
    p_export.add_argument("--from", dest="time_from", help="Filtr: od (YYYY-MM-DD)")
    p_export.add_argument("--to", dest="time_to", help="Filtr: do (YYYY-MM-DD)")

    # balances
    p_bal = sub.add_parser("balances", help="Zobraz zůstatky aktiv")
    p_bal.add_argument("--by-venue", action="store_true", help="Rozděl podle venue")

    # timeline
    sub.add_parser("timeline", help="Zobraz timeline všech toků")

    # interactive
    sub.add_parser("interactive", help="Spusť interaktivní terminálové menu")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    cfg = load_config()
    if args.db:
        cfg["db_path"] = args.db

    commands = {
        "init": cmd_init,
        "import": cmd_import,
        "add": cmd_add,
        "trade": cmd_trade,
        "reversal": cmd_reversal,
        "diagnostics": cmd_diagnostics,
        "export": cmd_export,
        "balances": cmd_balances,
        "timeline": cmd_timeline,
        "interactive": cmd_interactive,
    }

    commands[args.command](args, cfg)


if __name__ == "__main__":
    main()
