"""Pre-Production Hardening — section 3: core/services/account_role_service.py."""
from __future__ import annotations

from datetime import datetime

import pytest

from core.account_role_store import AccountRoleStore
from core.services.account_role_service import add_account_role

_T0 = datetime(2026, 9, 19)


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = AccountRoleStore(db_path)
    store.ensure_schema()
    store.close()
    return db_path


def test_valid_personal_role(db):
    role = add_account_role(db, "revolut", "Osobní CZK", "CZK", "PERSONAL", _T0)
    assert role.role == "PERSONAL"
    assert role.created_at is not None
    store = AccountRoleStore(db)
    assert store.count() == 1
    store.close()


def test_valid_investment_cash_role(db):
    role = add_account_role(db, "air_bank", "Investment Cash CZK", "CZK", "INVESTMENT_CASH", _T0)
    assert role.role == "INVESTMENT_CASH"


def test_rejects_legacy_unassigned_as_explicit_role(db):
    with pytest.raises(ValueError, match="LEGACY_UNASSIGNED"):
        add_account_role(db, "revolut", "x", "CZK", "LEGACY_UNASSIGNED", _T0)


def test_rejects_arbitrary_unknown_role_string(db):
    with pytest.raises(ValueError, match="role"):
        add_account_role(db, "revolut", "x", "CZK", "SOMETHING_ELSE", _T0)


def test_rejects_empty_venue(db):
    with pytest.raises(ValueError, match="venue"):
        add_account_role(db, "", "x", "CZK", "PERSONAL", _T0)


def test_rejects_empty_account(db):
    with pytest.raises(ValueError, match="account"):
        add_account_role(db, "revolut", "", "CZK", "PERSONAL", _T0)


def test_rejects_none_account(db):
    with pytest.raises(ValueError, match="account"):
        add_account_role(db, "revolut", None, "CZK", "PERSONAL", _T0)


def test_rejects_empty_currency(db):
    with pytest.raises(ValueError, match="currency"):
        add_account_role(db, "revolut", "x", "", "PERSONAL", _T0)


def test_rejects_non_datetime_effective_at(db):
    with pytest.raises(ValueError, match="effective_at"):
        add_account_role(db, "revolut", "x", "CZK", "PERSONAL", "2026-09-19")


def test_created_at_is_actual_write_time_not_caller_supplied(db):
    before = datetime.now()
    role = add_account_role(db, "revolut", "x", "CZK", "PERSONAL", _T0)
    after = datetime.now()
    assert before <= role.created_at <= after
    assert role.effective_at == _T0  # unchanged, caller-supplied
