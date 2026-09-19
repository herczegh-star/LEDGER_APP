"""AccountRoleStore: SQLite append-only store for cash_account_roles.

Lives in the SAME .db file as LedgerStore's `ledger` table, as a COMPLETELY
separate table — zero shared code path with LedgerStore / RawRow /
compute_positions(). See core/account_role.py for the role classification
logic this table feeds.

Lifecycle (same fixed pattern as core/reconciliation_store.py's Store
Initialization Safety fix): opening AccountRoleStore or calling any read
method NEVER mutates the database. Schema creation is a separate, explicit,
idempotent step (ensure_schema()), only ever invoked from a conscious
setup/migration path — never implicitly. A read or write against a DB whose
schema was never initialized raises AccountRoleSchemaNotInitializedError.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


class AccountRoleSchemaNotInitializedError(RuntimeError):
    """Raised when cash_account_roles does not exist yet on the DB an
    AccountRoleStore was opened against. Callers must explicitly initialize
    the schema first (see AccountRoleStore.ensure_schema(), or
    core.services.ui_facade.create_db() for the app's normal setup path)."""


@dataclass
class AccountRole:
    """One row of cash_account_roles.

    effective_at is the accounting-semantic time this role interpretation
    applies from; created_at is audit-only (when this row was actually
    written) — same distinction as ReconciliationSnapshot /
    BUY_COST_CORRECTION's timestamp vs imported_at.
    """

    id: str
    venue: str
    account: str
    currency: str
    role: str
    effective_at: datetime
    created_at: datetime
    source: Optional[str] = None
    note: Optional[str] = None


_TABLE_NAME = "cash_account_roles"


class AccountRoleStore:
    """Append-only store for account roles. __init__ ONLY opens a
    connection — it never creates or alters schema. Call ensure_schema()
    explicitly (once, from a setup path) before first use on a fresh DB."""

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
                "Cannot initialize schema on a read_only AccountRoleStore "
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
                role TEXT NOT NULL,
                effective_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                source TEXT,
                note TEXT
            )
        """)
        self.conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_account_role_key
            ON {_TABLE_NAME}(venue, account, currency)
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
            raise AccountRoleSchemaNotInitializedError(
                f"'{_TABLE_NAME}' table does not exist on {self.db_path!r}. "
                "Role schema is never created implicitly — call "
                "AccountRoleStore(db_path).ensure_schema() first (this "
                "happens automatically via core.services.ui_facade.create_db() "
                "for a normally-initialized app database)."
            )

    def add_role(self, role: AccountRole) -> None:
        self._require_schema()
        self.conn.execute(
            f"""INSERT INTO {_TABLE_NAME}
               (id, venue, account, currency, role, effective_at, created_at, source, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                role.id, role.venue.lower(), role.account, role.currency.upper(),
                role.role, role.effective_at.isoformat(), role.created_at.isoformat(),
                role.source, role.note,
            ),
        )
        self.conn.commit()

    def get_roles(self, venue: str, account: str, currency: str) -> List[AccountRole]:
        self._require_schema()
        rows = self.conn.execute(
            f"""SELECT * FROM {_TABLE_NAME}
               WHERE venue = ? AND account = ? AND currency = ?
               ORDER BY effective_at ASC, created_at ASC""",
            (venue.lower(), account, currency.upper()),
        ).fetchall()
        return [self._row_to_role(r) for r in rows]

    def get_all_roles(self) -> List[AccountRole]:
        self._require_schema()
        rows = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} ORDER BY venue, account, currency, effective_at ASC"
        ).fetchall()
        return [self._row_to_role(r) for r in rows]

    def count(self) -> int:
        self._require_schema()
        return self.conn.execute(f"SELECT COUNT(*) FROM {_TABLE_NAME}").fetchone()[0]

    def _row_to_role(self, r: sqlite3.Row) -> AccountRole:
        return AccountRole(
            id=r["id"], venue=r["venue"], account=r["account"], currency=r["currency"],
            role=r["role"],
            effective_at=datetime.fromisoformat(r["effective_at"]),
            created_at=datetime.fromisoformat(r["created_at"]),
            source=r["source"], note=r["note"],
        )

    def close(self) -> None:
        self.conn.close()
