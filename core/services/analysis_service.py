"""Analysis service — read-only projections above the accounting layer.

Provides hypothetical sell simulation functions.
Never writes to the ledger, never creates RawRow objects,
never calls compute_positions or any WAC engine.

All inputs are PositionDTOs already produced by the core accounting path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from core.services.ui_facade import VenueDashboardDTO

_ZERO = Decimal("0")

MODES = frozenset({"all", "profitable_only", "loss_only"})


@dataclass
class SellSimRow:
    """Single-asset projection row in a sell simulation result."""

    asset: str
    quantity: Decimal
    wac: Decimal                          # weighted-average cost per unit
    spot_price: Optional[Decimal]         # None when price is unavailable
    value: Optional[Decimal]              # quantity × spot_price; None when spot missing
    cost_basis: Decimal                   # total cost basis in fiat
    simulated_pnl: Optional[Decimal]      # value − cost_basis; None when spot missing
    roi: Optional[Decimal]                # simulated_pnl / cost_basis (fraction); None when cost_basis=0


@dataclass
class SellSimResult:
    """Output of simulate_sell()."""

    venue: str
    mode: str                             # "all" | "profitable_only" | "loss_only"
    rows: List[SellSimRow]
    total_value: Optional[Decimal]        # partial sum; None when no rows have a price
    total_cost_basis: Decimal
    total_simulated_pnl: Optional[Decimal]  # partial sum; None when no rows have a price
    missing_prices: List[str] = field(default_factory=list)


def simulate_sell(
    vdto: "VenueDashboardDTO",
    mode: str = "all",
) -> SellSimResult:
    """Project simulated sell PnL for eligible positions in *vdto*.

    Pure read-only: no ledger writes, no WAC computation, no HTTP calls.
    Uses PositionDTO.unrealized_pnl (= value − cost_basis) as simulated PnL.

    Args:
        vdto: VenueDashboardDTO from DashboardSnapshotDTO.by_venue.
        mode: "all" | "profitable_only" | "loss_only"

    Returns:
        SellSimResult with per-asset rows sorted by asset name.
        Excludes positions with quantity <= 0.
        In "profitable_only" / "loss_only" modes, positions without a spot
        price are excluded (profitability unknown).  In "all" mode they are
        included with simulated_pnl=None and noted in missing_prices.
        ROI is None when cost_basis == 0 (zero-cost / wallet-only assets).
    """
    rows: List[SellSimRow] = []
    missing: List[str] = []

    for p in vdto.positions:
        if p.quantity <= _ZERO:
            continue                       # closed or over-sold: skip

        sim_pnl: Optional[Decimal] = p.unrealized_pnl  # None when no spot price

        if p.spot_price is None:
            missing.append(p.asset)

        if mode == "profitable_only":
            if sim_pnl is None or sim_pnl <= _ZERO:
                continue
        elif mode == "loss_only":
            if sim_pnl is None or sim_pnl >= _ZERO:
                continue
        # "all": include all positions with qty > 0; no-price positions have pnl=None

        roi: Optional[Decimal] = None
        if p.cost_basis > _ZERO and sim_pnl is not None:
            roi = sim_pnl / p.cost_basis

        rows.append(SellSimRow(
            asset=p.asset,
            quantity=p.quantity,
            wac=p.wac,
            spot_price=p.spot_price,
            value=p.value,
            cost_basis=p.cost_basis,
            simulated_pnl=sim_pnl,
            roi=roi,
        ))

    rows.sort(key=lambda r: r.asset)

    total_cost = sum((r.cost_basis for r in rows), _ZERO)
    val_list = [r.value for r in rows if r.value is not None]
    total_value: Optional[Decimal] = sum(val_list, _ZERO) if val_list else None
    pnl_list = [r.simulated_pnl for r in rows if r.simulated_pnl is not None]
    total_pnl: Optional[Decimal] = sum(pnl_list, _ZERO) if pnl_list else None

    return SellSimResult(
        venue=vdto.venue,
        mode=mode,
        rows=rows,
        total_value=total_value,
        total_cost_basis=total_cost,
        total_simulated_pnl=total_pnl,
        missing_prices=missing,
    )
