"""AssetNoteStore: SQLite store for asset_notes.

Lives in the SAME .db file as LedgerStore's `ledger` table, as a COMPLETELY
separate table — pure UI metadata, zero relation to RawRow / compute_positions
/ WAC / realized PnL. See core/services/asset_note_service.py for validation.

Lifecycle (same fixed pattern as core/account_role_store.py's Store
Initialization Safety fix): opening AssetNoteStore or calling any read
method NEVER mutates the database. Schema creation is a separate, explicit,
idempotent step (ensure_schema()), only ever invoked from a conscious
setup/migration path — never implicitly. A read or write against a DB whose
schema was never initialized raises AssetNoteSchemaNotInitializedError.

Unlike cash_account_roles / cash_reconciliation_snapshots (append-only, for
audit reasons), asset_notes is genuinely mutable: a note carries no
accounting/audit significance, and the user must be able to edit or delete
it at any time. Keyed on `asset` alone (not venue) — Dashboard positions are
already aggregated per-asset-ticker globally, not per-venue.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


class AssetNoteSchemaNotInitializedError(RuntimeError):
    """Raised when asset_notes does not exist yet on the DB an
    AssetNoteStore was opened against. Callers must explicitly initialize
    the schema first (see AssetNoteStore.ensure_schema(), or
    core.services.ui_facade.create_db() for the app's normal setup path)."""


@dataclass
class AssetNote:
    """One row of asset_notes — a free-text annotation for one asset."""

    asset: str
    note: str
    updated_at: datetime


_TABLE_NAME = "asset_notes"


class AssetNoteStore:
    """Mutable store for per-asset notes. __init__ ONLY opens a connection —
    it never creates or alters schema. Call ensure_schema() explicitly
    (once, from a setup path) before first use on a fresh DB."""

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
                "Cannot initialize schema on a read_only AssetNoteStore "
                "connection — open one with read_only=False (the default) "
                "for schema setup."
            )
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                asset TEXT PRIMARY KEY,
                note TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
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
            raise AssetNoteSchemaNotInitializedError(
                f"'{_TABLE_NAME}' table does not exist on {self.db_path!r}. "
                "Schema is never created implicitly — call "
                "AssetNoteStore(db_path).ensure_schema() first (this "
                "happens automatically via core.services.ui_facade.create_db() "
                "for a normally-initialized app database)."
            )

    def set_note(self, note: AssetNote) -> None:
        """Insert or fully replace the note for note.asset (upsert)."""
        self._require_schema()
        self.conn.execute(
            f"""INSERT INTO {_TABLE_NAME} (asset, note, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(asset) DO UPDATE SET
                   note = excluded.note,
                   updated_at = excluded.updated_at""",
            (note.asset, note.note, note.updated_at.isoformat()),
        )
        self.conn.commit()

    def get_note(self, asset: str) -> Optional[AssetNote]:
        self._require_schema()
        row = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} WHERE asset = ?",
            (asset,),
        ).fetchone()
        return self._row_to_note(row) if row is not None else None

    def get_all_notes(self) -> List[AssetNote]:
        self._require_schema()
        rows = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} ORDER BY asset ASC"
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def delete_note(self, asset: str) -> bool:
        """Returns True iff a row existed and was deleted."""
        self._require_schema()
        cur = self.conn.execute(
            f"DELETE FROM {_TABLE_NAME} WHERE asset = ?",
            (asset,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def count(self) -> int:
        self._require_schema()
        return self.conn.execute(f"SELECT COUNT(*) FROM {_TABLE_NAME}").fetchone()[0]

    def _row_to_note(self, r: sqlite3.Row) -> AssetNote:
        return AssetNote(
            asset=r["asset"], note=r["note"],
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    def close(self) -> None:
        self.conn.close()
