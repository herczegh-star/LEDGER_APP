"""Reversal service: append-only trade cancellation by trade_id.

Reversing a trade creates a new group of REVERSAL rows that exactly
negate the original rows. No data is ever deleted or modified.

New reversal group ID format: "REV_{original_trade_id}_{8-char-hex}"
This allows querying all rows of a reversal as one logical group.

Usage:
    rows = reverse_trade(db_path, trade_id="550e8400-...")
    # → list of inserted RawRow objects (REVERSAL type)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from core.ledger_store import LedgerStore
from core.model import RawRow


def build_reversal_rows(
    original_rows: List[RawRow],
    new_trade_id: Optional[str] = None,
) -> List[RawRow]:
    """Pure function: build reversal rows for a group of original rows.

    All reversal rows share the same new_trade_id so they form a
    coherent group (same as the original trade pattern).

    Args:
        original_rows: The rows to be reversed (must be non-empty).
            All are expected to share the same .id (trade_id).
        new_trade_id: Optional override for the reversal group ID.
            Defaults to "REV_{original_trade_id}_{8-char-hex}".

    Returns:
        List of RawRow objects with:
          - type = "REVERSAL"
          - amount = -original.amount  (exact Decimal negation)
          - id = new_trade_id          (shared by all reversal rows)
          - timestamp = now (truncated to seconds)
          - note = "REVERSAL of {original_trade_id}" + original note
          - all other fields preserved

    Raises:
        ValueError: If original_rows is empty.
    """
    if not original_rows:
        raise ValueError("Cannot build reversals: original_rows is empty.")

    original_trade_id = original_rows[0].id or ""
    if new_trade_id is None:
        short = uuid.uuid4().hex[:8]
        new_trade_id = f"REV_{original_trade_id}_{short}"

    now = datetime.now().replace(microsecond=0)
    reversal_rows: List[RawRow] = []

    for row in original_rows:
        # Compose note: reference original, preserve existing note
        note_parts = [f"REVERSAL of {original_trade_id}"]
        if row.note:
            note_parts.append(row.note)
        note = "; ".join(note_parts)

        reversal_rows.append(RawRow(
            id=new_trade_id,
            timestamp=now,
            type="REVERSAL",
            asset=row.asset,
            amount=-row.amount,
            currency=row.currency,
            price=row.price,
            venue=row.venue,
            note=note,
        ))

    return reversal_rows


def reverse_trade(db_path: str, trade_id: str) -> List[RawRow]:
    """Load all rows for trade_id, build and append reversal rows.

    Steps:
      a) Load all rows with the given trade_id from the ledger.
      b) Validate that at least one row exists.
      c) Build reversal rows (pure, deterministic).
      d) Append via LedgerStore.import_rows() — dedup-safe.
      e) Return the appended reversal rows.

    Args:
        db_path: Path to the SQLite ledger file.
        trade_id: The group ID (UUID) of the trade to reverse.

    Returns:
        List of RawRow objects that were built as reversal rows.
        (Rows already present in the ledger — i.e. dedup hits — are
        still returned, so the caller always sees the full reversal.)

    Raises:
        ValueError: If no rows are found for the given trade_id.
    """
    store = LedgerStore(db_path)
    try:
        original_rows = store.get_rows_by_id(trade_id)
        if not original_rows:
            raise ValueError(
                f"No rows found for trade_id '{trade_id}'. "
                "Check the ID and try again."
            )
        existing = store.conn.execute(
            "SELECT COUNT(*) FROM ledger WHERE id LIKE ?",
            (f"REV_{trade_id}_%",),
        ).fetchone()[0]
        if existing > 0:
            raise ValueError(
                f"Trade '{trade_id}' has already been reversed. "
                "Each trade can only be reversed once."
            )
        reversal_rows = build_reversal_rows(original_rows)
        store.import_rows(reversal_rows)
        return reversal_rows
    finally:
        store.close()
