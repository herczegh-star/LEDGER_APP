"""Phase BUY-FIX — RawRow.imported_at: audit-only creation time.

Covers the timestamp model decision from the architecture analysis:
`imported_at` (audit/creation) must round-trip from the ledger_store's
pre-existing `imported_at` DB column, and must NEVER influence accounting
(compute_positions ordering/results), fingerprint, or dedup.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.positions import compute_positions
from core.services.trade_service import AddTradeInput, add_trade

_TS = datetime(2026, 9, 9, 21, 22, 39)


def test_imported_at_loaded_from_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=_TS, base_asset="ARB", base_amount=Decimal("100"),
        quote_currency="CZK", quote_amount=Decimal("1000"), venue="revolut",
    ))
    store = LedgerStore(db_path)
    rows = store.timeline()
    store.close()

    for row in rows:
        assert row.imported_at is not None
        # imported_at is the real insert moment — must not equal the (much
        # earlier, backdated) trade timestamp.
        assert row.imported_at > _TS


def test_imported_at_does_not_affect_fingerprint():
    """Two rows identical in every legacy field but different imported_at
    must produce the IDENTICAL fingerprint — imported_at is audit-only."""
    row_a = RawRow(
        id="x", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("100"),
        currency="CZK", price=Decimal("10"), venue="revolut",
        imported_at=datetime(2026, 9, 9, 21, 25, 0),
    )
    row_b = RawRow(
        id="x", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("100"),
        currency="CZK", price=Decimal("10"), venue="revolut",
        imported_at=datetime(2026, 9, 19, 12, 0, 0),
    )
    assert row_a.fingerprint() == row_b.fingerprint()
    assert row_a.fingerprint_v2() == row_b.fingerprint_v2()


def test_imported_at_does_not_affect_dedup(tmp_path):
    """Inserting the same logical row twice (with different imported_at
    values, since each insert stamps its own actual-now) must still dedup
    to exactly one stored row — imported_at is not part of row_fp."""
    db_path = str(tmp_path / "test.db")
    row = RawRow(
        id="x", timestamp=_TS, type="TRANSFER", asset="ARB", amount=Decimal("5"),
        currency="CZK", price=Decimal("1"), venue="revolut",
    )
    store = LedgerStore(db_path)
    first = store.insert(row)
    second = store.insert(row)  # identical row, re-inserted
    count = store.count()
    store.close()
    assert first is True
    assert second is False  # deduped
    assert count == 1


def test_imported_at_does_not_affect_compute_positions():
    """Two otherwise-identical row sets differing only in imported_at must
    produce bit-identical compute_positions() output."""
    rows_a = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("100"),
               currency="CZK", price=Decimal("10"), venue="revolut",
               imported_at=datetime(2026, 9, 9, 21, 25, 0)),
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="CZK", amount=Decimal("-1000"),
               currency="CZK", price=Decimal("1"), venue="revolut",
               imported_at=datetime(2026, 9, 9, 21, 25, 0)),
    ]
    rows_b = [
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("100"),
               currency="CZK", price=Decimal("10"), venue="revolut",
               imported_at=None),
        RawRow(id="b1", timestamp=_TS, type="BUY", asset="CZK", amount=Decimal("-1000"),
               currency="CZK", price=Decimal("1"), venue="revolut",
               imported_at=datetime(2026, 9, 19, 12, 0, 0)),
    ]
    pos_a = compute_positions(rows_a)
    pos_b = compute_positions(rows_b)
    assert pos_a == pos_b


def test_imported_at_defaults_to_none_for_freshly_built_rawrow():
    """A RawRow built directly (not read back from the DB) has
    imported_at=None by default — it's only ever populated on read from the
    ledger_store, never fabricated by application code."""
    row = RawRow(
        id="x", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("1"),
        currency="CZK", price=Decimal("1"), venue="revolut",
    )
    assert row.imported_at is None


def test_to_dict_serializes_imported_at_as_isoformat_string():
    row = RawRow(
        id="x", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("1"),
        currency="CZK", price=Decimal("1"), venue="revolut",
        imported_at=datetime(2026, 9, 19, 12, 0, 0),
    )
    d = row.to_dict()
    assert d["imported_at"] == "2026-09-19T12:00:00"

    row_none = RawRow(
        id="y", timestamp=_TS, type="BUY", asset="ARB", amount=Decimal("1"),
        currency="CZK", price=Decimal("1"), venue="revolut",
    )
    assert row_none.to_dict()["imported_at"] is None
