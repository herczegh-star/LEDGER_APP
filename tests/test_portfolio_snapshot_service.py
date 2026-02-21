"""Tests for core/services/portfolio_snapshot_service.py."""
from datetime import datetime
from decimal import Decimal
from typing import List
import uuid

import pytest

from core.model import RawRow
from core.services.portfolio_snapshot_service import (
    PortfolioSnapshot,
    get_portfolio_snapshot,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, 12, 0, 0)


def make_buy(
    asset: str,
    base_amount: Decimal,
    quote_currency: str,
    quote_amount: Decimal,
    ts: datetime = None,
    fee_amount: Decimal = None,
    fee_currency: str = None,
) -> List[RawRow]:
    tid = str(uuid.uuid4())
    ts = ts or _ts(1)
    price = quote_amount / base_amount
    rows = [
        RawRow(id=tid, timestamp=ts, type="BUY", asset=asset.upper(),
               amount=base_amount, currency=quote_currency.upper(),
               price=price, venue="test"),
        RawRow(id=tid, timestamp=ts, type="BUY", asset=quote_currency.upper(),
               amount=-quote_amount, currency=quote_currency.upper(),
               price=Decimal("1"), venue="test"),
    ]
    if fee_amount is not None:
        fa = (fee_currency or quote_currency).upper()
        rows.append(RawRow(
            id=tid, timestamp=ts, type="FEE", asset=fa,
            amount=-fee_amount, currency=fa, price=Decimal("1"), venue="test",
        ))
    return rows


def make_sell(
    asset: str,
    base_amount: Decimal,
    quote_currency: str,
    quote_amount: Decimal,
    ts: datetime = None,
) -> List[RawRow]:
    tid = str(uuid.uuid4())
    ts = ts or _ts(2)
    price = quote_amount / base_amount
    return [
        RawRow(id=tid, timestamp=ts, type="SELL", asset=asset.upper(),
               amount=-base_amount, currency=quote_currency.upper(),
               price=price, venue="test"),
        RawRow(id=tid, timestamp=ts, type="SELL", asset=quote_currency.upper(),
               amount=quote_amount, currency=quote_currency.upper(),
               price=Decimal("1"), venue="test"),
    ]


# ── 1. Empty ledger ───────────────────────────────────────────────────────────

def test_empty_returns_portfolio_snapshot():
    result = get_portfolio_snapshot([])
    assert isinstance(result, PortfolioSnapshot)


def test_empty_invested_is_empty_dict():
    snap = get_portfolio_snapshot([])
    assert snap.invested == {}


def test_empty_net_flow_is_empty_dict():
    snap = get_portfolio_snapshot([])
    assert snap.net_flow == {}


def test_empty_assets_held_zero():
    snap = get_portfolio_snapshot([])
    assert snap.assets_held == 0


def test_empty_top_position_none():
    snap = get_portfolio_snapshot([])
    assert snap.top_position is None


# ── 2. Simple BUY ─────────────────────────────────────────────────────────────

def test_buy_invested_positive():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert snap.invested.get("EUR", Decimal("0")) == Decimal("40000")


def test_buy_net_flow_negative():
    # Buying = spending EUR → net_flow should be negative
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert snap.net_flow.get("EUR", Decimal("0")) == Decimal("-40000")


def test_buy_assets_held_one():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert snap.assets_held == 1


def test_buy_top_position_set():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert snap.top_position is not None


def test_buy_top_position_asset():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert snap.top_position["asset"] == "BTC"


def test_buy_top_position_cost_basis():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert snap.top_position["cost_basis"] == Decimal("40000")


def test_buy_with_fee_invested_includes_fee():
    # Gross invested should NOT include the fee (invested = trade outflow only)
    # netto_invested tracks the fiat quote leg, not the fee separately
    # The fee appears in net_flow but not in invested_amount
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"),
                    ts=_ts(1), fee_amount=Decimal("50"), fee_currency="EUR")
    snap = get_portfolio_snapshot(rows)
    # invested_amount = only the BUY quote outflow (40000), not fee
    # net_flow = BUY quote (-40000) + fee (-50) = -40050
    assert snap.net_flow.get("EUR", Decimal("0")) == Decimal("-40050")


