"""Reconciliation service: append-only writer + read orchestration for
cash_reconciliation_snapshots.

add_reconciliation_snapshot() is the ONLY supported way to write a
reconciliation snapshot. It validates input, then appends via
ReconciliationStore — it NEVER touches the `ledger` table, NEVER builds a
RawRow, and NEVER creates a TRANSFER/DEPOSIT. A reconciliation snapshot is a
diagnostic fact about the real world, not an accounting transaction (see
core/reconciliation.py's module docstring).

The read functions here combine LedgerStore (for calculated balances) and
ReconciliationStore (for reported balances) — both read-only.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from core.ledger_store import LedgerStore
from core.reconciliation import MATCH, compute_status, get_tolerance
from core.reconciliation_store import ReconciliationSnapshot, ReconciliationStore
from core.reports.cash import EXTERNAL_VENUE, compute_cash_balance_as_of, compute_cash_balances


def add_reconciliation_snapshot(
    db_path: str,
    venue: str,
    account: str,
    currency: str,
    reported_balance: Decimal,
    as_of: datetime,
    note: Optional[str] = None,
) -> ReconciliationSnapshot:
    """Validate and append one reconciliation snapshot.

    Raises ValueError (before any DB write) for invalid input:
      - venue / account / currency empty
      - currency has no configured reconciliation tolerance
      - reported_balance not a Decimal
      - as_of not a datetime

    Writes ONLY to cash_reconciliation_snapshots — never to `ledger`.
    """
    if not venue or not venue.strip():
        raise ValueError("venue nesmí být prázdné")
    if not account or not account.strip():
        raise ValueError(
            "account nesmí být prázdný — reconciliation je podporována pouze "
            "pro tracked cash účty (account IS NOT NULL)."
        )
    if not currency or not currency.strip():
        raise ValueError("currency nesmí být prázdná")
    currency_uc = currency.upper().strip()
    get_tolerance(currency_uc)  # raises for an unsupported currency, before any write

    if not isinstance(reported_balance, Decimal):
        raise ValueError(f"reported_balance musí být Decimal, je {type(reported_balance)}")
    if not isinstance(as_of, datetime):
        raise ValueError(f"as_of musí být datetime, je {type(as_of)}")

    snapshot = ReconciliationSnapshot(
        id=str(uuid.uuid4()),
        venue=venue.lower().strip(),
        account=account.strip(),
        currency=currency_uc,
        reported_balance=reported_balance,
        as_of=as_of,
        created_at=datetime.now(),
        note=note,
    )

    store = ReconciliationStore(db_path)
    try:
        store.add_snapshot(snapshot)
    finally:
        store.close()

    return snapshot


@dataclass
class ReconciliationHistoryRow:
    """One reconciliation snapshot, enriched with its LIVE-computed
    calculated balance / difference / status — none of which are ever
    stored, only the reported_balance fact is persisted."""

    as_of: datetime
    calculated_balance: Decimal
    reported_balance: Decimal
    difference: Decimal
    status: str
    created_at: datetime
    note: Optional[str]


def get_reconciliation_history(
    db_path: str,
    venue: str,
    account: str,
    currency: str,
    now: Optional[datetime] = None,
    assignments: Optional[Dict[str, List]] = None,
) -> List[ReconciliationHistoryRow]:
    """Read-only: all reconciliation snapshots for one (venue, account,
    currency), oldest as_of first, each enriched with a freshly-computed
    calculated_balance/difference/status.

    assignments: optional historical_account_assignments overlay (see
    core.account_resolver.resolve_effective_account). MUST be the SAME
    overlay used by whatever else computes this account's balance (e.g. a
    Cash screen) — passing a different overlay (or omitting it when the
    Cash screen uses one) would let this figure silently diverge from that
    other view. Omitted (default): identical behaviour to before this
    parameter existed (row.account used directly, no overlay).
    """
    now = now or datetime.now()
    venue_norm = venue.lower().strip()
    account_norm = account.strip()
    currency_uc = currency.upper().strip()

    ledger_store = LedgerStore(db_path)
    try:
        rows = ledger_store.timeline()
    finally:
        ledger_store.close()

    recon_store = ReconciliationStore(db_path)
    try:
        snapshots = recon_store.get_snapshots(venue_norm, account_norm, currency_uc)
    finally:
        recon_store.close()

    history: List[ReconciliationHistoryRow] = []
    for snap in snapshots:
        calculated = compute_cash_balance_as_of(
            rows, venue_norm, account_norm, currency_uc, snap.as_of, assignments=assignments,
        )
        difference = snap.reported_balance - calculated
        status = compute_status(snap.reported_balance, calculated, snap.as_of, now, currency_uc)
        history.append(ReconciliationHistoryRow(
            as_of=snap.as_of,
            calculated_balance=calculated,
            reported_balance=snap.reported_balance,
            difference=difference,
            status=status,
            created_at=snap.created_at,
            note=snap.note,
        ))
    return history


def is_cash_account_reconciled(
    db_path: str,
    venue: str,
    account: str,
    currency: str,
    now: Optional[datetime] = None,
    assignments: Optional[Dict[str, List]] = None,
) -> bool:
    """True iff the MOST RECENT (by as_of) reconciliation snapshot for this
    account has status MATCH (implies not STALE — STALE is a distinct
    status value). False if no snapshot exists at all.

    assignments: same overlay-consistency requirement as
    get_reconciliation_history() — see its docstring.
    """
    history = get_reconciliation_history(db_path, venue, account, currency, now=now, assignments=assignments)
    if not history:
        return False
    latest = history[-1]
    return latest.status == MATCH


@dataclass
class ReadinessResult:
    """Result of cash_reconciliation_readiness() — state + human-readable
    reason, never a side effect. Does NOT flip Total Managed Value."""

    ready: bool
    reason: str
    accounts_checked: int
    accounts_not_ready: List[Tuple[str, str, str]]  # (venue, account, currency)


def cash_reconciliation_readiness(
    db_path: str,
    now: Optional[datetime] = None,
    roles: Optional[List] = None,
    assignments: Optional[Dict[str, List]] = None,
) -> ReadinessResult:
    """Read-only: is EVERY relevant tracked cash account currently
    reconciled (latest snapshot MATCH, not stale)?

    roles=None (default): IDENTICAL behaviour to before this parameter
        existed — every tracked account (account IS NOT NULL, venue !=
        'external') must be reconciled. This is the coarse, pre-account-role
        semantics; kept as the default for backward compatibility.
    roles=<list of AccountRole>: role-aware — ONLY accounts whose role
        resolves to INVESTMENT_CASH are required. PERSONAL-role accounts
        and accounts with no declared role are excluded entirely (neither
        required nor counted) — this is the correct semantics for Total
        Managed Value readiness (section 9 of the Model B decision).

    assignments: optional historical_account_assignments overlay — MUST be
        the SAME overlay used everywhere else this account's balance is
        computed (e.g. a Cash screen), or readiness could pass/fail based
        on a different account grouping than what the user is looking at.
        Threaded through to both the tracked_keys computation AND each
        account's is_cash_account_reconciled() check, so the whole
        function is internally consistent. Omitted (default): identical
        behaviour to before this parameter existed.

    Legacy/Unassigned Cash (account IS NULL) is never checked either way —
    already excluded from Cash Reserve by design (see
    core.reports.cash.compute_tracked_cash_reserve()).

    This function ONLY reports readiness state + reason. It never enables
    Total Managed Value or any other feature flag.
    """
    now = now or datetime.now()

    ledger_store = LedgerStore(db_path)
    try:
        rows = ledger_store.timeline()
    finally:
        ledger_store.close()

    balances = compute_cash_balances(rows, assignments=assignments, as_of=now)

    if roles is None:
        tracked_keys: List[Tuple[str, str, str]] = [
            (venue, account, currency)
            for (venue, account, currency) in balances
            if venue != EXTERNAL_VENUE and account is not None
        ]
    else:
        from core.account_role import INVESTMENT_CASH, resolve_role
        tracked_keys = [
            (venue, account, currency)
            for (venue, account, currency) in balances
            if venue != EXTERNAL_VENUE and account is not None
            and resolve_role(venue, account, currency, roles, now) == INVESTMENT_CASH
        ]

    if not tracked_keys:
        reason = (
            "No tracked cash accounts exist yet." if roles is None else
            "No INVESTMENT_CASH-role accounts exist yet."
        )
        return ReadinessResult(
            ready=False, reason=reason,
            accounts_checked=0, accounts_not_ready=[],
        )

    not_ready = [
        key for key in tracked_keys
        if not is_cash_account_reconciled(db_path, *key, now=now, assignments=assignments)
    ]

    if not_ready:
        return ReadinessResult(
            ready=False,
            reason=(
                f"{len(not_ready)} of {len(tracked_keys)} tracked cash account(s) "
                "are not reconciled (need a fresh MATCH snapshot)."
            ),
            accounts_checked=len(tracked_keys),
            accounts_not_ready=sorted(not_ready),
        )

    return ReadinessResult(
        ready=True,
        reason="All tracked cash accounts are reconciled (MATCH, not stale).",
        accounts_checked=len(tracked_keys),
        accounts_not_ready=[],
    )
