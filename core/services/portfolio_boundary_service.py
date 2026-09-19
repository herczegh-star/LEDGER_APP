"""Portfolio boundary service: append-only writer for PORTFOLIO_CONTRIBUTION
/ PORTFOLIO_WITHDRAWAL groups.

The ONLY supported way to write these types. Builds the 2-row group via
core/portfolio_boundary.py, validates structurally AND directionally
(sign matches the declared type, given which side is INVESTMENT_CASH —
see validate_portfolio_boundary_direction()'s docstring for why this is a
separate check from the structural one), optionally cross-checks account
roles (reject when BOTH sides already resolve to INVESTMENT_CASH — that
should be a plain TRANSFER, not a boundary crossing), then appends
dedup-safe via LedgerStore.insert_pair(). Never generates a
synthetic/inferred event — every call represents something the CALLER
explicitly asserts happened.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.portfolio_boundary import (
    CONTRIBUTION,
    WITHDRAWAL,
    build_portfolio_boundary_rows,
    validate_portfolio_boundary_direction,
    validate_portfolio_boundary_group,
)


def _add_boundary_event(
    boundary_type: str,
    db_path: str,
    timestamp: datetime,
    asset: str,
    amount: Decimal,
    outside_venue: str,
    inside_venue: str,
    inside_account: str,
    outside_account: Optional[str] = None,
    note: Optional[str] = None,
    roles: Optional[List] = None,
) -> List[RawRow]:
    row_outside, row_inside = build_portfolio_boundary_rows(
        boundary_type=boundary_type, timestamp=timestamp, asset=asset, amount=amount,
        outside_venue=outside_venue, outside_account=outside_account,
        inside_venue=inside_venue, inside_account=inside_account, note=note,
    )
    ok, errs = validate_portfolio_boundary_group([row_outside, row_inside])
    if not ok:
        raise ValueError(f"Invalid {boundary_type} group: " + "; ".join(errs))

    ok_dir, dir_errs = validate_portfolio_boundary_direction(
        [row_outside, row_inside], boundary_type, inside_venue, inside_account,
    )
    if not ok_dir:
        raise ValueError(f"Invalid {boundary_type} direction: " + "; ".join(dir_errs))

    if roles is not None and outside_account is not None:
        from core.account_role import INVESTMENT_CASH, resolve_role
        outside_role = resolve_role(outside_venue, outside_account, asset, roles, timestamp)
        if outside_role == INVESTMENT_CASH:
            raise ValueError(
                f"Both sides resolve to INVESTMENT_CASH (outside: {outside_venue}/"
                f"{outside_account}, inside: {inside_venue}/{inside_account}) — "
                f"this should be a plain TRANSFER, not a {boundary_type} "
                "(see core/portfolio_boundary.py module docstring)."
            )

    store = LedgerStore(db_path)
    try:
        ok_outside, ok_inside = store.insert_pair(row_outside, row_inside)
        if not (ok_outside and ok_inside):
            raise ValueError(
                f"{boundary_type} was NOT fully written — "
                f"outside leg inserted={ok_outside}, inside leg inserted={ok_inside}. "
                "Retry with a distinct timestamp if this is a genuinely different event."
            )
    finally:
        store.close()

    return [row_outside, row_inside]


def add_portfolio_contribution(
    db_path: str,
    timestamp: datetime,
    asset: str,
    amount: Decimal,
    outside_venue: str,
    inside_venue: str,
    inside_account: str,
    outside_account: Optional[str] = None,
    note: Optional[str] = None,
    roles: Optional[List] = None,
) -> List[RawRow]:
    """Capital enters the tracked portfolio scope: outside_venue/account
    loses `amount`, inside_venue/inside_account (INVESTMENT_CASH) gains it.
    outside_account may be None only when outside_venue == 'external'.

    roles: optional list[AccountRole] — when provided AND outside_account
    is set, rejects if the outside account ALSO already resolves to
    INVESTMENT_CASH (that combination should be a TRANSFER instead).
    """
    return _add_boundary_event(
        CONTRIBUTION, db_path, timestamp, asset, amount,
        outside_venue, inside_venue, inside_account, outside_account, note, roles,
    )


def add_portfolio_withdrawal(
    db_path: str,
    timestamp: datetime,
    asset: str,
    amount: Decimal,
    outside_venue: str,
    inside_venue: str,
    inside_account: str,
    outside_account: Optional[str] = None,
    note: Optional[str] = None,
    roles: Optional[List] = None,
) -> List[RawRow]:
    """Capital leaves the tracked portfolio scope: inside_venue/inside_account
    (INVESTMENT_CASH) loses `amount`, outside_venue/account gains it.

    roles: same optional cross-check as add_portfolio_contribution().
    """
    return _add_boundary_event(
        WITHDRAWAL, db_path, timestamp, asset, amount,
        outside_venue, inside_venue, inside_account, outside_account, note, roles,
    )
