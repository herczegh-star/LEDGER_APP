"""BUY_COST_CORRECTION service: append-only, narrowly-validated writer.

The ONLY supported way to write a BUY_COST_CORRECTION group to the ledger.
Builds the 2-row group via core/buy_cost_correction.py, validates it
structurally BEFORE touching the database, verifies correction_of
references an existing trade group that contains a BUY leg of the SAME
asset (the type's defining invariant — see core/buy_cost_correction.py
module docstring: this is NOT a general cost adjustment mechanism), resolves
an ordering-safe effective timestamp, then appends both rows dedup-safe via
LedgerStore.insert_pair().

Does not compute anything beyond what core/buy_cost_correction.py already
does — no WAC, no cost basis math lives here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import FrozenSet, List, Optional

from core.buy_cost_correction import (
    build_buy_cost_correction_rows,
    is_fully_open_since,
    resolve_historical_effective_timestamp,
    validate_buy_cost_correction_group,
)
from core.ledger_store import LedgerStore
from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


def add_buy_cost_correction(
    db_path: str,
    correction_of: str,
    asset: str,
    currency: str,
    cash_delta: Decimal,
    venue: str,
    reason: str,
    original_value: Decimal,
    corrected_value: Decimal,
    account: Optional[str] = None,
    mode: str = "historical",
    current_timestamp: Optional[datetime] = None,
    epsilon: timedelta = timedelta(microseconds=1),
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> List[RawRow]:
    """Build, validate, and append one BUY_COST_CORRECTION group.

    mode:
      "historical" (default, general case) — effective timestamp is
          resolve_historical_effective_timestamp(original_buy.timestamp),
          i.e. original BUY timestamp + epsilon, ONLY after verifying no
          other position-affecting row for *asset* occupies that interval.
          Correct for ANY position state (open, partially sold, closed) —
          see core/buy_cost_correction.py docstrings.
      "current_time" — effective timestamp is `current_timestamp` (defaults
          to datetime.now()). ONLY valid when is_fully_open_since(asset,
          original_buy.timestamp) is True (no SELL/TRANSFER/etc. of *asset*
          since the original BUY) — raises ValueError otherwise. Must be
          explicitly requested; never silently substituted for "historical".

    Raises ValueError (before any DB write) when:
      - the group is structurally invalid, or
      - correction_of does not reference an existing trade group, or
      - that trade group has no BUY leg of the given asset (the type's
        defining invariant — this is not a general correction mechanism), or
      - mode="current_time" but the position is not fully open since the
        original BUY, or
      - mode="historical" and no safe effective timestamp can be proven.

    Returns the two inserted RawRow objects (marker, cash_leg).
    """
    if mode not in ("historical", "current_time"):
        raise ValueError(f"mode must be 'historical' or 'current_time', got: {mode!r}")

    store = LedgerStore(db_path)
    try:
        rows = store.timeline()

        asset_uc = asset.upper().strip()
        original_buy_rows = [
            r for r in rows
            if r.id == correction_of and r.type == "BUY" and r.asset.upper() == asset_uc
        ]
        if not original_buy_rows:
            raise ValueError(
                f"correction_of={correction_of!r} does not reference an existing "
                f"trade group containing a BUY leg of asset {asset_uc!r}. "
                "BUY_COST_CORRECTION may only correct the cost of an actual "
                "historical BUY — not a general cost_basis adjustment."
            )
        original_timestamp = original_buy_rows[0].timestamp

        if mode == "current_time":
            if not is_fully_open_since(rows, asset_uc, original_timestamp):
                raise ValueError(
                    f"mode='current_time' requires {asset_uc} to be fully open "
                    "(no SELL/TRANSFER/etc.) since the original BUY "
                    f"({original_timestamp.isoformat()}) — that does not hold here. "
                    "Use mode='historical' instead, which is correct for any "
                    "position state."
                )
            effective_timestamp = current_timestamp or datetime.now()
        else:
            effective_timestamp = resolve_historical_effective_timestamp(
                rows, asset_uc, original_timestamp, epsilon=epsilon,
            )

        marker, cash_leg = build_buy_cost_correction_rows(
            timestamp=effective_timestamp,
            correction_of=correction_of,
            asset=asset_uc,
            currency=currency,
            cash_delta=cash_delta,
            venue=venue,
            reason=reason,
            original_value=original_value,
            corrected_value=corrected_value,
            account=account,
        )

        ok, errs = validate_buy_cost_correction_group([marker, cash_leg], fiat)
        if not ok:
            raise ValueError("Invalid BUY_COST_CORRECTION group: " + "; ".join(errs))

        # Same dedup-collision risk as CORRECTION's marker leg (Phase 3C
        # finding): use insert_pair() and require BOTH legs to succeed.
        ok_marker, ok_cash = store.insert_pair(marker, cash_leg)
        if not (ok_marker and ok_cash):
            raise ValueError(
                "BUY_COST_CORRECTION was NOT fully written — "
                f"marker leg inserted={ok_marker}, cash leg inserted={ok_cash}. "
                "This usually means a row with an identical "
                "(timestamp, type, venue, asset, currency, amount) shape "
                "already exists. Retry with a distinct timestamp/epsilon if "
                "this is a genuinely different correction."
            )
    finally:
        store.close()

    return [marker, cash_leg]
