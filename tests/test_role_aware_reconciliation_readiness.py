"""Phase Model B — core/services/reconciliation_service.py::
cash_reconciliation_readiness(roles=...).

Covers: PERSONAL snapshot ignored by readiness, INVESTMENT_CASH MATCH
required, STALE blocks readiness, missing snapshot blocks readiness, and
that roles=None preserves the exact pre-existing (coarse) behaviour.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.account_role_store import AccountRole
from core.reconciliation_store import ReconciliationStore
from core.services.reconciliation_service import (
    add_reconciliation_snapshot,
    cash_reconciliation_readiness,
)
from core.services.trade_service import AddTradeInput, add_trade

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = ReconciliationStore(db_path)
    try:
        store.ensure_schema()
    finally:
        store.close()
    return db_path


def _seed(db_path, account, ts=_TS_BUY, quote=Decimal("1000")):
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=ts, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=quote, venue="revolut", account=account,
    ))


def _role(role, venue, account, currency="CZK", effective_at=_TS_BUY):
    return AccountRole(
        id=f"r-{venue}-{account}", venue=venue, account=account, currency=currency,
        role=role, effective_at=effective_at, created_at=effective_at,
    )


def test_roles_none_preserves_old_coarse_behavior(db):
    _seed(db, "Osobní CZK")
    result_old = cash_reconciliation_readiness(db)  # no roles param -> old behaviour
    assert result_old.accounts_checked == 1  # coarse: any tracked account counted


def test_personal_role_ignored_by_readiness(db):
    _seed(db, "Osobní CZK")
    now = _TS_BUY + timedelta(days=1)
    roles = [_role("PERSONAL", "revolut", "Osobní CZK")]
    result = cash_reconciliation_readiness(db, now=now, roles=roles)
    assert result.accounts_checked == 0
    assert result.reason == "No INVESTMENT_CASH-role accounts exist yet."


def test_investment_cash_match_required_and_ready(db):
    _seed(db, "Investment Cash CZK")
    now = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Investment Cash CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=now,
    )
    roles = [_role("INVESTMENT_CASH", "revolut", "Investment Cash CZK")]
    result = cash_reconciliation_readiness(db, now=now, roles=roles)
    assert result.ready is True
    assert result.accounts_checked == 1


def test_investment_cash_missing_snapshot_blocks_readiness(db):
    _seed(db, "Investment Cash CZK")
    now = _TS_BUY + timedelta(days=1)
    roles = [_role("INVESTMENT_CASH", "revolut", "Investment Cash CZK")]
    result = cash_reconciliation_readiness(db, now=now, roles=roles)
    assert result.ready is False
    assert ("revolut", "Investment Cash CZK", "CZK") in result.accounts_not_ready


def test_investment_cash_stale_blocks_readiness(db):
    _seed(db, "Investment Cash CZK")
    old_as_of = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Investment Cash CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=old_as_of,
    )
    now = old_as_of + timedelta(days=60)  # beyond 30-day default cadence
    roles = [_role("INVESTMENT_CASH", "revolut", "Investment Cash CZK")]
    result = cash_reconciliation_readiness(db, now=now, roles=roles)
    assert result.ready is False
    assert ("revolut", "Investment Cash CZK", "CZK") in result.accounts_not_ready


def test_mixed_personal_and_investment_cash_only_investment_required(db):
    _seed(db, "Osobní CZK", ts=_TS_BUY)
    _seed(db, "Investment Cash CZK", ts=_TS_BUY + timedelta(hours=1))
    now = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Investment Cash CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=now,
    )
    # No snapshot for Osobní CZK at all — must not matter for readiness.
    roles = [
        _role("PERSONAL", "revolut", "Osobní CZK"),
        _role("INVESTMENT_CASH", "revolut", "Investment Cash CZK"),
    ]
    result = cash_reconciliation_readiness(db, now=now, roles=roles)
    assert result.ready is True
    assert result.accounts_checked == 1
