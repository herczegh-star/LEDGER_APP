"""Tests for core/services/trade_service.py.

Tests for build_trade_rows() are pure (no DB).
One integration test uses a temp SQLite file.
"""
import os
import tempfile
from datetime import datetime
from decimal import Decimal

import pytest

from core.services.trade_service import AddTradeInput, TradeResult, build_trade_rows, add_trade


# ── Shared fixture data ────────────────────────────────────────────────────────

_TS = datetime(2026, 2, 19, 12, 0, 0)

_BUY_INPUT = AddTradeInput(
    type="BUY",
    timestamp=_TS,
    base_asset="BTC",
    base_amount=Decimal("0.5"),
    quote_currency="EUR",
    quote_amount=Decimal("25000"),
    venue="kraken",
)

_SELL_INPUT = AddTradeInput(
    type="SELL",
    timestamp=_TS,
    base_asset="BTC",
    base_amount=Decimal("0.5"),
    quote_currency="EUR",
    quote_amount=Decimal("30000"),
    venue="kraken",
)

_FEE_INPUT = AddTradeInput(
    type="BUY",
    timestamp=_TS,
    base_asset="ETH",
    base_amount=Decimal("2"),
    quote_currency="EUR",
    quote_amount=Decimal("6000"),
    venue="coinbase",
    fee_amount=Decimal("5"),
    fee_currency="EUR",
)


# ── BUY signs ─────────────────────────────────────────────────────────────────

def test_buy_base_positive():
    rows = build_trade_rows(_BUY_INPUT)
    base = next(r for r in rows if r.asset == "BTC")
    assert base.amount > 0


def test_buy_quote_negative():
    rows = build_trade_rows(_BUY_INPUT)
    quote = next(r for r in rows if r.asset == "EUR" and r.type == "BUY")
    assert quote.amount < 0


def test_buy_base_amount_correct():
    rows = build_trade_rows(_BUY_INPUT)
    base = next(r for r in rows if r.asset == "BTC")
    assert base.amount == Decimal("0.5")


def test_buy_quote_amount_correct():
    rows = build_trade_rows(_BUY_INPUT)
    quote = next(r for r in rows if r.asset == "EUR" and r.type == "BUY")
    assert quote.amount == Decimal("-25000")


# ── SELL signs ────────────────────────────────────────────────────────────────

def test_sell_base_negative():
    rows = build_trade_rows(_SELL_INPUT)
    base = next(r for r in rows if r.asset == "BTC")
    assert base.amount < 0


def test_sell_quote_positive():
    rows = build_trade_rows(_SELL_INPUT)
    quote = next(r for r in rows if r.asset == "EUR" and r.type == "SELL")
    assert quote.amount > 0


def test_sell_base_amount_correct():
    rows = build_trade_rows(_SELL_INPUT)
    base = next(r for r in rows if r.asset == "BTC")
    assert base.amount == Decimal("-0.5")


def test_sell_quote_amount_correct():
    rows = build_trade_rows(_SELL_INPUT)
    quote = next(r for r in rows if r.asset == "EUR" and r.type == "SELL")
    assert quote.amount == Decimal("30000")


# ── Shared trade_id ───────────────────────────────────────────────────────────

def test_shared_trade_id_two_rows():
    rows = build_trade_rows(_BUY_INPUT)
    assert len(rows) == 2
    ids = {r.id for r in rows}
    assert len(ids) == 1, f"Expected all rows to share one id, got: {ids}"


# ── Fee row ───────────────────────────────────────────────────────────────────

def test_fee_row_count():
    rows = build_trade_rows(_FEE_INPUT)
    assert len(rows) == 3


def test_fee_amount_negative():
    rows = build_trade_rows(_FEE_INPUT)
    fee = next(r for r in rows if r.type == "FEE")
    assert fee.amount < 0
    assert fee.amount == Decimal("-5")


def test_fee_shared_id():
    rows = build_trade_rows(_FEE_INPUT)
    ids = {r.id for r in rows}
    assert len(ids) == 1


def test_fee_asset_is_fee_currency():
    rows = build_trade_rows(_FEE_INPUT)
    fee = next(r for r in rows if r.type == "FEE")
    assert fee.asset == "EUR"


def test_fee_defaults_to_quote_currency_when_not_specified():
    inp = AddTradeInput(
        type="BUY",
        timestamp=_TS,
        base_asset="BTC",
        base_amount=Decimal("1"),
        quote_currency="EUR",
        quote_amount=Decimal("50000"),
        venue="test",
        fee_amount=Decimal("10"),
        # fee_currency not set → should default to "EUR"
    )
    rows = build_trade_rows(inp)
    fee = next(r for r in rows if r.type == "FEE")
    assert fee.asset == "EUR"


# ── Row metadata ──────────────────────────────────────────────────────────────

def test_price_is_quote_over_base():
    rows = build_trade_rows(_BUY_INPUT)
    base = next(r for r in rows if r.asset == "BTC")
    expected = Decimal("25000") / Decimal("0.5")
    assert base.price == expected


def test_venue_lowercase():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="ETH", base_amount=Decimal("1"),
        quote_currency="EUR", quote_amount=Decimal("2000"),
        venue="Kraken",
    )
    rows = build_trade_rows(inp)
    assert all(r.venue == "kraken" for r in rows)


