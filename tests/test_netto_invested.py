"""Tests for core/reports/netto_invested.py"""
from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.reports.netto_invested import netto_invested, NettoInvestedRow


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


# ── TEST 1: BUY only → invested increases, net_flow negative ─────────────────

def test_buy_only_invested_and_negative_net():
    rows = [
        _row(datetime(2026, 1, 10), "BUY", "EUR", "-25000", id_="t1"),
    ]
    result = netto_invested(rows, bucket="day")
    assert len(result) == 1
    r = result[0]
    assert r.date == "2026-01-10"
    assert r.currency == "EUR"
    assert r.invested_amount == Decimal("25000")
    assert r.inflow_amount == Decimal("0")
    assert r.net_flow == Decimal("-25000")


# ── TEST 2: SELL only → invested = 0, net_flow positive ──────────────────────

def test_sell_only_zero_invested_positive_net():
    rows = [
        _row(datetime(2026, 2, 5), "SELL", "EUR", "30000", id_="t2"),
    ]
    result = netto_invested(rows, bucket="day")
    assert len(result) == 1
    r = result[0]
    assert r.invested_amount == Decimal("0")
    assert r.inflow_amount == Decimal("30000")
    assert r.net_flow == Decimal("30000")


# ── TEST 3: BUY + SELL same month bucket ─────────────────────────────────────

def test_buy_and_sell_same_month_bucket():
    """invested = gross BUY outflow, net_flow = BUY + SELL combined."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY",  "EUR", "-10000", id_="t1"),
        _row(datetime(2026, 1, 15), "SELL", "EUR",  "+5000", id_="t2"),
    ]
    result = netto_invested(rows, bucket="month")
    assert len(result) == 1
    r = result[0]
    assert r.date == "2026-01"
    assert r.invested_amount == Decimal("10000")   # only BUY outflow
    assert r.inflow_amount == Decimal("5000")       # only SELL inflow
    assert r.net_flow == Decimal("-5000")


# ── TEST 4: FEE in fiat counted as invested ───────────────────────────────────

def test_fee_fiat_counted_as_invested():
    rows = [
        _row(datetime(2026, 1, 15), "FEE", "EUR", "-5", currency="EUR", id_="fee1"),
    ]
    result = netto_invested(rows, bucket="day")
    assert len(result) == 1
    r = result[0]
    assert r.invested_amount == Decimal("5")
    assert r.inflow_amount == Decimal("0")
    assert r.net_flow == Decimal("-5")


def test_fee_non_fiat_ignored():
    """BTC fee is not in fiat set → no row in result."""
    rows = [
        _row(datetime(2026, 1, 15), "FEE", "BTC", "-0.001", currency="BTC", id_="fee2"),
    ]
    result = netto_invested(rows)
    assert result == []


# ── TEST 5: REVERSAL cancels within same bucket ───────────────────────────────

def test_reversal_cancels_in_same_bucket():
    """BUY and its REVERSAL in same month: invested and inflow both show, net=0."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY",     "EUR", "-25000", id_="t1"),
        _row(datetime(2026, 1, 12), "REVERSAL", "EUR", "+25000", id_="rev1"),
    ]
    result = netto_invested(rows, bucket="month")
    # Both in 2026-01; daily: Jan10=-25000, Jan12=+25000
    assert len(result) == 1
    r = result[0]
    assert r.invested_amount == Decimal("25000")
    assert r.inflow_amount == Decimal("25000")
    assert r.net_flow == Decimal("0")


def test_reversal_in_different_bucket():
    """REVERSAL in Feb shows as inflow in Feb, original BUY stays in Jan."""
    rows = [
        _row(datetime(2026, 1, 10), "BUY",     "EUR", "-25000", id_="t1"),
        _row(datetime(2026, 2, 5),  "REVERSAL", "EUR", "+25000", id_="rev1"),
    ]
    result = netto_invested(rows, bucket="month")
    assert len(result) == 2
    jan = next(r for r in result if r.date == "2026-01")
    feb = next(r for r in result if r.date == "2026-02")
    assert jan.invested_amount == Decimal("25000")
    assert jan.net_flow == Decimal("-25000")
    assert feb.inflow_amount == Decimal("25000")
    assert feb.net_flow == Decimal("25000")


# ── TEST 6: Bucketing groups correctly ────────────────────────────────────────

def test_month_bucket_aggregates_correctly():
    """Two BUY in Jan and one in Feb → two separate month rows."""
    rows = [
        _row(datetime(2026, 1, 5),  "BUY", "EUR", "-10000", id_="t1"),
        _row(datetime(2026, 1, 20), "BUY", "EUR", "-15000", id_="t2"),
        _row(datetime(2026, 2, 1),  "BUY", "EUR",  "-5000", id_="t3"),
    ]
    result = netto_invested(rows, bucket="month")
    assert len(result) == 2
    jan = next(r for r in result if r.date == "2026-01")
    feb = next(r for r in result if r.date == "2026-02")
    assert jan.invested_amount == Decimal("25000")
    assert feb.invested_amount == Decimal("5000")


def test_week_bucket():
    """Week bucket groups by ISO week."""
    rows = [
        _row(datetime(2026, 1, 5),  "BUY", "EUR", "-10000", id_="t1"),  # W02
        _row(datetime(2026, 1, 7),  "BUY", "EUR",  "-5000", id_="t2"),  # W02
        _row(datetime(2026, 1, 12), "BUY", "EUR",  "-8000", id_="t3"),  # W03
    ]
    result = netto_invested(rows, bucket="week")
    assert len(result) == 2
    w02 = next(r for r in result if r.date == "2026-W02")
    w03 = next(r for r in result if r.date == "2026-W03")
    assert w02.invested_amount == Decimal("15000")
    assert w03.invested_amount == Decimal("8000")


# ── TEST 7: Multiple currencies ───────────────────────────────────────────────

def test_multiple_currencies_separate_rows():
    rows = [
        _row(datetime(2026, 1, 10), "BUY", "EUR", "-25000", id_="t1"),
        _row(datetime(2026, 1, 10), "FEE", "CZK",   "-500", currency="CZK", id_="t2"),
    ]
    result = netto_invested(rows, bucket="day")
    assert len(result) == 2
    eur = next(r for r in result if r.currency == "EUR")
    czk = next(r for r in result if r.currency == "CZK")
    assert eur.invested_amount == Decimal("25000")
    assert czk.invested_amount == Decimal("500")


# ── TEST 8: Empty ledger ──────────────────────────────────────────────────────

def test_empty_returns_empty():
    assert netto_invested([]) == []
