"""Asset Notes — pure UI metadata for the Dashboard.

Covers: core/asset_note_store.py (Store Initialization Safety, mirrors
tests/test_account_role_store_lifecycle.py), core/services/asset_note_service.py
(validation), core/services/ui_facade.py's facade wrappers, and a regression
pinning that compute_positions() is completely unaffected by asset-note CRUD
(the note table has zero relation to `ledger` / RawRow / WAC / realized PnL).
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.asset_note_store import (
    AssetNote,
    AssetNoteSchemaNotInitializedError,
    AssetNoteStore,
)
from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.positions import compute_positions
from core.services.asset_note_service import delete_asset_note, set_asset_note
from core.services.ui_facade import (
    get_all_asset_notes,
    get_asset_note,
)
from core.services.ui_facade import (
    set_asset_note as facade_set_asset_note,
)
from core.services.ui_facade import (
    delete_asset_note as facade_delete_asset_note,
)
from core.services.ui_facade import create_db

_T0 = datetime(2026, 1, 1)


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def ledger_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = LedgerStore(db_path)
    store.close()
    return db_path


@pytest.fixture
def db(tmp_path):
    """A DB with asset_notes schema already initialized (normal app state)."""
    db_path = str(tmp_path / "test.db")
    store = AssetNoteStore(db_path)
    store.ensure_schema()
    store.close()
    return db_path


# ── Store: Initialization Safety (mirrors test_account_role_store_lifecycle) ─

def test_constructing_store_does_not_change_db_hash(ledger_db):
    before = _sha256(ledger_db)
    store = AssetNoteStore(ledger_db)
    store.close()
    assert _sha256(ledger_db) == before


def test_constructing_store_does_not_create_table(ledger_db):
    store = AssetNoteStore(ledger_db)
    exists = store.schema_exists()
    store.close()
    assert exists is False
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "asset_notes" not in tables


def test_read_on_uninitialized_schema_raises_without_creating_table(ledger_db):
    store = AssetNoteStore(ledger_db)
    with pytest.raises(AssetNoteSchemaNotInitializedError):
        store.get_note("WOO")
    still_missing = not store.schema_exists()
    store.close()
    assert still_missing


def test_set_note_on_missing_schema_fails_clearly(ledger_db):
    before = _sha256(ledger_db)
    store = AssetNoteStore(ledger_db)
    with pytest.raises(AssetNoteSchemaNotInitializedError, match="ensure_schema"):
        store.set_note(AssetNote(asset="WOO", note="x", updated_at=_T0))
    store.close()
    assert _sha256(ledger_db) == before


def test_ensure_schema_creates_expected_table(ledger_db):
    store = AssetNoteStore(ledger_db)
    store.ensure_schema()
    store.close()
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "asset_notes" in tables


def test_ensure_schema_is_idempotent(ledger_db):
    store = AssetNoteStore(ledger_db)
    store.ensure_schema()
    store.set_note(AssetNote(asset="WOO", note="x", updated_at=_T0))
    store.ensure_schema()
    store.ensure_schema()
    assert store.count() == 1
    store.close()


def test_read_only_connection_ensure_schema_raises(ledger_db):
    store = AssetNoteStore(ledger_db, read_only=True)
    with pytest.raises(RuntimeError, match="read_only"):
        store.ensure_schema()
    store.close()


def test_read_only_connection_cannot_mutate(ledger_db):
    setup = AssetNoteStore(ledger_db)
    setup.ensure_schema()
    setup.close()

    before = _sha256(ledger_db)
    ro_store = AssetNoteStore(ledger_db, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_store.set_note(AssetNote(asset="WOO", note="x", updated_at=_T0))
    ro_store.close()
    assert _sha256(ledger_db) == before


# ── Store: CRUD / mutability (genuinely mutable, unlike append-only tables) ──

def test_set_note_then_get_note(db):
    store = AssetNoteStore(db)
    store.set_note(AssetNote(asset="WOO", note="staked on WOOFi", updated_at=_T0))
    got = store.get_note("WOO")
    store.close()
    assert got.note == "staked on WOOFi"


def test_set_note_upserts_replacing_existing_value(db):
    store = AssetNoteStore(db)
    store.set_note(AssetNote(asset="WOO", note="first", updated_at=_T0))
    store.set_note(AssetNote(asset="WOO", note="second", updated_at=_T0))
    assert store.count() == 1
    got = store.get_note("WOO")
    store.close()
    assert got.note == "second"


def test_get_note_missing_returns_none(db):
    store = AssetNoteStore(db)
    got = store.get_note("WOO")
    store.close()
    assert got is None


def test_delete_note_removes_row_and_reports_true(db):
    store = AssetNoteStore(db)
    store.set_note(AssetNote(asset="WOO", note="x", updated_at=_T0))
    deleted = store.delete_note("WOO")
    still_there = store.get_note("WOO")
    store.close()
    assert deleted is True
    assert still_there is None


def test_delete_note_missing_returns_false(db):
    store = AssetNoteStore(db)
    deleted = store.delete_note("WOO")
    store.close()
    assert deleted is False


def test_get_all_notes_returns_every_asset(db):
    store = AssetNoteStore(db)
    store.set_note(AssetNote(asset="WOO", note="a", updated_at=_T0))
    store.set_note(AssetNote(asset="BTC", note="b", updated_at=_T0))
    notes = store.get_all_notes()
    store.close()
    assert {n.asset for n in notes} == {"WOO", "BTC"}


# ── Service: validation (mirrors test_account_role_service.py) ──────────────

def test_service_set_note_uppercases_asset(db):
    note = set_asset_note(db, "woo", "60 501.2768 WOO staked on WOOFi")
    assert note.asset == "WOO"


def test_service_set_note_rejects_empty_asset(db):
    with pytest.raises(ValueError, match="asset"):
        set_asset_note(db, "", "note text")


def test_service_set_note_rejects_empty_note(db):
    with pytest.raises(ValueError, match="note"):
        set_asset_note(db, "WOO", "")


def test_service_set_note_rejects_whitespace_only_note(db):
    with pytest.raises(ValueError, match="note"):
        set_asset_note(db, "WOO", "   ")


def test_service_delete_note_rejects_empty_asset(db):
    with pytest.raises(ValueError, match="asset"):
        delete_asset_note(db, "")


def test_service_delete_note_returns_false_when_missing(db):
    assert delete_asset_note(db, "WOO") is False


def test_service_delete_note_returns_true_when_present(db):
    set_asset_note(db, "WOO", "note text")
    assert delete_asset_note(db, "WOO") is True


# ── Facade: never raises, generic for any asset (no hardcoded WOO handling) ──

def test_facade_get_asset_note_on_fresh_uninitialized_db_returns_none(tmp_path):
    """create_db() always initializes asset_notes — but get_asset_note()
    must degrade gracefully (None, not raise) even if called against a DB
    where the schema was never set up, matching get_investment_cash_reserve's
    AccountRoleSchemaNotInitializedError-swallowing pattern."""
    db_path = str(tmp_path / "bare.db")
    store = LedgerStore(db_path)
    store.close()
    assert get_asset_note(db_path, "WOO") is None
    assert get_all_asset_notes(db_path) == {}


def test_facade_set_and_get_roundtrip(db):
    result = facade_set_asset_note(db, "ARB", "some generic note")
    assert result.success is True
    assert get_asset_note(db, "ARB") == "some generic note"


def test_facade_set_invalid_returns_error_not_raise(db):
    result = facade_set_asset_note(db, "ARB", "")
    assert result.success is False
    assert result.error_message


def test_facade_get_all_asset_notes_bulk(db):
    facade_set_asset_note(db, "ARB", "note 1")
    facade_set_asset_note(db, "SOL", "note 2")
    all_notes = get_all_asset_notes(db)
    assert all_notes == {"ARB": "note 1", "SOL": "note 2"}


def test_facade_delete_asset_note_removes_it(db):
    facade_set_asset_note(db, "ARB", "note 1")
    result = facade_delete_asset_note(db, "ARB")
    assert result.success is True
    assert get_asset_note(db, "ARB") is None


def test_facade_no_note_means_absent_not_empty_string(db):
    """UI must be able to distinguish 'no note' (hide the info icon) from an
    empty string — get_asset_note() returns None, never ''."""
    assert get_asset_note(db, "ARB") is None


# ── Regression: accounting core is completely unaffected by asset notes ─────

def test_compute_positions_unaffected_by_asset_note_crud(db):
    """Asset notes are pure Dashboard metadata — compute_positions() has no
    concept of asset_notes at all. Pin bit-identical output before/after a
    full note lifecycle (set/edit/delete) for the same asset."""
    rows = [
        RawRow(id="b1", timestamp=_T0, type="BUY", asset="WOO",
               amount=Decimal("60501.2768"), currency="CZK", price=Decimal("10"), venue="ledger_wallet"),
        RawRow(id="b1", timestamp=_T0, type="BUY", asset="CZK",
               amount=Decimal("-605012.768"), currency="CZK", price=Decimal("1"), venue="ledger_wallet"),
    ]
    positions_before = compute_positions(rows)

    facade_set_asset_note(db, "WOO", "60 501.2768 WOO staked on WOOFi / Network: Ethereum")
    facade_set_asset_note(db, "WOO", "edited note")
    facade_delete_asset_note(db, "WOO")

    positions_after = compute_positions(rows)
    assert positions_before == positions_after

    woo = next(p for p in positions_after if p.asset == "WOO")
    assert woo.quantity == Decimal("60501.2768")
    assert woo.cost_basis == Decimal("605012.768")


# ── Regression: create_db() must initialize asset_notes for PRE-EXISTING DBs
# too, not only brand-new ones — see bugfix, real ledger.db hit exactly this
# ("'asset_notes' table does not exist ... ensure_schema() first") because
# ui/app_flet.py's main_view() previously only called create_db() on the
# DB_MISSING (first-run onboarding) path, never for an already-OK DB that
# predates a given side-table. ──────────────────────────────────────────────

def test_create_db_adds_asset_notes_to_a_pre_existing_ledger_only_db(tmp_path):
    """Simulates the real-world case: a ledger.db created before asset_notes
    existed (only the `ledger` table present, no side-tables at all)."""
    db_path = str(tmp_path / "pre_existing.db")
    LedgerStore(db_path).close()

    store = AssetNoteStore(db_path)
    assert store.schema_exists() is False
    store.close()

    result = create_db(db_path)
    assert result.success is True

    store = AssetNoteStore(db_path)
    assert store.schema_exists() is True
    store.close()

    # And a note can now actually be saved — the exact operation that used
    # to raise AssetNoteSchemaNotInitializedError on such a DB.
    write_result = facade_set_asset_note(db_path, "WOO", "staked on WOOFi")
    assert write_result.success is True
    assert get_asset_note(db_path, "WOO") == "staked on WOOFi"


def test_create_db_on_existing_db_does_not_alter_ledger_rows(tmp_path):
    """create_db() re-run against an already-initialized DB must not touch
    existing ledger data — it only adds missing side-table schema."""
    from core.services.trade_service import AddTradeInput, add_trade

    db_path = str(tmp_path / "with_data.db")
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=_T0, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
    ))
    rows_before = LedgerStore(db_path).timeline()

    result = create_db(db_path)
    assert result.success is True

    rows_after = LedgerStore(db_path).timeline()
    assert rows_before == rows_after


def test_create_db_rerun_is_idempotent_and_preserves_existing_note(tmp_path):
    """Repeated app startups (repeated create_db() calls) must not raise and
    must not alter an already-saved note."""
    db_path = str(tmp_path / "repeat.db")
    LedgerStore(db_path).close()

    create_db(db_path)
    facade_set_asset_note(db_path, "WOO", "original note")

    create_db(db_path)  # second "startup"
    create_db(db_path)  # third "startup"

    assert get_asset_note(db_path, "WOO") == "original note"
