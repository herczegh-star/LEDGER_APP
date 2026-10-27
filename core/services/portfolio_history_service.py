"""Portfolio history service — the ONLY place that writes or reads
portfolio value/PnL/ROI history. No UI module and no other service
computes historical portfolio value itself (see
ui/app_flet.py's refresh() hook, which only ever calls
maybe_save_portfolio_snapshot() here).

Two responsibilities:

  maybe_save_portfolio_snapshot()  — write: best-effort, rate-limited
                                      persistence of ONE live headline
                                      snapshot (core.portfolio_snapshot_store).

  get_portfolio_value_trend()      — read: one point per calendar day,
                                      combining the hybrid A+B design from
                                      the approved spike:
                                        A) backfill — reconstructed from
                                           the ledger (LedgerStore.
                                           timeline_filtered() +
                                           compute_positions(), NEVER a
                                           new positions engine) +
                                           core.historical_price_store.
                                        B) live_snapshot — an already
                                           -saved portfolio_snapshots row.
                                      Each point is tagged with `source`
                                      so a future UI can visually
                                      distinguish the two (see the spike's
                                      point 10 — not implemented here).

IMPORTANT — intentional duplication, not a new engine:
    The Dashboard's headline Portfolio Value / Unrealized PnL / ROI
    formula (pnl = sum(value) - sum(cost_basis); roi = pnl / cost_basis)
    is inlined directly inside ui/app_flet.py's update_kpis() — it was
    never factored out into a reusable service function. To satisfy
    "same data + same instant = same result on Dashboard and here", this
    module replicates that exact 3-line formula (see
    _headline_value_pnl_roi() below) rather than inventing a different
    one. This is replication of an existing formula for consistency, NOT
    a new parallel positions/PnL engine — compute_positions() and
    compute_headline_positions() (both imported, never reimplemented) do
    all the actual accounting. A future minor refactor extracting that
    formula out of update_kpis() into a shared function would remove this
    duplication, but touches ui/app_flet.py and was out of scope here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from core.historical_price_store import HistoricalPriceSchemaNotInitializedError, HistoricalPriceStore
from core.ledger_store import LedgerStore
from core.portfolio_snapshot_store import (
    PortfolioSnapshotPositionRecord,
    PortfolioSnapshotRecord,
    PortfolioSnapshotSchemaNotInitializedError,
    PortfolioSnapshotStore,
)
from core.reports.positions import compute_positions

logger = logging.getLogger(__name__)

_FIAT_DEFAULT = frozenset({"EUR", "CZK"})
_ZERO = Decimal("0")
_DEFAULT_MIN_SNAPSHOT_INTERVAL = timedelta(hours=6)


# ── Write: live snapshots ────────────────────────────────────────────────────

def maybe_save_portfolio_snapshot(
    db_path: str,
    snap,  # DashboardSnapshotDTO — see core.services.ui_facade
    min_interval: timedelta = _DEFAULT_MIN_SNAPSHOT_INTERVAL,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Best-effort: save one portfolio_snapshots row from an
    already-successful get_dashboard_snapshot() result, UNLESS one was
    already saved within *min_interval* (default 6h — Refresh in this app
    is manual/event-triggered, not a timer, so this is the only guard
    against saving "hundreds of snapshots a day").

    NEVER raises. Any failure — schema not yet initialized, a locked DB,
    any other I/O error — is logged and treated as "skip this time",
    exactly like ui/app_flet.py's existing fire-and-forget
    icon_service.download_missing() call right after get_dashboard_snapshot().
    The ledger remains the sole accounting source of truth; a missed or
    failed snapshot never corrupts it and is never retried mid-call.

    Returns the new snapshot's pk, or None if skipped/failed.
    """
    try:
        now = now or datetime.now()
        store = PortfolioSnapshotStore(db_path)
        try:
            latest = store.get_latest_snapshot()
            if latest is not None and (now - latest.timestamp) < min_interval:
                return None

            from core.services.ui_facade import compute_headline_positions

            headline = compute_headline_positions(snap.positions)
            tv, pnl, roi = _headline_value_pnl_roi(headline)
            total_cost = sum((p.cost_basis for p in headline), _ZERO)

            record = PortfolioSnapshotRecord(
                pk=None,
                timestamp=now,
                portfolio_value=tv,
                cost_basis=total_cost,
                unrealized_pnl=pnl,
                roi_total=roi,
                net_flow_czk=snap.net_flow.get("CZK") if snap.net_flow else None,
                positions=[
                    # ALL positions (including PRESALE_ASSETS) — the header
                    # above is headline-filtered, but per-asset detail stays
                    # complete so future features (Contributors, Allocation)
                    # are not crippled by an early filtering decision.
                    PortfolioSnapshotPositionRecord(
                        asset=p.asset, quantity=p.quantity, spot_price=p.spot_price,
                        value=p.value, cost_basis=p.cost_basis,
                    )
                    for p in snap.positions
                ],
            )
            return store.save_snapshot(record)
        finally:
            store.close()
    except Exception:
        logger.exception("maybe_save_portfolio_snapshot: failed, skipping this snapshot")
        return None


def _headline_value_pnl_roi(headline: list) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
    """Exact replica of ui/app_flet.py's update_kpis() headline formula —
    see this module's docstring for why this is duplicated rather than
    imported. *headline* is a list of PositionDTO-like objects (.value,
    .cost_basis) already filtered by compute_headline_positions().

    Returns (portfolio_value, unrealized_pnl, roi_total) — all three None
    together when no headline position has a known price (never a
    misleading 0), matching update_kpis()'s own "—" behaviour.
    """
    total_cost = sum((p.cost_basis for p in headline), _ZERO)
    vals = [p.value for p in headline if p.value is not None]
    if not vals:
        return None, None, None
    tv = sum(vals, _ZERO)
    pnl = tv - total_cost
    roi = pnl / total_cost if total_cost > _ZERO else None
    return tv, pnl, roi


