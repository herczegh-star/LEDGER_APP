"""AccountAssignmentStore: SQLite append-only store for
historical_account_assignments.

Row-level overlay: maps a SPECIFIC ledger row (identified by its own
row_fp — already unique per idx_row_fp, no RawRow schema change needed) to
a declared effective_account, WITHOUT ever mutating the original ledger
row. Necessary because a single trade group can legitimately need
per-leg account assignment (crypto leg vs fiat quote leg vs fee leg — see
core/services/trade_service.py's own account propagation rules), so a
trade-group-level mapping would be unsafe.

Same lifecycle pattern as ReconciliationStore / AccountRoleStore: __init__
never mutates schema; ensure_schema() is explicit and idempotent; every
read/write requires the schema to already exist.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


class AccountAssignmentSchemaNotInitializedError(RuntimeError):
    """Raised when historical_account_assignments does not exist yet on the
    DB an AccountAssignmentStore was opened against."""


@dataclass
class AccountAssignment:
    """One row of historical_account_assignments.

    row_fp addresses the SPECIFIC ledger row being reassigned (via its
    existing fingerprint — see core/model.py RawRow.fingerprint()).
    trade_id is informational only (for human lookup/grouping), never the
    join key. effective_at / created_at mirror the same audit-vs-effective
    split used throughout this codebase (BUY_COST_CORRECTION, account roles).
    """

    id: str
    row_fp: str
    trade_id: str
    venue: str
    effective_account: str
    effective_at: datetime
    created_at: datetime
    source: Optional[str] = None
    note: Optional[str] = None


_TABLE_NAME = "historical_account_assignments"


class AccountAssignmentStore:
    """Append-only store for historical account assignment overrides.
    __init__ ONLY opens a connection — never creates or alters schema."""

    def __init__(self, db_path: str = "ledger.db", read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        if read_only:
            self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def ensure_schema(self) -> None:
        if self.read_only:
            raise RuntimeError(
                "Cannot initialize schema on a read_only AccountAssignmentStore "
                "connection — open one with read_only=False (the default) "
                "for schema setup."
            )
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                row_fp TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                venue TEXT NOT NULL,
                effective_account TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT,
                note TEXT
            )
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_account_assignment_row_fp
            ON {_TABLE_NAME}(row_fp)
        """)
        self.conn.commit()

    def schema_exists(self) -> bool:
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE_NAME,),
        ).fetchone()
        return row is not None

    def _require_schema(self) -> None:
        if not self.schema_exists():
            raise AccountAssignmentSchemaNotInitializedError(
                f"'{_TABLE_NAME}' table does not exist on {self.db_path!r}. "
                "Assignment schema is never created implicitly — call "
                "AccountAssignmentStore(db_path).ensure_schema() first."
            )

    def add_assignment(self, assignment: AccountAssignment) -> None:
        self._require_schema()
        self.conn.execute(
            f"""INSERT INTO {_TABLE_NAME}
               (id, row_fp, trade_id, venue, effective_account, effective_at, created_at, source, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assignment.id, assignment.row_fp, assignment.trade_id, assignment.venue.lower(),
                assignment.effective_account, assignment.effective_at.isoformat(),
                assignment.created_at.isoformat(), assignment.source, assignment.note,
            ),
        )
        self.conn.commit()

    def get_assignments_for_row_fp(self, row_fp: str) -> List[AccountAssignment]:
        self._require_schema()
        rows = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} WHERE row_fp = ? ORDER BY effective_at ASC, created_at ASC",
            (row_fp,),
        ).fetchall()
        return [self._row_to_assignment(r) for r in rows]

    def get_all_assignments(self) -> Dict[str, List[AccountAssignment]]:
        """All assignments, grouped by row_fp — the shape the central
        resolver (core/account_resolver.py) consumes directly."""
        self._require_schema()
        rows = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} ORDER BY row_fp, effective_at ASC, created_at ASC"
        ).fetchall()
        result: Dict[str, List[AccountAssignment]] = {}
        for r in rows:
            a = self._row_to_assignment(r)
            result.setdefault(a.row_fp, []).append(a)
        return result

    def count(self) -> int:
        self._require_schema()
        return self.conn.execute(f"SELECT COUNT(*) FROM {_TABLE_NAME}").fetchone()[0]

    def _row_to_assignment(self, r: sqlite3.Row) -> AccountAssignment:
        return AccountAssignment(
            id=r["id"], row_fp=r["row_fp"], trade_id=r["trade_id"], venue=r["venue"],
            effective_account=r["effective_account"],
            effective_at=datetime.fromisoformat(r["effective_at"]),
            created_at=datetime.fromisoformat(r["created_at"]),
            source=r["source"], note=r["note"],
        )

    def close(self) -> None:
        self.conn.close()
