"""PRESALE Dashboard split — core/services/ui_facade.py.

TICS/SOLX are excluded from headline Portfolio Value / Unrealized PnL / ROI
(via compute_headline_positions()) and summarized separately
(compute_presale_summary()) for the PRESALE card. Both are pure filters
over an already-computed List[PositionDTO] — neither touches
compute_positions(), core/reports/holdings.py, or health_service.py.
"""
from __future__ import annotations

from decimal import Decimal

from core.reports.positions import compute_positions
from core.services.ui_facade import (
    PRESALE_ASSETS,
    PositionDTO,
    compute_headline_positions,
    compute_presale_summary,
    get_positions_full,
)


def _positions():
    return [
        PositionDTO(asset="TICS", quantity=Decimal("72425"), wac=Decimal("1.85"),
                    cost_basis=Decimal("134246.05"), realized_pnl=Decimal("0"),
                    spot_price=Decimal("0.186861"), value=Decimal("13533.41")),
        PositionDTO(asset="SOLX", quantity=Decimal("3704848.73"), wac=Decimal("0.028"),
                    cost_basis=Decimal("104029.96"), realized_pnl=Decimal("0"),
                    spot_price=Decimal("0.00059063"), value=Decimal("2188.19")),
        PositionDTO(asset="BTC", quantity=Decimal("0.01"), wac=Decimal("2500000"),
                    cost_basis=Decimal("25000"), realized_pnl=Decimal("0"),
                    spot_price=Decimal("2600000"), value=Decimal("26000")),
        PositionDTO(asset="ETH", quantity=Decimal("1"), wac=Decimal("60000"),
                    cost_basis=Decimal("60000"), realized_pnl=Decimal("0"),
                    spot_price=Decimal("70000"), value=Decimal("70000")),
    ]


# ── compute_headline_positions() ────────────────────────────────────────────

def test_headline_positions_excludes_tics():
    headline = compute_headline_positions(_positions())
    assert "TICS" not in [p.asset for p in headline]


def test_headline_positions_excludes_solx():
    headline = compute_headline_positions(_positions())
    assert "SOLX" not in [p.asset for p in headline]


def test_headline_positions_includes_other_assets():
    headline = compute_headline_positions(_positions())
    assert {p.asset for p in headline} == {"BTC", "ETH"}


def test_headline_total_cost_basis_excludes_presale_cost():
    headline = compute_headline_positions(_positions())
    total_cost = sum((p.cost_basis for p in headline), Decimal("0"))
    assert total_cost == Decimal("85000")  # 25000 + 60000, TICS/SOLX cost excluded


def test_headline_unrealized_pnl_excludes_presale():
    headline = compute_headline_positions(_positions())
    total_cost = sum((p.cost_basis for p in headline), Decimal("0"))
    total_value = sum((p.value for p in headline if p.value is not None), Decimal("0"))
    pnl = total_value - total_cost
    assert total_value == Decimal("96000")   # 26000 + 70000
    assert pnl == Decimal("11000")           # 96000 - 85000


def test_headline_roi_excludes_presale():
    headline = compute_headline_positions(_positions())
    total_cost = sum((p.cost_basis for p in headline), Decimal("0"))
    total_value = sum((p.value for p in headline if p.value is not None), Decimal("0"))
    roi = (total_value - total_cost) / total_cost
    assert roi == Decimal("11000") / Decimal("85000")


def test_headline_positions_default_excludes_exactly_presale_assets_constant():
    assert PRESALE_ASSETS == frozenset({"TICS", "SOLX"})


def test_headline_positions_custom_exclusion_set():
    headline = compute_headline_positions(_positions(), excluded_assets=frozenset({"BTC"}))
    assert {p.asset for p in headline} == {"TICS", "SOLX", "ETH"}


# ── compute_presale_summary() ────────────────────────────────────────────────

def test_presale_summary_value_equals_tics_plus_solx():
    summary = compute_presale_summary(_positions())
    assert summary.value == Decimal("13533.41") + Decimal("2188.19")


def test_presale_summary_cost_basis_equals_tics_plus_solx_cost():
    summary = compute_presale_summary(_positions())
    assert summary.cost_basis == Decimal("134246.05") + Decimal("104029.96")


