"""ReconciliationStore: SQLite append-only store for
cash_reconciliation_snapshots.

Lives in the SAME .db file as LedgerStore's `ledger` table (one backup
covers both), but is a COMPLETELY separate table with its own schema, own
class, and zero shared code path with LedgerStore / RawRow /
compute_positions() / compute_cash_balances(). A reconciliation snapshot is
a diagnostic fact about the real world (what a bank/exchange app reports),
never an accounting transaction — see core/reconciliation.py's module
docstring. Nothing in this file ever touches the `ledger` table.

Lifecycle (Store Initialization Safety fix): opening a ReconciliationStore
or calling any read method NEVER mutates the database — no CREATE TABLE, no
CREATE INDEX, no write of any kind. Schema creation is a SEPARATE, explicit,
idempotent step (ensure_schema()), only ever invoked from a conscious
setup/migration path such as core.services.ui_facade.create_db() — never
implicitly. A read or write against a DB whose schema was never initialized
raises ReconciliationSchemaNotInitializedError with a clear message, rather
than silently creating the table on the fly.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional


class ReconciliationSchemaNotInitializedError(RuntimeError):
    """Raised when cash_reconciliation_snapshots does not exist yet on the
    DB a ReconciliationStore was opened against. Callers must explicitly
    initialize the schema first (see ReconciliationStore.ensure_schema(),
    or core.services.ui_facade.create_db() for the app's normal setup
    path) — this is never silently recovered from by creating the table
    as a side effect of a read or write."""


@dataclass
class ReconciliationSnapshot:
    """One row of cash_reconciliation_snapshots.

    reported_balance is the external fact (what the real account showed).
    Calculated balance and difference are NEVER stored — always computed
    live from the ledger (see core.reports.cash.compute_cash_balance_as_of).
    """

    id: str
    venue: str
    account: str
    currency: str
    reported_balance: Decimal
    as_of: datetime        # accounting-effective: when reported_balance was true
    created_at: datetime   # audit-only: when this snapshot was actually written
    note: Optional[str] = None


_TABLE_NAME = "cash_reconciliation_snapshots"


class ReconciliationStore:
    """Append-only store for reconciliation snapshots. No UPDATE/DELETE path
    is exposed — a correction is a NEW snapshot, mirroring the ledger's own
    append-only ethos, even though this table is not part of the accounting
    domain and could technically support UPDATE/DELETE safely.

    __init__ ONLY opens a connection — it never creates or alters schema.
    Call ensure_schema() explicitly (once, from a setup path) before first
    use on a fresh DB. Every read/write method checks schema presence first
    and raises ReconciliationSchemaNotInitializedError if it is missing,
    rather than creating it implicitly.
    """

    def __init__(self, db_path: str = "ledger.db", read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        if read_only:
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def ensure_schema(self) -> None:
        """Explicit, idempotent schema initialization (CREATE TABLE/INDEX IF
        NOT EXISTS). Must be called from a conscious setup/migration path —
        never from __init__ or any read/write method. Raises RuntimeError
        if called on a read_only connection."""
        if self.read_only:
            raise RuntimeError(
                "Cannot initialize schema on a read_only ReconciliationStore "
                "connection — open one with read_only=False (the default) "
                "for schema setup."
            )
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                venue TEXT NOT NULL,
                account TEXT NOT NULL,
                currency TEXT NOT NULL,
                reported_balance TEXT NOT NULL,
                as_of TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT
            )
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_reconciliation_account
            ON {_TABLE_NAME}(venue, account, currency)
        """)
        self.conn.commit()

    def schema_exists(self) -> bool:
        """Read-only check — safe on a read_only connection too (a plain
        SELECT against sqlite_master, never a write)."""
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE_NAME,),
        ).fetchone()
        return row is not None

    def _require_schema(self) -> None:
        if not self.schema_exists():
            raise ReconciliationSchemaNotInitializedError(
                f"'{_TABLE_NAME}' table does not exist on {self.db_path!r}. "
                "Reconciliation schema is never created implicitly — call "
                "ReconciliationStore(db_path).ensure_schema() first (this "
                "happens automatically via core.services.ui_facade.create_db() "
                "for a normally-initialized app database)."
            )

    def add_snapshot(self, snapshot: ReconciliationSnapshot) -> None:
        self._require_schema()
        self.conn.execute(
            f"""INSERT INTO {_TABLE_NAME}
               (id, venue, account, currency, reported_balance, as_of, created_at, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.id,
                snapshot.venue.lower(),
                snapshot.account,
                snapshot.currency.upper(),
                str(snapshot.reported_balance),
                snapshot.as_of.isoformat(),
                snapshot.created_at.isoformat(),
                snapshot.note,
            ),
        )
        self.conn.commit()

    def get_snapshots(self, venue: str, account: str, currency: str) -> List[ReconciliationSnapshot]:
        """All snapshots for one (venue, account, currency), oldest as_of first."""
        self._require_schema()
        rows = self.conn.execute(
            f"""SELECT * FROM {_TABLE_NAME}
               WHERE venue = ? AND account = ? AND currency = ?
               ORDER BY as_of ASC, created_at ASC""",
            (venue.lower(), account, currency.upper()),
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def get_all_snapshots(self) -> List[ReconciliationSnapshot]:
        self._require_schema()
        rows = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} "
            "ORDER BY venue ASC, account ASC, currency ASC, as_of ASC"
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def count(self) -> int:
        self._require_schema()
        return self.conn.execute(
            f"SELECT COUNT(*) FROM {_TABLE_NAME}"
        ).fetchone()[0]

    def _row_to_snapshot(self, r: sqlite3.Row) -> ReconciliationSnapshot:
        return ReconciliationSnapshot(
            id=r["id"], venue=r["venue"], account=r["account"], currency=r["currency"],
            reported_balance=Decimal(r["reported_balance"]),
            as_of=datetime.fromisoformat(r["as_of"]),
            created_at=datetime.fromisoformat(r["created_at"]),
            note=r["note"],
        )

    def close(self) -> None:
        self.conn.close()
