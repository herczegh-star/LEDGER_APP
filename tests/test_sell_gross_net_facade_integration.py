"""Phase 3B — end-to-end: dialog-computed gross quote_amount through the
UNCHANGED accounting core (ui_facade.add_trade() -> trade_service ->
compute_positions()).

Proves the full pipeline, not just the isolated resolve_gross_fee_net()
arithmetic: when the dialog resolves a NET-mode user entry to a GROSS
quote_amount and submits it exactly as ui/modules/add_trade_dialog.py now
does, the resulting ledger rows and realized PnL come out correct — the
core's cash_impact = quote_amount - fee logic is NOT touched by this fix,
only what value it receives as quote_amount.

Also covers the explicit non-regression requirements:
  - BUY is completely unaffected (quote_amount=None, facade derives as before).
  - Legacy SELL calls (quote_amount=None, no Amount Type concept) still work
    exactly as before this fix.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.ui_facade import AddTradeRequestDTO, add_trade
from ui.amount_mode import NET, resolve_gross_fee_net

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 31, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def _seed_buy(db_path):
    """8 HYPE @ 1200 CZK -> cost_basis 9600, wac 1200 (same fixture as Phase 1)."""
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="HYPE", amount=Decimal("8"),
        currency="CZK", price=Decimal("1200"), venue="revolut",
        account="Osobní CZK",
    ), db_path)


def test_dialog_net_mode_pipeline_produces_correct_cash_impact_and_pnl(db):
    """Reproduces exactly what add_trade_dialog.py now does for a Revolut-style
    SELL entered in Net mode: net=3344.25, fee=75 -> gross resolved to
    3419.25 -> submitted as quote_amount -> core computes cash_impact back
    down to 3344.25 (not 3269.25 — the old double-fee-subtraction bug)."""
    _seed_buy(db)

    # What the dialog's _submit() does: resolve gross from the user's net entry...
    gross, fee, net = resolve_gross_fee_net(Decimal("3344.25"), Decimal("75"), NET)
    assert gross == Decimal("3419.25")

    # ...then submit it as the DTO's quote_amount, exactly like the dialog.
    result = add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", quote_amount=gross, price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()

    positions = compute_positions(rows)
    hype = next(p for p in positions if p.asset == "HYPE")
    assert hype.quantity == Decimal("6")
    # realized_pnl = proceeds(gross) - cost_removed - fee = 3419.25 - 2400 - 75
    assert hype.realized_pnl == Decimal("944.25")

    balances = compute_cash_balances(rows)
    cash_change_this_sell = Decimal("3419.25") - Decimal("75")  # +gross, then -fee
    assert cash_change_this_sell == Decimal("3344.25")  # == the real Revolut net amount

    # Full account balance: -9600 (BUY) + 3419.25 (SELL proceeds) - 75 (fee)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-6255.75")


def test_dialog_gross_mode_pipeline_matches_net_mode_result(db):
    """Gross mode (today's existing default behaviour) submitting 3419.25
    directly must produce the identical cash/PnL outcome as net mode above
    — the two entry modes are two doors to the same accounting result."""
    _seed_buy(db)

    result = add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", quote_amount=Decimal("3419.25"), price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()

    hype = next(p for p in compute_positions(rows) if p.asset == "HYPE")
    assert hype.realized_pnl == Decimal("944.25")

    balances = compute_cash_balances(rows)
    assert balances[("revolut", "Osobní CZK", "CZK")] == Decimal("-6255.75")


def test_buy_quote_amount_derivation_unaffected(db):
    """BUY was explicitly out of scope for this fix — confirms it still
    derives quote_amount from amount*price exactly as before (quote_amount
    left as None, as the dialog still does for BUY)."""
    result = add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="BTC", amount=Decimal("0.01"),
        currency="CZK", price=Decimal("2500000"), quote_amount=None,
        venue="kraken", account="Trading CZK",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    czk_leg = next(r for r in rows if r.asset == "CZK")
    assert czk_leg.amount == Decimal("-25000")  # 0.01 * 2500000, unaffected


def test_legacy_sell_without_explicit_quote_amount_still_works(db):
    """A caller that doesn't know about Amount Type at all (quote_amount=None,
    price-only — the pre-Phase-3B calling convention) must still work
    exactly as before: facade derives quote_amount = amount * price."""
    result = add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", quote_amount=None, price=Decimal("1709.625"),
        venue="revolut",
    ), db)
    assert result.success

    store = LedgerStore(db)
    rows = store.timeline()
    store.close()
    czk_leg = next(r for r in rows if r.asset == "CZK")
    assert czk_leg.amount == Decimal("3419.25")  # 2 * 1709.625, legacy derivation path