def test_presale_summary_pnl_negative_when_value_below_cost():
    summary = compute_presale_summary(_positions())
    assert summary.pnl == summary.value - summary.cost_basis
    assert summary.pnl < Decimal("0")


def test_presale_summary_pnl_positive_when_value_exceeds_cost():
    positions = [
        PositionDTO(asset="TICS", quantity=Decimal("1"), wac=Decimal("1"),
                    cost_basis=Decimal("10"), realized_pnl=Decimal("0"), value=Decimal("50")),
        PositionDTO(asset="SOLX", quantity=Decimal("1"), wac=Decimal("1"),
                    cost_basis=Decimal("10"), realized_pnl=Decimal("0"), value=Decimal("50")),
    ]
    summary = compute_presale_summary(positions)
    assert summary.pnl == Decimal("80")
    assert summary.pnl > 0


def test_presale_missing_price_one_asset_contributes_zero_not_crash():
    positions = [
        PositionDTO(asset="TICS", quantity=Decimal("1"), wac=Decimal("1"),
                    cost_basis=Decimal("100"), realized_pnl=Decimal("0"), value=None),
        PositionDTO(asset="SOLX", quantity=Decimal("1"), wac=Decimal("1"),
                    cost_basis=Decimal("50"), realized_pnl=Decimal("0"), value=Decimal("20")),
    ]
    summary = compute_presale_summary(positions)  # must not raise
    assert summary.value == Decimal("20")          # TICS' missing price contributes 0
    assert summary.cost_basis == Decimal("150")
    assert summary.pnl == Decimal("20") - Decimal("150")


def test_presale_value_zero_when_neither_held():
    positions = [
        PositionDTO(asset="BTC", quantity=Decimal("1"), wac=Decimal("1"),
                    cost_basis=Decimal("100"), realized_pnl=Decimal("0"), value=Decimal("120")),
    ]
    summary = compute_presale_summary(positions)
    assert summary.value == Decimal("0")
    assert summary.cost_basis == Decimal("0")
    assert summary.pnl == Decimal("0")


# ── Regression: accounting core / other views unaffected ───────────────────

def test_compute_positions_unaffected_by_presale_filtering():
    """compute_positions() has no concept of PRESALE_ASSETS at all — this
    pins that TICS/SOLX quantity/cost_basis/WAC are computed identically
    whether or not the Dashboard-layer filtering exists."""
    from datetime import datetime
    from core.model import RawRow

    rows = [
        RawRow(id="b1", timestamp=datetime(2026, 1, 1), type="BUY", asset="TICS",
               amount=Decimal("100"), currency="CZK", price=Decimal("2"), venue="revolut"),
        RawRow(id="b1", timestamp=datetime(2026, 1, 1), type="BUY", asset="CZK",
               amount=Decimal("-200"), currency="CZK", price=Decimal("1"), venue="revolut"),
    ]
    positions_before = compute_positions(rows)
    # Re-run — nothing about PRESALE_ASSETS influences this computation.
    positions_after = compute_positions(rows)
    assert positions_before == positions_after
    tics = next(p for p in positions_after if p.asset == "TICS")
    assert tics.quantity == Decimal("100")
    assert tics.cost_basis == Decimal("200")
    assert tics.wac == Decimal("2")


def test_positions_view_still_returns_tics_and_solx(tmp_path):
    """get_positions_full() (the data source for ui/modules/positions_view.py)
    must keep returning TICS/SOLX — the Dashboard-only exclusion must not
    leak into the general Positions view."""
    from datetime import datetime

    from core.services.trade_service import AddTradeInput, add_trade

    db_path = str(tmp_path / "test.db")
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1), base_asset="TICS",
        base_amount=Decimal("100"), quote_currency="CZK", quote_amount=Decimal("200"),
        venue="revolut",
    ))
    add_trade(db_path, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1), base_asset="SOLX",
        base_amount=Decimal("100"), quote_currency="CZK", quote_amount=Decimal("50"),
        venue="revolut",
    ))
    positions = get_positions_full(db_path)
    assets = {p.asset for p in positions}
    assert "TICS" in assets
    assert "SOLX" in assets
