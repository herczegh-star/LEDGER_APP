"""Phase 1 — core/reports/cash.py: compute_cash_balances / compute_portfolio_cash_reserve
/ compute_external_flows.

Verifies:
  - group key is (venue, account, currency), NOT (venue, currency) — two
    accounts on the same venue never get merged.
  - legacy account=None rows are grouped separately from tagged rows.
  - venue == "external" is included in compute_cash_balances() (real data)
    but EXCLUDED from compute_portfolio_cash_reserve().
  - compute_external_flows() correctly derives deposits/withdrawals/net
    from venue == "external" rows.
  - the cash engine never computes WAC / cost basis / realized PnL — it only
    sums signed fiat amounts.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.reports.cash import (
    compute_cash_balances,
    compute_external_flows,
    compute_legacy_unassigned_flows,
    compute_portfolio_cash_reserve,
    compute_tracked_cash_reserve,
)

_TS = datetime(2026, 9, 10, 9, 31, 0)


def _row(venue, account, asset, amount, type_="TRANSFER", currency=None):
    return RawRow(
        timestamp=_TS, type=type_, asset=asset, amount=Decimal(amount),
        currency=currency or asset, price=Decimal("1"), venue=venue, account=account,
    )


# ── Group key: (venue, account, currency) ───────────────────────────────────

def test_two_accounts_same_venue_are_not_merged():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("revolut", "Investment CZK", "CZK", "5000"),
    ]
    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("1000")
    assert balances[("revolut", "Investment CZK", "CZK")] == Decimal("5000")
    assert len(balances) == 2


def test_legacy_account_none_grouped_separately_from_tagged():
    rows = [
        _row("revolut", None, "CZK", "500"),
        _row("revolut", "Osobní CZK", "CZK", "1000"),
    ]
    balances = compute_cash_balances(rows)
    assert balances[("revolut", None, "CZK")] == Decimal("500")
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("1000")


def test_zero_balance_key_is_pruned():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("revolut", "Osobní CZK", "CZK", "-1000"),
    ]
    balances = compute_cash_balances(rows)
    assert ("revolut", "Osobní CZK", "CZK") not in balances


def test_non_fiat_asset_ignored():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("kraken", None, "BTC", "0.5"),
    ]
    balances = compute_cash_balances(rows)
    assert ("kraken", None, "BTC") not in balances
    assert len(balances) == 1


# ── external boundary venue ──────────────────────────────────────────────────

def test_external_included_in_raw_balances():
    rows = [_row("external", None, "CZK", "-20000")]  # deposit: money left 'external'
    balances = compute_cash_balances(rows)
    assert balances[("external", None, "CZK")] == Decimal("-20000")


def test_external_excluded_from_portfolio_cash_reserve():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("external", None, "CZK", "-20000"),  # must NOT inflate reserve
    ]
    reserve = compute_portfolio_cash_reserve(rows)
    assert reserve["CZK"] == Decimal("1000")


def test_compute_external_flows_deposit_and_withdrawal():
    rows = [
        _row("external", None, "CZK", "-20000"),  # deposit into portfolio
        _row("revolut", "Osobní CZK", "CZK", "20000"),
        _row("external", None, "CZK", "5000"),    # withdrawal from portfolio
        _row("revolut", "Osobní CZK", "CZK", "-5000"),
    ]
    flows = compute_external_flows(rows)
    assert flows["CZK"]["deposits"] == Decimal("20000")
    assert flows["CZK"]["withdrawals"] == Decimal("5000")
    assert flows["CZK"]["net_contributed"] == Decimal("15000")


def test_compute_external_flows_ignores_non_external_venues():
    rows = [_row("revolut", "Osobní CZK", "CZK", "1000")]
    flows = compute_external_flows(rows)
    assert flows == {}


# ── SELL + fee example matching the Phase 1 spec ────────────────────────────

def test_sell_gross_minus_fee_nets_correctly_in_same_account():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "3419.25", type_="SELL"),
        _row("revolut", "Osobní CZK", "CZK", "-75", type_="FEE"),
    ]
    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("3344.25")


def test_cash_engine_has_no_wac_or_pnl_concept():
    """Sanity: compute_cash_balances() returns plain Decimal balances only —
    no cost basis / WAC / realized PnL fields exist on its output shape."""
    rows = [_row("revolut", "Osobní CZK", "CZK", "1000")]
    balances = compute_cash_balances(rows)
    value = balances[("revolut", "Osobní CZK", "CZK")]
    assert isinstance(value, Decimal)


# ── Phase 2.5: TRACKED vs LEGACY/UNASSIGNED split ───────────────────────────
#
# Found during visual UI review: account=None historical fiat flow (mostly
# BUY outflows with no matching recorded deposit) was showing up as huge
# negative "Cash Reserve" figures (e.g. -1 071 515 CZK) — not a real debt,
# just untracked history. compute_tracked_cash_reserve() /
# compute_portfolio_cash_reserve() must exclude it entirely; only accounts
# with an explicit account label are trustworthy as a current balance.

def test_negative_legacy_flow_not_in_tracked_reserve():
    rows = [_row("anycoin", None, "CZK", "-1071515.48", type_="BUY")]
    reserve = compute_tracked_cash_reserve(rows)
    assert "CZK" not in reserve


def test_positive_legacy_flow_not_in_tracked_reserve():
    rows = [_row("kraken", None, "CZK", "50000", type_="TRANSFER")]
    reserve = compute_tracked_cash_reserve(rows)
    assert "CZK" not in reserve


def test_explicit_account_positive_balance_in_tracked_reserve():
    rows = [_row("revolut", "Osobní CZK", "CZK", "12826.06")]
    reserve = compute_tracked_cash_reserve(rows)
    assert reserve["CZK"] == Decimal("12826.06")


def test_explicit_account_negative_balance_preserved_in_tracked_reserve():
    """Sign alone must never be the exclusion criterion — only account=None is."""
    rows = [_row("revolut", "Osobní CZK", "CZK", "-2000000", type_="BUY")]
    reserve = compute_tracked_cash_reserve(rows)
    assert reserve["CZK"] == Decimal("-2000000")


def test_external_not_in_tracked_reserve_even_with_account():
    rows = [_row("external", "somehow-tagged", "CZK", "-20000")]
    reserve = compute_tracked_cash_reserve(rows)
    assert "CZK" not in reserve


def test_portfolio_cash_reserve_is_alias_for_tracked_reserve():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("anycoin", None, "CZK", "-500000", type_="BUY"),
    ]
    assert compute_portfolio_cash_reserve(rows) == compute_tracked_cash_reserve(rows)


def test_legacy_unassigned_flows_contains_only_account_none_non_external():
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("anycoin", None, "CZK", "-1071515.48", type_="BUY"),
        _row("external", None, "CZK", "-20000"),
    ]
    legacy = compute_legacy_unassigned_flows(rows)
    assert ("anycoin", None, "CZK") in legacy
    assert legacy[("anycoin", None, "CZK")] == Decimal("-1071515.48")
    assert ("revolut", "Osobní CZK", "CZK") not in legacy  # tracked, not legacy
    assert ("external", None, "CZK") not in legacy          # external excluded too


def test_legacy_flows_and_tracked_reserve_are_disjoint():
    """No CZK balance should ever be counted in both places at once."""
    rows = [
        _row("revolut", "Osobní CZK", "CZK", "1000"),
        _row("anycoin", None, "CZK", "-1071515.48", type_="BUY"),
    ]
    legacy_venues = {k[0] for k in compute_legacy_unassigned_flows(rows)}
    tracked = compute_tracked_cash_reserve(rows)
    assert "anycoin" in legacy_venues
    # anycoin's -1 071 515.48 must not leak into the tracked CZK total
    assert tracked["CZK"] == Decimal("1000")
