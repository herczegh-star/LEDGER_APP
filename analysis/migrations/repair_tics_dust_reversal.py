"""One-off append-only reversal of the orphan HIST_REPAIR_TICS_DUST row.

The historical TICS/SOLX repair booked a bare ``BUY TICS +0.02`` on
``trust wallet`` (id ``HIST_REPAIR_TICS_DUST``) as an unallocated rounding
remainder.  The DOGE and ETH funding branches actually reconcile exactly to
55753.70 TICS == the pre-existing Trust Wallet -> Qubetics Wallet transfer, so
that 0.02 TICS is spurious and has no funding counterparty.

This migration appends a single REVERSAL row that shares the original id,
one second after the original timestamp, bringing Trust Wallet TICS to 0.
It backs up the DB, validates exact preconditions, inserts in one
transaction, and rolls back unless Trust Wallet TICS == 0 afterwards.

It does not change application logic and does not touch DOGE/ETH balances,
SOLX quantity, or the WAC/cost-basis engine.
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
ORPHAN_ID = "HIST_REPAIR_TICS_DUST"
NOTE = (
    "HISTORICAL REPAIR TICS/SOLX 2026-08-31; reversal of orphan dust BUY "
    "HIST_REPAIR_TICS_DUST -- spurious 0.02 TICS; DOGE+ETH branches reconcile "
    "to 55753.70 = existing Qubetics transfer; no funding implied"
)


def balances(con: sqlite3.Connection) -> dict[tuple[str, str], Decimal]:
    result: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for venue, asset, amount in con.execute("SELECT venue, asset, amount FROM ledger"):
        result[(venue.lower(), asset.upper())] += Decimal(amount)
    return dict(result)


def validate_preconditions(con: sqlite3.Connection) -> sqlite3.Row:
    orphan = con.execute(
        "SELECT * FROM ledger WHERE id = ? ORDER BY timestamp, pk", (ORPHAN_ID,)
    ).fetchall()
    if len(orphan) != 1:
        raise RuntimeError(f"Expected exactly 1 {ORPHAN_ID} row, found {len(orphan)}")
    dust = orphan[0]
    if not (dust["type"] == "BUY" and dust["asset"] == "TICS"
            and Decimal(dust["amount"]) == Decimal("0.02")
            and dust["venue"] == "trust wallet"):
        raise RuntimeError(f"Orphan row differs from audit: {dict(dust)!r}")
    tw_tics = balances(con).get(("trust wallet", "TICS"), Decimal("0"))
    if tw_tics != Decimal("0.02"):
        raise RuntimeError(f"Trust Wallet TICS expected 0.02, found {tw_tics}")
    return dust


def build_row(dust: sqlite3.Row) -> RawRow:
    return RawRow(
        id=ORPHAN_ID,
        timestamp=datetime.fromisoformat(dust["timestamp"]) + timedelta(seconds=1),
        type="REVERSAL",
        asset="TICS",
        amount=Decimal("-0.02"),
        currency="TICS",
        price=Decimal(dust["price"]) if dust["price"] is not None else None,
        venue="trust wallet",
        note=NOTE,
    )


def insert_row(con: sqlite3.Connection, item: RawRow) -> None:
    now = datetime.now().isoformat()
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
    backup = DB_PATH.with_name(f"ledger_pre_tics_dust_reversal_{stamp}.db")
    shutil.copy2(DB_PATH, backup)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        dust = validate_preconditions(con)
        row = build_row(dust)
        con.execute("BEGIN IMMEDIATE")
        insert_row(con, row)
        tw_tics = balances(con).get(("trust wallet", "TICS"), Decimal("0"))
        if tw_tics != Decimal("0"):
            raise RuntimeError(f"Post-insert Trust Wallet TICS expected 0, got {tw_tics}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"BACKUP={backup}")
    print("INSERTED=1")
    print("BALANCE=trust wallet|TICS|0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
