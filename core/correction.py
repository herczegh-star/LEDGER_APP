"""CORRECTION: a narrowly-validated accounting type for fixing a historical
trade's realized PnL / cash attribution WITHOUT touching quantity, WAC, or
cost basis, and WITHOUT rewriting existing rows (append-only).

A valid CORRECTION group is exactly two rows sharing one `id`:

    A) POSITION MARKER LEG
       type=CORRECTION, asset=<non-fiat corrected asset>, amount=0,
       currency=<fiat currency>, venue=<venue>, account=<account or None>

    B) FIAT DELTA LEG
       type=CORRECTION, asset=<fiat currency>, amount=<signed delta>,
       currency=<same fiat currency>, venue=<same venue>, account=<same account>

Both legs carry identical correction metadata (encoded in `note` — see
build_correction_note() / parse_correction_note(), the ONLY place that
format is built or parsed; do not hand-roll note parsing elsewhere).

This module builds rows (build_correction_rows) and validates a group's
structure (validate_correction_group) — it does NOT insert anything into a
database and does NOT touch core/reports/positions.py's WAC/cost-basis math.

A wrong correction is corrected by a NEW correction with the opposite delta
— never by reversing a CORRECTION with the standard REVERSAL mechanism (see
core/services/reversal_service.py's guard, and the Phase 3B/3C analysis:
reversal-of-a-profitable-SELL re-adds cost at proceeds value, not at the
true removed cost, which would double the error here too).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})
_NOTE_PREFIX = "CORRECTION of"


# ── Centralized note encoding / decoding — the ONLY place this format lives ─

def build_correction_note(
    correction_of: str,
    reason: str,
    original_value: Decimal,
    corrected_value: Decimal,
    delta: Decimal,
) -> str:
    """Build the single note string shared by both legs of a correction group.

    Format (stable, parsed back by parse_correction_note()):
        "CORRECTION of {correction_of} | reason={reason} | "
        "original={original_value} | corrected={corrected_value} | delta={delta}"
    """
    return (
        f"{_NOTE_PREFIX} {correction_of} | reason={reason} | "
        f"original={original_value} | corrected={corrected_value} | delta={delta}"
    )


def parse_correction_note(note: Optional[str]) -> Optional[Dict[str, object]]:
    """Best-effort parse of a note built by build_correction_note().

    Returns None (never raises) when *note* doesn't match the expected
    shape — callers must treat that as "not a recognizable correction note",
    not as a rejected/invalid correction. Structural validity of a
    CORRECTION group is a row-shape question (see validate_correction_group),
    independent of whether its note happens to be parseable.
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
            "delta": Decimal(fields["delta"]) if "delta" in fields else None,
        }
    except Exception:
        return None


# ── Row builder ──────────────────────────────────────────────────────────────

def build_correction_rows(
    timestamp: datetime,
    correction_of: str,
    asset: str,
    currency: str,
    delta: Decimal,
    venue: str,
    reason: str,
    original_value: Decimal,
    corrected_value: Decimal,
    account: Optional[str] = None,
    correction_id: Optional[str] = None,
) -> Tuple[RawRow, RawRow]:
    """Pure function — builds a valid 2-row CORRECTION group. No I/O.

    Raises ValueError for structurally invalid input (delta=0, missing
    correction_of, asset==currency). Both returned rows share the same id,
    venue, account, currency, and note — by construction, a group built
    here always passes validate_correction_group().
    """
    if delta == Decimal("0"):
        raise ValueError("delta nesmí být 0")
    if not correction_of or not correction_of.strip():
        raise ValueError("correction_of musí odkazovat na existující trade id")
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
        correction_id = f"CORR_{correction_of}_{short}"

    note = build_correction_note(correction_of, reason, original_value, corrected_value, delta)
    venue_norm = venue.lower().strip()

    marker = RawRow(
        id=correction_id, timestamp=timestamp, type="CORRECTION",
        asset=asset_uc, amount=Decimal("0"), currency=currency_uc,
        price=None, venue=venue_norm, note=note, account=account,
    )
    fiat_leg = RawRow(
        id=correction_id, timestamp=timestamp, type="CORRECTION",
        asset=currency_uc, amount=delta, currency=currency_uc,
        price=None, venue=venue_norm, note=note, account=account,
    )
    return marker, fiat_leg


# ── Group-level structural validator ────────────────────────────────────────

def validate_correction_group(
    rows: List[RawRow],
    fiat: Optional[Set[str]] = None,
) -> Tuple[bool, List[str]]:
    """Validate the structural shape of a CORRECTION group (all rows sharing
    one id, already filtered to type=='CORRECTION' rows if the caller has
    a mixed group — this function itself filters defensively).

    Does NOT check that correction_of references an existing trade id —
    that requires the full ledger and lives in health_service.py instead.
    """
    if fiat is None:
        fiat = _FIAT_DEFAULT
    fiat_set = frozenset(a.upper() for a in fiat)
    errors: List[str] = []

    correction_rows = [r for r in rows if r.type == "CORRECTION"]
    if not correction_rows:
        return False, ["Skupina neobsahuje žádný CORRECTION řádek."]

    markers = [r for r in correction_rows if r.asset.upper() not in fiat_set]
    fiat_legs = [r for r in correction_rows if r.asset.upper() in fiat_set]

    if len(markers) != 1:
        errors.append(
            f"Musí existovat přesně 1 position marker leg (ne-fiat asset), nalezeno {len(markers)}."
        )
    if len(fiat_legs) != 1:
        errors.append(
            f"Musí existovat přesně 1 fiat delta leg, nalezeno {len(fiat_legs)}."
        )

    if len(markers) == 1 and markers[0].amount != Decimal("0"):
        errors.append(f"Marker leg amount musí být 0, je {markers[0].amount}.")

    if len(fiat_legs) == 1 and fiat_legs[0].amount == Decimal("0"):
        errors.append("Correction delta (fiat leg amount) nesmí být 0.")

    if len(markers) == 1 and len(fiat_legs) == 1:
        marker, fiat_leg = markers[0], fiat_legs[0]
        if marker.venue != fiat_leg.venue:
            errors.append(f"venue mismatch: marker={marker.venue!r} vs fiat leg={fiat_leg.venue!r}")
        if marker.account != fiat_leg.account:
            errors.append(f"account mismatch: marker={marker.account!r} vs fiat leg={fiat_leg.account!r}")
        if marker.currency.upper() != fiat_leg.currency.upper():
            errors.append(
                f"currency mismatch: marker.currency={marker.currency!r} vs fiat_leg.currency={fiat_leg.currency!r}"
            )
        if marker.currency.upper() != fiat_leg.asset.upper():
            errors.append(
                "Marker currency musí odpovídat fiat leg asset (stejná fiat měna pro obě legs)."
            )

    return (len(errors) == 0, errors)
