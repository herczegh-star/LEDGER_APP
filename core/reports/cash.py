"""Cash balance report: fiat balances per (venue, account, currency).

Pure read-only projection over ledger rows. Sums signed fiat amounts only —
no WAC, no cost basis, no realized/unrealized PnL, no live pricing, no FX
conversion. Native-currency balances only.

Does NOT touch compute_positions() / compute_venue_holdings() — this module
reads the same RawRow stream but only the fiat legs those functions already
skip. Adding this module does not change any existing crypto-side numbers.

Group key is (venue, account, currency) — NOT (venue, currency). Legacy rows
(account is None) are grouped under account=None; this module never invents
a display label such as "Unassigned Cash" — that belongs in the presentation
layer, not here.

`venue == "external"` is a boundary pseudo-venue representing capital that
has left or not yet entered the tracked portfolio (deposits/withdrawals).
compute_cash_balances() includes it like any other venue (it is real ledger
data); compute_portfolio_cash_reserve() explicitly excludes it, and
compute_external_flows() reads only it.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})

EXTERNAL_VENUE = "external"

CashKey = Tuple[str, Optional[str], str]  # (venue, account, currency)


def compute_cash_balances(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
    assignments: Optional[Dict[str, List]] = None,
    as_of: Optional[datetime] = None,
) -> Dict[CashKey, Decimal]:
    """Compute fiat cash balances grouped by (venue, account, currency).

    Args:
        rows: All ledger rows (typically from svc.timeline()).
        fiat: Fiat asset set (default: {"EUR", "CZK"}).
        assignments: Optional historical_account_assignments overlay (see
            core.account_resolver.resolve_effective_account) — when
            provided, each row's grouping account is resolved through the
            overlay instead of using row.account directly. Omitted (the
            default): IDENTICAL behaviour to before this parameter existed.
        as_of: Point in time for overlay resolution (default: now). Ignored
            when assignments is None.

    Returns:
        Dict keyed by (venue, account, currency) -> signed balance.
        `account` is None for legacy/unassigned rows — never inferred.
        Keys with an exact-zero balance are omitted.
        Includes venue == "external" (real ledger data, not filtered here).
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)

    from core.account_resolver import resolve_effective_account

    balances: Dict[CashKey, Decimal] = {}
    for row in rows:
        asset = row.asset.upper()
        if asset not in fiat_set:
            continue
        effective_account = resolve_effective_account(row, assignments, as_of)
        key: CashKey = (row.venue.lower(), effective_account, asset)
        balances[key] = balances.get(key, Decimal("0")) + row.amount

    return {k: v for k, v in balances.items() if v != Decimal("0")}


def compute_cash_balance_as_of(
    rows: List[RawRow],
    venue: str,
    account: str,
    currency: str,
    as_of: datetime,
    fiat: Optional[Set[str]] = None,
    assignments: Optional[Dict[str, List]] = None,
) -> Decimal:
    """Calculated cash balance for ONE (venue, account, currency) triple, as
    of a specific point in time — sums only rows with timestamp <= as_of.

    Same tracked-cash semantics as compute_cash_balances(): a plain sum of
    signed fiat amounts for that exact key, with no filtering by row type
    (mirrors compute_cash_balances()'s existing type-agnostic behaviour).
    Purely additive — does not call or alter compute_cash_balances().

    assignments: optional overlay (see compute_cash_balances()) — resolved
    at `as_of` (the SAME as_of used for the balance calculation itself, so
    the overlay's own effective-dating stays consistent with the query).
    Omitted (default): identical behaviour to before this parameter existed.

    Used by the cash reconciliation layer (core/reconciliation.py) to
    compare a user-reported real balance against what the ledger implies
    AS OF the same date — never against today's live balance, which would
    be the wrong comparison if more rows were added since *as_of*.
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)
    currency_uc = currency.upper()
    venue_norm = venue.lower()

    from core.account_resolver import resolve_effective_account

    total = Decimal("0")
    for row in rows:
        if row.asset.upper() != currency_uc or currency_uc not in fiat_set:
            continue
        if row.venue.lower() != venue_norm:
            continue
        if row.timestamp > as_of:
            continue
        effective_account = resolve_effective_account(row, assignments, as_of)
        if effective_account != account:
            continue
        total += row.amount
    return total


def compute_tracked_cash_reserve(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
    assignments: Optional[Dict[str, List]] = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, Decimal]:
    """Total cash reserve per currency, from TRACKED accounts only.

    'Tracked' = account IS NOT NULL AND venue != 'external'.

    Why account=None is excluded (this is a correctness rule, not a
    formatting choice): the legacy ledger records fiat quote legs of BUY
    trades (outflows) going back years, but historically no corresponding
    starting balance / external deposit was ever entered for most venues.
    Summing those raw outflows produces a large NEGATIVE number (e.g.
    "-1 071 515 CZK" for a venue that simply never had its funding
    recorded) that looks like a real debt but isn't — it is untracked
    historical fiat flow, not a current cash balance. Presenting it as
    "Cash Reserve" would be a real accounting lie, not just noisy UI.

    Only rows carrying an explicit account (a user has actively identified
    a real cash account, e.g. "Revolut / Osobní CZK") are trustworthy
    enough to represent as a current balance — including when that
    tracked balance is itself negative (a real account CAN be genuinely
    overdrawn/negative; that is preserved here, only account=None rows
    are excluded).

    See compute_legacy_unassigned_flows() for the excluded data — kept
    available for diagnostics, never presented as a balance.
    """
    balances = compute_cash_balances(rows, fiat, assignments, as_of)
    totals: Dict[str, Decimal] = {}
    for (venue, account, currency), amount in balances.items():
        if venue == EXTERNAL_VENUE:
            continue
        if account is None:
            continue
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    return {k: v for k, v in totals.items() if v != Decimal("0")}


def compute_portfolio_cash_reserve(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> Dict[str, Decimal]:
    """Alias for compute_tracked_cash_reserve() — same tracked-only
    semantics. Kept as a separate name for facade-boundary stability;
    prefer compute_tracked_cash_reserve() in new code for clarity."""
    return compute_tracked_cash_reserve(rows, fiat)


def compute_legacy_unassigned_flows(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> Dict[CashKey, Decimal]:
    """Historical fiat flow recorded without a known cash account
    (account IS NULL), excluding 'external'.

    NOT a current cash balance — diagnostic/historical view only. Never
    included in compute_tracked_cash_reserve() / compute_portfolio_cash_reserve().
    Never auto-assigned an account; account stays None here, always.
    """
    balances = compute_cash_balances(rows, fiat)
    return {
        k: v for k, v in balances.items()
        if k[0] != EXTERNAL_VENUE and k[1] is None
    }


def list_known_accounts(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> List[Tuple[str, str, str]]:
    """List distinct (venue, account, currency) triples with a real
    (non-None) account, excluding venue == 'external'.

    Used to populate account-picker suggestions in the UI. Never includes
    the legacy 'unassigned' bucket (account is None) — that has no name to
    suggest — and never offers 'external' as a selectable portfolio account.

    Returns a sorted list; callers wanting just account labels can dedupe
    the second tuple element themselves.
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)

    seen: set = set()
    for row in rows:
        asset = row.asset.upper()
        if asset not in fiat_set:
            continue
        venue = row.venue.lower()
        if venue == EXTERNAL_VENUE:
            continue
        account = (row.account or "").strip()
        if not account:
            continue
        seen.add((venue, account, asset))

    return sorted(seen)


