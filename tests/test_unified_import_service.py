"""Tests for core/services/unified_import_service.py.

Coverage:
  - CSV import: 2 rows returned + persisted to DB
  - XLSX import: 2 rows returned + persisted to DB
  - sheet_name parameter: custom sheet loaded correctly
  - Deduplication: re-importing same file yields no extra rows in DB
  - Decimal precision: amounts stay Decimal, never float
  - Field normalisation: asset uppercase, venue lowercase, timestamp is datetime
  - Missing required column: ValueError raised for both CSV and XLSX
  - Missing sheet: ValueError raised
  - Unsupported file format: ValueError raised
  - Empty data (header only): returns []
"""
import csv
import os
import tempfile
from datetime import datetime
from decimal import Decimal

import pytest

from core.services.unified_import_service import import_unified_file

# ── Shared test data ──────────────────────────────────────────────────────────

_HEADERS = [
    "id", "timestamp", "type", "asset", "amount",
    "currency", "price", "venue", "note",
]

# A valid double-entry BUY pair (BTC / EUR)
_ROW_BASE = [
    "tx001", "2026-01-10T10:00:00", "BUY", "BTC",
    "0.5", "EUR", "50000", "kraken", "test buy",
]
_ROW_QUOTE = [
    "tx001", "2026-01-10T10:00:00", "BUY", "EUR",
    "-25000", "EUR", "1", "kraken", "test buy",
]
_TWO_ROWS = [_ROW_BASE, _ROW_QUOTE]


# ── Temp-file helpers ─────────────────────────────────────────────────────────

def _make_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _make_csv(rows=None, headers=None) -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers or _HEADERS)
        for row in (rows if rows is not None else _TWO_ROWS):
            writer.writerow(row)
    return path


def _make_xlsx(rows=None, headers=None, sheet_name=None) -> str:
    import openpyxl
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb = openpyxl.Workbook()
    ws = wb.active
    if sheet_name:
        ws.title = sheet_name
    ws.append(headers or _HEADERS)
    for row in (rows if rows is not None else _TWO_ROWS):
        ws.append(row)
    wb.save(path)
    return path


# ── CSV tests ─────────────────────────────────────────────────────────────────

def test_csv_import_returns_correct_row_count():
    csv_path = _make_csv()
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, csv_path)
        assert len(rows) == 2
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_import_persists_to_db():
    csv_path = _make_csv()
    db_path = _make_db()
    try:
        import_unified_file(db_path, csv_path)
        from core.ledger_store import LedgerStore
        store = LedgerStore(db_path)
        try:
            assert store.count() == 2
        finally:
            store.close()
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_deduplication():
    """Re-importing the same CSV must not create duplicate rows."""
    csv_path = _make_csv()
    db_path = _make_db()
    try:
        import_unified_file(db_path, csv_path)
        import_unified_file(db_path, csv_path)
        from core.ledger_store import LedgerStore
        store = LedgerStore(db_path)
        try:
            assert store.count() == 2   # still 2, not 4
        finally:
            store.close()
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_amount_is_decimal_not_float():
    """amount and price must be Decimal instances, never float."""
    csv_path = _make_csv()
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, csv_path)
        for row in rows:
            assert isinstance(row.amount, Decimal), \
                f"row.amount is {type(row.amount).__name__}, expected Decimal"
            if row.price is not None:
                assert isinstance(row.price, Decimal), \
                    f"row.price is {type(row.price).__name__}, expected Decimal"
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_decimal_precision_preserved():
    """Decimal('0.5') must not be coerced to float (0.5 is exact in float too,
    but this exercises the Decimal path explicitly)."""
    csv_path = _make_csv()
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, csv_path)
        base = next(r for r in rows if r.asset == "BTC")
        assert base.amount == Decimal("0.5")
        assert type(base.amount) is Decimal
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_field_normalisation():
    """asset → uppercase, venue → lowercase, timestamp → datetime."""
    csv_path = _make_csv(
        rows=[["", "2026-02-01T08:30:00", "BUY", "eth", "2.0", "eur", "", "Binance", ""]],
    )
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, csv_path)
        assert len(rows) == 1
        r = rows[0]
        assert r.asset == "ETH"
        assert r.currency == "EUR"
        assert r.venue == "binance"
        assert isinstance(r.timestamp, datetime)
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_optional_fields_nullable():
    """price and note may be absent/empty → None, not empty string."""
    csv_path = _make_csv(
        rows=[["", "2026-02-01T08:30:00", "TRANSFER", "BTC", "1.0", "BTC", "", "wallet_a", ""]],
    )
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, csv_path)
        r = rows[0]
        assert r.price is None
        assert r.note is None
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_missing_required_column_raises():
    """ValueError with column name when a required column is absent."""
    csv_path = _make_csv(
        headers=["timestamp", "type", "asset", "amount", "currency"],  # no venue
        rows=[["2026-01-10T10:00:00", "BUY", "BTC", "0.5", "EUR"]],
    )
    db_path = _make_db()
    try:
        with pytest.raises(ValueError, match="venue"):
            import_unified_file(db_path, csv_path)
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_multiple_missing_columns_all_listed():
    """All missing columns appear in the error, not just the first one."""
    csv_path = _make_csv(
        headers=["timestamp", "type"],   # missing asset, amount, currency, venue
        rows=[["2026-01-10T10:00:00", "BUY"]],
    )
    db_path = _make_db()
    try:
        with pytest.raises(ValueError) as exc_info:
            import_unified_file(db_path, csv_path)
        msg = str(exc_info.value)
        for col in ("amount", "asset", "currency", "venue"):
            assert col in msg, f"Expected {col!r} in error message"
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


