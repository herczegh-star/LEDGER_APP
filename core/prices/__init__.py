"""Price provider factory.

Usage:
    from core.prices import get_price_provider
    provider = get_price_provider(ttl_seconds=60)
    prices = provider.get_prices(["BTC", "ETH"], "CZK")
    # {"BTC": Decimal("2 500 000"), "ETH": Decimal("80 000")} or {}
"""
from __future__ import annotations

from core.prices.coinbase import CoinbasePriceProvider
from core.prices.coingecko import CoinGeckoPriceProvider
from core.prices.composite import CompositePriceProvider
from core.prices.interface import PriceProvider

__all__ = [
    "PriceProvider",
    "CoinGeckoPriceProvider",
    "CoinbasePriceProvider",
    "CompositePriceProvider",
    "get_price_provider",
]


def get_price_provider(ttl_seconds: int = 60) -> CompositePriceProvider:
    """Return a CompositePriceProvider (CoinGecko primary, Coinbase fallback)."""
    return CompositePriceProvider(
        primary=CoinGeckoPriceProvider(ttl_seconds=ttl_seconds),
        fallback=CoinbasePriceProvider(ttl_seconds=ttl_seconds),
    )
