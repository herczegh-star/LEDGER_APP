"""Phase Cash Reconciliation — full test matrix per the architecture approval:
A) exact match, B) positive difference, C) negative difference,
D) as_of historical exclusion, E) history ordering, F) stale snapshot,
G) currency-aware tolerance, H) no-snapshot -> NOT READY,
I) all MATCH+fresh -> READY, J) one DIFFERENCE -> NOT READY,
K) one STALE -> NOT READY, L) Legacy/Unassigned ignored,
M) snapshot never alters positions/cash ledger/dashboard/row count.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.reconciliation import DIFFERENCE, MATCH, STALE, compute_status, get_tolerance
from core.reconciliation_store import ReconciliationStore
from core.reports.cash import compute_cash_balance_as_of, compute_cash_balances
from core.reports.positions import compute_positions
from core.services.buy_cost_correction_service import add_buy_cost_correction
from core.services.reconciliation_service import (
    add_reconciliation_snapshot,
    cash_reconciliation_readiness,
    get_reconciliation_history,
    is_cash_account_reconciled,
)
from core.services.trade_service import AddTradeInput, add_trade

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)


@pytest.fixture
def db(tmp_path):
    """Mirrors ui_facade.create_db()'s real setup path: schema is
    initialized explicitly here, once, up front — never implicitly by
    opening a store or reading (see core/reconciliation_store.py)."""
    db_path = str(tmp_path / "test.db")
    store = ReconciliationStore(db_path)
    try:
        store.ensure_schema()
    finally:
        store.close()
    return db_path


def _seed_account(db_path, account="Osobní CZK", quote_amount=Decimal("1000"), ts=_TS_BUY):
    """One BUY that credits/debits `account` by -quote_amount CZK.

    `ts` must be distinct per call when seeding multiple accounts in one
    test — the legacy row_fp fingerprint deliberately excludes `account`
    (Phase 0 "Varianta 1"), so two BUYs identical in every OTHER field
    (timestamp/type/venue/asset/currency/amount) collide and the second is
    silently deduped, regardless of a different account.
    """
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=ts, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=quote_amount, venue="revolut", account=account,
    ))


# ── core/reconciliation.py: pure status logic ───────────────────────────────

def test_get_tolerance_known_currencies():
    assert get_tolerance("CZK") == Decimal("1.00")
    assert get_tolerance("EUR") == Decimal("0.01")


def test_get_tolerance_unknown_currency_raises():
    with pytest.raises(ValueError, match="No reconciliation tolerance"):
        get_tolerance("XYZ")


def test_compute_status_match_within_tolerance():
    now = datetime(2026, 9, 19, 12, 0, 0)
    status = compute_status(
        reported_balance=Decimal("1000.50"), calculated_balance=Decimal("1000.00"),
        as_of=now, now=now, currency="CZK",
    )
    assert status == MATCH


def test_compute_status_difference_beyond_tolerance():
    now = datetime(2026, 9, 19, 12, 0, 0)
    status = compute_status(
        reported_balance=Decimal("1005.00"), calculated_balance=Decimal("1000.00"),
        as_of=now, now=now, currency="CZK",
    )
    assert status == DIFFERENCE


def test_compute_status_stale_takes_precedence_over_match():
    as_of = datetime(2026, 1, 1, 12, 0, 0)
    now = datetime(2026, 9, 19, 12, 0, 0)  # far more than 30 days later
    status = compute_status(
        reported_balance=Decimal("1000.00"), calculated_balance=Decimal("1000.00"),
        as_of=as_of, now=now, currency="CZK",
    )
    assert status == STALE


def test_compute_status_currency_aware_tolerance_eur_stricter():
    now = datetime(2026, 9, 19, 12, 0, 0)
    # 0.50 CZK difference -> MATCH (tolerance 1.00)
    assert compute_status(Decimal("1000.50"), Decimal("1000.00"), now, now, "CZK") == MATCH
    # 0.50 EUR difference -> DIFFERENCE (tolerance 0.01)
    assert compute_status(Decimal("1000.50"), Decimal("1000.00"), now, now, "EUR") == DIFFERENCE


# ── A/B/C: compute_cash_balance_as_of + snapshot match/difference ──────────

def test_a_exact_match(db):
    _seed_account(db)
    as_of = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=as_of,
    )
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK", now=as_of)
    assert len(history) == 1
    assert history[0].calculated_balance == Decimal("-1000")
    assert history[0].difference == Decimal("0")
    assert history[0].status == MATCH


def test_b_positive_difference(db):
    _seed_account(db)
    as_of = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("12345"), as_of=as_of,
    )
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK", now=as_of)
    assert history[0].calculated_balance == Decimal("-1000")
    assert history[0].difference == Decimal("13345")  # reported - calculated
    assert history[0].status == DIFFERENCE


def test_c_negative_difference(db):
    _seed_account(db)
    as_of = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-2000"), as_of=as_of,
    )
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK", now=as_of)
    assert history[0].difference == Decimal("-1000")  # -2000 - (-1000)
    assert history[0].status == DIFFERENCE


# ── D: as_of historical exclusion ───────────────────────────────────────────

def test_d_as_of_excludes_later_transactions(db):
    _seed_account(db, quote_amount=Decimal("1000"))
    as_of = _TS_BUY + timedelta(days=1)

    # A SECOND buy happens AFTER as_of -> must NOT be included in the
    # calculated balance at as_of.
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=as_of + timedelta(days=10), base_asset="ARB",
        base_amount=Decimal("50"), quote_currency="CZK", quote_amount=Decimal("500"),
        venue="revolut", account="Osobní CZK",
    ))

    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=as_of,
    )
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK", now=as_of)
    # calculated_balance as of `as_of` must be -1000, NOT -1500.
    assert history[0].calculated_balance == Decimal("-1000")
    assert history[0].status == MATCH


def test_compute_cash_balance_as_of_directly(db):
    store = LedgerStore(db)
    from core.model import RawRow
    rows = [
        RawRow(id="b1", timestamp=_TS_BUY, type="BUY", asset="CZK",
               amount=Decimal("-1000"), currency="CZK", price=Decimal("1"),
               venue="revolut", account="Osobní CZK"),
        RawRow(id="b2", timestamp=_TS_BUY + timedelta(days=10), type="BUY", asset="CZK",
               amount=Decimal("-500"), currency="CZK", price=Decimal("1"),
               venue="revolut", account="Osobní CZK"),
    ]
    store.import_rows(rows)
    store.close()

    all_rows = LedgerStore(db).timeline()
    balance_early = compute_cash_balance_as_of(all_rows, "revolut", "Osobní CZK", "CZK", _TS_BUY + timedelta(days=1))
    balance_late = compute_cash_balance_as_of(all_rows, "revolut", "Osobní CZK", "CZK", _TS_BUY + timedelta(days=20))
    assert balance_early == Decimal("-1000")
    assert balance_late == Decimal("-1500")


# ── E: two snapshots, same account, history ordered correctly ─────────────

def test_e_two_snapshots_history_ordered_by_as_of(db):
    _seed_account(db)
    later_as_of = _TS_BUY + timedelta(days=10)
    earlier_as_of = _TS_BUY + timedelta(days=1)

    # Insert the LATER one first, to prove ordering is by as_of, not insert order.
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=later_as_of, note="second",
    )
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=earlier_as_of, note="first",
    )
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK")
    assert len(history) == 2
    assert history[0].as_of == earlier_as_of
    assert history[0].note == "first"
    assert history[1].as_of == later_as_of
    assert history[1].note == "second"


# ── F: stale snapshot ────────────────────────────────────────────────────

def test_f_stale_snapshot(db):
    _seed_account(db)
    old_as_of = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=old_as_of,
    )
    now = old_as_of + timedelta(days=60)  # well beyond 30-day default cadence
    history = get_reconciliation_history(db, "revolut", "Osobní CZK", "CZK", now=now)
    assert history[0].status == STALE


# ── G: currency-aware tolerance via add_reconciliation_snapshot ───────────

def test_g_add_snapshot_rejects_unsupported_currency(db):
    _seed_account(db)
    with pytest.raises(ValueError, match="No reconciliation tolerance"):
        add_reconciliation_snapshot(
            db_path=db, venue="revolut", account="Osobní CZK", currency="XYZ",
            reported_balance=Decimal("100"), as_of=_TS_BUY,
        )


def test_g_validation_rejects_empty_fields(db):
    with pytest.raises(ValueError, match="venue"):
        add_reconciliation_snapshot(
            db_path=db, venue="", account="Osobní CZK", currency="CZK",
            reported_balance=Decimal("100"), as_of=_TS_BUY,
        )
    with pytest.raises(ValueError, match="account"):
        add_reconciliation_snapshot(
            db_path=db, venue="revolut", account="", currency="CZK",
            reported_balance=Decimal("100"), as_of=_TS_BUY,
        )
    with pytest.raises(ValueError, match="reported_balance"):
        add_reconciliation_snapshot(
            db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
            reported_balance="100", as_of=_TS_BUY,  # not a Decimal
        )
    with pytest.raises(ValueError, match="as_of"):
        add_reconciliation_snapshot(
            db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
            reported_balance=Decimal("100"), as_of="2026-09-19",  # not a datetime
        )


# ── H/I/J/K: readiness ──────────────────────────────────────────────────────

def test_h_account_with_no_snapshot_not_ready(db):
    _seed_account(db)
    result = cash_reconciliation_readiness(db)
    assert result.ready is False
    assert ("revolut", "Osobní CZK", "CZK") in result.accounts_not_ready


def test_i_all_tracked_match_and_fresh_ready(db):
    _seed_account(db)
    now = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=now,
    )
    result = cash_reconciliation_readiness(db, now=now)
    assert result.ready is True
    assert result.accounts_not_ready == []
    assert result.accounts_checked == 1


def test_j_one_account_difference_not_ready(db):
    _seed_account(db, account="Acc A", ts=_TS_BUY)
    _seed_account(db, account="Acc B", ts=_TS_BUY + timedelta(hours=1))
    now = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Acc A", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=now,
    )
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Acc B", currency="CZK",
        reported_balance=Decimal("-9999"), as_of=now,  # way off -> DIFFERENCE
    )
    result = cash_reconciliation_readiness(db, now=now)
    assert result.ready is False
    assert ("revolut", "Acc B", "CZK") in result.accounts_not_ready
    assert ("revolut", "Acc A", "CZK") not in result.accounts_not_ready


def test_k_one_account_stale_not_ready(db):
    _seed_account(db, account="Acc A", ts=_TS_BUY)
    _seed_account(db, account="Acc B", ts=_TS_BUY + timedelta(hours=1))
    old_as_of = _TS_BUY + timedelta(days=1)
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Acc A", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=old_as_of,
    )
    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Acc B", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=old_as_of,
    )
    now = old_as_of + timedelta(days=60)  # both stale now
    result = cash_reconciliation_readiness(db, now=now)
    assert result.ready is False
    assert len(result.accounts_not_ready) == 2


# ── L: Legacy/Unassigned Cash ignored by readiness ──────────────────────────

def test_l_legacy_unassigned_ignored_by_readiness(db):
    # A legacy BUY with NO account (account=None) creates an "Unassigned
    # Cash" bucket -> must never be required to reconcile, never block readiness.
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=Decimal("1000"), venue="revolut",
        # no account -> legacy/unassigned
    ))
    result = cash_reconciliation_readiness(db)
    # No TRACKED accounts exist (only the legacy bucket) -> explicit
    # "no tracked accounts" reason, not a false READY.
    assert result.ready is False
    assert result.accounts_checked == 0
    assert result.accounts_not_ready == []


def test_l_legacy_unassigned_never_appears_in_not_ready_list(db):
    _seed_account(db, account="Acc A")  # tracked, unreconciled
    add_trade(db, AddTradeInput(  # legacy, untracked
        type="BUY", timestamp=_TS_BUY, base_asset="BTC", base_amount=Decimal("1"),
        quote_currency="CZK", quote_amount=Decimal("500000"), venue="kraken",
    ))
    result = cash_reconciliation_readiness(db)
    assert all(acct is not None for (_v, acct, _c) in result.accounts_not_ready)
    assert result.accounts_checked == 1  # only the tracked account counted


# ── M: adding a snapshot alters NOTHING accounting-relevant ────────────────

def test_m_snapshot_does_not_alter_positions_cash_or_row_count(db):
    _seed_account(db)
    store = LedgerStore(db)
    rows_before = store.timeline()
    store.close()
    positions_before = {p.asset: p for p in compute_positions(rows_before)}
    cash_before = compute_cash_balances(rows_before)
    row_count_before = len(rows_before)

    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("999999"), as_of=_TS_BUY + timedelta(days=1),
        note="should not touch ledger at all",
    )

    store = LedgerStore(db)
    rows_after = store.timeline()
    store.close()
    positions_after = {p.asset: p for p in compute_positions(rows_after)}
    cash_after = compute_cash_balances(rows_after)

    assert len(rows_after) == row_count_before  # ledger row count UNCHANGED
    assert positions_after == positions_before
    assert cash_after == cash_before


def test_m_snapshot_does_not_alter_buy_cost_correction_state(db):
    """Cross-check with the sibling BUY_COST_CORRECTION feature: adding a
    reconciliation snapshot around an existing correction must not perturb
    its effect at all."""
    result = add_trade(db, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=Decimal("1089"), venue="revolut",
        fee_amount=Decimal("89"), fee_currency="CZK", account="Osobní CZK",
    ))
    trade_id = result.rows[0].id
    add_buy_cost_correction(
        db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("1089"), corrected_value=Decimal("1000"),
        account="Osobní CZK", mode="historical",
    )
    store = LedgerStore(db)
    rows_before = store.timeline()
    store.close()
    positions_before = {p.asset: p for p in compute_positions(rows_before)}

    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-911"), as_of=_TS_BUY + timedelta(days=1),
    )

    store = LedgerStore(db)
    rows_after = store.timeline()
    store.close()
    positions_after = {p.asset: p for p in compute_positions(rows_after)}
    assert positions_after == positions_before
    assert len(rows_after) == len(rows_before)


def test_reconciliation_table_row_count_does_increase(db):
    """Sanity: the RECONCILIATION table itself is of course allowed to grow
    — only the `ledger` table must stay untouched."""
    from core.reconciliation_store import ReconciliationStore

    _seed_account(db)
    recon_store = ReconciliationStore(db)
    assert recon_store.count() == 0
    recon_store.close()

    add_reconciliation_snapshot(
        db_path=db, venue="revolut", account="Osobní CZK", currency="CZK",
        reported_balance=Decimal("-1000"), as_of=_TS_BUY + timedelta(days=1),
    )
    recon_store = ReconciliationStore(db)
    assert recon_store.count() == 1
    recon_store.close()
