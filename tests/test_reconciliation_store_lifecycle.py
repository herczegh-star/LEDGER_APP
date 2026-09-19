"""Store Initialization Safety — core/reconciliation_store.py.

The bug this fixes: opening ReconciliationStore(production_db) for a
read-only pre-write check silently ran CREATE TABLE IF NOT EXISTS +
CREATE INDEX (same class of implicit-write-on-open as LedgerStore, which
this codebase already works around by never opening it against production
for read-only inspection — see the real production incident this test
suite guards against).

Fixed contract:
  - __init__ (any mode) NEVER mutates the DB — no CREATE TABLE/INDEX.
  - ensure_schema() is the ONLY thing that creates schema — explicit,
    idempotent, and only meant to be called from a conscious setup path
    (core.services.ui_facade.create_db()).
  - Every read/write method raises ReconciliationSchemaNotInitializedError
    if the table doesn't exist yet, instead of creating it implicitly.
  - A read_only=True connection (sqlite mode=ro) can never write, even via
    ensure_schema() (which raises RuntimeError on it).
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.ledger_store import LedgerStore
from core.reconciliation_store import (
    ReconciliationSchemaNotInitializedError,
    ReconciliationSnapshot,
    ReconciliationStore,
)

_TS = datetime(2026, 9, 19, 10, 31, 0)


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def ledger_db(tmp_path):
    """A DB with the `ledger` table (via LedgerStore) but NO reconciliation
    schema — the realistic "not yet initialized for reconciliation" shape."""
    db_path = str(tmp_path / "test.db")
    store = LedgerStore(db_path)
    store.close()
    return db_path


def _snapshot(id_suffix="1") -> ReconciliationSnapshot:
    return ReconciliationSnapshot(
        id=f"snap-{id_suffix}", venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("100"), as_of=_TS, created_at=_TS,
    )


# ── constructing the store does not change DB hash/schema ──────────────────

def test_constructing_store_does_not_change_db_hash(ledger_db):
    before = _sha256(ledger_db)
    store = ReconciliationStore(ledger_db)
    store.close()
    after = _sha256(ledger_db)
    assert after == before


def test_constructing_store_does_not_create_table(ledger_db):
    store = ReconciliationStore(ledger_db)
    exists = store.schema_exists()
    store.close()
    assert exists is False

    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "cash_reconciliation_snapshots" not in tables


# ── read on uninitialized schema does not create table, raises clearly ────

def test_read_on_uninitialized_schema_raises_clearly_without_creating_table(ledger_db):
    store = ReconciliationStore(ledger_db)
    with pytest.raises(ReconciliationSchemaNotInitializedError, match="does not exist"):
        store.get_snapshots("revolut", "Osobní CZK", "CZK")
    still_missing = not store.schema_exists()
    store.close()
    assert still_missing


def test_count_on_uninitialized_schema_raises(ledger_db):
    store = ReconciliationStore(ledger_db)
    with pytest.raises(ReconciliationSchemaNotInitializedError):
        store.count()
    store.close()


# ── add_snapshot on missing schema fails clearly, does not auto-create ────

def test_add_snapshot_on_missing_schema_fails_clearly(ledger_db):
    before = _sha256(ledger_db)
    store = ReconciliationStore(ledger_db)
    with pytest.raises(ReconciliationSchemaNotInitializedError, match="ensure_schema"):
        store.add_snapshot(_snapshot())
    store.close()
    after = _sha256(ledger_db)
    assert after == before  # no partial/implicit write happened


# ── explicit ensure_schema creates exactly the expected table/index ───────

def test_ensure_schema_creates_expected_table_and_index(ledger_db):
    store = ReconciliationStore(ledger_db)
    store.ensure_schema()
    store.close()

    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cash_reconciliation_snapshots)")}
    conn.close()

    assert "cash_reconciliation_snapshots" in tables
    assert "idx_reconciliation_account" in indexes
    assert cols == {
        "pk", "id", "venue", "account", "currency",
        "reported_balance", "as_of", "created_at", "note",
    }


def test_ensure_schema_is_idempotent(ledger_db):
    store = ReconciliationStore(ledger_db)
    store.ensure_schema()
    store.add_snapshot(_snapshot("a"))
    store.ensure_schema()  # second call must not wipe/alter existing data
    store.ensure_schema()  # third call, same
    assert store.count() == 1
    store.close()


# ── read history on initialized schema works ───────────────────────────────

def test_read_history_on_initialized_schema_works(ledger_db):
    store = ReconciliationStore(ledger_db)
    store.ensure_schema()
    store.add_snapshot(_snapshot("a"))
    snapshots = store.get_snapshots("revolut", "Osobní CZK", "CZK")
    store.close()
    assert len(snapshots) == 1
    assert snapshots[0].reported_balance == Decimal("100")


# ── mode=ro cannot mutate DB ─────────────────────────────────────────────

def test_read_only_connection_ensure_schema_raises(ledger_db):
    store = ReconciliationStore(ledger_db, read_only=True)
    with pytest.raises(RuntimeError, match="read_only"):
        store.ensure_schema()
    store.close()


def test_read_only_connection_add_snapshot_fails_without_mutating(ledger_db):
    # Initialize schema first via a normal (write-capable) connection.
    setup = ReconciliationStore(ledger_db)
    setup.ensure_schema()
    setup.close()

    before = _sha256(ledger_db)
    ro_store = ReconciliationStore(ledger_db, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_store.add_snapshot(_snapshot())
    ro_store.close()
    after = _sha256(ledger_db)
    assert after == before


def test_read_only_connection_can_read_initialized_schema(ledger_db):
    setup = ReconciliationStore(ledger_db)
    setup.ensure_schema()
    setup.add_snapshot(_snapshot("a"))
    setup.close()

    ro_store = ReconciliationStore(ledger_db, read_only=True)
    snapshots = ro_store.get_snapshots("revolut", "Osobní CZK", "CZK")
    ro_store.close()
    assert len(snapshots) == 1


def test_read_only_connection_on_uninitialized_schema_raises_not_creates(ledger_db):
    before = _sha256(ledger_db)
    ro_store = ReconciliationStore(ledger_db, read_only=True)
    with pytest.raises(ReconciliationSchemaNotInitializedError):
        ro_store.get_snapshots("revolut", "Osobní CZK", "CZK")
    ro_store.close()
    after = _sha256(ledger_db)
    assert after == before


# ── existing production-style initialized DB remains compatible ───────────

def test_existing_initialized_db_remains_compatible(ledger_db):
    """Simulates the real production DB shape after the earlier (buggy)
    implicit CREATE happened — schema exists, possibly with data — and
    confirms the fixed store still reads/writes it correctly, with no
    re-migration needed."""
    legacy_style_store = ReconciliationStore(ledger_db)
    legacy_style_store.ensure_schema()
    legacy_style_store.add_snapshot(ReconciliationSnapshot(
        id="prod-style-1", venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("3563.66"), as_of=_TS, created_at=_TS,
        note="pre-existing production-style row",
    ))
    legacy_style_store.close()

    # A fresh store instance against the SAME already-initialized DB.
    store = ReconciliationStore(ledger_db)
    assert store.schema_exists() is True
    snapshots = store.get_snapshots("revolut", "Osobní CZK", "CZK")
    assert len(snapshots) == 1
    assert snapshots[0].reported_balance == Decimal("3563.66")
    store.ensure_schema()  # calling ensure_schema again must be a safe no-op
    assert store.count() == 1
    store.close()
