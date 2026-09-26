"""Phase Model B — core/reports/cash.py functions with the assignments
overlay parameter: default behavior unchanged, overlay changes grouping
when supplied.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.historical_account_assignment_store import AccountAssignment
from core.model import RawRow
from core.reports.cash import compute_cash_balance_as_of, compute_cash_balances

_T0 = datetime(2026, 1, 1)
_AS_OF = datetime(2026, 9, 19)


def _row():
    return RawRow(
        id="buy-1", timestamp=_T0, type="BUY", asset="CZK",
        amount=Decimal("-5000"), currency="CZK", price=Decimal("1"), venue="revolut",
        account=None,
    )


def _assignment_for(row, effective_account):
    fp = row.fingerprint()
    return {fp: [AccountAssignment(
        id="a1", row_fp=fp, trade_id=row.id, venue=row.venue,
        effective_account=effective_account, effective_at=_T0, created_at=_T0,
    )]}


def test_compute_cash_balances_default_behavior_unchanged():
    row = _row()
    balances_without = compute_cash_balances([row])
    balances_with_none = compute_cash_balances([row], assignments=None)
    assert balances_without == balances_with_none
    assert balances_without[("revolut", None, "CZK")] == Decimal("-5000")


def test_compute_cash_balances_applies_overlay():
    row = _row()
    assignments = _assignment_for(row, "Investment Cash CZK")
    balances = compute_cash_balances([row], assignments=assignments, as_of=_AS_OF)
    assert ("revolut", None, "CZK") not in balances
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("-5000")


def test_compute_cash_balance_as_of_default_behavior_unchanged():
    row = _row()
    result_without = compute_cash_balance_as_of([row], "revolut", None, "CZK", _AS_OF)
    result_with_none = compute_cash_balance_as_of([row], "revolut", None, "CZK", _AS_OF, assignments=None)
    assert result_without == result_with_none == Decimal("-5000")


def test_compute_cash_balance_as_of_applies_overlay():
    row = _row()
    assignments = _assignment_for(row, "Investment Cash CZK")
    result = compute_cash_balance_as_of(
        [row], "revolut", "Investment Cash CZK", "CZK", _AS_OF, assignments=assignments,
    )
    assert result == Decimal("-5000")
    # Querying the OLD (unassigned) account key must now find nothing.
    result_old_key = compute_cash_balance_as_of(
        [row], "revolut", None, "CZK", _AS_OF, assignments=assignments,
    )
    assert result_old_key == Decimal("0")
