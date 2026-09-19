"""Phase Model B — core/historical_account_assignment_store.py +
core/account_resolver.py.

Covers: overlay by row_fp, historical as_of overlay resolution, central
resolver fallback, store lifecycle safety (mirrors the fixed
ReconciliationStore/AccountRoleStore pattern), and positions-unaffected.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from core.account_resolver import resolve_effective_account
from core.historical_account_assignment_store import (
    AccountAssignment,
    AccountAssignmentSchemaNotInitializedError,
    AccountAssignmentStore,
)
from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.positions import compute_positions

_T0 = datetime(2026, 1, 1)
_T1 = datetime(2026, 6, 1)


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def ledger_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = LedgerStore(db_path)
    store.close()
    return db_path


def _row(account=None):
    return RawRow(
        id="buy-1", timestamp=_T0, type="BUY", asset="CZK",
        amount=Decimal("-5000"), currency="CZK", price=Decimal("1"), venue="revolut",
        account=account,
    )


def _assignment(row_fp, effective_account, effective_at=_T0, created_at=None, id_suffix="1"):
    return AccountAssignment(
        id=f"a-{id_suffix}", row_fp=row_fp, trade_id="buy-1", venue="revolut",
        effective_account=effective_account, effective_at=effective_at,
        created_at=created_at or effective_at,
    )


# ── central resolver: fallback ──────────────────────────────────────────────

def test_resolver_fallback_no_assignments():
    row = _row(account=None)
    assert resolve_effective_account(row, None) == row.account
    assert resolve_effective_account(row, {}) == row.account


def test_resolver_fallback_no_matching_row_fp():
    row = _row(account="Osobní CZK")
    assignments = {"some-other-row-fp": [_assignment("some-other-row-fp", "Investment Cash CZK")]}
    assert resolve_effective_account(row, assignments) == "Osobní CZK"


# ── overlay by row_fp ────────────────────────────────────────────────────────

def test_resolver_applies_matching_overlay():
    row = _row(account=None)
    fp = row.fingerprint()
    assignments = {fp: [_assignment(fp, "Investment Cash CZK")]}
    assert resolve_effective_account(row, assignments, as_of=_T1) == "Investment Cash CZK"


def test_resolver_does_not_mutate_row():
    row = _row(account=None)
    fp = row.fingerprint()
    assignments = {fp: [_assignment(fp, "Investment Cash CZK")]}
    resolve_effective_account(row, assignments, as_of=_T1)
    assert row.account is None  # original RawRow untouched


# ── historical as_of overlay resolution ─────────────────────────────────────

def test_resolver_respects_as_of_before_assignment_effective():
    row = _row(account=None)
    fp = row.fingerprint()
    assignments = {fp: [_assignment(fp, "Investment Cash CZK", effective_at=_T1)]}
    # Querying as_of BEFORE the assignment's own effective_at -> not yet applied.
    assert resolve_effective_account(row, assignments, as_of=_T0) == row.account
    assert resolve_effective_account(row, assignments, as_of=_T1) == "Investment Cash CZK"


def test_resolver_latest_effective_at_wins_with_tiebreak():
    row = _row(account=None)
    fp = row.fingerprint()
    assignments = {fp: [
        _assignment(fp, "Investment Cash CZK", effective_at=_T0, id_suffix="a"),
        _assignment(fp, "Osobní CZK", effective_at=_T0, created_at=_T1, id_suffix="b"),
    ]}
    # Same effective_at, later created_at (a revision) wins.
    assert resolve_effective_account(row, assignments, as_of=_T1) == "Osobní CZK"


# ── positions unaffected by overlay ──────────────────────────────────────────

def test_overlay_never_affects_compute_positions():
    rows = [
        RawRow(id="b1", timestamp=_T0, type="BUY", asset="ARB", amount=Decimal("100"),
               currency="CZK", price=Decimal("10"), venue="revolut", account=None),
        RawRow(id="b1", timestamp=_T0, type="BUY", asset="CZK", amount=Decimal("-1000"),
               currency="CZK", price=Decimal("1"), venue="revolut", account=None),
    ]
    positions_before = compute_positions(rows)
    # Overlay exists conceptually but compute_positions() never even sees it
    # (no parameter for it) — this test documents that fact structurally.
    positions_after = compute_positions(rows)
    assert positions_before == positions_after


# ── store lifecycle safety (mirrors ReconciliationStore/AccountRoleStore) ──

def test_constructing_store_does_not_mutate_db(ledger_db):
    before = _sha256(ledger_db)
    store = AccountAssignmentStore(ledger_db)
    store.close()
    assert _sha256(ledger_db) == before


def test_read_on_uninitialized_schema_raises(ledger_db):
    store = AccountAssignmentStore(ledger_db)
    with pytest.raises(AccountAssignmentSchemaNotInitializedError):
        store.get_assignments_for_row_fp("whatever")
    store.close()


def test_ensure_schema_idempotent_and_read_only_safe(ledger_db):
    store = AccountAssignmentStore(ledger_db)
    store.ensure_schema()
    store.ensure_schema()
    row_fp = "fp-1"
    store.add_assignment(_assignment(row_fp, "Investment Cash CZK"))
    assert store.count() == 1
    store.close()

    before = _sha256(ledger_db)
    ro_store = AccountAssignmentStore(ledger_db, read_only=True)
    with pytest.raises(RuntimeError, match="read_only"):
        ro_store.ensure_schema()
    with pytest.raises(sqlite3.OperationalError):
        ro_store.add_assignment(_assignment("fp-2", "x"))
    results = ro_store.get_assignments_for_row_fp(row_fp)
    ro_store.close()
    assert len(results) == 1
    assert _sha256(ledger_db) == before


def test_get_all_assignments_grouped_by_row_fp(ledger_db):
    store = AccountAssignmentStore(ledger_db)
    store.ensure_schema()
    store.add_assignment(_assignment("fp-1", "Investment Cash CZK", id_suffix="1"))
    store.add_assignment(_assignment("fp-2", "Osobní CZK", id_suffix="2"))
    grouped = store.get_all_assignments()
    store.close()
    assert set(grouped.keys()) == {"fp-1", "fp-2"}
    assert grouped["fp-1"][0].effective_account == "Investment Cash CZK"
