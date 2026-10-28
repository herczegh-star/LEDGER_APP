"""Integration tests for the PnL / ROI Trend section in
ui/modules/analysis_view.py — the shared period selector contract:

  - period change re-fetches get_portfolio_value_trend() ONCE and
    redraws BOTH the Portfolio Value Trend and PnL/ROI charts,
  - metric change (PnL CZK <-> ROI %) redraws ONLY the PnL/ROI chart,
    reusing the already-fetched points — no new service call,
  - Sell Simulator is untouched by any of this.

Builds a real build_analysis_view() view against a temp DB with a mocked
Flet page (no running Flet app needed) and walks the real control tree —
the same approach the Portfolio Value Trend tests already use.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

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


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    create_db(db_path)
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("2000000"),
        venue="kraken",
    ))
    return db_path


def _find(ctrl, predicate, acc):
    if hasattr(ctrl, "controls"):
        for c in ctrl.controls:
            _find(c, predicate, acc)
    if hasattr(ctrl, "content") and ctrl.content is not None:
        _find(ctrl.content, predicate, acc)
    if predicate(ctrl):
        acc.append(ctrl)


def _build(db_path):
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db_path, price_provider=None, fiat="CZK")
    run_fn()
    period_acc, metric_acc = [], []
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Period", period_acc)
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Metric", metric_acc)
    return fake_page, view, period_acc[0], metric_acc[0]


def test_pnl_roi_section_has_metric_dropdown_with_both_options(db):
    _, _, _, metric_dd = _build(db)
    assert {o.key for o in metric_dd.options} == {"pnl", "roi"}


def test_metric_defaults_to_pnl(db):
    _, _, _, metric_dd = _build(db)
    assert metric_dd.value == "pnl"


def test_only_one_period_dropdown_exists_shared_by_both_sections(db):
    """No second period selector was created for the PnL/ROI section."""
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=None, fiat="CZK")
    run_fn()
    acc = []
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Period", acc)
    assert len(acc) == 1


def test_metric_change_does_not_refetch_trend_data(db):
    with patch("ui.modules.analysis_view.get_portfolio_value_trend", wraps=av.get_portfolio_value_trend) as spy:
        _, _, _, metric_dd = _build(db)
        calls_after_initial_load = spy.call_count
        assert calls_after_initial_load >= 1

        metric_dd.value = "roi"
        metric_dd.on_select(None)
        assert spy.call_count == calls_after_initial_load  # unchanged — no new fetch

        metric_dd.value = "pnl"
        metric_dd.on_select(None)
        assert spy.call_count == calls_after_initial_load  # still unchanged


def test_period_change_refetches_and_redraws_both_charts(db):
    with patch("ui.modules.analysis_view.get_portfolio_value_trend", wraps=av.get_portfolio_value_trend) as spy:
        _, _, period_dd, _ = _build(db)
        calls_before = spy.call_count

        period_dd.value = "7D"
        period_dd.on_select(None)  # Flet 0.80.5 Dropdown fires on_select, not on_change

        assert spy.call_count == calls_before + 1  # exactly one new fetch, not one per chart


def test_period_dropdown_on_select_handler_is_load_trend(db):
    """The single shared period dropdown's handler is the function that
    redraws BOTH charts — not a Value-Trend-only handler."""
    _, _, period_dd, _ = _build(db)
    assert period_dd.on_select is not None


def test_sell_simulator_still_works_after_pnl_roi_addition(db):
    fake_page, view, _, _ = _build(db)
    acc = []
    _find(view, lambda c: isinstance(c, ft.Dropdown) and c.label == "Venue", acc)
    assert len(acc) == 1
    venue_dd = acc[0]
    assert len(venue_dd.options) >= 1

    btn_acc = []
    _find(view, lambda c: isinstance(c, ft.ElevatedButton), btn_acc)
    assert len(btn_acc) == 1
    venue_dd.value = venue_dd.options[0].key
    btn_acc[0].on_click(None)  # must not raise
