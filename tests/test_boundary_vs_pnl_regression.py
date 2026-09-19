"""Pre-Production Hardening — section 5: explicit regression test.

PORTFOLIO_CONTRIBUTION / PORTFOLIO_WITHDRAWAL must NEVER change:
    crypto quantity, cost basis, WAC, realized PnL
They only ever change cash / portfolio boundary flows.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.portfolio_boundary import CONTRIBUTION, WITHDRAWAL, build_portfolio_boundary_rows
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.trade_service import AddTradeInput, build_trade_rows

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 0, 0)
_TS_CONTRIB = _TS_BUY - timedelta(days=1)
_TS_WITHDRAW = _TS_SELL + timedelta(days=1)


def _fixture_rows():
    """A realistic BUY -> SELL history, mirroring the real HYPE scenario:
    8 HYPE @ 1200 CZK (cost_basis=9600), then SELL 2 for 3344.25 CZK
    proceeds with a 75 CZK fee -> realized_pnl = 3344.25-2400-75 = 869.25."""
    buy = build_trade_rows(AddTradeInput(
        type="BUY", timestamp=_TS_BUY, base_asset="HYPE", base_amount=Decimal("8"),
        quote_currency="CZK", quote_amount=Decimal("9600"), venue="revolut",
        account="Investment Cash CZK",
    ), trade_id="buy-1")
    sell = build_trade_rows(AddTradeInput(
        type="SELL", timestamp=_TS_SELL, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3344.25"), venue="revolut",
        fee_amount=Decimal("75"), fee_currency="CZK", account="Investment Cash CZK",
    ), trade_id="sell-1")
    return buy + sell


def test_contribution_before_activity_does_not_change_positions():
    rows = _fixture_rows()
    positions_before = compute_positions(rows)

    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS_CONTRIB, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    positions_after = compute_positions(rows + [outside, inside])
    assert positions_before == positions_after


def test_withdrawal_after_activity_does_not_change_positions():
    rows = _fixture_rows()
    positions_before = compute_positions(rows)

    outside, inside = build_portfolio_boundary_rows(
        WITHDRAWAL, timestamp=_TS_WITHDRAW, asset="CZK", amount=Decimal("500"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    positions_after = compute_positions(rows + [outside, inside])
    assert positions_before == positions_after


def test_realized_pnl_exact_value_unaffected_by_contribution_and_withdrawal():
    """Pin the exact HYPE realized_pnl figure (869.25, same fixture as the
    known-limitation reversal test) and prove it's bit-identical with
    boundary events layered in before AND after."""
    rows = _fixture_rows()

    contrib_outside, contrib_inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS_CONTRIB, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    withdraw_outside, withdraw_inside = build_portfolio_boundary_rows(
        WITHDRAWAL, timestamp=_TS_WITHDRAW, asset="CZK", amount=Decimal("500"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    all_rows = rows + [contrib_outside, contrib_inside, withdraw_outside, withdraw_inside]

    hype = next(p for p in compute_positions(all_rows) if p.asset == "HYPE")
    assert hype.quantity == Decimal("6")
    assert hype.cost_basis == Decimal("7200")
    assert hype.realized_pnl == Decimal("869.25")


def test_cash_boundary_flows_are_the_only_thing_that_changes():
    rows = _fixture_rows()
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS_CONTRIB, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    cash_before = compute_cash_balances(rows)
    cash_after = compute_cash_balances(rows + [outside, inside])

    # Investment Cash CZK changes by exactly +50000 -- everything else identical.
    key = ("revolut", "Investment Cash CZK", "CZK")
    assert cash_after[key] - cash_before.get(key, Decimal("0")) == Decimal("50000")
    others_before = {k: v for k, v in cash_before.items() if k != key}
    others_after = {k: v for k, v in cash_after.items() if k != key and k != ("revolut", "Osobní CZK", "CZK")}
    assert others_before == others_after
