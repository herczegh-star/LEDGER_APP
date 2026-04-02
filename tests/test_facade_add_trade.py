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
        _req(type="TRANSFER", price=Decimal("0"), quote_amount=None,
             currency="BTC", to_venue="trezor"),
        db,
    )
    assert result.success, result.error_message
    assert result.n_rows_added > 0


def test_transfer_price_zero_writes_two_rows(db):
    """TRANSFER 2.0: price=0 TRANSFER creates 2 rows (outflow + inflow)."""
    add_trade(
        _req(type="TRANSFER", price=Decimal("0"), quote_amount=None,
             currency="BTC", to_venue="trezor"),
        db,
    )
    assert _count(db) == 2


def test_transfer_price_zero_row_type_correct(db):
    add_trade(
        _req(type="TRANSFER", price=Decimal("0"), quote_amount=None,
             currency="BTC", to_venue="trezor"),
        db,
    )
    types = {r.type for r in _rows(db)}
    assert types == {"TRANSFER"}


def test_fee_price_zero_ok(db):
    """FEE with price=0 is allowed when asset == currency (fiat fee)."""
    result = add_trade(
        _req(type="FEE", price=Decimal("0"), quote_amount=None,
             asset="BTC", currency="BTC", amount=Decimal("-0.001")),
        db,
    )
    assert result.success, result.error_message
    assert result.n_rows_added > 0


# ── KROK 4.1: FEE price=0 only when asset == currency ─────────────────────────

def test_fee_fiat_price_zero_passes(db):
    """FEE asset=CZK currency=CZK price=0 → allowed (fiat-denominated fee)."""
    result = add_trade(
        _req(type="FEE", asset="CZK", currency="CZK",
             amount=Decimal("-25"), price=Decimal("0"), quote_amount=None),
        db,
    )
    assert result.success, result.error_message
    assert result.n_rows_added > 0


def test_fee_crypto_price_zero_fails(db):
    """FEE asset=ETH currency=CZK price=0 → rejected (ambiguous valuation)."""
    result = add_trade(
        _req(type="FEE", asset="ETH", currency="CZK",
             amount=Decimal("-0.002"), price=Decimal("0"), quote_amount=None),
        db,
    )
    assert not result.success
    assert result.n_rows_added == 0
    assert result.error_message