def test_csv_empty_data_returns_empty_list():
    """Header-only CSV (no data rows) returns []."""
    csv_path = _make_csv(rows=[])
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, csv_path)
        assert rows == []
    finally:
        os.unlink(csv_path)
        os.unlink(db_path)


# ── XLSX tests ────────────────────────────────────────────────────────────────

def test_xlsx_import_returns_correct_row_count():
    xlsx_path = _make_xlsx()
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, xlsx_path)
        assert len(rows) == 2
    finally:
        os.unlink(xlsx_path)
        os.unlink(db_path)


def test_xlsx_import_persists_to_db():
    xlsx_path = _make_xlsx()
    db_path = _make_db()
    try:
        import_unified_file(db_path, xlsx_path)
        from core.ledger_store import LedgerStore
        store = LedgerStore(db_path)
        try:
            assert store.count() == 2
        finally:
            store.close()
    finally:
        os.unlink(xlsx_path)
        os.unlink(db_path)


def test_xlsx_sheet_name_parameter():
    """Custom sheet_name is used when provided."""
    xlsx_path = _make_xlsx(sheet_name="trades")
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, xlsx_path, sheet_name="trades")
        assert len(rows) == 2
    finally:
        os.unlink(xlsx_path)
        os.unlink(db_path)


def test_xlsx_missing_sheet_raises():
    xlsx_path = _make_xlsx()
    db_path = _make_db()
    try:
        with pytest.raises(ValueError, match="not found"):
            import_unified_file(db_path, xlsx_path, sheet_name="nonexistent")
    finally:
        os.unlink(xlsx_path)
        os.unlink(db_path)


def test_xlsx_deduplication():
    xlsx_path = _make_xlsx()
    db_path = _make_db()
    try:
        import_unified_file(db_path, xlsx_path)
        import_unified_file(db_path, xlsx_path)
        from core.ledger_store import LedgerStore
        store = LedgerStore(db_path)
        try:
            assert store.count() == 2
        finally:
            store.close()
    finally:
        os.unlink(xlsx_path)
        os.unlink(db_path)


def test_xlsx_amount_is_decimal_not_float():
    xlsx_path = _make_xlsx()
    db_path = _make_db()
    try:
        rows = import_unified_file(db_path, xlsx_path)
        for row in rows:
            assert isinstance(row.amount, Decimal)
    finally:
        os.unlink(xlsx_path)
        os.unlink(db_path)


def test_xlsx_missing_required_column_raises():
    import openpyxl
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    db_path = _make_db()
    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["timestamp", "type", "asset", "amount", "currency"])  # no venue
        ws.append(["2026-01-10T10:00:00", "BUY", "BTC", "0.5", "EUR"])
        wb.save(path)
        with pytest.raises(ValueError, match="venue"):
            import_unified_file(db_path, path)
    finally:
        os.unlink(path)
        os.unlink(db_path)


# ── Unsupported format ────────────────────────────────────────────────────────

def test_unsupported_format_raises():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    db_path = _make_db()
    try:
        with pytest.raises(ValueError, match="Unsupported"):
            import_unified_file(db_path, path)
    finally:
        os.unlink(path)
        os.unlink(db_path)
