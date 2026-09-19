"""Phase BUY-FIX — core/buy_cost_correction.py: pure unit tests.

Mirrors tests/test_correction.py's structure for the sibling CORRECTION
type, but for BUY_COST_CORRECTION's narrower invariant:
cost_basis_delta = -cash_delta (never an independent input).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from core.buy_cost_correction import (
    build_buy_cost_correction_note,
    build_buy_cost_correction_rows,
    is_fully_open_since,
    parse_buy_cost_correction_note,
    resolve_historical_effective_timestamp,
    validate_buy_cost_correction_group,
)
from core.model import RawRow

_TS = datetime(2026, 9, 9, 21, 22, 39)


# ── Note round-trip ──────────────────────────────────────────────────────────

def test_note_round_trip():
    note = build_buy_cost_correction_note(
        correction_of="20260909_212239_REVOLUT_BUY_001",
        reason="revolut_total_cash_debited_misread_as_order_value",
        original_value=Decimal("5089"),
        corrected_value=Decimal("5000"),
        cash_delta=Decimal("89"),
    )
    parsed = parse_buy_cost_correction_note(note)
    assert parsed["correction_of"] == "20260909_212239_REVOLUT_BUY_001"
    assert parsed["reason"] == "revolut_total_cash_debited_misread_as_order_value"
    assert parsed["original"] == Decimal("5089")
    assert parsed["corrected"] == Decimal("5000")
    assert parsed["cash_delta"] == Decimal("89")
    assert parsed["cost_basis_delta"] == Decimal("-89")


def test_parse_unparseable_note_returns_none():
    assert parse_buy_cost_correction_note("just a plain note") is None
    assert parse_buy_cost_correction_note(None) is None
    assert parse_buy_cost_correction_note("") is None


def test_parse_does_not_confuse_with_plain_correction_note():
    """A CORRECTION note (different prefix) must not be misparsed as a
    BUY_COST_CORRECTION note — the two types must stay fully distinguishable."""
    from core.correction import build_correction_note
    plain_note = build_correction_note(
        "some_id", "reason", Decimal("1"), Decimal("2"), Decimal("1")
    )
    assert parse_buy_cost_correction_note(plain_note) is None


# ── build_buy_cost_correction_rows() ────────────────────────────────────────

def test_build_rows_happy_path():
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="20260909_212239_REVOLUT_BUY_001",
        asset="ARB", currency="CZK", cash_delta=Decimal("89"), venue="revolut",
        reason="r", original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        account="Osobní CZK",
    )
    assert marker.type == "BUY_COST_CORRECTION"
    assert marker.asset == "ARB"
    assert marker.amount == Decimal("0")
    assert marker.account is None  # crypto leg never carries a cash account

    assert cash_leg.type == "BUY_COST_CORRECTION"
    assert cash_leg.asset == "CZK"
    assert cash_leg.amount == Decimal("89")
    assert cash_leg.account == "Osobní CZK"

    assert marker.id == cash_leg.id
    assert marker.note == cash_leg.note


def test_build_rows_rejects_zero_delta():
    with pytest.raises(ValueError, match="cash_delta"):
        build_buy_cost_correction_rows(
            timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
            cash_delta=Decimal("0"), venue="revolut", reason="r",
            original_value=Decimal("1"), corrected_value=Decimal("1"),
        )


def test_build_rows_rejects_missing_correction_of():
    with pytest.raises(ValueError, match="correction_of"):
        build_buy_cost_correction_rows(
            timestamp=_TS, correction_of="", asset="ARB", currency="CZK",
            cash_delta=Decimal("10"), venue="revolut", reason="r",
            original_value=Decimal("1"), corrected_value=Decimal("1"),
        )


def test_build_rows_rejects_asset_equal_currency():
    with pytest.raises(ValueError, match="asset"):
        build_buy_cost_correction_rows(
            timestamp=_TS, correction_of="x", asset="CZK", currency="CZK",
            cash_delta=Decimal("10"), venue="revolut", reason="r",
            original_value=Decimal("1"), corrected_value=Decimal("1"),
        )


# ── validate_buy_cost_correction_group() ────────────────────────────────────

def test_validate_valid_group():
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        account="Osobní CZK",
    )
    ok, errs = validate_buy_cost_correction_group([marker, cash_leg])
    assert ok, errs
    assert errs == []


def test_validate_missing_marker():
    _, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    ok, errs = validate_buy_cost_correction_group([cash_leg])
    assert not ok
    assert any("marker" in e for e in errs)


def test_validate_missing_cash_leg():
    marker, _ = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    ok, errs = validate_buy_cost_correction_group([marker])
    assert not ok
    assert any("cash delta leg" in e for e in errs)


def test_validate_two_markers():
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    extra_marker = RawRow(
        id=marker.id, timestamp=_TS, type="BUY_COST_CORRECTION",
        asset="SOL", amount=Decimal("0"), currency="CZK", price=None, venue="revolut",
    )
    ok, errs = validate_buy_cost_correction_group([marker, extra_marker, cash_leg])
    assert not ok
    assert any("marker" in e for e in errs)


def test_validate_zero_delta_on_cash_leg_directly():
    marker = RawRow(
        id="X", timestamp=_TS, type="BUY_COST_CORRECTION",
        asset="ARB", amount=Decimal("0"), currency="CZK", price=None, venue="revolut",
    )
    cash_leg = RawRow(
        id="X", timestamp=_TS, type="BUY_COST_CORRECTION",
        asset="CZK", amount=Decimal("0"), currency="CZK", price=None, venue="revolut",
    )
    ok, errs = validate_buy_cost_correction_group([marker, cash_leg])
    assert not ok
    assert any("nesmí být 0" in e for e in errs)


def test_validate_venue_mismatch():
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    cash_leg.venue = "kraken"
    ok, errs = validate_buy_cost_correction_group([marker, cash_leg])
    assert not ok
    assert any("venue mismatch" in e for e in errs)


def test_validate_currency_mismatch():
    marker, cash_leg = build_buy_cost_correction_rows(
        timestamp=_TS, correction_of="x", asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="r",
        original_value=Decimal("1"), corrected_value=Decimal("2"),
    )
    cash_leg.asset = "EUR"
    cash_leg.currency = "EUR"
    ok, errs = validate_buy_cost_correction_group([marker, cash_leg])
    assert not ok
    assert any("currency" in e for e in errs)


# ── is_fully_open_since() / resolve_historical_effective_timestamp() ───────

def test_is_fully_open_since_true_when_no_later_rows():
    rows = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB",
               amount=Decimal("100"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    assert is_fully_open_since(rows, "ARB", _TS) is True


def test_is_fully_open_since_false_when_later_sell_exists():
    later = _TS + timedelta(days=1)
    rows = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB",
               amount=Decimal("100"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="s1", timestamp=later, type="SELL", asset="ARB",
               amount=Decimal("-10"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    assert is_fully_open_since(rows, "ARB", _TS) is False


def test_is_fully_open_since_ignores_other_assets():
    later = _TS + timedelta(days=1)
    rows = [
        RawRow(id="s1", timestamp=later, type="SELL", asset="BTC",
               amount=Decimal("-1"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    assert is_fully_open_since(rows, "ARB", _TS) is True


def test_resolve_historical_effective_timestamp_default_epsilon():
    rows = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB",
               amount=Decimal("100"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    eff = resolve_historical_effective_timestamp(rows, "ARB", _TS)
    assert eff == _TS + timedelta(microseconds=1)


def test_resolve_historical_effective_timestamp_raises_on_conflict():
    """If another ARB row already occupies the (original, original+epsilon]
    instant, resolution must refuse rather than guess."""
    conflict_ts = _TS + timedelta(microseconds=1)
    rows = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB",
               amount=Decimal("100"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="s1", timestamp=conflict_ts, type="SELL", asset="ARB",
               amount=Decimal("-10"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    with pytest.raises(ValueError, match="Nelze bezpečně odvodit"):
        resolve_historical_effective_timestamp(rows, "ARB", _TS)
