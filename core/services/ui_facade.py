"""UI Facade — single contract between UI and Core.

UI modules import *only* from this module (and core.constants).
No UI module should import core internals directly.

Public API:
    create_app_context(config_path) -> AppContextDTO
    create_db(db_path) -> SimpleResultDTO
    get_dashboard_snapshot(db_path, price_provider, fiat) -> DashboardSnapshotDTO
    add_trade(request, db_path) -> AddTradeResultDTO
    get_ledger_rows(db_path) -> list
    get_asset_detail(db_path, asset, price_provider, fiat) -> AssetDetailDTO
    get_health_report(db_path) -> TableReport
    get_positions_table_report(db_path) -> TableReport
    get_time_series_report(db_path, kind, bucket, fiat) -> TimeSeriesReport
    export_table_report_to_csv(report, out_path) -> str
    export_ledger_to_csv(db_path, out_path) -> str
    export_cashflow_to_csv(db_path, out_path, bucket, fiat) -> str
    export_netto_invested_to_csv(db_path, out_path, bucket, fiat) -> str
    export_positions_to_csv(db_path, out_path) -> str
    import_file(db_path, file_path, sheet_name) -> ImportResultDTO
    reverse_trade(db_path, trade_id) -> list
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.constants import TRADE_TYPES
from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.positions import compute_positions
from core.service import LedgerService
from core.services.portfolio_snapshot_service import get_portfolio_snapshot
from core.services.trade_service import AddTradeInput
from core.services.trade_service import add_trade as _core_add_trade
from core.services.trade_service import generate_canonical_id

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
class VenuePositionDTO:
    """WAC position for a single asset restricted to one venue."""

    venue: str
    quantity: Decimal
    wac: Decimal
    cost_basis: Decimal
    realized_pnl: Decimal
    roi_realized: Optional[Decimal] = None  # realized_pnl / cost_basis * 100
    # Populated by price enrichment when price_provider is supplied:
    spot_price: Optional[Decimal] = None
    value: Optional[Decimal] = None        # quantity * spot_price
    unrealized_pnl: Optional[Decimal] = None
    roi_total: Optional[Decimal] = None    # unrealized_pnl / cost_basis (fraction)


@dataclass
class AssetDetailDTO:
    """Full detail bundle for one asset, returned by get_asset_detail()."""

    asset: str
    position: PositionDTO              # aggregated position across all venues
    venue_positions: List[VenuePositionDTO]  # per-venue breakdown, sorted by venue name
    rows: List[RawRow]                 # raw ledger rows where row.asset == asset


@dataclass
class VenueDashboardDTO:
    """Per-venue portfolio summary for the dashboard venue breakdown."""

    venue: str
    positions: List[PositionDTO]          # WAC-based open positions (BUY/SELL only)
    holdings: Dict[str, Decimal]          # physical quantity per asset (incl. TRANSFER)
    cost_basis_total: Decimal             # sum of cost_basis across WAC positions
    assets_held: int                      # count of assets with physical quantity > 0
    invested: Dict[str, Decimal]          # gross fiat outflows for this venue
    net_flow: Dict[str, Decimal]          # net fiat flow for this venue
    # Populated when price_provider is supplied:
    value_total: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None


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
    by_venue: Dict[str, VenueDashboardDTO] = field(default_factory=dict)


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


@dataclass
class SimpleResultDTO:
    """Generic success/failure result for one-shot operations (e.g. create_db)."""

    success: bool
    error_message: Optional[str] = None


@dataclass
class ImportResultDTO:
    """Result of import_file().  Never raises — errors go into error_message."""

    success: bool
    rows_added: int
    rows_skipped: int
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
    import time as _time
    _t_snap = _time.perf_counter()

    def _snap_tick(label: str) -> None:
        ms = (_time.perf_counter() - _t_snap) * 1000
        logger.info("SNAP_TIMING +%7.1f ms  %s", ms, label)

    fiat_uc = fiat.upper()

    svc = LedgerService(db_path)
    try:
        rows = svc.timeline()
    finally:
        svc.close()
    _snap_tick(f"timeline() loaded {len(rows)} rows")

    # ── WAC positions ─────────────────────────────────────────────────────────
    raw_positions = compute_positions(rows, _FIAT_DEFAULT)
    _snap_tick(f"compute_positions() done  {len(raw_positions)} assets")

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
    prices_map: Dict[str, Optional[Decimal]] = {}
    if price_provider is not None and positions:
        assets = [p.asset for p in positions]
        _snap_tick(f"price_provider.get_prices() START  assets={assets}")
        prices_map = price_provider.get_prices(assets, fiat_uc)
        _snap_tick(f"price_provider.get_prices() DONE  got {len(prices_map)}/{len(assets)} prices")
        for p in positions:
            spot = prices_map.get(p.asset)
            p.spot_price = spot
            if spot is not None:
                p.value = p.quantity * spot
                if p.cost_basis > _ZERO:
                    p.unrealized_pnl = p.value - p.cost_basis
                    p.roi_total = p.unrealized_pnl / p.cost_basis

    # ── Physical venue holdings (TRANSFER-aware) ─────────────────────────────
    from core.reports.holdings import compute_venue_holdings
    all_holdings = compute_venue_holdings(rows, _FIAT_DEFAULT)

    # ── Per-venue breakdown ───────────────────────────────────────────────────
    venues = sorted({r.venue for r in rows} | set(all_holdings.keys()))
    by_venue: Dict[str, VenueDashboardDTO] = {}
    for venue in venues:
        venue_rows = [r for r in rows if r.venue == venue]
        venue_raw = compute_positions(venue_rows, _FIAT_DEFAULT)
        venue_snap = get_portfolio_snapshot(venue_rows, precomputed_positions=venue_raw)

        venue_positions: List[PositionDTO] = []
        for pos in venue_raw:
            if pos.quantity == _ZERO:
                continue
            roi_real = (
                (pos.realized_pnl / pos.cost_basis * Decimal("100")).quantize(_ROI_PLACES)
                if pos.cost_basis != _ZERO else None
            )
            vp = PositionDTO(
                asset=pos.asset,
                quantity=pos.quantity,
                wac=pos.wac,
                cost_basis=pos.cost_basis,
                realized_pnl=pos.realized_pnl,
                roi_realized=roi_real,
            )
            if prices_map:
                spot = prices_map.get(pos.asset)
                vp.spot_price = spot
                if spot is not None:
                    vp.value = vp.quantity * spot
                    if vp.cost_basis > _ZERO:
                        vp.unrealized_pnl = vp.value - vp.cost_basis
                        vp.roi_total = vp.unrealized_pnl / vp.cost_basis
            venue_positions.append(vp)

        cost_basis_total = sum((p.cost_basis for p in venue_positions), _ZERO)
        venue_vals = [p.value for p in venue_positions if p.value is not None]
        venue_value = sum(venue_vals, _ZERO) if venue_vals else None

        venue_holdings = all_holdings.get(venue, {})

        # Wallet-only venue (TRANSFER-in only): WAC positions are empty but holdings exist.
        # Compute value directly from physical holdings × spot price.
        if venue_value is None and venue_holdings and prices_map:
            holding_vals = [
                qty * prices_map[asset]
                for asset, qty in venue_holdings.items()
                if qty > _ZERO and prices_map.get(asset) is not None
            ]
            if holding_vals:
                venue_value = sum(holding_vals, _ZERO)

        # Only show unrealized PnL when there is a real cost basis (not wallet-only).
        venue_unr = (
            (venue_value - cost_basis_total)
            if venue_value is not None and cost_basis_total > _ZERO
            else None
        )
        physical_assets_held = sum(1 for q in venue_holdings.values() if q > _ZERO)

        by_venue[venue] = VenueDashboardDTO(
            venue=venue,
            positions=venue_positions,
            holdings=venue_holdings,
            cost_basis_total=cost_basis_total,
            assets_held=physical_assets_held if venue_holdings else len(venue_positions),
            invested=venue_snap.invested,
            net_flow=venue_snap.net_flow,
            value_total=venue_value,
            unrealized_pnl=venue_unr,
        )

    _snap_tick(f"per-venue loop done  venues={len(by_venue)}")

    # ── Portfolio-level aggregates ────────────────────────────────────────────
    snap = get_portfolio_snapshot(rows, precomputed_positions=raw_positions)
    _snap_tick("get_portfolio_snapshot() done")

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
        by_venue=by_venue,
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

    store = LedgerStore(db_path)
    try:
        canonical_id = generate_canonical_id(request.timestamp, venue, request.type, store.conn)
        row = RawRow(
            id=canonical_id,
            timestamp=request.timestamp,
            type=request.type,
            asset=asset,
            amount=request.amount,       # signed as-is (caller controls direction)
            currency=eff_currency,
            price=price,
            venue=venue,
            note=request.note,
        )
        rows_to_insert = [row]

        # TRANSFER with fee → second FEE row sharing the same trade_id.
        # FEE type itself never gets a bundled fee row (it IS the fee).
        if request.type == "TRANSFER" and request.fee_amount is not None:
            fee_asset = (
                request.fee_currency.upper().strip()
                if request.fee_currency
                else asset          # fallback: same asset as the transfer
            )
            rows_to_insert.append(RawRow(
                id=canonical_id,
                timestamp=request.timestamp,
                type="FEE",
                asset=fee_asset,
                amount=-abs(request.fee_amount),   # always outflow
                currency=fee_asset,
                price=Decimal("1"),
                venue=venue,
                note=request.note,
            ))

        counts = store.import_rows(rows_to_insert)
    finally:
        store.close()

    return AddTradeResultDTO(success=True, n_rows_added=counts["inserted"])


# ── AppContext ─────────────────────────────────────────────────────────────────

@dataclass
class AppContextDTO:
    """Runtime context created once at startup and passed to facade functions."""

    db_path: str
    fiat: str
    price_provider: Any            # CachedPriceProvider or None
    version: str = "1.0.0"
    db_state: str = "OK"           # "OK" | "DB_MISSING" | "DB_ERROR"
    error: Optional[str] = None    # non-None for fatal errors (DB_ERROR / config error)


def create_app_context(config_path: Optional[str] = None) -> AppContextDTO:
    """Load config + price provider and return an AppContextDTO.

    This is the only place in the UI boundary that touches core.config and
    core.prices.  The returned DTO is passed around; UI modules never import
    those core internals directly.

    Validation:
        - db_path must be non-empty (config error → AppContextDTO.error set)
        - DB file opened/verified at startup; OperationalError logged + surfaced
        - Price provider failure is non-fatal: continues with price_provider=None

    Args:
        config_path: Explicit path to ledger.ini.  When None, uses
                     ~/.ledger_app/ledger.ini (with project-root fallback).

    Returns:
        AppContextDTO (check .error before use).
    """
    from core.config import get_resolved_config_path, load_config

    try:
        from ledger_app import __version__ as _version
    except Exception:
        _version = "1.0.0"

    resolved_cfg = get_resolved_config_path(config_path)
    logger.info("Using config: %s", resolved_cfg)
    cfg = load_config(resolved_cfg)
    db_path = cfg.get("db_path", "").strip()
    fiat = cfg.get("prices_fiat", "CZK").upper()

    # ── Validate db_path ──────────────────────────────────────────────────────
    if not db_path:
        msg = (
            "db_path is not configured. "
            "Set [ledger] db_path in ledger.ini."
        )
        logger.error(msg)
        return AppContextDTO(db_path="", fiat=fiat, price_provider=None,
                             version=_version, error=msg)

    # ── Probe DB ──────────────────────────────────────────────────────────────
    # Non-fatal: file simply doesn't exist yet → DB_MISSING (onboarding flow).
    # Fatal: file exists but can't be opened (locked, corrupted) → DB_ERROR.
    if not os.path.exists(db_path):
        logger.info("Database not found (onboarding): %s", db_path)
        return AppContextDTO(db_path=db_path, fiat=fiat, price_provider=None,
                             version=_version, db_state="DB_MISSING")

    try:
        store = LedgerStore(db_path)
        store.close()
        logger.info("Database OK: %s", db_path)
    except Exception as exc:
        msg = f"Cannot open database '{db_path}': {exc}"
        logger.error(msg, exc_info=True)
        return AppContextDTO(db_path=db_path, fiat=fiat, price_provider=None,
                             version=_version, db_state="DB_ERROR", error=msg)

    # ── Price provider (failure is non-fatal) ─────────────────────────────────
    price_provider = None
    try:
        from core.prices import get_price_provider as _get_pp
        ttl = int(cfg.get("prices_ttl_seconds", 60))
        price_provider = _get_pp(ttl_seconds=ttl)
    except Exception as exc:
        logger.warning("Price provider unavailable (offline?): %s", exc)

    return AppContextDTO(
        db_path=db_path,
        fiat=fiat,
        price_provider=price_provider,
        version=_version,
    )


def set_db_path(new_db_path: str) -> SimpleResultDTO:
    """Persist *new_db_path* into ledger.ini and return a result.

    Used by the onboarding UI to let the user select an existing DB.
    """
    try:
        from core.config import set_db_path as _sdp
        _sdp(new_db_path)
        logger.info("db_path updated to: %s", new_db_path)
        return SimpleResultDTO(success=True)
    except Exception as exc:
        msg = f"Failed to update db_path: {exc}"
        logger.error(msg, exc_info=True)
        return SimpleResultDTO(success=False, error_message=msg)


def create_db(db_path: str) -> SimpleResultDTO:
    """Create (or open) the SQLite ledger DB at *db_path*.

    Idempotent — safe to call even if the DB already exists.

    Returns:
        SimpleResultDTO(success=True) on success.
        SimpleResultDTO(success=False, error_message=...) on failure.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        store = LedgerStore(db_path)
        store.close()
        logger.info("Database created/verified: %s", db_path)
        return SimpleResultDTO(success=True)
    except Exception as exc:
        msg = f"Failed to create database '{db_path}': {exc}"
        logger.error(msg, exc_info=True)
        return SimpleResultDTO(success=False, error_message=msg)


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