def test_asset_uppercase():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="btc", base_amount=Decimal("1"),
        quote_currency="eur", quote_amount=Decimal("50000"),
        venue="test",
    )
    rows = build_trade_rows(inp)
    assets = {r.asset for r in rows}
    assert "BTC" in assets
    assert "EUR" in assets


# ── Validation ────────────────────────────────────────────────────────────────

def test_validation_zero_base_amount():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="BTC", base_amount=Decimal("0"),
        quote_currency="EUR", quote_amount=Decimal("1000"),
        venue="test",
    )
    with pytest.raises(ValueError, match="base_amount"):
        build_trade_rows(inp)


def test_validation_zero_quote_amount():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="BTC", base_amount=Decimal("1"),
        quote_currency="EUR", quote_amount=Decimal("0"),
        venue="test",
    )
    with pytest.raises(ValueError, match="quote_amount"):
        build_trade_rows(inp)


def test_validation_negative_fee_amount():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="BTC", base_amount=Decimal("1"),
        quote_currency="EUR", quote_amount=Decimal("1000"),
        venue="test",
        fee_amount=Decimal("-5"),
    )
    with pytest.raises(ValueError, match="fee_amount"):
        build_trade_rows(inp)


def test_validation_fiat_as_base_asset():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="EUR",   # invalid: EUR is fiat
        base_amount=Decimal("100"),
        quote_currency="CZK",
        quote_amount=Decimal("2500"),
        venue="test",
    )
    with pytest.raises(ValueError, match="base_asset"):
        build_trade_rows(inp)


def test_validation_unsupported_quote_currency():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="BTC",
        base_amount=Decimal("1"),
        quote_currency="BTC",   # invalid: not fiat
        quote_amount=Decimal("0.1"),
        venue="test",
    )
    with pytest.raises(ValueError, match="quote_currency"):
        build_trade_rows(inp)


def test_validation_invalid_type():
    inp = AddTradeInput(
        type="TRANSFER",   # not BUY or SELL
        timestamp=_TS,
        base_asset="BTC",
        base_amount=Decimal("1"),
        quote_currency="EUR",
        quote_amount=Decimal("50000"),
        venue="test",
    )
    with pytest.raises(ValueError, match="BUY or SELL"):
        build_trade_rows(inp)


def test_validation_empty_venue():
    inp = AddTradeInput(
        type="BUY", timestamp=_TS,
        base_asset="BTC", base_amount=Decimal("1"),
        quote_currency="EUR", quote_amount=Decimal("50000"),
        venue="",
    )
    with pytest.raises(ValueError, match="venue"):
        build_trade_rows(inp)


# ── Integration: add_trade writes to real (temp) DB ───────────────────────────

def test_add_trade_integration_inserts_rows():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        from core.ledger_store import LedgerStore
        inp = AddTradeInput(
            type="BUY",
            timestamp=datetime(2026, 2, 19, 10, 0, 0),
            base_asset="BTC",
            base_amount=Decimal("0.25"),
            quote_currency="EUR",
            quote_amount=Decimal("12500"),
            venue="test_exchange",
            note="integration test",
        )
        result = add_trade(path, inp)
        assert isinstance(result, TradeResult)
        assert len(result.rows) == 2
        assert result.inserted == 2
        assert result.skipped == 0

        store = LedgerStore(path)
        try:
            assert store.count() == 2
        finally:
            store.close()
    finally:
        os.unlink(path)


def test_add_trade_integration_with_fee():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        from core.ledger_store import LedgerStore
        inp = AddTradeInput(
            type="SELL",
            timestamp=datetime(2026, 2, 19, 11, 0, 0),
            base_asset="ETH",
            base_amount=Decimal("5"),
            quote_currency="EUR",
            quote_amount=Decimal("15000"),
            venue="coinbase",
            fee_amount=Decimal("10"),
            fee_currency="EUR",
        )
        result = add_trade(path, inp)
        assert isinstance(result, TradeResult)
        assert len(result.rows) == 3
        assert result.inserted == 3
        assert result.skipped == 0

        store = LedgerStore(path)
        try:
            assert store.count() == 3
            # All rows share one trade_id
            trade_id = result.rows[0].id
            pair = store.get_rows_by_id(trade_id)
            assert len(pair) == 3
        finally:
            store.close()
    finally:
        os.unlink(path)


def test_add_trade_idempotent_deduplication():
    """Inserting the same trade twice must not duplicate rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        from core.ledger_store import LedgerStore
        # Build rows manually so we can reuse the same RawRow objects
        inp = AddTradeInput(
            type="BUY",
            timestamp=datetime(2026, 2, 19, 12, 0, 0),
            base_asset="BTC",
            base_amount=Decimal("1"),
            quote_currency="EUR",
            quote_amount=Decimal("50000"),
            venue="test",
        )
        rows = build_trade_rows(inp)
        store = LedgerStore(path)
        try:
            store.import_rows(rows)
            count_after_first = store.count()
            store.import_rows(rows)   # second insert – must be no-op
            count_after_second = store.count()
            assert count_after_first == count_after_second == 2
        finally:
            store.close()
    finally:
        os.unlink(path)
