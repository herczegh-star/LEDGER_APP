"""Cash account roles: pure classification logic. No I/O, no ledger access.

Distinguishes what a tracked cash account IS FOR, separately from the
ledger's own account string:

    PERSONAL          - ordinary personal account; outside Portfolio Cash
                         Reserve and outside Total Managed Value.
    INVESTMENT_CASH    - capital physically set aside for investing /
                         realized proceeds; part of Portfolio Cash Reserve;
                         part of Total Managed Value once reconciliation
                         readiness allows it.
    LEGACY_UNASSIGNED  - account IS NULL. Never stored as a role row --
                         implicit, always derived from row.account is None.

Role is metadata about an (venue, account, currency) triple, kept in a
SEPARATE table (core/account_role_store.py) — it never touches `ledger`
and never affects compute_positions() / WAC / cost basis / realized PnL.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

PERSONAL = "PERSONAL"
INVESTMENT_CASH = "INVESTMENT_CASH"
LEGACY_UNASSIGNED = "LEGACY_UNASSIGNED"


def resolve_role(
    venue: str,
    account: Optional[str],
    currency: str,
    roles: List["AccountRole"],  # noqa: F821 - see core.account_role_store
    as_of: Optional[datetime] = None,
) -> Optional[str]:
    """Resolve the role in effect for (venue, account, currency) at as_of.

    account is None -> always LEGACY_UNASSIGNED, without consulting `roles`
    at all (account=NULL rows never get a role row — see module docstring).

    Otherwise: among role rows for this exact key with effective_at <=
    as_of, pick the one with the latest effective_at (tiebreak: latest
    created_at) — a later role decision never silently reinterprets a
    report generated with an earlier as_of.

    Returns None (fail closed) when no role has been declared for this key
    at this point in time — callers must treat None as "not
    INVESTMENT_CASH", never assume a default.
    """
    if account is None:
        return LEGACY_UNASSIGNED

    as_of = as_of or datetime.now()
    venue_norm = venue.lower()
    currency_uc = currency.upper()

    candidates = [
        r for r in roles
        if r.venue == venue_norm and r.account == account and r.currency == currency_uc
        and r.effective_at <= as_of
    ]
    if not candidates:
        return None

    best = max(candidates, key=lambda r: (r.effective_at, r.created_at))
    return best.role
