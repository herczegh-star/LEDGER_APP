"""Bugfix — core/reports/proceeds.py must reflect CORRECTION-adjusted
economics, not just the originally-recorded SELL rows.

Covers: the known real HYPE case, SELL without correction (today's
behaviour unchanged), multiple correction deltas summing deterministically,
and an unrelated correction not leaking into a different SELL's row.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.correction import build_correction_rows
from core.model import RawRow
from core.reports.positions import compute_positions
from core.reports.proceeds import get_asset_proceeds

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 35, 42)
_TS_CORR = datetime(2026, 9, 19, 1, 55, 40)


def _hype_fixture():
    """8 HYPE @ 1200 CZK (cost_basis=9600), SELL 2 for 3344.25 proceeds
    with a 75 CZK fee — the real production HYPE shape."""
    return [
        RawRow(id="buy-1", timestamp=_TS_BUY, type="BUY", asset="HYPE",
               amount=Decimal("8"), currency="CZK", price=Decimal("1200"), venue="revolut"),
        RawRow(id="buy-1", timestamp=_TS_BUY, type="BUY", asset="CZK",
               amount=Decimal("-9600"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="sell-1", timestamp=_TS_SELL, type="SELL", asset="HYPE",
               amount=Decimal("-2"), currency="CZK", price=Decimal("1672.125"), venue="revolut"),
        RawRow(id="sell-1", timestamp=_TS_SELL, type="SELL", asset="CZK",
               amount=Decimal("3344.25"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="sell-1", timestamp=_TS_SELL, type="FEE", asset="CZK",
               amount=Decimal("-75"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]


def test_known_hype_case_shows_corrected_economics():
    rows = _hype_fixture()
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="sell-1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="revolut_sell_net_gross_fee",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
        account="Osobní CZK",
    )
    rows_with_correction = rows + [marker, fiat_leg]

    proceeds = get_asset_proceeds(rows_with_correction, "HYPE")
    assert len(proceeds) == 1
    row = proceeds[0]

    # Corrected (displayed) figures:
    assert row.quote_amount == Decimal("3419.25")
    assert row.fee == Decimal("75")
    assert row.cash_impact == Decimal("3344.25")

    # Original (audit) figures preserved, unchanged:
    assert row.recorded_quote_amount == Decimal("3344.25")
    assert row.recorded_cash_impact == Decimal("3269.25")
    assert row.correction_delta == Decimal("75")
    assert row.has_correction is True
    assert row.correction_accounts == ["Osobní CZK"]

    # Untouched facts:
    assert row.quantity_sold == Decimal("2")
    assert row.date.startswith("2026-09-10")
    assert row.cash_account is None  # original quote leg's account — never invented

    # No position/cost basis/WAC changes anywhere from this reporting fix:
    hype = next(p for p in compute_positions(rows_with_correction) if p.asset == "HYPE")
    assert hype.quantity == Decimal("6")
    assert hype.cost_basis == Decimal("7200")
    assert hype.realized_pnl == Decimal("944.25")  # 3419.25 - 2400 - 75, matches the correction


def test_sell_without_correction_unchanged_behaviour():
    rows = _hype_fixture()
    row = get_asset_proceeds(rows, "HYPE")[0]

    assert row.quote_amount == Decimal("3344.25")
    assert row.cash_impact == Decimal("3269.25")
    assert row.recorded_quote_amount == row.quote_amount
    assert row.recorded_cash_impact == row.cash_impact
    assert row.correction_delta == Decimal("0")
    assert row.has_correction is False
    assert row.correction_accounts == []


def test_multiple_corrections_sum_deterministically():
    rows = _hype_fixture()
    marker1, fiat1 = build_correction_rows(
        timestamp=_TS_CORR, correction_of="sell-1", asset="HYPE", currency="CZK",
        delta=Decimal("50"), venue="revolut", reason="r1",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3394.25"),
        correction_id="corr-1",
    )
    marker2, fiat2 = build_correction_rows(
        timestamp=_TS_CORR + timedelta(minutes=1), correction_of="sell-1", asset="HYPE", currency="CZK",
        delta=Decimal("25"), venue="revolut", reason="r2",
        original_value=Decimal("3394.25"), corrected_value=Decimal("3419.25"),
        correction_id="corr-2",
    )
    rows_with_corrections = rows + [marker1, fiat1, marker2, fiat2]

    row = get_asset_proceeds(rows_with_corrections, "HYPE")[0]
    assert row.correction_delta == Decimal("75")  # 50 + 25
    assert row.quote_amount == Decimal("3419.25")
    assert row.cash_impact == Decimal("3344.25")
    assert row.has_correction is True


def test_unrelated_correction_does_not_affect_other_sell():
    rows = _hype_fixture() + [
        RawRow(id="buy-2", timestamp=_TS_BUY, type="BUY", asset="ARB",
               amount=Decimal("100"), currency="CZK", price=Decimal("10"), venue="revolut"),
        RawRow(id="buy-2", timestamp=_TS_BUY, type="BUY", asset="CZK",
               amount=Decimal("-1000"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="sell-2", timestamp=_TS_SELL + timedelta(days=1), type="SELL", asset="ARB",
               amount=Decimal("-50"), currency="CZK", price=Decimal("12"), venue="revolut"),
        RawRow(id="sell-2", timestamp=_TS_SELL + timedelta(days=1), type="SELL", asset="CZK",
               amount=Decimal("600"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    # A correction that targets sell-1 (HYPE), not sell-2 (ARB).
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="sell-1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    rows_all = rows + [marker, fiat_leg]

    arb_row = get_asset_proceeds(rows_all, "ARB")[0]
    assert arb_row.has_correction is False
    assert arb_row.correction_delta == Decimal("0")
    assert arb_row.quote_amount == Decimal("600")
    assert arb_row.cash_impact == Decimal("600")

    hype_row = get_asset_proceeds(rows_all, "HYPE")[0]
    assert hype_row.has_correction is True
    assert hype_row.correction_delta == Decimal("75")


def test_correction_in_different_currency_is_ignored():
    """A CORRECTION whose fiat leg is in a DIFFERENT currency than the
    trade's own quote leg must never be folded in — currency-safety guard."""
    rows = _hype_fixture()
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="sell-1", asset="HYPE", currency="EUR",
        delta=Decimal("3"), venue="revolut", reason="r",
        original_value=Decimal("100"), corrected_value=Decimal("103"),
    )
    rows_with_correction = rows + [marker, fiat_leg]
    row = get_asset_proceeds(rows_with_correction, "HYPE")[0]
    assert row.has_correction is False
    assert row.correction_delta == Decimal("0")
    assert row.quote_amount == Decimal("3344.25")
