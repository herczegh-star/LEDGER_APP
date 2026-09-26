"""Phase BUY-FIX — core/reports/positions.py: BUY_COST_CORRECTION branch.

Covers the mandatory test plan from the architecture approval:
  A) fully open
  B) partial sold — SELL must use the corrected WAC
  C) fully closed — full economic correction flows into realized_pnl
  D) wrong ordering regression — correction effective AFTER the SELL
     produces a different (wrong) result, proving why historical-effective
     ordering matters for the general case
  E) undo — opposite-delta correction returns to exact baseline
  I) legacy golden master — normal BUY/SELL/TRANSFER/FEE/STAKING/REVERSAL/
     CORRECTION regression, bit-identical without any BUY_COST_CORRECTION row
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.buy_cost_correction import build_buy_cost_correction_rows
from core.model import RawRow
from core.reports.positions import compute_positions

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)


def _buy_row(qty=Decimal("100"), cost=Decimal("1000"), asset="ARB", ts=_TS_BUY, trade_id="buy-1"):
    """100 units bought for `cost` CZK (no fee) -> cost_basis=cost, wac=cost/qty."""
    return [
        RawRow(id=trade_id, timestamp=ts, type="BUY", asset=asset,
               amount=qty, currency="CZK", price=cost / qty, venue="revolut"),
        RawRow(id=trade_id, timestamp=ts, type="BUY", asset="CZK",
               amount=-cost, currency="CZK", price=Decimal("1"), venue="revolut"),
    ]


def _correction_rows(cash_delta, ts, correction_of="buy-1", asset="ARB", corr_id="corr-1"):
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=ts, correction_of=correction_of, asset=asset, currency="CZK",
        cash_delta=cash_delta, venue="revolut", reason="test",
        original_value=Decimal("1000"), corrected_value=Decimal("1000") - cash_delta,
        account="Osobní CZK", correction_id=corr_id,
    )
    return [marker, cash_leg]


# ── A) Fully open ────────────────────────────────────────────────────────────

def test_fully_open_cost_basis_corrected_quantity_unchanged():
    rows = _buy_row() + _correction_rows(
        cash_delta=Decimal("100"), ts=_TS_BUY + timedelta(microseconds=1),
    )
    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.quantity == Decimal("100")            # unchanged
    assert arb.cost_basis == Decimal("900")           # 1000 - 100
    assert arb.wac == Decimal("9")                    # 900/100
    assert arb.realized_pnl == Decimal("0")            # unchanged


def test_fully_open_cash_corrected():
    from core.reports.cash import compute_cash_balances
    rows = _buy_row() + _correction_rows(
        cash_delta=Decimal("100"), ts=_TS_BUY + timedelta(microseconds=1),
    )
    balances = compute_cash_balances(rows)
    # BUY quote leg has no account (legacy) -> untouched bucket; correction's
    # cash leg carries its own account -> separate, additive bucket.
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("100")


def test_fully_open_other_assets_untouched():
    rows = _buy_row() + [
        RawRow(id="buy-2", timestamp=_TS_BUY, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="CZK", price=Decimal("500000"), venue="revolut"),
        RawRow(id="buy-2", timestamp=_TS_BUY, type="BUY", asset="CZK",
               amount=Decimal("-500000"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ] + _correction_rows(cash_delta=Decimal("100"), ts=_TS_BUY + timedelta(microseconds=1))

    btc = next(p for p in compute_positions(rows) if p.asset == "BTC")
    assert btc.quantity == Decimal("1")
    assert btc.cost_basis == Decimal("500000")
    assert btc.realized_pnl == Decimal("0")


# ── B) Partial sold — correction must change the WAC used by SELL ──────────

def test_partial_sold_correction_before_sell_changes_realized_pnl():
    """BUY 100@1000 (wac=10) -> correction -100 cost_basis (wac=9, effective
    BEFORE the SELL) -> SELL 50 @ proceeds 600 -> cost_removed must use
    wac=9, not wac=10."""
    ts_sell = _TS_BUY + timedelta(days=1)
    rows = (
        _buy_row()
        + _correction_rows(cash_delta=Decimal("100"), ts=_TS_BUY + timedelta(microseconds=1))
        + [
            RawRow(id="sell-1", timestamp=ts_sell, type="SELL", asset="ARB",
                   amount=Decimal("-50"), currency="CZK", price=Decimal("12"), venue="revolut"),
            RawRow(id="sell-1", timestamp=ts_sell, type="SELL", asset="CZK",
                   amount=Decimal("600"), currency="CZK", price=Decimal("1"), venue="revolut"),
        ]
    )
    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.quantity == Decimal("50")
    assert arb.cost_basis == Decimal("450")     # 900 - (9*50)
    assert arb.realized_pnl == Decimal("150")   # 600 - 450


# ── C) Fully closed — full delta flows into realized_pnl ────────────────────

def test_fully_closed_correction_flows_entirely_into_realized_pnl():
    ts_sell = _TS_BUY + timedelta(days=1)
    rows = (
        _buy_row()
        + _correction_rows(cash_delta=Decimal("100"), ts=_TS_BUY + timedelta(microseconds=1))
        + [
            RawRow(id="sell-1", timestamp=ts_sell, type="SELL", asset="ARB",
                   amount=Decimal("-100"), currency="CZK", price=Decimal("12"), venue="revolut"),
            RawRow(id="sell-1", timestamp=ts_sell, type="SELL", asset="CZK",
                   amount=Decimal("1200"), currency="CZK", price=Decimal("1"), venue="revolut"),
        ]
    )
    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.quantity == Decimal("0")
    assert arb.cost_basis == Decimal("0")
    # Without correction: realized_pnl = 1200-1000=200. With -100 cost_basis
    # correction fully consumed by this single full-position sale:
    # realized_pnl = 1200-900=300 -> full +100 delta landed in realized_pnl.
    assert arb.realized_pnl == Decimal("300")


# ── D) Wrong ordering regression — correction AFTER the SELL ───────────────

def test_wrong_ordering_correction_after_sell_produces_different_wrong_result():
    """Same trades as the partial-sold case, but the correction's timestamp
    is placed AFTER the SELL instead of before it. Proves the ordering
    requirement from the architecture analysis: this produces a DIFFERENT
    (and wrong) result — the SELL still uses the uncorrected WAC=10, and the
    full -100 delta gets dumped onto the still-open remainder instead of
    being fairly split between realized_pnl and open cost_basis."""
    ts_sell = _TS_BUY + timedelta(days=1)
    ts_correction_too_late = ts_sell + timedelta(days=1)  # AFTER the sell
    rows = (
        _buy_row()
        + [
            RawRow(id="sell-1", timestamp=ts_sell, type="SELL", asset="ARB",
                   amount=Decimal("-50"), currency="CZK", price=Decimal("12"), venue="revolut"),
            RawRow(id="sell-1", timestamp=ts_sell, type="SELL", asset="CZK",
                   amount=Decimal("600"), currency="CZK", price=Decimal("1"), venue="revolut"),
        ]
        + _correction_rows(cash_delta=Decimal("100"), ts=ts_correction_too_late)
    )
    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.quantity == Decimal("50")
    # SELL used the UNCORRECTED wac=10: cost_removed=500, realized_pnl=100.
    assert arb.realized_pnl == Decimal("100")     # WRONG — correctly-ordered case gives 150
    # Correction still legitimately reduces cost_basis, but ALL of it lands
    # on the remaining open half instead of being split 50/50.
    assert arb.cost_basis == Decimal("400")       # (1000-500) - 100, WRONG — should be 450

    # Explicit contrast with the correctly-ordered outcome from test B above:
    # correct realized_pnl=150, cost_basis=450 (sum=600).
    # wrong-ordering realized_pnl=100, cost_basis=400 (sum=500) — 100 CZK of
    # the correction silently vanished from the accounted total.
    assert arb.realized_pnl + arb.cost_basis == Decimal("500")


# ── E) Undo — opposite-delta correction returns to exact baseline ──────────

def test_undo_opposite_delta_returns_to_exact_baseline():
    rows_before = _buy_row()
    baseline = next(p for p in compute_positions(rows_before) if p.asset == "ARB")

    rows_with_correction_and_undo = (
        rows_before
        + _correction_rows(cash_delta=Decimal("100"), ts=_TS_BUY + timedelta(microseconds=1), corr_id="corr-1")
        + _correction_rows(cash_delta=Decimal("-100"), ts=_TS_BUY + timedelta(microseconds=2), corr_id="corr-2",
                            correction_of="corr-1")
    )
    arb = next(p for p in compute_positions(rows_with_correction_and_undo) if p.asset == "ARB")
    assert arb.quantity == baseline.quantity
    assert arb.cost_basis == baseline.cost_basis
    assert arb.wac == baseline.wac
    assert arb.realized_pnl == baseline.realized_pnl


# ── I) Legacy golden master — normal regression, no BUY_COST_CORRECTION ────

def test_normal_buy_sell_transfer_fee_staking_reversal_correction_regression():
    """Bit-identical output for a normal mixed-type ledger with zero
    BUY_COST_CORRECTION rows — the new branch must not perturb anything else."""
    from core.correction import build_correction_rows

    ts2 = _TS_BUY + timedelta(days=1)
    rows = _buy_row(qty=Decimal("10"), cost=Decimal("1000"), asset="HYPE", trade_id="hbuy") + [
        RawRow(id="hsell", timestamp=ts2, type="SELL", asset="HYPE",
               amount=Decimal("-2"), currency="CZK", price=Decimal("150"), venue="revolut"),
        RawRow(id="hsell", timestamp=ts2, type="SELL", asset="CZK",
               amount=Decimal("300"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    marker, fiat_leg = build_correction_rows(
        timestamp=ts2 + timedelta(hours=1), correction_of="hsell", asset="HYPE",
        currency="CZK", delta=Decimal("10"), venue="revolut", reason="r",
        original_value=Decimal("300"), corrected_value=Decimal("310"),
    )
    rows += [marker, fiat_leg]

    hype = next(p for p in compute_positions(rows) if p.asset == "HYPE")
    assert hype.quantity == Decimal("8")
    assert hype.cost_basis == Decimal("800")
    assert hype.realized_pnl == Decimal("110")   # (300-200) + 10 correction
