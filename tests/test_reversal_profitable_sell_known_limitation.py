"""KNOWN LIMITATION (documented, not fixed — Phase 3B/3C analysis).

Standard REVERSAL (core/services/reversal_service.py + compute_positions())
does NOT correctly undo a profitable SELL: reversing re-adds cost basis at
the REVERSED PROCEEDS value, not at the true WAC-based cost that was
removed by the original sale. When there is realized gain/loss on the
original sale (proceeds != cost_removed), this is not a true mathematical
inverse — it distorts cost_basis by the magnitude of the original trade's
realized PnL, and a subsequent re-entry re-realizes that distortion.

This test PINS the current (undesirable but real) behaviour with a
self-contained synthetic fixture, so:
  - any future intentional fix to reversal semantics has to consciously
    update this test (it will fail otherwise — that's the point), and
  - nobody re-discovers this by surprise against production data again.

This is NOT a recommendation to use REVERSAL for correcting a profitable
SELL — see core/correction.py and the CORRECTION type for the safe way to
do that. See also core/services/reversal_service.py's explicit guard
against reversing a CORRECTION group for the adjacent, already-fixed risk.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.reports.positions import compute_positions
from core.services.reversal_service import build_reversal_rows

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 35, 42)


def _fixture():
    """8 HYPE @ 1200 CZK (cost_basis=9600, wac=1200), then SELL 2 HYPE for
    3344.25 CZK with a 75 CZK fee — a profitable sale (proceeds > cost
    removed): cost_removed = 1200*2 = 2400, realized_pnl = 3344.25-2400-75 = 869.25."""
    return [
        RawRow(id="BUY1", timestamp=_TS_BUY, type="BUY", asset="HYPE",
               amount=Decimal("8"), currency="CZK", price=Decimal("1200"), venue="revolut"),
        RawRow(id="BUY1", timestamp=_TS_BUY, type="BUY", asset="CZK",
               amount=Decimal("-9600"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="SELL1", timestamp=_TS_SELL, type="SELL", asset="HYPE",
               amount=Decimal("-2"), currency="CZK", price=Decimal("1672.125"), venue="revolut"),
        RawRow(id="SELL1", timestamp=_TS_SELL, type="SELL", asset="CZK",
               amount=Decimal("3344.25"), currency="CZK", price=Decimal("1"), venue="revolut"),
        RawRow(id="SELL1", timestamp=_TS_SELL, type="FEE", asset="CZK",
               amount=Decimal("-75"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]


def test_baseline_sanity_profitable_sell_realized_pnl():
    """Sanity-check the fixture itself before exercising the reversal bug."""
    rows = _fixture()
    hype = next(p for p in compute_positions(rows) if p.asset == "HYPE")
    assert hype.quantity == Decimal("6")
    assert hype.cost_basis == Decimal("7200")   # 9600 - 2400 removed
    assert hype.realized_pnl == Decimal("869.25")  # 3344.25 - 2400 - 75


def test_KNOWN_LIMITATION_reversal_of_profitable_sell_distorts_cost_basis():
    """Pins the current (buggy-for-this-use-case) behaviour: reversing
    SELL1 (all 3 rows, one reversal group) does NOT restore the pre-sale
    state — cost_basis and realized_pnl end up different from a clean undo."""
    rows = _fixture()
    original_group = [r for r in rows if r.id == "SELL1"]

    reversal_rows = build_reversal_rows(original_group)
    rows_after = rows + reversal_rows

    hype_before_sale = compute_positions([r for r in rows if r.id != "SELL1"])
    hype_before = next(p for p in hype_before_sale if p.asset == "HYPE")

    hype_after_reversal = next(p for p in compute_positions(rows_after) if p.asset == "HYPE")

    # A CORRECT undo would restore exactly the pre-sale state:
    #   quantity=8, cost_basis=9600, realized_pnl=0
    # It does NOT:
    assert hype_before.quantity == Decimal("8")
    assert hype_before.cost_basis == Decimal("9600")
    assert hype_before.realized_pnl == Decimal("0")

    # quantity DOES restore correctly (this part is fine)...
    assert hype_after_reversal.quantity == Decimal("8")

    # ...but cost_basis and realized_pnl do NOT match the pre-sale state —
    # this is the documented distortion. If this assertion ever starts
    # failing because reversal semantics were intentionally fixed, update
    # this test to reflect the new (correct) behaviour deliberately.
    assert hype_after_reversal.cost_basis != Decimal("9600")
    assert hype_after_reversal.realized_pnl != Decimal("0")

    # Exact pinned values for this fixture, so any semantic change is caught.
    # Note the distortion here is actually WORSE than a simple "re-adds at
    # proceeds instead of true cost": build_reversal_rows() puts the SELL's
    # quote leg (-3344.25) AND its fee leg (+75) reversal into ONE shared
    # group id. compute_positions()'s fiat_by_id indexing can only hold ONE
    # fiat value per id (plain dict overwrite, not accumulation) — whichever
    # of the two fiat rows sorts last (here: the smaller +75 fee-reversal,
    # since positive amounts sort after negative in the tiebreak) silently
    # wins, so the HYPE reversal leg re-adds cost_basis of only 75 (not
    # 3344.25, and not the true removed cost of 2400 either). Cost basis
    # ends up UNDER the pre-sale value, not over it:
    assert hype_after_reversal.cost_basis == Decimal("7275")  # 7200 + 75, not 9600
    assert hype_after_reversal.realized_pnl == Decimal("869.25")  # unchanged — never "undone" at all
