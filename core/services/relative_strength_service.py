"""Relative Strength — pure price-performance comparison for currently
held headline assets.

Public API:
    compute_relative_strength(db_path, positions, price_provider, fiat="CZK")
        -> List[RelativeStrengthRowDTO]

NOT a trading indicator (no RSI/MACD), NOT a BUY/SELL signal, and NOT a
personal P&L figure (that's core.services.portfolio_history_service.
get_portfolio_contributors() — capital-flow-aware realized/unrealized
attribution). This module answers a narrower question: "how has this
asset's MARKET PRICE moved, regardless of when I bought it, compared to
itself a week/month ago and to BTC". An asset bought yesterday still
gets a valid 30D return — its price existed for 30 days even if your
position didn't.

24H / 7D / 30D / vs BTC (7D) — see compute_relative_strength()'s own
docstring for the exact formulas and why 24H deliberately does NOT come
from historical_prices (see core/prices/coingecko.py's module docstring
for the daily-bucket-vs-live-now mismatch this avoids).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from core.historical_price_store import HistoricalPriceStore
from core.services.ui_facade import PositionDTO, compute_headline_positions

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BTC = "BTC"
# Mirrors ui/modules/analysis_view.py's own _STABLECOINS — kept as a
# separate constant (not imported) to avoid a UI-module -> service-module
# dependency; both lists are small and change together deliberately.
_STABLECOINS = frozenset({"USDC", "USDT"})


@dataclass
class RelativeStrengthRowDTO:
    """One headline asset's price performance.

    change_24h_pct is CoinGecko's own percentage NUMBER (e.g. 1.23 means
    +1.23%) — see core/prices/coingecko.py's PriceWithChange. return_7d /
    return_30d / vs_btc_7d are fractions (e.g. 0.194 means +19.4%),
    matching this codebase's existing roi_total convention elsewhere.
    Any field is None when the underlying price data was unavailable —
    NEVER substituted with 0 (a real 0% move is a genuine, different,
    fact).
    """

    asset: str
    change_24h_pct: Optional[Decimal]
    return_7d: Optional[Decimal]
    return_30d: Optional[Decimal]
    vs_btc_7d: Optional[Decimal]


def _return_since(spot_now: Optional[Decimal], hist_price: Optional[Decimal]) -> Optional[Decimal]:
    if spot_now is None or hist_price is None or hist_price == _ZERO:
        return None
    return spot_now / hist_price - _ONE


def compute_relative_strength(
    db_path: str,
    positions: List[PositionDTO],
    price_provider,
    fiat: str = "CZK",
) -> List[RelativeStrengthRowDTO]:
    """Price-performance table for currently held headline assets.

    Scope: compute_headline_positions(positions) (excludes PRESALE_ASSETS,
    same as Dashboard/Allocation), then keeps quantity > 0, then drops
    BTC itself and stablecoins (_STABLECOINS) — BTC is the benchmark, not
    a row; stablecoins have no meaningful "outperform the market" signal.
    Closed positions and recently-bought assets are NOT specially
    excluded — recency of purchase is irrelevant to a pure price
    comparison (quantity > 0 already covers "closed").

        return_7d  = spot_now / historical_price(asset, today - 7)  - 1
        return_30d = spot_now / historical_price(asset, today - 30) - 1
        vs_btc_7d  = asset.return_7d - btc.return_7d   (fixed 7D horizon,
                     independent of which column the UI happens to sort by)

    spot_now is each PositionDTO's own .spot_price — already fetched
    independently by get_dashboard_snapshot() before this is called — so
    7D/30D keep working even if the live 24h-change call below fails
    entirely (failure tolerance: one data source going down never takes
    the others with it).

    24H comes ONLY from price_provider.get_prices_with_24h_change()
    (CoinGecko's own live 24h window) — never reconstructed from
    historical_prices' daily buckets, never defaulted to 0 when missing.
    *price_provider* must duck-type that method (hold a
    CoinGeckoPriceProvider instance directly, exactly like the backfill
    service does for history) — a provider without it, or a provider
    that raises, simply yields change_24h_pct=None for every row.

    BTC's own spot price (for the benchmark) is fetched directly via
    price_provider.get_prices(["BTC"], fiat) — independent of whether
    BTC is itself held, so vs_btc_7d still works with zero BTC in the
    portfolio. If that fetch, or BTC's historical price 7 days ago, is
    unavailable, vs_btc_7d is None for every row.

    Never raises. Returns rows sorted by return_7d descending, with
    return_7d=None rows last (the stated MVP default; no interactive
    sort yet).
    """
    headline = compute_headline_positions(positions)
    candidates = [
        p for p in headline
        if p.quantity > _ZERO
        and p.asset.upper() != _BTC
        and p.asset.upper() not in _STABLECOINS
    ]
    if not candidates:
        return []

    price_store = HistoricalPriceStore(db_path, read_only=True)
    try:
        schema_ok = price_store.schema_exists()

        def hist_price(asset: str, d: date) -> Optional[Decimal]:
            if not schema_ok:
                return None
            rec = price_store.get_price(asset, d)
            return rec.price_czk if rec is not None else None

        today = date.today()
        d7 = today - timedelta(days=7)
        d30 = today - timedelta(days=30)

        change_map = {}
        if price_provider is not None:
            try:
                change_map = price_provider.get_prices_with_24h_change(
                    [p.asset for p in candidates], fiat
                )
            except Exception:
                change_map = {}

        btc_prices = {}
        if price_provider is not None:
            try:
                btc_prices = price_provider.get_prices([_BTC], fiat)
            except Exception:
                btc_prices = {}
        btc_spot = btc_prices.get(_BTC)
        btc_return_7d = _return_since(btc_spot, hist_price(_BTC, d7))

        rows: List[RelativeStrengthRowDTO] = []
        for p in candidates:
            change_entry = change_map.get(p.asset)
            change_24h = change_entry.change_24h_pct if change_entry is not None else None

            r7 = _return_since(p.spot_price, hist_price(p.asset, d7))
            r30 = _return_since(p.spot_price, hist_price(p.asset, d30))
            vs_btc = (r7 - btc_return_7d) if (r7 is not None and btc_return_7d is not None) else None

            rows.append(RelativeStrengthRowDTO(
                asset=p.asset, change_24h_pct=change_24h,
                return_7d=r7, return_30d=r30, vs_btc_7d=vs_btc,
            ))

        rows.sort(key=lambda r: (r.return_7d is None, -(r.return_7d if r.return_7d is not None else _ZERO)))
        return rows
    finally:
        price_store.close()
