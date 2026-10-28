"""Integration tests for the Portfolio Pulse section in
ui/modules/analysis_view.py (top of the Analysis page): Portfolio Value,
24H Change, Best Contributor, Worst Contributor.

Builds a real build_analysis_view() view against a temp DB with a mocked
Flet page and walks the real control tree -- same approach already used
for Allocation / Relative Strength / Contributors.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import flet as ft
import pytest

import ui.modules.analysis_view as av
from core.historical_price_store import HistoricalPrice, HistoricalPriceStore
from core.prices.coingecko import CoinGeckoPriceProvider, PriceWithChange
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import create_db


class _FakeSpotProvider:
    def __init__(self, prices: dict):
        self._prices = prices

    def get_prices(self, assets, fiat):
        return {a: self._prices[a] for a in assets if a in self._prices}


@pytest.fixture(autouse=True)
def no_network_coingecko(monkeypatch):
    """Default: no live 24h data, no network. Tests that need specific
    24h figures override via monkeypatch.setattr again within the test."""
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change", lambda self, assets, fiat: {})
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices", lambda self, assets, fiat: {})


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    create_db(db_path)
    return db_path


def _seed(db_path, asset, d, price_czk):
    store = HistoricalPriceStore(db_path)
    store.set_price(HistoricalPrice(asset=asset, price_date=d, price_czk=Decimal(price_czk), fetched_at=datetime.now()))
    store.close()


def _find(ctrl, predicate, acc):
    if hasattr(ctrl, "controls"):
        for c in ctrl.controls:
            _find(c, predicate, acc)
    if hasattr(ctrl, "content") and ctrl.content is not None:
        _find(ctrl.content, predicate, acc)
    if predicate(ctrl):
        acc.append(ctrl)


def _pulse_cards(view) -> dict:
    """{label -> value Text control} for the 4 Pulse cards, found by
    each card's own label Text sitting right before the value Text in
    its Column."""
    cols = []
    _find(view, lambda c: isinstance(c, ft.Column) and len(c.controls) == 3
          and isinstance(c.controls[0], ft.Text)
          and c.controls[0].value in ("Portfolio Value", "24H Change", "Best Contributor", "Worst Contributor"), cols)
    return {c.controls[0].value: c.controls[-1] for c in cols}


def test_portfolio_value_matches_headline_snapshot(db):
    for asset, qty, price in [("BTC", "1", "700"), ("ETH", "1", "300"), ("TICS", "1", "9999")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price) * Decimal(qty), venue="kraken",
        ))
    provider = _FakeSpotProvider({"BTC": Decimal("700"), "ETH": Decimal("300"), "TICS": Decimal("9999")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    cards = _pulse_cards(view)
    assert cards["Portfolio Value"].value == "1\xa0000 CZK"  # TICS (presale) excluded, 700+300=1000


def test_best_and_worst_contributor_match_max_min_contribution(db, monkeypatch):
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change", lambda self, assets, fiat: {})
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices", lambda self, assets, fiat: {})

    for asset, qty, p0 in [("AAA", "10", "100"), ("BBB", "10", "100"), ("CCC", "10", "100")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(qty) * Decimal(p0), venue="kraken",
        ))
    earliest = av.get_earliest_ledger_date(db)
    date_to = av.date.today()
    _seed(db, "AAA", earliest, "100"); _seed(db, "AAA", date_to, "110")  # +100
    _seed(db, "BBB", earliest, "100"); _seed(db, "BBB", date_to, "80")   # -200
    _seed(db, "CCC", earliest, "100"); _seed(db, "CCC", date_to, "105")  # +50

    provider = _FakeSpotProvider({"AAA": Decimal("110"), "BBB": Decimal("80"), "CCC": Decimal("105")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    # Default period is 30D -- switch to ALL so our hand-seeded prices (at
    # the ledger's earliest date) actually fall inside the window.
    period_acc = []
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Period", period_acc)
    period_acc[0].value = "ALL"
    period_acc[0].on_select(None)

    cards = _pulse_cards(view)
    assert cards["Best Contributor"].value.startswith("AAA")
    assert cards["Worst Contributor"].value.startswith("BBB")


def test_weighted_24h_change_rendered(db, monkeypatch):
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change",
                         lambda self, assets, fiat: {"BTC": PriceWithChange(price=Decimal("700"), change_24h_pct=Decimal("10")),
                                                      "ETH": PriceWithChange(price=Decimal("300"), change_24h_pct=Decimal("-10"))})
    for asset, qty, price in [("BTC", "1", "700"), ("ETH", "1", "300")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price) * Decimal(qty), venue="kraken",
        ))
    provider = _FakeSpotProvider({"BTC": Decimal("700"), "ETH": Decimal("300")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    cards = _pulse_cards(view)
    assert cards["24H Change"].value == "+4.00%"  # 0.7*10 + 0.3*-10


def test_low_coverage_shows_dash(db, monkeypatch):
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change",
                         lambda self, assets, fiat: {"BTC": PriceWithChange(price=Decimal("50"), change_24h_pct=Decimal("10"))})
    for asset, qty, price in [("BTC", "1", "50"), ("ETH", "1", "50")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price) * Decimal(qty), venue="kraken",
        ))
    provider = _FakeSpotProvider({"BTC": Decimal("50"), "ETH": Decimal("50")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    cards = _pulse_cards(view)
    assert cards["24H Change"].value == "—"  # 50% coverage -- below the 90% threshold


def test_period_change_updates_best_worst_without_new_selector(db, monkeypatch):
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change", lambda self, assets, fiat: {})
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices", lambda self, assets, fiat: {})

    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    earliest = av.get_earliest_ledger_date(db)
    # Only seed a historical price far enough back that it falls OUTSIDE
    # the default 30D window but INSIDE "ALL" -- so Best Contributor is
    # "-" under the default period and a real value only under "ALL",
    # proving the shared period selector actually drives this card.
    _seed(db, "BTC", earliest, "100")
    _seed(db, "BTC", av.date.today(), "120")
    provider = _FakeSpotProvider({"BTC": Decimal("120")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    period_acc = []
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Period", period_acc)
    assert len(period_acc) == 1  # no new selector was added

    period_acc[0].value = "ALL"
    period_acc[0].on_select(None)
    after = _pulse_cards(view)["Best Contributor"].value
    assert after.startswith("BTC")


def test_no_extra_contributors_fetch_from_pulse(db, monkeypatch):
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change", lambda self, assets, fiat: {})
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices", lambda self, assets, fiat: {})

    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    calls = {"n": 0}
    real = av.get_portfolio_contributors

    def _spy(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(av, "get_portfolio_contributors", _spy)

    provider = _FakeSpotProvider({"BTC": Decimal("120")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    assert calls["n"] == 1  # Contributors section fetched once; Pulse reused it, no second call


def test_empty_portfolio_shows_dashes(db):
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=None, fiat="CZK")
    run_fn()

    cards = _pulse_cards(view)
    assert cards["Portfolio Value"].value == "—"
    assert cards["24H Change"].value == "—"
    assert cards["Best Contributor"].value == "—"
    assert cards["Worst Contributor"].value == "—"


def test_sell_simulator_still_works_after_pulse_addition(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=None, fiat="CZK")
    run_fn()

    venue_acc, btn_acc = [], []
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Venue", venue_acc)
    _find(view, lambda c: isinstance(c, ft.ElevatedButton), btn_acc)
    assert len(venue_acc) == 1 and len(btn_acc) == 1
    venue_dd = venue_acc[0]
    assert len(venue_dd.options) >= 1
    venue_dd.value = venue_dd.options[0].key
    btn_acc[0].on_click(None)  # must not raise
