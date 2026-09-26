"""Phase 1 — same-venue, different-account transfer: DATA-LAYER PROOF ONLY.

Scope note (per Phase 1 instructions, section 9): this file proves that the
model/store layer already supports a same-venue transfer between two
different cash accounts (e.g. Revolut / Osobní CZK -> Revolut / Investment
CZK) WITHOUT any dedup collision — it does NOT implement the business-rule
validation that would reject a true no-op (same venue AND same account).

That validation lives in core/services/ui_facade.py's TRANSFER branch,
which today hard-rejects ANY transfer where to_venue == venue:

    if to_venue_raw == venue:
        return error("to_venue must differ from venue")

Relaxing that check to "reject only when (venue, account) is identical on
both sides" requires extending AddTradeRequestDTO with account/to_account
and touching ui_facade.add_trade(). Per the Phase 1 scope guard, that
facade change is NOT made here — this is intentionally deferred to Phase 2
and is flagged in the Phase 1 report (section J / K), not implemented.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.cash import compute_cash_balances

_TS = datetime(2026, 9, 10, 12, 0, 0)


def test_same_venue_different_account_transfer_has_no_dedup_collision():
    """Two TRANSFER rows, SAME venue, DIFFERENT account, opposite amounts,
    same trade id -> both insert successfully. Their legacy fingerprints
    already differ because `amount` differs in sign -- this has nothing to
    do with account-awareness; it is inherent to the existing TRANSFER
    two-row model and confirms the data layer needs zero change for this
    scenario."""
    row_out = RawRow(
        id="transfer-1", timestamp=_TS, type="TRANSFER", asset="CZK",
        amount=Decimal("-50000"), currency="CZK", price=Decimal("1"),
        venue="revolut", account="Osobní CZK",
    )
    row_in = RawRow(
        id="transfer-1", timestamp=_TS, type="TRANSFER", asset="CZK",
        amount=Decimal("50000"), currency="CZK", price=Decimal("1"),
        venue="revolut", account="Investment CZK",
    )
    assert row_out.fingerprint() != row_in.fingerprint()  # differ by amount sign alone

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    try:
        store = LedgerStore(db_path)
        ok_out, ok_in = store.insert_pair(row_out, row_in)
        assert ok_out and ok_in

        rows = store.timeline()
        store.close()

        balances = compute_cash_balances(rows)
        assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-50000")
        assert balances[("revolut", "Investment CZK", "CZK")] == Decimal("50000")
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_same_venue_same_account_selftransfer_is_representable_but_not_validated():
    """Documents current (Phase 1) behaviour: the data layer does NOT reject
    a same-venue, same-account 'transfer' (a no-op with zero net effect on
    that account). Business-rule rejection of this case is explicitly
    OUT OF SCOPE for Phase 1 — see module docstring. This test exists so a
    future Phase 2 validation change has a concrete before/after to check
    against."""
    row_out = RawRow(
        id="transfer-2", timestamp=_TS, type="TRANSFER", asset="CZK",
        amount=Decimal("-1000"), currency="CZK", price=Decimal("1"),
        venue="revolut", account="Osobní CZK",
    )
    row_in = RawRow(
        id="transfer-2", timestamp=_TS, type="TRANSFER", asset="CZK",
        amount=Decimal("1000"), currency="CZK", price=Decimal("1"),
        venue="revolut", account="Osobní CZK",  # same venue AND same account
    )

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    try:
        store = LedgerStore(db_path)
        ok_out, ok_in = store.insert_pair(row_out, row_in)
        # Data layer accepts it today -- no validation guard exists here.
        assert ok_out and ok_in

        rows = store.timeline()
        store.close()
        balances = compute_cash_balances(rows)
        # Nets to zero, as expected for a true no-op -- but nothing stopped
        # it from being written in the first place.
        assert ("revolut", "Osobní CZK", "CZK") not in balances
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
