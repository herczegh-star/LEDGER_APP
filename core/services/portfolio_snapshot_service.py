"""Portfolio snapshot service.

Aggregates ledger rows into a single PortfolioSnapshot DTO.
All computation happens here; UI only renders the result.

Usage:
    svc = LedgerService(db_path)
    try:
        rows = svc.timeline()
    finally:
        svc.close()
    snap = get_portfolio_snapshot(rows)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set

from core.reports.positions import compute_positions

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


_ROI_PLACES = Decimal("0.01")


@dataclass
class PortfolioSnapshot:
    """Aggregated portfolio summary derived purely from ledger rows.

    Fields:
        invested:     Gross fiat outflows per currency (always >= 0).
                      Empty dict when ledger is empty.
        net_flow:     Net fiat flow per currency (negative = net spent).
                      Empty dict when ledger is empty.
        assets_held:  Number of non-fiat positions with quantity > 0.
        top_position: Asset with the highest cost_basis (cost_basis > 0),
                      or None when there are no open/non-zero-cost positions.
                      Dict keys: "asset" (str), "cost_basis" (Decimal).
        roi:          Total portfolio ROI = (sum realized_pnl / sum cost_basis) * 100,
                      rounded to 2 decimal places. None when total cost_basis == 0.
    """

    invested:     Dict[str, Decimal]
    net_flow:     Dict[str, Decimal]
    assets_held:  int
    top_position: Optional[Dict]
    roi:          Optional[Decimal] = None


def get_portfolio_snapshot(
    rows: list,
    fiat: Optional[Set[str]] = None,
    precomputed_positions: Optional[List] = None,
) -> PortfolioSnapshot:
    """Compute a portfolio snapshot from ledger rows.

    Args:
        rows:                  RawRow list from svc.timeline().
        fiat:                  Fiat asset set (default: {"EUR", "CZK"}).
        precomputed_positions: Optional pre-computed compute_positions() result.
                               When provided, skips the internal compute_positions()
                               call to avoid redundant recomputation.

    Returns:
        PortfolioSnapshot DTO with aggregated totals and position summary.
    """
    _fiat = frozenset(a.upper() for a in (fiat or _FIAT_DEFAULT))

    # ── Invested + Net flow — single pass over fiat rows ─────────────────────
    # Replaces netto_invested_report() + cashflow_report() (each called cashflow()
    # internally). We first bucket fiat rows by day (same as cashflow(day)) then
    # split daily nets by sign — identical logic, no separate function calls.
    _daily_fiat: Dict = defaultdict(Decimal)
    for row in rows:
        if row.asset in _fiat:
            _daily_fiat[(row.timestamp.strftime("%Y-%m-%d"), row.asset)] += row.amount

    _invested_acc: Dict[str, Decimal] = defaultdict(Decimal)
    _net_flow_acc: Dict[str, Decimal] = defaultdict(Decimal)
    for (_, cur), daily_net in _daily_fiat.items():
        _net_flow_acc[cur] += daily_net
        if daily_net < Decimal("0"):
            _invested_acc[cur] += -daily_net  # store as positive

    invested: Dict[str, Decimal] = {k: v for k, v in _invested_acc.items() if v}
    net_flow: Dict[str, Decimal] = {k: v for k, v in _net_flow_acc.items() if v}

    # ── Positions ─────────────────────────────────────────────────────────────
    positions = (
        precomputed_positions
        if precomputed_positions is not None
        else compute_positions(rows, _fiat)
    )

    assets_held = sum(1 for p in positions if p.quantity > Decimal("0"))

    # Top position: the asset with the highest cost_basis (> 0)
    candidates = [p for p in positions if p.cost_basis > Decimal("0")]
    top_position: Optional[Dict] = None
    if candidates:
        top = max(candidates, key=lambda p: p.cost_basis)
        top_position = {
            "asset":      top.asset,
            "cost_basis": top.cost_basis,
        }

    # Portfolio ROI: total realized P&L relative to total cost basis
    total_realized_pnl = sum((p.realized_pnl for p in positions), Decimal("0"))
    total_cost_basis   = sum((p.cost_basis   for p in positions), Decimal("0"))
    roi: Optional[Decimal] = (
        (total_realized_pnl / total_cost_basis * Decimal("100")).quantize(_ROI_PLACES)
        if total_cost_basis != Decimal("0")
        else None
    )

    return PortfolioSnapshot(
        invested=invested,
        net_flow=net_flow,
        assets_held=assets_held,
        top_position=top_position,
        roi=roi,
    )
