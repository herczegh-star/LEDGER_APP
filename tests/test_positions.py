"""Tests for core/reports/positions.py (WAC-based positions engine)."""
from datetime import datetime
from decimal import Decimal
from typing import List

import pytest

from core.model import RawRow
from core.reports.positions import compute_positions, PositionRow


# ── Helpers ───────────────────────────────────────────────────────────────────

_TS = datetime(2026, 1, 1, 12, 0, 0)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, 0)


def make_buy(
    trade_id: str,
    asset: str,
    base_amount: Decimal,
    quote_currency: str,
    quote_amount: Decimal,
    venue: str = "test",
    ts: datetime = _TS,
    fee_amount: Decimal | None = None,
    fee_currency: str | None = None,
) -> List[RawRow]:
    """Return 2 (or 3) RawRow objects for a BUY trade."""
    price = quote_amount / base_amount
    rows = [
        RawRow(
            id=trade_id,
            timestamp=ts,
            type="BUY",
            asset=asset.upper(),
            amount=base_amount,
            currency=quote_currency.upper(),
            price=price,
            venue=venue,
        ),
        RawRow(
            id=trade_id,
            timestamp=ts,
            type="BUY",
            asset=quote_currency.upper(),
            amount=-quote_amount,
            currency=quote_currency.upper(),
            price=Decimal("1"),
            venue=venue,
        ),
    ]
    if fee_amount is not None:
        fee_asset = (fee_currency or quote_currency).upper()
        rows.append(RawRow(
            id=trade_id,
            timestamp=ts,
            type="FEE",
            asset=fee_asset,
            amount=-fee_amount,
            currency=fee_asset,
            price=Decimal("1"),
            venue=venue,
        ))
    return rows


def make_sell(
    trade_id: str,
    asset: str,
    base_amount: Decimal,
    quote_currency: str,
    quote_amount: Decimal,
    venue: str = "test",
    ts: datetime = _TS,
    fee_amount: Decimal | None = None,
    fee_currency: str | None = None,
) -> List[RawRow]:
    """Return 2 (or 3) RawRow objects for a SELL trade."""
    price = quote_amount / base_amount
    rows = [
        RawRow(
            id=trade_id,
            timestamp=ts,
            type="SELL",
            asset=asset.upper(),
            amount=-base_amount,
            currency=quote_currency.upper(),
            price=price,
            venue=venue,
        ),
        RawRow(
            id=trade_id,
            timestamp=ts,
            type="SELL",
            asset=quote_currency.upper(),
            amount=quote_amount,
            currency=quote_currency.upper(),
            price=Decimal("1"),
            venue=venue,
        ),
    ]
    if fee_amount is not None:
        fee_asset = (fee_currency or quote_currency).upper()
        rows.append(RawRow(
            id=trade_id,
            timestamp=ts,
            type="FEE",
            asset=fee_asset,
            amount=-fee_amount,
            currency=fee_asset,
            price=Decimal("1"),
            venue=venue,
        ))
    return rows


def _get(positions: List[PositionRow], asset: str) -> PositionRow:
    return next(p for p in positions if p.asset == asset.upper())


# ── 1. Simple BUY ─────────────────────────────────────────────────────────────

def test_simple_buy_quantity():
    rows = make_buy("t1", "BTC", Decimal("0.5"), "EUR", Decimal("25000"))
    result = compute_positions(rows)
    pos = _get(result, "BTC")
    assert pos.quantity == Decimal("0.5")


def test_simple_buy_cost_basis():
    rows = make_buy("t1", "BTC", Decimal("0.5"), "EUR", Decimal("25000"))
    result = compute_positions(rows)
    pos = _get(result, "BTC")
    assert pos.cost_basis == Decimal("25000")


def test_simple_buy_wac():
    rows = make_buy("t1", "BTC", Decimal("0.5"), "EUR", Decimal("25000"))
    result = compute_positions(rows)
    pos = _get(result, "BTC")
    # WAC = 25000 / 0.5 = 50000
    assert pos.wac == Decimal("50000")


def test_simple_buy_no_realized_pnl():
    rows = make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"))
    result = compute_positions(rows)
    pos = _get(result, "BTC")
    assert pos.realized_pnl == Decimal("0")


def test_fiat_not_in_positions():
    rows = make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"))
    result = compute_positions(rows)
    assets = {p.asset for p in result}
    assert "EUR" not in assets
    assert "CZK" not in assets


# ── 2. Two BUYs → correct WAC recalculation ───────────────────────────────────

def test_two_buys_quantity():
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.quantity == Decimal("2")


def test_two_buys_wac():
    # First buy: 1 BTC @ 40000 EUR  → cost_basis = 40000
    # Second buy: 1 BTC @ 50000 EUR → cost_basis = 90000
    # WAC = 90000 / 2 = 45000
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.cost_basis == Decimal("90000")
    assert pos.wac == Decimal("45000")


# ── 3. Partial SELL → realized_pnl + new quantity ─────────────────────────────

