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

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, FrozenSet, Optional, Set

from core.reports.cashflow import cashflow_report
from core.reports.netto_invested import netto_invested_report
from core.reports.positions import compute_positions

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


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
    """

    invested:     Dict[str, Decimal]
    net_flow:     Dict[str, Decimal]
    assets_held:  int
    top_position: Optional[Dict]


def get_portfolio_snapshot(
    rows: list,
    fiat: Optional[Set[str]] = None,
) -> PortfolioSnapshot:
    """Compute a portfolio snapshot from ledger rows.

    Args:
        rows: RawRow list from svc.timeline().
        fiat: Fiat asset set (default: {"EUR", "CZK"}).

    Returns:
        PortfolioSnapshot DTO with aggregated totals and position summary.
    """
    _fiat = frozenset(a.upper() for a in (fiat or _FIAT_DEFAULT))

    # ── Invested (gross fiat outflows) ────────────────────────────────────────
    # netto_invested_report totals[currency]["invested_amount"] = gross outflow
    ni_report = netto_invested_report(rows, bucket="day", fiat=_fiat)
    invested: Dict[str, Decimal] = {}
    if ni_report.totals:
        for currency, metrics in ni_report.totals.items():
            v = metrics.get("invested_amount", Decimal("0"))
            if v != Decimal("0"):
                invested[currency] = v

    # ── Net flow (sum of all fiat movements) ──────────────────────────────────
    # cashflow_report totals[currency]["net_amount"] = signed net fiat flow
    cf_report = cashflow_report(rows, bucket="day", fiat=_fiat)
    net_flow: Dict[str, Decimal] = {}
    if cf_report.totals:
        for currency, metrics in cf_report.totals.items():
            v = metrics.get("net_amount", Decimal("0"))
            if v != Decimal("0"):
                net_flow[currency] = v

    # ── Positions ─────────────────────────────────────────────────────────────
    positions = compute_positions(rows, _fiat)

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

    return PortfolioSnapshot(
        invested=invested,
        net_flow=net_flow,
        assets_held=assets_held,
        top_position=top_position,
    )
