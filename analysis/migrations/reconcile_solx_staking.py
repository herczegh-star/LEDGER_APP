"""One-off append-only reconciliation of the SOLX staking-reward balance.

The Solaxy Wallet holds 3 704 848.73 SOLX (3 704 847 staked + 1.73 liquid) as
observed on 2026-09-01.  The ledger recorded only the 1 989 162 SOLX acquired
via the ETH swap (HIST_REPAIR_SOLX).  The 1 715 686.73 SOLX difference is
cumulative staking rewards, not a new fiat/ETH investment.

This migration appends ONE STAKING row (same shape as the 11 existing STAKING
rows in the ledger): single row, no fiat leg, currency = asset, price = 0.
STAKING carries zero cost basis, so SOLX quantity rises and SOLX WAC falls
while the cost basis stays 104 029.95571950768353 CZK.

It does not change application logic or the WAC engine.  It backs up the DB,
validates preconditions, inserts in one transaction, and rolls back unless the
approved SOLX balance / cost basis are reached.
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

ROW_ID = "20260901_000000_SOLAXY WALLET_STAKING_001"
NOTE = (
    "SOLX staking rewards -- aggregate reconciliation to Solaxy Wallet observed "
    "balance 3704848.73 SOLX (3704847 staked + 1.73 available) as of 2026-09-01; "
    "cumulative staking increment, individual reward events/timestamps unknown; "
    "zero cost basis, no new investment"
)

AMOUNT = Decimal("1715686.73")
TARGET_SOLX_QTY = Decimal("3704848.73")
TARGET_SOLX_CB = Decimal("104029.9557195076835275483401")
TARGET_SOLX_WAC = Decimal("0.02807940709619949410")
WAC_TOL = Decimal("0.00000000001")

FROZEN_CB = {
    "DOGE": Decimal("42694.54618592665640300694882"),
    "ETH":  Decimal("83979.8957396079242454425509"),
    "TICS": Decimal("134246.0541718231852190879683"),
}
FROZEN_QTY = {
    "DOGE": Decimal("9679.92556595"),
    "ETH":  Decimal("1.918869329"),
    "TICS": Decimal("69231.67"),
}
TARGET_BAL = {
    ("solaxy wallet", "SOLX"): TARGET_SOLX_QTY,
    ("trust wallet", "DOGE"): Decimal("0"),
    ("anycoin", "DOGE"): Decimal("9029.15556595"),
    ("trust wallet", "TICS"): Decimal("0"),
    ("qubetics wallet", "TICS"): Decimal("69231.67"),
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


def validate_preconditions(con: sqlite3.Connection) -> None:
    if con.execute("SELECT COUNT(*) FROM ledger WHERE id = ?", (ROW_ID,)).fetchone()[0]:
        raise RuntimeError(f"Row id {ROW_ID} already present; refusing to run twice")
    solx = con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM ledger WHERE asset='SOLX'"
    ).fetchone()[0]
    if Decimal(str(solx)) != Decimal("1989162"):
        raise RuntimeError(f"Pre-insert SOLX qty expected 1989162, found {solx}")


def build_row() -> RawRow:
    return RawRow(
        id=ROW_ID,
        timestamp=datetime.fromisoformat("2026-09-01T00:00:00"),
        type="STAKING",
        asset="SOLX",
        amount=AMOUNT,
        currency="SOLX",
        price=Decimal("0"),
        venue="solaxy wallet",
        note=NOTE,
    )


def insert_row(con: sqlite3.Connection, it: RawRow) -> None:
    now = datetime.now().isoformat()
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
    backup = DB_PATH.with_name(f"ledger_pre_solx_staking_reconcile_{stamp}.db")
    shutil.copy2(DB_PATH, backup)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        validate_preconditions(con)
        con.execute("BEGIN IMMEDIATE")
        insert_row(con, build_row())

        rows = load_rows(con)
        pos = {p.asset: p for p in compute_positions(rows)}
        errors: list[str] = []

        s = pos["SOLX"]
        if s.quantity != TARGET_SOLX_QTY:
            errors.append(f"SOLX qty: want {TARGET_SOLX_QTY}, got {s.quantity}")
        if s.cost_basis != TARGET_SOLX_CB:
            errors.append(f"SOLX cost_basis: want {TARGET_SOLX_CB}, got {s.cost_basis}")
        if abs(s.wac - TARGET_SOLX_WAC) > WAC_TOL:
            errors.append(f"SOLX wac: want ~{TARGET_SOLX_WAC}, got {s.wac}")

        for asset, want in FROZEN_CB.items():
            if pos[asset].cost_basis != want:
                errors.append(f"{asset} cost_basis changed: want {want}, got {pos[asset].cost_basis}")
        for asset, want in FROZEN_QTY.items():
            if pos[asset].quantity != want:
                errors.append(f"{asset} qty changed: want {want}, got {pos[asset].quantity}")

        bal = balances(con)
        for key, want in TARGET_BAL.items():
            if bal.get(key, Decimal("0")) != want:
                errors.append(f"balance {key}: want {want}, got {bal.get(key, Decimal('0'))}")

        if errors:
            raise RuntimeError("Post-insert validation failed: " + "; ".join(errors))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"BACKUP={backup}")
    print("INSERTED=1")
    print(f"SOLX_QTY={TARGET_SOLX_QTY}")
    print(f"SOLX_COST_BASIS={TARGET_SOLX_CB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
