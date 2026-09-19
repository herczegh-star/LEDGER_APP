"""Account role service: append-only writer for cash_account_roles.

The ONLY supported way to write a role. Validates BEFORE any DB write —
AccountRoleStore.add_role() itself performs no validation beyond schema
presence, so this service is what actually enforces the role invariants
(PERSONAL/INVESTMENT_CASH only, non-empty key fields, valid timestamps).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from core.account_role import INVESTMENT_CASH, PERSONAL
from core.account_role_store import AccountRole, AccountRoleStore

_VALID_ROLES = frozenset({PERSONAL, INVESTMENT_CASH})


def add_account_role(
    db_path: str,
    venue: str,
    account: str,
    currency: str,
    role: str,
    effective_at: datetime,
    source: Optional[str] = None,
    note: Optional[str] = None,
) -> AccountRole:
    """Validate and append one account role declaration.

    Raises ValueError (before any DB write) when:
      - venue / account / currency empty
      - account is None (LEGACY_UNASSIGNED is implicit, never a stored row
        — see core/account_role.py's module docstring)
      - role not in {PERSONAL, INVESTMENT_CASH} — LEGACY_UNASSIGNED and any
        other string are explicitly rejected; this table only ever stores
        the two roles a human can meaningfully declare
      - effective_at / (given) source note not a datetime

    created_at is always datetime.now() — the actual write moment — never
    caller-supplied, mirroring imported_at / BUY_COST_CORRECTION's
    timestamp-vs-audit split.
    """
    if not venue or not venue.strip():
        raise ValueError("venue nesmí být prázdné")
    if not account or not account.strip():
        raise ValueError(
            "account nesmí být prázdný — LEGACY_UNASSIGNED (account IS NULL) "
            "se nikdy neukládá jako role row."
        )
    if not currency or not currency.strip():
        raise ValueError("currency nesmí být prázdná")
    if role not in _VALID_ROLES:
        raise ValueError(
            f"role musí být jedna z {sorted(_VALID_ROLES)}, je {role!r} "
            "(LEGACY_UNASSIGNED je implicitní a nikdy se neukládá)."
        )
    if not isinstance(effective_at, datetime):
        raise ValueError(f"effective_at musí být datetime, je {type(effective_at)}")

    account_role = AccountRole(
        id=str(uuid.uuid4()),
        venue=venue.lower().strip(),
        account=account.strip(),
        currency=currency.upper().strip(),
        role=role,
        effective_at=effective_at,
        created_at=datetime.now(),
        source=source,
        note=note,
    )

    store = AccountRoleStore(db_path)
    try:
        store.add_role(account_role)
    finally:
        store.close()

    return account_role
