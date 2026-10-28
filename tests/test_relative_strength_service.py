"""Tests for core/services/relative_strength_service.py.

compute_relative_strength() is the service layer for the Relative
Strength table: pure price-performance comparison (NOT personal P&L,
NOT a trading indicator) for currently held headline assets. Uses a
temp DB (for HistoricalPriceStore) + a fake price_provider (no network).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from core.historical_price_store import HistoricalPrice, HistoricalPriceStore
from core.prices.coingecko import PriceWithChange
from core.services.relative_strength_service import compute_relative_strength
from core.services.ui_facade import PositionDTO, create_db

_D = Decimal
_TODAY = date.today()
_D7 = _TODAY - timedelta(days=7)
_D30 = _TODAY - timedelta(days=30)


class _FakeProvider:
    """No-network stand-in duck-typing CoinGeckoPriceProvider's two
    relevant methods."""

    def __init__(self, spot: dict, change: dict | None = None, raise_on_change: bool = False,
                 raise_on_spot: bool = False):
        self._spot = spot
        self._change = change or {}
        self._raise_on_change = raise_on_change
        self._raise_on_spot = raise_on_spot

    def get_prices(self, assets, fiat):
        if self._raise_on_spot:
            raise RuntimeError("network down")
        return {a: self._spot[a] for a in assets if a in self._spot}

    def get_prices_with_24h_change(self, assets, fiat):
        if self._raise_on_change:
            raise RuntimeError("network down")
        return {a: self._change[a] for a in assets if a in self._change}


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    create_db(db_path)
    return db_path


def _seed(db_path, asset, d, price_czk):
    store = HistoricalPriceStore(db_path)
    store.set_price(HistoricalPrice(asset=asset, price_date=d, price_czk=_D(price_czk), fetched_at=datetime.now()))
    store.close()


def _pos(asset, qty, spot_price):
    return PositionDTO(
        asset=asset, quantity=_D(qty), wac=_D("1"), cost_basis=_D("1"),
        realized_pnl=_D("0"), value=_D(spot_price) * _D(qty) if spot_price is not None else None,
        spot_price=_D(spot_price) if spot_price is not None else None,
    )


def test_24h_change_parsed_from_provider(db):
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")}, change={"ATOM": PriceWithChange(price=_D("120"), change_24h_pct=_D("2.5"))})
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].change_24h_pct == _D("2.5")


def test_7d_return_calculated_from_spot_and_history(db):
    _seed(db, "ATOM", _D7, "100")
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].return_7d == _D("0.2")


def test_30d_return_calculated_from_spot_and_history(db):
    _seed(db, "ATOM", _D30, "90")
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].return_30d.quantize(_D("0.0001")) == _D("0.3333")


def test_vs_btc_7d_is_excess_return_over_btc(db):
    _seed(db, "ATOM", _D7, "100")
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})  # BTC 7D = +10%
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].return_7d == _D("0.2")        # ATOM 7D = +20%
    assert rows[0].vs_btc_7d == _D("0.1")         # 20% - 10%


def test_btc_used_as_benchmark_but_never_a_row(db):
    _seed(db, "ATOM", _D7, "100")
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120"), _pos("BTC", "1", "1100000")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assets = [r.asset for r in rows]
    assert "BTC" not in assets
    assert assets == ["ATOM"]
    # Benchmark still correctly applied:
    assert rows[0].vs_btc_7d == _D("0.1")


def test_stablecoins_excluded(db):
    positions = [_pos("USDC", "100", "25"), _pos("USDT", "100", "25")]
    provider = _FakeProvider(spot={})
    rows = compute_relative_strength(db, positions, provider)
    assert rows == []


def test_presale_assets_excluded(db):
    positions = [_pos("TICS", "100", "1"), _pos("SOLX", "100", "1")]
    provider = _FakeProvider(spot={})
    rows = compute_relative_strength(db, positions, provider)
    assert rows == []


def test_zero_quantity_excluded(db):
    positions = [_pos("ATOM", "0", "100")]
    provider = _FakeProvider(spot={})
    rows = compute_relative_strength(db, positions, provider)
    assert rows == []


def test_recently_bought_asset_not_excluded_just_for_recency(db):
    """A recent purchase is NOT a reason to drop the row -- only genuinely
    missing historical price data is. Here the asset's own price history
    goes back further than the (hypothetical) purchase date, so 7D/30D
    must still compute normally."""
    _seed(db, "ATOM", _D7, "100")
    _seed(db, "ATOM", _D30, "90")
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]  # "bought yesterday", in spirit
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].return_7d is not None
    assert rows[0].return_30d is not None


def test_missing_7d_price_yields_none_not_zero(db):
    # No historical price seeded for ATOM at all.
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].return_7d is None


def test_missing_30d_price_yields_none_not_zero(db):
    _seed(db, "ATOM", _D7, "100")  # 7D seeded, 30D deliberately not
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].return_7d is not None
    assert rows[0].return_30d is None


def test_missing_btc_7d_makes_vs_btc_none_for_every_row(db):
    _seed(db, "ATOM", _D7, "100")
    _seed(db, "DOT", _D7, "50")
    # BTC historical price deliberately never seeded.
    positions = [_pos("ATOM", "10", "120"), _pos("DOT", "10", "55")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert all(r.vs_btc_7d is None for r in rows)
    # 7D itself is unaffected by the missing BTC benchmark:
    assert all(r.return_7d is not None for r in rows)


def test_24h_change_failure_does_not_break_7d_30d(db):
    _seed(db, "ATOM", _D7, "100")
    _seed(db, "BTC", _D7, "1000000")
    positions = [_pos("ATOM", "10", "120")]
    provider = _FakeProvider(spot={"BTC": _D("1100000")}, raise_on_change=True)
    rows = compute_relative_strength(db, positions, provider)
    assert rows[0].change_24h_pct is None
    assert rows[0].return_7d == _D("0.2")


def test_default_sort_is_7d_descending_with_none_last(db):
    _seed(db, "AAA", _D7, "100")   # +20%
    _seed(db, "BBB", _D7, "100")   # +50%
    # CCC: no 7D price seeded -> None, must sort last regardless of value.
    _seed(db, "BTC", _D7, "1000000")
    positions = [
        _pos("AAA", "1", "120"),
        _pos("BBB", "1", "150"),
        _pos("CCC", "1", "999"),
    ]
    provider = _FakeProvider(spot={"BTC": _D("1100000")})
    rows = compute_relative_strength(db, positions, provider)
    assert [r.asset for r in rows] == ["BBB", "AAA", "CCC"]
    assert rows[-1].return_7d is None


def test_empty_positions_returns_empty_list(db):
    assert compute_relative_strength(db, [], _FakeProvider(spot={})) == []


def test_none_price_provider_degrades_gracefully(db):
    _seed(db, "ATOM", _D7, "100")
    positions = [_pos("ATOM", "10", "120")]
    rows = compute_relative_strength(db, positions, None)
    assert rows[0].change_24h_pct is None
    assert rows[0].vs_btc_7d is None
    assert rows[0].return_7d == _D("0.2")  # still works -- uses PositionDTO.spot_price
