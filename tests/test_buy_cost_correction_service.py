"""Phase BUY-FIX — core/services/buy_cost_correction_service.py.

Covers: correction_of must exist AND contain a BUY leg of the same asset,
mode="historical" vs mode="current_time" behaviour, ordering-safety
refusal, and the dedup-collision safety check (mirrors
tests/test_correction_service.py's discovered-and-fixed collision case).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.buy_cost_correction_service import add_buy_cost_correction
from core.services.trade_service import AddTradeInput, add_trade

_TS_BUY = datetime(2026, 9, 9, 21, 22, 39)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def _seed_arb_buy(db_path, ts=_TS_BUY):
    result = add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=ts, base_asset="ARB", base_amount=Decimal("1495.0309551"),
        quote_currency="CZK", quote_amount=Decimal("5000"), venue="revolut",
        fee_amount=Decimal("89"), fee_currency="CZK",
    ))
    return result.rows[0].id


def test_historical_mode_corrects_cost_basis_and_cash(db):
    trade_id = _seed_arb_buy(db)
    added = add_buy_cost_correction(
        db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="total_cash_debited",
        original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        account="Osobní CZK", mode="historical",
    )
    assert len(added) == 2

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()

    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.cost_basis == Decimal("5000")   # 5089 - 89
    assert arb.realized_pnl == Decimal("0")
    assert arb.quantity == Decimal("1495.0309551")

    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("89")

    # Effective timestamp anchored right after the original BUY, not "now".
    marker = next(r for r in rows if r.type == "BUY_COST_CORRECTION" and r.asset == "ARB")
    assert marker.timestamp == _TS_BUY + timedelta(microseconds=1)


def test_current_time_mode_safe_when_fully_open(db):
    trade_id = _seed_arb_buy(db)
    fixed_now = datetime(2026, 9, 19, 12, 0, 0)
    added = add_buy_cost_correction(
        db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="total_cash_debited",
        original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        account="Osobní CZK", mode="current_time", current_timestamp=fixed_now,
    )
    marker = next(r for r in added if r.type == "BUY_COST_CORRECTION" and r.asset == "ARB")
    assert marker.timestamp == fixed_now

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.cost_basis == Decimal("5000")
    assert arb.realized_pnl == Decimal("0")


def test_current_time_mode_rejected_when_not_fully_open(db):
    trade_id = _seed_arb_buy(db)
    # Sell some ARB after the BUY -> position no longer fully open.
    add_trade(db, AddTradeInput(
        type="SELL", timestamp=_TS_BUY + timedelta(days=1), base_asset="ARB",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("40"),
        venue="revolut",
    ))
    with pytest.raises(ValueError, match="fully open"):
        add_buy_cost_correction(
            db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
            cash_delta=Decimal("89"), venue="revolut", reason="r",
            original_value=Decimal("5089"), corrected_value=Decimal("5000"),
            mode="current_time",
        )


def test_historical_mode_still_works_after_a_later_sell(db):
    """mode='historical' must remain correct (and usable) even when the
    fully-open shortcut is unavailable."""
    trade_id = _seed_arb_buy(db)
    add_trade(db, AddTradeInput(
        type="SELL", timestamp=_TS_BUY + timedelta(days=1), base_asset="ARB",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("40"),
        venue="revolut",
    ))
    added = add_buy_cost_correction(
        db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        mode="historical",
    )
    assert len(added) == 2


def test_correction_of_must_exist(db):
    with pytest.raises(ValueError, match="does not reference an existing"):
        add_buy_cost_correction(
            db_path=db, correction_of="nonexistent_id", asset="ARB", currency="CZK",
            cash_delta=Decimal("89"), venue="revolut", reason="r",
            original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        )


def test_correction_of_must_contain_a_buy_leg_of_the_same_asset(db):
    """The type's defining invariant: correction_of must point at a real BUY
    of THIS asset — not just any existing trade id (e.g. not a SELL id, and
    not a BUY of a different asset)."""
    # A SELL trade with no prior BUY (using it just to get a real trade id).
    result = add_trade(db, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
    ))
    btc_buy_id = result.rows[0].id

    with pytest.raises(ValueError, match="BUY leg of asset"):
        add_buy_cost_correction(
            db_path=db, correction_of=btc_buy_id, asset="ARB", currency="CZK",
            cash_delta=Decimal("89"), venue="revolut", reason="r",
            original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        )


def test_ordering_safety_blocks_a_second_same_asset_correction_at_the_same_instant(db):
    """The mandatory ordering-safety check (resolve_historical_effective_timestamp)
    catches a same-asset repeat correction EARLIER and with a clearer message
    than the dedup-collision path ever would — an emergent safety property
    beyond what CORRECTION's (Phase 3C) design had."""
    trade_id = _seed_arb_buy(db)
    add_buy_cost_correction(
        db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("50"), venue="revolut", reason="r1",
        original_value=Decimal("5089"), corrected_value=Decimal("5039"),
        mode="historical",
    )
    with pytest.raises(ValueError, match="Nelze bezpečně odvodit"):
        add_buy_cost_correction(
            db_path=db, correction_of=trade_id, asset="ARB", currency="CZK",
            cash_delta=Decimal("39"), venue="revolut", reason="r2",
            original_value=Decimal("5039"), corrected_value=Decimal("5000"),
            mode="historical",
        )


def test_dedup_collision_across_two_different_assets_raises_clear_error(db):
    """Residual dedup-collision risk NOT covered by the (per-asset-scoped)
    ordering-safety check: two DIFFERENT assets' corrections, at the exact
    same explicit timestamp/venue/currency, with the SAME cash_delta
    magnitude, produce cash legs that are byte-identical on every fingerprint
    field except `id` (which fingerprint() doesn't use — Phase 0 "Varianta
    1") -> the second insert must fail loudly, not silently half-write.
    Mirrors the CORRECTION collision fix from Phase 3C."""
    arb_id = _seed_arb_buy(db)
    btc_result = add_trade(db, AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="revolut",
    ))
    btc_id = btc_result.rows[0].id
    fixed_now = datetime(2026, 9, 19, 12, 0, 0)

    add_buy_cost_correction(
        db_path=db, correction_of=arb_id, asset="ARB", currency="CZK",
        cash_delta=Decimal("50"), venue="revolut", reason="r1",
        original_value=Decimal("5089"), corrected_value=Decimal("5039"),
        mode="current_time", current_timestamp=fixed_now,
    )
    with pytest.raises(ValueError, match="NOT fully written"):
        add_buy_cost_correction(
            db_path=db, correction_of=btc_id, asset="BTC", currency="CZK",
            cash_delta=Decimal("50"), venue="revolut", reason="r2",
            original_value=Decimal("25000"), corrected_value=Decimal("24950"),
            mode="current_time", current_timestamp=fixed_now,
        )
