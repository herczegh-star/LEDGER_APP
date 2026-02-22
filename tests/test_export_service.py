"""Tests for core/services/export_service.py."""
import csv
import os
import tempfile
from datetime import datetime
from decimal import Decimal
from typing import List
import uuid

import pytest

from core.model import RawRow
from core.ledger_store import LedgerStore
from core.services.export_service import (
    export_ledger_csv,
    export_timeseries_report_csv,
    export_table_report_csv,
    export_cashflow_csv,
    export_netto_invested_csv,
    export_positions_csv,
)
from core.services.report_service import get_report, get_positions_report, ReportKind


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_TS = datetime(2026, 1, 15, 10, 0, 0)
_TS2 = datetime(2026, 2, 20, 12, 0, 0)


def _make_trade_rows(trade_id: str = None, ts: datetime = _TS) -> List[RawRow]:
    """Minimal 2-row BUY trade (base + quote)."""
    tid = trade_id or str(uuid.uuid4())
    return [
        RawRow(id=tid, timestamp=ts, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="kraken"),
        RawRow(id=tid, timestamp=ts, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="kraken"),
    ]


def _store_with_rows(rows: List[RawRow]) -> str:
    """Write rows to a temp DB and return db_path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = LedgerStore(tmp.name)
    store.import_rows(rows)
    store.close()
    return tmp.name


def _tmp_csv() -> str:
    """Return a temp path for a CSV file (does not create the file)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp.close()
    os.unlink(tmp.name)
    return tmp.name


# ── export_ledger_csv ─────────────────────────────────────────────────────────


