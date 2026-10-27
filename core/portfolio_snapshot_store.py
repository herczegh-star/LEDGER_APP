"""PortfolioSnapshotStore: SQLite store for portfolio_snapshots /
portfolio_snapshot_positions.

Lives in the SAME .db file as LedgerStore's `ledger` table, as COMPLETELY
separate tables — a derived analytical layer, zero relation to RawRow /
compute_positions() / WAC / realized PnL. The ledger stays the sole
accounting source of truth; these two tables only ever cache an already
-computed headline result (see core/services/portfolio_history_service.py's
maybe_save_portfolio_snapshot(), the only writer).

NOT to be confused with core.services.portfolio_snapshot_service.
PortfolioSnapshot — that is an existing, unrelated, in-memory/point-in-time
DTO (invested/net_flow/assets_held/top_position/roi), never persisted.
This module's PortfolioSnapshotRecord is the persisted, timestamped,
headline-KPI history row.

Lifecycle (same fixed pattern as core/account_role_store.py's Store
Initialization Safety fix): opening PortfolioSnapshotStore or calling any
read method NEVER mutates the database. Schema creation is a separate,
explicit, idempotent step (ensure_schema()), only ever invoked from a
conscious setup/migration path — never implicitly. A read or write
against a DB whose schema was never initialized raises
PortfolioSnapshotSchemaNotInitializedError.

Append-only: a snapshot is a historical fact about what the headline
numbers were at a point in time — it is never edited or deleted (same
rationale as cash_reconciliation_snapshots / cash_account_roles).

A snapshot's header row and its per-asset position rows are written in
ONE transaction (save_snapshot()) so a reader never observes a header
without its positions, or vice versa.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional


class PortfolioSnapshotSchemaNotInitializedError(RuntimeError):
    """Raised when portfolio_snapshots/portfolio_snapshot_positions do not
    exist yet on the DB a PortfolioSnapshotStore was opened against.
    Callers must explicitly initialize the schema first (see
    PortfolioSnapshotStore.ensure_schema(), or
    core.services.ui_facade.create_db() for the app's normal setup
    path)."""


@dataclass
class PortfolioSnapshotPositionRecord:
    """One asset's contribution to a PortfolioSnapshotRecord — mirrors the
    per-asset fields get_dashboard_snapshot() enriches PositionDTO with.
    Stored for EVERY held asset (including PRESALE_ASSETS), even though
    the header's headline totals exclude them — see
    maybe_save_portfolio_snapshot()'s docstring for why."""

    asset: str
    quantity: Decimal
    spot_price: Optional[Decimal]
    value: Optional[Decimal]
    cost_basis: Decimal


@dataclass
class PortfolioSnapshotRecord:
    """One row of portfolio_snapshots, plus its linked per-asset detail.

    portfolio_value / cost_basis / unrealized_pnl / roi_total are the
    HEADLINE figures — i.e. computed the same way as the Dashboard's
    update_kpis() headline KPIs (PRESALE_ASSETS excluded). See
    core/services/portfolio_history_service.py for the single place that
    computes these before calling save_snapshot().
    """

    pk: Optional[int]  # None before insert; set by save_snapshot()
    timestamp: datetime
    portfolio_value: Optional[Decimal]
    cost_basis: Decimal
    unrealized_pnl: Optional[Decimal]
    roi_total: Optional[Decimal]
    net_flow_czk: Optional[Decimal]
    positions: List[PortfolioSnapshotPositionRecord] = field(default_factory=list)


_HEADER_TABLE = "portfolio_snapshots"
_POSITIONS_TABLE = "portfolio_snapshot_positions"


