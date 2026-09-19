"""Phase BUY-FIX — core/services/reversal_service.py guard against reversing
a BUY_COST_CORRECTION via the standard REVERSAL mechanism. Mirrors
tests/test_reversal_correction_guard.py for the sibling CORRECTION guard.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.buy_cost_correction_service import add_buy_cost_correction
from core.services.reversal_service import reverse_trade
from core.services.trade_service import AddTradeInput, add_trade

_TS_BUY = datetime(2026, 9, 9, 21, 22, 39)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_reverse_trade_rejects_a_buy_cost_correction_group(db):
    result = add_trade(db, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="ARB", base_amount=Decimal("1495.0309551"),
        quote_currency="CZK", quote_amount=Decimal("5000"), venue="revolut",
        fee_amount=Decimal("89"), fee_currency="CZK",
    ))
    trade_id = result.rows[0].id

    added = add_buy_cost_correction(
        db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        mode="historical",
    )
    corr_id = added[0].id

    with pytest.raises(ValueError, match="BUY_COST_CORRECTION group"):
        reverse_trade(db, corr_id)

    store = LedgerStore(db)
    count = store.count()
    store.close()
    assert count == 5  # 3 original BUY/FEE rows + 2 correction rows, no reversal added


def test_reverse_trade_still_works_normally_for_non_correction_trades(db):
    """The new guard must not affect reversal of ordinary trades."""
    result = add_trade(db, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
    ))
    trade_id = result.rows[0].id
    reversal_rows = reverse_trade(db, trade_id)
    assert len(reversal_rows) == 2
    assert all(r.type == "REVERSAL" for r in reversal_rows)
