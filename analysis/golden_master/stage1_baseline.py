"""Stage 1 migration — pre-migration baseline capture. Read-only against a
backup copy (never production directly). Captures everything needed to
prove the schema migration changes nothing accounting-relevant.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.model import RawRow
from core.reports.positions import compute_positions
from core.reports.holdings import compute_venue_holdings
from core.services.health_service import health_report


def _dec(v):
    return None if v is None else str(v)


def build_baseline(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    raw = conn.execute("SELECT * FROM ledger ORDER BY pk").fetchall()

    row_fps = [r["row_fp"] for r in raw]
    trade_ids = sorted({r["id"] for r in raw})

    rows = [
        RawRow(id=r["id"], timestamp=datetime.fromisoformat(r["timestamp"]), type=r["type"],
               asset=r["asset"], amount=Decimal(r["amount"]), currency=r["currency"],
               price=Decimal(r["price"]) if r["price"] else None, venue=r["venue"], note=r["note"])
        for r in raw
    ]
    conn.close()

    positions = compute_positions(rows)
    positions_snap = {
        p.asset: {"quantity": _dec(p.quantity), "wac": _dec(p.wac),
                   "cost_basis": _dec(p.cost_basis), "realized_pnl": _dec(p.realized_pnl)}
        for p in positions
    }

    holdings = compute_venue_holdings(rows)
    holdings_snap = {
        venue: {a: _dec(q) for a, q in sorted(assets.items())}
        for venue, assets in sorted(holdings.items())
    }

    health = health_report(rows)
    health_snap = sorted(
        [(r.values["severity"], r.values["kind"], r.values["trade_id"], r.values["asset"]) for r in health.rows]
    )

    return {
        "row_count": len(raw),
        "row_fps": row_fps,
        "trade_ids": trade_ids,
        "positions": positions_snap,
        "holdings": holdings_snap,
        "health_issues": health_snap,
        "health_issue_count": len(health.rows),
    }


if __name__ == "__main__":
    result = build_baseline(sys.argv[1])
    out = Path(__file__).parent / "stage1_baseline.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, sort_keys=True)
    print(f"written to {out}")
    print(f"row_count={result['row_count']}  assets={len(result['positions'])}  "
          f"venues={len(result['holdings'])}  health_issues={result['health_issue_count']}")
