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
        TRANSFER (inflow, amount > 0):
            destination venue: quantity += row.amount  (explicit ledger row)
        FEE (crypto):
            quantity += row.amount  (reduces balance; fiat FEE ignored)
        Fiat assets:
            ignored entirely

    Two-row TRANSFER model:
        Every TRANSFER is stored as two explicit DB rows sharing the same id:
            outflow: amount < 0, venue = source
            inflow:  amount > 0, venue = destination
        holdings reflects only what is in the ledger — no inference from note.
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

        # Skip fiat assets entirely
        if asset in fiat_set:
            continue

        # Crypto FEE (e.g. on-chain withdrawal fee in BTC) reduces the balance
        if row.type == "FEE":
            _add(venue, asset, row.amount)
            continue

        if row.type not in _HOLDING_TYPES:
            continue

        # TRANSFER rows (outflow and inflow) are explicit DB rows — use amount as-is.
        # Note-based synthesis is intentionally removed: holdings reflects only
        # actual ledger rows, never inferred from note content.
        _add(venue, asset, row.amount)

    # Prune zero-quantity entries (closed / fully-transferred out)
    result: Dict[str, Dict[str, Decimal]] = {}
    for venue, assets in holdings.items():
        non_zero = {a: q for a, q in assets.items() if q != Decimal("0")}
        if non_zero:
            result[venue] = non_zero

    return result