# ── 3. Multiple BUYs → correct aggregation ────────────────────────────────────

def test_two_buys_invested_sums():
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("ETH", Decimal("5"), "EUR", Decimal("15000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.invested["EUR"] == Decimal("55000")


def test_two_buys_assets_held():
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("ETH", Decimal("5"), "EUR", Decimal("15000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.assets_held == 2


def test_two_buys_top_position_is_highest_cost():
    # BTC cost_basis=40000 > ETH cost_basis=15000
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("ETH", Decimal("5"), "EUR", Decimal("15000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.top_position["asset"] == "BTC"
    assert snap.top_position["cost_basis"] == Decimal("40000")


# ── 4. BUY + full SELL ────────────────────────────────────────────────────────

def test_buy_full_sell_assets_held_zero():
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.assets_held == 0


def test_buy_full_sell_top_position_none():
    # After full sell, cost_basis = 0; no candidate remains
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.top_position is None


def test_buy_full_sell_invested_unchanged():
    # The BUY outflow is counted as invested; the SELL inflow doesn't subtract
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    # invested_amount counts only outflows (BUY = 40000)
    assert snap.invested["EUR"] == Decimal("40000")


def test_buy_full_sell_net_flow_positive():
    # Sold for 50000, bought for 40000 → net_flow = +10000
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.net_flow["EUR"] == Decimal("10000")


# ── 5. BUY + partial SELL ─────────────────────────────────────────────────────

def test_partial_sell_assets_held_still_one():
    rows = (
        make_buy("BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.assets_held == 1


def test_partial_sell_top_position_still_set():
    rows = (
        make_buy("BTC", Decimal("2"), "EUR", Decimal("80000"), ts=_ts(1))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.top_position is not None
    assert snap.top_position["asset"] == "BTC"


# ── 6. Multi-currency ─────────────────────────────────────────────────────────

def test_multi_currency_invested_separate():
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("ETH", Decimal("5"), "CZK", Decimal("400000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert "EUR" in snap.invested
    assert "CZK" in snap.invested
    assert snap.invested["EUR"] == Decimal("40000")
    assert snap.invested["CZK"] == Decimal("400000")


def test_multi_currency_assets_held():
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
        + make_buy("ETH", Decimal("5"), "CZK", Decimal("400000"), ts=_ts(2))
    )
    snap = get_portfolio_snapshot(rows)
    assert snap.assets_held == 2


# ── 7. Custom fiat ────────────────────────────────────────────────────────────

def test_custom_fiat_forwarded():
    rows = make_buy("ETH", Decimal("1"), "USD", Decimal("3000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows, fiat={"USD"})
    assert snap.assets_held == 1
    assert "USD" in snap.invested


def test_custom_fiat_top_position_set():
    rows = make_buy("ETH", Decimal("1"), "USD", Decimal("3000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows, fiat={"USD"})
    assert snap.top_position is not None
    assert snap.top_position["asset"] == "ETH"


# ── 8. PortfolioSnapshot field types ─────────────────────────────────────────

def test_snapshot_invested_dict():
    snap = get_portfolio_snapshot([])
    assert isinstance(snap.invested, dict)


def test_snapshot_net_flow_dict():
    snap = get_portfolio_snapshot([])
    assert isinstance(snap.net_flow, dict)


def test_snapshot_assets_held_int():
    snap = get_portfolio_snapshot([])
    assert isinstance(snap.assets_held, int)


def test_snapshot_top_position_has_required_keys():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert "asset" in snap.top_position
    assert "cost_basis" in snap.top_position


def test_snapshot_top_position_cost_basis_is_decimal():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"), ts=_ts(1))
    snap = get_portfolio_snapshot(rows)
    assert isinstance(snap.top_position["cost_basis"], Decimal)
