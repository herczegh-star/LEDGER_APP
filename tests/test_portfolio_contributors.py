"""Tests for core/services/portfolio_history_service.py —
get_portfolio_contributors().

contribution_i = (value_end_i - value_start_i) - net_capital_flow_into_i

Every numeric expectation below was independently hand-derived (see the
implementation conversation) before being encoded here — not reverse
-engineered from whatever the code happened to output.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from core.historical_price_store import HistoricalPrice, HistoricalPriceStore
from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.portfolio_history_service import get_portfolio_contributors
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import create_db

_D_START = date(2025, 12, 31)
_D_END = date(2026, 1, 5)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    create_db(db_path)
    return db_path


def _price(db_path, asset, d, price_czk):
    store = HistoricalPriceStore(db_path)
    store.set_price(HistoricalPrice(asset=asset, price_date=d, price_czk=Decimal(price_czk), fetched_at=datetime.now()))
    store.close()


def _get(contributors, asset):
    return next(c for c in contributors if c.asset == asset)


# ── 1. Pure BUY (also covers "newly opened position") ──────────────────────

def test_pure_buy_during_window(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 3), base_asset="ETH",
        base_amount=Decimal("5"), quote_currency="CZK", quote_amount=Decimal("500"), venue="kraken",
    ))
    _price(db, "ETH", _D_END, "120")  # 5*120=600
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "ETH")
    assert c.value_start == Decimal("0")
    assert c.value_end == Decimal("600")
    assert c.contribution_czk == Decimal("100")  # 600 - 0 - 500(buy cost)


# ── 2. SELL — full position closed within window ────────────────────────────

def test_full_sell_closes_position(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="SELL", timestamp=datetime(2026, 1, 3), base_asset="BTC",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1200"), venue="kraken",
    ))
    _price(db, "BTC", _D_START, "100")
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "BTC")
    assert c.value_start == Decimal("1000")
    assert c.value_end == Decimal("0")  # not held at end — no price lookup needed
    assert c.contribution_czk == Decimal("200")  # realized gain: 1200 proceeds - 1000 cost


# ── 3. Partial SELL ──────────────────────────────────────────────────────────

def test_partial_sell(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="SELL", timestamp=datetime(2026, 1, 3), base_asset="BTC",
        base_amount=Decimal("4"), quote_currency="CZK", quote_amount=Decimal("440"), venue="kraken",
    ))
    _price(db, "BTC", _D_START, "100")
    _price(db, "BTC", _D_END, "120")
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "BTC")
    assert c.value_start == Decimal("1000")
    assert c.value_end == Decimal("720")  # 6 remaining * 120
    # 4 sold @110 (realized +40) + 6 held, price 100->120 (+120 unrealized) = +160
    assert c.contribution_czk == Decimal("160")


# ── 4. Transfer between venues = zero economic impact ───────────────────────

def test_transfer_between_venues_has_zero_impact(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    store = LedgerStore(db)
    store.import_rows([
        RawRow(id="tx-1", timestamp=datetime(2026, 1, 3), type="TRANSFER", asset="BTC",
               amount=Decimal("-5"), currency="BTC", price=Decimal("1"), venue="kraken"),
        RawRow(id="tx-1", timestamp=datetime(2026, 1, 3), type="TRANSFER", asset="BTC",
               amount=Decimal("5"), currency="BTC", price=Decimal("1"), venue="ledger_wallet"),
    ])
    store.close()
    _price(db, "BTC", _D_START, "100")
    _price(db, "BTC", _D_END, "100")  # flat price — isolate the transfer's own effect
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "BTC")
    assert c.value_start == Decimal("1000")
    assert c.value_end == Decimal("1000")  # global quantity unchanged (10 total)
    assert c.contribution_czk == Decimal("0")


# ── 5. Asset held on multiple venues — aggregated globally ──────────────────

def test_asset_on_multiple_venues_is_aggregated_globally(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("3"), quote_currency="CZK", quote_amount=Decimal("300"), venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("7"), quote_currency="CZK", quote_amount=Decimal("700"), venue="bybit",
    ))
    _price(db, "BTC", _D_START, "100")   # 10 * 100 = 1000
    _price(db, "BTC", _D_END, "110")     # 10 * 110 = 1100
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "BTC")
    assert c.value_start == Decimal("1000")
    assert c.value_end == Decimal("1100")
    assert c.contribution_czk == Decimal("100")  # pure price move, no window activity


# ── 6. Asset -> asset SWAP via cost_transfer ─────────────────────────────────

def test_swap_capital_flow_attributed_via_cost_transfer(db):
    """TAO price drops 100->90 (true loss on retained units); RUNE
    (acquired via swap at implied cost 2/unit) rises to 3/unit (true gain).
    Independently hand-verified: TAO -60, RUNE +200."""
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="TAO",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    store = LedgerStore(db)
    store.import_rows([
        RawRow(id="swap-1", timestamp=datetime(2026, 1, 3), type="SELL", asset="TAO",
               amount=Decimal("-4"), currency="RUNE", price=None, venue="kraken"),
        RawRow(id="swap-1", timestamp=datetime(2026, 1, 3), type="BUY", asset="RUNE",
               amount=Decimal("200"), currency="TAO", price=None, venue="kraken"),
    ])
    store.close()
    _price(db, "TAO", _D_START, "100")
    _price(db, "TAO", _D_END, "90")
    _price(db, "RUNE", _D_END, "3")

    contributors = get_portfolio_contributors(db, _D_START, _D_END)
    tao = _get(contributors, "TAO")
    rune = _get(contributors, "RUNE")

    assert tao.value_start == Decimal("1000")
    assert tao.value_end == Decimal("540")       # 6 * 90
    assert tao.contribution_czk == Decimal("-60")

    assert rune.value_start == Decimal("0")
    assert rune.value_end == Decimal("600")      # 200 * 3
    assert rune.contribution_czk == Decimal("200")


def test_swap_with_flat_prices_yields_zero_contribution_both_sides(db):
    """Sanity check: if neither asset's price actually moved, a pure
    capital reallocation via swap must show 0 contribution on both legs —
    proves the swap capital flow exactly offsets the quantity change."""
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="TAO",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    store = LedgerStore(db)
    store.import_rows([
        RawRow(id="swap-1", timestamp=datetime(2026, 1, 3), type="SELL", asset="TAO",
               amount=Decimal("-4"), currency="RUNE", price=None, venue="kraken"),
        RawRow(id="swap-1", timestamp=datetime(2026, 1, 3), type="BUY", asset="RUNE",
               amount=Decimal("200"), currency="TAO", price=None, venue="kraken"),
    ])
    store.close()
    _price(db, "TAO", _D_START, "100")
    _price(db, "TAO", _D_END, "100")
    _price(db, "RUNE", _D_END, "2")  # matches the implied swap-in cost of 400/200

    contributors = get_portfolio_contributors(db, _D_START, _D_END)
    assert _get(contributors, "TAO").contribution_czk == Decimal("0")
    assert _get(contributors, "RUNE").contribution_czk == Decimal("0")


# ── 7. Missing historical price ─────────────────────────────────────────────

def test_missing_start_price_yields_none_contribution(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    _price(db, "BTC", _D_END, "120")  # no price for _D_START
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "BTC")
    assert c.value_start is None
    assert c.value_end == Decimal("120")
    assert c.contribution_czk is None  # never fabricate 0


def test_missing_end_price_yields_none_contribution(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    _price(db, "BTC", _D_START, "100")  # no price for _D_END
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "BTC")
    assert c.value_end is None
    assert c.contribution_czk is None


# ── Presale exclusion (consistent with Value/PnL/ROI Trend's headline scope) ─

def test_presale_assets_excluded(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="TICS",
        base_amount=Decimal("100"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    _price(db, "TICS", _D_START, "10")
    _price(db, "TICS", _D_END, "12")
    contributors = get_portfolio_contributors(db, _D_START, _D_END)
    assert all(c.asset != "TICS" for c in contributors)
    assert all(c.asset != "SOLX" for c in contributors)


# ── Edge cases ───────────────────────────────────────────────────────────────

def test_empty_ledger_returns_empty_list(db):
    assert get_portfolio_contributors(db, _D_START, _D_END) == []


def test_date_from_after_date_to_raises(db):
    with pytest.raises(ValueError):
        get_portfolio_contributors(db, _D_END, _D_START)


def test_opened_and_closed_within_same_window(db):
    """Bought and fully sold within the window — value_start=0,
    value_end=0, contribution must still equal the realized gain."""
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1), base_asset="ETH",
        base_amount=Decimal("2"), quote_currency="CZK", quote_amount=Decimal("200"), venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="SELL", timestamp=datetime(2026, 1, 4), base_asset="ETH",
        base_amount=Decimal("2"), quote_currency="CZK", quote_amount=Decimal("240"), venue="kraken",
    ))
    c = _get(get_portfolio_contributors(db, _D_START, _D_END), "ETH")
    assert c.value_start == Decimal("0")
    assert c.value_end == Decimal("0")
    assert c.contribution_czk == Decimal("40")  # 240 proceeds - 200 cost
