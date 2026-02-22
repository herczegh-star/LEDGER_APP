"""CompositeProvider: try primary, fill gaps with fallback."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

from core.prices.interface import PriceProvider


class CompositePriceProvider(PriceProvider):
    """Try *primary* first; use *fallback* for any assets not returned.

    Neither provider is required to return prices for all assets.
    If *primary* raises, *fallback* is tried for all assets.
    If *fallback* also fails, the result is whatever *primary* returned
    (possibly empty).
    """

    def __init__(self, primary: PriceProvider, fallback: PriceProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    def get_prices(self, assets: List[str], fiat: str) -> Dict[str, Decimal]:
        try:
            result: Dict[str, Decimal] = dict(self._primary.get_prices(assets, fiat))
        except Exception:
            result = {}

        missing = [a for a in assets if a not in result]
        if missing:
            try:
                fb = self._fallback.get_prices(missing, fiat)
                result.update(fb)
            except Exception:
                pass

        return result
