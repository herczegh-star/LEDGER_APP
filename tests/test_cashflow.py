"""Tests for core/reports/cashflow.py"""
from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.reports.cashflow import cashflow, CashflowRow


def _row(ts, type_, asset, amount, currency="EUR", venue="anycoin", id_="tx1", note=None):
    return RawRow(
        timestamp=ts,
        type=type_,
        asset=asset,
        amount=Decimal(str(amount)),
        currency=currency,
        price=None,
        venue=venue,
        note=note,
        id=id_,
    )


# ── TEST 1: BUY fiat leg is negative (outflow) ────────────────────────────────

def test_buy_fiat_leg_is_outflow():
    """BUY double-entry: EUR leg has negative amount → cashflow outflow."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY", "EUR", "-25000", currency="BTC", id_="tx1"),
        _row(datetime(2026, 1, 10), "BUY", "BTC", "0.5",    currency="EUR", id_="tx1"),
    ]
    result = cashflow(rows, bucket="day")
    assert len(result) == 1
    assert result[0].currency == "EUR"
    assert result[0].net_amount == Decimal("-25000")
    assert result[0].date == "2026-01-10"


# ── TEST 2: SELL fiat leg is positive (inflow) ────────────────────────────────

def test_sell_fiat_leg_is_inflow():
    """SELL double-entry: EUR leg has positive amount → cashflow inflow."""
    rows = [
        _row(datetime(2026, 2, 5), "SELL", "EUR", "30000", currency="BTC", id_="tx2"),
        _row(datetime(2026, 2, 5), "SELL", "BTC", "-0.5",  currency="EUR", id_="tx2"),
    ]
    result = cashflow(rows, bucket="day")
    assert len(result) == 1
    assert result[0].currency == "EUR"
    assert result[0].net_amount == Decimal("30000")


# ── TEST 3: FEE in fiat reduces cashflow ──────────────────────────────────────

def test_fee_in_fiat_is_outflow():
    """FEE row with fiat asset contributes negative cashflow."""
    rows = [
        _row(datetime(2026, 1, 15), "FEE", "EUR", "-5", currency="EUR", id_="fee1"),
    ]
    result = cashflow(rows, bucket="day")
    assert len(result) == 1
    assert result[0].net_amount == Decimal("-5")


def test_fee_in_non_fiat_is_ignored():
    """FEE row with non-fiat asset (e.g. BTC fee) is excluded."""
    rows = [
        _row(datetime(2026, 1, 15), "FEE", "BTC", "-0.001", currency="BTC", id_="fee2"),
    ]
    result = cashflow(rows, bucket="day")
    assert result == []


# ── TEST 4: REVERSAL cancels original ─────────────────────────────────────────

def test_reversal_cancels_in_same_bucket():
    """REVERSAL with opposite sign nets to zero → excluded from result."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY",      "EUR", "-25000", id_="tx1"),
        _row(datetime(2026, 1, 12), "REVERSAL",  "EUR",  "25000", id_="rev1"),
    ]
    result = cashflow(rows, bucket="month")
    # Both fall in 2026-01, net = 0 → excluded
    assert result == []


def test_reversal_in_different_bucket_shows_separately():
    """REVERSAL in a later month appears as separate inflow bucket."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY",      "EUR", "-25000", id_="tx1"),
        _row(datetime(2026, 2, 5),  "REVERSAL",  "EUR",  "25000", id_="rev1"),
    ]
    result = cashflow(rows, bucket="month")
    assert len(result) == 2
    jan = next(r for r in result if r.date == "2026-01")
    feb = next(r for r in result if r.date == "2026-02")
    assert jan.net_amount == Decimal("-25000")
    assert feb.net_amount == Decimal("25000")


# ── TEST 5: Bucketing groups correctly ────────────────────────────────────────

def test_month_bucket_aggregates_multiple_days():
    """Multiple transactions in the same month are aggregated into one bucket."""
    rows = [
        _row(datetime(2026, 1, 5),  "BUY", "EUR", "-10000", id_="t1"),
        _row(datetime(2026, 1, 20), "BUY", "EUR", "-15000", id_="t2"),
        _row(datetime(2026, 2, 1),  "BUY", "EUR",  "-5000", id_="t3"),
    ]
    result = cashflow(rows, bucket="month")
    assert len(result) == 2
    jan = next(r for r in result if r.date == "2026-01")
    feb = next(r for r in result if r.date == "2026-02")
    assert jan.net_amount == Decimal("-25000")
    assert feb.net_amount == Decimal("-5000")


def test_day_bucket_keeps_days_separate():
    """Day bucket does not aggregate across dates."""
    rows = [
        _row(datetime(2026, 1, 5),  "BUY", "EUR", "-10000", id_="t1"),
        _row(datetime(2026, 1, 6),  "BUY", "EUR", "-20000", id_="t2"),
    ]
    result = cashflow(rows, bucket="day")
    assert len(result) == 2
    assert {r.date for r in result} == {"2026-01-05", "2026-01-06"}


def test_week_bucket():
    """Week bucket groups by ISO week."""
    rows = [
        _row(datetime(2026, 1, 5),  "BUY", "EUR", "-10000", id_="t1"),  # week 02
        _row(datetime(2026, 1, 7),  "BUY", "EUR",  "-5000", id_="t2"),  # week 02
        _row(datetime(2026, 1, 12), "BUY", "EUR",  "-8000", id_="t3"),  # week 03
    ]
    result = cashflow(rows, bucket="week")
    assert len(result) == 2
    dates = {r.date for r in result}
    assert "2026-W02" in dates
    assert "2026-W03" in dates
    w02 = next(r for r in result if r.date == "2026-W02")
    assert w02.net_amount == Decimal("-15000")


# ── TEST 6: Multiple currencies ───────────────────────────────────────────────

def test_multiple_fiat_currencies_separate_rows():
    """EUR and CZK flows appear as separate CashflowRow entries."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY", "EUR", "-25000", id_="t1"),
        _row(datetime(2026, 1, 10), "FEE", "CZK",   "-500", currency="CZK", id_="t2"),
    ]
    result = cashflow(rows, bucket="day")
    assert len(result) == 2
    eur = next(r for r in result if r.currency == "EUR")
    czk = next(r for r in result if r.currency == "CZK")
    assert eur.net_amount == Decimal("-25000")
    assert czk.net_amount == Decimal("-500")


# ── TEST 7: Empty ledger ──────────────────────────────────────────────────────

def test_empty_rows_returns_empty():
    assert cashflow([]) == []


# ── TEST 8: Custom fiat set ───────────────────────────────────────────────────

def test_custom_fiat_set():
    """custom fiat set overrides the default EUR/CZK."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY", "EUR",  "-25000", id_="t1"),
        _row(datetime(2026, 1, 10), "BUY", "USDT", "-500",   id_="t2"),
    ]
    # Only USDT in custom set → EUR ignored
    result = cashflow(rows, fiat={"USDT"})
    assert len(result) == 1
    assert result[0].currency == "USDT"
