"""Ledger Store: SQLite append-only databáze. Žádné UPDATE / DELETE."""
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple
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
            "SELECT asset, amount FROM ledger ORDER BY asset"
        ).fetchall()
        totals: dict = {}
        for r in rows:
            asset = r["asset"]
            totals[asset] = totals.get(asset, Decimal("0")) + Decimal(r["amount"])
        return totals

    def venue_balances(self) -> dict:
        rows = self.conn.execute(
            "SELECT venue, asset, amount FROM ledger ORDER BY venue, asset"
        ).fetchall()
        result: dict = {}
        for r in rows:
            venue = r["venue"]
            asset = r["asset"]
            if venue not in result:
                result[venue] = {}
            result[venue][asset] = result[venue].get(asset, Decimal("0")) + Decimal(r["amount"])
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

    def insert_pair(self, row_a: RawRow, row_b: RawRow) -> Tuple[bool, bool]:
        """Vloží dva řádky v jedné transakci (pro double-entry)."""
        fp_a = row_a.fingerprint()
        fp_b = row_b.fingerprint()
        now = datetime.now().isoformat()
        results = [False, False]
        try:
            self.conn.execute(
                """INSERT INTO ledger
                   (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_a.id or "", row_a.timestamp.isoformat(), row_a.type,
                 row_a.asset.upper(), str(row_a.amount), row_a.currency.upper(),
                 str(row_a.price) if row_a.price is not None else None,
                 row_a.venue.lower(), row_a.note, fp_a, now),
            )
            results[0] = True
        except sqlite3.IntegrityError:
            pass
        try:
            self.conn.execute(
                """INSERT INTO ledger
                   (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row_b.id or "", row_b.timestamp.isoformat(), row_b.type,
                 row_b.asset.upper(), str(row_b.amount), row_b.currency.upper(),
                 str(row_b.price) if row_b.price is not None else None,
                 row_b.venue.lower(), row_b.note, fp_b, now),
            )
            results[1] = True
        except sqlite3.IntegrityError:
            pass
        self.conn.commit()
        return tuple(results)

    def get_row_by_pk(self, pk: int) -> Optional[RawRow]:
        """Načte řádek podle primary key."""
        r = self.conn.execute("SELECT * FROM ledger WHERE pk = ?", (pk,)).fetchone()
        if r is None:
            return None
        return self._row_to_rawrow(r)

    def get_rows_by_id(self, row_id: str) -> List[RawRow]:
        """Načte všechny řádky se stejným id (double-entry pár)."""
        rows = self.conn.execute(
            "SELECT * FROM ledger WHERE id = ? ORDER BY timestamp ASC", (row_id,)
        ).fetchall()
        return [self._row_to_rawrow(r) for r in rows]

    def recent_rows(self, limit: int = 20) -> List[dict]:
        """Vrátí posledních N řádků s pk pro zobrazení/výběr."""
        rows = self.conn.execute(
            "SELECT pk, id, timestamp, type, asset, amount, venue FROM ledger ORDER BY pk DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    def close(self):
        self.conn.close()