def test_partial_sell_quantity():
    rows = (
        make_buy("t1", "BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("55000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.quantity == Decimal("1")


def test_partial_sell_cost_basis():
    # Buy 2 BTC @ 40000 WAC → cost_basis = 80000
    # Sell 1 BTC → cost_removed = 40000
    # Remaining cost_basis = 40000
    rows = (
        make_buy("t1", "BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("55000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.cost_basis == Decimal("40000")


def test_partial_sell_realized_pnl():
    # Sell 1 BTC for 55000, WAC cost_removed = 40000, no fee
    # realized_pnl = 55000 - 40000 = 15000
    rows = (
        make_buy("t1", "BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("55000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.realized_pnl == Decimal("15000")


# ── 4. Full SELL → quantity zero, cost_basis zero ────────────────────────────

def test_full_sell_quantity_zero():
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.quantity == Decimal("0")


def test_full_sell_cost_basis_zero():
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.cost_basis == Decimal("0")


def test_full_sell_realized_pnl():
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.realized_pnl == Decimal("10000")


def test_full_sell_included_in_output():
    """Closed positions (quantity=0) must still appear in output."""
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    result = compute_positions(rows)
    assets = {p.asset for p in result}
    assert "BTC" in assets


# ── 5. BUY + SELL with fiat fee ───────────────────────────────────────────────

def test_buy_fee_increases_cost_basis():
    # Buy 1 BTC for 40000 EUR + 50 EUR fee → cost_basis = 40050
    rows = make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"),
                    ts=_ts(1), fee_amount=Decimal("50"), fee_currency="EUR")
    pos = _get(compute_positions(rows), "BTC")
    assert pos.cost_basis == Decimal("40050")


def test_buy_fee_wac():
    # WAC = 40050 / 1 = 40050
    rows = make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"),
                    ts=_ts(1), fee_amount=Decimal("50"), fee_currency="EUR")
    pos = _get(compute_positions(rows), "BTC")
    assert pos.wac == Decimal("40050")


def test_sell_fee_reduces_realized_pnl():
    # Buy 1 BTC @ 40000 (no fee)
    # Sell 1 BTC @ 50000, fee = 100 EUR
    # realized_pnl = 50000 - 40000 - 100 = 9900
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"),
                    ts=_ts(2), fee_amount=Decimal("100"), fee_currency="EUR")
    )
    pos = _get(compute_positions(rows), "BTC")
    assert pos.realized_pnl == Decimal("9900")


def test_non_fiat_fee_ignored():
    """A fee paid in the base asset (non-fiat) must NOT affect cost_basis."""
    rows = make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"),
                    ts=_ts(1), fee_amount=Decimal("0.001"), fee_currency="BTC")
    pos = _get(compute_positions(rows), "BTC")
    # Non-fiat fee → fee_by_id should NOT accumulate (BTC not in fiat set)
    assert pos.cost_basis == Decimal("40000")


# ── 6. Multiple assets independent ────────────────────────────────────────────

def test_multiple_assets_both_present():
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("t2", "ETH", Decimal("10"), "EUR", Decimal("30000"), ts=_ts(2))
    )
    result = compute_positions(rows)
    assets = {p.asset for p in result}
    assert "BTC" in assets
    assert "ETH" in assets


def test_multiple_assets_independent_quantities():
    rows = (
        make_buy("t1", "BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_buy("t2", "ETH", Decimal("5"), "EUR", Decimal("15000"), ts=_ts(2))
    )
    result = compute_positions(rows)
    btc = _get(result, "BTC")
    eth = _get(result, "ETH")
    assert btc.quantity == Decimal("2")
    assert eth.quantity == Decimal("5")


def test_multiple_assets_sell_only_affects_target():
    rows = (
        make_buy("t1", "BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_buy("t2", "ETH", Decimal("5"), "EUR", Decimal("15000"), ts=_ts(2))
        + make_sell("t3", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(3))
    )
    result = compute_positions(rows)
    btc = _get(result, "BTC")
    eth = _get(result, "ETH")
    assert btc.quantity == Decimal("1")
    assert eth.quantity == Decimal("5")   # untouched
    assert eth.realized_pnl == Decimal("0")


# ── 7. Deterministic ordering ─────────────────────────────────────────────────

def test_deterministic_ordering_reversed_input():
    """Result must be the same regardless of input row order."""
    rows_forward = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    rows_reversed = list(reversed(rows_forward))

    result_f = compute_positions(rows_forward)
    result_r = compute_positions(rows_reversed)

    pos_f = _get(result_f, "BTC")
    pos_r = _get(result_r, "BTC")
    assert pos_f.quantity == pos_r.quantity
    assert pos_f.cost_basis == pos_r.cost_basis
    assert pos_f.realized_pnl == pos_r.realized_pnl


def test_output_sorted_alphabetically():
    rows = (
        make_buy("t1", "ETH", Decimal("5"), "EUR", Decimal("15000"), ts=_ts(1))
        + make_buy("t2", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(2))
    )
    result = compute_positions(rows)
    assert [p.asset for p in result] == sorted(p.asset for p in result)


# ── 8. Edge cases ─────────────────────────────────────────────────────────────

def test_empty_rows_returns_empty():
    assert compute_positions([]) == []


def test_oversell_yields_negative_quantity():
    """Over-sell must not raise — the position goes negative (diagnostic indicator)."""
    rows = (
        make_buy("t1", "BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("t2", "BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(2))
    )
    result = compute_positions(rows)
    btc = next(p for p in result if p.asset == "BTC")
    assert btc.quantity == Decimal("-1")


def test_custom_fiat_set():
    """Passing a custom fiat set should use it correctly."""
    rows = make_buy("t1", "ETH", Decimal("1"), "USD", Decimal("3000"))
    # With USD as fiat, ETH should be position; otherwise USD would be too
    result = compute_positions(rows, fiat=frozenset({"USD"}))
    assets = {p.asset for p in result}
    assert "ETH" in assets
    assert "USD" not in assets
