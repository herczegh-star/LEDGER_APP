"""Tests for core/services/portfolio_pulse_service.py and the small
compute_headline_value() helper in core/services/ui_facade.py that
Portfolio Pulse's "Portfolio Value" card reuses.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from core.prices.coingecko import PriceWithChange
from core.services.portfolio_pulse_service import Portfolio24hChangeDTO, compute_portfolio_24h_change
from core.services.ui_facade import PositionDTO, compute_headline_value

_D = Decimal


def _pos(asset, qty, value):
    return PositionDTO(
        asset=asset, quantity=_D(qty), wac=_D("1"), cost_basis=_D("1"),
        realized_pnl=_D("0"), value=_D(value) if value is not None else None,
    )


class _FakeProvider:
    def __init__(self, change: dict, raise_exc: bool = False):
        self._change = change
        self._raise_exc = raise_exc

    def get_prices_with_24h_change(self, assets, fiat):
        if self._raise_exc:
            raise RuntimeError("network down")
        return {a: self._change[a] for a in assets if a in self._change}


def _chg(pct):
    return PriceWithChange(price=_D("1"), change_24h_pct=_D(pct))


# ── compute_headline_value() ─────────────────────────────────────────────

def test_headline_value_sums_non_presale_positions_with_value():
    positions = [_pos("BTC", "1", "700"), _pos("ETH", "1", "300"), _pos("TICS", "1", "999")]
    assert compute_headline_value(positions) == _D("1000")


def test_headline_value_none_when_nothing_priced():
    positions = [_pos("BTC", "1", None)]
    assert compute_headline_value(positions) is None


def test_headline_value_empty_portfolio_is_none():
    assert compute_headline_value([]) is None


# ── compute_portfolio_24h_change() ──────────────────────────────────────────

def test_weighted_24h_change_full_coverage():
    positions = [_pos("BTC", "1", "700"), _pos("ETH", "1", "300")]
    provider = _FakeProvider({"BTC": _chg("10"), "ETH": _chg("-10")})
    result = compute_portfolio_24h_change(positions, provider)
    assert result.change_pct == _D("4")  # 0.7*10 + 0.3*-10
    assert result.coverage == _D("1")


def test_renormalization_when_one_asset_missing_24h_data():
    positions = [_pos("BTC", "1", "500"), _pos("ETH", "1", "440"), _pos("DOT", "1", "60")]
    # DOT has no 24h figure -> covered_value = 940 (94% coverage, above the
    # threshold), weights renormalized over BTC/ETH only.
    provider = _FakeProvider({"BTC": _chg("10"), "ETH": _chg("20")})
    result = compute_portfolio_24h_change(positions, provider)
    expected = (_D("500") * _D("10") + _D("440") * _D("20")) / _D("940")
    assert result.change_pct == expected
    assert result.coverage == _D("940") / _D("1000")


def test_missing_asset_never_treated_as_zero():
    """If a missing-data asset were silently treated as 0% change, a
    large missing holding would drag the weighted average toward 0 --
    this must NOT happen; the missing asset must vanish from both sides
    of the division instead."""
    positions = [_pos("BTC", "1", "100"), _pos("HUGE", "1", "10000")]
    provider = _FakeProvider({"BTC": _chg("50")})  # HUGE missing entirely
    result = compute_portfolio_24h_change(positions, provider)
    # Coverage is tiny (100 / 10100) so the card should withhold -- but
    # IF it were computed, it must equal BTC's own +50%, never diluted
    # toward 0 by HUGE's absence.
    assert result.covered_value == _D("100")
    assert result.change_pct is None  # withheld due to low coverage (see below)


def test_coverage_calculation():
    positions = [_pos("BTC", "1", "600"), _pos("ETH", "1", "400")]
    provider = _FakeProvider({"BTC": _chg("5")})  # only BTC covered
    result = compute_portfolio_24h_change(positions, provider)
    assert result.coverage == _D("600") / _D("1000")
    assert result.covered_value == _D("600")
    assert result.total_value == _D("1000")


def test_below_coverage_threshold_returns_none_change_pct():
    positions = [_pos("BTC", "1", "50"), _pos("ETH", "1", "50")]
    provider = _FakeProvider({"BTC": _chg("100")})  # 50% coverage, well under 90%
    result = compute_portfolio_24h_change(positions, provider)
    assert result.coverage == _D("0.5")
    assert result.change_pct is None


def test_at_or_above_coverage_threshold_returns_a_value():
    positions = [_pos("BTC", "1", "95"), _pos("ETH", "1", "5")]
    provider = _FakeProvider({"BTC": _chg("10")})  # 95% coverage, above 90%
    result = compute_portfolio_24h_change(positions, provider)
    assert result.coverage == _D("0.95")
    assert result.change_pct == _D("10")


def test_presale_excluded_from_scope():
    positions = [_pos("BTC", "1", "100"), _pos("TICS", "1", "999")]
    provider = _FakeProvider({"BTC": _chg("5"), "TICS": _chg("500")})
    result = compute_portfolio_24h_change(positions, provider)
    assert result.total_value == _D("100")  # TICS excluded from scope entirely
    assert result.change_pct == _D("5")


def test_zero_quantity_excluded():
    positions = [_pos("BTC", "1", "100"), _pos("OLD", "0", "0")]
    provider = _FakeProvider({"BTC": _chg("5")})
    result = compute_portfolio_24h_change(positions, provider)
    assert result.total_value == _D("100")


def test_empty_portfolio_returns_none_change_pct():
    result = compute_portfolio_24h_change([], _FakeProvider({}))
    assert result == Portfolio24hChangeDTO(change_pct=None, covered_value=_D("0"), total_value=_D("0"), coverage=None)


def test_provider_exception_degrades_gracefully():
    positions = [_pos("BTC", "1", "100")]
    provider = _FakeProvider({}, raise_exc=True)
    result = compute_portfolio_24h_change(positions, provider)
    assert result.change_pct is None
    assert result.covered_value == _D("0")


def test_none_price_provider_degrades_gracefully():
    positions = [_pos("BTC", "1", "100")]
    result = compute_portfolio_24h_change(positions, None)
    assert result.change_pct is None
