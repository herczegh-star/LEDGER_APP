"""Historical price history — core/historical_price_store.py: Store
Initialization Safety (mirrors tests/test_account_role_store_lifecycle.py)
plus CRUD / immutability tests specific to this store.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.historical_price_store import (
    HistoricalPrice,
    HistoricalPriceSchemaNotInitializedError,
    HistoricalPriceStore,
)
from core.ledger_store import LedgerStore

_FETCHED_AT = datetime(2026, 1, 1, 12, 0, 0)
_D1 = date(2026, 1, 1)
_D2 = date(2026, 1, 2)


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def ledger_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = LedgerStore(db_path)
    store.close()
    return db_path


@pytest.fixture
def db(tmp_path):
    """A DB with historical_prices schema already initialized."""
    db_path = str(tmp_path / "test.db")
    store = HistoricalPriceStore(db_path)
    store.ensure_schema()
    store.close()
    return db_path


def _price(asset="BTC", d=_D1, price="2500000"):
    return HistoricalPrice(asset=asset, price_date=d, price_czk=Decimal(price), fetched_at=_FETCHED_AT)


# ── Store: Initialization Safety ────────────────────────────────────────────

def test_constructing_store_does_not_change_db_hash(ledger_db):
    before = _sha256(ledger_db)
    store = HistoricalPriceStore(ledger_db)
    store.close()
    assert _sha256(ledger_db) == before


def test_constructing_store_does_not_create_table(ledger_db):
    store = HistoricalPriceStore(ledger_db)
    exists = store.schema_exists()
    store.close()
    assert exists is False
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "historical_prices" not in tables


def test_read_on_uninitialized_schema_raises_without_creating_table(ledger_db):
    store = HistoricalPriceStore(ledger_db)
    with pytest.raises(HistoricalPriceSchemaNotInitializedError):
        store.get_price("BTC", _D1)
    still_missing = not store.schema_exists()
    store.close()
    assert still_missing


def test_set_price_on_missing_schema_fails_clearly(ledger_db):
    before = _sha256(ledger_db)
    store = HistoricalPriceStore(ledger_db)
    with pytest.raises(HistoricalPriceSchemaNotInitializedError, match="ensure_schema"):
        store.set_price(_price())
    store.close()
    assert _sha256(ledger_db) == before


def test_ensure_schema_creates_expected_table(ledger_db):
    store = HistoricalPriceStore(ledger_db)
    store.ensure_schema()
    store.close()
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "historical_prices" in tables


def test_ensure_schema_is_idempotent(ledger_db):
    store = HistoricalPriceStore(ledger_db)
    store.ensure_schema()
    store.set_price(_price())
    store.ensure_schema()
    store.ensure_schema()
    assert store.count() == 1
    store.close()


def test_read_only_connection_ensure_schema_raises(ledger_db):
    store = HistoricalPriceStore(ledger_db, read_only=True)
    with pytest.raises(RuntimeError, match="read_only"):
        store.ensure_schema()
    store.close()


def test_read_only_connection_cannot_mutate(ledger_db):
    setup = HistoricalPriceStore(ledger_db)
    setup.ensure_schema()
    setup.close()

    before = _sha256(ledger_db)
    ro_store = HistoricalPriceStore(ledger_db, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_store.set_price(_price())
    ro_store.close()
    assert _sha256(ledger_db) == before


# ── Store: CRUD / immutability ──────────────────────────────────────────────

def test_set_price_then_get_price(db):
    store = HistoricalPriceStore(db)
    store.set_price(_price())
    got = store.get_price("BTC", _D1)
    store.close()
    assert got.price_czk == Decimal("2500000")
    assert got.price_date == _D1


def test_set_price_returns_true_on_first_write(db):
    store = HistoricalPriceStore(db)
    written = store.set_price(_price())
    store.close()
    assert written is True


def test_set_price_is_first_write_wins_not_overwritten(db):
    """A (asset, date) price is immutable once written — a second
    set_price() with a DIFFERENT value for the same key must be a silent
    no-op, not an overwrite (see module docstring)."""
    store = HistoricalPriceStore(db)
    store.set_price(_price(price="2500000"))
    written_again = store.set_price(_price(price="9999999"))
    got = store.get_price("BTC", _D1)
    store.close()
    assert written_again is False
    assert got.price_czk == Decimal("2500000")  # unchanged, not 9999999
    assert HistoricalPriceStore(db).count() == 1


def test_get_price_missing_returns_none(db):
    store = HistoricalPriceStore(db)
    got = store.get_price("BTC", _D1)
    store.close()
    assert got is None


def test_get_existing_dates_returns_only_cached_dates(db):
    store = HistoricalPriceStore(db)
    store.set_price(_price(d=_D1))
    store.set_price(_price(d=_D2))
    dates = store.get_existing_dates("BTC")
    store.close()
    assert dates == {_D1, _D2}


def test_get_existing_dates_scoped_per_asset(db):
    store = HistoricalPriceStore(db)
    store.set_price(_price(asset="BTC", d=_D1))
    store.set_price(_price(asset="ETH", d=_D1))
    btc_dates = store.get_existing_dates("BTC")
    store.close()
    assert btc_dates == {_D1}


def test_asset_normalized_to_uppercase(db):
    store = HistoricalPriceStore(db)
    store.set_price(_price(asset="btc"))
    got = store.get_price("btc", _D1)
    store.close()
    assert got.asset == "BTC"
