"""Phase 2 (+ Phase 2.5 TRACKED/LEGACY split) — get_cash_accounts_view() /
get_cash_account_movements() / get_known_account_labels() /
get_portfolio_cash_reserve() / get_legacy_unassigned_fiat_flows().

Verifies (spec section 4, 10, 12; Phase 2.5 correction):
  - Cash view groups by (venue, account, currency).
  - 'external' never appears as a row in either section.
  - get_cash_accounts_view() returns ONLY TRACKED accounts (account IS NOT
    NULL) — account=None historical fiat flow is NOT a current balance and
    must not appear here (Phase 2.5 fix: previously it did, which made
    untracked historical BUY outflows look like real negative cash debts).
  - get_legacy_unassigned_fiat_flows() returns exactly the excluded
    account=None data, unlabelled (account stays None; "Unassigned Cash" is
    a UI-layer-only label, tested elsewhere).
  - get_cash_account_movements() returns the right ledger rows for one account.
  - get_known_account_labels() excludes 'external' and the None bucket.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.ui_facade import (
    AddTradeRequestDTO,
    add_trade,
    get_cash_account_movements,
    get_cash_accounts_view,
    get_known_account_labels,
    get_legacy_unassigned_fiat_flows,
    get_portfolio_cash_reserve,
)

_TS = datetime(2026, 9, 10, 9, 31, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_cash_accounts_view_groups_by_venue_account_currency(db):
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
        account="Osobní CZK",
    ), db)
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS, asset="BTC", amount=Decimal("0.01"),
        currency="CZK", price=Decimal("2500000"), venue="revolut",
        account="Investment CZK",
    ), db)

    view = get_cash_accounts_view(db)
    keyed = {(r.venue, r.account, r.currency): r.balance for r in view}
    assert ("revolut", "Osobní CZK", "CZK") in keyed
    assert ("revolut", "Investment CZK", "CZK") in keyed
    assert keyed[("revolut", "Osobní CZK", "CZK")] == Decimal("3344.25")


def test_external_never_appears_in_cash_accounts_view(db):
    add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("20000"),
        currency="CZK", price=Decimal("0"), venue="external", to_venue="revolut",
        to_account="Osobní CZK",
    ), db)

    view = get_cash_accounts_view(db)
    assert all(r.venue != "external" for r in view)
    # But the portfolio side of the transfer IS a real account row.
    assert any(r.venue == "revolut" and r.account == "Osobní CZK" for r in view)


def test_legacy_account_none_row_excluded_from_tracked_view(db):
    """Phase 2.5: account=None historical fiat flow must NOT appear in
    get_cash_accounts_view() — that view is TRACKED-only now."""
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
    ), db)  # no account provided

    view = get_cash_accounts_view(db)
    assert not any(r.venue == "revolut" for r in view)


def test_legacy_account_none_row_appears_in_legacy_section_unlabelled(db):
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
    ), db)  # no account provided

    legacy = get_legacy_unassigned_fiat_flows(db)
    row = next(r for r in legacy if r.venue == "revolut")
    assert row.account is None  # never "Unassigned Cash" at this layer


def test_negative_legacy_flow_excluded_from_tracked_reserve(db):
    """The exact scenario reported from the visual review: a venue with
    only untracked historical BUY outflows must NOT show up as a negative
    'cash reserve' — it is excluded entirely, not just hidden if positive."""
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS, asset="BTC", amount=Decimal("1"),
        currency="CZK", price=Decimal("2000000"), venue="anycoin",
    ), db)  # no account -> large negative CZK leg, account=None

    view = get_cash_accounts_view(db)
    assert not any(r.venue == "anycoin" for r in view)

    reserve = get_portfolio_cash_reserve(db)
    assert "CZK" not in reserve or reserve.get("CZK", Decimal("0")) == Decimal("0")


def test_positive_legacy_flow_also_excluded_from_tracked_reserve(db):
    """A positive account=None balance is just as untrustworthy as a
    negative one — both are excluded, not just the alarming-looking ones."""
    add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("10000"),
        currency="CZK", price=Decimal("0"), venue="external", to_venue="kraken",
    ), db)  # to_account not provided -> destination leg account=None

    view = get_cash_accounts_view(db)
    assert not any(r.venue == "kraken" for r in view)
    reserve = get_portfolio_cash_reserve(db)
    assert reserve.get("CZK", Decimal("0")) == Decimal("0")


def test_explicit_account_positive_balance_is_in_tracked_reserve(db):
    add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("20000"),
        currency="CZK", price=Decimal("0"), venue="external", to_venue="revolut",
        to_account="Osobní CZK",
    ), db)

    reserve = get_portfolio_cash_reserve(db)
    assert reserve["CZK"] == Decimal("20000")
    view = get_cash_accounts_view(db)
    assert any(r.venue == "revolut" and r.account == "Osobní CZK" for r in view)


def test_explicit_account_negative_balance_is_preserved_as_real(db):
    """A TRACKED account CAN legitimately be negative (overdrawn) — that is
    real information and must NOT be filtered out. Only account=None is
    excluded, never based on the sign of the balance."""
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS, asset="BTC", amount=Decimal("1"),
        currency="CZK", price=Decimal("2000000"), venue="revolut",
        account="Osobní CZK",
    ), db)

    reserve = get_portfolio_cash_reserve(db)
    assert reserve["CZK"] == Decimal("-2000000")
    view = get_cash_accounts_view(db)
    row = next(r for r in view if r.account == "Osobní CZK")
    assert row.balance == Decimal("-2000000")


def test_get_cash_account_movements_filters_correctly(db):
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
        fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS, asset="BTC", amount=Decimal("0.01"),
        currency="CZK", price=Decimal("2500000"), venue="revolut",
        account="Investment CZK",
    ), db)

    movements = get_cash_account_movements(db, "revolut", "Osobní CZK", "CZK")
    assert all(r.venue == "revolut" and r.account == "Osobní CZK" for r in movements)
    assert {r.type for r in movements} == {"SELL", "FEE"}


def test_get_known_account_labels_excludes_external_and_none(db):
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
        account="Osobní CZK",
    ), db)
    add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("20000"),
        currency="CZK", price=Decimal("0"), venue="external", to_venue="revolut",
        to_account="Investment CZK",
    ), db)
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS, asset="ETH", amount=Decimal("1"),
        currency="CZK", price=Decimal("60000"), venue="kraken",
    ), db)  # no account -> None, must not appear as a label

    labels = get_known_account_labels(db)
    assert labels == ["Investment CZK", "Osobní CZK"]


def test_portfolio_cash_reserve_excludes_external(db):
    add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("20000"),
        currency="CZK", price=Decimal("0"), venue="external", to_venue="revolut",
        to_account="Osobní CZK",
    ), db)

    reserve = get_portfolio_cash_reserve(db)
    assert reserve["CZK"] == Decimal("20000")
