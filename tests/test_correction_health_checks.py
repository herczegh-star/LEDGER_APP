"""core/services/health_service.py — Check 9: CORRECTION group validity.

Verifies malformed CORRECTION groups surface as health issues, valid ones
don't, and misuse of standard REVERSAL against a CORRECTION is detected.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.correction import build_correction_rows
from core.model import RawRow
from core.services.health_service import health_report

_TS_SELL = datetime(2026, 9, 10, 9, 35, 42)
_TS_CORR = datetime(2026, 9, 19, 13, 0, 0)


def _original_sell():
    return [
        RawRow(id="SELL1", timestamp=_TS_SELL, type="SELL", asset="HYPE",
               amount=Decimal("-2"), currency="CZK", price=Decimal("1672.125"), venue="revolut"),
        RawRow(id="SELL1", timestamp=_TS_SELL, type="SELL", asset="CZK",
               amount=Decimal("3344.25"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="SELL1", timestamp=_TS_SELL, type="FEE", asset="CZK",
               amount=Decimal("-75"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]


def _kinds(report):
    return {r.values["kind"] for r in report.rows}


def test_valid_correction_group_has_no_correction_issues():
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    rows = _original_sell() + [marker, fiat_leg]
    report = health_report(rows)
    kinds = _kinds(report)
    assert "invalid_correction_group" not in kinds
    assert "correction_of_unknown_trade" not in kinds
    assert "correction_reversed_via_standard_reversal" not in kinds


def test_marker_without_fiat_leg_is_a_health_issue():
    marker, _fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    rows = _original_sell() + [marker]
    report = health_report(rows)
    assert "invalid_correction_group" in _kinds(report)


def test_fiat_leg_without_marker_is_a_health_issue():
    _marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    rows = _original_sell() + [fiat_leg]
    report = health_report(rows)
    assert "invalid_correction_group" in _kinds(report)


def test_correction_of_unknown_trade_is_a_health_issue():
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="TRADE_THAT_DOES_NOT_EXIST", asset="HYPE",
        currency="CZK", delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    rows = _original_sell() + [marker, fiat_leg]
    report = health_report(rows)
    assert "correction_of_unknown_trade" in _kinds(report)


def test_correction_of_a_correction_is_not_flagged_as_unknown():
    """Correction-of-a-correction (CORR2 references CORR1's own id) must
    NOT be flagged as an unknown trade — it's the explicitly supported
    'undo a wrong correction' pattern."""
    marker1, fiat1 = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r1",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
        correction_id="CORR1",
    )
    marker2, fiat2 = build_correction_rows(
        timestamp=_TS_CORR.replace(minute=5), correction_of="CORR1", asset="HYPE",
        currency="CZK", delta=Decimal("-75"), venue="revolut", reason="undo",
        original_value=Decimal("2"), corrected_value=Decimal("1"),
        correction_id="CORR2",
    )
    rows = _original_sell() + [marker1, fiat1, marker2, fiat2]
    report = health_report(rows)
    assert "correction_of_unknown_trade" not in _kinds(report)


def test_standard_reversal_of_correction_is_a_health_issue():
    """Simulates someone (mis-)using reverse_trade()/build_reversal_rows()
    against a CORRECTION group id — must be flagged, not silently accepted."""
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
        correction_id="CORR1",
    )
    fake_reversal = RawRow(
        id="REV_CORR1_abcd1234", timestamp=_TS_CORR.replace(minute=10), type="REVERSAL",
        asset="CZK", amount=Decimal("-75"), currency="CZK", price=Decimal("1"), venue="revolut",
    )
    rows = _original_sell() + [marker, fiat_leg, fake_reversal]
    report = health_report(rows)
    assert "correction_reversed_via_standard_reversal" in _kinds(report)


def test_currency_venue_account_mismatch_is_a_health_issue():
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    fiat_leg.venue = "kraken"  # tamper: mismatch vs marker.venue
    rows = _original_sell() + [marker, fiat_leg]
    report = health_report(rows)
    assert "invalid_correction_group" in _kinds(report)


def test_existing_checks_unaffected_by_correction_rows():
    """A normal, unrelated missing_quote_leg issue elsewhere in the ledger
    must still be detected exactly as before, alongside a valid correction."""
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS_CORR, correction_of="SELL1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    orphan = RawRow(id="ORPHAN1", timestamp=_TS_SELL, type="BUY", asset="ETH",
                     amount=Decimal("1"), currency="CZK", price=Decimal("60000"), venue="kraken")
    rows = _original_sell() + [marker, fiat_leg, orphan]
    report = health_report(rows)
    assert "missing_quote_leg" in _kinds(report)