def compute_external_flows(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Decimal]]:
    """Net external capital flow per currency, derived from venue == 'external'.

    A row recorded at venue == 'external' mirrors the TRANSFER two-row model:
        amount < 0  => money left 'external'   => external DEPOSIT into the portfolio
        amount > 0  => money entered 'external' => external WITHDRAWAL from the portfolio

    Returns:
        {currency: {"deposits": D, "withdrawals": W, "net_contributed": D - W}}
        D and W are always >= 0; net_contributed may be negative.
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)

    result: Dict[str, Dict[str, Decimal]] = {}
    for row in rows:
        if row.venue.lower() != EXTERNAL_VENUE:
            continue
        asset = row.asset.upper()
        if asset not in fiat_set:
            continue
        if asset not in result:
            result[asset] = {"deposits": Decimal("0"), "withdrawals": Decimal("0")}
        if row.amount < Decimal("0"):
            result[asset]["deposits"] += -row.amount
        elif row.amount > Decimal("0"):
            result[asset]["withdrawals"] += row.amount

    for currency, d in result.items():
        d["net_contributed"] = d["deposits"] - d["withdrawals"]

    return result


def compute_investment_cash_reserve(
    rows: List[RawRow],
    roles: List,
    fiat: Optional[Set[str]] = None,
    assignments: Optional[Dict[str, List]] = None,
    as_of: Optional[datetime] = None,
) -> Dict[str, Decimal]:
    """Total cash reserve per currency, from accounts whose role resolves to
    INVESTMENT_CASH ONLY — a NARROWER, role-aware sibling of
    compute_tracked_cash_reserve() (which uses the coarser "any non-None
    account" rule).

    PERSONAL-role accounts, accounts with no declared role (fail closed —
    see core.account_role.resolve_role), and the LEGACY_UNASSIGNED bucket
    (account IS NULL) are all excluded, exactly like
    compute_tracked_cash_reserve() excludes account IS NULL and 'external'.

    This is the correct source for Total Managed Value's cash component —
    compute_tracked_cash_reserve() must NOT be reused for that purpose
    without role filtering, since a PERSONAL account (e.g. a mixed bank
    account) is explicitly out of portfolio scope.
    """
    from core.account_role import INVESTMENT_CASH, resolve_role

    balances = compute_cash_balances(rows, fiat, assignments, as_of)
    totals: Dict[str, Decimal] = {}
    for (venue, account, currency), amount in balances.items():
        if venue == EXTERNAL_VENUE or account is None:
            continue
        role = resolve_role(venue, account, currency, roles, as_of)
        if role != INVESTMENT_CASH:
            continue
        totals[currency] = totals.get(currency, Decimal("0")) + amount
    return {k: v for k, v in totals.items() if v != Decimal("0")}
