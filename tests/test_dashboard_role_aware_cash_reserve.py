"""Bugfix — Dashboard Cash Reserve must be role-aware.

Root cause: ui/app_flet.py's update_cash_reserve() called
get_cash_accounts_view() (role-UNAWARE: any account IS NOT NULL), so a
PERSONAL-role account (e.g. Revolut / Osobní CZK, carrying only a few
tagged correction cash legs) appeared in the Dashboard's "CASH RESERVE"
chips alongside genuine INVESTMENT_CASH accounts.

Fix: Dashboard now calls get_investment_cash_accounts_view() /
get_investment_cash_reserve() (both role-aware, INVESTMENT_CASH only).
get_cash_accounts_view() itself is UNCHANGED and remains the (correct,
intentional) data source for the general Cash Accounts screen
(ui/modules/cash_view.py), which must keep showing PERSONAL accounts too.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.account_role_service import add_account_role
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import (
    create_db,
    get_cash_accounts_view,
    get_investment_cash_accounts_view,
    get_investment_cash_reserve,
)

_T0 = datetime(2026, 1, 1)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    result = create_db(db_path)
    assert result.success, result.error_message
    return db_path


def _seed_scenario(db_path):
    """Revolut / Osobní CZK = PERSONAL, cash +253 (mirrors the real
    production correction cash legs). Trinity / Investment Returns =
    INVESTMENT_CASH, cash +1327.40 (mirrors the real production
    PORTFOLIO_CONTRIBUTION). Raw CZK-only rows inserted directly — a plain
    BUY/SELL pair isn't needed here; compute_cash_balances() sums fiat rows
    type-agnostically regardless of pairing, exactly like a real
    CORRECTION/PORTFOLIO_CONTRIBUTION cash leg."""
    store = LedgerStore(db_path)
    store.insert(RawRow(
        id="seed-personal", timestamp=_T0, type="TRANSFER", asset="CZK",
        amount=Decimal("253"), currency="CZK", price=Decimal("1"),
        venue="revolut", account="Osobní CZK",
    ))
    store.insert(RawRow(
        id="seed-investment", timestamp=_T0 + timedelta(hours=1), type="TRANSFER", asset="CZK",
        amount=Decimal("1327.40"), currency="CZK", price=Decimal("1"),
        venue="trinity_bank", account="Investment Returns",
    ))
    store.close()
    add_account_role(db_path, "revolut", "Osobní CZK", "CZK", "PERSONAL", _T0)
    add_account_role(db_path, "trinity_bank", "Investment Returns", "CZK", "INVESTMENT_CASH", _T0)


def test_dashboard_view_excludes_personal_includes_investment_cash(db):
    _seed_scenario(db)
    rows = get_investment_cash_accounts_view(db)
    labels = [(r.venue, r.account) for r in rows]

    assert ("trinity_bank", "Investment Returns") in labels
    assert ("revolut", "Osobní CZK") not in labels
    assert len(rows) == 1
    assert rows[0].balance == Decimal("1327.40")


def test_dashboard_reserve_total_matches_only_investment_cash(db):
    _seed_scenario(db)
    reserve = get_investment_cash_reserve(db)
    assert reserve == {"CZK": Decimal("1327.40")}


def test_general_cash_accounts_screen_still_shows_personal_account(db):
    """The dedicated Cash Accounts screen (ui/modules/cash_view.py) must NOT
    lose visibility of the PERSONAL account — get_cash_accounts_view()
    itself is deliberately unchanged."""
    _seed_scenario(db)
    rows = get_cash_accounts_view(db)
    labels = [(r.venue, r.account) for r in rows]

    assert ("revolut", "Osobní CZK") in labels
    assert ("trinity_bank", "Investment Returns") in labels
    assert len(rows) == 2


def test_no_role_declared_excluded_from_dashboard_view(db):
    """An account with NO role row at all must not leak into the Dashboard
    Cash Reserve either (fail-closed, same as compute_investment_cash_reserve())."""
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=_T0, base_asset="ARB", base_amount=Decimal("1"),
        quote_currency="CZK", quote_amount=Decimal("500"), venue="kraken",
        account="Untagged Account",
    ))
    rows = get_investment_cash_accounts_view(db)
    assert ("kraken", "Untagged Account") not in [(r.venue, r.account) for r in rows]


def test_account_null_legacy_unassigned_excluded_from_dashboard_view(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=_T0, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
        # no account -> legacy/unassigned
    ))
    rows = get_investment_cash_accounts_view(db)
    assert all(r.account is not None for r in rows)


def test_dashboard_view_gracefully_handles_missing_role_schema(tmp_path):
    """A DB that only has `ledger` (no cash_account_roles table at all —
    e.g. an older DB that never ran create_db()'s updated setup) must not
    crash the Dashboard — it should simply show nothing, not raise."""
    from core.ledger_store import LedgerStore
    db_path = str(tmp_path / "no_role_schema.db")
    LedgerStore(db_path).close()
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=_T0, base_asset="ARB", base_amount=Decimal("1"),
        quote_currency="CZK", quote_amount=Decimal("100"), venue="revolut",
        account="Osobní CZK",
    ))
    rows = get_investment_cash_accounts_view(db_path)  # must not raise
    assert rows == []
    reserve = get_investment_cash_reserve(db_path)  # must not raise
    assert reserve == {}
