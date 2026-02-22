"""Tests for ui_facade.add_trade() — unified_format_raw validation rules.

Covers the 7 required scenarios from KROK 4:
  1) BUY  with price=0  → FAIL
  2) SELL with price=0  → FAIL
  3) TRANSFER with price=0 → OK
  4) Unknown type → FAIL
  5) Normalization: asset/currency → uppercase, venue → lowercase
  6) amount=0 → FAIL
  7) REVERSAL (via facade.reverse_trade) is append-only; original rows survive
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.ui_facade import AddTradeRequestDTO, add_trade, reverse_trade


# ── Fixtures & helpers ─────────────────────────────────────────────────────────

_TS = datetime(2026, 2, 19, 12, 0, 0)


@pytest.fixture
def db(tmp_path):
    """Path to a fresh temp SQLite file."""
    return str(tmp_path / "test_facade.db")


def _req(**overrides) -> AddTradeRequestDTO:
    """Minimal valid BUY request; override any field via kwargs."""
    defaults: dict = dict(
        type="BUY",
        timestamp=_TS,
        asset="BTC",
        amount=Decimal("0.5"),
        currency="EUR",
        price=Decimal("50000"),
        venue="kraken",
    )
    defaults.update(overrides)
    return AddTradeRequestDTO(**defaults)


def _count(db_path: str) -> int:
    store = LedgerStore(db_path)
    try:
        return store.count()
    finally:
        store.close()


def _rows(db_path: str):
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


# ── 1) BUY with price=0 → FAIL ────────────────────────────────────────────────

def test_buy_price_zero_fails(db):
    result = add_trade(_req(price=Decimal("0"), quote_amount=None), db)
    assert not result.success
    assert result.n_rows_added == 0
    assert result.error_message


def test_buy_price_zero_nothing_written(db):
    add_trade(_req(price=Decimal("0"), quote_amount=None), db)
    assert _count(db) == 0


def test_buy_quote_amount_zero_fails(db):
    """Explicit quote_amount=0 is also rejected for BUY."""
    result = add_trade(_req(price=None, quote_amount=Decimal("0")), db)
    assert not result.success
    assert _count(db) == 0


# ── 2) SELL with price=0 → FAIL ───────────────────────────────────────────────

def test_sell_price_zero_fails(db):
    result = add_trade(_req(type="SELL", price=Decimal("0"), quote_amount=None), db)
    assert not result.success
    assert result.n_rows_added == 0
    assert result.error_message


def test_sell_price_zero_nothing_written(db):
    add_trade(_req(type="SELL", price=Decimal("0"), quote_amount=None), db)
    assert _count(db) == 0


# ── 3) TRANSFER with price=0 → OK ─────────────────────────────────────────────

def test_transfer_price_zero_ok(db):
    result = add_trade(
        _req(type="TRANSFER", price=Decimal("0"), quote_amount=None, currency="BTC"),
        db,
    )
    assert result.success, result.error_message
    assert result.n_rows_added > 0


def test_transfer_price_zero_writes_one_row(db):
    add_trade(
        _req(type="TRANSFER", price=Decimal("0"), quote_amount=None, currency="BTC"),
        db,
    )
    assert _count(db) == 1


def test_transfer_price_zero_row_type_correct(db):
    add_trade(
        _req(type="TRANSFER", price=Decimal("0"), quote_amount=None, currency="BTC"),
        db,
    )
    row = _rows(db)[0]
    assert row.type == "TRANSFER"


def test_fee_price_zero_ok(db):
    """FEE also allows price=0."""
    result = add_trade(
        _req(type="FEE", price=Decimal("0"), quote_amount=None,
             asset="BTC", currency="BTC", amount=Decimal("-0.001")),
        db,
    )
    assert result.success, result.error_message
    assert result.n_rows_added > 0


# ── 4) Unknown / disallowed type → FAIL ───────────────────────────────────────

def test_unknown_type_fails(db):
    result = add_trade(_req(type="BARTER"), db)
    assert not result.success
    assert result.n_rows_added == 0
    assert result.error_message


def test_unknown_type_nothing_written(db):
    add_trade(_req(type="BARTER"), db)
    assert _count(db) == 0


def test_reversal_via_add_trade_fails(db):
    """REVERSAL must go through reverse_trade(), not add_trade()."""
    result = add_trade(_req(type="REVERSAL"), db)
    assert not result.success
    assert result.n_rows_added == 0
    assert "reverse_trade" in (result.error_message or "").lower()


def test_reversal_via_add_trade_nothing_written(db):
    add_trade(_req(type="REVERSAL"), db)
    assert _count(db) == 0


# ── 5) Normalization ──────────────────────────────────────────────────────────

def test_normalize_asset_uppercase(db):
    """asset 'eth' must be written as 'ETH'."""
    result = add_trade(
        _req(asset="eth", amount=Decimal("1"), price=Decimal("2000"),
             currency="EUR", venue="kraken"),
        db,
    )
    assert result.success, result.error_message
    assets = {r.asset for r in _rows(db)}
    assert "ETH" in assets
    assert "eth" not in assets


def test_normalize_currency_uppercase(db):
    """currency 'eur' must be written as 'EUR'."""
    result = add_trade(
        _req(asset="BTC", amount=Decimal("1"), price=Decimal("50000"),
             currency="eur", venue="kraken"),
        db,
    )
    assert result.success, result.error_message
    currencies = {r.currency for r in _rows(db)}
    assert "EUR" in currencies
    assert "eur" not in currencies


def test_normalize_venue_lowercase(db):
    """venue 'KrAkEn' must be written as 'kraken'."""
    result = add_trade(
        _req(asset="BTC", amount=Decimal("1"), price=Decimal("50000"),
             currency="EUR", venue="KrAkEn"),
        db,
    )
    assert result.success, result.error_message
    venues = {r.venue for r in _rows(db)}
    assert "kraken" in venues
    assert "KrAkEn" not in venues


# ── 6) amount=0 → FAIL ────────────────────────────────────────────────────────

def test_amount_zero_fails(db):
    result = add_trade(_req(amount=Decimal("0")), db)
    assert not result.success
    assert result.n_rows_added == 0
    assert result.error_message


def test_amount_zero_nothing_written(db):
    add_trade(_req(amount=Decimal("0")), db)
    assert _count(db) == 0


# ── 7) reverse_trade is append-only ───────────────────────────────────────────

def test_reverse_trade_appends_rows(db):
    """Reversing a trade adds rows; original rows survive."""
    buy = add_trade(_req(), db)
    assert buy.success
    original_count = _count(db)

    trade_id = _rows(db)[0].id
    reversal_rows = reverse_trade(db, trade_id)

    assert len(reversal_rows) > 0
    assert _count(db) == original_count + len(reversal_rows)


def test_reverse_trade_original_rows_unchanged(db):
    """Original rows must not be modified — only new rows are appended."""
    add_trade(_req(), db)
    original_pairs = {(r.id, str(r.amount)) for r in _rows(db)}

    trade_id = _rows(db)[0].id
    reverse_trade(db, trade_id)

    after_pairs = {(r.id, str(r.amount)) for r in _rows(db)}
    assert original_pairs.issubset(after_pairs)


def test_reverse_trade_reversal_rows_are_type_reversal(db):
    """All rows added by reverse_trade() must have type REVERSAL."""
    add_trade(_req(), db)
    trade_id = _rows(db)[0].id

    reversal_rows = reverse_trade(db, trade_id)
    assert all(r.type == "REVERSAL" for r in reversal_rows)


def test_reverse_trade_negates_original_amounts(db):
    """Each reversal amount must exactly negate the original amount."""
    add_trade(_req(), db)
    original = _rows(db)
    original_by_asset = {r.asset: r.amount for r in original}

    trade_id = original[0].id
    reversal_rows = reverse_trade(db, trade_id)

    for rev in reversal_rows:
        orig_amt = original_by_asset.get(rev.asset)
        if orig_amt is not None:
            assert rev.amount == -orig_amt, (
                f"Reversal amount {rev.amount} should negate {orig_amt} for {rev.asset}"
            )


def test_reverse_trade_unknown_id_raises(db):
    """Reversing a non-existent trade_id raises ValueError."""
    with pytest.raises(ValueError):
        reverse_trade(db, "nonexistent-id-00000000")
