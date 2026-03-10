"""Venue-level physical holdings engine.

Tracks the *physical* quantity of each asset in each venue, taking TRANSFER
rows into account.  This is intentionally separate from the WAC engine
(core/reports/positions.py) which ignores TRANSFER by design.

Key differences from compute_positions():
  - TRANSFER rows ARE processed (+ inflow, - outflow)
  - No cost_basis, WAC, or realized_pnl — only quantities
  - Result reflects where assets physically reside, not where they were bought

Usage:
    from core.reports.holdings import compute_venue_holdings
    rows = svc.timeline()
    holdings = compute_venue_holdings(rows)
    # holdings["kraken"]["BTC"] == Decimal("0.5")
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set

from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})

# Row types that move asset quantity into/out of a venue
_HOLDING_TYPES = frozenset({"BUY", "SELL", "REVERSAL", "STAKING", "TRANSFER"})


def compute_venue_holdings(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Decimal]]:
    """Compute physical asset quantities per venue.

    Args:
        rows: All ledger rows (typically from svc.timeline()).
        fiat: Fiat asset set (default: {"EUR", "CZK"}).
              Fiat assets are excluded from results.

    Returns:
        Nested dict: { venue: { asset: physical_quantity } }.
        Only (venue, asset) pairs with non-zero quantity are included.

    Processing rules:
        BUY / SELL / REVERSAL / STAKING:
            quantity += row.amount  (same sign semantics as WAC engine)
        TRANSFER (outflow, amount < 0):
            source venue: quantity += row.amount  (negative = reduce)
            if row.note is a plain venue name → destination venue: quantity += abs(row.amount)
        TRANSFER (inflow, amount > 0):
            quantity += row.amount  (explicit inflow row, no note needed)
        FEE:
            ignored (fee does not change asset holdings position)
        Fiat assets:
            ignored entirely

    Single-entry TRANSFER convention:
        One row records the outflow (amount < 0, venue = source, note = destination).
        compute_venue_holdings() synthesises the matching inflow automatically so
        the destination venue appears in holdings without a second DB row.
        A note qualifies as a destination venue when it is non-empty and contains
        no spaces (i.e. looks like a plain venue identifier, not a sentence).
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)

    # Deterministic processing order
    sorted_rows = sorted(rows, key=lambda r: (r.timestamp, r.id or ""))

    holdings: Dict[str, Dict[str, Decimal]] = {}

    def _add(venue: str, asset: str, amount: Decimal) -> None:
        if venue not in holdings:
            holdings[venue] = {}
        if asset not in holdings[venue]:
            holdings[venue][asset] = Decimal("0")
        holdings[venue][asset] += amount

    for row in sorted_rows:
        asset = row.asset.upper()
        venue = row.venue.lower()

        # Skip fiat and fees — neither changes crypto holdings
        if asset in fiat_set or row.type == "FEE":
            continue

        if row.type not in _HOLDING_TYPES:
            continue

        if row.type == "TRANSFER" and row.amount < Decimal("0"):
            # Outflow from source venue
            _add(venue, asset, row.amount)
            # Synthesise inflow to destination when note looks like a venue name
            note = (row.note or "").strip()
            if note and " " not in note:
                dest = note.lower()
                _add(dest, asset, abs(row.amount))
        else:
            _add(venue, asset, row.amount)

    # Prune zero-quantity entries (closed / fully-transferred out)
    result: Dict[str, Dict[str, Decimal]] = {}
    for venue, assets in holdings.items():
        non_zero = {a: q for a, q in assets.items() if q != Decimal("0")}
        if non_zero:
            result[venue] = non_zero

    return result
