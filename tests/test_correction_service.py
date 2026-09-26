"""core/services/correction_service.py — add_correction(): validated,
append-only CORRECTION group writer against a real (temp) LedgerStore DB.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.correction_service import add_correction
from core.services.trade_service import AddTradeInput
from core.services.trade_service import add_trade as _core_add_trade

_TS_SELL = datetime(2026, 9, 10, 9, 35, 42)
_TS_CORR = datetime(2026, 9, 19, 13, 0, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def _seed_sell(db_path) -> str:
    """Seed a SELL trade and return its trade_id."""
    inp = AddTradeInput(
        type="SELL", timestamp=_TS_SELL, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3344.25"), venue="revolut",
        fee_amount=Decimal("75"), fee_currency="CZK", account="Osobní CZK",
    )
    result = _core_add_trade(db_path, inp)
    return result.rows[0].id


def test_add_correction_success(db):
    trade_id = _seed_sell(db)
    rows = add_correction(
        db_path=db, timestamp=_TS_CORR, correction_of=trade_id,
        asset="HYPE", currency="CZK", delta=Decimal("75"), venue="revolut",
        reason="revolut_sell_net_gross_fee",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
        account="Osobní CZK",
    )
    assert len(rows) == 2

    store = LedgerStore(db)
    stored = store.get_rows_by_id(rows[0].id)
    store.close()
    assert len(stored) == 2
    assert {r.asset for r in stored} == {"HYPE", "CZK"}


def test_add_correction_rejects_unknown_correction_of(db):
    _seed_sell(db)  # DB has rows, but not this id
    with pytest.raises(ValueError, match="does not reference an existing"):
        add_correction(
            db_path=db, timestamp=_TS_CORR, correction_of="NONEXISTENT_TRADE",
            asset="HYPE", currency="CZK", delta=Decimal("75"), venue="revolut",
            reason="r", original_value=Decimal("1"), corrected_value=Decimal("2"),
        )

    store = LedgerStore(db)
    count_after = store.count()
    store.close()
    # Must fail BEFORE any write — only the original 3 SELL rows remain.
    assert count_after == 3


def test_add_correction_rejects_invalid_group_before_write(db):
    """Zero delta is rejected by build_correction_rows() itself (called
    internally by add_correction() before any DB access) — the important
    guarantee is that NOTHING gets written, regardless of which validation
    layer catches it first."""
    trade_id = _seed_sell(db)
    with pytest.raises(ValueError, match="delta"):
        add_correction(
            db_path=db, timestamp=_TS_CORR, correction_of=trade_id,
            asset="HYPE", currency="CZK", delta=Decimal("0"), venue="revolut",
            reason="r", original_value=Decimal("1"), corrected_value=Decimal("1"),
        )
    store = LedgerStore(db)
    count_after = store.count()
    store.close()
    assert count_after == 3  # nothing written


def test_add_correction_of_a_correction(db):
    """Undo a correction with a NEW correction referencing the first
    correction's own id — the explicitly supported 'wrong correction' fix.

    Uses a DISTINCT timestamp for CORR2 (as any two real, separately-made
    corrections naturally would) — see
    test_second_correction_at_identical_timestamp_raises_clear_error below
    for what happens if they collide, and why."""
    trade_id = _seed_sell(db)
    corr1_rows = add_correction(
        db_path=db, timestamp=_TS_CORR, correction_of=trade_id,
        asset="HYPE", currency="CZK", delta=Decimal("75"), venue="revolut",
        reason="r1", original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    corr1_id = corr1_rows[0].id

    corr2_rows = add_correction(
        db_path=db, timestamp=_TS_CORR.replace(minute=1), correction_of=corr1_id,
        asset="HYPE", currency="CZK", delta=Decimal("-75"), venue="revolut",
        reason="undo_wrong_correction",
        original_value=Decimal("3419.25"), corrected_value=Decimal("3344.25"),
    )
    assert len(corr2_rows) == 2

    from core.reports.positions import compute_positions
    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    hype = next(p for p in compute_positions(rows) if p.asset == "HYPE")
    # No prior BUY was seeded, so cost_removed=0 for this SELL; baseline
    # realized_pnl = proceeds(3344.25) - cost_removed(0) - fee(75) = 3269.25.
    # CORR1 (+75) -> 3344.25, CORR2 (-75) -> back to exactly 3269.25.
    assert hype.realized_pnl == Decimal("3269.25")  # net of the two corrections is zero


def test_second_correction_at_identical_timestamp_raises_clear_error(db):
    """DISCOVERED DURING TESTING (documented, not silently worked around —
    Phase 0 explicitly decided to keep the legacy fingerprint/row_fp
    unchanged and accept this narrow collision risk).

    A CORRECTION marker leg's shape is minimal: (timestamp, type=CORRECTION,
    venue, asset, currency, amount=0) — every correction of the SAME
    asset/venue/currency has an IDENTICAL marker shape regardless of which
    logical correction it belongs to. If two DIFFERENT corrections share the
    exact same timestamp, their marker legs collide on the legacy row_fp
    (which is id-blind by design) and the second one is silently dropped by
    plain import_rows(). add_correction() uses insert_pair() specifically to
    catch this and raise loudly instead of silently half-writing."""
    trade_id = _seed_sell(db)
    add_correction(
        db_path=db, timestamp=_TS_CORR, correction_of=trade_id,
        asset="HYPE", currency="CZK", delta=Decimal("75"), venue="revolut",
        reason="r1", original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )

    with pytest.raises(ValueError, match="NOT fully written"):
        add_correction(
            db_path=db, timestamp=_TS_CORR,  # SAME timestamp as CORR1 -> collision
            correction_of=trade_id,
            asset="HYPE", currency="CZK", delta=Decimal("10"), venue="revolut",
            reason="a_different_correction", original_value=Decimal("1"), corrected_value=Decimal("11"),
        )