def test_export_ledger_csv_creates_file():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        result = export_ledger_csv(db_path, out)
        assert os.path.isfile(result)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_returns_abs_path():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        result = export_ledger_csv(db_path, out)
        assert os.path.isabs(result)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_has_header():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_ledger_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == ["id", "timestamp", "type", "asset", "amount", "currency", "price", "venue", "note"]
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_row_count():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_ledger_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            data_rows = list(reader)[1:]  # skip header
        assert len(data_rows) == len(rows)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_decimal_as_string():
    """Decimal values must be written as plain strings, never as float."""
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_ledger_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            content = f.read()
        # float representation would produce e.g. "40000.0" or "1.0"
        # Decimal normalised representation: "40000" or "1"
        assert "40000" in content
        # No scientific notation in numeric columns (amount, price).
        # We check only those two columns to avoid false positives from UUIDs
        # (which can contain patterns like "8e-4" inside a hex segment).
        import re
        import csv
        import io
        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            assert not re.search(r"\d[Ee][+\-]\d", row["amount"]), (
                f"Scientific notation in amount: {row['amount']}"
            )
            if row.get("price"):
                assert not re.search(r"\d[Ee][+\-]\d", row["price"]), (
                    f"Scientific notation in price: {row['price']}"
                )
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_utf8_sig_bom():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_ledger_csv(db_path, out)
        with open(out, "rb") as f:
            bom = f.read(3)
        assert bom == b"\xef\xbb\xbf"
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_empty_ledger():
    """Empty ledger exports header only (no rows)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = LedgerStore(tmp.name)
    store.close()
    out = _tmp_csv()
    try:
        export_ledger_csv(tmp.name, out)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            all_rows = list(reader)
        assert len(all_rows) == 1  # header only
    finally:
        os.unlink(tmp.name)
        if os.path.exists(out):
            os.unlink(out)


def test_export_ledger_csv_stable_ordering():
    """Same ledger → same file content on repeated export."""
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out1, out2 = _tmp_csv(), _tmp_csv()
    try:
        export_ledger_csv(db_path, out1)
        export_ledger_csv(db_path, out2)
        with open(out1, encoding="utf-8-sig") as f1, open(out2, encoding="utf-8-sig") as f2:
            assert f1.read() == f2.read()
    finally:
        os.unlink(db_path)
        for p in (out1, out2):
            if os.path.exists(p):
                os.unlink(p)


def test_export_ledger_csv_creates_parent_dir():
    """Export creates parent directory if it does not exist."""
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "subdir", "ledger.csv")
        try:
            export_ledger_csv(db_path, out)
            assert os.path.isfile(out)
        finally:
            os.unlink(db_path)


# ── export_timeseries_report_csv ──────────────────────────────────────────────


def test_export_timeseries_csv_creates_file():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        store = LedgerStore(db_path)
        ledger_rows = store.timeline()
        store.close()
        report = get_report(ledger_rows, kind=ReportKind.CASHFLOW, bucket="month")
        result = export_timeseries_report_csv(report, out)
        assert os.path.isfile(result)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_timeseries_csv_header_starts_with_date_currency():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        store = LedgerStore(db_path)
        ledger_rows = store.timeline()
        store.close()
        report = get_report(ledger_rows, kind=ReportKind.CASHFLOW, bucket="month")
        export_timeseries_report_csv(report, out)
        with open(out, encoding="utf-8-sig") as f:
            header = next(csv.reader(f))
        assert header[0] == "date"
        assert header[1] == "currency"
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_timeseries_csv_no_totals_row():
    """Totals must NOT appear as a data row in the CSV."""
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        store = LedgerStore(db_path)
        ledger_rows = store.timeline()
        store.close()
        report = get_report(ledger_rows, kind=ReportKind.CASHFLOW, bucket="month")
        export_timeseries_report_csv(report, out)
        with open(out, encoding="utf-8-sig") as f:
            content = f.read()
        # "total" should not appear as a date value in any row
        assert "total" not in content.lower().split("\n")[1:]  # ignore header
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_cashflow_csv_has_expected_columns():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_cashflow_csv(db_path, out, bucket="month")
        with open(out, encoding="utf-8-sig") as f:
            header = next(csv.reader(f))
        assert "date" in header
        assert "currency" in header
        # cashflow has net_amount metric
        assert "net_amount" in header
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_netto_invested_csv_has_expected_columns():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_netto_invested_csv(db_path, out, bucket="month")
        with open(out, encoding="utf-8-sig") as f:
            header = next(csv.reader(f))
        assert "date" in header
        assert "currency" in header
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_cashflow_csv_stable_ordering():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out1, out2 = _tmp_csv(), _tmp_csv()
    try:
        export_cashflow_csv(db_path, out1)
        export_cashflow_csv(db_path, out2)
        with open(out1, encoding="utf-8-sig") as f1, open(out2, encoding="utf-8-sig") as f2:
            assert f1.read() == f2.read()
    finally:
        os.unlink(db_path)
        for p in (out1, out2):
            if os.path.exists(p):
                os.unlink(p)


# ── export_table_report_csv (positions) ──────────────────────────────────────


def test_export_table_report_csv_creates_file():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        store = LedgerStore(db_path)
        ledger_rows = store.timeline()
        store.close()
        report = get_positions_report(ledger_rows)
        result = export_table_report_csv(report, out)
        assert os.path.isfile(result)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_table_report_csv_header_starts_with_key():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        store = LedgerStore(db_path)
        ledger_rows = store.timeline()
        store.close()
        report = get_positions_report(ledger_rows)
        export_table_report_csv(report, out)
        with open(out, encoding="utf-8-sig") as f:
            header = next(csv.reader(f))
        assert header[0] == "key"
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_positions_csv_has_expected_columns():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_positions_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            header = next(csv.reader(f))
        assert "key" in header
        assert "quantity" in header
        assert "wac" in header
        assert "cost_basis" in header
        assert "realized_pnl" in header
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_positions_csv_row_contains_btc():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_positions_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            content = f.read()
        assert "BTC" in content
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_positions_csv_fiat_not_in_rows():
    """EUR and CZK (fiat) must not appear as position rows."""
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_positions_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
            keys = [row[0] for row in reader]
        assert "EUR" not in keys
        assert "CZK" not in keys
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_positions_csv_no_scientific_notation():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_positions_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            content = f.read()
        import re
        assert not re.search(r"\d[Ee][+\-]\d", content)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_positions_csv_stable_ordering():
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out1, out2 = _tmp_csv(), _tmp_csv()
    try:
        export_positions_csv(db_path, out1)
        export_positions_csv(db_path, out2)
        with open(out1, encoding="utf-8-sig") as f1, open(out2, encoding="utf-8-sig") as f2:
            assert f1.read() == f2.read()
    finally:
        os.unlink(db_path)
        for p in (out1, out2):
            if os.path.exists(p):
                os.unlink(p)


def test_export_positions_csv_metric_columns_sorted():
    """Metric columns after 'key' must be alphabetically sorted."""
    rows = _make_trade_rows()
    db_path = _store_with_rows(rows)
    out = _tmp_csv()
    try:
        export_positions_csv(db_path, out)
        with open(out, encoding="utf-8-sig") as f:
            header = next(csv.reader(f))
        assert header[0] == "key"
        metric_cols = header[1:]
        assert metric_cols == sorted(metric_cols)
    finally:
        os.unlink(db_path)
        if os.path.exists(out):
            os.unlink(out)


def test_export_positions_csv_empty_ledger():
    """Empty ledger → CSV with only header, no data rows."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = LedgerStore(tmp.name)
    store.close()
    out = _tmp_csv()
    try:
        export_positions_csv(tmp.name, out)
        with open(out, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            all_rows = list(reader)
        assert len(all_rows) == 1  # header only
    finally:
        os.unlink(tmp.name)
        if os.path.exists(out):
            os.unlink(out)
