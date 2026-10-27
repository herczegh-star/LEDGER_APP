"""HistoricalPriceStore: SQLite store for historical_prices.

Lives in the SAME .db file as LedgerStore's `ledger` table, as a COMPLETELY
separate table — pure analytical cache, zero relation to RawRow /
compute_positions() / WAC / realized PnL. See
core/services/historical_price_service.py for the backfill logic that
populates this table.

Lifecycle (same fixed pattern as core/account_role_store.py's Store
Initialization Safety fix): opening HistoricalPriceStore or calling any
read method NEVER mutates the database. Schema creation is a separate,
explicit, idempotent step (ensure_schema()), only ever invoked from a
conscious setup/migration path — never implicitly. A read or write
against a DB whose schema was never initialized raises
HistoricalPriceSchemaNotInitializedError.

Immutable-once-written: a (asset, date) price is a historical fact about
a date that has already passed — once fetched, it never needs to change.
set_price() therefore uses INSERT OR IGNORE (first write wins, silent
no-op on a repeat) rather than an upsert — this is what makes repeated
backfill runs cheap and idempotent (see historical_price_service.py).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Set


class HistoricalPriceSchemaNotInitializedError(RuntimeError):
    """Raised when historical_prices does not exist yet on the DB a
    HistoricalPriceStore was opened against. Callers must explicitly
    initialize the schema first (see HistoricalPriceStore.ensure_schema(),
    or core.services.ui_facade.create_db() for the app's normal setup
    path)."""


@dataclass
class HistoricalPrice:
    """One row of historical_prices — a single asset's price on a single
    calendar date, in CZK. See core/historical_price_store.py's module
    docstring for why this is immutable-once-written."""

    asset: str
    price_date: date
    price_czk: Decimal
    fetched_at: datetime


_TABLE_NAME = "historical_prices"


class HistoricalPriceStore:
    """Cache store for historical daily prices. __init__ ONLY opens a
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
                "Cannot initialize schema on a read_only HistoricalPriceStore "
                "connection — open one with read_only=False (the default) "
                "for schema setup."
            )
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                asset      TEXT NOT NULL,
                date       TEXT NOT NULL,
                price_czk  TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (asset, date)
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
            raise HistoricalPriceSchemaNotInitializedError(
                f"'{_TABLE_NAME}' table does not exist on {self.db_path!r}. "
                "Schema is never created implicitly — call "
                "HistoricalPriceStore(db_path).ensure_schema() first (this "
                "happens automatically via core.services.ui_facade.create_db() "
                "for a normally-initialized app database)."
            )

    def set_price(self, price: HistoricalPrice) -> bool:
        """Insert a price for (asset, date) if not already present.

        Returns True if a new row was written, False if a price for this
        (asset, date) already existed (first-write-wins — see module
        docstring; the existing value is never overwritten).
        """
        self._require_schema()
        cur = self.conn.execute(
            f"""INSERT OR IGNORE INTO {_TABLE_NAME} (asset, date, price_czk, fetched_at)
               VALUES (?, ?, ?, ?)""",
            (
                price.asset.upper(),
                price.price_date.isoformat(),
                str(price.price_czk),
                price.fetched_at.isoformat(),
            ),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_price(self, asset: str, price_date: date) -> Optional[HistoricalPrice]:
        self._require_schema()
        row = self.conn.execute(
            f"SELECT * FROM {_TABLE_NAME} WHERE asset = ? AND date = ?",
            (asset.upper(), price_date.isoformat()),
        ).fetchone()
        return self._row_to_price(row) if row is not None else None

    def get_existing_dates(self, asset: str) -> Set[date]:
        """All dates already cached for *asset* — used by the backfill
        service to compute which (asset, date) pairs are still missing."""
        self._require_schema()
        rows = self.conn.execute(
            f"SELECT date FROM {_TABLE_NAME} WHERE asset = ?",
            (asset.upper(),),
        ).fetchall()
        return {date.fromisoformat(r["date"]) for r in rows}

    def count(self) -> int:
        self._require_schema()
        return self.conn.execute(f"SELECT COUNT(*) FROM {_TABLE_NAME}").fetchone()[0]

    def _row_to_price(self, r: sqlite3.Row) -> HistoricalPrice:
        return HistoricalPrice(
            asset=r["asset"],
            price_date=date.fromisoformat(r["date"]),
            price_czk=Decimal(r["price_czk"]),
            fetched_at=datetime.fromisoformat(r["fetched_at"]),
        )

    def close(self) -> None:
        self.conn.close()
