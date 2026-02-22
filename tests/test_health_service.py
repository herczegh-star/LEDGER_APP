"""Tests for core/services/health_service.py."""
from datetime import datetime
from decimal import Decimal
from typing import List
import uuid

import pytest

from core.model import RawRow
from core.dto.reporting import TableReport
from core.services.health_service import health_report, ERROR, WARNING


# ── Helpers ───────────────────────────────────────────────────────────────────

_TS = datetime(2026, 1, 15, 10, 0, 0)


def _tid() -> str:
    return str(uuid.uuid4())


def _buy(tid: str, ts=_TS) -> List[RawRow]:
    """Normal 2-row BUY trade: BTC investment + EUR quote."""
    return [
        RawRow(id=tid, timestamp=ts, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="kraken"),
        RawRow(id=tid, timestamp=ts, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="kraken"),
    ]


def _sell(tid: str, ts=_TS) -> List[RawRow]:
    """Normal 2-row SELL trade: BTC investment + EUR quote."""
    return [
        RawRow(id=tid, timestamp=ts, type="SELL", asset="BTC",
               amount=Decimal("-1"), currency="EUR",
               price=Decimal("50000"), venue="kraken"),
        RawRow(id=tid, timestamp=ts, type="SELL", asset="EUR",
               amount=Decimal("50000"), currency="EUR",
               price=Decimal("1"), venue="kraken"),
    ]


def _issues(rows, fiat=None) -> list:
    report = health_report(rows, fiat=fiat)
    return [r.values for r in report.rows]


def _kinds(rows, fiat=None) -> list:
    return [i["kind"] for i in _issues(rows, fiat=fiat)]


def _severities(rows, fiat=None) -> list:
    return [i["severity"] for i in _issues(rows, fiat=fiat)]


# ── 1. Return type ────────────────────────────────────────────────────────────

def test_returns_table_report():
    assert isinstance(health_report([]), TableReport)


def test_meta_kind_is_snapshot():
    assert health_report([]).meta.kind == "snapshot"


def test_meta_bucket_is_snapshot():
    assert health_report([]).meta.bucket == "snapshot"


# ── 2. Empty ledger ───────────────────────────────────────────────────────────

def test_empty_ledger_no_issues():
    report = health_report([])
    assert len(report.rows) == 0


# ── 3. Valid trade → no issues ────────────────────────────────────────────────

def test_valid_buy_trade_no_issues():
    tid = _tid()
    rows = _buy(tid)
    assert len(health_report(rows).rows) == 0


def test_valid_buy_and_sell_no_issues():
    buy_tid = _tid()
    sell_tid = _tid()
    ts2 = datetime(2026, 2, 1, 10, 0, 0)
    rows = _buy(buy_tid) + _sell(sell_tid, ts=ts2)
    assert len(health_report(rows).rows) == 0


# ── 4. Check 1: Missing quote leg ────────────────────────────────────────────

def test_missing_quote_leg_produces_error():
    tid = _tid()
    # Only BTC leg, no EUR leg
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
    ]
    kinds = _kinds(rows)
    assert "missing_quote_leg" in kinds


def test_missing_quote_leg_severity_is_error():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
    ]
    issues = _issues(rows)
    mql = [i for i in issues if i["kind"] == "missing_quote_leg"]
    assert all(i["severity"] == ERROR for i in mql)


def test_missing_quote_leg_trade_id_populated():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
    ]
    issues = _issues(rows)
    mql = [i for i in issues if i["kind"] == "missing_quote_leg"]
    assert mql[0]["trade_id"] == tid


# ── 5. Check 2: Missing investment leg ───────────────────────────────────────

def test_missing_investment_leg_produces_error():
    tid = _tid()
    # Only EUR fiat leg, no BTC leg
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    kinds = _kinds(rows)
    assert "missing_investment_leg" in kinds


def test_missing_investment_leg_severity_is_error():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    issues = _issues(rows)
    mil = [i for i in issues if i["kind"] == "missing_investment_leg"]
    assert all(i["severity"] == ERROR for i in mil)


def test_no_investment_leg_error_for_valid_trade():
    tid = _tid()
    rows = _buy(tid)
    kinds = _kinds(rows)
    assert "missing_investment_leg" not in kinds


