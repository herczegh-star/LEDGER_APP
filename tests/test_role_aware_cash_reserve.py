"""Phase Model B — core/reports/cash.py::compute_investment_cash_reserve().

Covers: PERSONAL excluded, INVESTMENT_CASH included, no role excluded,
account IS NULL (Legacy Unassigned) excluded.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.account_role_store import AccountRole
from core.model import RawRow
from core.reports.cash import compute_investment_cash_reserve, compute_tracked_cash_reserve

_T0 = datetime(2026, 1, 1)
_AS_OF = datetime(2026, 9, 19)


def _role(role, venue, account, currency="CZK"):
    return AccountRole(
        id=f"r-{venue}-{account}", venue=venue, account=account, currency=currency,
        role=role, effective_at=_T0, created_at=_T0,
    )


def _cash_row(venue, account, amount):
    return RawRow(
        id=f"row-{venue}-{account}-{amount}", timestamp=_T0, type="BUY", asset="CZK",
        amount=amount, currency="CZK", price=Decimal("1"), venue=venue, account=account,
    )


def test_personal_role_excluded():
    rows = [_cash_row("revolut", "Osobní CZK", Decimal("3563.66"))]
    roles = [_role("PERSONAL", "revolut", "Osobní CZK")]
    reserve = compute_investment_cash_reserve(rows, roles, as_of=_AS_OF)
    assert reserve == {}


def test_investment_cash_role_included():
    rows = [_cash_row("air_bank", "Investment Cash CZK", Decimal("50000"))]
    roles = [_role("INVESTMENT_CASH", "air_bank", "Investment Cash CZK")]
    reserve = compute_investment_cash_reserve(rows, roles, as_of=_AS_OF)
    assert reserve == {"CZK": Decimal("50000")}


def test_no_role_declared_excluded():
    rows = [_cash_row("revolut", "Some Untagged Account", Decimal("1000"))]
    reserve = compute_investment_cash_reserve(rows, roles=[], as_of=_AS_OF)
    assert reserve == {}


def test_account_null_legacy_unassigned_excluded():
    rows = [_cash_row("revolut", None, Decimal("-154681.84"))]
    roles = [_role("INVESTMENT_CASH", "revolut", "Osobní CZK")]  # irrelevant, different account
    reserve = compute_investment_cash_reserve(rows, roles, as_of=_AS_OF)
    assert reserve == {}


def test_mixed_accounts_only_investment_cash_counted():
    rows = [
        _cash_row("revolut", "Osobní CZK", Decimal("3563.66")),
        _cash_row("air_bank", "Investment Cash CZK", Decimal("50000")),
        _cash_row("revolut", None, Decimal("-154681.84")),
    ]
    roles = [
        _role("PERSONAL", "revolut", "Osobní CZK"),
        _role("INVESTMENT_CASH", "air_bank", "Investment Cash CZK"),
    ]
    reserve = compute_investment_cash_reserve(rows, roles, as_of=_AS_OF)
    assert reserve == {"CZK": Decimal("50000")}


def test_investment_cash_reserve_is_narrower_than_tracked_reserve():
    """compute_tracked_cash_reserve() (coarse: any non-None account) must
    NOT be conflated with compute_investment_cash_reserve() (role-aware) —
    the whole point of Model B."""
    rows = [_cash_row("revolut", "Osobní CZK", Decimal("3563.66"))]
    roles = [_role("PERSONAL", "revolut", "Osobní CZK")]
    tracked = compute_tracked_cash_reserve(rows)
    investment = compute_investment_cash_reserve(rows, roles, as_of=_AS_OF)
    assert tracked == {"CZK": Decimal("3563.66")}   # old, coarse behaviour unchanged
    assert investment == {}                          # new, role-aware behaviour
