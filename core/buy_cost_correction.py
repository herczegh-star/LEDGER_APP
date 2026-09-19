"""BUY_COST_CORRECTION: a narrowly-validated accounting type for fixing a
historical BUY's cost basis / cash attribution WITHOUT touching quantity or
realized PnL directly, and WITHOUT rewriting existing rows (append-only).

Distinct from core/correction.py's CORRECTION type, which adjusts
realized_pnl only (for a trade whose position has already been sold and
closed). BUY_COST_CORRECTION adjusts cost_basis only, for a BUY whose
recorded quote_amount/fee was wrong. WAC is never stored — it is always
cost_basis / quantity downstream, so it "corrects itself" automatically.

This type carries a strict, narrow invariant — it is NOT a general cost
adjustment mechanism:

    cash_delta            = the corrected cash leg amount, signed
    cost_basis_delta       = -cash_delta

This holds structurally for a BUY: cost_basis = quote_amount + fee and
cash_impact = -(quote_amount + fee) are computed from the exact same two
input numbers (core/reports/positions.py, core/services/trade_service.py),
so any error in either one moves cost_basis and cash by equal, opposite
amounts. A caller does not supply cost_basis_delta independently — it is
always derived from cash_delta to keep this invariant impossible to violate
by construction.

A valid BUY_COST_CORRECTION group is exactly two rows sharing one `id`:

    A) POSITION MARKER LEG
       type=BUY_COST_CORRECTION, asset=<non-fiat asset of the original BUY>,
       amount=0, currency=<fiat currency>, venue=<venue>, account=<usually
       None — quantity-neutral marker, not itself a cash movement>

    B) CASH DELTA LEG
       type=BUY_COST_CORRECTION, asset=<fiat currency>, amount=<cash_delta>,
       currency=<same fiat currency>, venue=<same venue>, account=<cash
       account this delta actually landed in>

Both legs carry identical correction metadata (encoded in `note` — see
build_buy_cost_correction_note() / parse_buy_cost_correction_note(), the
ONLY place that format is built or parsed).

`correction_of` MUST reference an existing trade group that contains a BUY
leg of the SAME asset — enforced by core/services/buy_cost_correction_service.py
(needs the full ledger) and cross-checked by health_service.py. This module
itself only validates row-shape, not cross-referential validity.

A wrong correction is corrected by a NEW BUY_COST_CORRECTION with the
opposite cash_delta — never by reversing with the standard REVERSAL
mechanism (see core/services/reversal_service.py's guard).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})
_NOTE_PREFIX = "BUY_COST_CORRECTION of"

# Row types whose timestamp participates in WAC/position ordering for a
# given asset — used by resolve_historical_effective_timestamp() /
# is_fully_open_since() to check for ordering conflicts. Mirrors
# core/reports/positions.py's _INVESTMENT_TYPES plus FEE (non-fiat FEE also
# mutates position state) and BUY_COST_CORRECTION itself (a second
# correction must not silently land inside another one's ordering gap).
#
# KNOWN OVER-CONSERVATIVE FALSE POSITIVE (tech debt, NOT fixed — see
# tests/test_buy_cost_correction_service.py's Sept-7-ARB-shaped scenario,
# and the real production Sept 7 correction where this forced a fallback
# from mode="current_time" to mode="historical"):
#
# is_fully_open_since() treats ANY later row of these types as blocking
# mode="current_time", but only SELL, TRANSFER/REVERSAL with amount<0, and
# non-fiat FEE with amount<0 actually READ the running WAC
# (wac_per_unit = cost_basis/quantity in core/reports/positions.py) and are
# therefore genuinely order-sensitive. BUY, STAKING, BUY_COST_CORRECTION,
# and TRANSFER/REVERSAL with amount>0 are purely ADDITIVE — they commute
# freely with each other regardless of relative order, so a later one of
# THOSE types must not block the "fully open" shortcut.
#
# Minimal future fix: split this into a narrower `_WAC_CONSUMING_TYPES`
# (SELL always; TRANSFER/REVERSAL/FEE only when amount<0 and asset not
# fiat — exactly positions.py's own conditions for reading wac_per_unit)
# and use THAT set in is_fully_open_since() /
# resolve_historical_effective_timestamp()'s conflict check, instead of
# this broader set.
#
# Future test matrix (not yet written):
#   - is_fully_open_since() stays True with a LATER same-asset BUY,
#     STAKING, BUY_COST_CORRECTION, or positive REVERSAL/TRANSFER present.
#   - is_fully_open_since() still returns False with a later SELL (already
#     covered today).
#   - NEW coverage: returns False with a later TRANSFER-out, negative
#     REVERSAL, or non-fiat outflow FEE (not explicitly tested today —
#     only SELL is).
#   - Regression: existing test_current_time_mode_rejected_when_not_fully_open
#     (a real SELL) must keep passing unchanged.
#   - New: mode="current_time" succeeds when a later BUY_COST_CORRECTION
#     exists but no SELL does — the exact real Sept 7 production shape —
#     without needing to fall back to mode="historical".
_ASSET_ORDERING_TYPES: FrozenSet[str] = frozenset({
    "BUY", "SELL", "TRANSFER", "REVERSAL", "STAKING", "FEE",
    "BUY_COST_CORRECTION",
})


# ── Centralized note encoding / decoding — the ONLY place this format lives ─

def build_buy_cost_correction_note(
    correction_of: str,
    reason: str,
    original_value: Decimal,
    corrected_value: Decimal,
    cash_delta: Decimal,
) -> str:
    """Build the single note string shared by both legs of a correction group.

    Format (stable, parsed back by parse_buy_cost_correction_note()):
        "BUY_COST_CORRECTION of {correction_of} | reason={reason} | "
        "original={original_value} | corrected={corrected_value} | "
        "cash_delta={cash_delta} | cost_basis_delta={cost_basis_delta}"
    """
    cost_basis_delta = -cash_delta
    return (
        f"{_NOTE_PREFIX} {correction_of} | reason={reason} | "
        f"original={original_value} | corrected={corrected_value} | "
        f"cash_delta={cash_delta} | cost_basis_delta={cost_basis_delta}"
    )


def parse_buy_cost_correction_note(note: Optional[str]) -> Optional[Dict[str, object]]:
    """Best-effort parse of a note built by build_buy_cost_correction_note().

    Returns None (never raises) when *note* doesn't match the expected
    shape — callers must treat that as "not a recognizable correction note",
    not as a rejected/invalid correction.
    """
    if not note or not note.startswith(_NOTE_PREFIX):
        return None
    try:
        rest = note[len(_NOTE_PREFIX):].strip()
        parts = [p.strip() for p in rest.split("|")]
        if not parts or not parts[0]:
            return None
        correction_of = parts[0]
        fields: Dict[str, str] = {}
        for p in parts[1:]:
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            fields[k.strip()] = v.strip()
        return {
            "correction_of": correction_of,
            "reason": fields.get("reason"),
            "original": Decimal(fields["original"]) if "original" in fields else None,
            "corrected": Decimal(fields["corrected"]) if "corrected" in fields else None,
            "cash_delta": Decimal(fields["cash_delta"]) if "cash_delta" in fields else None,
            "cost_basis_delta": Decimal(fields["cost_basis_delta"]) if "cost_basis_delta" in fields else None,
        }
    except Exception:
        return None


# ── Row builder ──────────────────────────────────────────────────────────────

def build_buy_cost_correction_rows(
    timestamp: datetime,
    correction_of: str,
    asset: str,
    currency: str,
    cash_delta: Decimal,
    venue: str,
    reason: str,
    original_value: Decimal,
    corrected_value: Decimal,
    account: Optional[str] = None,
    correction_id: Optional[str] = None,
) -> Tuple[RawRow, RawRow]:
    """Pure function — builds a valid 2-row BUY_COST_CORRECTION group. No I/O.

    Raises ValueError for structurally invalid input (cash_delta=0, missing
    correction_of, asset==currency). Both returned rows share the same id,
    venue, account, currency, and note — by construction, a group built
    here always passes validate_buy_cost_correction_group().

    cost_basis_delta is NOT a parameter — it is always -cash_delta (see
    module docstring for why this invariant is enforced structurally).
    """
    if cash_delta == Decimal("0"):
        raise ValueError("cash_delta nesmí být 0")
    if not correction_of or not correction_of.strip():
        raise ValueError("correction_of musí odkazovat na existující BUY trade id")
    if not venue or not venue.strip():
        raise ValueError("venue nesmí být prázdné")

    asset_uc = asset.upper().strip()
    currency_uc = currency.upper().strip()
    if not asset_uc:
        raise ValueError("asset nesmí být prázdný")
    if not currency_uc:
        raise ValueError("currency nesmí být prázdná")
    if asset_uc == currency_uc:
        raise ValueError(
            f"asset ({asset_uc}) nesmí být stejný jako fiat currency ({currency_uc}) — "
            "position marker musí opravovat NE-fiat pozici"
        )

    if correction_id is None:
        short = uuid.uuid4().hex[:8]
        correction_id = f"BCC_{correction_of}_{short}"

    note = build_buy_cost_correction_note(
        correction_of, reason, original_value, corrected_value, cash_delta
    )
    venue_norm = venue.lower().strip()

    marker = RawRow(
        id=correction_id, timestamp=timestamp, type="BUY_COST_CORRECTION",
        asset=asset_uc, amount=Decimal("0"), currency=currency_uc,
        price=None, venue=venue_norm, note=note, account=None,
    )
    cash_leg = RawRow(
        id=correction_id, timestamp=timestamp, type="BUY_COST_CORRECTION",
        asset=currency_uc, amount=cash_delta, currency=currency_uc,
        price=None, venue=venue_norm, note=note, account=account,
    )
    return marker, cash_leg


# ── Group-level structural validator ────────────────────────────────────────

def validate_buy_cost_correction_group(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> Tuple[bool, List[str]]:
    """Validate the structural shape of a BUY_COST_CORRECTION group (all rows
    sharing one id). Does NOT check that correction_of references an
    existing BUY trade — that requires the full ledger and lives in
    core/services/buy_cost_correction_service.py / health_service.py.
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)
    errors: List[str] = []

    correction_rows = [r for r in rows if r.type == "BUY_COST_CORRECTION"]
    if not correction_rows:
        return False, ["Skupina neobsahuje žádný BUY_COST_CORRECTION řádek."]

    markers = [r for r in correction_rows if r.asset.upper() not in fiat_set]
    cash_legs = [r for r in correction_rows if r.asset.upper() in fiat_set]

    if len(markers) != 1:
        errors.append(
            f"Musí existovat přesně 1 position marker leg (ne-fiat asset), nalezeno {len(markers)}."
        )
    if len(cash_legs) != 1:
        errors.append(
            f"Musí existovat přesně 1 cash delta leg, nalezeno {len(cash_legs)}."
        )

    if len(markers) == 1 and markers[0].amount != Decimal("0"):
        errors.append(f"Marker leg amount musí být 0, je {markers[0].amount}.")

    if len(cash_legs) == 1 and cash_legs[0].amount == Decimal("0"):
        errors.append("Cash delta (cash leg amount) nesmí být 0.")

    if len(markers) == 1 and len(cash_legs) == 1:
        marker, cash_leg = markers[0], cash_legs[0]
        if marker.venue != cash_leg.venue:
            errors.append(f"venue mismatch: marker={marker.venue!r} vs cash leg={cash_leg.venue!r}")
        if marker.currency.upper() != cash_leg.currency.upper():
            errors.append(
                f"currency mismatch: marker.currency={marker.currency!r} vs cash_leg.currency={cash_leg.currency!r}"
            )
        if marker.currency.upper() != cash_leg.asset.upper():
            errors.append(
                "Marker currency musí odpovídat cash leg asset (stejná fiat měna pro obě legs)."
            )

    return (len(errors) == 0, errors)


