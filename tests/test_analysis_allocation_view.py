"""Integration tests for the Allocation section in ui/modules/analysis_view.py.

Builds a real build_analysis_view() view against a temp DB with a mocked
Flet page and walks the real control tree -- same approach already used
for the Contributors / PnL-ROI Trend tests.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import flet as ft
import pytest

import ui.modules.analysis_view as av
from core.prices.coingecko import CoinGeckoPriceProvider
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import create_db


@pytest.fixture(autouse=True)
def no_network_coingecko(monkeypatch):
    """Relative Strength and Portfolio Pulse each hold their own
    CoinGeckoPriceProvider for live 24h-change — build_analysis_view()'s
    run_fn() always renders both sections, so every test here would
    otherwise make real network calls unrelated to what it's testing."""
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices_with_24h_change", lambda self, assets, fiat: {})
    monkeypatch.setattr(CoinGeckoPriceProvider, "get_prices", lambda self, assets, fiat: {})


class _FakePriceProvider:
    """Minimal price_provider stub -- fixed spot prices, no network."""

    def __init__(self, prices: dict):
        self._prices = prices

    def get_prices(self, assets, fiat):
        return {a: self._prices[a] for a in assets if a in self._prices}


def _find(ctrl, predicate, acc):
    if hasattr(ctrl, "controls"):
        for c in ctrl.controls:
            _find(c, predicate, acc)
    if hasattr(ctrl, "content") and ctrl.content is not None:
        _find(ctrl.content, predicate, acc)
    if predicate(ctrl):
        acc.append(ctrl)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    create_db(db_path)
    return db_path


def _allocation_section(view) -> ft.Column:
    acc = []
    _find(view, lambda c: isinstance(c, ft.Column) and len(c.controls) >= 1
          and isinstance(c.controls[0], ft.Text) and c.controls[0].value == "Allocation", acc)
    return acc[0]


def _row_names_and_pcts(rows_col: ft.Column):
    out = []
    for row in rows_col.controls:
        if isinstance(row, ft.Row):
            out.append((row.controls[0].value, row.controls[-1].value))
    return out


def test_allocation_section_ranks_by_value_descending(db):
    for asset, qty, price in [("BTC", "1", "300"), ("ETH", "1", "500"), ("DOT", "1", "200")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price), venue="kraken",
        ))
    provider = _FakePriceProvider({"BTC": Decimal("300"), "ETH": Decimal("500"), "DOT": Decimal("200")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _allocation_section(view)
    rows_col = section.controls[2].content.controls[0]  # card -> Column -> rows_col
    names = [n for n, _ in _row_names_and_pcts(rows_col)]
    assert names == ["ETH", "BTC", "DOT"]


def test_allocation_excludes_presale_assets(db):
    for asset, qty, price in [("BTC", "1", "100"), ("TICS", "1", "999"), ("SOLX", "1", "999")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price), venue="kraken",
        ))
    provider = _FakePriceProvider({"BTC": Decimal("100"), "TICS": Decimal("999"), "SOLX": Decimal("999")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _allocation_section(view)
    rows_col = section.controls[2].content.controls[0]
    names = [n for n, _ in _row_names_and_pcts(rows_col)]
    assert names == ["BTC"]
    assert names[0] == "BTC" and "100.0" in dict(_row_names_and_pcts(rows_col))["BTC"]


def test_allocation_note_shown_only_when_price_missing(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    # No price_provider -> BTC's live `value` stays None -> excluded, noted.
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=None, fiat="CZK")
    run_fn()

    section = _allocation_section(view)
    note = section.controls[-1]
    assert note.visible is True
    assert "BTC" in note.value
    assert "no live price" in note.value


def test_allocation_no_note_when_all_priced(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
    ))
    provider = _FakePriceProvider({"BTC": Decimal("120")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _allocation_section(view)
    note = section.controls[-1]
    assert note.visible is False


def test_allocation_empty_portfolio_shows_no_data(db):
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=None, fiat="CZK")
    run_fn()

    section = _allocation_section(view)
    rows_col = section.controls[2].content.controls[0]
    assert len(rows_col.controls) == 1
    assert isinstance(rows_col.controls[0], ft.Text)
    assert rows_col.controls[0].value == "No data available."


def test_sell_simulator_still_works_after_allocation_addition(db):
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
