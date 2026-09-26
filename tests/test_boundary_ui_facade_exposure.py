"""Pre-Production Hardening — section 9: PORTFOLIO_CONTRIBUTION/WITHDRAWAL
must NOT be reachable via the generic Add Trade path — only through the
dedicated portfolio_boundary_service.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.constants import TRADE_TYPES
from core.ledger_store import LedgerStore
from core.services.ui_facade import AddTradeRequestDTO, add_trade

_TS = datetime(2026, 9, 19)


def test_portfolio_types_not_in_trade_types_constant():
    assert "PORTFOLIO_CONTRIBUTION" not in TRADE_TYPES
    assert "PORTFOLIO_WITHDRAWAL" not in TRADE_TYPES


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_generic_add_trade_rejects_portfolio_contribution_type(db):
    result = add_trade(AddTradeRequestDTO(
        type="PORTFOLIO_CONTRIBUTION", timestamp=_TS, asset="CZK", amount=Decimal("1000"),
        currency="CZK", price=Decimal("1"), venue="revolut", account="Investment Cash CZK",
    ), db)
    assert not result.success
    assert "Invalid trade type" in (result.error_message or "")


def test_generic_add_trade_rejects_portfolio_withdrawal_type(db):
    result = add_trade(AddTradeRequestDTO(
        type="PORTFOLIO_WITHDRAWAL", timestamp=_TS, asset="CZK", amount=Decimal("1000"),
        currency="CZK", price=Decimal("1"), venue="revolut", account="Investment Cash CZK",
    ), db)
    assert not result.success
    assert "Invalid trade type" in (result.error_message or "")


def test_nothing_written_by_rejected_attempt(db):
    add_trade(AddTradeRequestDTO(
        type="PORTFOLIO_CONTRIBUTION", timestamp=_TS, asset="CZK", amount=Decimal("1000"),
        currency="CZK", price=Decimal("1"), venue="revolut", account="Investment Cash CZK",
    ), db)
    # DB file may not even have been created (add_trade opens LedgerStore
    # only after the type check today — either way, no boundary row exists).
    import os
    if os.path.exists(db):
        store = LedgerStore(db)
        count = store.count()
        store.close()
        assert count == 0


def test_add_trade_dialog_module_never_imports_boundary_service():
    """Static confirmation that the generic Add Trade UI module has no
    code path reaching the dedicated boundary service."""
    import ui.modules.add_trade_dialog as dlg
    import inspect
    source = inspect.getsource(dlg)
    assert "portfolio_boundary_service" not in source
    assert "PORTFOLIO_CONTRIBUTION" not in source
    assert "PORTFOLIO_WITHDRAWAL" not in source
