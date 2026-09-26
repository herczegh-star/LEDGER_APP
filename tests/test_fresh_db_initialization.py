"""Pre-Production Hardening — section 1: fresh database initialization.

create_db() is the ONE conscious setup path — confirms it initializes
cash_account_roles and historical_account_assignments in addition to
cash_reconciliation_snapshots and `ledger`, and that store constructors
themselves still never create schema.
"""
from __future__ import annotations

import sqlite3

import pytest

from core.account_role_store import AccountRoleStore
from core.historical_account_assignment_store import AccountAssignmentStore
from core.reconciliation_store import ReconciliationStore
from core.services.ui_facade import create_db


@pytest.fixture
def fresh_db_path(tmp_path):
    return str(tmp_path / "fresh.db")


def test_create_db_initializes_all_four_schemas(fresh_db_path):
    result = create_db(fresh_db_path)
    assert result.success, result.error_message

    conn = sqlite3.connect(fresh_db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert "ledger" in tables
    assert "cash_reconciliation_snapshots" in tables
    assert "cash_account_roles" in tables
    assert "historical_account_assignments" in tables


def test_create_db_is_idempotent_across_all_schemas(fresh_db_path):
    result1 = create_db(fresh_db_path)
    result2 = create_db(fresh_db_path)
    assert result1.success and result2.success

    role_store = AccountRoleStore(fresh_db_path)
    assign_store = AccountAssignmentStore(fresh_db_path)
    recon_store = ReconciliationStore(fresh_db_path)
    assert role_store.count() == 0
    assert assign_store.count() == 0
    assert recon_store.count() == 0
    role_store.close()
    assign_store.close()
    recon_store.close()


def test_after_create_db_all_stores_are_immediately_usable(fresh_db_path):
    create_db(fresh_db_path)
    # No ensure_schema() call needed by the caller — create_db() already did it.
    role_store = AccountRoleStore(fresh_db_path)
    roles = role_store.get_roles("revolut", "Osobní CZK", "CZK")  # would raise if uninitialized
    role_store.close()
    assert roles == []

    assign_store = AccountAssignmentStore(fresh_db_path)
    assignments = assign_store.get_all_assignments()
    assign_store.close()
    assert assignments == {}


def test_store_constructors_still_do_not_mutate_a_db_created_without_create_db(tmp_path):
    """Opening a store directly (not via create_db()) must still never
    mutate schema — create_db() being the setup path doesn't weaken this."""
    from core.ledger_store import LedgerStore
    db_path = str(tmp_path / "raw.db")
    LedgerStore(db_path).close()  # only `ledger` exists, nothing else

    role_store = AccountRoleStore(db_path)
    exists = role_store.schema_exists()
    role_store.close()
    assert exists is False