# ── Timestamp / ordering safety (Phase BUY-FIX architecture, section 3/5) ──

def is_fully_open_since(
    rows: List[RawRow],
    asset: str,
    since_timestamp: datetime,
) -> bool:
    """True iff no position-affecting row for *asset* has a timestamp
    strictly after *since_timestamp*.

    When this holds, a cost_basis correction may safely use ANY timestamp
    at-or-after since_timestamp (including "now") — the final (quantity,
    cost_basis, WAC) state is mathematically identical either way, because
    there is no intervening SELL/TRANSFER/etc. whose WAC-dependent result
    could differ depending on exactly when the correction is dated.
    """
    asset_uc = asset.upper()
    return not any(
        r.asset.upper() == asset_uc
        and r.type in _ASSET_ORDERING_TYPES
        and r.timestamp > since_timestamp
        for r in rows
    )


def resolve_historical_effective_timestamp(
    rows: List[RawRow],
    asset: str,
    original_timestamp: datetime,
    epsilon: timedelta = timedelta(microseconds=1),
) -> datetime:
    """Resolve a deterministic effective timestamp for a BUY_COST_CORRECTION
    that must be anchored to (i.e. take effect immediately after) an
    original BUY's timestamp — the "general case" from the Phase BUY-FIX
    architecture analysis (section 5).

    Returns original_timestamp + epsilon, but ONLY after positively
    verifying that interval (original_timestamp, original_timestamp +
    epsilon] contains no OTHER position-affecting row for *asset* — i.e. the
    correction is guaranteed to land strictly after the entire original BUY
    group and strictly before any subsequent trade of the same asset,
    without relying on id-string lexicographic tiebreaks.

    Raises ValueError (refuses to resolve a timestamp) if that safety
    cannot be proven — e.g. another row for the same asset already occupies
    that exact instant. This is a deliberate STOP, not a best-effort guess:
    per the architecture decision, an unprovable safe ordering must fail
    validation rather than silently risk misordering.
    """
    candidate = original_timestamp + epsilon
    asset_uc = asset.upper()
    conflicts = [
        r for r in rows
        if r.asset.upper() == asset_uc
        and r.type in _ASSET_ORDERING_TYPES
        and original_timestamp < r.timestamp <= candidate
    ]
    if conflicts:
        raise ValueError(
            f"Nelze bezpečně odvodit effective timestamp pro {asset_uc}: "
            f"interval ({original_timestamp.isoformat()}, {candidate.isoformat()}] "
            f"již obsahuje {len(conflicts)} jiný(ch) řádek/řádky "
            f"({sorted({r.id for r in conflicts})}). "
            "Correction by mohla obejít nebo být obejita jiným obchodem — "
            "zvol jiný epsilon nebo řeš ruční anchor timestamp."
        )
    return candidate
