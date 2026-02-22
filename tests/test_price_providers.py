"""Unit tests for core/prices/ — no real HTTP calls.

All network I/O is mocked with unittest.mock.patch so tests are
fully offline and deterministic.
"""
from __future__ import annotations

import json
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from core.prices.cache import TtlCache
from core.prices.coingecko import CoinGeckoPriceProvider, COINGECKO_IDS
from core.prices.coinbase import CoinbasePriceProvider
from core.prices.composite import CompositePriceProvider


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_urlopen(body: dict):
    """Return a context-manager mock that yields a response with *body* as JSON."""
    encoded = json.dumps(body).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=encoded)))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ── TtlCache ───────────────────────────────────────────────────────────────────

class TestTtlCache:
    def test_miss_returns_none(self):
        cache = TtlCache(ttl_seconds=60)
        assert cache.get("missing") is None

    def test_hit_returns_value(self):
        cache = TtlCache(ttl_seconds=60)
        cache.set("k", {"BTC": Decimal("2500000")})
        assert cache.get("k") == {"BTC": Decimal("2500000")}

    def test_expired_returns_none(self):
        # Inject a clock that starts at 0
        t = [0.0]
        cache = TtlCache(ttl_seconds=60, clock=lambda: t[0])
        cache.set("k", "value")
        assert cache.get("k") == "value"     # still fresh

        t[0] = 61.0                           # advance past TTL
        assert cache.get("k") is None         # expired

    def test_not_expired_before_ttl(self):
        t = [0.0]
        cache = TtlCache(ttl_seconds=60, clock=lambda: t[0])
        cache.set("k", "value")
        t[0] = 59.9
        assert cache.get("k") == "value"

    def test_clear_evicts_all(self):
        cache = TtlCache(ttl_seconds=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_overwrite_resets_ttl(self):
        t = [0.0]
        cache = TtlCache(ttl_seconds=60, clock=lambda: t[0])
        cache.set("k", "old")
        t[0] = 50.0
        cache.set("k", "new")   # resets expiry to t=110
        t[0] = 90.0
        assert cache.get("k") == "new"


# ── CoinGeckoPriceProvider ────────────────────────────────────────────────────

class TestCoinGeckoProvider:
    def test_known_assets_url_contains_coin_ids(self):
        """The HTTP request must contain the CoinGecko coin IDs."""
        response_body = {
            "bitcoin": {"czk": 2500000},
            "ethereum": {"czk": 80000},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            provider.get_prices(["BTC", "ETH"], "CZK")
            url_called = m.call_args[0][0].full_url
        assert "bitcoin" in url_called
        assert "ethereum" in url_called
        assert "czk" in url_called.lower()

    def test_returns_decimal_prices(self):
        response_body = {"bitcoin": {"czk": 2500000}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_prices(["BTC"], "CZK")
        assert "BTC" in result
        assert isinstance(result["BTC"], Decimal)
        assert result["BTC"] == Decimal("2500000")

    def test_unknown_asset_skipped(self):
        """Assets without a CoinGecko mapping must not crash and must be absent."""
        response_body = {}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_prices(["UNKNOWNCOIN"], "CZK")
        assert result == {}

    def test_all_unknown_skips_http(self):
        """If no assets are mappable, no HTTP call should be made."""
        with patch("urllib.request.urlopen") as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_prices(["FAKETOKEN"], "CZK")
        m.assert_not_called()
        assert result == {}

    def test_network_error_returns_empty(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_prices(["BTC"], "CZK")
        assert result == {}

    def test_result_is_cached(self):
        """Second call with same args must NOT issue a second HTTP request."""
        response_body = {"bitcoin": {"czk": 2500000}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            provider.get_prices(["BTC"], "CZK")
            provider.get_prices(["BTC"], "CZK")   # second call → should hit cache
        assert m.call_count == 1

    def test_cache_expires(self):
        """After TTL, a fresh HTTP request must be issued."""
        response_body = {"bitcoin": {"czk": 2500000}}
        t = [0.0]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            provider._cache._clock = lambda: t[0]   # inject clock
            provider.get_prices(["BTC"], "CZK")
            t[0] = 61.0                              # expire cache
            provider.get_prices(["BTC"], "CZK")
        assert m.call_count == 2

    def test_multiple_assets_single_request(self):
        """Multiple known assets → single HTTP call."""
        response_body = {
            "bitcoin":  {"czk": 2500000},
            "ethereum": {"czk": 80000},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_prices(["BTC", "ETH"], "CZK")
        assert m.call_count == 1
        assert "BTC" in result and "ETH" in result


# ── CoinbasePriceProvider ─────────────────────────────────────────────────────

class TestCoinbaseProvider:
    def test_returns_decimal_price(self):
        response_body = {"data": {"amount": "2500000.00", "currency": "CZK"}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinbasePriceProvider(ttl_seconds=60)
            result = provider.get_prices(["BTC"], "CZK")
        assert "BTC" in result
        assert isinstance(result["BTC"], Decimal)
        assert result["BTC"] == Decimal("2500000.00")

    def test_network_error_returns_empty(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            provider = CoinbasePriceProvider(ttl_seconds=60)
            result = provider.get_prices(["BTC"], "CZK")
        assert result == {}

    def test_result_is_cached(self):
        response_body = {"data": {"amount": "2500000.00", "currency": "CZK"}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinbasePriceProvider(ttl_seconds=60)
            provider.get_prices(["BTC"], "CZK")
            provider.get_prices(["BTC"], "CZK")
        assert m.call_count == 1

    def test_multiple_assets_multiple_requests(self):
        """Coinbase fetches one asset at a time → N requests for N assets."""
        response_body = {"data": {"amount": "1000.00", "currency": "CZK"}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinbasePriceProvider(ttl_seconds=60)
            provider.get_prices(["BTC", "ETH"], "CZK")
        assert m.call_count == 2

    def test_missing_asset_absent_from_result(self):
        """If Coinbase returns a malformed response, asset is absent (no crash)."""
        with patch("urllib.request.urlopen", side_effect=Exception("404")):
            provider = CoinbasePriceProvider(ttl_seconds=60)
            result = provider.get_prices(["UNKNOWN"], "CZK")
        assert "UNKNOWN" not in result


# ── CompositePriceProvider ────────────────────────────────────────────────────

class TestCompositeProvider:
    def _make_stub(self, prices: dict) -> MagicMock:
        stub = MagicMock()
        stub.get_prices = MagicMock(return_value={k: Decimal(str(v)) for k, v in prices.items()})
        return stub

    def test_primary_used_when_successful(self):
        primary  = self._make_stub({"BTC": 2500000, "ETH": 80000})
        fallback = self._make_stub({})
        composite = CompositePriceProvider(primary, fallback)
        result = composite.get_prices(["BTC", "ETH"], "CZK")
        primary.get_prices.assert_called_once()
        fallback.get_prices.assert_not_called()
        assert "BTC" in result and "ETH" in result

    def test_fallback_used_for_missing_assets(self):
        """Primary returns BTC but not ETH; fallback provides ETH."""
        primary  = self._make_stub({"BTC": 2500000})
        fallback = self._make_stub({"ETH": 80000})
        composite = CompositePriceProvider(primary, fallback)
        result = composite.get_prices(["BTC", "ETH"], "CZK")
        fallback.get_prices.assert_called_once_with(["ETH"], "CZK")
        assert result["BTC"] == Decimal("2500000")
        assert result["ETH"] == Decimal("80000")

    def test_fallback_used_when_primary_raises(self):
        primary = MagicMock()
        primary.get_prices = MagicMock(side_effect=RuntimeError("API down"))
        fallback = self._make_stub({"BTC": 2500000})
        composite = CompositePriceProvider(primary, fallback)
        result = composite.get_prices(["BTC"], "CZK")
        assert result["BTC"] == Decimal("2500000")

    def test_empty_when_both_fail(self):
        primary  = MagicMock()
        primary.get_prices = MagicMock(side_effect=RuntimeError("down"))
        fallback = MagicMock()
        fallback.get_prices = MagicMock(side_effect=RuntimeError("down"))
        composite = CompositePriceProvider(primary, fallback)
        result = composite.get_prices(["BTC"], "CZK")
        assert result == {}

    def test_fallback_not_called_when_primary_covers_all(self):
        primary  = self._make_stub({"BTC": 2500000, "ETH": 80000})
        fallback = self._make_stub({"ATOM": 500})
        composite = CompositePriceProvider(primary, fallback)
        composite.get_prices(["BTC", "ETH"], "CZK")
        fallback.get_prices.assert_not_called()
