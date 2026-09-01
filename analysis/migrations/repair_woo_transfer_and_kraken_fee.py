"""One-off append-only repair: Kraken fee-currency correction + WOO withdrawal.

Part 1 - Kraken fee currency correction (trade 20260325_170034_KRAKEN_TRANSFER_001):
    The withdrawal fee on the 100 USDC Kraken -> Revolut transfer was booked as
    FEE USDT -0.5902 by mistake; no USDT balance ever existed.  Append a
    REVERSAL USDT +0.5902 (cancels the mis-booked fee) and the correct
    FEE USDC -0.5902, sharing the transfer id, one second later.

Part 2 - WOO delisting withdrawal from Anycoin to the Ledger cold wallet:
    WOO was delisted on Anycoin and the whole balance was withdrawn to the
    Ledger wallet.  A flat 35 WOO network/withdrawal fee was paid.  Append
    FEE WOO -35 on anycoin, then a TRANSFER of the net 60501.27682432 WOO from
    anycoin to "ledger wallet".  Exact historical date unknown -> reconciliation
    timestamp 2026-09-01T00:00:00.

Relies on the negative non-fiat FEE support in compute_positions /
compute_transfer_costs and on the health fee-correction guard.  Backs up the
DB, inserts all 5 rows in one transaction, rolls back unless every approved
target is reached.
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
from core.reports.positions import compute_positions, compute_transfer_costs
from core.reports.holdings import compute_venue_holdings
from core.services.health_service import health_report

DB_PATH = Path(r"C:\Users\hercz\.ledger_app\ledger.db")

KRAKEN_TID = "20260325_170034_KRAKEN_TRANSFER_001"
KRAKEN_NOTE = (
    "fee currency correction: the withdrawal fee on the 100 USDC Kraken->Revolut "
    "transfer was booked as FEE USDT -0.5902 by mistake; no USDT balance ever "
    "existed; reversing the USDT fee and re-booking it as the correct "
    "FEE USDC -0.5902"
)
WOO_FEE_ID = "20260901_000000_ANYCOIN_WOO_FEE_001"
WOO_TRANSFER_ID = "20260901_000000_ANYCOIN_TRANSFER_001"
WOO_FEE_NOTE = (
    "WOO delisted on Anycoin; flat network/withdrawal fee paid on the withdrawal "
    "to the Ledger cold wallet; historical reconstruction, exact date unknown, "
    "dated to reconciliation timestamp 2026-09-01"
)
WOO_TR_NOTE = (
    "WOO delisted on Anycoin; full balance net of the 35 WOO withdrawal fee moved "
    "to the Ledger cold wallet ('ledger wallet', distinct from 'tresor'); "
    "historical reconstruction to observed Ledger Wallet balance 60501.27682432 "
    "as of 2026-09-01, exact transfer date unknown"
)

MOVE = Decimal("60501.27682432")


def _rows(con):
    out = []
    for r in con.execute(
        "SELECT id,timestamp,type,asset,amount,currency,price,venue,note FROM ledger"
    ):
        out.append(RawRow(
            id=r[0], timestamp=datetime.fromisoformat(r[1]), type=r[2], asset=r[3],
            amount=Decimal(r[4]), currency=r[5],
            price=Decimal(r[6]) if r[6] is not None else None, venue=r[7], note=r[8],
        ))
    return out


def _new_rows() -> list[RawRow]:
    t_kraken = datetime.fromisoformat("2026-03-25T17:00:35")
    t_woo = datetime.fromisoformat("2026-09-01T00:00:00")
    return [
        RawRow(id=KRAKEN_TID, timestamp=t_kraken, type="REVERSAL", asset="USDT",
               amount=Decimal("0.5902"), currency="USDT", price=Decimal("1"),
               venue="kraken", note=KRAKEN_NOTE),
        RawRow(id=KRAKEN_TID, timestamp=t_kraken, type="FEE", asset="USDC",
               amount=Decimal("-0.5902"), currency="USDC", price=Decimal("1"),
               venue="kraken", note=KRAKEN_NOTE),
        RawRow(id=WOO_FEE_ID, timestamp=t_woo, type="FEE", asset="WOO",
               amount=Decimal("-35"), currency="WOO", price=Decimal("0"),
               venue="anycoin", note=WOO_FEE_NOTE),
        RawRow(id=WOO_TRANSFER_ID, timestamp=t_woo, type="TRANSFER", asset="WOO",
               amount=-MOVE, currency="WOO", price=Decimal("0"),
               venue="anycoin", note=WOO_TR_NOTE),
        RawRow(id=WOO_TRANSFER_ID, timestamp=t_woo, type="TRANSFER", asset="WOO",
               amount=MOVE, currency="WOO", price=Decimal("0"),
               venue="ledger wallet", note=WOO_TR_NOTE),
    ]


def _validate_pre(con):
    if con.execute(
        "SELECT COUNT(*) FROM ledger WHERE id=? AND "
        "((type='REVERSAL' AND asset='USDT') OR (type='FEE' AND asset='USDC'))",
        (KRAKEN_TID,),
    ).fetchone()[0]:
        raise RuntimeError("Kraken correction already present")
    orig = con.execute(
        "SELECT COUNT(*) FROM ledger WHERE id=? AND type='FEE' AND asset='USDT'",
        (KRAKEN_TID,),
    ).fetchone()[0]
    if orig != 1:
        raise RuntimeError(f"expected the 1 mis-booked FEE USDT row, found {orig}")
    if con.execute("SELECT COUNT(*) FROM ledger WHERE id IN (?,?)",
                   (WOO_FEE_ID, WOO_TRANSFER_ID)).fetchone()[0]:
        raise RuntimeError("WOO repair rows already present")
    usdt = con.execute("SELECT COUNT(*) FROM ledger WHERE asset='USDT' OR currency='USDT'").fetchone()[0]
    if usdt != 1:
        raise RuntimeError(f"expected exactly 1 USDT row pre-repair, found {usdt}")
    woo = sum(
        (Decimal(a) for (a,) in con.execute("SELECT amount FROM ledger WHERE asset='WOO'")),
        Decimal("0"),
    )
    if woo != Decimal("60536.27682432"):
        raise RuntimeError(f"pre-repair WOO qty expected 60536.27682432, found {woo}")


def _insert(con, rows):
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


def _check_post(con):
    rows = _rows(con)
    pos = {p.asset: p for p in compute_positions(rows)}
    holds = compute_venue_holdings(rows)
    errs = []

    woo = pos["WOO"]
    if woo.quantity != MOVE:
        errs.append(f"global WOO qty want {MOVE} got {woo.quantity}")
    if abs(woo.cost_basis - Decimal("112101.1494611684435535437180")) > Decimal("1e-6"):
        errs.append(f"WOO cost_basis want ~112101.14946116844 got {woo.cost_basis}")
    if abs(woo.wac - Decimal("1.8528724573314556")) > Decimal("1e-10"):
        errs.append(f"WOO wac want ~1.8528724573314556 got {woo.wac}")
    if woo.realized_pnl != Decimal("0"):
        errs.append(f"WOO realized_pnl want 0 got {woo.realized_pnl}")

    if holds.get("anycoin", {}).get("WOO") is not None:
        errs.append(f"anycoin WOO holding want 0 got {holds['anycoin']['WOO']}")
    if holds.get("ledger wallet", {}).get("WOO") != MOVE:
        errs.append(f"ledger wallet WOO want {MOVE} got {holds.get('ledger wallet', {}).get('WOO')}")

    usdc = pos["USDC"]
    if usdc.quantity != Decimal("153.671047"):
        errs.append(f"USDC qty want 153.671047 got {usdc.quantity}")
    if holds.get("kraken", {}).get("USDC") != Decimal("10.4323"):
        errs.append(f"kraken USDC holding want 10.4323 got {holds.get('kraken', {}).get('USDC')}")
    if "USDT" in holds.get("kraken", {}):
        errs.append(f"kraken USDT holding should be gone, got {holds['kraken']['USDT']}")
    usdt = pos.get("USDT")
    if usdt is not None and usdt.quantity != Decimal("0"):
        errs.append(f"USDT position qty want 0 got {usdt.quantity}")

    hr = health_report(rows)
    for row in hr.rows:
        v = row.values
        if v["severity"] == "error" and v["kind"] == "oversell" and v["asset"] == "USDT":
            errs.append("Health still reports oversell USDT")
        if v["severity"] == "error" and v["kind"] == "missing_quote_leg" and v["trade_id"] == KRAKEN_TID:
            errs.append("Health reports missing_quote_leg from the Kraken correction")

    if errs:
        raise RuntimeError("post-insert validation failed: " + "; ".join(errs))
    return woo, usdc


def main() -> int:
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_name(f"ledger_pre_woo_usdt_repair_{stamp}.db")
    shutil.copy2(DB_PATH, backup)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        _validate_pre(con)
        con.execute("BEGIN IMMEDIATE")
        _insert(con, _new_rows())
        woo, usdc = _check_post(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    print(f"BACKUP={backup}")
    print("INSERTED=5")
    print(f"WOO_QTY={woo.quantity}")
    print(f"WOO_COST_BASIS={woo.cost_basis}")
    print(f"WOO_WAC={woo.wac}")
    print(f"USDC_QTY={usdc.quantity}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
