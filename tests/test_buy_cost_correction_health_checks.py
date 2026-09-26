"""Phase BUY-FIX — core/services/health_service.py: Check 10
(BUY_COST_CORRECTION group validity), mirroring
tests/test_correction_health_checks.py for the sibling CORRECTION check.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.buy_cost_correction import build_buy_cost_correction_rows
from core.model import RawRow
from core.services.health_service import health_report

_TS_BUY = datetime(2026, 9, 9, 21, 22, 39)


def _buy_rows(trade_id="buy-1", asset="ARB", ts=_TS_BUY):
    return [
        RawRow(id=trade_id, timestamp=ts, type="BUY", asset=asset,
               amount=Decimal("100"), currency="CZK", price=Decimal("10"), venue="revolut"),
        RawRow(id=trade_id, timestamp=ts, type="BUY", asset="CZK",
               amount=Decimal("-1000"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]


def _kinds(report):
    return {row.values["kind"] for row in report.rows}


def test_valid_group_produces_no_bcc_issues():
    rows = _buy_rows() + list(build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="buy-1",
        asset="ARB", currency="CZK", cash_delta=Decimal("100"), venue="revolut",
        reason="r", original_value=Decimal("1000"), corrected_value=Decimal("900"),
    ))
    kinds = _kinds(health_report(rows))
    assert not any(k.startswith("buy_cost_correction") or k == "invalid_buy_cost_correction_group" for k in kinds)


def test_missing_marker_flagged():
    _, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="buy-1",
        asset="ARB", currency="CZK", cash_delta=Decimal("100"), venue="revolut",
        reason="r", original_value=Decimal("1000"), corrected_value=Decimal("900"),
    )
    rows = _buy_rows() + [cash_leg]
    kinds = _kinds(health_report(rows))
    assert "invalid_buy_cost_correction_group" in kinds


def test_missing_cash_leg_flagged():
    marker, _ = build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="buy-1",
        asset="ARB", currency="CZK", cash_delta=Decimal("100"), venue="revolut",
        reason="r", original_value=Decimal("1000"), corrected_value=Decimal("900"),
    )
    rows = _buy_rows() + [marker]
    kinds = _kinds(health_report(rows))
    assert "invalid_buy_cost_correction_group" in kinds


def test_correction_of_unknown_trade_flagged():
    rows = list(build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="does_not_exist",
        asset="ARB", currency="CZK", cash_delta=Decimal("100"), venue="revolut",
        reason="r", original_value=Decimal("1000"), corrected_value=Decimal("900"),
    ))
    kinds = _kinds(health_report(rows))
    assert "buy_cost_correction_of_unknown_trade" in kinds


def test_correction_of_not_a_buy_flagged():
    """correction_of exists but is a SELL, not a BUY, of the corrected asset."""
    sell_rows = [
        RawRow(id="sell-1", timestamp=_TS_BUY, type="SELL", asset="ARB",
               amount=Decimal("-10"), currency="CZK", price=Decimal("10"), venue="revolut"),
        RawRow(id="sell-1", timestamp=_TS_BUY, type="SELL", asset="CZK",
               amount=Decimal("100"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    rows = sell_rows + list(build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="sell-1",
        asset="ARB", currency="CZK", cash_delta=Decimal("10"), venue="revolut",
        reason="r", original_value=Decimal("100"), corrected_value=Decimal("90"),
    ))
    kinds = _kinds(health_report(rows))
    assert "buy_cost_correction_of_not_a_buy" in kinds


def test_correction_of_wrong_asset_flagged():
    """correction_of references a real BUY, but of a DIFFERENT asset than
    the correction's own marker — must be flagged, not silently accepted."""
    rows = _buy_rows(asset="ARB") + list(build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="buy-1",
        asset="BTC", currency="CZK", cash_delta=Decimal("10"), venue="revolut",
        reason="r", original_value=Decimal("100"), corrected_value=Decimal("90"),
    ))
    kinds = _kinds(health_report(rows))
    assert "buy_cost_correction_of_not_a_buy" in kinds


def test_unparseable_note_flagged():
    marker = RawRow(id="X", timestamp=_TS_BUY + timedelta(microseconds=1),
                     type="BUY_COST_CORRECTION", asset="ARB", amount=Decimal("0"),
                     currency="CZK", price=None, venue="revolut", note="garbage")
    cash_leg = RawRow(id="X", timestamp=_TS_BUY + timedelta(microseconds=1),
                       type="BUY_COST_CORRECTION", asset="CZK", amount=Decimal("10"),
                       currency="CZK", price=None, venue="revolut", note="garbage")
    rows = _buy_rows() + [marker, cash_leg]
    kinds = _kinds(health_report(rows))
    assert "buy_cost_correction_note_unparseable" in kinds


def test_reversed_via_standard_reversal_flagged():
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="buy-1",
        asset="ARB", currency="CZK", cash_delta=Decimal("100"), venue="revolut",
        reason="r", original_value=Decimal("1000"), corrected_value=Decimal("900"),
        correction_id="BCC_buy-1_abcd1234",
    )
    rev_marker = RawRow(id="REV_BCC_buy-1_abcd1234_ffff0000",
                         timestamp=_TS_BUY + timedelta(days=1), type="REVERSAL",
                         asset="ARB", amount=Decimal("0"), currency="CZK",
                         price=None, venue="revolut")
    rows = _buy_rows() + [marker, cash_leg, rev_marker]
    kinds = _kinds(health_report(rows))
    assert "buy_cost_correction_reversed_via_standard_reversal" in kinds


def test_zero_amount_marker_not_flagged_as_generic_zero_amount():
    """BUY_COST_CORRECTION's marker (amount=0 by design) must be excluded
    from the generic zero_amount WARNING (check 5), same treatment as
    CORRECTION's marker."""
    rows = _buy_rows() + list(build_buy_cost_correction_rows(
        timestamp=_TS_BUY + timedelta(microseconds=1), correction_of="buy-1",
        asset="ARB", currency="CZK", cash_delta=Decimal("100"), venue="revolut",
        reason="r", original_value=Decimal("1000"), corrected_value=Decimal("900"),
    ))
    report = health_report(rows)
    zero_amount_issues = [r for r in report.rows if r.values["kind"] == "zero_amount"]
    assert zero_amount_issues == []
