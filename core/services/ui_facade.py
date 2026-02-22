"""UI Facade — single contract between UI and Core.

UI modules import *only* from this module (and core.constants).
No UI module should import core internals directly.

Public API:
    create_app_context(config_path) -> AppContextDTO
    get_dashboard_snapshot(db_path, price_provider, fiat) -> DashboardSnapshotDTO
    add_trade(request, db_path) -> AddTradeResultDTO
    get_ledger_rows(db_path) -> list
    get_health_report(db_path) -> TableReport
    get_positions_table_report(db_path) -> TableReport
    get_time_series_report(db_path, kind, bucket, fiat) -> TimeSeriesReport
    export_table_report_to_csv(report, out_path) -> str
    export_ledger_to_csv(db_path, out_path) -> str
    export_cashflow_to_csv(db_path, out_path, bucket, fiat) -> str
    export_netto_invested_to_csv(db_path, out_path, bucket, fiat) -> str
    export_positions_to_csv(db_path, out_path) -> str
    import_file(db_path, file_path, sheet_name) -> list
    reverse_trade(db_path, trade_id) -> list
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from core.constants import TRADE_TYPES
from core.ledger_store import LedgerStore
from core.model import RawRow
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

    Enforces unified_format_raw compatibility rules by type:

        BUY / SELL:  amount != 0, effective price > 0 (quote_amount > 0),
                     currency required.  Produces double-entry rows.
        TRANSFER:    amount != 0, price >= 0 (0 is explicitly allowed).
                     Single row written directly.
        FEE:         amount != 0, price >= 0.
                     Single row written directly.
        REVERSAL:    always rejected here — use reverse_trade() instead.

    Normalization (always applied):
        asset      → .upper().strip()
        currency   → .upper().strip()
        venue      → .lower().strip()

    Never raises — all errors captured in AddTradeResultDTO.error_message.
    """
    # ── 1. Type ───────────────────────────────────────────────────────────────
    if request.type not in TRADE_TYPES:
        return AddTradeResultDTO(
            success=False,
            n_rows_added=0,
            error_message=(
                f"Invalid trade type: {request.type!r}. "
                f"Must be one of {TRADE_TYPES}."
            ),
        )

    if request.type == "REVERSAL":
        return AddTradeResultDTO(
            success=False,
            n_rows_added=0,
            error_message=(
                "REVERSAL cannot be created via add_trade. "
                "Use reverse_trade(db_path, trade_id) instead."
            ),
        )

    # ── 2. Normalize ──────────────────────────────────────────────────────────
    asset    = (request.asset    or "").upper().strip()
    currency = (request.currency or "").upper().strip()
    venue    = (request.venue    or "").lower().strip()

    # ── 3. Common validation ──────────────────────────────────────────────────
    if not asset:
        return AddTradeResultDTO(
            success=False, n_rows_added=0,
            error_message="asset must not be empty",
        )
    if not venue:
        return AddTradeResultDTO(
            success=False, n_rows_added=0,
            error_message="venue must not be empty",
        )
    if request.amount == _ZERO:
        return AddTradeResultDTO(
            success=False, n_rows_added=0,
            error_message="amount must not be zero",
        )

    # ── 4. BUY / SELL — double-entry via trade_service ────────────────────────
    if request.type in ("BUY", "SELL"):
        if not currency:
            return AddTradeResultDTO(
                success=False, n_rows_added=0,
                error_message="currency must not be empty for BUY/SELL",
            )

        # Derive effective quote_amount (fiat total paid/received).
        # Falls back to abs(amount * price); both 0 → explicit FAIL.
        quote_amount = request.quote_amount
        if quote_amount is None and request.price is not None:
            quote_amount = abs(request.amount * request.price)
        if quote_amount is None or quote_amount <= _ZERO:
            return AddTradeResultDTO(
                success=False, n_rows_added=0,
                error_message=(
                    "price must be > 0 for BUY/SELL trades "
                    "(provide price > 0 or quote_amount > 0)"
                ),
            )

        fee_currency: Optional[str] = None
        if request.fee_currency:
            fee_currency = request.fee_currency.upper().strip() or None

        inp = AddTradeInput(
            type=request.type,
            timestamp=request.timestamp,
            base_asset=asset,
            base_amount=abs(request.amount),
            quote_currency=currency,
            quote_amount=quote_amount,
            venue=venue,
            fee_amount=request.fee_amount,
            fee_currency=fee_currency,
            note=request.note,
        )

        try:
            result = _core_add_trade(db_path, inp)
        except ValueError as exc:
            return AddTradeResultDTO(
                success=False, n_rows_added=0,
                error_message=str(exc),
            )

        return AddTradeResultDTO(success=True, n_rows_added=result.inserted)

    # ── 5. TRANSFER / FEE — single raw row ────────────────────────────────────
    # price >= 0; 0 is explicitly allowed for TRANSFER (no market price).
    price: Decimal = request.price if request.price is not None else _ZERO
    if price < _ZERO:
        return AddTradeResultDTO(
            success=False, n_rows_added=0,
            error_message="price must be >= 0 for TRANSFER/FEE",
        )

    # currency defaults to asset when not provided (common for FEE/TRANSFER).
    eff_currency = currency or asset

    # FEE with price=0 is only valid when asset == currency (fiat-denominated
    # fee where the amount already expresses the fiat cost; no conversion needed).
    # If asset != currency the caller must supply price > 0.
    if request.type == "FEE" and price == _ZERO and asset != eff_currency:
        return AddTradeResultDTO(
            success=False, n_rows_added=0,
            error_message=(
                "FEE with price=0 is only allowed when asset == currency "
                "(fiat-denominated fee). "
                f"Got asset={asset!r}, currency={eff_currency!r}. "
                "Provide price > 0 to express the fiat value of the fee."
            ),
        )

    row = RawRow(
        id=str(uuid.uuid4()),
        timestamp=request.timestamp,
        type=request.type,
        asset=asset,
        amount=request.amount,       # signed as-is (caller controls direction)
        currency=eff_currency,
        price=price,
        venue=venue,
        note=request.note,
    )

    store = LedgerStore(db_path)
    try:
        counts = store.import_rows([row])
    finally:
        store.close()

    return AddTradeResultDTO(success=True, n_rows_added=counts["inserted"])


