"""Pre-Production Hardening — section 4: core/services/account_assignment_service.py."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.historical_account_assignment_store import AccountAssignmentStore
from core.services.account_assignment_service import add_historical_account_assignment
from core.services.trade_service import AddTradeInput, add_trade

_TS = datetime(2026, 9, 19)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = AccountAssignmentStore(db_path)
    store.ensure_schema()
    store.close()
    return db_path


def _seed_row_fp(db_path):
    result = add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=_TS, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=Decimal("1000"), venue="revolut",
    ))
    czk_row = next(r for r in result.rows if r.asset == "CZK")
    return czk_row.fingerprint()


def test_valid_assignment(db):
    fp = _seed_row_fp(db)
    assignment = add_historical_account_assignment(
        db, row_fp=fp, effective_account="Investment Cash CZK", effective_at=_TS,
        source="test", note="n",
    )
    assert assignment.effective_account == "Investment Cash CZK"
    assert assignment.trade_id  # derived automatically
    assert assignment.venue == "revolut"  # derived automatically, matches the real row


def test_rejects_nonexistent_row_fp(db):
    with pytest.raises(ValueError, match="neodpovídá žádnému řádku"):
        add_historical_account_assignment(
            db, row_fp="does-not-exist", effective_account="Investment Cash CZK", effective_at=_TS,
        )


def test_rejects_empty_effective_account(db):
    fp = _seed_row_fp(db)
    with pytest.raises(ValueError, match="effective_account"):
        add_historical_account_assignment(db, row_fp=fp, effective_account="", effective_at=_TS)


def test_rejects_non_datetime_effective_at(db):
    fp = _seed_row_fp(db)
    with pytest.raises(ValueError, match="effective_at"):
        add_historical_account_assignment(db, row_fp=fp, effective_account="x", effective_at="2026-09-19")


def test_duplicate_revision_assignment_allowed(db):
    """A second assignment for the SAME row_fp (a revision) must be
    accepted, not rejected as a duplicate."""
    fp = _seed_row_fp(db)
    add_historical_account_assignment(db, row_fp=fp, effective_account="Investment Cash CZK", effective_at=_TS)
    add_historical_account_assignment(db, row_fp=fp, effective_account="Osobní CZK", effective_at=_TS + timedelta(days=1))

    store = AccountAssignmentStore(db)
    all_for_fp = store.get_assignments_for_row_fp(fp)
    store.close()
    assert len(all_for_fp) == 2


def test_same_effective_at_created_at_tiebreak(db):
    """Two assignments with the IDENTICAL effective_at (a correction of a
    correction, written moments apart) — resolver must pick the later
    created_at, not raise or pick arbitrarily."""
    from core.account_resolver import resolve_effective_account
    fp = _seed_row_fp(db)
    add_historical_account_assignment(db, row_fp=fp, effective_account="Investment Cash CZK", effective_at=_TS)
    add_historical_account_assignment(db, row_fp=fp, effective_account="Osobní CZK", effective_at=_TS)

    store = AccountAssignmentStore(db)
    grouped = store.get_all_assignments()
    store.close()

    from core.ledger_store import LedgerStore
    rows = LedgerStore(db).timeline()
    row = next(r for r in rows if r.fingerprint() == fp)
    resolved = resolve_effective_account(row, grouped, as_of=_TS)
    assert resolved == "Osobní CZK"  # later created_at wins
