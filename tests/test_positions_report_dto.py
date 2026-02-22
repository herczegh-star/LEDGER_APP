"""Tests for positions_report() → TableReport DTO contract.

All tests are pure (no DB). Row fixtures reuse the same helper pattern
as test_positions.py to create double-entry BUY/SELL rows.
"""
from datetime import datetime
from decimal import Decimal
from typing import List
import uuid

import pytest

from core.dto.reporting import ReportMeta, TableReport, TableRow
from core.model import RawRow
from core.reports.positions import compute_positions, positions_report
from core.services.report_service import get_positions_report, ReportKind


# ── Helpers ───────────────────────────────────────────────────────────────────

_TS = datetime(2026, 1, 1, 12, 0, 0)


def make_buy(
    asset: str,
    base_amount: Decimal,
    quote_currency: str,
    quote_amount: Decimal,
    ts: datetime = _TS,
) -> List[RawRow]:
    tid = str(uuid.uuid4())
    price = quote_amount / base_amount
    return [
        RawRow(id=tid, timestamp=ts, type="BUY", asset=asset.upper(),
               amount=base_amount, currency=quote_currency.upper(),
               price=price, venue="test"),
        RawRow(id=tid, timestamp=ts, type="BUY", asset=quote_currency.upper(),
               amount=-quote_amount, currency=quote_currency.upper(),
               price=Decimal("1"), venue="test"),
    ]


def make_sell(
    asset: str,
    base_amount: Decimal,
    quote_currency: str,
    quote_amount: Decimal,
    ts: datetime = _TS,
) -> List[RawRow]:
    tid = str(uuid.uuid4())
    price = quote_amount / base_amount
    return [
        RawRow(id=tid, timestamp=ts, type="SELL", asset=asset.upper(),
               amount=-base_amount, currency=quote_currency.upper(),
               price=price, venue="test"),
        RawRow(id=tid, timestamp=ts, type="SELL", asset=quote_currency.upper(),
               amount=quote_amount, currency=quote_currency.upper(),
               price=Decimal("1"), venue="test"),
    ]


# ── 1. Return type checks ─────────────────────────────────────────────────────

def test_returns_table_report():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    result = positions_report(rows)
    assert isinstance(result, TableReport)


def test_rows_are_table_rows():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    assert all(isinstance(r, TableRow) for r in report.rows)


# ── 2. Key / asset mapping ────────────────────────────────────────────────────

def test_key_is_asset_name():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    assert report.rows[0].key == "BTC"


def test_key_uppercase():
    rows = make_buy("eth", Decimal("1"), "EUR", Decimal("3000"))
    report = positions_report(rows)
    assert report.rows[0].key == "ETH"


# ── 3. Required value keys ────────────────────────────────────────────────────

_REQUIRED_KEYS = {"quantity", "wac", "cost_basis", "realized_pnl", "roi"}
# roi can be None (when cost_basis == 0); all other keys are always Decimal
_DECIMAL_KEYS = {"quantity", "wac", "cost_basis", "realized_pnl"}


def test_required_keys_present():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    assert _REQUIRED_KEYS.issubset(report.rows[0].values.keys())


def test_no_extra_unexpected_keys():
    """values should contain exactly the 5 required keys (no extras)."""
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    assert set(report.rows[0].values.keys()) == _REQUIRED_KEYS


# ── 4. Value types ────────────────────────────────────────────────────────────

def test_values_are_decimal():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    for key in _DECIMAL_KEYS:
        assert isinstance(report.rows[0].values[key], Decimal), (
            f"Expected Decimal for '{key}', got {type(report.rows[0].values[key])}"
        )


# ── 5. Sorting ────────────────────────────────────────────────────────────────

def test_sorted_alphabetically():
    rows = (
        make_buy("ETH", Decimal("1"), "EUR", Decimal("3000"))
        + make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
        + make_buy("ADA", Decimal("1000"), "EUR", Decimal("500"))
    )
    report = positions_report(rows)
    keys = [r.key for r in report.rows]
    assert keys == sorted(keys), f"Expected sorted keys, got: {keys}"


# ── 6. Empty ledger ───────────────────────────────────────────────────────────

def test_empty_rows_returns_table_report():
    result = positions_report([])
    assert isinstance(result, TableReport)


def test_empty_rows_has_no_table_rows():
    result = positions_report([])
    assert result.rows == []


# ── 7. Meta fields ────────────────────────────────────────────────────────────

def test_meta_kind_is_snapshot():
    report = positions_report([])
    assert report.meta.kind == "snapshot"


def test_meta_bucket_is_snapshot():
    report = positions_report([])
    assert report.meta.bucket == "snapshot"


def test_meta_fiat_default():
    report = positions_report([])
    assert report.meta.fiat == frozenset({"EUR", "CZK"})


def test_meta_is_report_meta():
    report = positions_report([])
    assert isinstance(report.meta, ReportMeta)


# ── 8. Fiat exclusion ────────────────────────────────────────────────────────

def test_fiat_eur_not_in_rows():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    keys = {r.key for r in report.rows}
    assert "EUR" not in keys


def test_fiat_czk_not_in_rows():
    rows = make_buy("BTC", Decimal("1"), "CZK", Decimal("1000000"))
    report = positions_report(rows)
    keys = {r.key for r in report.rows}
    assert "CZK" not in keys