class PortfolioSnapshotStore:
    """Append-only store for portfolio snapshot history. __init__ ONLY
    opens a connection — it never creates or alters schema. Call
    ensure_schema() explicitly (once, from a setup path) before first use
    on a fresh DB."""

    def __init__(self, db_path: str = "ledger.db", read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        if read_only:
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def ensure_schema(self) -> None:
        """Explicit, idempotent schema initialization. Raises RuntimeError
        if called on a read_only connection."""
        if self.read_only:
            raise RuntimeError(
                "Cannot initialize schema on a read_only PortfolioSnapshotStore "
                "connection — open one with read_only=False (the default) "
                "for schema setup."
            )
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_HEADER_TABLE} (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                portfolio_value TEXT,
                cost_basis TEXT NOT NULL,
                unrealized_pnl TEXT,
                roi_total TEXT,
                net_flow_czk TEXT
            )
        """)
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_POSITIONS_TABLE} (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_pk INTEGER NOT NULL,
                asset TEXT NOT NULL,
                quantity TEXT NOT NULL,
                spot_price TEXT,
                value TEXT,
                cost_basis TEXT NOT NULL,
                FOREIGN KEY(snapshot_pk) REFERENCES {_HEADER_TABLE}(pk)
            )
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_snapshot_positions_snapshot_pk
            ON {_POSITIONS_TABLE}(snapshot_pk)
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_timestamp
            ON {_HEADER_TABLE}(timestamp)
        """)
        self.conn.commit()

    def schema_exists(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_HEADER_TABLE,),
        ).fetchone()
        return row is not None

    def _require_schema(self) -> None:
        if not self.schema_exists():
            raise PortfolioSnapshotSchemaNotInitializedError(
                f"'{_HEADER_TABLE}' table does not exist on {self.db_path!r}. "
                "Schema is never created implicitly — call "
                "PortfolioSnapshotStore(db_path).ensure_schema() first (this "
                "happens automatically via core.services.ui_facade.create_db() "
                "for a normally-initialized app database)."
            )

    def save_snapshot(self, record: PortfolioSnapshotRecord) -> int:
        """Insert the header row and all its position rows in one
        transaction. Returns the new header pk."""
        self._require_schema()
        with self.conn:
            cur = self.conn.execute(
                f"""INSERT INTO {_HEADER_TABLE}
                   (timestamp, portfolio_value, cost_basis, unrealized_pnl, roi_total, net_flow_czk)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.timestamp.isoformat(),
                    _opt_str(record.portfolio_value),
                    str(record.cost_basis),
                    _opt_str(record.unrealized_pnl),
                    _opt_str(record.roi_total),
                    _opt_str(record.net_flow_czk),
                ),
            )
            snapshot_pk = cur.lastrowid
            for pos in record.positions:
                self.conn.execute(
                    f"""INSERT INTO {_POSITIONS_TABLE}
                       (snapshot_pk, asset, quantity, spot_price, value, cost_basis)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot_pk, pos.asset, str(pos.quantity),
                        _opt_str(pos.spot_price), _opt_str(pos.value), str(pos.cost_basis),
                    ),
                )
        return snapshot_pk

    def get_latest_snapshot(self) -> Optional[PortfolioSnapshotRecord]:
        """Most recent snapshot by timestamp, with its positions. None if
        no snapshot has ever been saved."""
        self._require_schema()
        row = self.conn.execute(
            f"SELECT * FROM {_HEADER_TABLE} ORDER BY timestamp DESC, pk DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self._load_record(row)

    def get_snapshots(
        self,
        time_from: Optional[datetime] = None,
        time_to: Optional[datetime] = None,
    ) -> List[PortfolioSnapshotRecord]:
        """All snapshots (with positions) in [time_from, time_to], oldest
        first. Both bounds optional/inclusive."""
        self._require_schema()
        query = f"SELECT * FROM {_HEADER_TABLE} WHERE 1=1"
        params: list = []
        if time_from is not None:
            query += " AND timestamp >= ?"
            params.append(time_from.isoformat())
        if time_to is not None:
            query += " AND timestamp <= ?"
            params.append(time_to.isoformat())
        query += " ORDER BY timestamp ASC, pk ASC"
        rows = self.conn.execute(query, params).fetchall()
        return [self._load_record(r) for r in rows]

    def count(self) -> int:
        self._require_schema()
        return self.conn.execute(f"SELECT COUNT(*) FROM {_HEADER_TABLE}").fetchone()[0]

    def _load_record(self, row: sqlite3.Row) -> PortfolioSnapshotRecord:
        pos_rows = self.conn.execute(
            f"SELECT * FROM {_POSITIONS_TABLE} WHERE snapshot_pk = ? ORDER BY asset ASC",
            (row["pk"],),
        ).fetchall()
        positions = [
            PortfolioSnapshotPositionRecord(
                asset=p["asset"],
                quantity=Decimal(p["quantity"]),
                spot_price=_opt_decimal(p["spot_price"]),
                value=_opt_decimal(p["value"]),
                cost_basis=Decimal(p["cost_basis"]),
            )
            for p in pos_rows
        ]
        return PortfolioSnapshotRecord(
            pk=row["pk"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            portfolio_value=_opt_decimal(row["portfolio_value"]),
            cost_basis=Decimal(row["cost_basis"]),
            unrealized_pnl=_opt_decimal(row["unrealized_pnl"]),
            roi_total=_opt_decimal(row["roi_total"]),
            net_flow_czk=_opt_decimal(row["net_flow_czk"]),
            positions=positions,
        )

    def close(self) -> None:
        self.conn.close()


def _opt_str(v: Optional[Decimal]) -> Optional[str]:
    return str(v) if v is not None else None


def _opt_decimal(v: Optional[str]) -> Optional[Decimal]:
    return Decimal(v) if v is not None else None