# ── 6. Check 3: Multiple fiat currencies ─────────────────────────────────────

def test_multi_fiat_quote_produces_warning():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-20000"), currency="EUR",
               price=Decimal("1"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="CZK",
               amount=Decimal("-500000"), currency="CZK",
               price=Decimal("1"), venue="test"),
    ]
    kinds = _kinds(rows)
    assert "multi_fiat_quote" in kinds


def test_multi_fiat_quote_severity_is_warning():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-20000"), currency="EUR",
               price=Decimal("1"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="CZK",
               amount=Decimal("-500000"), currency="CZK",
               price=Decimal("1"), venue="test"),
    ]
    issues = _issues(rows)
    mfq = [i for i in issues if i["kind"] == "multi_fiat_quote"]
    assert all(i["severity"] == WARNING for i in mfq)


def test_single_fiat_quote_no_multi_fiat_warning():
    tid = _tid()
    rows = _buy(tid)
    kinds = _kinds(rows)
    assert "multi_fiat_quote" not in kinds


# ── 7. Check 4: Fiat as investment leg ───────────────────────────────────────

def test_fiat_as_investment_leg_buy_positive():
    """EUR with positive amount in BUY = fiat being bought as investment."""
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("-1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("+40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    kinds = _kinds(rows)
    assert "fiat_as_investment_leg" in kinds


def test_fiat_as_investment_leg_severity_is_error():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("-1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("+40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    issues = _issues(rows)
    fail = [i for i in issues if i["kind"] == "fiat_as_investment_leg"]
    assert all(i["severity"] == ERROR for i in fail)


def test_normal_sell_proceeds_not_flagged():
    """SELL with positive EUR amount = normal proceeds, must NOT be flagged."""
    buy_tid = _tid()
    sell_tid = _tid()
    ts2 = datetime(2026, 2, 1)
    rows = _buy(buy_tid) + _sell(sell_tid, ts=ts2)
    kinds = _kinds(rows)
    assert "fiat_as_investment_leg" not in kinds


# ── 8. Check 5: Zero amount ───────────────────────────────────────────────────

def test_zero_amount_produces_warning():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("0"), currency="EUR",
               price=Decimal("40000"), venue="test"),
    ]
    kinds = _kinds(rows)
    assert "zero_amount" in kinds


def test_zero_amount_severity_is_warning():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("0"), currency="EUR",
               price=Decimal("40000"), venue="test"),
    ]
    issues = _issues(rows)
    za = [i for i in issues if i["kind"] == "zero_amount"]
    assert all(i["severity"] == WARNING for i in za)


def test_nonzero_amount_no_zero_warning():
    tid = _tid()
    rows = _buy(tid)
    kinds = _kinds(rows)
    assert "zero_amount" not in kinds


# ── 9. Check 6: Missing timestamp ────────────────────────────────────────────

def test_missing_timestamp_produces_error():
    row = RawRow(
        id=_tid(),
        timestamp=None,   # type: ignore[arg-type]
        type="BUY",
        asset="BTC",
        amount=Decimal("1"),
        currency="EUR",
        price=Decimal("40000"),
        venue="test",
    )
    kinds = _kinds([row])
    assert "missing_timestamp" in kinds


def test_missing_timestamp_severity_is_error():
    row = RawRow(
        id=_tid(),
        timestamp=None,   # type: ignore[arg-type]
        type="BUY",
        asset="BTC",
        amount=Decimal("1"),
        currency="EUR",
        price=Decimal("40000"),
        venue="test",
    )
    issues = _issues([row])
    mt = [i for i in issues if i["kind"] == "missing_timestamp"]
    assert all(i["severity"] == ERROR for i in mt)


# ── 10. Check 7: Oversell ─────────────────────────────────────────────────────

