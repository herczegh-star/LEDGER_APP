"""Golden-master snapshot for the cash-accounts feature, Phase 0.

Freezes every deterministic, ledger-derived number produced by the CURRENT
(pre-cash-accounts) system, so a future regression test can assert byte-for-byte
equality after the cash read-layer is introduced.

READ-ONLY. Runs exclusively against a frozen backup copy of the production DB
(never the live ~/.ledger_app/ledger.db). Writes nothing back to any database.

Deliberately excludes live-price-dependent fields (spot_price, value,
unrealized_pnl, roi_total) — those change every time market prices change and
are NOT a valid golden-master target. Only ledger-derived (deterministic)
numbers are frozen: quantity, WAC, cost_basis, realized_pnl, roi_realized,
invested, net_flow, physical venue holdings.

Usage:
    python analysis/golden_master/snapshot_2026_09_18.py <path-to-backup-db>
"""
from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.service import LedgerService
from core.reports.positions import compute_positions
from core.reports.holdings import compute_venue_holdings
from core.services.ui_facade import get_dashboard_snapshot


def _dec(v):
    """Serialize Decimal (or None) as an exact string — never float."""
    if v is None:
        return None
    return str(v)


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_snapshot(db_path: str) -> dict:
    svc = LedgerService(db_path)
    try:
        rows = svc.timeline()
    finally:
        svc.close()

    # ── Raw counts / structural facts ──────────────────────────────────────
    total_rows = len(rows)
    distinct_venues = sorted({r.venue for r in rows})
    distinct_assets = sorted({r.asset for r in rows})
    distinct_types = sorted({r.type for r in rows})
    distinct_currencies = sorted({r.currency for r in rows})

    # ── compute_positions() — production WAC engine ───────────────────────
    positions = compute_positions(rows)
    positions_snapshot = [
        {
            "asset": p.asset,
            "quantity": _dec(p.quantity),
            "wac": _dec(p.wac),
            "cost_basis": _dec(p.cost_basis),
            "realized_pnl": _dec(p.realized_pnl),
        }
        for p in positions
    ]

    # ── compute_venue_holdings() — physical, TRANSFER-aware ────────────────
    holdings = compute_venue_holdings(rows)
    holdings_snapshot = {
        venue: {asset: _dec(qty) for asset, qty in sorted(assets.items())}
        for venue, assets in sorted(holdings.items())
    }

    # ── get_dashboard_snapshot() — no price_provider => fully deterministic ─
    snap = get_dashboard_snapshot(db_path, price_provider=None, fiat="CZK")

    dashboard_snapshot = {
        "invested": {k: _dec(v) for k, v in sorted(snap.invested.items())},
        "net_flow": {k: _dec(v) for k, v in sorted(snap.net_flow.items())},
        "assets_held": snap.assets_held,
        "top_position": (
            {"asset": snap.top_position["asset"], "cost_basis": _dec(snap.top_position["cost_basis"])}
            if snap.top_position else None
        ),
        "realized_roi": _dec(snap.realized_roi),
        "positions": [
            {
                "asset": p.asset,
                "quantity": _dec(p.quantity),
                "wac": _dec(p.wac),
                "cost_basis": _dec(p.cost_basis),
                "realized_pnl": _dec(p.realized_pnl),
                "roi_realized": _dec(p.roi_realized),
            }
            for p in snap.positions
        ],
        "by_venue": {
            venue: {
                "positions": [
                    {
                        "asset": p.asset,
                        "quantity": _dec(p.quantity),
                        "wac": _dec(p.wac),
                        "cost_basis": _dec(p.cost_basis),
                        "realized_pnl": _dec(p.realized_pnl),
                        "roi_realized": _dec(p.roi_realized),
                    }
                    for p in vdto.positions
                ],
                "holdings": {a: _dec(q) for a, q in sorted(vdto.holdings.items())},
                "cost_basis_total": _dec(vdto.cost_basis_total),
                "assets_held": vdto.assets_held,
                "invested": {k: _dec(v) for k, v in sorted(vdto.invested.items())},
                "net_flow": {k: _dec(v) for k, v in sorted(vdto.net_flow.items())},
            }
            for venue, vdto in sorted(snap.by_venue.items())
        },
    }

    return {
        "_meta": {
            "purpose": "Phase 0 golden master — cash accounts feature — pre-implementation baseline",
            "source_db_path": db_path,
            "source_db_sha256": _sha256_of_file(db_path),
            "total_rows": total_rows,
            "distinct_venues": distinct_venues,
            "distinct_assets": distinct_assets,
            "distinct_types": distinct_types,
            "distinct_currencies": distinct_currencies,
            "note": (
                "price_provider=None by design — spot_price/value/unrealized_pnl/"
                "roi_total are excluded (non-deterministic, market-price dependent). "
                "Only ledger-derived numbers are frozen here."
            ),
        },
        "compute_positions": positions_snapshot,
        "compute_venue_holdings": holdings_snapshot,
        "dashboard_snapshot": dashboard_snapshot,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python snapshot_2026_09_18.py <path-to-backup-db>", file=sys.stderr)
        sys.exit(1)

    db_path = sys.argv[1]
    result = build_snapshot(db_path)

    out_path = Path(__file__).parent / "snapshot_2026_09_18.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, sort_keys=True)

    print(f"Golden master written to: {out_path}")
    print(f"Total ledger rows: {result['_meta']['total_rows']}")
    print(f"Distinct venues:   {result['_meta']['distinct_venues']}")
    print(f"Distinct assets:   {result['_meta']['distinct_assets']}")
    print(f"Positions count:   {len(result['compute_positions'])}")
