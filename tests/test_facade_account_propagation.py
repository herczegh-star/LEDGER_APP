"""Phase 2 — account propagation through ui_facade.add_trade() (BUY/SELL/TRANSFER).

Complements Phase 1's tests/test_account_propagation.py (which tests
trade_service.build_trade_rows() directly). This file exercises the same
propagation through the full facade entry point used by the UI.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.ui_facade import AddTradeRequestDTO, add_trade

_TS = datetime(2026, 9, 10, 9, 31, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def _rows(db_path):
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


def test_sell_via_facade_propagates_account_to_fiat_leg_only(db):
    req = AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
        account="Osobní CZK",
    )
    result = add_trade(req, db)
    assert result.success

    rows = _rows(db)
    hype = next(r for r in rows if r.asset == "HYPE")
    czk = next(r for r in rows if r.asset == "CZK")
    assert hype.account is None
    assert czk.account == "Osobní CZK"


def test_buy_via_facade_propagates_account(db):
    req = AddTradeRequestDTO(
        type="BUY", timestamp=_TS, asset="BTC", amount=Decimal("0.01"),
        currency="CZK", price=Decimal("2500000"), venue="kraken",
        account="Trading CZK",
    )
    result = add_trade(req, db)
    assert result.success

    rows = _rows(db)
    czk = next(r for r in rows if r.asset == "CZK")
    assert czk.account == "Trading CZK"


def test_transfer_via_facade_propagates_account_to_both_legs(db):
    req = AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        currency="CZK", price=Decimal("0"), venue="revolut", to_venue="air bank",
        account="Osobní CZK", to_account="Investment Cash CZK",
    )
    result = add_trade(req, db)
    assert result.success

    rows = _rows(db)
    out_leg = next(r for r in rows if r.venue == "revolut")
    in_leg = next(r for r in rows if r.venue == "air bank")
    assert out_leg.account == "Osobní CZK"
    assert in_leg.account == "Investment Cash CZK"


def test_no_account_provided_matches_legacy_facade_behaviour(db):
    req = AddTradeRequestDTO(
        type="SELL", timestamp=_TS, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1672.125"), venue="revolut",
    )
    result = add_trade(req, db)
    assert result.success
    rows = _rows(db)
    assert all(r.account is None for r in rows)
