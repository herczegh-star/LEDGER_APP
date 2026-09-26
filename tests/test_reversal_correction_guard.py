"""core/services/reversal_service.py — guard against reversing a CORRECTION
via the standard REVERSAL mechanism (Phase 3C point 8).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.correction_service import add_correction
from core.services.reversal_service import reverse_trade
from core.services.trade_service import AddTradeInput
from core.services.trade_service import add_trade as core_add_trade

_TS_SELL = datetime(2026, 9, 10, 9, 35, 42)
_TS_CORR = datetime(2026, 9, 19, 13, 0, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_reverse_trade_rejects_a_correction_group(db):
    inp = AddTradeInput(
        type="SELL", timestamp=_TS_SELL, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3344.25"), venue="revolut",
        fee_amount=Decimal("75"), fee_currency="CZK",
    )
    trade_id = core_add_trade(db, inp).rows[0].id

    corr_rows = add_correction(
        db_path=db, timestamp=_TS_CORR, correction_of=trade_id,
        asset="HYPE", currency="CZK", delta=Decimal("75"), venue="revolut",
        reason="r", original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    corr_id = corr_rows[0].id

    with pytest.raises(ValueError, match="CORRECTION group"):
        reverse_trade(db, corr_id)

    # Nothing was written by the rejected reversal attempt.
    store = LedgerStore(db)
    count = store.count()
    store.close()
    assert count == 5  # 3 original SELL/FEE rows + 2 correction rows, no reversal added


def test_reverse_trade_still_works_normally_for_non_correction_trades(db):
    """The guard must not affect reversal of ordinary trades."""
    inp = AddTradeInput(
        type="BUY", timestamp=_TS_SELL, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
    )
    trade_id = core_add_trade(db, inp).rows[0].id
    reversal_rows = reverse_trade(db, trade_id)
    assert len(reversal_rows) == 2
    assert all(r.type == "REVERSAL" for r in reversal_rows)
