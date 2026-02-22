"""Abstract price provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, List


class PriceProvider(ABC):
    """Fetch current spot prices for a list of asset symbols."""

    @abstractmethod
    def get_prices(self, assets: List[str], fiat: str) -> Dict[str, Decimal]:
        """Return {asset: price_in_fiat} for known/available assets.

        Args:
            assets: List of uppercase asset symbols, e.g. ["BTC", "ETH"].
            fiat:   Target fiat currency, e.g. "CZK".

        Returns:
            Dict with a Decimal price for each asset whose price was
            successfully fetched.  Missing assets are simply absent from
            the dict — callers must treat absence as "price unknown".
            Never raises; on any network or parse error return {}.
        """
