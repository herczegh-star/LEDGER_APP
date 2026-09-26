"""Cash reconciliation: pure status/tolerance logic. No I/O, no ledger access.

Compares a user-reported real account balance against the ledger's
CALCULATED balance for a (venue, account, currency) — a read-only
DIAGNOSTIC layer, never an accounting transaction. It never changes
cost_basis, realized_pnl, quantity, or the cash ledger balance, and it
never creates a TRANSFER/DEPOSIT row. See core/reconciliation_store.py for
persistence (a table completely separate from `ledger`) and
core/services/reconciliation_service.py for orchestration.

A Difference is NOT PnL — it is an unexplained gap between what the ledger
thinks and what the real account reports, to be investigated by a human
(missing deposit/withdrawal/fee, wrong account assignment, wrong BUY/SELL
cash impact, ...). Nothing here auto-corrects anything.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Optional

MATCH = "MATCH"
DIFFERENCE = "DIFFERENCE"
STALE = "STALE"

# Currency-aware tolerance map. A currency with no entry here has NO safe
# default — get_tolerance() raises rather than silently applying an
# inappropriate value for an unmapped currency.
_DEFAULT_TOLERANCE_BY_CURRENCY: Dict[str, Decimal] = {
    "CZK": Decimal("1.00"),
    "EUR": Decimal("0.01"),
}

_DEFAULT_STALE_AFTER = timedelta(days=30)


def get_tolerance(
    currency: str,
    tolerance_map: Optional[Dict[str, Decimal]] = None,
) -> Decimal:
    """Return the reconciliation tolerance for *currency*.

    Raises ValueError for a currency with no configured tolerance — never
    silently falls back to a guessed value for an unmapped currency.
    """
    m = tolerance_map if tolerance_map is not None else _DEFAULT_TOLERANCE_BY_CURRENCY
    currency_uc = currency.upper().strip()
    if currency_uc not in m:
        raise ValueError(
            f"No reconciliation tolerance configured for currency {currency_uc!r}. "
            f"Known: {sorted(m)}. Pass an explicit tolerance_map to add support "
            "for this currency rather than guessing a value."
        )
    return m[currency_uc]


def compute_status(
    reported_balance: Decimal,
    calculated_balance: Decimal,
    as_of: datetime,
    now: datetime,
    currency: str,
    tolerance_map: Optional[Dict[str, Decimal]] = None,
    stale_after: timedelta = _DEFAULT_STALE_AFTER,
) -> str:
    """Return MATCH, DIFFERENCE, or STALE for one reconciliation snapshot.

    STALE takes precedence over MATCH/DIFFERENCE: a snapshot whose `as_of`
    is older than `stale_after` relative to `now` is no longer trustworthy
    as a CURRENT statement about the account, even if the numbers happen to
    agree — so it is reported as STALE rather than MATCH.

    Raises ValueError (via get_tolerance) if `currency` has no configured
    tolerance — never silently guesses.
    """
    if now - as_of > stale_after:
        return STALE
    tolerance = get_tolerance(currency, tolerance_map)
    difference = reported_balance - calculated_balance
    if abs(difference) <= tolerance:
        return MATCH
    return DIFFERENCE
