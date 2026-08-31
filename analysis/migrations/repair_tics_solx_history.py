"""One-off append-only repair for the historical TICS/SOLX branch.

This migration is intentionally data-specific.  It does not change application
logic.  It backs up the active database, validates exact preconditions, inserts
all repair rows in one SQLite transaction, and rolls back unless the approved
venue balances are reached.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from core.model import RawRow


DB_PATH = Path(r"C:\Users\hercz\.ledger_app\ledger.db")

BAD_IDS = (
    "20250130_170420_TRUST WALLET_SWAP_001",
    "20250131_100957_TRUST WALLET_SWAP_001",
    "20250131_171202_TRUST WALLET_SWAP_001",
    "20250307_151420_TRUST WALLET_SWAP_001",
    "20250311_131630_TRUST WALLET_SWAP_001",
    "20250311_201837_TRUST WALLET_SWAP_001",
)

EXPECTED_ORIGINALS = {
    "20250130_170420_TRUST WALLET_SWAP_001": {("DOGE", "-2933.83"), ("TICS", "16185.67")},
    "20250131_100957_TRUST WALLET_SWAP_001": {("DOGE", "-1503.42"), ("TICS", "8066.62")},
    "20250131_171202_TRUST WALLET_SWAP_001": {("DOGE", "-56"), ("TICS", "309.13")},
    "20250307_151420_TRUST WALLET_SWAP_001": {("ETH", "-0.99"), ("TICS", "22370.35")},
    "20250311_131630_TRUST WALLET_SWAP_001": {("ETH", "-0.45"), ("TICS", "8123.56")},
    "20250311_201837_TRUST WALLET_SWAP_001": {("ETH", "-0.0299"), ("TICS", "698.37")},
}

TARGETS = {
    ("anycoin", "DOGE"): Decimal("9029.15556595"),
    ("trust wallet", "DOGE"): Decimal("0"),
    ("anycoin", "ETH"): Decimal("0.05563753"),
    ("trust wallet", "ETH"): Decimal("0.00495976"),
    ("trust wallet", "TICS"): Decimal("0.02"),
    ("qubetics wallet", "TICS"): Decimal("69231.67"),
    ("solaxy wallet", "SOLX"): Decimal("1989162"),
}


def row(row_id: str, ts: str, type_: str, asset: str, amount: str,
        currency: str, price: str, venue: str, note: str) -> RawRow:
    return RawRow(
        id=row_id,
        timestamp=datetime.fromisoformat(ts),
        type=type_,
        asset=asset,
        amount=Decimal(amount),
        currency=currency,
        price=Decimal(price),
        venue=venue,
        note=note,
    )


def balances(con: sqlite3.Connection) -> dict[tuple[str, str], Decimal]:
    result: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for venue, asset, amount in con.execute("SELECT venue, asset, amount FROM ledger"):
        result[(venue.lower(), asset.upper())] += Decimal(amount)
    return dict(result)


def validate_preconditions(con: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in BAD_IDS)
    originals = con.execute(
        f"SELECT * FROM ledger WHERE id IN ({placeholders}) ORDER BY timestamp, pk",
        BAD_IDS,
    ).fetchall()
    if len(originals) != 12:
        raise RuntimeError(f"Expected 12 original TICS rows, found {len(originals)}")
    found: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for item in originals:
        found[item["id"]].add((item["asset"], item["amount"]))
    if dict(found) != EXPECTED_ORIGINALS:
        raise RuntimeError(f"Original TICS rows differ from audit: {dict(found)!r}")
    existing = con.execute(
        "SELECT COUNT(*) FROM ledger WHERE note LIKE 'HISTORICAL REPAIR TICS/SOLX 2026-08-31%'"
    ).fetchone()[0]
    if existing:
        raise RuntimeError("Repair marker already exists; refusing to run twice")
    return originals


def build_rows(originals: list[sqlite3.Row]) -> list[RawRow]:
    rows: list[RawRow] = []

    # Each inverse swap has one shared ID.  Its negative TICS leg sorts before
    # the positive funding-asset leg at the same timestamp in the WAC engine.
    by_id: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for item in originals:
        by_id[item["id"]].append(item)
    for index, original_id in enumerate(BAD_IDS, start=1):
        pair = by_id[original_id]
        reversal_id = f"HIST_REPAIR_REV_TICS_{index:02d}"
        reversal_ts = datetime.fromisoformat(pair[0]["timestamp"]) + timedelta(seconds=1)
        for item in pair:
            rows.append(RawRow(
                id=reversal_id,
                timestamp=reversal_ts,
                type="REVERSAL",
                asset=item["asset"],
                amount=-Decimal(item["amount"]),
                currency=item["currency"],
                price=Decimal(item["price"]) if item["price"] is not None else None,
                venue=item["venue"],
                note=f"HISTORICAL REPAIR TICS/SOLX 2026-08-31; reversal of {original_id}",
            ))

    # DOGE funding purchases are complete by 2025-01-31 10:41:16.  Transfer
    # and aggregate recognition occur after the final bad swap has been undone.
    rows.extend([
        row("HIST_REPAIR_DOGE_TRANSFER", "2025-01-31T17:12:03.500000", "TRANSFER", "DOGE", "-4508", "DOGE", "1", "anycoin", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; funding Anycoin -> Trust Wallet"),
        row("HIST_REPAIR_DOGE_TRANSFER", "2025-01-31T17:12:03.500000", "TRANSFER", "DOGE", "4508", "DOGE", "1", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; funding Anycoin -> Trust Wallet"),
        row("HIST_REPAIR_DOGE_TICS", "2025-01-31T17:12:04", "SELL", "DOGE", "-4508", "TICS", "5.44840727595385980479148181012", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; aggregate DOGE -> TICS"),
        row("HIST_REPAIR_DOGE_TICS", "2025-01-31T17:12:04", "BUY", "TICS", "24561.42", "TICS", "1", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; aggregate DOGE -> TICS"),
    ])

    # Missing Anycoin purchases: placed after the last existing historical
    # funding buy and before transfer/consumption.  Exact minutes are unknown.
    rows.extend([
        row("HIST_REPAIR_ETH_BUY_26000", "2025-02-10T12:00:00", "BUY", "ETH", "0.39880784", "CZK", "65194.30510694072614018821696", "anycoin", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; documented purchase 26000 CZK"),
        row("HIST_REPAIR_ETH_BUY_26000", "2025-02-10T12:00:00", "BUY", "CZK", "-26000", "CZK", "1", "anycoin", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; documented purchase 26000 CZK"),
        row("HIST_REPAIR_ETH_BUY_45000", "2025-02-11T12:00:00", "BUY", "ETH", "0.69466114", "CZK", "64779.78601192518124736328277", "anycoin", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; documented purchase 45000 CZK"),
        row("HIST_REPAIR_ETH_BUY_45000", "2025-02-11T12:00:00", "BUY", "CZK", "-45000", "CZK", "1", "anycoin", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; documented purchase 45000 CZK"),
        row("HIST_REPAIR_ETH_TRANSFER", "2025-03-07T15:14:19", "TRANSFER", "ETH", "-2.99304712", "ETH", "1", "anycoin", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; historical funding Anycoin -> Trust Wallet"),
        row("HIST_REPAIR_ETH_TRANSFER", "2025-03-07T15:14:19", "TRANSFER", "ETH", "2.99304712", "ETH", "1", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; historical funding Anycoin -> Trust Wallet"),
        row("HIST_REPAIR_ETH_TICS", "2025-03-11T20:18:39", "SELL", "ETH", "-1.439", "TICS", "21676.3585823488533703961084086", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; aggregate ETH -> TICS"),
        row("HIST_REPAIR_ETH_TICS", "2025-03-11T20:18:39", "BUY", "TICS", "31192.28", "TICS", "1", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; aggregate ETH -> TICS"),
        row("HIST_REPAIR_TICS_DUST", "2025-03-11T20:18:40", "BUY", "TICS", "0.02", "TICS", "1", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; unallocated historical rounding/dust difference; no DOGE/ETH branch attribution"),
        row("HIST_REPAIR_SOLX", "2025-03-12T12:00:00", "SELL", "ETH", "-1.54908736", "SOLX", "1284086.39264863667856666263160", "trust wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; SOLX acquisition value 100930.79 CZK"),
        row("HIST_REPAIR_SOLX", "2025-03-12T12:00:00", "BUY", "SOLX", "1989162", "SOLX", "1", "solaxy wallet", "HISTORICAL REPAIR TICS/SOLX 2026-08-31; SOLX acquisition value 100930.79 CZK; acquired directly on solaxy wallet"),
    ])
    return rows


def insert_rows(con: sqlite3.Connection, rows: list[RawRow]) -> None:
    now = datetime.now().isoformat()
    for item in rows:
        con.execute(
            """INSERT INTO ledger
               (id,timestamp,type,asset,amount,currency,price,venue,note,row_fp,imported_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (item.id, item.timestamp.isoformat(), item.type, item.asset.upper(),
             str(item.amount), item.currency.upper(),
             str(item.price) if item.price is not None else None,
             item.venue.lower(), item.note, item.fingerprint(), now),
        )


def main() -> int:
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_name(f"ledger_pre_tics_solx_repair_{stamp}.db")
    shutil.copy2(DB_PATH, backup)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        originals = validate_preconditions(con)
        repair_rows = build_rows(originals)
        con.execute("BEGIN IMMEDIATE")
        insert_rows(con, repair_rows)
        actual = balances(con)
        errors = []
        for key, expected in TARGETS.items():
            if actual.get(key, Decimal("0")) != expected:
                errors.append(f"{key}: expected {expected}, got {actual.get(key, Decimal('0'))}")
        if errors:
            raise RuntimeError("Target balance validation failed: " + "; ".join(errors))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"BACKUP={backup}")
    print(f"INSERTED={len(repair_rows)}")
    for key, expected in TARGETS.items():
        print(f"BALANCE={key[0]}|{key[1]}|{expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
