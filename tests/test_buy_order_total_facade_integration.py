"""Phase BUY-FIX — end-to-end: dialog-computed order_value quote_amount
through the UNCHANGED accounting core (ui_facade.add_trade() ->
trade_service -> compute_positions()).

Mirrors tests/test_sell_gross_net_facade_integration.py exactly, but for
BUY's "Total Cash Debited" Amount Type — the fee-direction mirror of SELL's
net/gross toggle (see ui/amount_mode.py::resolve_buy_order_total()).

Source-verified real scenario (Revolut ARB BUY, screenshot confirmed
2026-09-19): Displayed Amount 5000 CZK is the TOTAL CASH DEBITED (fee
already included), fee = 89 CZK -> order_value (true quote_amount) = 4911 CZK.

Also covers the explicit non-regression requirements:
  - SELL is completely unaffected by this change.
  - Legacy BUY calls (quote_amount=None, price-only) still work exactly as
    before this fix.
  - fee == 0 collapses gross/net BUY entry to the identical result.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.ui_facade import AddTradeRequestDTO, add_trade
from ui.amount_mode import NET, resolve_buy_order_total

_TS_BUY = datetime(2026, 9, 9, 21, 22, 39)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_dialog_total_cash_debited_mode_produces_correct_cost_basis_and_cash(db):
    """Reproduces exactly what add_trade_dialog.py now does for a Revolut-style
    BUY entered in 'Total Cash Debited' mode: total=5000, fee=89 -> order_value
    resolved to 4911 -> submitted as quote_amount -> core computes
    cost_basis = 4911 + 89 = 5000 and cash_impact = -5000 (not -5089 — the
    old double-fee-addition bug)."""
    order_value, fee, total_cash_debited = resolve_buy_order_total(
        Decimal("5000"), Decimal("89"), NET
    )
    assert order_value == Decimal("4911")

    result = add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="ARB", amount=Decimal("1495.0309551"),
        currency="CZK", quote_amount=order_value, price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("89"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()

    positions = compute_positions(rows)
    arb = next(p for p in positions if p.asset == "ARB")
    assert arb.cost_basis == Decimal("5000")

    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-5000")


def test_dialog_order_value_mode_matches_total_cash_debited_mode_result(db):
    """Order Value mode (today's existing default behaviour) submitting 4911
    directly must produce the identical cost_basis/cash outcome as Total
    Cash Debited mode above — two doors to the same accounting result."""
    result = add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="ARB", amount=Decimal("1495.0309551"),
        currency="CZK", quote_amount=Decimal("4911"), price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("89"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()

    arb = next(p for p in compute_positions(rows) if p.asset == "ARB")
    assert arb.cost_basis == Decimal("5000")

    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-5000")


def test_buy_fee_zero_gross_and_net_mode_identical(db):
    order_value, _fee, _total = resolve_buy_order_total(
        Decimal("1000"), Decimal("0"), NET
    )
    assert order_value == Decimal("1000")

    result = add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="BTC", amount=Decimal("0.01"),
        currency="CZK", quote_amount=order_value, price=Decimal("1"),
        venue="kraken",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    btc = next(p for p in compute_positions(rows) if p.asset == "BTC")
    assert btc.cost_basis == Decimal("1000")


def test_legacy_buy_without_explicit_quote_amount_still_works(db):
    """A caller that doesn't know about Amount Type at all (quote_amount=None,
    price-only — the pre-Phase-BUY-FIX calling convention) must still work
    exactly as before: facade derives quote_amount = amount * price."""
    result = add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="ARB", amount=Decimal("1495.0309551"),
        currency="CZK", quote_amount=None, price=Decimal("3.2848"),
        venue="revolut",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    czk_leg = next(r for r in rows if r.asset == "CZK")
    assert czk_leg.amount == Decimal("-1495.0309551") * Decimal("3.2848")


def test_sell_behaviour_completely_unaffected(db):
    """SELL's Phase 3B gross/net pipeline must remain byte-for-byte unchanged
    by this BUY-side addition — same fixture/assertions as
    tests/test_sell_gross_net_facade_integration.py's gross-mode case."""
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=datetime(2026, 8, 1, 10, 0, 0), asset="HYPE",
        amount=Decimal("8"), currency="CZK", price=Decimal("1200"),
        venue="revolut", account="Osobní CZK",
    ), db)

    result = add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=datetime(2026, 9, 10, 9, 31, 0), asset="HYPE",
        amount=Decimal("2"), currency="CZK", quote_amount=Decimal("3419.25"),
        price=Decimal("1"), venue="revolut", fee_amount=Decimal("75"),
        fee_currency="CZK", account="Osobní CZK",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()

    hype = next(p for p in compute_positions(rows) if p.asset == "HYPE")
    assert hype.realized_pnl == Decimal("944.25")

    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-6255.75")
