"""Unit tests for core/prices/ — no real HTTP calls.

All network I/O is mocked with unittest.mock.patch so tests are
fully offline and deterministic.
"""
from __future__ import annotations

import json
from datetime import date
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

    def test_usdc_usdt_are_mapped(self):
        assert "USDC" in COINGECKO_IDS
        assert "USDT" in COINGECKO_IDS

    def test_no_api_key_means_no_extra_header_byte_for_byte_same_as_before(self):
        """Default behaviour (no COINGECKO_API_KEY set, no api_key= passed)
        must be identical to before get_historical_prices()/api_key existed."""
        response_body = {"bitcoin": {"czk": 2500000}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            provider.get_prices(["BTC"], "CZK")
            headers_sent = m.call_args[0][0].headers
        assert not any(k.lower() == "x-cg-demo-api-key" for k in headers_sent)

    def test_explicit_api_key_adds_header(self):
        response_body = {"bitcoin": {"czk": 2500000}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60, api_key="demo-123")
            provider.get_prices(["BTC"], "CZK")
            headers_sent = m.call_args[0][0].headers
        assert headers_sent.get("X-cg-demo-api-key") == "demo-123"

    def test_api_key_from_environment_variable(self, monkeypatch):
        monkeypatch.setenv("COINGECKO_API_KEY", "from-env-456")
        response_body = {"bitcoin": {"czk": 2500000}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)  # no explicit api_key
            provider.get_prices(["BTC"], "CZK")
            headers_sent = m.call_args[0][0].headers
        assert headers_sent.get("X-cg-demo-api-key") == "from-env-456"

    def test_explicit_api_key_overrides_environment(self, monkeypatch):
        monkeypatch.setenv("COINGECKO_API_KEY", "from-env")
        provider = CoinGeckoPriceProvider(ttl_seconds=60, api_key="explicit")
        assert provider._api_key == "explicit"


# ── CoinGeckoPriceProvider.get_historical_prices() ───────────────────────────

class TestCoinGeckoHistoricalPrices:
    def test_unknown_asset_returns_empty_without_http_call(self):
        with patch("urllib.request.urlopen") as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("FAKETOKEN", "CZK", days=30)
        m.assert_not_called()
        assert result == {}

    def test_url_contains_coin_id_and_days_and_daily_interval(self):
        response_body = {"prices": [[1704067200000, 2500000]]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            provider.get_historical_prices("BTC", "CZK", days=90)
            url_called = m.call_args[0][0].full_url
        assert "coins/bitcoin/market_chart" in url_called
        assert "days=90" in url_called
        assert "interval=daily" in url_called
        assert "vs_currency=czk" in url_called

    def test_parses_timestamp_ms_to_utc_date(self):
        # 1704067200000 ms == 2024-01-01T00:00:00Z
        response_body = {"prices": [[1704067200000, 2500000]]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=30)
        assert result[date(2024, 1, 1)] == Decimal("2500000")

    def test_multiple_points_same_day_last_one_wins(self):
        response_body = {"prices": [
            [1704067200000, 2500000],   # 2024-01-01T00:00:00Z
            [1704110400000, 2600000],   # 2024-01-01T12:00:00Z, same UTC date
        ]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=30)
        assert result[date(2024, 1, 1)] == Decimal("2600000")

    def test_network_error_returns_empty_not_raise(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=30)
        assert result == {}

    def test_malformed_response_returns_empty_not_raise(self):
        response_body = {"unexpected": "shape"}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=30)
        assert result == {}

    def test_one_malformed_point_does_not_void_other_valid_points(self):
        response_body = {"prices": [
            [1704067200000, 2500000],
            ["not-a-timestamp", "not-a-price"],
            [1704153600000, 2600000],   # 2024-01-02T00:00:00Z
        ]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=30)
        assert result[date(2024, 1, 1)] == Decimal("2500000")
        assert result[date(2024, 1, 2)] == Decimal("2600000")

    def test_zero_days_returns_empty_without_http_call(self):
        with patch("urllib.request.urlopen") as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=0)
        m.assert_not_called()
        assert result == {}


# ── New CoinGecko mappings (2026-10-28 verification pass) ───────────────────

class TestNewCoinGeckoMappings:
    """Each mapping below was the SOLE exact-symbol match on CoinGecko's own
    /search at verification time — see core/prices/coingecko.py's module
    docstring. PUMP and TON were deliberately left unmapped (genuine
    ticker-collision risk) and must stay absent until a human confirms."""

    @pytest.mark.parametrize("ticker,expected_id", [
        ("ENA", "ethena"),
        ("HYPE", "hyperliquid"),
        ("INJ", "injective-protocol"),
        ("MON", "monad"),
        ("MORPHO", "morpho"),
        ("NEAR", "near"),
        ("RENDER", "render-token"),
        ("XLM", "stellar"),
    ])
    def test_new_mapping_resolves_to_verified_id(self, ticker, expected_id):
        assert COINGECKO_IDS[ticker] == expected_id

    def test_pump_deliberately_unresolved(self):
        """Two real coins share the PUMP ticker (pump-fun rank 50 vs.
        big-pump rank 2802) — must not be auto-resolved without confirmation."""
        assert "PUMP" not in COINGECKO_IDS

    def test_ton_deliberately_unresolved(self):
        """CoinGecko's current TON-symbol coin (Tokamak Network) is NOT
        the Toncoin this ledger almost certainly means — the real
        candidate (the-open-network) now trades under a different symbol
        after an apparent rebrand. Must not be auto-resolved."""
        assert "TON" not in COINGECKO_IDS


