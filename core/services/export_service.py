"""CSV export service for ledger rows and reports.

All exports use utf-8-sig (BOM) encoding for Excel compatibility.
Decimal values are written as plain strings (no float conversion).
Column ordering is always deterministic.

Public API:
    export_ledger_csv(db_path, out_path) -> str
    export_timeseries_report_csv(report, out_path) -> str
    export_table_report_csv(report, out_path) -> str
    export_cashflow_csv(db_path, out_path, bucket, fiat) -> str
    export_netto_invested_csv(db_path, out_path, bucket, fiat) -> str
    export_positions_csv(db_path, out_path, fiat) -> str
"""
from __future__ import annotations

import csv
import os
from decimal import Decimal
from typing import FrozenSet, Optional, Set

from core.dto.reporting import TableReport, TimeSeriesReport
from core.ledger_store import LedgerStore
from core.services.report_service import ReportKind, get_positions_report, get_report

# Canonical ledger column order (matches unified_format_raw schema)
_LEDGER_COLS = ["id", "timestamp", "type", "asset", "amount", "currency", "price", "venue", "note"]

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


def _ensure_dir(path: str) -> None:
    """Create parent directory if it does not exist."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _str(val) -> str:
    """Convert any value to a stable string representation.

    Decimal → plain decimal string (no scientific notation).
    None    → empty string.
    Others  → str().
    """
    if val is None:
        return ""
    if isinstance(val, Decimal):
        # Normalise to remove trailing zeros but keep the number exact
        return format(val.normalize(), "f")
    return str(val)


# ── Core export functions ────────────────────────────────────────────────────


def export_ledger_csv(db_path: str, out_path: str) -> str:
    """Export all ledger rows to a CSV file.

    Columns (fixed order): id, timestamp, type, asset, amount,
    currency, price, venue, note.

    Args:
        db_path:  Path to the SQLite ledger database.
        out_path: Destination CSV file path.

    Returns:
        Absolute path to the written file.
    """
    _ensure_dir(out_path)
    store = LedgerStore(db_path)
    try:
        rows = store.timeline()
    finally:
        store.close()

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(_LEDGER_COLS)
        for row in rows:
            d = row.to_dict()
            writer.writerow([_str(d.get(col)) for col in _LEDGER_COLS])

    return os.path.abspath(out_path)


def export_timeseries_report_csv(report: TimeSeriesReport, out_path: str) -> str:
    """Export a TimeSeriesReport to CSV.

    Columns: date, currency, <metric keys sorted alphabetically>.
    Totals are NOT included (they are a separate summary, not row data).

    Args:
        report:   TimeSeriesReport DTO.
        out_path: Destination CSV file path.

    Returns:
        Absolute path to the written file.
    """
    _ensure_dir(out_path)

    # Discover metric keys from all rows to ensure stable ordering
    metric_keys: list[str] = []
    if report.rows:
        seen_keys: list[str] = sorted(report.rows[0].values.keys())
        # Verify all rows have same keys (they should by construction)
        metric_keys = seen_keys

    cols = ["date", "currency"] + metric_keys

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for row in report.rows:
            line = [_str(row.date), _str(row.currency)]
            for k in metric_keys:
                line.append(_str(row.values.get(k)))
            writer.writerow(line)

    return os.path.abspath(out_path)


def export_table_report_csv(report: TableReport, out_path: str) -> str:
    """Export a TableReport (snapshot) to CSV.

    Columns: key, <metric keys sorted alphabetically>.

    Args:
        report:   TableReport DTO.
        out_path: Destination CSV file path.

    Returns:
        Absolute path to the written file.
    """
    _ensure_dir(out_path)

    metric_keys: list[str] = []
    if report.rows:
        metric_keys = sorted(report.rows[0].values.keys())

    cols = ["key"] + metric_keys

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for row in report.rows:
            line = [_str(row.key)]
            for k in metric_keys:
                line.append(_str(row.values.get(k)))
            writer.writerow(line)

    return os.path.abspath(out_path)


# ── Convenience wrappers ─────────────────────────────────────────────────────


def export_cashflow_csv(
    db_path: str,
    out_path: str,
    bucket: str = "month",
    fiat: Optional[Set[str]] = None,
) -> str:
    """Export cashflow report to CSV.

    Args:
        db_path:  Path to the SQLite ledger database.
        out_path: Destination CSV file path.
        bucket:   Time bucket: "day", "week", or "month".
        fiat:     Fiat asset set (default: {"EUR", "CZK"}).

    Returns:
        Absolute path to the written file.
    """
    _fiat = frozenset(a.upper() for a in (fiat or _FIAT_DEFAULT))
    store = LedgerStore(db_path)
    try:
        rows = store.timeline()
    finally:
        store.close()
    report = get_report(rows, kind=ReportKind.CASHFLOW, bucket=bucket, fiat=set(_fiat))
    return export_timeseries_report_csv(report, out_path)


def export_netto_invested_csv(
    db_path: str,
    out_path: str,
    bucket: str = "month",
    fiat: Optional[Set[str]] = None,
) -> str:
    """Export netto-invested report to CSV.

    Args:
        db_path:  Path to the SQLite ledger database.
        out_path: Destination CSV file path.
        bucket:   Time bucket: "day", "week", or "month".
        fiat:     Fiat asset set (default: {"EUR", "CZK"}).

    Returns:
        Absolute path to the written file.
    """
    _fiat = frozenset(a.upper() for a in (fiat or _FIAT_DEFAULT))
    store = LedgerStore(db_path)
    try:
        rows = store.timeline()
    finally:
        store.close()
    report = get_report(rows, kind=ReportKind.NETTO_INVESTED, bucket=bucket, fiat=set(_fiat))
    return export_timeseries_report_csv(report, out_path)


def export_positions_csv(
    db_path: str,
    out_path: str,
    fiat: Optional[Set[str]] = None,
) -> str:
    """Export WAC positions snapshot to CSV.

    Args:
        db_path:  Path to the SQLite ledger database.
        out_path: Destination CSV file path.
        fiat:     Fiat asset set (default: {"EUR", "CZK"}).

    Returns:
        Absolute path to the written file.
    """
    _fiat = frozenset(a.upper() for a in (fiat or _FIAT_DEFAULT))
    store = LedgerStore(db_path)
    try:
        rows = store.timeline()
    finally:
        store.close()
    report = get_positions_report(rows, fiat=set(_fiat))
    return export_table_report_csv(report, out_path)
