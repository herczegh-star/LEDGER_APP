"""Tests for deterministic ordering of LedgerStore.timeline().

Covers: same-timestamp tie-breaking by (id ASC, row_fp ASC).
No external fixtures needed — uses tmp_path only.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow

_TS = datetime(2025, 6, 15, 10, 0, 0)  # shared fixed timestamp for tie tests


@pytest.fixture
def store(tmp_path):
    s = LedgerStore(str(tmp_path / "ordering.db"))
    yield s
    s.close()


def _row(id_: str, asset: str, amount: str = "1", ts: datetime = _TS) -> RawRow:
    return RawRow(
        id=id_,
        timestamp=ts,
        type="BUY",
        asset=asset,
        amount=Decimal(amount),
        currency="EUR",
        price=Decimal("1"),
        venue="test",
    )


# ── Primary order: timestamp ASC ─────────────────────────────────────────────

def test_different_timestamps_ordered_correctly(store):
    store.insert(_row("trade-late",  "BTC", ts=datetime(2025, 6, 20)))
    store.insert(_row("trade-early", "ETH", ts=datetime(2025, 6, 10)))

    result = store.timeline()

    assert len(result) == 2
    assert result[0].id == "trade-early"
    assert result[1].id == "trade-late"


# ── Tie-breaker 1: same timestamp, different id → order by id ASC ─────────────

def test_same_timestamp_ordered_by_id_asc(store):
    # "z-trade" > "a-trade" lexicographically; id ASC puts "a-trade" first
    store.insert(_row("z-trade", "ZZZ"))
    store.insert(_row("a-trade", "AAA"))

    result = store.timeline()

    assert len(result) == 2
    assert result[0].id == "a-trade", "Lower id must come first when timestamps are equal"
    assert result[1].id == "z-trade"


def test_three_rows_same_timestamp_ordered_by_id(store):
    for id_ in ["c-trade", "a-trade", "b-trade"]:
        store.insert(_row(id_, id_.upper()[:3]))  # unique assets to avoid fp collision

    result = store.timeline()

    assert [r.id for r in result] == ["a-trade", "b-trade", "c-trade"]


# ── Tie-breaker 2: same timestamp AND same id → order by row_fp ASC ───────────

def test_same_timestamp_same_id_ordered_by_row_fp(store):
    """Double-entry pair: rows share trade id, differ only by asset/amount/row_fp."""
    # BUY trade pair: BTC (positive) + EUR (negative)
    row_btc = RawRow(
        id="shared-trade-id",
        timestamp=_TS,
        type="BUY",
        asset="BTC",
        amount=Decimal("0.5"),
        currency="EUR",
        price=Decimal("50000"),
        venue="test",
    )
    row_eur = RawRow(
        id="shared-trade-id",
        timestamp=_TS,
        type="BUY",
        asset="EUR",
        amount=Decimal("-25000"),
        currency="EUR",
        price=Decimal("1"),
        venue="test",
    )

    # Compute expected fingerprints to know which comes first
    fp_btc = row_btc.fingerprint()
    fp_eur = row_eur.fingerprint()
    expected_first_asset = "BTC" if fp_btc < fp_eur else "EUR"
    expected_second_asset = "EUR" if fp_btc < fp_eur else "BTC"

    store.insert(row_btc)
    store.insert(row_eur)

    result = store.timeline()

    assert len(result) == 2
    assert result[0].id == result[1].id == "shared-trade-id"
    assert result[0].asset == expected_first_asset
    assert result[1].asset == expected_second_asset


# ── Idempotence: same result regardless of insertion order ─────────────────────

def test_ordering_independent_of_insertion_order(store, tmp_path):
    """Inserting in reverse order must still yield same timeline()."""
    store2 = LedgerStore(str(tmp_path / "ordering2.db"))

    rows = [
        _row("c-trade", "CCC"),
        _row("a-trade", "AAA"),
        _row("b-trade", "BBB"),
    ]

    for r in rows:
        store.insert(r)
    for r in reversed(rows):
        store2.insert(r)

    result1 = [r.id for r in store.timeline()]
    result2 = [r.id for r in store2.timeline()]

    assert result1 == result2 == ["a-trade", "b-trade", "c-trade"]

    store2.close()


# ── timeline_filtered() uses the same tie-breakers ────────────────────────────

def test_timeline_filtered_same_determinism(store):
    store.insert(_row("z-trade", "ZZZ"))
    store.insert(_row("a-trade", "AAA"))

    result = store.timeline_filtered()  # no filters — returns all

    assert len(result) == 2
    assert result[0].id == "a-trade"
    assert result[1].id == "z-trade"
