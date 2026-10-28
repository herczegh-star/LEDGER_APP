"""Integration tests for the Relative Strength section in
ui/modules/analysis_view.py.

Builds a real build_analysis_view() view against a temp DB with a mocked
Flet page and walks the real control tree -- same approach already used
for Contributors / Allocation.

The section's internal CoinGecko-only provider (used ONLY for the live
24h-change capability) is monkeypatched to avoid real network calls in
tests -- the 24h-change parsing itself is already covered against a fake
provider in tests/test_relative_strength_service.py. These tests focus
on wiring: scope, alias display, note, sort, Top10/Bottom10 collapse +
toggle, and Sell Simulator survival.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import flet as ft
import pytest

import ui.modules.analysis_view as av
from core.historical_price_store import HistoricalPrice, HistoricalPriceStore
from core.prices.coingecko import CoinGeckoPriceProvider
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import create_db

_TODAY = date.today()
_D7 = _TODAY - timedelta(days=7)


class _FakeSpotProvider:
    """Stand-in for the generic price_provider passed to
    build_analysis_view() -- feeds PositionDTO.spot_price via
    get_dashboard_snapshot(), independent of the section's own internal
    CoinGecko-only 24h-change provider."""

    def __init__(self, prices: dict):
        self._prices = prices

    def get_prices(self, assets, fiat):
        return {a: self._prices[a] for a in assets if a in self._prices}


@pytest.fixture(autouse=True)
def no_network_coingecko(monkeypatch):
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


def _rs_section(view) -> ft.Column:
    acc = []
    _find(view, lambda c: isinstance(c, ft.Column) and len(c.controls) >= 1
          and isinstance(c.controls[0], ft.Text) and c.controls[0].value == "Relative Strength", acc)
    return acc[0]


def _tables(section) -> list:
    acc = []
    _find(section, lambda c: isinstance(c, ft.DataTable), acc)
    return acc


def _visible_asset_names(section) -> list:
    """Concatenated asset names across however many DataTables the
    section currently renders (one when <=20 rows or expanded, two --
    Top/Bottom -- when collapsed with >20 rows)."""
    names = []
    for table in _tables(section):
        names.extend(row.cells[0].content.value for row in table.rows)
    return names


def _toggle_button(section):
    acc = []
    _find(section, lambda c: isinstance(c, ft.TextButton), acc)
    return acc[0] if acc else None


def _seed_n_assets(db_path, n: int):
    """n distinct assets, each with a distinct 7D return (asset i's spot
    is 100 + i, its 7D-ago price is always 100 -> strictly increasing
    returns, so the resulting sort order is fully deterministic and
    every asset is trivially distinguishable)."""
    spot = {}
    for i in range(n):
        asset = f"A{i:02d}"
        add_trade(db_path, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("100"), venue="kraken",
        ))
        _seed(db_path, asset, _D7, "100")
        spot[asset] = Decimal(100 + i)
    return _FakeSpotProvider(spot)


def test_table_renders_scoped_assets_only(db):
    for asset, qty, price in [("ATOM", "10", "120"), ("USDC", "100", "1"), ("TICS", "10", "1")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price) * Decimal(qty), venue="kraken",
        ))
    _seed(db, "ATOM", _D7, "100")
    provider = _FakeSpotProvider({"ATOM": Decimal("120"), "USDC": Decimal("1"), "TICS": Decimal("1")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    assert _visible_asset_names(section) == ["ATOM"]  # USDC/TICS excluded from scope


def test_ton_alias_displayed_as_gram_in_asset_column(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="TON",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    _seed(db, "TON", _D7, "90")
    provider = _FakeSpotProvider({"TON": Decimal("100")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    assert _visible_asset_names(section) == ["GRAM (TON)"]


def test_note_shown_for_scoped_asset_missing_historical_data(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 12, 1), base_asset="ATOM",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    # Deliberately never seed ATOM's historical price.
    provider = _FakeSpotProvider({"ATOM": Decimal("120")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    note = section.controls[-1]
    assert note.visible is True
    assert "ATOM" in note.value
    assert "Missing historical data" in note.value


def test_no_note_when_scope_exclusions_are_the_only_gap(db):
    """Stablecoins/presale/BTC are scope exclusions, not "missing data" --
    they must never trigger the disclosure note by themselves."""
    for asset, qty, price in [("USDC", "100", "1"), ("TICS", "10", "1")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price) * Decimal(qty), venue="kraken",
        ))
    provider = _FakeSpotProvider({"USDC": Decimal("1"), "TICS": Decimal("1")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    note = section.controls[-1]
    assert note.visible is False


def test_empty_portfolio_shows_no_data(db):
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=None, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    assert _tables(section) == []
    texts = []
    _find(section.controls[2], lambda c: isinstance(c, ft.Text), texts)
    assert any(t.value == "No data available." for t in texts)


def test_default_sort_is_7d_descending(db):
    for asset, qty, price in [("AAA", "1", "120"), ("BBB", "1", "150")]:
        add_trade(db, AddTradeInput(
            type="BUY", timestamp=datetime(2025, 12, 1), base_asset=asset,
            base_amount=Decimal(qty), quote_currency="CZK", quote_amount=Decimal(price), venue="kraken",
        ))
    _seed(db, "AAA", _D7, "100")  # +20%
    _seed(db, "BBB", _D7, "100")  # +50%
    provider = _FakeSpotProvider({"AAA": Decimal("120"), "BBB": Decimal("150")})
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    assert _visible_asset_names(section) == ["BBB", "AAA"]


# ── Top 10 / Bottom 10 collapse + toggle ────────────────────────────────────

def test_29_rows_shows_20_by_default(db):
    provider = _seed_n_assets(db, 29)
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    names = _visible_asset_names(section)
    assert len(names) == 20
    # Highest 7D returns (A28..A19) first, lowest (A09..A00) last -- two
    # separate tables (Top 10 / Bottom 10), toggle button sits between them.
    assert names[:10] == [f"A{i:02d}" for i in range(28, 18, -1)]
    assert names[10:] == [f"A{i:02d}" for i in range(9, -1, -1)]
    btn = _toggle_button(section)
    assert btn is not None
    assert btn.text == "Show all 29"


def test_show_all_expands_to_29(db):
    provider = _seed_n_assets(db, 29)
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    btn = _toggle_button(section)
    btn.on_click(None)

    names = _visible_asset_names(section)
    assert len(names) == 29
    assert names == [f"A{i:02d}" for i in range(28, -1, -1)]  # fully 7D-descending
    btn2 = _toggle_button(section)
    assert btn2.text == "Show less"


def test_show_less_collapses_back_to_20(db):
    provider = _seed_n_assets(db, 29)
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    btn = _toggle_button(section)
    btn.on_click(None)  # expand
    btn.on_click(None)  # collapse back

    names = _visible_asset_names(section)
    assert len(names) == 20
    btn2 = _toggle_button(section)
    assert btn2.text == "Show all 29"


def test_20_rows_or_fewer_shows_all_with_no_toggle(db):
    provider = _seed_n_assets(db, 20)
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    assert len(_visible_asset_names(section)) == 20
    assert _toggle_button(section) is None


def test_no_duplicate_assets_between_top_and_bottom(db):
    provider = _seed_n_assets(db, 29)
    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()

    section = _rs_section(view)
    names = _visible_asset_names(section)
    assert len(names) == len(set(names))


def test_no_refetch_on_toggle(db, monkeypatch):
    provider = _seed_n_assets(db, 29)
    calls = {"n": 0}
    real = av.compute_relative_strength

    def _spy(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(av, "compute_relative_strength", _spy)

    fake_page = MagicMock()
    view, run_fn = av.build_analysis_view(fake_page, db, price_provider=provider, fiat="CZK")
    run_fn()
    assert calls["n"] == 1

    section = _rs_section(view)
    btn = _toggle_button(section)
    btn.on_click(None)  # expand
    _toggle_button(section).on_click(None)  # collapse
    assert calls["n"] == 1  # still just the one fetch from run_fn()


def test_sell_simulator_still_works_after_relative_strength_addition(db):
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
