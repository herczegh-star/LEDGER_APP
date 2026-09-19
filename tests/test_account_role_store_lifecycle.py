"""Phase Model B — core/account_role_store.py: Store Initialization Safety.

Mirrors tests/test_reconciliation_store_lifecycle.py's coverage for the
sibling AccountRoleStore.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from core.account_role_store import (
    AccountRole,
    AccountRoleSchemaNotInitializedError,
    AccountRoleStore,
)
from core.ledger_store import LedgerStore

_T0 = datetime(2026, 1, 1)


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def ledger_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = LedgerStore(db_path)
    store.close()
    return db_path


def _role(id_suffix="1"):
    return AccountRole(
        id=f"role-{id_suffix}", venue="revolut", account="Osobní CZK", currency="CZK",
        role="PERSONAL", effective_at=_T0, created_at=_T0,
    )


def test_constructing_store_does_not_change_db_hash(ledger_db):
    before = _sha256(ledger_db)
    store = AccountRoleStore(ledger_db)
    store.close()
    assert _sha256(ledger_db) == before


def test_constructing_store_does_not_create_table(ledger_db):
    store = AccountRoleStore(ledger_db)
    exists = store.schema_exists()
    store.close()
    assert exists is False
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "cash_account_roles" not in tables


def test_read_on_uninitialized_schema_raises_without_creating_table(ledger_db):
    store = AccountRoleStore(ledger_db)
    with pytest.raises(AccountRoleSchemaNotInitializedError):
        store.get_roles("revolut", "Osobní CZK", "CZK")
    still_missing = not store.schema_exists()
    store.close()
    assert still_missing


def test_add_role_on_missing_schema_fails_clearly(ledger_db):
    before = _sha256(ledger_db)
    store = AccountRoleStore(ledger_db)
    with pytest.raises(AccountRoleSchemaNotInitializedError, match="ensure_schema"):
        store.add_role(_role())
    store.close()
    assert _sha256(ledger_db) == before


def test_ensure_schema_creates_expected_table_and_index(ledger_db):
    store = AccountRoleStore(ledger_db)
    store.ensure_schema()
    store.close()
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "cash_account_roles" in tables
    assert "idx_account_role_key" in indexes


def test_ensure_schema_is_idempotent(ledger_db):
    store = AccountRoleStore(ledger_db)
    store.ensure_schema()
    store.add_role(_role("a"))
    store.ensure_schema()
    store.ensure_schema()
    assert store.count() == 1
    store.close()


def test_read_only_connection_ensure_schema_raises(ledger_db):
    store = AccountRoleStore(ledger_db, read_only=True)
    with pytest.raises(RuntimeError, match="read_only"):
        store.ensure_schema()
    store.close()


def test_read_only_connection_cannot_mutate(ledger_db):
    setup = AccountRoleStore(ledger_db)
    setup.ensure_schema()
    setup.close()

    before = _sha256(ledger_db)
    ro_store = AccountRoleStore(ledger_db, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_store.add_role(_role())
    ro_store.close()
    assert _sha256(ledger_db) == before


def test_read_only_connection_reads_initialized_schema(ledger_db):
    setup = AccountRoleStore(ledger_db)
    setup.ensure_schema()
    setup.add_role(_role("a"))
    setup.close()

    ro_store = AccountRoleStore(ledger_db, read_only=True)
    roles = ro_store.get_roles("revolut", "Osobní CZK", "CZK")
    ro_store.close()
    assert len(roles) == 1
    assert roles[0].role == "PERSONAL"
