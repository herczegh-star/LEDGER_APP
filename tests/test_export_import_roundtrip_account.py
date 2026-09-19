"""Phase 1 — export/import round-trip must preserve `account`, and legacy
files without an `account` column must keep working exactly as before.

Two scenarios required by the Phase 1 spec:
  1. ledger row with account -> export -> import -> account preserved.
  2. legacy export/file WITHOUT account column -> import -> account=None,
     no regression (account stays optional, never required).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.export_service import export_ledger_csv
from core.services.unified_import_service import import_unified_file

_TS = datetime(2026, 9, 10, 9, 31, 0)


def _tmp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def _tmp_csv_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    return path


def test_account_survives_export_then_reimport():
    src_db = _tmp_db_path()
    dst_db = _tmp_db_path()
    csv_path = _tmp_csv_path()
    try:
        store = LedgerStore(src_db)
        store.insert(RawRow(
            id="t1", timestamp=_TS, type="SELL", asset="CZK",
            amount=Decimal("3344.25"), currency="CZK", price=Decimal("1"),
            venue="revolut", account="Osobní CZK",
        ))
        store.close()

        export_ledger_csv(src_db, csv_path)

        # Header must include account (round-trip precondition)
        with open(csv_path, encoding="utf-8-sig") as f:
            header = f.readline().strip().split(",")
        assert "account" in header

        imported_rows = import_unified_file(dst_db, csv_path)
        assert len(imported_rows) == 1
        assert imported_rows[0].account == "Osobní CZK"

        # Also verify what actually landed in the destination DB
        store2 = LedgerStore(dst_db)
        rows = store2.timeline()
        store2.close()
        assert len(rows) == 1
        assert rows[0].account == "Osobní CZK"
    finally:
        for p in (src_db, dst_db, csv_path):
            if os.path.exists(p):
                os.unlink(p)


def test_legacy_csv_without_account_column_still_works():
    """A unified_format_raw CSV predating the cash-accounts feature (no
    `account` column at all) must import exactly as before: account=None,
    no error, no required-column failure."""
    csv_path = _tmp_csv_path()
    dst_db = _tmp_db_path()
    try:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("id,timestamp,type,asset,amount,currency,price,venue,note\n")
            f.write("t1,2026-09-10T09:31:00,SELL,CZK,3344.25,CZK,1,revolut,\n")

        imported_rows = import_unified_file(dst_db, csv_path)
        assert len(imported_rows) == 1
        assert imported_rows[0].account is None

        store = LedgerStore(dst_db)
        rows = store.timeline()
        store.close()
        assert rows[0].account is None
    finally:
        for p in (csv_path, dst_db):
            if os.path.exists(p):
                os.unlink(p)


def test_legacy_csv_with_blank_account_cell_normalizes_to_none():
    csv_path = _tmp_csv_path()
    dst_db = _tmp_db_path()
    try:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("id,timestamp,type,asset,amount,currency,price,venue,note,account\n")
            f.write("t1,2026-09-10T09:31:00,SELL,CZK,3344.25,CZK,1,revolut,,\n")

        imported_rows = import_unified_file(dst_db, csv_path)
        assert imported_rows[0].account is None
    finally:
        for p in (csv_path, dst_db):
            if os.path.exists(p):
                os.unlink(p)
