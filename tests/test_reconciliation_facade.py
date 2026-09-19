"""Phase Cash Reconciliation — ui_facade.py wrapper layer.

Confirms the UI-facing wrapper never raises (errors captured in
SimpleResultDTO.error_message, matching every other write path in this
facade) and that the read-only wrappers delegate correctly.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import (
    add_reconciliation_snapshot,
    create_db,
    get_cash_reconciliation_readiness,
    get_reconciliation_history,
)

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)


@pytest.fixture
def db(tmp_path):
    """Uses the real app setup path (create_db()) — exercises the actual
    production wiring that initializes both the ledger and reconciliation
    schemas explicitly, once, up front."""
    db_path = str(tmp_path / "test.db")
    result = create_db(db_path)
    assert result.success, result.error_message
    return db_path


@pytest.fixture
def db_no_reconciliation_schema(tmp_path):
    """A DB with the `ledger` table but WITHOUT reconciliation schema —
    simulates a pre-fix DB or one whose ensure_schema() was never called."""
    return str(tmp_path / "test.db")


def _seed(db_path):
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=Decimal("1000"), venue="revolut",
        account="Osobní CZK",
    ))


def test_add_reconciliation_snapshot_success(db):
    _seed(db)
    result = add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=_TS_BUY + timedelta(days=1),
    )
    assert result.success
    assert result.error_message is None


def test_add_reconciliation_snapshot_never_raises_on_invalid_input(db):
    """Mirrors create_db()'s never-raises contract — errors are captured,
    not propagated as exceptions, for the UI-facing wrapper."""
    result = add_reconciliation_snapshot(
        db_path=db, venue="", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("100"), as_of=_TS_BUY,
    )
    assert not result.success
    assert "venue" in (result.error_message or "")


def test_add_reconciliation_snapshot_unsupported_currency_captured(db):
    result = add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="XYZ",
        reported_balance=Decimal("100"), as_of=_TS_BUY,
    )
    assert not result.success
    assert "tolerance" in (result.error_message or "")


def test_get_reconciliation_history_delegates(db):
    _seed(db)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=_TS_BUY + timedelta(days=1),
    )
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK")
    assert len(history) == 1
    assert history[0].reported_balance == Decimal("-1000")


def test_get_cash_reconciliation_readiness_delegates(db):
    _seed(db)
    result = get_cash_reconciliation_readiness(db)
    assert result.ready is False
    assert result.accounts_checked == 1


def test_facade_write_on_uninitialized_schema_captured_not_raised(db_no_reconciliation_schema):
    """Store Initialization Safety: a write against a DB whose reconciliation
    schema was never initialized must be captured as a clear error by the
    UI-facing wrapper, not raise, and must NOT silently create the schema."""
    result = add_reconciliation_snapshot(
        db_path=db_no_reconciliation_schema, venue="revolut", account="Osobní CZK",
        currency="CZK", reported_balance=Decimal("100"), as_of=_TS_BUY,
    )
    assert not result.success
    assert "does not exist" in (result.error_message or "")
