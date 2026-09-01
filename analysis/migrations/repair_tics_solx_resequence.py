"""One-off append-only re-sequence of the TICS/SOLX historical repair.

The earlier repair back-dated HIST_REPAIR_DOGE_TICS to 2025-01-31, i.e. before
the March 2025 bad TICS swaps.  Those March bad-swap BUY legs then blended into
a TICS pool that already held the DOGE aggregate, so their same-second
HIST_REPAIR_REV_TICS_04/05/06 reversals unwound at a blended WAC and could not
cost-cancel.  Result: ~12.5k CZK of ETH-tranche cost basis was misattributed
from SOLX/ETH into TICS.  Global cost basis was preserved.

This migration appends 4 rows (2 trade pairs, append-only, no edits/deletes):

  1. HIST_REPAIR_REV_DOGE_TICS  @ 2025-01-31T17:12:05
       REVERSAL TICS -24561.42 ; REVERSAL DOGE +4508
       -> backdated reversal of the mis-sequenced aggregate; nets to zero on
          both DOGE and TICS because nothing else touched TICS at 17:12:04-05.

  2. HIST_REPAIR_DOGE_TICS_V2   @ 2025-03-11T20:18:38.500000
       SELL DOGE -4508 ; BUY TICS +24561.42
       -> re-inserts the identical DOGE->TICS conversion AFTER
          HIST_REPAIR_REV_TICS_06 (20:18:38) and before HIST_REPAIR_ETH_TICS
          (20:18:39), so all six bad swaps cost-cancel against an empty pool.

It does not change application logic, the WAC/cost-basis engine, quantities,
or any venue balance.  It backs up the DB, validates exact preconditions,
inserts in one transaction, and rolls back unless the dry-run target cost
bases are reached.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from core.model import RawRow
from core.reports.positions import compute_positions

DB_PATH = Path(r"C:\Users\hercz\.ledger_app\ledger.db")
MARKER = "HISTORICAL REPAIR TICS/SOLX re-sequence 2026-09-01"
NOTE_REV = MARKER + "; reverse mis-sequenced DOGE->TICS aggregate"
NOTE_V2 = MARKER + "; DOGE->TICS aggregate re-sequenced after bad-swap reversals"

ORIG_ID = "HIST_REPAIR_DOGE_TICS"
EXPECTED_ORIG = {("DOGE", "-4508"), ("TICS", "24561.42")}

TOL = Decimal("0.01")
TARGET_CB = {
    "DOGE": Decimal("42694.54618592665640300694882"),
    "ETH":  Decimal("83979.8957396079242454425509"),
    "TICS": Decimal("134246.0541718231852190879683"),
    "SOLX": Decimal("104029.9557195076835275483401"),
}
TARGET_BAL = {
    ("anycoin", "DOGE"): Decimal("9029.15556595"),
    ("trust wallet", "DOGE"): Decimal("0"),
    ("kraken", "DOGE"): Decimal("650.77"),
    ("anycoin", "ETH"): Decimal("0.05563753"),
    ("trust wallet", "ETH"): Decimal("0.00495976"),
    ("trust wallet", "TICS"): Decimal("0"),
    ("qubetics wallet", "TICS"): Decimal("69231.67"),
    ("solaxy wallet", "SOLX"): Decimal("1989162"),
}


def balances(con: sqlite3.Connection) -> dict[tuple[str, str], Decimal]:
    out: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for venue, asset, amount in con.execute("SELECT venue, asset, amount FROM ledger"):
        out[(venue.lower(), asset.upper())] += Decimal(amount)
    return dict(out)


def load_rows(con: sqlite3.Connection) -> list[RawRow]:
    rows: list[RawRow] = []
    for r in con.execute(
        "SELECT id,timestamp,type,asset,amount,currency,price,venue,note FROM ledger"
    ):
        rows.append(RawRow(
            id=r[0], timestamp=datetime.fromisoformat(r[1]), type=r[2], asset=r[3],
            amount=Decimal(r[4]), currency=r[5],
            price=Decimal(r[6]) if r[6] is not None else None, venue=r[7], note=r[8],
        ))
    return rows


def validate_preconditions(con: sqlite3.Connection) -> dict[str, str]:
    legs = con.execute(
        "SELECT asset, amount, price, currency, venue FROM ledger WHERE id = ? ORDER BY amount",
        (ORIG_ID,),
    ).fetchall()
    if len(legs) != 2:
        raise RuntimeError(f"Expected 2 {ORIG_ID} legs, found {len(legs)}")
    found = {(row[0], row[1]) for row in legs}
    if found != EXPECTED_ORIG:
        raise RuntimeError(f"{ORIG_ID} legs differ from audit: {found!r}")
    for row in legs:
        if row[4] != "trust wallet" or row[3] != "TICS":
            raise RuntimeError(f"{ORIG_ID} leg venue/currency differs: {tuple(row)!r}")
    if con.execute(
        "SELECT COUNT(*) FROM ledger WHERE note LIKE ?", (MARKER + "%",)
    ).fetchone()[0]:
        raise RuntimeError("Re-sequence marker already present; refusing to run twice")
    prices = {row[0]: row[2] for row in legs}   # asset -> price string
    return prices


def build_rows(prices: dict[str, str]) -> list[RawRow]:
    doge_price = Decimal(prices["DOGE"])
    tics_price = Decimal(prices["TICS"])
    ts_rev = datetime.fromisoformat("2025-01-31T17:12:05")
    ts_v2 = datetime.fromisoformat("2025-03-11T20:18:38.500000")
    return [
        RawRow(id="HIST_REPAIR_REV_DOGE_TICS", timestamp=ts_rev, type="REVERSAL",
               asset="TICS", amount=Decimal("-24561.42"), currency="TICS",
               price=tics_price, venue="trust wallet", note=NOTE_REV),
        RawRow(id="HIST_REPAIR_REV_DOGE_TICS", timestamp=ts_rev, type="REVERSAL",
               asset="DOGE", amount=Decimal("4508"), currency="TICS",
               price=doge_price, venue="trust wallet", note=NOTE_REV),
        RawRow(id="HIST_REPAIR_DOGE_TICS_V2", timestamp=ts_v2, type="SELL",
               asset="DOGE", amount=Decimal("-4508"), currency="TICS",
               price=doge_price, venue="trust wallet", note=NOTE_V2),
        RawRow(id="HIST_REPAIR_DOGE_TICS_V2", timestamp=ts_v2, type="BUY",
               asset="TICS", amount=Decimal("24561.42"), currency="TICS",
               price=tics_price, venue="trust wallet", note=NOTE_V2),
    ]


def insert_rows(con: sqlite3.Connection, rows: list[RawRow]) -> None:
    now = datetime.now().isoformat()
    for it in rows:
        con.execute(
            """INSERT INTO ledger
               (id,timestamp,type,asset,amount,currency,price,venue,note,row_fp,imported_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (it.id, it.timestamp.isoformat(), it.type, it.asset.upper(),
             str(it.amount), it.currency.upper(),
             str(it.price) if it.price is not None else None,
             it.venue.lower(), it.note, it.fingerprint(), now),
        )


def main() -> int:
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_name(f"ledger_pre_resequence_repair_{stamp}.db")
    shutil.copy2(DB_PATH, backup)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        prices = validate_preconditions(con)
        new_rows = build_rows(prices)
        con.execute("BEGIN IMMEDIATE")
        insert_rows(con, new_rows)

        rows = load_rows(con)
        pos = {p.asset: p for p in compute_positions(rows)}
        errors: list[str] = []
        for asset, want in TARGET_CB.items():
            got = pos[asset].cost_basis if asset in pos else Decimal("0")
            if abs(got - want) > TOL:
                errors.append(f"{asset} cost_basis: want {want}, got {got}")
        bal = balances(con)
        for key, want in TARGET_BAL.items():
            got = bal.get(key, Decimal("0"))
            if got != want:
                errors.append(f"balance {key}: want {want}, got {got}")
        if errors:
            raise RuntimeError("Post-insert validation failed: " + "; ".join(errors))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"BACKUP={backup}")
    print("INSERTED=4")
    for asset in ("DOGE", "ETH", "TICS", "SOLX"):
        print(f"COST_BASIS={asset}|{TARGET_CB[asset]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
