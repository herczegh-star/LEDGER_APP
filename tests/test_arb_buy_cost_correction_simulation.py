"""Phase BUY-FIX — Section 12 MANDATORY invariant test: real Sept 9 ARB
BUY_COST_CORRECTION simulation.

Simulates the correction against a COPY of the real production ledger
(never the production DB directly — copied again into a pytest tmp_path for
full isolation, same pattern as tests/test_hype_correction_simulation.py).
Verifies the exact numbers from the architecture approval (section 12):

    ARB quantity:      unchanged (6322.76881815)
    ARB cost_basis:    -89 (30177.99999980676200000000000 -> 30088.99...)
    ARB WAC:           ≈ 4.758832857123282072754649890
    ARB realized_pnl:  unchanged (0)
    Revolut / Osobní CZK: +89 CZK attributable to the correction
    All other assets:  bit-identical

Skips (does not fail) on a machine that doesn't have this specific
reference DB copy — a real, machine-local artifact, not checked into
version control (same convention as test_hype_correction_simulation.py).

Sept 7 BUY (20260907_133957_REVOLUT_BUY_001) is deliberately NOT touched —
per the architecture decision, it remains unconfirmed pending its own
source screenshot.
"""
from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from core.ledger_store import LedgerStore
from core.reports.cash import compute_cash_balances
from core.reports.positions import compute_positions
from core.services.buy_cost_correction_service import add_buy_cost_correction

_REFERENCE_DB = Path(
    r"C:\Users\hercz\.ledger_app\ledger_buy_cost_correction_test_20260919.db"
)
_TRADE_ID = "20260909_212239_REVOLUT_BUY_001"
_SEPT7_TRADE_ID = "20260907_133957_REVOLUT_BUY_001"


@pytest.fixture
def db(tmp_path):
    if not _REFERENCE_DB.exists():
        pytest.skip(f"reference DB not present on this machine: {_REFERENCE_DB}")
    dst = tmp_path / "arb_correction_test.db"
    shutil.copyfile(_REFERENCE_DB, dst)
    return str(dst)


def _snapshot(db_path):
    store = LedgerStore(db_path)
    rows = store.timeline()
    store.close()
    positions = {p.asset: p for p in compute_positions(rows)}
    cash = compute_cash_balances(rows)
    return rows, positions, cash


def test_sept9_arb_correction_mandatory_invariants(db):
    rows_before, positions_before, cash_before = _snapshot(db)
    arb_before = positions_before["ARB"]

    # Sanity: this really is the real historical trade, untagged.
    original_rows = [r for r in rows_before if r.id == _TRADE_ID]
    assert len(original_rows) == 3
    assert all(r.account is None for r in original_rows)

    # Confirm the exact pre-correction values quoted in the architecture
    # approval before applying anything.
    assert arb_before.quantity == Decimal("6322.76881815")
    assert arb_before.cost_basis == Decimal("30177.99999980676200000000000")
    assert arb_before.realized_pnl == Decimal("0")

    added = add_buy_cost_correction(
        db_path=db, correction_of=_TRADE_ID, asset="ARB", currency="CZK",
        cash_delta=Decimal("89"), venue="revolut", reason="revolut_total_cash_debited_misread_as_order_value",
        original_value=Decimal("5089"), corrected_value=Decimal("5000"),
        account="Osobní CZK", mode="historical",
    )
    assert len(added) == 2

    rows_after, positions_after, cash_after = _snapshot(db)
    arb_after = positions_after["ARB"]

    # ── quantity: UNCHANGED ──────────────────────────────────────────────────
    assert arb_after.quantity == arb_before.quantity

    # ── cost_basis: exactly -89 ──────────────────────────────────────────────
    assert arb_before.cost_basis - arb_after.cost_basis == Decimal("89")
    assert arb_after.cost_basis == Decimal("30088.99999980676200000000000")

    # ── WAC: automatically recomputed from the corrected cost_basis ─────────
    expected_wac = arb_after.cost_basis / arb_after.quantity
    assert arb_after.wac == expected_wac

    # ── realized_pnl: UNCHANGED ──────────────────────────────────────────────
    assert arb_after.realized_pnl == arb_before.realized_pnl == Decimal("0")

    # ── every other asset: bit-identical ─────────────────────────────────────
    others_before = {
        a: (p.quantity, p.cost_basis, p.realized_pnl)
        for a, p in positions_before.items() if a != "ARB"
    }
    others_after = {
        a: (p.quantity, p.cost_basis, p.realized_pnl)
        for a, p in positions_after.items() if a != "ARB"
    }
    assert others_before == others_after

    # ── cash: +89 CZK, in the TAGGED account ─────────────────────────────────
    tagged_key = ("revolut", "Osobní CZK", "CZK")
    assert cash_after.get(tagged_key, Decimal("0")) - cash_before.get(tagged_key, Decimal("0")) == Decimal("89")

    # Row count: exactly 2 new rows, nothing else changed structurally.
    assert len(rows_after) == len(rows_before) + 2

    # ── Sept 7 BUY: untouched, unclassified ──────────────────────────────────
    sept7_rows = [r for r in rows_after if r.id == _SEPT7_TRADE_ID]
    assert len(sept7_rows) == 3
    assert all(r.type in ("BUY", "FEE") for r in sept7_rows)
    # No BUY_COST_CORRECTION group references the Sept 7 trade.
    bcc_notes = [r.note or "" for r in rows_after if r.type == "BUY_COST_CORRECTION"]
    assert not any(_SEPT7_TRADE_ID in note for note in bcc_notes)