# ── AppContext ─────────────────────────────────────────────────────────────────

@dataclass
class AppContextDTO:
    """Runtime context created once at startup and passed to facade functions."""

    db_path: str
    fiat: str
    price_provider: Any   # CachedPriceProvider or None


def create_app_context(config_path: Optional[str] = None) -> AppContextDTO:
    """Load config + price provider and return an AppContextDTO.

    This is the only place in the UI boundary that touches core.config and
    core.prices.  The returned DTO is passed around; UI modules never import
    those core internals directly.

    Args:
        config_path: Path to ledger.ini (default: "ledger.ini").

    Returns:
        AppContextDTO with db_path, fiat, and price_provider populated.
    """
    from core.config import load_config
    from core.prices import get_price_provider as _get_pp

    cfg = load_config(config_path) if config_path else load_config()
    ttl = int(cfg.get("prices_ttl_seconds", 60))
    fiat = cfg.get("prices_fiat", "CZK").upper()
    return AppContextDTO(
        db_path=cfg["db_path"],
        fiat=fiat,
        price_provider=_get_pp(ttl_seconds=ttl),
    )


# ── Low-level data access ──────────────────────────────────────────────────────

def get_ledger_rows(db_path: str) -> list:
    """Return all ledger rows ordered by timestamp (RawRow list)."""
    svc = LedgerService(db_path)
    try:
        return svc.timeline()
    finally:
        svc.close()


# ── Health report ──────────────────────────────────────────────────────────────

def get_health_report(db_path: str):
    """Run integrity checks and return a TableReport."""
    from core.services.health_service import health_report as _health_report
    rows = get_ledger_rows(db_path)
    return _health_report(rows)


# ── Positions table report ─────────────────────────────────────────────────────

def get_positions_table_report(db_path: str):
    """Return WAC positions as a TableReport DTO."""
    from core.services.report_service import get_positions_report as _gpr
    rows = get_ledger_rows(db_path)
    return _gpr(rows)


# ── Time-series reports ────────────────────────────────────────────────────────

def get_time_series_report(
    db_path: str,
    kind: str,
    bucket: str = "month",
    fiat: Optional[set] = None,
):
    """Return a time-series report (cashflow or netto_invested).

    Args:
        db_path: Path to the SQLite ledger.
        kind:    Report kind string: "cashflow" or "netto_invested".
        bucket:  Time bucket: "day", "week", or "month".
        fiat:    Fiat currency set (default: {"EUR", "CZK"}).
    """
    from core.services.report_service import ReportKind, get_report as _get_report
    rows = get_ledger_rows(db_path)
    return _get_report(rows, ReportKind(kind), bucket=bucket, fiat=fiat)


# ── Export ─────────────────────────────────────────────────────────────────────

def export_table_report_to_csv(report, out_path: str) -> str:
    """Serialize a TableReport to CSV and return the saved absolute path."""
    from core.services.export_service import export_table_report_csv as _etrc
    return _etrc(report, out_path)


def export_ledger_to_csv(db_path: str, out_path: str) -> str:
    """Export all ledger rows to CSV and return the saved path."""
    from core.services.export_service import export_ledger_csv as _elc
    return _elc(db_path, out_path)


def export_cashflow_to_csv(
    db_path: str,
    out_path: str,
    bucket: str = "month",
    fiat: Optional[set] = None,
) -> str:
    """Export cashflow report to CSV and return the saved path."""
    from core.services.export_service import export_cashflow_csv as _ecc
    return _ecc(db_path, out_path, bucket=bucket, fiat=fiat)


def export_netto_invested_to_csv(
    db_path: str,
    out_path: str,
    bucket: str = "month",
    fiat: Optional[set] = None,
) -> str:
    """Export netto-invested report to CSV and return the saved path."""
    from core.services.export_service import export_netto_invested_csv as _enic
    return _enic(db_path, out_path, bucket=bucket, fiat=fiat)


def export_positions_to_csv(db_path: str, out_path: str) -> str:
    """Export WAC positions to CSV and return the saved path."""
    from core.services.export_service import export_positions_csv as _epc
    return _epc(db_path, out_path)


# ── Import ─────────────────────────────────────────────────────────────────────

def import_file(
    db_path: str,
    file_path: str,
    sheet_name: Optional[str] = None,
) -> list:
    """Import a unified_format_raw file and return inserted RawRow list."""
    from core.services.unified_import_service import import_unified_file as _iuf
    return _iuf(db_path, file_path, sheet_name=sheet_name)


# ── Reversal ───────────────────────────────────────────────────────────────────

def reverse_trade(db_path: str, trade_id: str) -> list:
    """Append REVERSAL rows for a trade group. Raises ValueError if not found."""
    from core.services.reversal_service import reverse_trade as _rt
    return _rt(db_path, trade_id)
