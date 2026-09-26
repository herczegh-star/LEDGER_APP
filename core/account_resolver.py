"""Central account resolver: the ONLY place cash reports should consult a
historical account assignment overlay. No I/O, no ledger access — pure
function over data already loaded.

Never touches compute_positions() / WAC / cost basis / realized PnL —
`account` has never affected those (see core/services/trade_service.py's
own account propagation rules), and this resolver only changes what
ACCOUNT a row is attributed to for cash reporting, never its amount/type/
asset/timestamp.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from core.model import RawRow


def resolve_effective_account(
    row: RawRow,
    assignments: Optional[Dict[str, List["AccountAssignment"]]] = None,  # noqa: F821
    as_of: Optional[datetime] = None,
) -> Optional[str]:
    """Resolve the account a row should be attributed to for cash reporting.

    Fallback (no assignments, or none matching): row.account, unchanged —
    this is what every cash report already returns today.

    Overlay: if assignments contains an entry for row.fingerprint() with at
    least one entry whose effective_at <= as_of, returns the
    latest-effective_at (tiebreak latest created_at) effective_account.

    Never mutates row. Never used by compute_positions()/WAC/cost
    basis/realized PnL — cash-reporting only.
    """
    if not assignments:
        return row.account

    history = assignments.get(row.fingerprint())
    if not history:
        return row.account

    as_of = as_of or datetime.now()
    valid = [a for a in history if a.effective_at <= as_of]
    if not valid:
        return row.account

    best = max(valid, key=lambda a: (a.effective_at, a.created_at))
    return best.effective_account
