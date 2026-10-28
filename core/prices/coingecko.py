"""CoinGecko simple/price provider.

Uses the free public endpoint — no API key required for live prices.
Results are TTL-cached to avoid hammering the rate limit.

Asset mapping:
    Maps uppercase portfolio symbols → CoinGecko coin IDs.
    Unknown symbols are silently skipped (no crash, no price returned).

Optional Demo API key:
    An API key is NOT required — if COINGECKO_API_KEY is unset (the
    default), behaviour is byte-for-byte identical to before this was
    added: same URL, same (lack of) headers. Setting the COINGECKO_API_KEY
    environment variable (or passing api_key= explicitly) adds the
    `x-cg-demo-api-key` header CoinGecko's free Demo plan expects, raising
    the effective rate limit — see core/prices/coingecko.py's module for
    get_historical_prices(), which benefits from this most (backfill).

get_historical_prices() / get_historical_prices_range() (new):
    Backfill-only capability, deliberately NOT part of the shared
    PriceProvider interface (core/prices/interface.py) — CoinbasePriceProvider
    and CompositePriceProvider have no equivalent, and adding an abstract
    method there would force an unrelated change on both. Callers that need
    historical backfill must hold a CoinGeckoPriceProvider instance
    directly (see core/services/historical_price_service.py), not the
    generic live price_provider used by the Dashboard.

get_prices_with_24h_change() (new):
    Same deliberately-not-on-the-interface pattern as
    get_historical_prices() above — Coinbase has no equivalent live
    24h-change figure wired up, and CompositePriceProvider has no way to
    merge a "24h change" from two different sources meaningfully, so this
    stays CoinGecko-only (see core/services/relative_strength_service.py,
    which holds a CoinGeckoPriceProvider instance directly for this one
    field, exactly like the backfill service does for history). Verified
    empirically (2026-10-28) against the real /simple/price endpoint with
    include_24hr_change=true: the field is named "{fiat}_24h_change" and
    is already a percentage NUMBER (e.g. 0.39 means +0.39%, not a
    fraction) — not a guess from memory.

Mapping verification (2026-10-28, per-ticker CoinGecko /search lookup —
see the Etapa 1 validation follow-up conversation for full reasoning):
    ENA, HYPE, INJ, MORPHO, NEAR, RENDER, XLM were each the sole coin on
    CoinGecko with that exact ticker symbol — unambiguous. MON resolved to
    "monad" (id=monad) — also the sole exact-symbol match, despite fuzzy
    text-search noise from "Monero".

    PUMP -> pump-fun (confirmed 2026-10-28, human-verified against a real
    transaction, not a ticker-symbol guess): two real coins share the PUMP
    ticker (pump-fun rank 50 vs. big-pump rank 2802), so the exact-symbol
    search alone was ambiguous. Resolved by cross-checking a real Revolut
    trade (2026-10-18 18:14, 26750.72651741 PUMP for 2500 CZK, i.e.
    0.091249 CZK/PUMP) against pump-fun's CoinGecko historical price for
    that same date, which matches to within normal daily-close precision;
    the other PUMP candidate is several orders of magnitude off.

    TON -> the-open-network (confirmed 2026-10-28, human-verified, not a
    ticker-symbol guess): this ledger's "TON" is the original Toncoin,
    which CoinGecko now lists as "Gram (prev. Toncoin)" after a rebrand —
    its id (the-open-network) is stable across the rename, only its
    *symbol* changed (now GRAM), which is why a naive current-symbol
    search for "TON" would have wrongly matched the unrelated Tokamak
    Network instead (id=tokamak-network, a different project). The ledger
    asset symbol "TON" itself is untouched — this mapping only affects
    which CoinGecko id its price lookups resolve to.

Anonymous-tier history limit (found during validation, 2026-10):
    CoinGecko's keyless public tier hard-caps historical lookback at 365
    days before "today" — confirmed via /market_chart/range returning
    HTTP 401 "exceeds the allowed time range" for any window reaching
    further back, regardless of chunk size. get_historical_prices_range()
    does not special-case this: a chunk older than the tier allows simply
    fails like any other chunk (see backfill_historical_prices()'s
    per-chunk failure tolerance) — a future paid/Demo key with a longer
    window would transparently reach further back with the same code.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from core.prices.cache import TtlCache
from core.prices.interface import PriceProvider

# ── Symbol → CoinGecko coin-id mapping ────────────────────────────────────────
# Extend this dict as new assets are added to the portfolio. Each entry must
# be verified against CoinGecko's own /search results (exact symbol match,
# not a guess) — see this module's docstring for the 2026-10-28 verification
# pass and the two tickers (PUMP, TON) deliberately left unmapped.
COINGECKO_IDS: Dict[str, str] = {
    "AAVE": "aave",
    "ARB":  "arbitrum",
    "ATOM": "cosmos",
    "AVAX": "avalanche-2",
    "B3":   "b3",
    "BTC":  "bitcoin",
    "DOGE": "dogecoin",
    "DOT":  "polkadot",
    "DYDX": "dydx",
    "ENA":  "ethena",
    "ETH":  "ethereum",
    "FET":  "fetch-ai",
    "GRT":  "the-graph",
    "HYPE": "hyperliquid",
    "INJ":  "injective-protocol",
    "LINK": "chainlink",
    "MON":  "monad",
    "MORPHO": "morpho",
    "NEAR": "near",
    "ONDO": "ondo-finance",
    "PENGU":"pudgy-penguins",
    "PUMP": "pump-fun",
    "RENDER": "render-token",
    "RUNE": "thorchain",
    "SOL":  "solana",
    "SOLX": "solaxy",
    "TAO":  "bittensor",
    "TICS": "qubetics",
    "TON":  "the-open-network",
    "UNI":  "uniswap",
    "USDC": "usd-coin",
    "USDT": "tether",
    "WOO":  "woo-network",
    "XLM":  "stellar",
    "XRP":  "ripple",
}

_BASE_URL = "https://api.coingecko.com/api/v3/simple/price"
_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart"
_MARKET_CHART_RANGE_URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart/range"
_HEADERS = {"User-Agent": "LedgerApp/1.0", "Accept": "application/json"}

_MAX_RETRIES = 3            # bounded — never an infinite loop
_RETRY_BACKOFF_SECONDS = 2.0  # multiplied by attempt number (2s, 4s, 6s)


def _fetch_json_with_retry(url: str, headers: Dict[str, str], timeout: int) -> Optional[dict]:
    """GET *url*, retrying only on HTTP 429 (rate limit) up to _MAX_RETRIES
    times with a short linear backoff. Any other failure (network error,
    timeout, 401/404/etc., malformed JSON) returns None immediately — no
    point retrying a request that can never succeed. Never raises.
    """
    for attempt in range(_MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            return None
        except Exception:
            return None
    return None


@dataclass
class PriceWithChange:
    """One asset's live spot price plus CoinGecko's own 24h % change.

    change_24h_pct is already a percentage NUMBER (CoinGecko convention:
    0.39 means +0.39%, not a fraction) — see this module's docstring for
    the empirical verification. None when CoinGecko omitted the field
    for this coin (never fabricated as 0)."""

    price: Decimal
    change_24h_pct: Optional[Decimal]


class CoinGeckoPriceProvider(PriceProvider):
    """Fetch prices via CoinGecko /simple/price endpoint.

    A single HTTP call fetches all requested assets at once.
    Results are cached by (sorted-assets-tuple, fiat) with TTL.
    """

    def __init__(self, ttl_seconds: int = 60, api_key: Optional[str] = None) -> None:
        self._cache = TtlCache(ttl_seconds)
        self._api_key = api_key if api_key is not None else os.environ.get("COINGECKO_API_KEY")

    def _headers(self) -> Dict[str, str]:
        if not self._api_key:
            return _HEADERS
        return {**_HEADERS, "x-cg-demo-api-key": self._api_key}

    def get_prices(self, assets: List[str], fiat: str) -> Dict[str, Decimal]:
        """Return prices in *fiat* for *assets* whose CoinGecko ID is known."""
        fiat_lc = fiat.lower()
        cache_key = (tuple(sorted(assets)), fiat_lc)

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Filter to assets we have a mapping for
        id_map = {a: COINGECKO_IDS[a] for a in assets if a in COINGECKO_IDS}
        if not id_map:
            return {}

        ids_param = ",".join(id_map.values())
        url = f"{_BASE_URL}?ids={ids_param}&vs_currencies={fiat_lc}"

        try:
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=5) as resp:
                data: dict = json.loads(resp.read())
        except Exception:
            return {}

        result: Dict[str, Decimal] = {}
        for asset, coin_id in id_map.items():
            if coin_id in data and fiat_lc in data[coin_id]:
                result[asset] = Decimal(str(data[coin_id][fiat_lc]))

        self._cache.set(cache_key, result)
        return result

    def get_prices_with_24h_change(self, assets: List[str], fiat: str) -> Dict[str, PriceWithChange]:
        """Return {asset: PriceWithChange} via ONE /simple/price call with
        include_24hr_change=true — CoinGecko's own 24h window, never
        reconstructed from historical_prices' daily buckets (see this
        module's docstring: a daily UTC-midnight anchor cannot give a
        genuine 24h figure relative to "now").

        Same never-raises, same cache-by-(assets,fiat) pattern as
        get_prices(). A coin missing from the response, or missing just
        the change field, never crashes the rest: absent key means
        unknown price (asset dropped from the result dict, exactly like
        get_prices()); a present price with no change field yields
        change_24h_pct=None for that one asset, every other asset in the
        same call is unaffected.
        """
        fiat_lc = fiat.lower()
        cache_key = (tuple(sorted(assets)), fiat_lc, "24h_change")

        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        id_map = {a: COINGECKO_IDS[a] for a in assets if a in COINGECKO_IDS}
        if not id_map:
            return {}

        ids_param = ",".join(id_map.values())
        url = (
            f"{_BASE_URL}?ids={ids_param}&vs_currencies={fiat_lc}"
            f"&include_24hr_change=true"
        )

        data = _fetch_json_with_retry(url, self._headers(), timeout=5)
        if data is None:
            return {}

        change_field = f"{fiat_lc}_24h_change"
        result: Dict[str, PriceWithChange] = {}
        for asset, coin_id in id_map.items():
            coin_data = data.get(coin_id)
            if not isinstance(coin_data, dict) or fiat_lc not in coin_data:
                continue
            try:
                price = Decimal(str(coin_data[fiat_lc]))
            except (InvalidOperation, TypeError, ValueError):
                continue
            change_raw = coin_data.get(change_field)
            change: Optional[Decimal] = None
            if change_raw is not None:
                try:
                    change = Decimal(str(change_raw))
                except (InvalidOperation, TypeError, ValueError):
                    change = None
            result[asset] = PriceWithChange(price=price, change_24h_pct=change)

        self._cache.set(cache_key, result)
        return result

    def get_historical_prices(self, asset: str, fiat: str, days: int) -> Dict[date, Decimal]:
        """Return {calendar_date: price} for *asset* over the trailing *days*
        days, via ONE /coins/{id}/market_chart call — the whole requested
        range in a single request (not one call per day).

        Never raises. Returns {} when: asset has no CoinGecko ID mapping,
        the request fails (network/HTTP/timeout), or the response can't be
        parsed. Missing data is always an absent key, never a 0 price (a
        caller must treat absence as "price unknown for that day", exactly
        like get_prices() already does for live prices).

        The returned price for a given date is CoinGecko's last data point
        that falls on that UTC calendar date (see this module's docstring
        and the project's backfill spike analysis for the precision
        caveat this implies — this is an analytical approximation, not an
        exact accounting timestamp).
        """
        asset_uc = asset.upper()
        coin_id = COINGECKO_IDS.get(asset_uc)
        if coin_id is None:
            return {}
        if days < 1:
            return {}

        fiat_lc = fiat.lower()
        url = (
            f"{_MARKET_CHART_URL.format(id=coin_id)}"
            f"?vs_currency={fiat_lc}&days={days}&interval=daily"
        )

        data = _fetch_json_with_retry(url, self._headers(), timeout=10)
        if data is None:
            return {}
        try:
            points = data["prices"]  # list of [timestamp_ms, price]
        except (KeyError, TypeError):
            return {}

        return _parse_market_chart_points(points)

    def get_historical_prices_range(
        self, asset: str, fiat: str, date_from: date, date_to: date,
    ) -> Dict[date, Decimal]:
        """Return {calendar_date: price} for *asset* over the explicit
        [date_from, date_to] window, via ONE /coins/{id}/market_chart/range
        call. Unlike get_historical_prices() (always trailing N days from
        today), this can target an arbitrary past window — the building
        block backfill_historical_prices() uses to chunk a long history
        into several bounded-size requests (see this module's docstring
        for the anonymous-tier 365-day lookback wall this still runs
        into for old-enough windows — that failure surfaces the same way
        as any other: an empty dict, never a raise).

        Same never-raises contract, same "absent key = unknown price, not
        a 0" contract, and the same retry-on-429 behaviour as
        get_historical_prices().
        """
        asset_uc = asset.upper()
        coin_id = COINGECKO_IDS.get(asset_uc)
        if coin_id is None:
            return {}
        if date_from > date_to:
            return {}

        fiat_lc = fiat.lower()
        from_ts = int(datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        to_ts = int(datetime.combine(date_to, datetime.min.time(), tzinfo=timezone.utc).timestamp()) + 86400
        url = (
            f"{_MARKET_CHART_RANGE_URL.format(id=coin_id)}"
            f"?vs_currency={fiat_lc}&from={from_ts}&to={to_ts}"
        )

        data = _fetch_json_with_retry(url, self._headers(), timeout=15)
        if data is None:
            return {}
        try:
            points = data["prices"]
        except (KeyError, TypeError):
            return {}

        return _parse_market_chart_points(points)


def _parse_market_chart_points(points) -> Dict[date, Decimal]:
    """Shared [timestamp_ms, price] list -> {date: Decimal} parser for
    market_chart and market_chart/range responses. One malformed point
    never voids the rest of the response."""
    result: Dict[date, Decimal] = {}
    for point in points:
        try:
            ts_ms, price = point[0], point[1]
            day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
            result[day] = Decimal(str(price))
        except (IndexError, TypeError, ValueError, InvalidOperation):
            continue
    return result
