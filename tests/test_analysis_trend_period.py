"""Tests for ui/modules/analysis_view.py's _trend_period_range() — pure
date-range picking for the Portfolio Value Trend period selector
(7D/30D/90D/1Y/ALL). Not a portfolio calculation, just date arithmetic,
so it's directly unit-testable without a running Flet page.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from core.ledger_store import LedgerStore
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import get_earliest_ledger_date
from ui.modules.analysis_view import _DEFAULT_TREND_PERIOD, _TREND_PERIODS, _trend_period_range


def test_default_period_is_30d():
    assert _DEFAULT_TREND_PERIOD == "30D"


def test_all_five_periods_are_defined():
    assert {k for k, _ in _TREND_PERIODS} == {"7D", "30D", "90D", "1Y", "ALL"}


def test_7d_range_is_seven_days_inclusive():
    today = date.today()
    date_from, date_to = _trend_period_range("7D", earliest_ledger_date=date(2020, 1, 1))
    assert date_to == today
    assert (date_to - date_from).days + 1 == 7


def test_30d_range_is_thirty_days_inclusive():
    today = date.today()
    date_from, date_to = _trend_period_range("30D", earliest_ledger_date=date(2020, 1, 1))
    assert date_to == today
    assert (date_to - date_from).days + 1 == 30


def test_90d_range_is_ninety_days_inclusive():
    date_from, date_to = _trend_period_range("90D", earliest_ledger_date=date(2020, 1, 1))
    assert (date_to - date_from).days + 1 == 90


def test_1y_range_is_365_days_inclusive():
    date_from, date_to = _trend_period_range("1Y", earliest_ledger_date=date(2020, 1, 1))
    assert (date_to - date_from).days + 1 == 365


def test_all_uses_earliest_ledger_date():
    earliest = date(2024, 12, 17)
    date_from, date_to = _trend_period_range("ALL", earliest_ledger_date=earliest)
    assert date_from == earliest
    assert date_to == date.today()


def test_all_falls_back_to_today_when_ledger_empty():
    """An empty ledger (no rows at all) must not crash — ALL degrades to
    a single-day (today-only) range rather than raising or going infinite."""
    today = date.today()
    date_from, date_to = _trend_period_range("ALL", earliest_ledger_date=None)
    assert date_from == today
    assert date_to == today


def test_unknown_period_key_does_not_crash():
    """Defensive: an unrecognized key falls back to the dict.get(None)
    branch, i.e. behaves like ALL with no earliest date rather than
    raising a KeyError."""
    date_from, date_to = _trend_period_range("BOGUS", earliest_ledger_date=date(2024, 1, 1))
    assert date_from == date(2024, 1, 1)
    assert date_to == date.today()


# ── get_earliest_ledger_date() (ui_facade) ───────────────────────────────────

def test_earliest_ledger_date_none_for_empty_ledger(tmp_path):
    db_path = str(tmp_path / "test.db")
    LedgerStore(db_path).close()
    assert get_earliest_ledger_date(db_path) is None


def test_earliest_ledger_date_matches_oldest_row(tmp_path):
    db_path = str(tmp_path / "test.db")
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 3, 1), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=datetime(2025, 1, 15), base_asset="ETH",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("1000"), venue="kraken",
    ))
    assert get_earliest_ledger_date(db_path) == date(2025, 1, 15)
