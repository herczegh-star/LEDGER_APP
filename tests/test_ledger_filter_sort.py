"""Unit tests for the pure filter_and_sort_ledger helper in ledger_view.py.

No Flet page or database required.
"""
from datetime import datetime
from decimal import Decimal
from typing import List
import uuid

import pytest

from core.model import RawRow
from ui.modules.ledger_view import filter_and_sort_ledger


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_TS1 = datetime(2026, 1, 1, 10, 0, 0)
_TS2 = datetime(2026, 2, 1, 12, 0, 0)
_TS3 = datetime(2026, 3, 1,  8, 0, 0)


def _row(
    asset: str = "BTC",
    type_: str = "BUY",
    venue: str = "kraken",
    ts: datetime = _TS1,
    amount: str = "1",
    note: str = None,
    trade_id: str = None,
) -> RawRow:
    return RawRow(
        id=trade_id or str(uuid.uuid4()),
        timestamp=ts,
        type=type_,
        asset=asset,
        amount=Decimal(amount),
        currency="EUR",
        price=Decimal("40000"),
        venue=venue,
        note=note,
    )


# Sample dataset
_SAMPLE: List[RawRow] = [
    _row("BTC",  "BUY",      "kraken",  _TS2, "1",      note="first buy"),
    _row("ETH",  "SELL",     "kraken",  _TS1, "-5",     note=None),
    _row("SOL",  "TRANSFER", "binance", _TS3, "100",    note="cold wallet"),
    _row("BNB",  "FEE",      "binance", _TS1, "-0.01",  note=None),
    _row("EUR",  "REVERSAL", "kraken",  _TS2, "40000",  note="REVERSAL of abc"),
    _row("USDT", "BUY",      "bybit",   _TS3, "500",    note=None),
]


def _types(rows) -> List[str]:
    return [r.type for r in rows]


def _assets(rows) -> List[str]:
    return [r.asset for r in rows]


def _venues(rows) -> List[str]:
    return [r.venue for r in rows]


# ── No filter (default) ───────────────────────────────────────────────────────

def test_no_filter_returns_all():
    result = filter_and_sort_ledger(_SAMPLE)
    assert len(result) == len(_SAMPLE)


def test_default_sort_is_desc_newest_first():
    result = filter_and_sort_ledger(_SAMPLE)
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps, reverse=True)


def test_does_not_mutate_input():
    original_ids = [r.id for r in _SAMPLE]
    filter_and_sort_ledger(_SAMPLE, search="BTC", type_filter="SELL")
    assert [r.id for r in _SAMPLE] == original_ids


def test_returns_new_list():
    result = filter_and_sort_ledger(_SAMPLE)
    assert result is not _SAMPLE


# ── Search ────────────────────────────────────────────────────────────────────

def test_search_by_asset_exact():
    result = filter_and_sort_ledger(_SAMPLE, search="BTC")
    assert all(r.asset == "BTC" for r in result)
    assert len(result) == 1


def test_search_by_asset_case_insensitive():
    result = filter_and_sort_ledger(_SAMPLE, search="btc")
    assert len(result) == 1
    assert result[0].asset == "BTC"


def test_search_by_asset_substring():
    # "B" matches BTC, BNB
    result = filter_and_sort_ledger(_SAMPLE, search="B")
    assert set(_assets(result)) >= {"BTC", "BNB"}


def test_search_by_type():
    result = filter_and_sort_ledger(_SAMPLE, search="sell")
    assert all("sell" in r.type.lower() for r in result)


def test_search_by_venue():
    result = filter_and_sort_ledger(_SAMPLE, search="binance")
    assert all(r.venue == "binance" for r in result)
    assert len(result) == 2


def test_search_by_note():
    result = filter_and_sort_ledger(_SAMPLE, search="cold wallet")
    assert len(result) == 1
    assert result[0].asset == "SOL"


def test_search_by_trade_id_partial():
    tid = _SAMPLE[0].id
    # Search for first 8 chars of trade_id
    result = filter_and_sort_ledger(_SAMPLE, search=tid[:8])
    assert any(r.id == tid for r in result)


def test_search_empty_no_filter():
    result = filter_and_sort_ledger(_SAMPLE, search="")
    assert len(result) == len(_SAMPLE)


def test_search_no_match_empty():
    result = filter_and_sort_ledger(_SAMPLE, search="XXXXXXNOTFOUND")
    assert result == []


