"""Correction service: append-only, narrowly-validated CORRECTION group writer.

The ONLY supported way to write a CORRECTION group to the ledger. Builds the
2-row group via core/correction.py, validates it structurally BEFORE
touching the database, verifies correction_of references an existing id
(either an original trade or another correction — see module docstring in
core/correction.py on correction-of-a-correction), then appends both rows
dedup-safe via LedgerStore.import_rows().

Does not compute anything beyond what core/correction.py already does — no
WAC, no cost basis, no realized PnL math lives here.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import FrozenSet, List, Optional

from core.correction import build_correction_rows, validate_correction_group
from core.ledger_store import LedgerStore
from core.model import RawRow

_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK"})


def add_correction(
    db_path: str,
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
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> List[RawRow]:
    """Build, validate, and append one CORRECTION group.

    Raises ValueError (before any DB write) when:
      - the group is structurally invalid (see validate_correction_group), or
      - correction_of does not reference any existing id in the ledger
        (original trade or another correction).

    Returns the two inserted RawRow objects (marker, fiat_leg). Duplicate
    rows (identical to something already in the ledger) are silently
    skipped by LedgerStore's existing dedup — same contract as every other
    *_service.py write path in this codebase.
    """
    marker, fiat_leg = build_correction_rows(
        timestamp=timestamp,
        correction_of=correction_of,
        asset=asset,
        currency=currency,
        delta=delta,
        venue=venue,
        reason=reason,
        original_value=original_value,
        corrected_value=corrected_value,
        account=account,
    )

    ok, errs = validate_correction_group([marker, fiat_leg], fiat)
    if not ok:
        raise ValueError("Invalid correction group: " + "; ".join(errs))

    store = LedgerStore(db_path)
    try:
        existing_ids = {r.id for r in store.timeline()}
        if correction_of not in existing_ids:
            raise ValueError(
                f"correction_of={correction_of!r} does not reference an "
                "existing trade id (original trade or another correction) "
                "in the ledger."
            )

        # Use insert_pair() (atomic, per-row success reported) rather than
        # import_rows() — a correction MUST NEVER silently half-write. The
        # marker leg's shape is minimal (asset, amount=0, currency, venue —
        # identical across every correction of the same asset/venue/currency)
        # so it is at real, non-hypothetical risk of colliding on the legacy
        # row_fp with ANOTHER correction's marker leg if both share the same
        # timestamp (Phase 0 "Varianta 1" — legacy fingerprint is
        # account/id-blind by design; this is that exact edge case
        # materialising, not a bug in fingerprint()). A silent partial write
        # here would be far worse than for a normal trade: the group would
        # be structurally invalid (caught by health_service's check 9) and
        # the caller would have no idea their correction was never applied.
        ok_marker, ok_fiat = store.insert_pair(marker, fiat_leg)
        if not (ok_marker and ok_fiat):
            raise ValueError(
                "Correction was NOT fully written — "
                f"marker leg inserted={ok_marker}, fiat leg inserted={ok_fiat}. "
                "This usually means a row with an identical "
                "(timestamp, type, venue, asset, currency, amount) shape "
                "already exists (either an exact resubmission of this same "
                "correction, or — for the amount=0 marker leg specifically — "
                "a DIFFERENT correction of the same asset/venue/currency at "
                "the exact same timestamp, whose marker leg happens to look "
                "identical). Retry with a distinct timestamp if this is a "
                "genuinely different correction."
            )
    finally:
        store.close()

    return [marker, fiat_leg]
