"""Phase Model B — core/services/portfolio_boundary_service.py."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.reports.cash import compute_cash_balances
from core.services.portfolio_boundary_service import (
    add_portfolio_contribution,
    add_portfolio_withdrawal,
)

_TS = datetime(2026, 9, 19, 12, 0, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_add_contribution_writes_exactly_two_rows(db):
    added = add_portfolio_contribution(
        db_path=db, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
        note="first real contribution",
    )
    assert len(added) == 2
    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    assert len(rows) == 2
    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("50000")


def test_add_withdrawal_writes_exactly_two_rows(db):
    add_portfolio_contribution(
        db_path=db, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    added = add_portfolio_withdrawal(
        db_path=db, timestamp=datetime(2026, 9, 20), asset="CZK", amount=Decimal("3000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    assert len(added) == 2
    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    assert len(rows) == 4
    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("47000")


def test_add_contribution_from_external(db):
    added = add_portfolio_contribution(
        db_path=db, timestamp=_TS, asset="CZK", amount=Decimal("10000"),
        outside_venue="external", outside_account=None,
        inside_venue="air_bank", inside_account="Investment Cash CZK",
        note="salary deposit",
    )
    assert len(added) == 2
    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    balances = compute_cash_balances(rows)
    assert balances[("air_bank", "Investment Cash CZK", "CZK")] == Decimal("10000")
    assert balances[("external", None, "CZK")] == Decimal("-10000")


def test_invalid_contribution_rejected_before_write(db):
    with pytest.raises(ValueError):
        add_portfolio_contribution(
            db_path=db, timestamp=_TS, asset="CZK", amount=Decimal("0"),
            outside_venue="revolut", outside_account="Osobní CZK",
            inside_venue="revolut", inside_account="Investment Cash CZK",
        )
    store = LedgerStore(db)
    count = store.count()
    store.close()
    assert count == 0
