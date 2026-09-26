"""Phase 1 — reversal must preserve `account` on both reversal code paths.

Without this, reversing an account-tagged row would silently lose the
account tag and land in "Unassigned Cash" in any cash-balance view — exactly
the bug identified during the Phase 0 addendum review.

Covers both reversal implementations:
    core/reversal.py               (create_reversal / create_reversal_pair)
    core/services/reversal_service.py (build_reversal_rows / reverse_trade)
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.cash import compute_cash_balances
from core.reversal import create_reversal, create_reversal_pair
from core.services.reversal_service import build_reversal_rows, reverse_trade

_TS = datetime(2026, 9, 10, 9, 31, 0)


def _sell_quote_leg(account="Osobní CZK", amount=Decimal("3344.25")):
    return RawRow(
        id="trade-1", timestamp=_TS, type="SELL", asset="CZK",
        amount=amount, currency="CZK", price=Decimal("1"),
        venue="revolut", account=account,
    )


# ── core/reversal.py ────────────────────────────────────────────────────────

def test_create_reversal_preserves_account():
    original = _sell_quote_leg()
    rev = create_reversal(original)
    assert rev.account == "Osobní CZK"
    assert rev.amount == -original.amount


def test_create_reversal_pair_preserves_account_per_row():
    crypto_leg = RawRow(
        id="trade-1", timestamp=_TS, type="SELL", asset="HYPE",
        amount=Decimal("-2"), currency="CZK", price=Decimal("1672.125"),
        venue="revolut", account=None,  # crypto leg never has an account
    )
    fiat_leg = _sell_quote_leg()
    reversals = create_reversal_pair([crypto_leg, fiat_leg])
    rev_crypto = next(r for r in reversals if r.asset == "HYPE")
    rev_fiat = next(r for r in reversals if r.asset == "CZK")
    assert rev_crypto.account is None
    assert rev_fiat.account == "Osobní CZK"


# ── core/services/reversal_service.py ───────────────────────────────────────

def test_build_reversal_rows_preserves_account():
    original = [_sell_quote_leg()]
    reversals = build_reversal_rows(original)
    assert reversals[0].account == "Osobní CZK"


def test_reverse_trade_end_to_end_stays_in_same_cash_account():
    """Integration: insert an account-tagged SELL proceeds row, reverse it
    via reverse_trade(), and confirm the cash balance for that (venue,
    account, currency) key nets to exactly zero — NOT split into a separate
    'Unassigned Cash' bucket.
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)  # LedgerStore creates it fresh
    try:
        store = LedgerStore(db_path)
        original = _sell_quote_leg()
        store.insert(original)
        store.close()

        reversal_rows = reverse_trade(db_path, "trade-1")
        assert len(reversal_rows) == 1
        assert reversal_rows[0].account == "Osobní CZK"

        store = LedgerStore(db_path)
        all_rows = store.timeline()
        store.close()

        balances = compute_cash_balances(all_rows)
        # The (venue, account, currency) key must net to exactly zero and
        # must NOT appear split under account=None.
        key_tagged = ("revolut", "Osobní CZK", "CZK")
        key_unassigned = ("revolut", None, "CZK")
        assert key_tagged not in balances  # zero balances are pruned
        assert key_unassigned not in balances
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