def get_positions_full(
    db_path: str,
    price_provider=None,
    fiat: str = "CZK",
) -> list:
    """Return list[PositionDTO] for ALL assets (including closed quantity=0).

    When price_provider is supplied, enriches each PositionDTO with
    unrealized_pnl, roi_total, value, and spot_price.
    Those fields are None when no price provider is configured.

    Unlike get_dashboard_snapshot(), this includes zero-quantity (closed) positions.
    """
    fiat_uc = fiat.upper()

    svc = LedgerService(db_path)
    try:
        rows = svc.timeline()
    finally:
        svc.close()

    raw_positions = compute_positions(rows, _FIAT_DEFAULT)
    positions: list = []
    for pos in raw_positions:
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

    return positions


def get_asset_detail(
    db_path: str,
    asset: str,
    price_provider=None,
    fiat: str = "CZK",
) -> AssetDetailDTO:
    """Return a full detail bundle for a single asset.

    Includes:
    - Aggregated WAC position (same compute path as get_positions_full).
    - Per-venue position breakdown, computed by reusing compute_positions()
      on venue-filtered subsets of all rows — no WAC logic is duplicated.
    - Raw ledger rows where row.asset == asset (for transaction history).

    WAC computation always uses _FIAT_DEFAULT (EUR, CZK) to stay consistent
    with the rest of the facade.  The fiat parameter controls only the price
    query currency.

    When price_provider is None, spot_price / value / unrealized_pnl /
    roi_total remain None on all DTOs.

    If the asset is not found in the ledger, returns an AssetDetailDTO with a
    zero-filled PositionDTO and empty venue_positions / rows lists.
    """
    asset_uc = asset.upper()
    fiat_uc = fiat.upper()

    store = LedgerStore(db_path)
    try:
        all_rows = store.timeline()
        asset_rows = store.timeline_filtered(asset=asset_uc)
    finally:
        store.close()

    # ── 1) Aggregated position (reuse canonical compute path) ─────────────────
    all_positions = compute_positions(all_rows, _FIAT_DEFAULT)
    agg = next((p for p in all_positions if p.asset == asset_uc), None)

    if agg is None:
        position = PositionDTO(
            asset=asset_uc, quantity=_ZERO, wac=_ZERO,
            cost_basis=_ZERO, realized_pnl=_ZERO,
        )
        return AssetDetailDTO(
            asset=asset_uc, position=position, venue_positions=[], rows=asset_rows,
        )

    roi_real = (
        (agg.realized_pnl / agg.cost_basis * Decimal("100")).quantize(_ROI_PLACES)
        if agg.cost_basis != _ZERO
        else None
    )
    position = PositionDTO(
        asset=agg.asset,
        quantity=agg.quantity,
        wac=agg.wac,
        cost_basis=agg.cost_basis,
        realized_pnl=agg.realized_pnl,
        roi_realized=roi_real,
    )

    # ── 2) Venue breakdown ────────────────────────────────────────────────────
    # Discover venues from asset-only rows (only venues where this asset appears).
    # For each venue, filter ALL rows by venue — this ensures fiat quote legs
    # (same trade_id, same venue) are included so WAC is computed correctly.
    venues = sorted({r.venue for r in asset_rows})
    venue_positions: List[VenuePositionDTO] = []

    for venue in venues:
        venue_rows = [r for r in all_rows if r.venue == venue]
        vp_list = compute_positions(venue_rows, _FIAT_DEFAULT)
        vp = next((p for p in vp_list if p.asset == asset_uc), None)
        if vp is None:
            continue
        vp_roi_real = (
            (vp.realized_pnl / vp.cost_basis * Decimal("100")).quantize(_ROI_PLACES)
            if vp.cost_basis != _ZERO
            else None
        )
        venue_positions.append(VenuePositionDTO(
            venue=venue,
            quantity=vp.quantity,
            wac=vp.wac,
            cost_basis=vp.cost_basis,
            realized_pnl=vp.realized_pnl,
            roi_realized=vp_roi_real,
        ))

    # ── 3) Price enrichment ───────────────────────────────────────────────────
    if price_provider is not None:
        prices = price_provider.get_prices([asset_uc], fiat_uc)
        spot = prices.get(asset_uc)
        position.spot_price = spot
        if spot is not None:
            position.value = position.quantity * spot
            if position.cost_basis > _ZERO:
                position.unrealized_pnl = position.value - position.cost_basis
                position.roi_total = position.unrealized_pnl / position.cost_basis
        for vp in venue_positions:
            vp.spot_price = spot
            if spot is not None:
                vp.value = vp.quantity * spot
                if vp.cost_basis > _ZERO:
                    vp.unrealized_pnl = vp.value - vp.cost_basis
                    vp.roi_total = vp.unrealized_pnl / vp.cost_basis

    return AssetDetailDTO(
        asset=asset_uc,
        position=position,
        venue_positions=venue_positions,
        rows=asset_rows,
    )


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
) -> ImportResultDTO:
    """Import a unified_format_raw file.

    Returns:
        ImportResultDTO with rows_added, rows_skipped, and success flag.
        Never raises — errors go into ImportResultDTO.error_message.
    """
    from core.services.unified_import_service import import_unified_file as _iuf

    # Count rows before import to derive inserted vs skipped.
    try:
        store = LedgerStore(db_path)
        try:
            before = store.count()
        finally:
            store.close()
    except Exception as exc:
        return ImportResultDTO(success=False, rows_added=0, rows_skipped=0,
                               error_message=str(exc))

    try:
        rows = _iuf(db_path, file_path, sheet_name=sheet_name)
    except Exception as exc:
        return ImportResultDTO(success=False, rows_added=0, rows_skipped=0,
                               error_message=str(exc))

    try:
        store = LedgerStore(db_path)
        try:
            after = store.count()
        finally:
            store.close()
    except Exception:
        after = before + len(rows)  # fallback: assume all inserted

    rows_added = after - before
    rows_skipped = max(0, len(rows) - rows_added)
    return ImportResultDTO(success=True, rows_added=rows_added,
                           rows_skipped=rows_skipped)


# ── Reversal ───────────────────────────────────────────────────────────────────

def reverse_trade(db_path: str, trade_id: str) -> list:
    """Append REVERSAL rows for a trade group. Raises ValueError if not found."""
    from core.services.reversal_service import reverse_trade as _rt
    return _rt(db_path, trade_id)


def delete_trade(db_path: str, trade_id: str) -> int:
    """Hard DELETE všech řádků se stejným trade_id. Vrátí počet smazaných řádků."""
    store = LedgerStore(db_path)
    try:
        return store.delete_by_id(trade_id)
    finally:
        store.close()
