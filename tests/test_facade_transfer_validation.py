"""Phase 2 — TRANSFER validation: (venue, account) source/destination identity.

Replaces the old blanket `to_venue == venue` rejection with:
    source == destination  =>  reject (true no-op)
    otherwise               =>  accept, including same-venue/different-account

Required scenarios (Phase 2 spec, section 1):
  - same venue + same account            -> reject
  - same venue + different account       -> accept
  - different venue + same account label -> accept
  - legacy account=None transfer between different venues -> unchanged
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


def _transfer(**overrides) -> AddTradeRequestDTO:
    defaults = dict(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        currency="CZK", price=Decimal("0"), venue="revolut", to_venue="revolut",
    )
    defaults.update(overrides)
    return AddTradeRequestDTO(**defaults)


def test_same_venue_same_account_is_rejected(db):
    result = add_trade(
        _transfer(account="Osobní CZK", to_account="Osobní CZK"), db,
    )
    assert result.success is False
    assert "identical" in result.error_message.lower()


def test_same_venue_no_account_on_either_side_is_rejected(db):
    """Legacy behaviour preserved: same venue, both accounts None (never set)
    is still a no-op and must still be rejected — this is the pre-Phase-2
    blanket case collapsing correctly into the new rule."""
    result = add_trade(_transfer(), db)  # account/to_account both default to None
    assert result.success is False
    assert "identical" in result.error_message.lower()


def test_same_venue_different_account_is_accepted(db):
    result = add_trade(
        _transfer(account="Osobní CZK", to_account="Investment CZK"), db,
    )
    assert result.success is True

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    accounts = {r.account for r in rows}
    assert accounts == {"Osobní CZK", "Investment CZK"}


def test_different_venue_same_account_label_is_accepted(db):
    """Same account LABEL on two different venues is not the same account —
    accepted, exactly like today's cross-venue transfers."""
    result = add_trade(
        _transfer(venue="revolut", to_venue="air bank",
                  account="Investment", to_account="Investment"),
        db,
    )
    assert result.success is True


def test_legacy_account_none_transfer_between_venues_unchanged(db):
    """No account fields set at all -> behaves exactly as before Phase 2."""
    result = add_trade(_transfer(venue="revolut", to_venue="air bank"), db)
    assert result.success is True

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    assert all(r.account is None for r in rows)
