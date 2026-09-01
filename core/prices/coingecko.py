"""CoinGecko simple/price provider.

Uses the free public endpoint — no API key required.
Results are TTL-cached to avoid hammering the rate limit.

Asset mapping:
    Maps uppercase portfolio symbols → CoinGecko coin IDs.
    Unknown symbols are silently skipped (no crash, no price returned).
"""
from __future__ import annotations

import json
import urllib.request
from decimal import Decimal
from typing import Dict, List

from core.prices.cache import TtlCache
from core.prices.interface import PriceProvider

# ── Symbol → CoinGecko coin-id mapping ────────────────────────────────────────
# Extend this dict as new assets are added to the portfolio.
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
    "ETH":  "ethereum",
    "FET":  "fetch-ai",
    "GRT":  "the-graph",
    "LINK": "chainlink",
    "ONDO": "ondo-finance",
    "PENGU":"pudgy-penguins",
    "RUNE": "thorchain",
    "SOL":  "solana",
    "SOLX": "solaxy",
    "TAO":  "bittensor",
    "TICS": "qubetics",
    "UNI":  "uniswap",
    "WOO":  "woo-network",
    "XRP":  "ripple",
}

_BASE_URL = "https://api.coingecko.com/api/v3/simple/price"
_HEADERS = {"User-Agent": "LedgerApp/1.0", "Accept": "application/json"}


class CoinGeckoPriceProvider(PriceProvider):
    """Fetch prices via CoinGecko /simple/price endpoint.

    A single HTTP call fetches all requested assets at once.
    Results are cached by (sorted-assets-tuple, fiat) with TTL.
    """

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._cache = TtlCache(ttl_seconds)

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
            req = urllib.request.Request(url, headers=_HEADERS)
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
