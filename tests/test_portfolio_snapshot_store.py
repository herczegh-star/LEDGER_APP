"""core/portfolio_snapshot_store.py — Store Initialization Safety (mirrors
tests/test_account_role_store_lifecycle.py) + save/retrieve roundtrip for
the header + per-asset positions pair.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from core.ledger_store import LedgerStore
from core.portfolio_snapshot_store import (
    PortfolioSnapshotPositionRecord,
    PortfolioSnapshotRecord,
    PortfolioSnapshotSchemaNotInitializedError,
    PortfolioSnapshotStore,
)

_T0 = datetime(2026, 1, 1, 12, 0, 0)
_T1 = datetime(2026, 1, 1, 18, 0, 0)


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
    db_path = str(tmp_path / "test.db")
    store = PortfolioSnapshotStore(db_path)
    store.ensure_schema()
    store.close()
    return db_path


def _record(ts=_T0, value="1000000", cost="800000", pnl="200000", roi="0.25", net_flow="50000"):
    return PortfolioSnapshotRecord(
        pk=None,
        timestamp=ts,
        portfolio_value=Decimal(value) if value is not None else None,
        cost_basis=Decimal(cost),
        unrealized_pnl=Decimal(pnl) if pnl is not None else None,
        roi_total=Decimal(roi) if roi is not None else None,
        net_flow_czk=Decimal(net_flow) if net_flow is not None else None,
        positions=[
            PortfolioSnapshotPositionRecord(
                asset="BTC", quantity=Decimal("0.01"), spot_price=Decimal("2500000"),
                value=Decimal("25000"), cost_basis=Decimal("20000"),
            ),
            PortfolioSnapshotPositionRecord(
                asset="ETH", quantity=Decimal("1"), spot_price=Decimal("80000"),
                value=Decimal("80000"), cost_basis=Decimal("70000"),
            ),
        ],
    )


# ── Store: Initialization Safety ────────────────────────────────────────────

def test_constructing_store_does_not_change_db_hash(ledger_db):
    before = _sha256(ledger_db)
    store = PortfolioSnapshotStore(ledger_db)
    store.close()
    assert _sha256(ledger_db) == before


def test_constructing_store_does_not_create_tables(ledger_db):
    store = PortfolioSnapshotStore(ledger_db)
    exists = store.schema_exists()
    store.close()
    assert exists is False
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "portfolio_snapshots" not in tables
    assert "portfolio_snapshot_positions" not in tables


def test_read_on_uninitialized_schema_raises_without_creating_tables(ledger_db):
    store = PortfolioSnapshotStore(ledger_db)
    with pytest.raises(PortfolioSnapshotSchemaNotInitializedError):
        store.get_latest_snapshot()
    still_missing = not store.schema_exists()
    store.close()
    assert still_missing


def test_save_on_missing_schema_fails_clearly(ledger_db):
    before = _sha256(ledger_db)
    store = PortfolioSnapshotStore(ledger_db)
    with pytest.raises(PortfolioSnapshotSchemaNotInitializedError, match="ensure_schema"):
        store.save_snapshot(_record())
    store.close()
    assert _sha256(ledger_db) == before


def test_ensure_schema_creates_both_tables(ledger_db):
    store = PortfolioSnapshotStore(ledger_db)
    store.ensure_schema()
    store.close()
    conn = sqlite3.connect(ledger_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "portfolio_snapshots" in tables
    assert "portfolio_snapshot_positions" in tables


def test_ensure_schema_is_idempotent(ledger_db):
    store = PortfolioSnapshotStore(ledger_db)
    store.ensure_schema()
    store.save_snapshot(_record())
    store.ensure_schema()
    store.ensure_schema()
    assert store.count() == 1
    store.close()


def test_read_only_connection_ensure_schema_raises(ledger_db):
    store = PortfolioSnapshotStore(ledger_db, read_only=True)
    with pytest.raises(RuntimeError, match="read_only"):
        store.ensure_schema()
    store.close()


def test_read_only_connection_cannot_mutate(ledger_db):
    setup = PortfolioSnapshotStore(ledger_db)
    setup.ensure_schema()
    setup.close()

    before = _sha256(ledger_db)
    ro_store = PortfolioSnapshotStore(ledger_db, read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_store.save_snapshot(_record())
    ro_store.close()
    assert _sha256(ledger_db) == before


# ── Save / retrieve roundtrip ───────────────────────────────────────────────

def test_save_snapshot_returns_pk(db):
    store = PortfolioSnapshotStore(db)
    pk = store.save_snapshot(_record())
    store.close()
    assert isinstance(pk, int)
    assert pk > 0


def test_get_latest_snapshot_returns_header_fields(db):
    store = PortfolioSnapshotStore(db)
    store.save_snapshot(_record())
    latest = store.get_latest_snapshot()
    store.close()
    assert latest.portfolio_value == Decimal("1000000")
    assert latest.cost_basis == Decimal("800000")
    assert latest.unrealized_pnl == Decimal("200000")
    assert latest.roi_total == Decimal("0.25")
    assert latest.net_flow_czk == Decimal("50000")


def test_positions_are_linked_to_correct_snapshot(db):
    """Two snapshots, each with its own BTC row at a different value —
    get_latest_snapshot() must return ONLY the latest one's positions,
    never a mix."""
    store = PortfolioSnapshotStore(db)
    store.save_snapshot(_record(ts=_T0, value="1000000"))
    second = _record(ts=_T1, value="1100000")
    second.positions[0].value = Decimal("30000")  # distinct BTC value for snapshot 2
    pk2 = store.save_snapshot(second)
    latest = store.get_latest_snapshot()
    store.close()
    assert latest.pk == pk2
    btc = next(p for p in latest.positions if p.asset == "BTC")
    assert btc.value == Decimal("30000")


def test_get_latest_snapshot_none_when_empty(db):
    store = PortfolioSnapshotStore(db)
    latest = store.get_latest_snapshot()
    store.close()
    assert latest is None


def test_get_snapshots_returns_oldest_first(db):
    store = PortfolioSnapshotStore(db)
    store.save_snapshot(_record(ts=_T1))
    store.save_snapshot(_record(ts=_T0))
    all_snaps = store.get_snapshots()
    store.close()
    assert [s.timestamp for s in all_snaps] == [_T0, _T1]


def test_get_snapshots_time_range_filter(db):
    store = PortfolioSnapshotStore(db)
    store.save_snapshot(_record(ts=_T0))
    store.save_snapshot(_record(ts=_T1))
    only_t1 = store.get_snapshots(time_from=_T1)
    store.close()
    assert [s.timestamp for s in only_t1] == [_T1]


def test_nullable_portfolio_value_and_pnl_round_trip_as_none(db):
    """Deliberate schema deviation from the raw spec (portfolio_value /
    unrealized_pnl are nullable here, not NOT NULL) — mirrors the
    Dashboard's own update_kpis(), which shows '—' rather than a
    misleading 0 when no priced position exists yet. See final report."""
    store = PortfolioSnapshotStore(db)
    store.save_snapshot(_record(value=None, pnl=None, roi=None))
    latest = store.get_latest_snapshot()
    store.close()
    assert latest.portfolio_value is None
    assert latest.unrealized_pnl is None
    assert latest.roi_total is None
    assert latest.cost_basis == Decimal("800000")  # cost_basis still always present
