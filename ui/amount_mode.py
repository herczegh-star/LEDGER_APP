"""Pure gross/fee/net resolution for SELL "Amount Type" input (Phase 3B).

Extracted from ui/modules/add_trade_dialog.py so the arithmetic is
independently unit-testable without a live ft.Page — mirrors the
ui/refresh_guard.py precedent. No Flet dependency, no business logic beyond
plain Decimal arithmetic; does NOT touch cost basis / WAC / realized PnL —
those remain the sole responsibility of core/reports/positions.py.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Tuple

GROSS = "gross"
NET = "net"


def resolve_gross_fee_net(
    total: Decimal,
    fee: Decimal,
    mode: str,
) -> Tuple[Decimal, Decimal, Decimal]:
    """Resolve (gross, fee, net) from a user-entered *total* interpreted
    per *mode*.

    mode == "net":  *total* is the NET cash actually received (after fee) —
                     e.g. what a Revolut transaction detail screen shows.
                     gross = total + fee.
    anything else (default "gross"): *total* is the GROSS trade value
                     (before fee) — today's existing, unchanged behaviour.
                     net = total - fee.

    fee == 0 collapses both modes to gross == net, by construction — no
    special-casing needed.

    The caller is responsible for feeding the resulting `gross` value to
    the accounting core as quote_amount; cash_impact = quote_amount - fee
    is computed downstream in core/reports/positions.py, unchanged.
    """
    if mode == NET:
        net = total
        gross = total + fee
    else:
        gross = total
        net = total - fee
    return gross, fee, net


def resolve_buy_order_total(
    total: Decimal,
    fee: Decimal,
    mode: str,
) -> Tuple[Decimal, Decimal, Decimal]:
    """Resolve (order_value, fee, total_cash_debited) from a user-entered
    *total* interpreted per *mode*, for BUY — the fee-direction mirror of
    resolve_gross_fee_net(): for a BUY, fee ADDS to the cash outflow (and to
    cost basis), rather than subtracting from proceeds as it does for SELL.

    mode == "net":  *total* is the TOTAL CASH actually debited from the
                     source account (fee already included) — e.g. what a
                     Revolut transaction detail screen shows.
                     order_value = total - fee.
    anything else (default "gross"): *total* is the ORDER VALUE (before
                     fee) — today's existing, unchanged behaviour.
                     total_cash_debited = total + fee.

    fee == 0 collapses both modes to order_value == total_cash_debited.

    The caller feeds `order_value` to the accounting core as quote_amount;
    cost_basis = quote_amount + fee and cash_impact = -(quote_amount + fee)
    are computed downstream in core/reports/positions.py, unchanged.
    """
    if mode == NET:
        total_cash_debited = total
        order_value = total - fee
    else:
        order_value = total
        total_cash_debited = total + fee
    return order_value, fee, total_cash_debited
