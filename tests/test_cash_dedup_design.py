"""Phase 0 design-validation tests for the cash-accounts dedup mechanism.

IMPORTANT — this file tests a NOT-YET-IMPLEMENTED design, not production code.
`core/model.py` (RawRow) is intentionally NOT modified in this phase. The
candidate `fingerprint_v2()` / `operation_key()` functions below are defined
locally, as a prototype, so the design can be validated with real RawRow
instances before any production change is made.

RawRow has no `account` field yet. These tests attach `.account` dynamically
(RawRow is a plain @dataclass without __slots__, so this is legal Python and
requires zero change to core/model.py) purely to exercise the prototype
hashing functions against realistic row shapes.

Findings from this file feed directly into the Phase 0 report — in particular
`test_switch_based_operation_key_ALLOWS_mixed_import_duplicate`, which proves
that the "operation_key = fingerprint_v2 if account else legacy fingerprint"
design proposed earlier is UNSAFE and must not be implemented as specified.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from core.model import RawRow


# ── Prototype functions (candidate design, NOT in core/model.py) ───────────

def fingerprint_v2(row: RawRow) -> str:
    """Candidate account-aware fingerprint. Prototype only — mirrors the
    design proposed for a future RawRow.fingerprint_v2()."""
    account = getattr(row, "account", None)
    parts = "|".join([
        row.timestamp.isoformat(),
        row.type.upper(),
        row.venue.lower(),
        (account or "").strip(),
        row.asset.upper(),
        row.currency.upper(),
        f"{row.amount:.8f}",
    ])
    return hashlib.sha256(parts.encode()).hexdigest()


def operation_key(row: RawRow) -> str:
    """Candidate switch-based identity: v2 hash when account is set,
    otherwise fall back to the legacy fingerprint() untouched."""
    account = getattr(row, "account", None)
    if (account or "").strip():
        return fingerprint_v2(row)
    return row.fingerprint()


def _base_row(**overrides) -> RawRow:
    defaults = dict(
        timestamp=datetime(2026, 9, 10, 9, 31, 0),
        type="SELL",
        asset="HYPE",
        amount=Decimal("-2"),
        currency="CZK",
        price=Decimal("1672.125"),
        venue="revolut",
        note=None,
    )
    defaults.update(overrides)
    return RawRow(**defaults)


# ── 1. Legacy fingerprint() must stay byte-identical ───────────────────────

def test_legacy_fingerprint_pinned_value():
    """Regression pin: if this ever changes, fingerprint() was modified —
    which Phase 0 explicitly forbids."""
    row = _base_row(id="fixed-id-for-pin-test")
    expected = hashlib.sha256(
        "|".join([
            "2026-09-10T09:31:00",
            "SELL",
            "revolut",
            "HYPE",
            "CZK",
            "-2.00000000",
        ]).encode()
    ).hexdigest()
    assert row.fingerprint() == expected


def test_legacy_fingerprint_deterministic():
    row_a = _base_row(id="a")
    row_b = _base_row(id="b")  # different id — fingerprint must not use id
    assert row_a.fingerprint() == row_b.fingerprint()


# ── 2. fingerprint_v2() determinism ─────────────────────────────────────────

def test_fingerprint_v2_deterministic():
    row = _base_row()
    row.account = "Osobní CZK"
    assert fingerprint_v2(row) == fingerprint_v2(row)

    row_again = _base_row()
    row_again.account = "Osobní CZK"
    assert fingerprint_v2(row) == fingerprint_v2(row_again)


# ── 3. account=None => operation_key == legacy fingerprint ─────────────────

def test_operation_key_account_none_equals_legacy_fingerprint():
    row = _base_row()  # .account never set => getattr(..., None) => None
    assert operation_key(row) == row.fingerprint()


def test_operation_key_account_empty_string_equals_legacy_fingerprint():
    row = _base_row()
    row.account = "   "  # whitespace-only => treated as empty
    assert operation_key(row) == row.fingerprint()


# ── 4. Same core attributes + different account => different operation_key ─

def test_operation_key_differs_by_account():
    row_a = _base_row()
    row_a.account = "Osobní CZK"
    row_b = _base_row()
    row_b.account = "Investment CZK"
    assert operation_key(row_a) != operation_key(row_b)


# ── 5. Whitespace normalization on account ──────────────────────────────────

def test_account_whitespace_normalization():
    row_a = _base_row()
    row_a.account = "Osobní CZK"
    row_b = _base_row()
    row_b.account = "  Osobní CZK  "
    assert fingerprint_v2(row_a) == fingerprint_v2(row_b)
    assert operation_key(row_a) == operation_key(row_b)


# ── 6. account must NOT influence the legacy fingerprint ───────────────────

def test_legacy_fingerprint_blind_to_account():
    row_a = _base_row()
    row_a.account = "Osobní CZK"
    row_b = _base_row()
    row_b.account = "Investment CZK"
    row_c = _base_row()  # no account at all
    assert row_a.fingerprint() == row_b.fingerprint() == row_c.fingerprint()


# ── 7. CRITICAL — mixed legacy / account-aware import duplicate risk ───────

def test_switch_based_operation_key_ALLOWS_mixed_import_duplicate():
    """Proves the vulnerability requested for Phase 0 step 4.

    Scenario: the SAME economic transaction is recorded twice —
    once through an account-aware entry path (account="Osobní CZK"),
    once through a legacy/import path unaware of account (account=None).

    Under the switch-based operation_key design proposed earlier,
    these two rows produce DIFFERENT operation_key values, so a
    UNIQUE INDEX on operation_key would NOT catch this as a duplicate —
    both rows would be silently inserted.

    This test intentionally asserts the unsafe outcome to document it.
    It is proof of a design flaw, not a specification to implement.
    """
    row_account_aware = _base_row()
    row_account_aware.account = "Osobní CZK"

    row_legacy_import = _base_row()
    # .account never set -> getattr(..., None) -> None (simulates an import
    # path that has no knowledge of the account concept at all)

    assert row_account_aware.fingerprint() == row_legacy_import.fingerprint(), (
        "Precondition: both rows must be the SAME economic transaction "
        "(identical legacy shape) for this to be a meaningful duplicate test."
    )

    op_key_aware  = operation_key(row_account_aware)
    op_key_legacy = operation_key(row_legacy_import)

    assert op_key_aware != op_key_legacy, (
        "VULNERABILITY CONFIRMED: switch-based operation_key produces two "
        "different identities for the same economic transaction when one "
        "import path knows the account and the other does not. A UNIQUE "
        "INDEX on operation_key alone would admit both rows as if distinct — "
        "silent duplicate. See Phase 0 report section D for safe alternatives."
    )


def test_legacy_only_uniqueness_WOULD_prevent_the_same_duplicate():
    """Control test: shows that keeping row_fp (legacy, unconditional) as the
    SOLE unique constraint — i.e. never switching to fingerprint_v2 for
    uniqueness — correctly collides on the mixed-import scenario above,
    which is the safe behavior we want.
    """
    row_account_aware = _base_row()
    row_account_aware.account = "Osobní CZK"

    row_legacy_import = _base_row()

    # Legacy fingerprint alone (unconditionally, ignoring account) already
    # collides for these two rows -> a UNIQUE INDEX on row_fp alone would
    # reject the second insert as a duplicate, exactly like it does today.
    assert row_account_aware.fingerprint() == row_legacy_import.fingerprint()
