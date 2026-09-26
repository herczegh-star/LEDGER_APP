"""Phase Model B — core/portfolio_boundary.py + engine effects.

Covers: contribution/withdrawal affect the portfolio boundary (cash),
BUY/SELL never auto-create one, TRANSFER within the portfolio (two
INVESTMENT_CASH accounts) doesn't change total managed capital, positions
(WAC/cost basis) are completely unaffected either way.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow
from core.portfolio_boundary import (
    CONTRIBUTION,
    WITHDRAWAL,
    build_portfolio_boundary_rows,
    validate_portfolio_boundary_direction,
    validate_portfolio_boundary_group,
)
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions

_TS = datetime(2026, 9, 19, 12, 0, 0)


# ── build_portfolio_boundary_rows() ─────────────────────────────────────────

def test_contribution_row_shape():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    assert outside.type == CONTRIBUTION
    assert outside.amount == Decimal("-50000")
    assert outside.account == "Osobní CZK"
    assert inside.type == CONTRIBUTION
    assert inside.amount == Decimal("50000")
    assert inside.account == "Investment Cash CZK"
    assert outside.id == inside.id


def test_withdrawal_row_shape_is_opposite_sign():
    outside, inside = build_portfolio_boundary_rows(
        WITHDRAWAL, timestamp=_TS, asset="CZK", amount=Decimal("3000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    assert outside.amount == Decimal("3000")   # outside GAINS on withdrawal
    assert inside.amount == Decimal("-3000")    # inside LOSES on withdrawal


def test_contribution_from_external_no_outside_account():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("10000"),
        outside_venue="external", outside_account=None,
        inside_venue="air_bank", inside_account="Investment Cash CZK",
    )
    assert outside.account is None
    assert outside.venue == "external"


def test_rejects_zero_or_negative_amount():
    with pytest.raises(ValueError, match="amount"):
        build_portfolio_boundary_rows(
            CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("0"),
            outside_venue="revolut", outside_account="x",
            inside_venue="revolut", inside_account="y",
        )


def test_rejects_empty_inside_account():
    with pytest.raises(ValueError, match="inside_account"):
        build_portfolio_boundary_rows(
            CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
            outside_venue="revolut", outside_account="x",
            inside_venue="revolut", inside_account="",
        )


def test_rejects_unknown_boundary_type():
    with pytest.raises(ValueError, match="boundary_type"):
        build_portfolio_boundary_rows(
            "SOMETHING_ELSE", timestamp=_TS, asset="CZK", amount=Decimal("100"),
            outside_venue="revolut", outside_account="x",
            inside_venue="revolut", inside_account="y",
        )


# ── validate_portfolio_boundary_group() ─────────────────────────────────────

def test_validate_valid_contribution_group():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert ok, errs


def test_validate_rejects_nonzero_net():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    inside.amount = Decimal("40000")  # tampered — no longer nets to 0
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert not ok
    assert any("0" in e for e in errs)


def test_validate_rejects_currency_mismatch():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    inside.asset = "EUR"
    inside.currency = "EUR"
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert not ok


def test_validate_rejects_mixed_types_in_group():
    c_outside, c_inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
        boundary_id="X",
    )
    c_inside.type = WITHDRAWAL  # tampered
    ok, errs = validate_portfolio_boundary_group([c_outside, c_inside])
    assert not ok


# ── engine effect: contribution/withdrawal move the boundary (cash) ────────

def test_contribution_increases_investment_cash_balance():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    balances = compute_cash_balances([outside, inside])
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("50000")
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-50000")


def test_withdrawal_decreases_investment_cash_balance():
    outside, inside = build_portfolio_boundary_rows(
        WITHDRAWAL, timestamp=_TS, asset="CZK", amount=Decimal("3000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    balances = compute_cash_balances([outside, inside])
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("-3000")
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("3000")


def test_positions_unaffected_by_contribution_or_withdrawal():
    """CZK-only rows are always fiat -> compute_positions() must not
    produce ANY position for them, and must leave unrelated crypto
    positions completely untouched."""
    crypto_rows = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("100"),
               currency="CZK", price=Decimal("10"), venue="revolut",
               account="Investment Cash CZK"),
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="CZK", amount=Decimal("-1000"),
               currency="CZK", price=Decimal("1"), venue="revolut",
               account="Investment Cash CZK"),
    ]
    positions_before = compute_positions(crypto_rows)

    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    positions_after = compute_positions(crypto_rows + [outside, inside])
    assert positions_before == positions_after
    assert not any(p.asset == "CZK" for p in positions_after)


# ── BUY/SELL never auto-create contribution/withdrawal ──────────────────────

def test_buy_never_creates_a_boundary_row():
    rows = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("100"),
               currency="CZK", price=Decimal("10"), venue="revolut",
               account="Investment Cash CZK"),
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="CZK", amount=Decimal("-1000"),
               currency="CZK", price=Decimal("1"), venue="revolut",
               account="Investment Cash CZK"),
    ]
    assert not any(r.type in (CONTRIBUTION, WITHDRAWAL) for r in rows)
    balances = compute_cash_balances(rows)
    # Investment Cash goes DOWN by the BUY alone — no offsetting contribution
    # was synthesized anywhere.
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("-1000")


def test_sell_never_creates_a_boundary_row():
    rows = [
        RawRow(id="s1", timestamp=_TS, type="SELL", asset="ARB", amount=Decimal("-50"),
               currency="CZK", price=Decimal("12"), venue="revolut",
               account="Investment Cash CZK"),
        RawRow(id="s1", timestamp=_TS, type="SELL", asset="CZK", amount=Decimal("600"),
               currency="CZK", price=Decimal("1"), venue="revolut",
               account="Investment Cash CZK"),
    ]
    assert not any(r.type in (CONTRIBUTION, WITHDRAWAL) for r in rows)
    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("600")


# ── TRANSFER within the portfolio (two INVESTMENT_CASH accounts) ───────────

def test_transfer_between_two_investment_cash_accounts_does_not_change_total():
    """A plain TRANSFER (not contribution/withdrawal) between two
    INVESTMENT_CASH-role accounts must leave the SUM of both balances
    unchanged — total managed capital is conserved, only its location moves."""
    rows = [
        RawRow(id="t1", timestamp=_TS, type="TRANSFER", asset="CZK", amount=Decimal("-20000"),
               currency="CZK", price=Decimal("1"), venue="revolut", account="Investment Cash CZK"),
        RawRow(id="t1", timestamp=_TS, type="TRANSFER", asset="CZK", amount=Decimal("20000"),
               currency="CZK", price=Decimal("1"), venue="air_bank", account="Investment Cash CZK"),
    ]
    balances = compute_cash_balances(rows)
    total = sum(balances.values())
    assert total == Decimal("0")  # conserved: -20000 + 20000
    assert balances[("revolut", "Investment Cash CZK", "CZK")] == Decimal("-20000")
    assert balances[("air_bank", "Investment Cash CZK", "CZK")] == Decimal("20000")


# ── Pre-production hardening: strict invariants ─────────────────────────────

def test_rejects_crypto_asset():
    """'Nesmí být možné: contribution crypto assetem'."""
    with pytest.raises(ValueError, match="fiat"):
        build_portfolio_boundary_rows(
            CONTRIBUTION, timestamp=_TS, asset="BTC", amount=Decimal("1"),
            outside_venue="revolut", outside_account="Osobní CZK",
            inside_venue="revolut", inside_account="Investment Cash CZK",
        )


def test_validate_group_rejects_crypto_asset_directly():
    """Even a hand-tampered (not built via the builder) group with a
    crypto asset must be caught by validate_portfolio_boundary_group()."""
    outside = RawRow(id="X", timestamp=_TS, type=CONTRIBUTION, asset="BTC",
                      amount=Decimal("-1"), currency="BTC", price=Decimal("1"),
                      venue="revolut", account="Osobní CZK")
    inside = RawRow(id="X", timestamp=_TS, type=CONTRIBUTION, asset="BTC",
                     amount=Decimal("1"), currency="BTC", price=Decimal("1"),
                     venue="revolut", account="Investment Cash CZK")
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert not ok
    assert any("fiat" in e for e in errs)


def test_rejects_identical_source_and_destination():
    """'Musí existovat... žádná no-op' — mirrors TRANSFER's existing
    source==destination rejection."""
    with pytest.raises(ValueError, match="identické"):
        build_portfolio_boundary_rows(
            CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
            outside_venue="revolut", outside_account="Investment Cash CZK",
            inside_venue="revolut", inside_account="Investment Cash CZK",
        )


def test_rejects_two_positive_legs_via_direct_construction():
    """'Nesmí být možné: dva kladné legs'."""
    outside = RawRow(id="X", timestamp=_TS, type=CONTRIBUTION, asset="CZK",
                      amount=Decimal("100"), currency="CZK", price=Decimal("1"),
                      venue="revolut", account="Osobní CZK")
    inside = RawRow(id="X", timestamp=_TS, type=CONTRIBUTION, asset="CZK",
                     amount=Decimal("100"), currency="CZK", price=Decimal("1"),
                     venue="revolut", account="Investment Cash CZK")
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert not ok  # net != 0 AND "one positive one negative" both fail


def test_rejects_missing_external_leg():
    """'Nesmí být možné: missing external leg' — a single-row 'group'."""
    _outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    ok, errs = validate_portfolio_boundary_group([inside])
    assert not ok


def test_rejects_mismatched_amount_via_tampering():
    """'Nesmí být možné: mismatched amount'."""
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    outside.amount = Decimal("-99")  # was -100, now mismatched vs inside's +100
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert not ok


def test_rejects_mismatched_currency_via_tampering():
    """'Nesmí být možné: mismatched currency'."""
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("100"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    outside.asset = "EUR"
    outside.currency = "EUR"
    ok, errs = validate_portfolio_boundary_group([outside, inside])
    assert not ok


# ── validate_portfolio_boundary_direction() ─────────────────────────────────

def test_direction_ok_for_correctly_signed_contribution():
    outside, inside = build_portfolio_boundary_rows(
        CONTRIBUTION, timestamp=_TS, asset="CZK", amount=Decimal("50000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    ok, errs = validate_portfolio_boundary_direction(
        [outside, inside], CONTRIBUTION, "revolut", "Investment Cash CZK",
    )
    assert ok, errs


def test_direction_ok_for_correctly_signed_withdrawal():
    outside, inside = build_portfolio_boundary_rows(
        WITHDRAWAL, timestamp=_TS, asset="CZK", amount=Decimal("3000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    ok, errs = validate_portfolio_boundary_direction(
        [outside, inside], WITHDRAWAL, "revolut", "Investment Cash CZK",
    )
    assert ok, errs


def test_direction_catches_a_mislabeled_group():
    """A WITHDRAWAL-shaped pair (inside leg negative) incorrectly labeled
    as type=CONTRIBUTION must be caught by the direction check — this is
    exactly the case validate_portfolio_boundary_group() alone CANNOT
    catch (it doesn't know which side is 'inside')."""
    outside, inside = build_portfolio_boundary_rows(
        WITHDRAWAL, timestamp=_TS, asset="CZK", amount=Decimal("3000"),
        outside_venue="revolut", outside_account="Osobní CZK",
        inside_venue="revolut", inside_account="Investment Cash CZK",
    )
    # Mislabel both rows as CONTRIBUTION (sign pattern of a WITHDRAWAL kept).
    outside.type = CONTRIBUTION
    inside.type = CONTRIBUTION
    ok, errs = validate_portfolio_boundary_direction(
        [outside, inside], CONTRIBUTION, "revolut", "Investment Cash CZK",
    )
    assert not ok
    assert any("WITHDRAWAL" in e or "kladný" in e for e in errs)
