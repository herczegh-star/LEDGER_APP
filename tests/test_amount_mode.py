"""Phase 3B — ui/amount_mode.py: resolve_gross_fee_net().
Phase BUY-FIX — ui/amount_mode.py: resolve_buy_order_total().

Pure arithmetic, no Flet/DB dependency. These are the functions
ui/modules/add_trade_dialog.py actually calls (not a paraphrase of them).
"""
from __future__ import annotations

from decimal import Decimal

from ui.amount_mode import GROSS, NET, resolve_buy_order_total, resolve_gross_fee_net


# ── The real HYPE scenario, both directions ─────────────────────────────────

def test_revolut_net_input_scenario():
    """Spec: net=3344.25, fee=75 -> gross=3419.25, cash impact (net) stays 3344.25."""
    gross, fee, net = resolve_gross_fee_net(Decimal("3344.25"), Decimal("75"), NET)
    assert gross == Decimal("3419.25")
    assert fee == Decimal("75")
    assert net == Decimal("3344.25")


def test_gross_input_scenario():
    """Spec: gross=3419.25, fee=75 -> cash impact (net) = 3344.25."""
    gross, fee, net = resolve_gross_fee_net(Decimal("3419.25"), Decimal("75"), GROSS)
    assert gross == Decimal("3419.25")
    assert fee == Decimal("75")
    assert net == Decimal("3344.25")


# ── Fee = 0 collapses both modes ────────────────────────────────────────────

def test_fee_zero_gross_equals_net_in_gross_mode():
    gross, fee, net = resolve_gross_fee_net(Decimal("1000"), Decimal("0"), GROSS)
    assert gross == net == Decimal("1000")


def test_fee_zero_gross_equals_net_in_net_mode():
    gross, fee, net = resolve_gross_fee_net(Decimal("1000"), Decimal("0"), NET)
    assert gross == net == Decimal("1000")


# ── Default / unrecognised mode behaves as gross (safe default) ────────────

def test_unrecognised_mode_defaults_to_gross_behaviour():
    gross, fee, net = resolve_gross_fee_net(Decimal("500"), Decimal("20"), "anything-else")
    assert gross == Decimal("500")
    assert net == Decimal("480")


def test_empty_string_mode_defaults_to_gross_behaviour():
    """Matches the dialog's default dd_amount_mode.value == 'gross'."""
    gross, fee, net = resolve_gross_fee_net(Decimal("500"), Decimal("20"), "")
    assert gross == Decimal("500")
    assert net == Decimal("480")


# ── Round-trip consistency ──────────────────────────────────────────────────

def test_gross_then_net_round_trip():
    """Feeding gross-mode's own gross back through net-mode must recover
    the same net figure — internal consistency of the two modes."""
    gross, fee, net = resolve_gross_fee_net(Decimal("3419.25"), Decimal("75"), GROSS)
    gross2, fee2, net2 = resolve_gross_fee_net(net, fee, NET)
    assert gross2 == gross
    assert net2 == net


# ── Phase BUY-FIX: resolve_buy_order_total() ────────────────────────────────
# The real Revolut ARB BUY scenario (source-verified 2026-09-19 screenshot):
#   Displayed Amount 5000 CZK = TOTAL CASH DEBITED (fee already included),
#   fee = 89 CZK -> order_value = 4911 CZK.

def test_revolut_arb_buy_total_cash_debited_scenario():
    order_value, fee, total_cash_debited = resolve_buy_order_total(
        Decimal("5000"), Decimal("89"), NET
    )
    assert order_value == Decimal("4911")
    assert fee == Decimal("89")
    assert total_cash_debited == Decimal("5000")


def test_buy_order_value_gross_mode_matches_todays_behaviour():
    """gross mode (default) = today's unchanged behaviour: total IS the
    order value; total_cash_debited = total + fee."""
    order_value, fee, total_cash_debited = resolve_buy_order_total(
        Decimal("4911"), Decimal("89"), GROSS
    )
    assert order_value == Decimal("4911")
    assert fee == Decimal("89")
    assert total_cash_debited == Decimal("5000")


def test_buy_fee_zero_collapses_both_modes():
    order_value, fee, total_cash_debited = resolve_buy_order_total(
        Decimal("1000"), Decimal("0"), GROSS
    )
    assert order_value == total_cash_debited == Decimal("1000")

    order_value2, fee2, total_cash_debited2 = resolve_buy_order_total(
        Decimal("1000"), Decimal("0"), NET
    )
    assert order_value2 == total_cash_debited2 == Decimal("1000")


def test_buy_legacy_default_mode_unaffected():
    """Unrecognised/empty mode defaults to gross (order-value) behaviour,
    matching the dialog's default dd_amount_mode.value == 'gross' — legacy
    BUY entries (no Amount Type ever chosen) are unaffected."""
    order_value, fee, total_cash_debited = resolve_buy_order_total(
        Decimal("500"), Decimal("20"), ""
    )
    assert order_value == Decimal("500")
    assert total_cash_debited == Decimal("520")


def test_buy_total_cash_debited_then_gross_round_trip():
    order_value, fee, total_cash_debited = resolve_buy_order_total(
        Decimal("5000"), Decimal("89"), NET
    )
    order_value2, fee2, total_cash_debited2 = resolve_buy_order_total(
        order_value, fee, GROSS
    )
    assert order_value2 == order_value
    assert total_cash_debited2 == total_cash_debited