# ── CoinGeckoPriceProvider.get_historical_prices_range() ────────────────────

class TestCoinGeckoHistoricalPricesRange:
    def test_unknown_asset_returns_empty_without_http_call(self):
        with patch("urllib.request.urlopen") as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range(
                "FAKETOKEN", "CZK", date(2026, 1, 1), date(2026, 1, 31),
            )
        m.assert_not_called()
        assert result == {}

    def test_date_from_after_date_to_returns_empty_without_http_call(self):
        with patch("urllib.request.urlopen") as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range(
                "BTC", "CZK", date(2026, 2, 1), date(2026, 1, 1),
            )
        m.assert_not_called()
        assert result == {}

    def test_url_contains_coin_id_and_range_endpoint_and_unix_bounds(self):
        response_body = {"prices": [[1704067200000, 2500000]]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)) as m:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            provider.get_historical_prices_range("BTC", "CZK", date(2026, 1, 1), date(2026, 6, 1))
            url_called = m.call_args[0][0].full_url
        assert "coins/bitcoin/market_chart/range" in url_called
        assert "vs_currency=czk" in url_called
        assert "from=" in url_called
        assert "to=" in url_called

    def test_parses_timestamp_ms_to_utc_date(self):
        response_body = {"prices": [[1704067200000, 2500000]]}  # 2024-01-01T00:00:00Z
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range("BTC", "CZK", date(2024, 1, 1), date(2024, 1, 2))
        assert result[date(2024, 1, 1)] == Decimal("2500000")

    def test_network_error_returns_empty_not_raise(self):
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range("BTC", "CZK", date(2026, 1, 1), date(2026, 1, 31))
        assert result == {}

    def test_malformed_response_returns_empty_not_raise(self):
        response_body = {"unexpected": "shape"}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(response_body)):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range("BTC", "CZK", date(2026, 1, 1), date(2026, 1, 31))
        assert result == {}


# ── Retry-on-429 (both historical methods share _fetch_json_with_retry) ─────

def _http_error(code: int, body: bytes = b"{}"):
    import urllib.error
    return urllib.error.HTTPError(
        url="http://x", code=code, msg="error", hdrs=None, fp=BytesIO(body),
    )


class TestCoinGeckoRetry:
    def test_429_then_success_retries_and_returns_data(self):
        response_body = {"prices": [[1704067200000, 2500000]]}
        ok_response = _mock_urlopen(response_body)
        with patch("urllib.request.urlopen", side_effect=[_http_error(429), ok_response]) as m, \
             patch("core.prices.coingecko.time.sleep") as sleep_mock:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range("BTC", "CZK", date(2026, 1, 1), date(2026, 1, 31))
        assert m.call_count == 2
        assert sleep_mock.call_count == 1  # backed off exactly once, then succeeded
        assert result[date(2024, 1, 1)] == Decimal("2500000")

    def test_429_exhausts_retries_then_returns_empty_not_raise(self):
        with patch("urllib.request.urlopen", side_effect=[_http_error(429)] * 10) as m, \
             patch("core.prices.coingecko.time.sleep"):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range("BTC", "CZK", date(2026, 1, 1), date(2026, 1, 31))
        assert result == {}
        # Bounded: at most (1 initial + _MAX_RETRIES) attempts — never an infinite loop.
        from core.prices.coingecko import _MAX_RETRIES
        assert m.call_count == _MAX_RETRIES + 1

    def test_401_does_not_retry(self):
        """A 401 (e.g. 'exceeds the allowed time range') can never succeed
        by retrying — must fail fast, no wasted attempts."""
        with patch("urllib.request.urlopen", side_effect=_http_error(401)) as m, \
             patch("core.prices.coingecko.time.sleep") as sleep_mock:
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices_range("BTC", "CZK", date(2026, 1, 1), date(2026, 1, 31))
        assert result == {}
        assert m.call_count == 1
        sleep_mock.assert_not_called()

    def test_get_historical_prices_also_retries_on_429(self):
        """Same retry contract applies to the trailing-days method, not
        just the range method — both share _fetch_json_with_retry()."""
        response_body = {"prices": [[1704067200000, 2500000]]}
        ok_response = _mock_urlopen(response_body)
        with patch("urllib.request.urlopen", side_effect=[_http_error(429), ok_response]) as m, \
             patch("core.prices.coingecko.time.sleep"):
            provider = CoinGeckoPriceProvider(ttl_seconds=60)
            result = provider.get_historical_prices("BTC", "CZK", days=30)
        assert m.call_count == 2
        assert result[date(2024, 1, 1)] == Decimal("2500000")


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