def test_fee_crypto_price_nonzero_passes(db):
    """FEE asset=ETH currency=CZK price>0 → allowed (fiat value expressed)."""
    result = add_trade(
        _req(type="FEE", asset="ETH", currency="CZK",
             amount=Decimal("-0.002"), price=Decimal("65000"), quote_amount=None),
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
    """EUR input is normalized and converted to CZK — 'eur' never stored."""
    result = add_trade(
        _req(asset="BTC", amount=Decimal("1"), price=Decimal("50000"),
             currency="eur", venue="kraken"),
        db,
    )
    assert result.success, result.error_message
    currencies = {r.currency for r in _rows(db)}
    # EUR is converted to CZK at write time — neither "EUR" nor "eur" appear in DB.
    assert "CZK" in currencies
    assert "EUR" not in currencies
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


# ── TRANSFER + fee ─────────────────────────────────────────────────────────────

# ── TRANSFER 2.0 — two explicit rows ──────────────────────────────────────────

def _transfer_req(**overrides):
    """Minimal valid TRANSFER request with to_venue."""
    defaults = dict(
        type="TRANSFER",
        timestamp=_TS,
        asset="BTC",
        amount=Decimal("0.4"),
        currency="BTC",
        price=Decimal("0"),
        venue="anycoin",
        to_venue="trezor",
        fee_amount=None,
        fee_currency=None,
    )
    defaults.update(overrides)
    return AddTradeRequestDTO(**defaults)


def test_transfer_creates_two_rows(db):
    """TRANSFER without fee → exactly 2 rows (outflow + inflow)."""
    result = add_trade(_transfer_req(), db)
    assert result.success, result.error_message
    assert _count(db) == 2


def test_transfer_outflow_negative(db):
    """Source row must have negative amount (outflow)."""
    add_trade(_transfer_req(), db)
    source = next(r for r in _rows(db) if r.venue == "anycoin")
    assert source.amount == Decimal("-0.4")


def test_transfer_inflow_positive(db):
    """Destination row must have positive amount (inflow)."""
    add_trade(_transfer_req(), db)
    dest = next(r for r in _rows(db) if r.venue == "trezor")
    assert dest.amount == Decimal("0.4")


def test_transfer_rows_share_id(db):
    """Both TRANSFER rows must share the same trade_id."""
    add_trade(_transfer_req(), db)
    ids = {r.id for r in _rows(db)}
    assert len(ids) == 1


def test_transfer_both_rows_type_transfer(db):
    """Both rows must have type TRANSFER."""
    add_trade(_transfer_req(), db)
    types = [r.type for r in _rows(db)]
    assert all(t == "TRANSFER" for t in types)


def test_transfer_with_fee_creates_three_rows(db):
    """TRANSFER with fee → 3 rows (outflow + inflow + FEE)."""
    result = add_trade(_transfer_req(fee_amount=Decimal("0.0001"), fee_currency="BTC"), db)
    assert result.success, result.error_message
    assert _count(db) == 3


def test_transfer_with_fee_row_types(db):
    """Three rows: 2x TRANSFER, 1x FEE."""
    add_trade(_transfer_req(fee_amount=Decimal("0.0001"), fee_currency="BTC"), db)
    types = [r.type for r in _rows(db)]
    assert types.count("TRANSFER") == 2
    assert types.count("FEE") == 1


def test_transfer_fee_on_source_venue(db):
    """FEE row must be on the source venue (from_venue), not destination."""
    add_trade(_transfer_req(fee_amount=Decimal("0.0001"), fee_currency="BTC"), db)
    fee_row = next(r for r in _rows(db) if r.type == "FEE")
    assert fee_row.venue == "anycoin"


def test_transfer_fee_amount_negative(db):
    """FEE row amount must be negative (outflow)."""
    add_trade(_transfer_req(fee_amount=Decimal("0.0001"), fee_currency="BTC"), db)
    fee_row = next(r for r in _rows(db) if r.type == "FEE")
    assert fee_row.amount == Decimal("-0.0001")


def test_transfer_fee_currency_fallback(db):
    """fee_currency missing → FEE asset inherits transfer asset."""
    add_trade(_transfer_req(fee_amount=Decimal("0.0001"), fee_currency=None), db)
    fee_row = next(r for r in _rows(db) if r.type == "FEE")
    assert fee_row.asset == "BTC"


def test_transfer_amount_normalized(db):
    """Facade normalizes amount: outflow = -abs, inflow = +abs."""
    # User might enter positive amount — facade must still produce correct signs.
    add_trade(_transfer_req(amount=Decimal("0.4")), db)
    amounts = sorted(r.amount for r in _rows(db))
    assert amounts == [Decimal("-0.4"), Decimal("0.4")]


def test_transfer_to_venue_required(db):
    """TRANSFER without to_venue must fail."""
    result = add_trade(_transfer_req(to_venue=None), db)
    assert not result.success
    assert _count(db) == 0


def test_transfer_same_venue_fails(db):
    """from_venue == to_venue must be rejected."""
    result = add_trade(_transfer_req(venue="anycoin", to_venue="anycoin"), db)
    assert not result.success
    assert _count(db) == 0


def test_transfer_sum_invariant(db):
    """Sum of all TRANSFER amounts for an asset must equal zero."""
    add_trade(_transfer_req(), db)
    transfer_rows = [r for r in _rows(db) if r.type == "TRANSFER"]
    total = sum(r.amount for r in transfer_rows)
    assert total == Decimal("0")


def test_transfer_note_not_used_for_routing(db):
    """Note is irrelevant to routing — destination comes only from to_venue."""
    add_trade(_transfer_req(note="some random text"), db)
    dest = next(r for r in _rows(db) if r.venue == "trezor")
    assert dest is not None   # destination row exists from to_venue, not from note
    assert dest.note == "some random text"   # note passes through unchanged


def test_transfer_holdings_source_decreases(db):
    """After BUY + TRANSFER, source venue quantity decreases."""
    from core.reports.holdings import compute_venue_holdings
    from core.ledger_store import LedgerStore as _Store

    add_trade(_req(type="BUY", asset="BTC", amount=Decimal("1"),
                   currency="EUR", price=Decimal("20000"), venue="anycoin"), db)
    add_trade(_transfer_req(amount=Decimal("0.4")), db)

    store = _Store(db)
    try:
        rows = store.timeline()
    finally:
        store.close()

    h = compute_venue_holdings(rows)
    assert h["anycoin"]["BTC"] == Decimal("0.6")


def test_transfer_holdings_destination_increases(db):
    """After BUY + TRANSFER, destination venue receives the quantity."""
    from core.reports.holdings import compute_venue_holdings
    from core.ledger_store import LedgerStore as _Store

    add_trade(_req(type="BUY", asset="BTC", amount=Decimal("1"),
                   currency="EUR", price=Decimal("20000"), venue="anycoin"), db)
    add_trade(_transfer_req(amount=Decimal("0.4")), db)

    store = _Store(db)
    try:
        rows = store.timeline()
    finally:
        store.close()

    h = compute_venue_holdings(rows)
    assert h["trezor"]["BTC"] == Decimal("0.4")


def test_transfer_global_wac_unchanged(db):
    """Global WAC must not change after a TRANSFER."""
    from core.reports.positions import compute_positions
    from core.ledger_store import LedgerStore as _Store

    add_trade(_req(type="BUY", asset="BTC", amount=Decimal("1"),
                   currency="EUR", price=Decimal("20000"), venue="anycoin"), db)

    store = _Store(db)
    try:
        rows_before = store.timeline()
    finally:
        store.close()
    wac_before = {p.asset: p.wac for p in compute_positions(rows_before)}

    add_trade(_transfer_req(amount=Decimal("0.4")), db)

    store = _Store(db)
    try:
        rows_after = store.timeline()
    finally:
        store.close()
    wac_after = {p.asset: p.wac for p in compute_positions(rows_after)}

    assert wac_before["BTC"] == wac_after["BTC"]


def test_buy_sell_regression_unaffected(db):
    """BUY still produces 3 rows (base + quote + fee) — no regression."""
    add_trade(
        _req(type="BUY", asset="BTC", amount=Decimal("0.5"),
             currency="EUR", price=Decimal("50000"),
             fee_amount=Decimal("5"), fee_currency="EUR"),
        db,
    )
    assert _count(db) == 3
    types = [r.type for r in _rows(db)]
    assert types.count("BUY") == 2
    assert types.count("FEE") == 1
