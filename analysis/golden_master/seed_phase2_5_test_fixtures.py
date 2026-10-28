"""Phase 2.5 — seed small, clearly-marked TEST DATA into the visual-test DB copy.

SAFETY: this script takes db_path as an explicit CLI argument and refuses to
run against any path containing '.ledger_app\\ledger.db' (the production
filename) unless it's the dedicated phase2_5 test copy. It never touches
ledger.ini or any other file.

All rows carry a note starting with "TEST DATA — Phase 2.5" so they are
trivially identifiable and removable.
"""
from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.services.ui_facade import AddTradeRequestDTO, add_trade

_NOTE = "TEST DATA — Phase 2.5 visual review (COPY ONLY, not real)"
_TS = datetime(2026, 9, 18, 21, 0, 0)


def seed(db_path: str) -> None:
    if "ledger.db" in db_path and "phase2_5" not in db_path.lower():
        raise SystemExit(f"REFUSING to seed into a non-test-copy path: {db_path}")

    results = []

    # 1) External deposit into Revolut / Osobní CZK
    results.append(add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS, asset="CZK", amount=Decimal("20000"),
        currency="CZK", price=Decimal("0"), venue="external", to_venue="revolut",
        to_account="Osobní CZK", note=_NOTE,
    ), db_path))

    # 2) Small SELL of HYPE credited to Revolut / Osobní CZK (account-tagged
    #    proceeds row, for visual contrast against the real legacy
    #    account=None HYPE SELLs already in the copied history).
    results.append(add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS.replace(minute=5), asset="HYPE", amount=Decimal("0.5"),
        currency="CZK", quote_amount=Decimal("836.0625"), price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("10"), fee_currency="CZK",
        account="Osobní CZK", note=_NOTE,
    ), db_path))

    # 3) Same-venue, different-account transfer (Revolut / Osobní CZK ->
    #    Revolut / Investment CZK) — the scenario Phase 2 specifically
    #    unblocked.
    results.append(add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS.replace(minute=10), asset="CZK", amount=Decimal("3000"),
        currency="CZK", price=Decimal("0"), venue="revolut", to_venue="revolut",
        account="Osobní CZK", to_account="Investment CZK", note=_NOTE,
    ), db_path))

    # 4) Cross-venue transfer (Revolut -> Air Bank), matching the user's
    #    Air Bank example from the Phase 0 brief.
    results.append(add_trade(AddTradeRequestDTO(
        type="TRANSFER", timestamp=_TS.replace(minute=15), asset="CZK", amount=Decimal("5000"),
        currency="CZK", price=Decimal("0"), venue="revolut", to_venue="air bank",
        account="Osobní CZK", to_account="Investment Cash", note=_NOTE,
    ), db_path))

    for i, r in enumerate(results, 1):
        status = "OK" if r.success else f"FAILED: {r.error_message}"
        print(f"[{i}] rows_added={r.n_rows_added}  {status}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python seed_phase2_5_test_fixtures.py <path-to-test-copy-db>", file=sys.stderr)
        sys.exit(1)
    seed(sys.argv[1])
