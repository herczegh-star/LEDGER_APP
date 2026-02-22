"""Coinbase public spot-price fallback provider.

Fetches one asset at a time via the Coinbase Data API v2:
    GET https://api.coinbase.com/v2/prices/{BASE}-{QUOTE}/spot

Results are TTL-cached per (asset, fiat) pair.
If a pair is not supported (HTTP error or parse failure) that asset
is simply absent from the returned dict.
"""
from __future__ import annotations

import json
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from core.prices.cache import TtlCache
from core.prices.interface import PriceProvider

_BASE_URL = "https://api.coinbase.com/v2/prices/{pair}/spot"
_HEADERS = {"User-Agent": "LedgerApp/1.0", "Accept": "application/json"}


class CoinbasePriceProvider(PriceProvider):
    """Fetch spot prices from Coinbase (one request per asset).

    CZK is directly supported by Coinbase (e.g. BTC-CZK).
    """

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._cache = TtlCache(ttl_seconds)

    def get_prices(self, assets: List[str], fiat: str) -> Dict[str, Decimal]:
        """Return {asset: spot_price_in_fiat}."""
        fiat_uc = fiat.upper()
        result: Dict[str, Decimal] = {}

        for asset in assets:
            cache_key = (asset.upper(), fiat_uc)
            cached = self._cache.get(cache_key)
            if cached is not None:
                result[asset] = cached
                continue

            price = self._fetch_spot(asset.upper(), fiat_uc)
            if price is not None:
                self._cache.set(cache_key, price)
                result[asset] = price

        return result

    def _fetch_spot(self, asset: str, fiat: str) -> Optional[Decimal]:
        """Fetch a single spot price. Returns None on any failure."""
        pair = f"{asset}-{fiat}"
        url = _BASE_URL.format(pair=pair)
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data: dict = json.loads(resp.read())
            amount_str: str = data["data"]["amount"]
            return Decimal(amount_str)
        except Exception:
            return None
