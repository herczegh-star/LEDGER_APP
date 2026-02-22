"""UI Facade — single contract between UI and Core.

UI modules import *only* from this module (and core.constants).
No UI module should import core internals directly.

Public API:
    get_dashboard_snapshot(db_path, price_provider, fiat) -> DashboardSnapshotDTO
    add_trade(request, db_path) -> AddTradeResultDTO
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from core.constants import TRADE_TYPES
from core.reports.positions import compute_positions
from core.service import LedgerService
from core.services.portfolio_snapshot_service import get_portfolio_snapshot
from core.services.trade_service import AddTradeInput
from core.services.trade_service import add_trade as _core_add_trade

_ROI_PLACES = Decimal("0.01")
_ZERO = Decimal("0")
_FIAT_DEFAULT = frozenset({"EUR", "CZK"})


# ── DTOs ──────────────────────────────────────────────────────────────────────

@dataclass
class PositionDTO:
    """Per-asset position enriched with optional live-price data."""

    asset: str
    quantity: Decimal
    wac: Decimal                           # weighted average cost per unit
    cost_basis: Decimal
    realized_pnl: Decimal
    roi_realized: Optional[Decimal] = None  # realized_pnl / cost_basis * 100
    # Populated by live-price enrichment inside get_dashboard_snapshot():
    spot_price: Optional[Decimal] = None
    value: Optional[Decimal] = None        # quantity * spot_price
    unrealized_pnl: Optional[Decimal] = None
    roi_total: Optional[Decimal] = None    # unrealized_pnl / cost_basis (fraction)


@dataclass
class DashboardSnapshotDTO:
    """Full dashboard payload returned by get_dashboard_snapshot()."""

    invested: Dict[str, Decimal]
    net_flow: Dict[str, Decimal]
    assets_held: int
    top_position: Optional[Dict]
    realized_roi: Optional[Decimal]        # portfolio-level realized ROI in %pts
    positions: List[PositionDTO]
    # Aggregate live-price totals (None when no price provider):
    total_value: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    roi_total: Optional[Decimal] = None    # fraction: unrealized / total_cost


@dataclass
class AddTradeRequestDTO:
    """Input for add_trade().  Normalization happens inside the facade."""

    type: str                              # must be in TRADE_TYPES
    timestamp: datetime
    asset: str                             # base asset (e.g. "BTC")
    amount: Decimal                        # positive quantity
    currency: str                          # quote currency (e.g. "EUR")
    price: Optional[Decimal]              # informational unit price
    venue: str
    quote_amount: Optional[Decimal] = None  # actual fiat total; falls back to amount*price
    fee_amount: Optional[Decimal] = None
    fee_currency: Optional[str] = None
    note: Optional[str] = None


@dataclass
class AddTradeResultDTO:
    """Result of add_trade().  Never raises — errors go into error_message."""

    success: bool
    n_rows_added: int
    error_message: Optional[str] = None


# ── Public functions ───────────────────────────────────────────────────────────

def get_dashboard_snapshot(
    db_path: str,
    price_provider=None,
    fiat: str = "CZK",
) -> DashboardSnapshotDTO:
    """Load ledger, compute WAC positions, and optionally enrich with prices.

    Fields that require live prices (unrealized_pnl, roi_total, value,
    spot_price) are None when no price_provider is supplied or the asset
    price is unavailable.

    Args:
        db_path:        Path to the SQLite ledger.
        price_provider: Optional PriceProvider instance (from core.prices).
        fiat:           Fiat currency for price queries (default "CZK").

    Returns:
        DashboardSnapshotDTO with positions and aggregate totals.
    """
    fiat_uc = fiat.upper()

    svc = LedgerService(db_path)
    try:
        rows = svc.timeline()
    finally:
        svc.close()

    # ── WAC positions ─────────────────────────────────────────────────────────
    raw_positions = compute_positions(rows, _FIAT_DEFAULT)

    positions: List[PositionDTO] = []
    for pos in raw_positions:
        if pos.quantity == _ZERO:
            continue  # closed positions not shown on dashboard
        roi_real = (
            (pos.realized_pnl / pos.cost_basis * Decimal("100")).quantize(_ROI_PLACES)
            if pos.cost_basis != _ZERO
            else None
        )
        positions.append(PositionDTO(
            asset=pos.asset,
            quantity=pos.quantity,
            wac=pos.wac,
            cost_basis=pos.cost_basis,
            realized_pnl=pos.realized_pnl,
            roi_realized=roi_real,
        ))

    # ── Live-price enrichment (failure-tolerant) ──────────────────────────────
    if price_provider is not None and positions:
        assets = [p.asset for p in positions]
        prices = price_provider.get_prices(assets, fiat_uc)
        for p in positions:
            spot = prices.get(p.asset)
            p.spot_price = spot
            if spot is not None:
                p.value = p.quantity * spot
                if p.cost_basis > _ZERO:
                    p.unrealized_pnl = p.value - p.cost_basis
                    p.roi_total = p.unrealized_pnl / p.cost_basis

    # ── Portfolio-level aggregates ────────────────────────────────────────────
    snap = get_portfolio_snapshot(rows)

    vals = [p.value for p in positions if p.value is not None]
    total_cost = sum((p.cost_basis for p in positions), _ZERO)
    total_value: Optional[Decimal] = sum(vals, _ZERO) if vals else None
    unrealized: Optional[Decimal] = (
        (total_value - total_cost) if total_value is not None else None
    )
    roi_total: Optional[Decimal] = (
        (unrealized / total_cost)
        if (unrealized is not None and total_cost > _ZERO)
        else None
    )

    return DashboardSnapshotDTO(
        invested=snap.invested,
        net_flow=snap.net_flow,
        assets_held=snap.assets_held,
        top_position=snap.top_position,
        realized_roi=snap.roi,
        positions=positions,
        total_value=total_value,
        unrealized_pnl=unrealized,
        roi_total=roi_total,
    )


def add_trade(request: AddTradeRequestDTO, db_path: str) -> AddTradeResultDTO:
    """Validate, normalize, and append a trade to the ledger.

    Normalization applied before calling the core trade service:
        asset      → .upper().strip()
        currency   → .upper().strip()
        venue      → .lower().strip()

    Never raises — validation and domain errors are captured in
    AddTradeResultDTO.error_message with success=False.
    """
    if request.type not in TRADE_TYPES:
        return AddTradeResultDTO(
            success=False,
            n_rows_added=0,
            error_message=(
                f"Invalid trade type: {request.type!r}. "
                f"Must be one of {TRADE_TYPES}."
            ),
        )

    asset    = request.asset.upper().strip()
    currency = request.currency.upper().strip()
    venue    = request.venue.lower().strip()

    quote_amount = request.quote_amount
    if quote_amount is None and request.price is not None:
        quote_amount = abs(request.amount * request.price)
    if quote_amount is None:
        quote_amount = _ZERO

    inp = AddTradeInput(
        type=request.type,
        timestamp=request.timestamp,
        base_asset=asset,
        base_amount=abs(request.amount),
        quote_currency=currency,
        quote_amount=quote_amount,
        venue=venue,
        fee_amount=request.fee_amount,
        fee_currency=request.fee_currency,
        note=request.note,
    )

    try:
        result = _core_add_trade(db_path, inp)
    except ValueError as exc:
        return AddTradeResultDTO(
            success=False,
            n_rows_added=0,
            error_message=str(exc),
        )

    return AddTradeResultDTO(
        success=True,
        n_rows_added=result.inserted,
    )
