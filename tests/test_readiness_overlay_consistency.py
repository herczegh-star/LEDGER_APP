"""Pre-Production Hardening — section 7: readiness + overlay consistency.

This test suite exists because the review FOUND a real bug: before this
fix, get_reconciliation_history() (and therefore is_cash_account_reconciled()
and cash_reconciliation_readiness()) computed calculated_balance WITHOUT
passing the assignments overlay through to compute_cash_balance_as_of() /
compute_cash_balances() — so a Cash screen using the overlay and
Reconciliation using no overlay could show two DIFFERENT numbers for what
should be the same account. Fixed by threading `assignments` through the
whole reconciliation call chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.account_role_store import AccountRole
from core.historical_account_assignment_store import AccountAssignment
from core.reports.cash import compute_cash_balance_as_of, compute_cash_balances
from core.services.reconciliation_service import (
    add_reconciliation_snapshot,
    cash_reconciliation_readiness,
    get_reconciliation_history,
)
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import create_db

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    result = create_db(db_path)
    assert result.success, result.error_message
    return db_path


def _seed_legacy_row(db_path):
    """A historical BUY, account=NULL (legacy) — the exact real-world shape
    an overlay would reassign to Investment Cash CZK."""
    result = add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=Decimal("1000"), venue="revolut",
    ))
    czk_row = next(r for r in result.rows if r.asset == "CZK")
    return czk_row.fingerprint()


def _assignments_for(row_fp, effective_account="Investment Cash CZK"):
    return {row_fp: [AccountAssignment(
        id="a1", row_fp=row_fp, trade_id="x", venue="revolut",
        effective_account=effective_account, effective_at=_TS_BUY, created_at=_TS_BUY,
    )]}


def test_same_numbers_with_and_without_overlay_through_both_paths(db):
    """The core consistency proof: whatever compute_cash_balances() (the
    'Cash screen' path) reports for a given overlay must be EXACTLY what
    get_reconciliation_history() reports for the SAME overlay."""
    row_fp = _seed_legacy_row(db)
    assignments = _assignments_for(row_fp)
    as_of = _TS_BUY + timedelta(days=1)

    from core.ledger_store import LedgerStore
    rows = LedgerStore(db).timeline()

    # "Cash screen" path.
    cash_screen_balance = compute_cash_balance_as_of(
        rows, "revolut", "Investment Cash CZK", "CZK", as_of, assignments=assignments,
    )
    assert cash_screen_balance == Decimal("-1000")

    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Investment Cash CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=as_of,
    )

    # "Reconciliation" path — MUST get the identical number when given the SAME overlay.
    history = get_reconciliation_history(
        db, "revolut", "Investment Cash CZK", "CZK", now=as_of, assignments=assignments,
    )
    assert history[0].calculated_balance == cash_screen_balance == Decimal("-1000")
    from core.reconciliation import MATCH
    assert history[0].status == MATCH


def test_without_overlay_reconciliation_would_have_been_wrong(db):
    """Demonstrates the BUG this fix prevents: omitting the overlay on the
    reconciliation side (while the real balance depends on it) produces a
    calculated_balance of 0 (nothing tagged to Investment Cash CZK without
    the overlay) instead of the true -1000 -- a false DIFFERENCE, not a
    false MATCH, so the bug fails safe, but it's still wrong."""
    row_fp = _seed_legacy_row(db)
    assignments = _assignments_for(row_fp)
    as_of = _TS_BUY + timedelta(days=1)

    history_without_overlay = get_reconciliation_history(
        db, "revolut", "Investment Cash CZK", "CZK", now=as_of, assignments=None,
    )
    # No snapshot yet -> empty history either way; prove the underlying
    # calculated balance itself differs by overlay presence directly:
    from core.ledger_store import LedgerStore
    rows = LedgerStore(db).timeline()
    balance_without_overlay = compute_cash_balance_as_of(
        rows, "revolut", "Investment Cash CZK", "CZK", as_of, assignments=None,
    )
    balance_with_overlay = compute_cash_balance_as_of(
        rows, "revolut", "Investment Cash CZK", "CZK", as_of, assignments=assignments,
    )
    assert balance_without_overlay == Decimal("0")
    assert balance_with_overlay == Decimal("-1000")
    assert balance_without_overlay != balance_with_overlay
    assert history_without_overlay == []


def test_readiness_consistent_with_overlay(db):
    row_fp = _seed_legacy_row(db)
    assignments = _assignments_for(row_fp)
    now = _TS_BUY + timedelta(days=1)

    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Investment Cash CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=now,
    )
    roles = [AccountRole(
        id="r1", venue="revolut", account="Investment Cash CZK", currency="CZK",
        role="INVESTMENT_CASH", effective_at=_TS_BUY, created_at=_TS_BUY,
    )]

    # Without the overlay, readiness wouldn't even SEE "Investment Cash CZK"
    # as a tracked key (the row is still account=NULL from compute_cash_balances()'s
    # point of view) -> "no accounts" rather than a false MATCH.
    result_without_overlay = cash_reconciliation_readiness(db, now=now, roles=roles, assignments=None)
    assert result_without_overlay.accounts_checked == 0

    # With the SAME overlay used consistently, readiness correctly finds
    # the account and its MATCH snapshot.
    result_with_overlay = cash_reconciliation_readiness(db, now=now, roles=roles, assignments=assignments)
    assert result_with_overlay.ready is True
    assert result_with_overlay.accounts_checked == 1
