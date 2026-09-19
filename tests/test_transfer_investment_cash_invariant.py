"""Pre-Production Hardening — section 6: TRANSFER between two
INVESTMENT_CASH accounts changes only LOCATION, never:
    total investment cash, external flows, portfolio contributions/withdrawals.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.model import RawRow
from core.portfolio_boundary import CONTRIBUTION, build_portfolio_boundary_rows
from core.reports.cash import compute_cash_balances, compute_external_flows

_TS = datetime(2026, 9, 19)


def _contribution_rows(amount=Decimal("50000")):
    return build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=amount,
        outside_venue="external", outside_account=None,
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )


def _transfer_rows(amount=Decimal("20000"), ts=_TS + timedelta(days=1)):
    return (
        RawRow(id="t1", timestamp=ts, type="TRANSFER", asset="CZK", amount=-amount,
               currency="CZK", price=Decimal("1"), venue="revolut", account="Investment Cash CZK"),
        RawRow(id="t1", timestamp=ts, type="TRANSFER", asset="CZK", amount=amount,
               currency="CZK", price=Decimal("1"), venue="air_bank", account="Investment Cash CZK"),
    )


def test_total_investment_cash_unchanged_by_internal_transfer():
    outside, inside = _contribution_rows()
    t_out, t_in = _transfer_rows()
    rows = [outside, inside, t_out, t_in]

    balances = compute_cash_balances(rows)
    total_investment_cash = (
        balances.get(("revolut", "Investment Cash CZK", "CZK"), Decimal("0"))
        + balances.get(("air_bank", "Investment Cash CZK", "CZK"), Decimal("0"))
    )
    assert total_investment_cash == Decimal("50000")  # unchanged by the TRANSFER
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("30000")
    assert balances[("air_bank", "Investment Cash CZK", "CZK")] == Decimal("20000")


def test_external_flows_unchanged_by_internal_transfer():
    outside, inside = _contribution_rows()
    t_out, t_in = _transfer_rows()
    rows_without_transfer = [outside, inside]
    rows_with_transfer = [outside, inside, t_out, t_in]

    flows_without = compute_external_flows(rows_without_transfer)
    flows_with = compute_external_flows(rows_with_transfer)
    assert flows_without == flows_with  # TRANSFER never touches venue="external"


def test_contribution_total_unchanged_by_later_internal_transfer():
    """The TRANSFER must not be double-counted as (or alter) a separate
    contribution — only the ORIGINAL PORTFOLIO_CONTRIBUTION row(s) count."""
    outside, inside = _contribution_rows()
    t_out, t_in = _transfer_rows()
    rows = [outside, inside, t_out, t_in]

    contribution_rows = [r for r in rows if r.type == CONTRIBUTION]
    assert len(contribution_rows) == 2  # only the original pair
    total_contributed = sum(
        (r.amount for r in contribution_rows if r.account == "Investment Cash CZK"), Decimal("0")
    )
    assert total_contributed == Decimal("50000")  # unaffected by the TRANSFER existing


def test_transfer_rows_are_never_typed_as_boundary_events():
    t_out, t_in = _transfer_rows()
    assert t_out.type == "TRANSFER"
    assert t_in.type == "TRANSFER"
    assert t_out.type not in ("PORTFOLIO_CONTRIBUTION", "PORTFOLIO_WITHDRAWAL")
