"""core/correction.py — pure unit tests: note builder/parser, row builder,
group-level structural validator. No DB, no compute_positions().
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.correction import (
    build_correction_note,
    build_correction_rows,
    parse_correction_note,
    validate_correction_group,
)

_TS = datetime(2026, 9, 19, 13, 0, 0)


# ── Note builder / parser round-trip ────────────────────────────────────────

def test_build_and_parse_note_round_trip():
    note = build_correction_note(
        correction_of="20260910_093542_REVOLUT_SELL_001",
        reason="revolut_sell_net_gross_fee",
        original_value=Decimal("3344.25"),
        corrected_value=Decimal("3419.25"),
        delta=Decimal("75.00"),
    )
    parsed = parse_correction_note(note)
    assert parsed is not None
    assert parsed["correction_of"] == "20260910_093542_REVOLUT_SELL_001"
    assert parsed["reason"] == "revolut_sell_net_gross_fee"
    assert parsed["original"] == Decimal("3344.25")
    assert parsed["corrected"] == Decimal("3419.25")
    assert parsed["delta"] == Decimal("75.00")


def test_parse_note_returns_none_for_unrelated_text():
    assert parse_correction_note("just a regular note") is None
    assert parse_correction_note(None) is None
    assert parse_correction_note("") is None


def test_parse_note_never_raises_on_garbage():
    assert parse_correction_note("CORRECTION of  | reason= | original=notanumber") is None or True
    # must not raise regardless of malformed content
    parse_correction_note("CORRECTION of X | delta=abc")


# ── build_correction_rows() ─────────────────────────────────────────────────

def test_build_correction_rows_shape():
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS, correction_of="TRADE_1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
    )
    assert marker.type == "CORRECTION"
    assert marker.asset == "HYPE"
    assert marker.amount == Decimal("0")
    assert marker.currency == "CZK"
    assert fiat_leg.type == "CORRECTION"
    assert fiat_leg.asset == "CZK"
    assert fiat_leg.amount == Decimal("75")
    assert marker.id == fiat_leg.id
    assert marker.note == fiat_leg.note
    assert marker.venue == fiat_leg.venue == "revolut"


def test_build_correction_rows_carries_account_on_both_legs():
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS, correction_of="TRADE_1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
        account="Osobní CZK",
    )
    assert marker.account == "Osobní CZK"
    assert fiat_leg.account == "Osobní CZK"


def test_build_correction_rows_rejects_zero_delta():
    with pytest.raises(ValueError):
        build_correction_rows(
            timestamp=_TS, correction_of="TRADE_1", asset="HYPE", currency="CZK",
            delta=Decimal("0"), venue="revolut", reason="r",
            original_value=Decimal("1"), corrected_value=Decimal("1"),
        )


def test_build_correction_rows_rejects_missing_correction_of():
    with pytest.raises(ValueError):
        build_correction_rows(
            timestamp=_TS, correction_of="", asset="HYPE", currency="CZK",
            delta=Decimal("75"), venue="revolut", reason="r",
            original_value=Decimal("1"), corrected_value=Decimal("2"),
        )


def test_build_correction_rows_rejects_asset_equal_to_currency():
    with pytest.raises(ValueError):
        build_correction_rows(
            timestamp=_TS, correction_of="TRADE_1", asset="CZK", currency="CZK",
            delta=Decimal("75"), venue="revolut", reason="r",
            original_value=Decimal("1"), corrected_value=Decimal("2"),
        )


# ── validate_correction_group() ─────────────────────────────────────────────

def _valid_group(**overrides):
    marker, fiat_leg = build_correction_rows(
        timestamp=_TS, correction_of="TRADE_1", asset="HYPE", currency="CZK",
        delta=Decimal("75"), venue="revolut", reason="r",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
        account=overrides.get("account"),
    )
    return marker, fiat_leg


def test_valid_group_passes():
    marker, fiat_leg = _valid_group()
    ok, errs = validate_correction_group([marker, fiat_leg])
    assert ok, errs
    assert errs == []


def test_marker_without_fiat_leg_is_invalid():
    marker, _fiat_leg = _valid_group()
    ok, errs = validate_correction_group([marker])
    assert not ok
    assert any("fiat delta leg" in e for e in errs)


def test_fiat_leg_without_marker_is_invalid():
    _marker, fiat_leg = _valid_group()
    ok, errs = validate_correction_group([fiat_leg])
    assert not ok
    assert any("position marker" in e for e in errs)


def test_two_marker_legs_is_invalid():
    marker1, fiat_leg = _valid_group()
    marker2, _ = _valid_group()
    ok, errs = validate_correction_group([marker1, marker2, fiat_leg])
    assert not ok
    assert any("position marker" in e for e in errs)


def test_currency_mismatch_is_invalid():
    marker, fiat_leg = _valid_group()
    fiat_leg.currency = "EUR"
    ok, errs = validate_correction_group([marker, fiat_leg])
    assert not ok
    assert any("currency mismatch" in e for e in errs)


def test_venue_mismatch_is_invalid():
    marker, fiat_leg = _valid_group()
    fiat_leg.venue = "kraken"
    ok, errs = validate_correction_group([marker, fiat_leg])
    assert not ok
    assert any("venue mismatch" in e for e in errs)


def test_account_mismatch_is_invalid():
    marker, fiat_leg = _valid_group()
    marker.account = "Osobní CZK"
    fiat_leg.account = "Investment CZK"
    ok, errs = validate_correction_group([marker, fiat_leg])
    assert not ok
    assert any("account mismatch" in e for e in errs)


def test_zero_delta_fiat_leg_is_invalid():
    marker, fiat_leg = _valid_group()
    fiat_leg.amount = Decimal("0")
    ok, errs = validate_correction_group([marker, fiat_leg])
    assert not ok
    assert any("nesmí být 0" in e for e in errs)


def test_nonzero_marker_amount_is_invalid():
    marker, fiat_leg = _valid_group()
    marker.amount = Decimal("2")
    ok, errs = validate_correction_group([marker, fiat_leg])
    assert not ok
    assert any("musí být 0" in e for e in errs)


def test_empty_rows_list_is_invalid():
    ok, errs = validate_correction_group([])
    assert not ok
