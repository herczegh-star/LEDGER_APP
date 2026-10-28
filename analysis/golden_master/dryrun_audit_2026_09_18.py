"""Phase 0 dry-run audit for the cash-accounts dedup migration design.

READ-ONLY against a COPY of the production DB. Performs zero writes, zero
ALTER TABLE, zero index changes. Verifies:

  1. Row count is unchanged before/after the audit (proves no writes happened).
  2. For every existing (legacy, account=NULL) row: recomputed operation_key
     (per the Phase 0 dedup design) equals the row's stored row_fp exactly.
  3. The stored row_fp column matches RawRow.fingerprint() recomputed from
     the row's own fields (sanity check that stored fingerprints are not
     stale relative to the current fingerprint() formula).
  4. No duplicate row_fp values exist (would violate the current unique
     index — defensive check, not expected to fire).
  5. Structural anomalies relevant to a future migration: NULL/empty venue,
     asset, currency, id; zero amounts; unusual per-id row-group sizes.

Usage:
    python analysis/golden_master/dryrun_audit_2026_09_18.py <path-to-backup-db>
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.ledger_store import LedgerStore

# Same prototype logic as tests/test_cash_dedup_design.py — duplicated here
# deliberately (this script must not import from a test module).
def _operation_key(row, account=None) -> str:
    fp2_input = "|".join([
        row.timestamp.isoformat(), row.type.upper(), row.venue.lower(),
        (account or "").strip(), row.asset.upper(), row.currency.upper(),
        f"{row.amount:.8f}",
    ])
    import hashlib
    fp2 = hashlib.sha256(fp2_input.encode()).hexdigest()
    return fp2 if (account or "").strip() else row.fingerprint()


def run_audit(db_path: str) -> dict:
    store = LedgerStore(db_path)
    try:
        count_before = store.count()
        rows = store.timeline()

        # ── 2 & 3: operation_key vs stored row_fp, fingerprint recompute check ──
        mismatches_fp_recompute = []
        mismatches_operation_key = []
        row_fp_seen = defaultdict(int)

        cur = store.conn.execute("SELECT pk, id, row_fp FROM ledger ORDER BY pk")
        stored_fp_by_pk = {r["pk"]: (r["id"], r["row_fp"]) for r in cur.fetchall()}

        # timeline() doesn't return pk, so re-query rows-with-pk directly
        full_rows = store.conn.execute("SELECT * FROM ledger ORDER BY pk").fetchall()
        from core.model import RawRow
        from decimal import Decimal
        from datetime import datetime

        for r in full_rows:
            row = RawRow(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                type=r["type"],
                asset=r["asset"],
                amount=Decimal(r["amount"]),
                currency=r["currency"],
                price=Decimal(r["price"]) if r["price"] else None,
                venue=r["venue"],
                note=r["note"],
            )
            recomputed_fp = row.fingerprint()
            stored_fp = r["row_fp"]
            row_fp_seen[stored_fp] += 1

            if recomputed_fp != stored_fp:
                mismatches_fp_recompute.append({
                    "pk": r["pk"], "id": r["id"],
                    "stored_row_fp": stored_fp, "recomputed_fingerprint": recomputed_fp,
                })

            # account is always None for every existing row (column doesn't
            # exist yet) => operation_key MUST equal legacy fingerprint.
            op_key = _operation_key(row, account=None)
            if op_key != stored_fp:
                mismatches_operation_key.append({
                    "pk": r["pk"], "id": r["id"],
                    "stored_row_fp": stored_fp, "operation_key": op_key,
                })

        # ── 4: duplicate row_fp values (would violate unique index) ─────────
        duplicate_row_fps = {fp: n for fp, n in row_fp_seen.items() if n > 1}

        # ── 5: structural anomalies ──────────────────────────────────────────
        anomalies = {
            "null_or_empty_venue": [],
            "null_or_empty_asset": [],
            "null_or_empty_currency": [],
            "null_or_empty_id": [],
            "zero_amount": [],
            "invalid_type": [],
        }
        _VALID_TYPES = {"BUY", "SELL", "TRANSFER", "FEE", "REVERSAL", "STAKING"}

        id_group_sizes = defaultdict(int)

        for r in full_rows:
            pk = r["pk"]
            if not r["venue"] or not r["venue"].strip():
                anomalies["null_or_empty_venue"].append(pk)
            if not r["asset"] or not r["asset"].strip():
                anomalies["null_or_empty_asset"].append(pk)
            if not r["currency"] or not r["currency"].strip():
                anomalies["null_or_empty_currency"].append(pk)
            if not r["id"] or not r["id"].strip():
                anomalies["null_or_empty_id"].append(pk)
            try:
                if float(r["amount"]) == 0.0:
                    anomalies["zero_amount"].append(pk)
            except (TypeError, ValueError):
                pass
            if r["type"] not in _VALID_TYPES:
                anomalies["invalid_type"].append({"pk": pk, "type": r["type"]})
            id_group_sizes[r["id"]] += 1

        unusual_group_sizes = {
            id_: size for id_, size in id_group_sizes.items()
            if size not in (1, 2, 3)  # 1=lone/FEE, 2=BUY/SELL or TRANSFER pair, 3=trade+fee
        }

        count_after = store.count()

        return {
            "count_before": count_before,
            "count_after": count_after,
            "count_unchanged": count_before == count_after,
            "total_rows_scanned": len(full_rows),
            "fingerprint_recompute_mismatches": mismatches_fp_recompute,
            "operation_key_vs_row_fp_mismatches": mismatches_operation_key,
            "duplicate_row_fp_values": duplicate_row_fps,
            "anomalies": anomalies,
            "unusual_id_group_sizes": unusual_group_sizes,
        }
    finally:
        store.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python dryrun_audit_2026_09_18.py <path-to-backup-db>", file=sys.stderr)
        sys.exit(1)

    result = run_audit(sys.argv[1])

    out_path = Path(__file__).parent / "dryrun_audit_2026_09_18.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, sort_keys=True)

    print(f"Dry-run audit written to: {out_path}")
    print(f"count_before == count_after: {result['count_unchanged']} "
          f"({result['count_before']} -> {result['count_after']})")
    print(f"fingerprint recompute mismatches: {len(result['fingerprint_recompute_mismatches'])}")
    print(f"operation_key vs row_fp mismatches: {len(result['operation_key_vs_row_fp_mismatches'])}")
    print(f"duplicate row_fp values: {len(result['duplicate_row_fp_values'])}")
    for k, v in result["anomalies"].items():
        print(f"anomaly[{k}]: {len(v)}")
    print(f"unusual id-group sizes: {len(result['unusual_id_group_sizes'])}")
