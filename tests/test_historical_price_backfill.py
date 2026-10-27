"""Tests for core/services/historical_price_service.py — backfill_historical_prices().

Uses a tiny in-memory fake provider (no real HTTP) implementing the same
duck-typed get_historical_prices_range(asset, fiat, date_from, date_to)
contract as CoinGeckoPriceProvider — see core/prices/coingecko.py.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from core.historical_price_store import HistoricalPriceStore
from core.ledger_store import LedgerStore
from core.services.historical_price_service import CHUNK_DAYS, _date_chunks, backfill_historical_prices
from core.services.trade_service import AddTradeInput, add_trade

_D0 = date(2026, 1, 1)
_D1 = date(2026, 1, 2)
_D2 = date(2026, 1, 3)


class _FakeProvider:
    """call_log records every get_historical_prices_range() invocation so
    tests can assert exactly which (asset, window) pairs triggered a fetch."""

    def __init__(self, data: dict, fail_for: frozenset = frozenset()):
        # data: {asset: {date: Decimal}}
        self._data = data
        self._fail_for = fail_for
        self.call_log: list = []

    def get_historical_prices_range(self, asset, fiat, date_from, date_to):
        self.call_log.append((asset, fiat, date_from, date_to))
        if asset in self._fail_for:
            return {}
        all_prices = self._data.get(asset, {})
        return {d: p for d, p in all_prices.items() if date_from <= d <= date_to}


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = HistoricalPriceStore(db_path)
    store.ensure_schema()
    store.close()
    return db_path


def _seed_trade(db_path, asset, ts, qty="1", quote="1000"):
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=ts, base_asset=asset, base_amount=Decimal(qty),
        quote_currency="CZK", quote_amount=Decimal(quote), venue="kraken",
    ))


# ── Asset / date-range discovery from the ledger ────────────────────────────

def test_empty_ledger_and_no_explicit_assets_does_nothing(db):
    provider = _FakeProvider({})
    result = backfill_historical_prices(db, provider)
    assert result.assets_considered == []
    assert provider.call_log == []


def test_derives_assets_from_ledger_when_not_given(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    _seed_trade(db, "ETH", datetime(2026, 1, 2))
    provider = _FakeProvider({"BTC": {}, "ETH": {}})
    backfill_historical_prices(db, provider, as_of=_D2)
    called_assets = {c[0] for c in provider.call_log}
    assert called_assets == {"BTC", "ETH"}


def test_fiat_rows_never_treated_as_an_asset_to_backfill(db):
    """add_trade() writes both a BTC leg and a CZK (fiat) leg sharing one
    trade id — only BTC must ever reach the provider."""
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    provider = _FakeProvider({"BTC": {}})
    backfill_historical_prices(db, provider, as_of=_D2)
    called_assets = {c[0] for c in provider.call_log}
    assert called_assets == {"BTC"}


# ── Idempotence: already-cached dates are never re-fetched ──────────────────

def test_already_complete_asset_triggers_no_http_call(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    data = {"BTC": {_D0: Decimal("2500000"), _D1: Decimal("2510000"), _D2: Decimal("2520000")}}
    provider = _FakeProvider(data)
    backfill_historical_prices(db, provider, since=_D0, as_of=_D2)  # first run fills everything
    assert provider.call_log == [("BTC", "CZK", _D0, _D2)]

    provider.call_log.clear()
    result = backfill_historical_prices(db, provider, since=_D0, as_of=_D2)  # second run
    assert provider.call_log == []  # no HTTP call at all
    assert result.assets_already_complete == ["BTC"]
    assert result.dates_stored == 0


def test_partial_cache_still_skips_already_present_asset_once_fully_filled(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    store = HistoricalPriceStore(db)
    from core.historical_price_store import HistoricalPrice
    store.set_price(HistoricalPrice(asset="BTC", price_date=_D0, price_czk=Decimal("2500000"), fetched_at=datetime.now()))
    store.close()

    data = {"BTC": {_D1: Decimal("2510000"), _D2: Decimal("2520000")}}
    provider = _FakeProvider(data)
    result = backfill_historical_prices(db, provider, since=_D0, as_of=_D2)
    assert provider.call_log == [("BTC", "CZK", _D0, _D2)]  # still fetched (D1/D2 missing)
    assert result.dates_stored == 2

    provider.call_log.clear()
    result2 = backfill_historical_prices(db, provider, since=_D0, as_of=_D2)
    assert provider.call_log == []  # now fully cached, no further calls
    assert result2.assets_already_complete == ["BTC"]


def test_existing_price_is_never_overwritten_by_a_second_backfill(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    provider1 = _FakeProvider({"BTC": {_D0: Decimal("2500000")}})
    backfill_historical_prices(db, provider1, since=_D0, as_of=_D0)

    provider2 = _FakeProvider({"BTC": {_D0: Decimal("9999999")}})
    backfill_historical_prices(db, provider2, since=_D0, as_of=_D0)

    store = HistoricalPriceStore(db)
    got = store.get_price("BTC", _D0)
    store.close()
    assert got.price_czk == Decimal("2500000")


# ── Failure tolerance ────────────────────────────────────────────────────────

def test_provider_failure_for_one_asset_does_not_block_others(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    _seed_trade(db, "ETH", datetime(2026, 1, 1))
    data = {"ETH": {_D0: Decimal("80000")}}
    provider = _FakeProvider(data, fail_for=frozenset({"BTC"}))
    result = backfill_historical_prices(db, provider, since=_D0, as_of=_D0)
    assert "BTC" in result.assets_failed
    assert "ETH" in result.assets_fetched
    store = HistoricalPriceStore(db)
    assert store.get_price("ETH", _D0) is not None
    assert store.get_price("BTC", _D0) is None
    store.close()


def test_provider_failure_does_not_raise(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    provider = _FakeProvider({}, fail_for=frozenset({"BTC"}))
    result = backfill_historical_prices(db, provider, since=_D0, as_of=_D0)  # must not raise
    assert result.assets_failed == ["BTC"]


def test_unknown_coingecko_asset_recorded_as_failed_not_crash(db):
    """A real CoinGeckoPriceProvider.get_historical_prices_range() returns
    {} for an asset it has no mapping for — same contract as the fake here."""
    _seed_trade(db, "NOSUCHCOIN", datetime(2026, 1, 1))
    provider = _FakeProvider({})  # empty for everything
    result = backfill_historical_prices(db, provider, since=_D0, as_of=_D0)
    assert result.assets_failed == ["NOSUCHCOIN"]


def test_schema_not_initialized_raises_clearly(tmp_path):
    """backfill assumes create_db() already ran — same contract as every
    other side-table service in this project (asset_note_service etc.)."""
    from core.historical_price_store import HistoricalPriceSchemaNotInitializedError

    db_path = str(tmp_path / "no_schema.db")
    LedgerStore(db_path).close()
    _seed_trade(db_path, "BTC", datetime(2026, 1, 1))
    provider = _FakeProvider({"BTC": {_D0: Decimal("2500000")}})
    with pytest.raises(HistoricalPriceSchemaNotInitializedError):
        backfill_historical_prices(db_path, provider, since=_D0, as_of=_D0)


# ── Date-range derivation ───────────────────────────────────────────────────

def test_since_defaults_to_earliest_ledger_row_date(db):
    _seed_trade(db, "BTC", datetime(2026, 1, 5))
    provider = _FakeProvider({"BTC": {}})
    backfill_historical_prices(db, provider, as_of=date(2026, 1, 7))
    # 2026-01-05 .. 2026-01-07 inclusive == 3 days, fits in one chunk
    assert provider.call_log == [("BTC", "CZK", date(2026, 1, 5), date(2026, 1, 7))]


def test_as_of_defaults_to_today(db, monkeypatch):
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    provider = _FakeProvider({"BTC": {}})
    result = backfill_historical_prices(db, provider, since=date(2026, 1, 1))
    assert result.assets_considered == ["BTC"]  # ran without error, used date.today()


# ── _date_chunks() ───────────────────────────────────────────────────────────

def test_date_chunks_single_chunk_when_range_fits():
    chunks = list(_date_chunks(date(2026, 1, 1), date(2026, 1, 10)))
    assert chunks == [(date(2026, 1, 1), date(2026, 1, 10))]


def test_date_chunks_splits_long_range_at_chunk_days():
    since, as_of = date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=400)
    chunks = list(_date_chunks(since, as_of, chunk_days=180))
    assert len(chunks) == 3  # 180 + 180 + 41
    assert chunks[0] == (since, since + timedelta(days=179))
    assert chunks[1] == (since + timedelta(days=180), since + timedelta(days=359))
    assert chunks[2] == (since + timedelta(days=360), as_of)


def test_date_chunks_are_contiguous_no_gap_no_overlap():
    chunks = list(_date_chunks(date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=400), chunk_days=180))
    for (f1, t1), (f2, t2) in zip(chunks, chunks[1:]):
        assert f2 == t1 + timedelta(days=1)


# ── Chunked backfill over a long range ──────────────────────────────────────

def test_long_range_backfill_issues_multiple_chunked_calls(db):
    """A >CHUNK_DAYS range must be split into several requests, never one
    request covering the whole span and never one request per day."""
    since = date(2026, 1, 1)
    as_of = since + timedelta(days=400)
    _seed_trade(db, "BTC", datetime(2026, 1, 1))

    all_dates = {since + timedelta(days=i): Decimal("1000") for i in range(401)}
    provider = _FakeProvider({"BTC": all_dates})

    result = backfill_historical_prices(db, provider, since=since, as_of=as_of)

    assert len(provider.call_log) == 3  # chunked, not 1 giant call, not 401 daily calls
    for call in provider.call_log:
        span_days = (call[3] - call[2]).days + 1
        assert span_days <= CHUNK_DAYS
    assert result.dates_stored == 401
    assert result.assets_fetched == ["BTC"]


def test_chunk_boundary_dates_are_not_double_counted(db):
    """PRIMARY KEY (asset, date) dedup must make a boundary date safe even
    if a provider's chunk windows were to overlap by a day."""
    since = date(2026, 1, 1)
    as_of = since + timedelta(days=400)
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    all_dates = {since + timedelta(days=i): Decimal("1000") for i in range(401)}
    provider = _FakeProvider({"BTC": all_dates})

    backfill_historical_prices(db, provider, since=since, as_of=as_of)

    store = HistoricalPriceStore(db)
    stored_dates = store.get_existing_dates("BTC")
    store.close()
    assert len(stored_dates) == 401  # no duplicates despite chunking


