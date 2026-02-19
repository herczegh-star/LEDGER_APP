"""Unit tests for core/services/report_service.py."""
from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow
from core.dto.reporting import TimeSeriesReport
from core.services.report_service import ReportKind, get_report


def _row(ts, type_, asset, amount, currency="EUR", venue="testx", id_="tx1"):
    return RawRow(
        timestamp=ts,
        type=type_,
        asset=asset,
        amount=Decimal(str(amount)),
        currency=currency,
        price=None,
        venue=venue,
        note=None,
        id=id_,
    )


ROWS = [
    _row(datetime(2026, 1, 10), "BUY",  "EUR", "-20000", id_="t1"),
    _row(datetime(2026, 1, 20), "SELL", "EUR",  "+5000", id_="t2"),
    _row(datetime(2026, 2,  5), "BUY",  "EUR", "-10000", id_="t3"),
]


# ── ReportKind ────────────────────────────────────────────────────────────────

def test_report_kind_values():
    assert ReportKind.CASHFLOW.value       == "cashflow"
    assert ReportKind.NETTO_INVESTED.value == "netto_invested"


def test_report_kind_from_string():
    assert ReportKind("cashflow")        is ReportKind.CASHFLOW
    assert ReportKind("netto_invested")  is ReportKind.NETTO_INVESTED


# ── get_report – CASHFLOW ─────────────────────────────────────────────────────

def test_get_report_cashflow_returns_time_series_report():
    report = get_report(ROWS, ReportKind.CASHFLOW, bucket="month")
    assert isinstance(report, TimeSeriesReport)


def test_get_report_cashflow_meta():
    report = get_report(ROWS, ReportKind.CASHFLOW, bucket="month")
    assert report.meta.bucket == "month"


def test_get_report_cashflow_row_count():
    report = get_report(ROWS, ReportKind.CASHFLOW, bucket="month")
    assert len(report.rows) == 2   # Jan + Feb


def test_get_report_cashflow_totals():
    report = get_report(ROWS, ReportKind.CASHFLOW, bucket="month", fiat={"EUR"})
    assert report.totals is not None
    assert report.totals["EUR"]["net_amount"] == Decimal("-25000")


def test_get_report_cashflow_empty():
    report = get_report([], ReportKind.CASHFLOW, bucket="day")
    assert report.rows == []
    assert report.totals is None


# ── get_report – NETTO_INVESTED ───────────────────────────────────────────────

def test_get_report_netto_invested_returns_time_series_report():
    report = get_report(ROWS, ReportKind.NETTO_INVESTED, bucket="month")
    assert isinstance(report, TimeSeriesReport)


def test_get_report_netto_invested_keys():
    report = get_report(ROWS, ReportKind.NETTO_INVESTED, bucket="month")
    for r in report.rows:
        assert "invested_amount" in r.values
        assert "inflow_amount"   in r.values
        assert "net_flow"        in r.values


def test_get_report_netto_invested_totals():
    report = get_report(ROWS, ReportKind.NETTO_INVESTED, bucket="month", fiat={"EUR"})
    eur = report.totals["EUR"]
    assert eur["invested_amount"] == Decimal("30000")
    assert eur["inflow_amount"]   == Decimal("5000")
    assert eur["net_flow"]        == Decimal("-25000")


def test_get_report_netto_invested_empty():
    report = get_report([], ReportKind.NETTO_INVESTED, bucket="month")
    assert report.rows == []
    assert report.totals is None


# ── get_report – custom fiat ──────────────────────────────────────────────────

def test_get_report_custom_fiat_filters():
    """Only EUR rows should appear when fiat={'EUR'}."""
    report = get_report(ROWS, ReportKind.CASHFLOW, bucket="month", fiat={"EUR"})
    currencies = {r.currency for r in report.rows}
    assert currencies == {"EUR"}


def test_get_report_unknown_fiat_no_rows():
    """Rows in EUR won't appear if fiat={'CZK'}."""
    report = get_report(ROWS, ReportKind.CASHFLOW, bucket="month", fiat={"CZK"})
    assert report.rows == []


# ── get_report – invalid kind ─────────────────────────────────────────────────

def test_get_report_invalid_kind_raises():
    with pytest.raises((ValueError, Exception)):
        get_report(ROWS, "unknown_kind", bucket="month")  # type: ignore[arg-type]