# ── 9. Value correctness (cross-check with compute_positions) ─────────────────

def test_wac_matches_compute_positions():
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"),
                 ts=datetime(2026, 1, 1, 10, 0, 0))
        + make_buy("BTC", Decimal("1"), "EUR", Decimal("60000"),
                   ts=datetime(2026, 1, 2, 10, 0, 0))
    )
    report = positions_report(rows)
    engine = compute_positions(rows)

    pos_dto = report.rows[0]
    pos_engine = engine[0]

    assert pos_dto.values["wac"]          == pos_engine.wac
    assert pos_dto.values["cost_basis"]   == pos_engine.cost_basis
    assert pos_dto.values["quantity"]     == pos_engine.quantity
    assert pos_dto.values["realized_pnl"] == pos_engine.realized_pnl


def test_realized_pnl_correct_after_sell():
    # Buy 1 BTC @ 40000, sell 1 BTC @ 50000 → realized_pnl = 10000
    rows = (
        make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"),
                 ts=datetime(2026, 1, 1, 10, 0, 0))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"),
                    ts=datetime(2026, 1, 2, 10, 0, 0))
    )
    report = positions_report(rows)
    pos = report.rows[0]
    assert pos.values["realized_pnl"] == Decimal("10000")


# ── 10. Custom fiat forwarded ─────────────────────────────────────────────────

def test_custom_fiat_forwarded():
    rows = make_buy("ETH", Decimal("1"), "USD", Decimal("3000"))
    # USD as custom fiat; ETH should be in positions
    report = positions_report(rows, fiat=frozenset({"USD"}))
    keys = {r.key for r in report.rows}
    assert "ETH" in keys
    assert "USD" not in keys


def test_custom_fiat_in_meta():
    report = positions_report([], fiat=frozenset({"USD", "GBP"}))
    assert report.meta.fiat == frozenset({"USD", "GBP"})


# ── 11. Service dispatcher (get_positions_report) ────────────────────────────

def test_service_returns_table_report():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    result = get_positions_report(rows)
    assert isinstance(result, TableReport)


def test_service_default_fiat():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    result = get_positions_report(rows)
    assert result.meta.fiat == frozenset({"EUR", "CZK"})


def test_service_custom_fiat():
    rows = make_buy("ETH", Decimal("1"), "USD", Decimal("3000"))
    result = get_positions_report(rows, fiat={"USD"})
    keys = {r.key for r in result.rows}
    assert "ETH" in keys
    assert "USD" not in keys


# ── 12. ReportKind.POSITIONS exists ──────────────────────────────────────────

def test_report_kind_positions_exists():
    assert ReportKind.POSITIONS == "positions"


def test_report_kind_positions_value():
    assert ReportKind.POSITIONS.value == "positions"


# ── 13. ROI field ─────────────────────────────────────────────────────────────

def test_roi_positive():
    # Buy 2 BTC @ 80000 total (wac=40000), sell 1 BTC @ 50000
    # → pnl=10000, cost_basis_remaining=40000 → roi=25.00%
    rows = (
        make_buy("BTC", Decimal("2"), "EUR", Decimal("80000"),
                 ts=datetime(2026, 1, 1, 10, 0, 0))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("50000"),
                    ts=datetime(2026, 1, 2, 10, 0, 0))
    )
    report = positions_report(rows)
    pos = report.rows[0]
    assert pos.values["roi"] == Decimal("25.00")


def test_roi_negative():
    # Buy 2 BTC @ 100000 total (wac=50000), sell 1 BTC @ 40000
    # → pnl=-10000, cost_basis_remaining=50000 → roi=-20.00%
    rows = (
        make_buy("BTC", Decimal("2"), "EUR", Decimal("100000"),
                 ts=datetime(2026, 1, 1, 10, 0, 0))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("40000"),
                    ts=datetime(2026, 1, 2, 10, 0, 0))
    )
    report = positions_report(rows)
    pos = report.rows[0]
    assert pos.values["roi"] == Decimal("-20.00")


def test_roi_rounds_to_two_decimal_places():
    # Buy 2 BTC @ 60000 total (wac=30000), sell 1 BTC @ 40000
    # → pnl=10000, cost_basis_remaining=30000 → roi=33.33...% → 33.33
    rows = (
        make_buy("BTC", Decimal("2"), "EUR", Decimal("60000"),
                 ts=datetime(2026, 1, 1, 10, 0, 0))
        + make_sell("BTC", Decimal("1"), "EUR", Decimal("40000"),
                    ts=datetime(2026, 1, 2, 10, 0, 0))
    )
    report = positions_report(rows)
    pos = report.rows[0]
    assert pos.values["roi"] == Decimal("33.33")


def test_roi_is_none_when_cost_basis_zero():
    # Row without a fiat quote leg → cost_basis stays 0 → roi = None
    tid = str(uuid.uuid4())
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR", price=None, venue="test"),
        # No fiat leg → fiat_by_id has no entry → base_cost = 0
    ]
    report = positions_report(rows)
    assert len(report.rows) == 1
    assert report.rows[0].values["roi"] is None


def test_roi_is_decimal_when_cost_basis_nonzero():
    rows = make_buy("BTC", Decimal("1"), "EUR", Decimal("40000"))
    report = positions_report(rows)
    assert isinstance(report.rows[0].values["roi"], Decimal)