def test_oversell_produces_error():
    """Sell more BTC than bought → oversell issue."""
    buy_tid = _tid()
    sell_tid = _tid()
    ts2 = datetime(2026, 2, 1)
    rows = [
        # Buy 1 BTC
        RawRow(id=buy_tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=buy_tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
        # Sell 2 BTC (oversell!)
        RawRow(id=sell_tid, timestamp=ts2, type="SELL", asset="BTC",
               amount=Decimal("-2"), currency="EUR",
               price=Decimal("50000"), venue="test"),
        RawRow(id=sell_tid, timestamp=ts2, type="SELL", asset="EUR",
               amount=Decimal("100000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    kinds = _kinds(rows)
    assert "oversell" in kinds


def test_oversell_severity_is_error():
    buy_tid = _tid()
    sell_tid = _tid()
    ts2 = datetime(2026, 2, 1)
    rows = [
        RawRow(id=buy_tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=buy_tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
        RawRow(id=sell_tid, timestamp=ts2, type="SELL", asset="BTC",
               amount=Decimal("-2"), currency="EUR",
               price=Decimal("50000"), venue="test"),
        RawRow(id=sell_tid, timestamp=ts2, type="SELL", asset="EUR",
               amount=Decimal("100000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    issues = _issues(rows)
    ov = [i for i in issues if i["kind"] == "oversell"]
    assert all(i["severity"] == ERROR for i in ov)


def test_no_oversell_when_balanced():
    buy_tid = _tid()
    sell_tid = _tid()
    ts2 = datetime(2026, 2, 1)
    rows = _buy(buy_tid) + _sell(sell_tid, ts=ts2)
    kinds = _kinds(rows)
    assert "oversell" not in kinds


# ── 11. Issue key format ──────────────────────────────────────────────────────

def test_issue_keys_are_sequential():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("0"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("0"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    report = health_report(rows)
    keys = [r.key for r in report.rows]
    assert keys == [f"ISSUE_{i:04d}" for i in range(1, len(keys) + 1)]


def test_issue_values_have_required_keys():
    tid = _tid()
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("0"), currency="EUR",
               price=Decimal("40000"), venue="test"),
    ]
    issues = _issues(rows)
    required = {"severity", "kind", "trade_id", "asset", "timestamp", "message", "hint"}
    for issue in issues:
        assert required.issubset(issue.keys())


# ── 12. Deterministic ordering ────────────────────────────────────────────────

def test_errors_before_warnings():
    """All ERROR issues must appear before any WARNING issues."""
    tid = _tid()
    rows = [
        # zero_amount → WARNING
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("0"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        # missing_investment_leg → ERROR (fiat-only trade)
        RawRow(id=_tid(), timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    sevs = _severities(rows)
    # Find first WARNING index; all before it must be ERROR
    if WARNING in sevs:
        first_warn = sevs.index(WARNING)
        assert all(s == ERROR for s in sevs[:first_warn])


def test_same_input_same_output_order():
    """health_report is deterministic: same rows → same output each call."""
    buy_tid = _tid()
    tid2 = _tid()
    rows = (
        [RawRow(id=buy_tid, timestamp=_TS, type="BUY", asset="BTC",
                amount=Decimal("1"), currency="EUR",
                price=Decimal("40000"), venue="test")]
        + [RawRow(id=tid2, timestamp=_TS, type="BUY", asset="EUR",
                  amount=Decimal("0"), currency="EUR",
                  price=Decimal("1"), venue="test")]
    )
    report1 = health_report(rows)
    report2 = health_report(rows)
    assert [(r.key, r.values["kind"]) for r in report1.rows] == \
           [(r.key, r.values["kind"]) for r in report2.rows]


# ── 13. Custom fiat set ───────────────────────────────────────────────────────

def test_custom_fiat_respected():
    """With fiat={"USD"}, EUR is treated as non-fiat."""
    tid = _tid()
    rows = [
        # BTC + EUR trade — with fiat={"USD"}, EUR is non-fiat
        # so this trade has no USD quote leg → missing_quote_leg
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]
    # With default fiat, this is valid
    assert len(health_report(rows).rows) == 0
    # With fiat={"USD"}, EUR becomes non-fiat → both legs are non-fiat
    # → no quote leg (neither is USD) → missing_quote_leg
    issues_usd = _issues(rows, fiat={"USD"})
    kinds = [i["kind"] for i in issues_usd]
    assert "missing_quote_leg" in kinds


def test_meta_fiat_matches_custom_fiat():
    report = health_report([], fiat={"USD"})
    assert report.meta.fiat == frozenset({"USD"})
