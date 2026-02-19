"""Tests for TimeSeriesReport DTOs returned by cashflow_report() and netto_invested_report()."""
from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.dto.reporting import ReportMeta, TimeSeriesRow, TimeSeriesReport
from core.reports.cashflow import cashflow_report
from core.reports.netto_invested import netto_invested_report


def _row(ts, type_, asset, amount, currency="EUR", venue="anycoin", id_="tx1"):
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


ROWS_BUY_SELL = [
    _row(datetime(2026, 1, 10), "BUY",  "EUR", "-25000", id_="t1"),
    _row(datetime(2026, 1, 15), "SELL", "EUR",  "+5000", id_="t2"),
    _row(datetime(2026, 2, 3),  "BUY",  "EUR", "-10000", id_="t3"),
]


# ── cashflow_report ────────────────────────────────────────────────────────────

def test_cashflow_report_meta_bucket():
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    assert report.meta.bucket == "month"


def test_cashflow_report_meta_fiat_default():
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    assert "EUR" in report.meta.fiat
    assert "CZK" in report.meta.fiat


def test_cashflow_report_meta_fiat_custom():
    report = cashflow_report(ROWS_BUY_SELL, bucket="month", fiat={"EUR"})
    assert report.meta.fiat == frozenset({"EUR"})


def test_cashflow_report_rows_sorted():
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    dates = [r.date for r in report.rows]
    assert dates == sorted(dates)


def test_cashflow_report_values_key():
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    for r in report.rows:
        assert "net_amount" in r.values
        assert isinstance(r.values["net_amount"], Decimal)


def test_cashflow_report_values_correct():
    """Jan net = -25000+5000 = -20000; Feb = -10000."""
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    assert len(report.rows) == 2
    jan = next(r for r in report.rows if r.date == "2026-01")
    feb = next(r for r in report.rows if r.date == "2026-02")
    assert jan.values["net_amount"] == Decimal("-20000")
    assert feb.values["net_amount"] == Decimal("-10000")


def test_cashflow_report_totals_correct():
    """totals["EUR"]["net_amount"] = sum of all net_amounts = -30000."""
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    assert report.totals is not None
    assert "EUR" in report.totals
    assert report.totals["EUR"]["net_amount"] == Decimal("-30000")


def test_cashflow_report_totals_match_row_sum():
    """totals must equal sum of rows.values for each metric."""
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    for cur, metrics in report.totals.items():
        for metric, total in metrics.items():
            row_sum = sum(
                r.values[metric] for r in report.rows if r.currency == cur
            )
            assert total == row_sum, f"{cur}.{metric}: totals {total} != row_sum {row_sum}"


def test_cashflow_report_empty():
    report = cashflow_report([], bucket="day")
    assert report.rows == []
    assert report.totals is None
    assert report.meta.bucket == "day"


# ── netto_invested_report ──────────────────────────────────────────────────────

def test_netto_invested_report_meta_bucket():
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    assert report.meta.bucket == "month"


def test_netto_invested_report_meta_fiat_default():
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    assert "EUR" in report.meta.fiat


def test_netto_invested_report_rows_sorted():
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    dates = [r.date for r in report.rows]
    assert dates == sorted(dates)


def test_netto_invested_report_values_keys():
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    for r in report.rows:
        assert "invested_amount" in r.values
        assert "inflow_amount" in r.values
        assert "net_flow" in r.values
        for v in r.values.values():
            assert isinstance(v, Decimal)


def test_netto_invested_report_values_correct():
    """Jan: BUY -25000, SELL +5000 → invested=25000, inflow=5000, net=-20000.
       Feb: BUY -10000            → invested=10000, inflow=0,    net=-10000.
    """
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    assert len(report.rows) == 2
    jan = next(r for r in report.rows if r.date == "2026-01")
    feb = next(r for r in report.rows if r.date == "2026-02")

    assert jan.values["invested_amount"] == Decimal("25000")
    assert jan.values["inflow_amount"]   == Decimal("5000")
    assert jan.values["net_flow"]        == Decimal("-20000")

    assert feb.values["invested_amount"] == Decimal("10000")
    assert feb.values["inflow_amount"]   == Decimal("0")
    assert feb.values["net_flow"]        == Decimal("-10000")


def test_netto_invested_report_totals_correct():
    """totals["EUR"] sums all rows."""
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    assert report.totals is not None
    eur = report.totals["EUR"]
    assert eur["invested_amount"] == Decimal("35000")
    assert eur["inflow_amount"]   == Decimal("5000")
    assert eur["net_flow"]        == Decimal("-30000")


def test_netto_invested_report_totals_match_row_sum():
    """totals must equal sum of rows.values for each metric."""
    report = netto_invested_report(ROWS_BUY_SELL, bucket="month")
    for cur, metrics in report.totals.items():
        for metric, total in metrics.items():
            row_sum = sum(
                r.values[metric] for r in report.rows if r.currency == cur
            )
            assert total == row_sum, f"{cur}.{metric}: {total} != {row_sum}"


def test_netto_invested_report_empty():
    report = netto_invested_report([], bucket="day")
    assert report.rows == []
    assert report.totals is None


# ── TimeSeriesReport is a proper dataclass ────────────────────────────────────

def test_report_is_dataclass_instance():
    report = cashflow_report(ROWS_BUY_SELL, bucket="month")
    assert isinstance(report, TimeSeriesReport)
    assert isinstance(report.meta, ReportMeta)
    assert all(isinstance(r, TimeSeriesRow) for r in report.rows)
