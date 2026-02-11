"""Ledger Store: SQLite append-only databáze. Žádné UPDATE / DELETE."""
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from core.model import RawRow


class LedgerStore:
    def __init__(self, db_path: str = "ledger.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                asset TEXT NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                price TEXT,
                venue TEXT NOT NULL,
                note TEXT,
                row_fp TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_row_fp ON ledger(row_fp)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger(timestamp)
        """)
        self.conn.commit()

    def insert(self, row: RawRow) -> bool:
        fp = row.fingerprint()
        try:
            self.conn.execute(
                """INSERT INTO ledger
                   (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.id or "",
                    row.timestamp.isoformat(),
                    row.type,
                    row.asset.upper(),
                    str(row.amount),
                    row.currency.upper(),
                    str(row.price) if row.price is not None else None,
                    row.venue.lower(),
                    row.note,
                    fp,
                    datetime.now().isoformat(),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def import_rows(self, rows: List[RawRow]) -> dict:
        inserted = 0
        skipped = 0
        for row in rows:
            if self.insert(row):
                inserted += 1
            else:
                skipped += 1
        return {"inserted": inserted, "skipped": skipped}

    def _row_to_rawrow(self, r: sqlite3.Row) -> RawRow:
        return RawRow(
            id=r["id"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            type=r["type"],
            asset=r["asset"],
            amount=Decimal(r["amount"]),
            currency=r["currency"],
            price=Decimal(r["price"]) if r["price"] else None,
            venue=r["venue"],
            note=r["note"],
        )

    def timeline(self) -> List[RawRow]:
        rows = self.conn.execute("SELECT * FROM ledger ORDER BY timestamp ASC").fetchall()
        return [self._row_to_rawrow(r) for r in rows]

    def asset_balances(self) -> dict:
        rows = self.conn.execute(
            "SELECT asset, SUM(CAST(amount AS REAL)) as total FROM ledger GROUP BY asset ORDER BY asset"
        ).fetchall()
        return {r["asset"]: Decimal(str(r["total"])) for r in rows}

    def venue_balances(self) -> dict:
        rows = self.conn.execute(
            """SELECT venue, asset, SUM(CAST(amount AS REAL)) as total
               FROM ledger GROUP BY venue, asset ORDER BY venue, asset"""
        ).fetchall()
        result = {}
        for r in rows:
            venue = r["venue"]
            if venue not in result:
                result[venue] = {}
            result[venue][r["asset"]] = Decimal(str(r["total"]))
        return result

    def diagnostics(self) -> List[dict]:
        warnings = []
        venue_bal = self.venue_balances()
        for venue, assets in venue_bal.items():
            for asset, balance in assets.items():
                if balance < 0:
                    warnings.append({
                        "type": "NEGATIVE_BALANCE",
                        "venue": venue,
                        "asset": asset,
                        "balance": balance,
                        "msg": f"Záporný zůstatek: {asset} na {venue} = {balance}"
                    })
        return warnings

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    def close(self):
        self.conn.close()