def test_one_chunk_fails_others_succeed_yields_partial(db):
    """A provider that fails only on the SECOND of three chunks must leave
    the asset with some data stored (assets_partial), not wipe everything
    or count as a full failure."""
    since = date(2026, 1, 1)
    as_of = since + timedelta(days=400)
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    all_dates = {since + timedelta(days=i): Decimal("1000") for i in range(401)}

    class _ChunkFailingProvider:
        def __init__(self):
            self.call_log = []

        def get_historical_prices_range(self, asset, fiat, date_from, date_to):
            self.call_log.append((date_from, date_to))
            if len(self.call_log) == 2:  # fail exactly the 2nd chunk
                return {}
            return {d: p for d, p in all_dates.items() if date_from <= d <= date_to}

    provider = _ChunkFailingProvider()
    result = backfill_historical_prices(db, provider, since=since, as_of=as_of)

    assert len(provider.call_log) == 3
    assert result.assets_partial == ["BTC"]
    assert 0 < result.dates_stored < 401


def test_already_covered_chunk_is_skipped_no_http_call(db):
    """If every date in one chunk's window is already cached, that chunk
    must not trigger an HTTP call at all, even though other chunks still
    have missing dates."""
    since = date(2026, 1, 1)
    as_of = since + timedelta(days=400)
    _seed_trade(db, "BTC", datetime(2026, 1, 1))

    # Pre-fill the entire FIRST chunk (180 days) directly in the store.
    store = HistoricalPriceStore(db)
    from core.historical_price_store import HistoricalPrice
    for i in range(180):
        store.set_price(HistoricalPrice(
            asset="BTC", price_date=since + timedelta(days=i),
            price_czk=Decimal("1000"), fetched_at=datetime.now(),
        ))
    store.close()

    all_dates = {since + timedelta(days=i): Decimal("1000") for i in range(401)}
    provider = _FakeProvider({"BTC": all_dates})
    backfill_historical_prices(db, provider, since=since, as_of=as_of)

    # Only the 2nd and 3rd chunk windows should have been requested.
    requested_froms = [c[2] for c in provider.call_log]
    assert since not in requested_froms


def test_idempotent_after_chunked_backfill(db):
    """Re-running a chunked backfill must be a total no-op (zero HTTP
    calls) once everything in range is cached — same guarantee as the
    single-chunk case, just over a longer range."""
    since = date(2026, 1, 1)
    as_of = since + timedelta(days=400)
    _seed_trade(db, "BTC", datetime(2026, 1, 1))
    all_dates = {since + timedelta(days=i): Decimal("1000") for i in range(401)}
    provider = _FakeProvider({"BTC": all_dates})

    backfill_historical_prices(db, provider, since=since, as_of=as_of)
    provider.call_log.clear()
    result = backfill_historical_prices(db, provider, since=since, as_of=as_of)

    assert provider.call_log == []
    assert result.assets_already_complete == ["BTC"]
