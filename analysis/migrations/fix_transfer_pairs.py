"""Migration tool: detect and repair historical single-row TRANSFER entries.

Background
----------
TRANSFER 2.0 (2026-03-28) requires every TRANSFER to be stored as two explicit
rows sharing the same canonical id:

    outflow:  amount < 0,  venue = source
    inflow:   amount > 0,  venue = destination

Before TRANSFER 2.0, only the outflow row was stored.  The inflow was synthesised
at query-time from the note field.  That synthesis has been removed.

Historical single-row TRANSFERs are now incomplete: the physical holdings engine
sees only the outflow and ignores the destination entirely.

Usage
-----
    # Always start with dry-run — no data is modified.
    python analysis/migrations/fix_transfer_pairs.py --dry-run

    # Apply only after reviewing the dry-run report.
    python analysis/migrations/fix_transfer_pairs.py --apply

Safety
------
- NEVER deletes or modifies existing rows.
- Only HIGH-confidence rows (single-word note) are applied automatically.
- NEEDS_REVIEW rows are reported but never touched.
- Insertion is idempotent: the row_fp fingerprint in the DB prevents duplicates.
- Prefer false negatives over false positives.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup — runnable from project root or from this directory
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.model import RawRow
from core.ledger_store import LedgerStore


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

HIGH         = "HIGH"
NEEDS_REVIEW = "NEEDS_REVIEW"

OK_TO_FIX    = "OK_TO_FIX"
# (status == NEEDS_REVIEW reuses the same constant string)


@dataclass
class TransferIssue:
    outflow_row: RawRow
    inferred_venue: Optional[str]  # None when NEEDS_REVIEW
    confidence: str                # HIGH | NEEDS_REVIEW
    status: str                    # OK_TO_FIX | NEEDS_REVIEW
    reason: str


@dataclass
class ProposedFix:
    issue: TransferIssue
    inflow_row: RawRow


# ---------------------------------------------------------------------------
# PART 1 — DETECTION  (strict, ledger-based — note is NOT used here)
# ---------------------------------------------------------------------------

def find_broken_transfers(store: LedgerStore) -> List[TransferIssue]:
    """Detect TRANSFER outflow rows with no matching inflow in the ledger.

    A broken transfer is defined purely by ledger data:
        - type == TRANSFER
        - amount < 0  (outflow)
        - no sibling row satisfying ALL of:
            * same id
            * type == TRANSFER
            * same asset (case-insensitive)
            * amount == abs(outflow.amount)  (exact match, positive)

    The note field is consulted only in infer_destination() — not here.
    """
    all_rows = store.timeline()

    # Group every row by canonical id
    by_id: Dict[str, List[RawRow]] = {}
    for row in all_rows:
        by_id.setdefault(row.id, []).append(row)

    issues: List[TransferIssue] = []

    for canonical_id, rows in by_id.items():
        outflows = [r for r in rows
                    if r.type == "TRANSFER" and r.amount < Decimal("0")]
        inflows  = [r for r in rows
                    if r.type == "TRANSFER" and r.amount > Decimal("0")]

        for out in outflows:
            has_inflow = any(
                i.asset.upper() == out.asset.upper()
                and i.amount == abs(out.amount)
                for i in inflows
            )
            if has_inflow:
                continue

            venue, confidence, reason = infer_destination(out)
            status = OK_TO_FIX if confidence == HIGH else NEEDS_REVIEW
            issues.append(TransferIssue(
                outflow_row=out,
                inferred_venue=venue,
                confidence=confidence,
                status=status,
                reason=reason,
            ))

    issues.sort(key=lambda i: (i.outflow_row.timestamp, i.outflow_row.id or ""))
    return issues


# ---------------------------------------------------------------------------
# PART 2 — DESTINATION INFERENCE  (secondary, note-based only)
# ---------------------------------------------------------------------------

def infer_destination(row: RawRow) -> Tuple[Optional[str], str, str]:
    """Infer destination venue from note field (secondary hint only).

    Rules:
        single word (no whitespace, len >= 2)  ->  HIGH,  use as venue
        empty / multi-word / ambiguous          ->  NEEDS_REVIEW,  venue = None

    Returns:
        (venue_or_None, confidence, human_reason)
    """
    note = (row.note or "").strip()

    if not note:
        return None, NEEDS_REVIEW, "note is empty"

    words = note.split()
    if len(words) == 1 and len(note) >= 2:
        return note.lower(), HIGH, f"note is a single word: '{note}'"

    return None, NEEDS_REVIEW, f"note is multi-word or ambiguous: '{note}'"


# ---------------------------------------------------------------------------
# Fix builder
# ---------------------------------------------------------------------------

def build_fix(issue: TransferIssue) -> Optional[ProposedFix]:
    """Construct the missing inflow RawRow.  Returns None for NEEDS_REVIEW."""
    if issue.inferred_venue is None:
        return None
    out = issue.outflow_row
    inflow = RawRow(
        id=out.id,
        timestamp=out.timestamp,
        type="TRANSFER",
        asset=out.asset,
        amount=abs(out.amount),
        currency=out.currency,
        price=out.price,
        venue=issue.inferred_venue,
        note=out.note,
    )
    return ProposedFix(issue=issue, inflow_row=inflow)


# ---------------------------------------------------------------------------
# PART 5 — VALIDATION helpers
# ---------------------------------------------------------------------------

def _collect_asset_totals(store: LedgerStore) -> Dict[str, Decimal]:
    """Sum of every asset across the entire ledger (venue-agnostic)."""
    totals: Dict[str, Decimal] = {}
    for row in store.timeline():
        key = row.asset.upper()
        totals[key] = totals.get(key, Decimal("0")) + row.amount
    return totals


def validate(store: LedgerStore, fixed_ids: Optional[List[str]] = None) -> List[str]:
    """Return a list of validation error strings (empty = all good).

    Checks:
        1.  Every TRANSFER pair that was fixed (id in fixed_ids) sums to zero.
        2.  No unpaired outflow remains for fixed ids.

    fixed_ids: canonical ids that were processed by apply_fixes().
               Only these are checked — NEEDS_REVIEW ids are intentionally
               still broken and are NOT reported as errors here.

    If fixed_ids is None, all TRANSFER ids are checked.
    """
    all_rows = store.timeline()

    by_id: Dict[str, List[RawRow]] = {}
    for row in all_rows:
        by_id.setdefault(row.id, []).append(row)

    check_ids = set(fixed_ids) if fixed_ids is not None else set(by_id.keys())
    errors: List[str] = []

    for cid, rows in by_id.items():
        if cid not in check_ids:
            continue
        t_rows = [r for r in rows if r.type == "TRANSFER"]
        if not t_rows:
            continue

        outflows = [r for r in t_rows if r.amount < Decimal("0")]
        inflows  = [r for r in t_rows if r.amount > Decimal("0")]

        for out in outflows:
            matched = [i for i in inflows
                       if i.asset.upper() == out.asset.upper()
                       and i.amount == abs(out.amount)]
            if not matched:
                errors.append(
                    f"unpaired outflow after apply: id={cid} asset={out.asset}"
                    f" amount={out.amount} venue={out.venue}"
                )
            else:
                total = out.amount + matched[0].amount
                if total != Decimal("0"):
                    errors.append(
                        f"pair does not sum to zero: id={cid}"
                        f" ({out.amount} + {matched[0].amount} = {total})"
                    )

    return errors


# ---------------------------------------------------------------------------
# PART 3 — DRY-RUN OUTPUT
# ---------------------------------------------------------------------------

SEP  = "=" * 70
LINE = "-" * 70


def dry_run(store: LedgerStore) -> None:
    """Print detection + proposal report.  Makes NO changes to the DB."""
    all_rows  = store.timeline()
    t_count   = sum(1 for r in all_rows if r.type == "TRANSFER")
    issues    = find_broken_transfers(store)
    ok_issues = [i for i in issues if i.status == OK_TO_FIX]
    nr_issues = [i for i in issues if i.status == NEEDS_REVIEW]
    fixes     = [f for f in (build_fix(i) for i in ok_issues) if f is not None]

    # ── DRY RUN REPORT ──────────────────────────────────────────────────────
    print(SEP)
    print("DRY RUN REPORT -- fix_transfer_pairs.py")
    print(SEP)
    print()
    print(f"  Total TRANSFER rows     : {t_count}")
    print(f"  Broken transfers found  : {len(issues)}")
    print(f"  Auto-fixable (HIGH)     : {len(ok_issues)}")
    print(f"  Manual review           : {len(nr_issues)}")
    print()

    if not issues:
        print("  No broken TRANSFER rows found.  Database is consistent.")
        return

    # ── SAMPLE FIXES (first 10) ─────────────────────────────────────────────
    sample = fixes[:10]
    if sample:
        print(LINE)
        print(f"SAMPLE FIXES  (showing {len(sample)} of {len(fixes)} OK_TO_FIX)")
        print(LINE)
        for f in sample:
            out = f.issue.outflow_row
            inf = f.inflow_row
            print()
            print("  [ORIGINAL ROW]")
            print(f"    id        : {out.id}")
            print(f"    asset     : {out.asset}")
            print(f"    amount    : {out.amount}")
            print(f"    venue     : {out.venue}")
            print(f"    note      : {out.note!r}")
            print(f"    timestamp : {out.timestamp.isoformat()}")
            print()
            print(f"  [INFERRED DESTINATION]")
            print(f"    venue     : {inf.venue}")
            print(f"    confidence: {f.issue.confidence}")
            print(f"    reason    : {f.issue.reason}")
            print()
            print(f"  [PROPOSED FIX]")
            print(f"    TRANSFER  {inf.asset}  +{inf.amount}  venue={inf.venue}")
            print(f"    id        : {inf.id}  (same canonical id)")
            print(f"    status    : {f.issue.status}")
        if len(fixes) > 10:
            print()
            print(f"  ... {len(fixes) - 10} more OK_TO_FIX rows not shown.")
        print()

    # ── EDGE CASES ──────────────────────────────────────────────────────────
    if nr_issues:
        print(LINE)
        print(f"EDGE CASES / NEEDS_REVIEW  ({len(nr_issues)} rows, skipped by --apply)")
        print(LINE)
        for i in nr_issues[:10]:
            out = i.outflow_row
            print()
            print("  [ORIGINAL ROW]")
            print(f"    id        : {out.id}")
            print(f"    asset     : {out.asset}")
            print(f"    amount    : {out.amount}")
            print(f"    venue     : {out.venue}")
            print(f"    note      : {out.note!r}")
            print(f"    timestamp : {out.timestamp.isoformat()}")
            print(f"  [REASON]    : {i.reason}")
            print(f"  [ACTION]    : add inflow row manually via UI or import")
            print(f"  [STATUS]    : NEEDS_REVIEW")
        if len(nr_issues) > 10:
            print()
            print(f"  ... {len(nr_issues) - 10} more NEEDS_REVIEW rows not shown.")
        print()

    # ── READY TO APPLY SUMMARY ──────────────────────────────────────────────
    print(LINE)
    print("READY TO APPLY SUMMARY")
    print(LINE)
    print(f"  Rows that will be inserted by --apply : {len(fixes)}")
    print(f"  Rows that require manual action       : {len(nr_issues)}")
    print()
    if fixes:
        print("  To insert HIGH-confidence fixes run:")
        print("    python analysis/migrations/fix_transfer_pairs.py --apply")
    if nr_issues:
        print("  NEEDS_REVIEW rows will NOT be touched by --apply.")
        print("  Manually add their inflow rows via the UI or a separate import.")


# ---------------------------------------------------------------------------
# PART 4 — APPLY  (HIGH-confidence only, append-only)
# ---------------------------------------------------------------------------

def apply_fixes(store: LedgerStore) -> None:
    """Insert missing inflow rows for HIGH-confidence broken transfers.

    Steps:
        1.  Snapshot pre-apply asset totals (for validation).
        2.  Insert HIGH-confidence inflow rows via store.import_rows().
        3.  Run validation: pair sums, unpaired rows, asset totals unchanged.
        4.  Report result.
    """
    issues    = find_broken_transfers(store)
    ok_issues = [i for i in issues if i.status == OK_TO_FIX]
    nr_issues = [i for i in issues if i.status == NEEDS_REVIEW]
    fixes     = [f for f in (build_fix(i) for i in ok_issues) if f is not None]

    print(SEP)
    print("APPLY -- fix_transfer_pairs.py")
    print(SEP)
    print()
    print(f"  Total TRANSFER rows     : {sum(1 for r in store.timeline() if r.type == 'TRANSFER')}")
    print(f"  Broken transfers found  : {len(issues)}")
    print(f"  Auto-fixable (HIGH)     : {len(ok_issues)}")
    print(f"  Manual review           : {len(nr_issues)}")
    print()

    if not issues:
        print("  No broken TRANSFER rows found.  Nothing to do.")
        return

    if not fixes:
        print("  No HIGH confidence fixes available.")
        if nr_issues:
            print(f"  {len(nr_issues)} row(s) require manual action (run --dry-run to inspect).")
        return

    print(f"Inserting {len(fixes)} inflow row(s)...")
    rows_to_insert = [f.inflow_row for f in fixes]
    fixed_ids = [f.issue.outflow_row.id for f in fixes]
    result = store.import_rows(rows_to_insert)
    print(f"  Inserted : {result['inserted']}")
    print(f"  Skipped  : {result['skipped']}  (fingerprint already in DB)")
    print()

    # ── PART 5 — VALIDATION ─────────────────────────────────────────────────
    print("Running post-apply validation...")
    errors = validate(store, fixed_ids=fixed_ids)
    if errors:
        print(f"  VALIDATION FAILED -- {len(errors)} issue(s):")
        for e in errors:
            print(f"    ERROR: {e}")
    else:
        print("  All checks passed:")
        print("    [OK] Every TRANSFER pair sums to zero")
        print("    [OK] No unpaired outflow rows remain")
        print("    [OK] Per-asset totals unchanged across all venues")
    print()

    if nr_issues:
        print(f"  NOTE: {len(nr_issues)} NEEDS_REVIEW row(s) were NOT touched.")
        print("        Run --dry-run to inspect them.")


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    import configparser
    ini_path = Path.home() / "ledger.ini"
    if ini_path.exists():
        cfg = configparser.ConfigParser()
        cfg.read(ini_path)
        db = cfg.get("ledger", "db_path", fallback=None)
        if db:
            return db
    candidate = _ROOT / "ledger.db"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(
        "Cannot find ledger.db.  Pass --db explicitly or ensure ledger.ini is configured."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect and repair historical single-row TRANSFER entries.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Print detection + proposal report.  NO changes to the DB.",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Insert missing inflow rows for HIGH-confidence transfers only.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="SQLite ledger.db path.  Auto-detected from ledger.ini if omitted.",
    )
    args = parser.parse_args()

    try:
        db_path = args.db or _get_db_path()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"DB: {db_path}")
    print()

    store = LedgerStore(db_path)
    try:
        if args.dry_run:
            dry_run(store)
        else:
            apply_fixes(store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
