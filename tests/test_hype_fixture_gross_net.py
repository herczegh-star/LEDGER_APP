"""Phase 1 — real-world fixture: SELL 2 HYPE on Revolut / Osobní CZK.

    2 HYPE sold, reported resulting amount 3 344,25 CZK, fee 75 CZK.

This fixture deliberately does NOT decide whether 3 344,25 CZK is the GROSS
trade value or the NET amount actually credited to the account — we have no
source Revolut export to confirm that yet (Phase 1 spec, section 12/13:
"Nevybírej jednu variantu bez zdrojových dat"). Both interpretations are
modelled side by side so either can be verified later once real data is
available.

    Interpretation A — 3 344,25 CZK is NET (already fee-deducted):
        quote_amount (gross trade value) = 3 344,25 + 75 = 3 419,25
        fee_amount = 75
        cash actually credited = 3 419,25 - 75 = 3 344,25  (matches the
        reported figure exactly)

    Interpretation B — 3 344,25 CZK is GROSS (fee taken out of it):
        quote_amount = 3 344,25
        fee_amount = 75
        cash actually credited = 3 344,25 - 75 = 3 269,25  (LESS than the
        reported figure — the fee was already inside it)

The two interpretations produce different realized_pnl (differing by
exactly the fee amount) and different final cash balances. Both are
asserted explicitly below so the discrepancy is visible and traceable,
not silently picked.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.trade_service import AddTradeInput, build_trade_rows

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 31, 0)

_ACCOUNT = "Osobní CZK"
_VENUE = "revolut"

# Prior position: BUY 8 HYPE at 1200 CZK/unit -> cost_basis = 9600 CZK, wac=1200
_BUY_INPUT = AddTradeInput(
    type="BUY", timestamp=_TS_BUY, base_asset="HYPE", base_amount=Decimal("8"),
    quote_currency="CZK", quote_amount=Decimal("9600"), venue=_VENUE,
    account=_ACCOUNT,
)


def _sell_rows(quote_amount: Decimal) -> list:
    inp = AddTradeInput(
        type="SELL", timestamp=_TS_SELL, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=quote_amount, venue=_VENUE,
        fee_amount=Decimal("75"), fee_currency="CZK",
        account=_ACCOUNT,
    )
    return build_trade_rows(inp)


def _run_scenario(quote_amount: Decimal):
    buy_rows = build_trade_rows(_BUY_INPUT, trade_id="buy-1")
    sell_rows = _sell_rows(quote_amount)
    all_rows = buy_rows + sell_rows

    positions = compute_positions(all_rows)
    hype = next(p for p in positions if p.asset == "HYPE")

    balances = compute_cash_balances(all_rows)
    cash = balances.get((_VENUE, _ACCOUNT, "CZK"), Decimal("0"))

    return hype, cash


def test_interpretation_A_net_reported_amount():
    """3 344,25 CZK = NET. quote_amount must be grossed up to 3 419,25."""
    hype, cash = _run_scenario(quote_amount=Decimal("3419.25"))

    assert hype.quantity == Decimal("6")
    assert hype.realized_pnl == Decimal("944.25")  # 3419.25 - 2400 - 75
    # Net cash impact matches the reported figure exactly under this reading.
    assert cash == Decimal("-6255.75")  # -9600 (BUY) + 3419.25 - 75 (SELL+fee)


def test_interpretation_B_gross_reported_amount():
    """3 344,25 CZK = GROSS. quote_amount used as-is; fee comes out of it."""
    hype, cash = _run_scenario(quote_amount=Decimal("3344.25"))

    assert hype.quantity == Decimal("6")
    assert hype.realized_pnl == Decimal("869.25")  # 3344.25 - 2400 - 75
    assert cash == Decimal("-6330.75")  # -9600 (BUY) + 3344.25 - 75 (SELL+fee)


def test_quantity_remaining_is_identical_regardless_of_interpretation():
    """The gross/net ambiguity affects money, never the crypto quantity."""
    hype_a, _ = _run_scenario(quote_amount=Decimal("3419.25"))
    hype_b, _ = _run_scenario(quote_amount=Decimal("3344.25"))
    assert hype_a.quantity == hype_b.quantity == Decimal("6")


def test_gross_net_ambiguity_shifts_realized_pnl_by_exactly_the_fee_amount():
    """Documents the concrete size of the ambiguity: realized PnL differs
    between the two readings by precisely the fee amount (75 CZK) — this is
    the number that needs resolving once real Revolut source data exists."""
    hype_a, cash_a = _run_scenario(quote_amount=Decimal("3419.25"))
    hype_b, cash_b = _run_scenario(quote_amount=Decimal("3344.25"))

    assert hype_a.realized_pnl - hype_b.realized_pnl == Decimal("75")
    assert cash_a - cash_b == Decimal("75")