# ── Read: historical trend ───────────────────────────────────────────────────

@dataclass
class PortfolioValueTrendPointDTO:
    """One day's headline figures, tagged with where they came from.

    cost_basis is always set (always computable from the ledger alone).
    portfolio_value / unrealized_pnl / roi_total are None together when
    no held position had a known price that day — never a misleading 0.
    """

    date: date
    portfolio_value: Optional[Decimal]
    cost_basis: Decimal
    unrealized_pnl: Optional[Decimal]
    roi_total: Optional[Decimal]
    source: str  # "backfill" | "live_snapshot"


def get_portfolio_value_trend(
    db_path: str,
    date_from: date,
    date_to: date,
) -> List[PortfolioValueTrendPointDTO]:
    """One point per calendar day in [date_from, date_to], oldest first.

    A day already covered by a saved portfolio_snapshots row uses that
    row directly (source="live_snapshot"; the LAST snapshot of that
    calendar day wins if more than one was saved that day). Every other
    day is reconstructed from LedgerStore.timeline_filtered(time_to=...)
    + compute_positions() + historical_prices (source="backfill") — see
    this module's docstring for the exact formula reused.

    Never raises: missing historical_prices or portfolio_snapshots schema
    (backfill/live history never run yet) degrades gracefully — every day
    simply falls back to the backfill path with portfolio_value=None
    wherever no price is cached, rather than failing the whole range.
    """
    if date_from > date_to:
        raise ValueError(f"date_from ({date_from}) must be <= date_to ({date_to})")

    from core.services.ui_facade import compute_headline_positions

    # ── 1. Already-saved live snapshots in range, bucketed by calendar day ──
    live_by_date: Dict[date, PortfolioSnapshotRecord] = {}
    try:
        snap_store = PortfolioSnapshotStore(db_path, read_only=True)
        try:
            snapshots = snap_store.get_snapshots(
                time_from=datetime.combine(date_from, time.min),
                time_to=datetime.combine(date_to, time.max),
            )
            for rec in snapshots:  # oldest-first -> last snapshot of a day wins
                live_by_date[rec.timestamp.date()] = rec
        finally:
            snap_store.close()
    except PortfolioSnapshotSchemaNotInitializedError:
        pass  # no live history yet — every day falls back to backfill

    # ── 2. Historical price cache availability (for the backfill path) ──
    price_store = HistoricalPriceStore(db_path, read_only=True)
    price_schema_ok = price_store.schema_exists()

    # ── 3. Ledger rows, loaded once; filtered per day below ──
    ledger_store = LedgerStore(db_path)

    points: List[PortfolioValueTrendPointDTO] = []
    try:
        day_count = (date_to - date_from).days + 1
        for i in range(day_count):
            d = date_from + timedelta(days=i)

            if d in live_by_date:
                rec = live_by_date[d]
                points.append(PortfolioValueTrendPointDTO(
                    date=d,
                    portfolio_value=rec.portfolio_value,
                    cost_basis=rec.cost_basis,
                    unrealized_pnl=rec.unrealized_pnl,
                    roi_total=rec.roi_total,
                    source="live_snapshot",
                ))
                continue

            points.append(_reconstruct_backfill_point(
                ledger_store, price_store if price_schema_ok else None, d, compute_headline_positions,
            ))
    finally:
        ledger_store.close()
        price_store.close()

    return points


def _reconstruct_backfill_point(
    ledger_store: LedgerStore,
    price_store: Optional[HistoricalPriceStore],
    d: date,
    compute_headline_positions,
) -> PortfolioValueTrendPointDTO:
    """Reconstruct ONE day's headline figures purely from the ledger +
    historical_prices. See get_portfolio_value_trend()'s docstring."""
    cutoff = datetime.combine(d, time.max)
    rows = ledger_store.timeline_filtered(time_to=cutoff)
    raw_positions = compute_positions(rows, _FIAT_DEFAULT)

    # Build minimal PositionDTO-like objects, enriched with the historical
    # price for *d* — mirrors get_dashboard_snapshot()'s live-price
    # enrichment loop, just with a historical lookup instead of a live one.
    from core.services.ui_facade import PositionDTO

    enriched = []
    for pos in raw_positions:
        if pos.quantity == _ZERO:
            continue  # closed positions not shown — mirrors get_dashboard_snapshot()
        dto = PositionDTO(
            asset=pos.asset, quantity=pos.quantity, wac=pos.wac,
            cost_basis=pos.cost_basis, realized_pnl=pos.realized_pnl,
        )
        price = price_store.get_price(pos.asset, d) if price_store is not None else None
        if price is not None:
            dto.spot_price = price.price_czk
            dto.value = pos.quantity * price.price_czk
            if pos.cost_basis > _ZERO:
                dto.unrealized_pnl = dto.value - pos.cost_basis
                dto.roi_total = dto.unrealized_pnl / pos.cost_basis
        enriched.append(dto)

    headline = compute_headline_positions(enriched)
    tv, pnl, roi = _headline_value_pnl_roi(headline)
    total_cost = sum((p.cost_basis for p in headline), _ZERO)

    return PortfolioValueTrendPointDTO(
        date=d, portfolio_value=tv, cost_basis=total_cost,
        unrealized_pnl=pnl, roi_total=roi, source="backfill",
    )
