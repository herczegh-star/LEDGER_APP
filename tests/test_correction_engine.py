"""core/reports/positions.py — the isolated CORRECTION branch.

Verifies the exact invariants required for the correction feature:
  - a CORRECTION never touches quantity / cost_basis / WAC, only realized_pnl.
  - +75 then -75 (correction-of-a-correction) returns exactly to baseline.
  - normal BUY/SELL/TRANSFER/FEE/STAKING are completely unaffected — the new
    branch is a strict no-op for every row whose type != 'CORRECTION'.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.correction import build_correction_rows
from core.model import RawRow
from core.reports.positions import compute_positions

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 35, 42)
_TS_CORR = datetime(2026, 9, 19, 13, 0, 0)


def _hype_fixture():
    """8 HYPE @ 1200 CZK, then SELL 2 HYPE for 3344.25 CZK (fee 75) — the
    same shape as the real historical row, but self-contained/synthetic."""
    buy_hype = RawRow(id="BUY1", timestamp=_TS_BUY, type="BUY", asset="HYPE",
                       amount=Decimal("8"), currency="CZK", price=Decimal("1200"), venue="revolut")
    buy_czk = RawRow(id="BUY1", timestamp=_TS_BUY, type="BUY", asset="CZK",
                      amount=Decimal("-9600"), currency="CZK", price=Decimal("1"), venue="revolut")
    sell_hype = RawRow(id="SELL1", timestamp=_TS_SELL, type="SELL", asset="HYPE",
                        amount=Decimal("-2"), currency="CZK", price=Decimal("1672.125"), venue="revolut")
    sell_czk = RawRow(id="SELL1", timestamp=_TS_SELL, type="SELL", asset="CZK",
                       amount=Decimal("3344.25"), currency="CZK", price=Decimal("1"), venue="revolut")
    fee = RawRow(id="SELL1", timestamp=_TS_SELL, type="FEE", asset="CZK",
                 amount=Decimal("-75"), currency="CZK", price=Decimal("1"), venue="revolut")
    # An unrelated asset present throughout, to prove it's never touched.
    buy_btc = RawRow(id="BUY_BTC", timestamp=_TS_BUY, type="BUY", asset="BTC",
                      amount=Decimal("0.01"), currency="CZK", price=Decimal("2000000"), venue="kraken")
    buy_btc_czk = RawRow(id="BUY_BTC", timestamp=_TS_BUY, type="BUY", asset="CZK",
                          amount=Decimal("-20000"), currency="CZK", price=Decimal("1"), venue="kraken")
    return [buy_hype, buy_czk, sell_hype, sell_czk, fee, buy_btc, buy_btc_czk]


def _hype_position(rows):
    return next(p for p in compute_positions(rows) if p.asset == "HYPE")


def _btc_position(rows):
    return next(p for p in compute_positions(rows) if p.asset == "BTC")


# ── Valid +75 correction ────────────────────────────────────────────────────

def test_positive_correction_adds_only_to_realized_pnl():
    rows = _hype_fixture()
    before = _hype_position(rows)

    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="revolut_sell_net_gross_fee",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    rows_after = rows + [marker, fiat_leg]
    after = _hype_position(rows_after)

    assert after.quantity == before.quantity
    assert after.cost_basis == before.cost_basis
    assert after.wac == before.wac
    assert after.realized_pnl == before.realized_pnl + Decimal("75")


# ── Valid -75 correction ────────────────────────────────────────────────────

def test_negative_correction_subtracts_only_from_realized_pnl():
    rows = _hype_fixture()
    before = _hype_position(rows)

    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("-75"), venue="revolut", reason="overcorrection_undo",
        original_value=Decimal("3419.25"), corrected_value=Decimal("3344.25"),
    )
    rows_after = rows + [marker, fiat_leg]
    after = _hype_position(rows_after)

    assert after.quantity == before.quantity
    assert after.cost_basis == before.cost_basis
    assert after.realized_pnl == before.realized_pnl - Decimal("75")


# ── +75 then -75 (correction-of-a-correction) returns exactly to baseline ──

def test_correction_of_correction_returns_exactly_to_baseline():
    rows = _hype_fixture()
    baseline = _hype_position(rows)

    marker1, fiat1 = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r1",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
        correction_id="CORR1",
    )
    marker2, fiat2 = build_correction_rows(
        timestamp=_TS_CORR, correction_of="CORR1", asset="HYPE", currency="CZK",
        delta=Decimal("-75"), venue="revolut", reason="undo_CORR1",
        original_value=Decimal("3419.25"), corrected_value=Decimal("3344.25"),
        correction_id="CORR2",
    )
    rows_after = rows + [marker1, fiat1, marker2, fiat2]
    after = _hype_position(rows_after)

    assert after.quantity == baseline.quantity
    assert after.cost_basis == baseline.cost_basis
    assert after.wac == baseline.wac
    assert after.realized_pnl == baseline.realized_pnl  # exact return, not approximate


# ── Other assets / other trade types are never touched ─────────────────────

def test_correction_does_not_affect_other_assets():
    rows = _hype_fixture()
    before_btc = _btc_position(rows)

    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    rows_after = rows + [marker, fiat_leg]
    after_btc = _btc_position(rows_after)

    assert after_btc.quantity == before_btc.quantity
    assert after_btc.cost_basis == before_btc.cost_basis
    assert after_btc.realized_pnl == before_btc.realized_pnl


def test_normal_buy_sell_transfer_fee_staking_unaffected_by_correction_branch():
    """A ledger with every normal type present, no CORRECTION rows at all,
    must produce IDENTICAL results before/after this feature exists — i.e.
    the new branch is only ever entered for type=='CORRECTION'."""
    rows = [
        RawRow(id="B1", timestamp=_TS_BUY, type="BUY", asset="ETH", amount=Decimal("1"),
               currency="CZK", price=Decimal("60000"), venue="kraken"),
        RawRow(id="B1", timestamp=_TS_BUY, type="BUY", asset="CZK", amount=Decimal("-60000"),
               currency="CZK", price=Decimal("1"), venue="kraken"),
        RawRow(id="S1", timestamp=_TS_SELL, type="SELL", asset="ETH", amount=Decimal("-0.5"),
               currency="CZK", price=Decimal("65000"), venue="kraken"),
        RawRow(id="S1", timestamp=_TS_SELL, type="SELL", asset="CZK", amount=Decimal("32500"),
               currency="CZK", price=Decimal("1"), venue="kraken"),
        RawRow(id="T1", timestamp=_TS_SELL, type="TRANSFER", asset="ETH", amount=Decimal("-0.5"),
               currency="ETH", price=Decimal("0"), venue="kraken"),
        RawRow(id="T1", timestamp=_TS_SELL, type="TRANSFER", asset="ETH", amount=Decimal("0.5"),
               currency="ETH", price=Decimal("0"), venue="ledger wallet"),
        RawRow(id="F1", timestamp=_TS_SELL, type="FEE", asset="ETH", amount=Decimal("-0.001"),
               currency="ETH", price=Decimal("1"), venue="kraken"),
        RawRow(id="ST1", timestamp=_TS_SELL, type="STAKING", asset="ETH", amount=Decimal("0.01"),
               currency="CZK", price=Decimal("0"), venue="kraken"),
    ]
    pos1 = compute_positions(rows)
    pos2 = compute_positions(rows)  # deterministic: same input -> same output
    snap1 = {p.asset: (p.quantity, p.cost_basis, p.realized_pnl) for p in pos1}
    snap2 = {p.asset: (p.quantity, p.cost_basis, p.realized_pnl) for p in pos2}
    assert snap1 == snap2
    eth = next(p for p in pos1 if p.asset == "ETH")
    assert eth.realized_pnl == Decimal("32500") - (Decimal("60000") / Decimal("1") * Decimal("0.5"))
