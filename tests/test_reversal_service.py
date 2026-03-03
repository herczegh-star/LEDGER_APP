"""Tests for core/services/reversal_service.py."""
import tempfile
import os
from datetime import datetime
from decimal import Decimal
from typing import List
import uuid

import pytest

from core.model import RawRow
from core.ledger_store import LedgerStore
from core.services.reversal_service import build_reversal_rows, reverse_trade
from core.reports.positions import compute_positions
from core.reports.cashflow import cashflow


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_TS = datetime(2026, 1, 1, 12, 0, 0)


def _make_trade_rows(trade_id: str = None) -> List[RawRow]:
    """Return a minimal 2-row BUY trade (base + quote legs)."""
    tid = trade_id or str(uuid.uuid4())
    return [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test"),
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="EUR",
               amount=Decimal("-40000"), currency="EUR",
               price=Decimal("1"), venue="test"),
    ]


def _store_with_rows(rows: List[RawRow]) -> tuple:
    """Write rows to a temp DB and return (db_path, trade_id)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = LedgerStore(tmp.name)
    store.import_rows(rows)
    store.close()
    return tmp.name, rows[0].id


# ── 1. build_reversal_rows (pure function) ────────────────────────────────────

def test_build_reversal_rows_count():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    assert len(rev) == len(rows)


def test_build_reversal_rows_type_is_reversal():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    assert all(r.type == "REVERSAL" for r in rev)


def test_build_reversal_rows_amounts_are_exact_negatives():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    for orig, r in zip(rows, rev):
        assert r.amount == -orig.amount


def test_build_reversal_amounts_are_decimal():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    for r in rev:
        assert isinstance(r.amount, Decimal)


def test_build_reversal_rows_share_new_trade_id():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    ids = {r.id for r in rev}
    assert len(ids) == 1


def test_build_reversal_rows_new_id_differs_from_original():
    tid = str(uuid.uuid4())
    rows = _make_trade_rows(tid)
    rev = build_reversal_rows(rows)
    assert rev[0].id != tid


def test_build_reversal_rows_id_format():
    tid = str(uuid.uuid4())
    rows = _make_trade_rows(tid)
    rev = build_reversal_rows(rows)
    assert rev[0].id.startswith(f"REV_{tid}_")


def test_build_reversal_rows_id_suffix_is_8_chars():
    tid = str(uuid.uuid4())
    rows = _make_trade_rows(tid)
    rev = build_reversal_rows(rows)
    prefix = f"REV_{tid}_"
    suffix = rev[0].id[len(prefix):]
    assert len(suffix) == 8


def test_build_reversal_rows_note_contains_original_id():
    tid = str(uuid.uuid4())
    rows = _make_trade_rows(tid)
    rev = build_reversal_rows(rows)
    for r in rev:
        assert tid in r.note


def test_build_reversal_rows_note_has_reversal_prefix():
    tid = str(uuid.uuid4())
    rows = _make_trade_rows(tid)
    rev = build_reversal_rows(rows)
    for r in rev:
        assert r.note.startswith(f"REVERSAL of {tid}")


def test_build_reversal_rows_note_preserves_original_note():
    tid = str(uuid.uuid4())
    rows = [
        RawRow(id=tid, timestamp=_TS, type="BUY", asset="BTC",
               amount=Decimal("1"), currency="EUR",
               price=Decimal("40000"), venue="test",
               note="my original note"),
    ]
    rev = build_reversal_rows(rows)
    assert "my original note" in rev[0].note


def test_build_reversal_rows_note_no_duplication_when_no_original_note():
    rows = _make_trade_rows()  # note=None
    rev = build_reversal_rows(rows)
    # Should not have double semicolons or trailing separators
    for r in rev:
        assert not r.note.endswith("; ")
        assert "; ;" not in r.note


def test_build_reversal_rows_asset_preserved():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    for orig, r in zip(rows, rev):
        assert r.asset == orig.asset


def test_build_reversal_rows_venue_preserved():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    for orig, r in zip(rows, rev):
        assert r.venue == orig.venue


def test_build_reversal_rows_currency_preserved():
    rows = _make_trade_rows()
    rev = build_reversal_rows(rows)
    for orig, r in zip(rows, rev):
        assert r.currency == orig.currency


def test_build_reversal_rows_custom_new_trade_id():
    rows = _make_trade_rows()
    custom_id = "CUSTOM-ID-123"
    rev = build_reversal_rows(rows, new_trade_id=custom_id)
    assert all(r.id == custom_id for r in rev)


def test_build_reversal_rows_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        build_reversal_rows([])


# ── 2. reverse_trade (integration) ───────────────────────────────────────────

def test_reverse_trade_returns_list():
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        result = reverse_trade(db_path, tid)
        assert isinstance(result, list)
    finally:
        os.unlink(db_path)


def test_reverse_trade_returns_correct_count():
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        result = reverse_trade(db_path, tid)
        assert len(result) == len(trade_rows)
    finally:
        os.unlink(db_path)


def test_reverse_trade_rows_appended_to_ledger():
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        reverse_trade(db_path, tid)
        store = LedgerStore(db_path)
        all_rows = store.timeline()
        store.close()
        assert len(all_rows) == len(trade_rows) * 2
    finally:
        os.unlink(db_path)


def test_reverse_trade_missing_id_raises():
    trade_rows = _make_trade_rows()
    db_path, _ = _store_with_rows(trade_rows)
    try:
        with pytest.raises(ValueError, match="No rows found"):
            reverse_trade(db_path, "nonexistent-trade-id")
    finally:
        os.unlink(db_path)


def test_reverse_trade_cancels_position():
    """After reversing a BUY, the WAC positions should be empty."""
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        reverse_trade(db_path, tid)
        store = LedgerStore(db_path)
        all_rows = store.timeline()
        store.close()
        positions = compute_positions(all_rows)
        # BTC position should be zero after reversal
        btc_positions = [p for p in positions if p.asset == "BTC"]
        if btc_positions:
            assert btc_positions[0].quantity == Decimal("0")
    finally:
        os.unlink(db_path)


def test_reverse_trade_cancels_cashflow():
    """After reversing a BUY, the net cashflow should be zero."""
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        reverse_trade(db_path, tid)
        store = LedgerStore(db_path)
        all_rows = store.timeline()
        store.close()
        cf_rows = cashflow(all_rows)
        # EUR net should sum to 0 (BUY -40000 + REVERSAL +40000)
        eur_total = sum(r.net_amount for r in cf_rows if r.currency == "EUR")
        assert eur_total == Decimal("0")
    finally:
        os.unlink(db_path)


def test_reverse_trade_second_call_raises():
    """Calling reverse_trade twice on the same trade_id must raise ValueError.
    Each trade can only be reversed once.
    """
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        reverse_trade(db_path, tid)
        with pytest.raises(ValueError, match="already been reversed"):
            reverse_trade(db_path, tid)
    finally:
        os.unlink(db_path)


def test_reverse_trade_reversal_rows_have_reversal_type():
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        rev = reverse_trade(db_path, tid)
        assert all(r.type == "REVERSAL" for r in rev)
    finally:
        os.unlink(db_path)


def test_reverse_trade_reversal_amounts_are_negated():
    trade_rows = _make_trade_rows()
    db_path, tid = _store_with_rows(trade_rows)
    try:
        rev = reverse_trade(db_path, tid)
        # Map original amounts by asset for comparison
        orig_by_asset = {r.asset: r.amount for r in trade_rows}
        for r in rev:
            assert r.amount == -orig_by_asset[r.asset]
    finally:
        os.unlink(db_path)