def test_search_whitespace_stripped():
    result = filter_and_sort_ledger(_SAMPLE, search="  btc  ")
    assert len(result) == 1


def test_search_reversal_keyword():
    result = filter_and_sort_ledger(_SAMPLE, search="REVERSAL")
    # Matches both the type and rows where note mentions REVERSAL
    assert len(result) >= 1
    assert all(
        "reversal" in r.type.lower() or "reversal" in (r.note or "").lower()
        for r in result
    )


# ── Type filter ───────────────────────────────────────────────────────────────

def test_type_filter_buy():
    result = filter_and_sort_ledger(_SAMPLE, type_filter="BUY")
    assert all(r.type == "BUY" for r in result)
    assert len(result) == 2


def test_type_filter_sell():
    result = filter_and_sort_ledger(_SAMPLE, type_filter="SELL")
    assert all(r.type == "SELL" for r in result)


def test_type_filter_reversal():
    result = filter_and_sort_ledger(_SAMPLE, type_filter="REVERSAL")
    assert all(r.type == "REVERSAL" for r in result)
    assert len(result) == 1


def test_type_filter_empty_all():
    result = filter_and_sort_ledger(_SAMPLE, type_filter="")
    assert len(result) == len(_SAMPLE)


def test_type_filter_no_match_empty():
    result = filter_and_sort_ledger(_SAMPLE, type_filter="STAKE")
    assert result == []


# ── Venue filter ──────────────────────────────────────────────────────────────

def test_venue_filter_kraken():
    result = filter_and_sort_ledger(_SAMPLE, venue_filter="kraken")
    assert all(r.venue == "kraken" for r in result)
    assert len(result) == 3


def test_venue_filter_binance():
    result = filter_and_sort_ledger(_SAMPLE, venue_filter="binance")
    assert all(r.venue == "binance" for r in result)
    assert len(result) == 2


def test_venue_filter_empty_all():
    result = filter_and_sort_ledger(_SAMPLE, venue_filter="")
    assert len(result) == len(_SAMPLE)


def test_venue_filter_no_match_empty():
    result = filter_and_sort_ledger(_SAMPLE, venue_filter="coinbase")
    assert result == []


# ── Sort ──────────────────────────────────────────────────────────────────────

def test_sort_desc_newest_first():
    result = filter_and_sort_ledger(_SAMPLE, sort_dir="desc")
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps, reverse=True)


def test_sort_asc_oldest_first():
    result = filter_and_sort_ledger(_SAMPLE, sort_dir="asc")
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps)


def test_sort_desc_first_is_latest():
    result = filter_and_sort_ledger(_SAMPLE, sort_dir="desc")
    assert result[0].timestamp == _TS3


def test_sort_asc_first_is_earliest():
    result = filter_and_sort_ledger(_SAMPLE, sort_dir="asc")
    assert result[0].timestamp == _TS1


# ── Combined filters ──────────────────────────────────────────────────────────

def test_search_and_type_filter():
    result = filter_and_sort_ledger(_SAMPLE, search="kraken", type_filter="BUY")
    assert all(r.venue == "kraken" and r.type == "BUY" for r in result)


def test_type_and_venue_filter():
    result = filter_and_sort_ledger(_SAMPLE, type_filter="BUY", venue_filter="bybit")
    assert all(r.type == "BUY" and r.venue == "bybit" for r in result)
    assert len(result) == 1


def test_search_and_sort():
    result = filter_and_sort_ledger(_SAMPLE, search="kraken", sort_dir="asc")
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps)


def test_all_filters_combined():
    result = filter_and_sort_ledger(
        _SAMPLE,
        search="kraken",
        type_filter="BUY",
        venue_filter="kraken",
        sort_dir="asc",
    )
    assert all(r.venue == "kraken" and r.type == "BUY" for r in result)
    timestamps = [r.timestamp for r in result]
    assert timestamps == sorted(timestamps)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_input():
    assert filter_and_sort_ledger([]) == []


def test_single_row():
    rows = [_row()]
    result = filter_and_sort_ledger(rows)
    assert len(result) == 1


def test_rows_with_none_note_no_crash():
    rows = [_row(note=None), _row(note="hello", asset="ETH")]
    result = filter_and_sort_ledger(rows, search="hello")
    assert len(result) == 1
    assert result[0].asset == "ETH"


def test_rows_with_none_note_search_does_not_match_none():
    rows = [_row(note=None)]
    result = filter_and_sort_ledger(rows, search="None")
    assert result == []
