"""Phase 3C — MANDATORY invariant test (spec section 9/10).

Simulates the real HYPE correction against a COPY of the real historical
ledger (never the production DB directly — copied again into a pytest
tmp_path for full isolation, so this test can never mutate anything on
disk outside its own sandbox). Verifies:

    HYPE quantity, WAC, cost_basis:  UNCHANGED
    HYPE realized_pnl:               PRECISELY +75.00 CZK
    tracked cash:                    PRECISELY +75.00 CZK, landing in
                                      (revolut, "Osobní CZK", CZK) —
                                      NOT in (revolut, None, CZK) /
                                      "Unassigned Cash"
    every other asset:                bit-identical

Skips (does not fail) on a machine that doesn't have this specific
reference DB copy — it is a real, machine-local artifact from the Phase 0
baseline work, not something to fabricate or check into version control.
"""
from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from core.ledger_store import LedgerStore
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.correction_service import add_correction

_REFERENCE_DB = Path(
    r"C:\Users\hercz\.ledger_app\ledger_phase3c_correction_test_20260919_014530.db"
)
_TRADE_ID = "20260910_093542_REVOLUT_SELL_001"


@pytest.fixture
def db(tmp_path):
    if not _REFERENCE_DB.exists():
        pytest.skip(f"reference DB not present on this machine: {_REFERENCE_DB}")
    dst = tmp_path / "hype_correction_test.db"
    shutil.copyfile(_REFERENCE_DB, dst)
    return str(dst)


def _snapshot(db_path):
    store = LedgerStore(db_path)
    rows = store.timeline()
    store.close()
    positions = {p.asset: p for p in compute_positions(rows)}
    cash = compute_cash_balances(rows)
    return rows, positions, cash


def test_hype_correction_mandatory_invariants(db):
    rows_before, positions_before, cash_before = _snapshot(db)
    hype_before = positions_before["HYPE"]

    # Sanity: this is really the real historical trade, untagged (production
    # never had the account column migrated).
    original_rows = [r for r in rows_before if r.id == _TRADE_ID]
    assert len(original_rows) == 3
    assert all(r.account is None for r in original_rows)

    added = add_correction(
        db_path=db,
        timestamp=original_rows[0].timestamp.replace(hour=13, minute=0, second=0, microsecond=0)
        .replace(year=2026, month=9, day=19),
        correction_of=_TRADE_ID,
        asset="HYPE", currency="CZK", delta=Decimal("75"),
        venue="revolut", reason="revolut_sell_net_gross_fee",
        original_value=Decimal("3344.25"), corrected_value=Decimal("3419.25"),
        account="Osobní CZK",
    )
    assert len(added) == 2

    rows_after, positions_after, cash_after = _snapshot(db)
    hype_after = positions_after["HYPE"]

    # ── quantity / WAC / cost_basis: UNCHANGED ──────────────────────────────
    assert hype_after.quantity == hype_before.quantity
    assert hype_after.cost_basis == hype_before.cost_basis
    assert hype_after.wac == hype_before.wac

    # ── realized_pnl: PRECISELY +75.00 CZK ──────────────────────────────────
    assert hype_after.realized_pnl - hype_before.realized_pnl == Decimal("75.00")

    # ── every other asset: bit-identical ────────────────────────────────────
    others_before = {
        a: (p.quantity, p.cost_basis, p.realized_pnl)
        for a, p in positions_before.items() if a != "HYPE"
    }
    others_after = {
        a: (p.quantity, p.cost_basis, p.realized_pnl)
        for a, p in positions_after.items() if a != "HYPE"
    }
    assert others_before == others_after

    # ── cash: PRECISELY +75.00 CZK, in the TAGGED account, not Unassigned ──
    tagged_key = ("revolut", "Osobní CZK", "CZK")
    unassigned_key = ("revolut", None, "CZK")

    assert cash_after.get(tagged_key, Decimal("0")) == Decimal("75.00")
    assert tagged_key not in cash_before  # didn't exist before the correction

    # The original (untagged) SELL's contribution to Unassigned Cash is
    # completely unaffected by the correction — the +75 landed ONLY in the
    # newly-tagged account, never bled into the untagged bucket.
    assert cash_after.get(unassigned_key, Decimal("0")) == cash_before.get(unassigned_key, Decimal("0"))

    # Row count: exactly 2 new rows, nothing else changed structurally.
    assert len(rows_after) == len(rows_before) + 2
