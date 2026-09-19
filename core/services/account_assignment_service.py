"""Historical account assignment service: append-only writer for
historical_account_assignments.

The ONLY supported way to write an assignment. Validates BEFORE any DB
write — critically, that row_fp actually references a REAL row in the
ledger (AccountAssignmentStore.add_assignment() itself only checks its own
schema presence, not the ledger's content). This is what makes the overlay
safe: you cannot declare an assignment for a row that doesn't exist.

The overlay can NEVER change venue/currency/amount/type of the original
row — this isn't a runtime check, it's a structural guarantee: the
original `ledger` row is never touched by anything in this file (see
core/historical_account_assignment_store.py's module docstring); the
overlay only ever changes what core.account_resolver.resolve_effective_account()
RETURNS for that row, nothing about the row itself.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from core.historical_account_assignment_store import (
    AccountAssignment,
    AccountAssignmentStore,
)
from core.ledger_store import LedgerStore


def add_historical_account_assignment(
    db_path: str,
    row_fp: str,
    effective_account: str,
    effective_at: datetime,
    source: Optional[str] = None,
    note: Optional[str] = None,
) -> AccountAssignment:
    """Validate and append one historical account assignment override.

    Raises ValueError (before any DB write) when:
      - row_fp does not match any row currently in the ledger
      - effective_account empty
      - effective_at not a datetime

    trade_id and venue are derived automatically from the matched ledger
    row (informational only — row_fp remains the sole resolution key, see
    core/account_resolver.py), so callers never risk them disagreeing with
    the actual row.

    A second, later assignment for the SAME row_fp is explicitly allowed
    (a revision — resolved by latest effective_at, tiebreak created_at,
    see core/account_resolver.py) — this is not an error.
    """
    if not effective_account or not effective_account.strip():
        raise ValueError("effective_account nesmí být prázdný")
    if not isinstance(effective_at, datetime):
        raise ValueError(f"effective_at musí být datetime, je {type(effective_at)}")

    ledger_store = LedgerStore(db_path)
    try:
        rows = ledger_store.timeline()
    finally:
        ledger_store.close()

    matches = [r for r in rows if r.fingerprint() == row_fp]
    if not matches:
        raise ValueError(
            f"row_fp={row_fp!r} neodpovídá žádnému řádku v ledgeru — "
            "assignment nelze vytvořit pro neexistující řádek."
        )
    matched_row = matches[0]

    assignment = AccountAssignment(
        id=str(uuid.uuid4()),
        row_fp=row_fp,
        trade_id=matched_row.id or "",
        venue=matched_row.venue,
        effective_account=effective_account.strip(),
        effective_at=effective_at,
        created_at=datetime.now(),
        source=source,
        note=note,
    )

    store = AccountAssignmentStore(db_path)
    try:
        store.add_assignment(assignment)
    finally:
        store.close()

    return assignment
