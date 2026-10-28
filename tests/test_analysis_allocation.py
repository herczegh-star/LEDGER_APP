"""Tests for core/services/ui_facade.py — compute_allocation().

Pure function over an already-computed List[PositionDTO] — no DB, no
price provider, no fetching. Mirrors the Dashboard headline KPI scope
(compute_headline_positions(): excludes PRESALE_ASSETS) plus
quantity > 0 and value is not None.
"""
from __future__ import annotations

from decimal import Decimal

from core.services.ui_facade import AllocationDTO, PositionDTO, compute_allocation

_D = Decimal


def _pos(asset: str, quantity: str, value) -> PositionDTO:
    return PositionDTO(
        asset=asset,
        quantity=_D(quantity),
        wac=_D("1"),
        cost_basis=_D("1"),
        realized_pnl=_D("0"),
        value=_D(value) if value is not None else None,
    )


def test_basic_weight_calculation_two_assets():
    a = compute_allocation([_pos("BTC", "1", "75"), _pos("ETH", "1", "25")], top_n=5)
    assert [(r.asset, r.pct) for r in a.rows] == [("BTC", _D("75.0")), ("ETH", _D("25.0"))]
    assert a.other_pct == _D("0")
    assert a.top1_pct == _D("75.0")
    assert a.top3_pct == _D("100.0")  # only 2 assets exist, top3 = all of them


def test_top5_ranking_and_descending_order():
    positions = [
        _pos("UNI", "1", "2430"),
        _pos("ETH", "1", "1120"),
        _pos("BTC", "1", "810"),
        _pos("SOL", "1", "700"),
        _pos("DOT", "1", "600"),
        _pos("ATOM", "1", "2100"),
        _pos("AVAX", "1", "2240"),
    ]
    a = compute_allocation(positions, top_n=5)
    assert [r.asset for r in a.rows] == ["UNI", "AVAX", "ATOM", "ETH", "BTC"]
    # descending by value within the Top 5
    values = [r.value for r in a.rows]
    assert values == sorted(values, reverse=True)


def test_other_aggregates_remaining_positions_beyond_top_n():
    positions = [_pos(f"A{i}", "1", "100") for i in range(7)]  # 7 equal positions
    a = compute_allocation(positions, top_n=5)
    assert len(a.rows) == 5
    # 2 leftover assets out of 700 total value -> Other ~= 2/7 * 100,
    # largest-remainder rounding settles it at 28.5 (not a naive 28.6)
    assert a.other_pct == _D("28.5")
    assert sum((r.pct for r in a.rows), _D("0")) + a.other_pct == _D("100.0")


def test_fewer_than_top_n_positions_yields_zero_other():
    a = compute_allocation([_pos("BTC", "1", "60"), _pos("ETH", "1", "40")], top_n=5)
    assert len(a.rows) == 2
    assert a.other_pct == _D("0")


def test_value_none_excluded_from_rows_and_listed_as_excluded():
    positions = [_pos("BTC", "1", "100"), _pos("ETH", "1", None)]
    a = compute_allocation(positions, top_n=5)
    assert [r.asset for r in a.rows] == ["BTC"]
    assert a.rows[0].pct == _D("100.0")  # ETH excluded from denominator too, not treated as 0
    assert a.excluded_assets == ["ETH"]


def test_zero_quantity_closed_position_excluded():
    positions = [_pos("BTC", "1", "100"), _pos("OLD", "0", "0")]
    a = compute_allocation(positions, top_n=5)
    assert [r.asset for r in a.rows] == ["BTC"]
    assert a.excluded_assets == []  # OLD is dropped silently, not "excluded due to missing price"


def test_presale_assets_excluded_like_dashboard_headline():
    positions = [
        _pos("BTC", "1", "100"),
        _pos("TICS", "1", "999"),
        _pos("SOLX", "1", "999"),
    ]
    a = compute_allocation(positions, top_n=5)
    assert [r.asset for r in a.rows] == ["BTC"]
    assert a.other_pct == _D("0")
    assert a.excluded_assets == []  # presale is scope exclusion, not a "missing price" exclusion


def test_empty_portfolio_returns_empty_allocation():
    a = compute_allocation([], top_n=5)
    assert a == AllocationDTO(rows=[], other_pct=_D("0"), top1_pct=_D("0"), top3_pct=_D("0"), excluded_assets=[])


def test_all_values_none_returns_empty_allocation_not_zero_division():
    positions = [_pos("BTC", "1", None), _pos("ETH", "1", None)]
    a = compute_allocation(positions, top_n=5)
    assert a.rows == []
    assert sorted(a.excluded_assets) == ["BTC", "ETH"]


def test_top1_and_top3_independent_of_top_n_split():
    positions = [_pos(f"A{i}", "1", "100") for i in range(10)]
    a = compute_allocation(positions, top_n=5)
    assert a.top1_pct == _D("10.0")
    assert a.top3_pct == _D("30.0")


def test_rounding_sums_to_exactly_100_for_three_equal_thirds():
    """A naive independent round() of three equal 33.333...% shares would
    sum to 99.9 or 100.1 depending on rounding mode -- the largest-
    remainder method here must always land on exactly 100.0."""
    positions = [_pos("A", "1", "1"), _pos("B", "1", "1"), _pos("C", "1", "1")]
    a = compute_allocation(positions, top_n=5)
    assert sum((r.pct for r in a.rows), _D("0")) + a.other_pct == _D("100.0")


def test_rounding_sums_to_exactly_100_with_other_bucket():
    positions = [_pos(f"A{i}", "1", str(v)) for i, v in enumerate([2430, 1120, 810, 700, 600, 2100, 2240])]
    a = compute_allocation(positions, top_n=5)
    assert sum((r.pct for r in a.rows), _D("0")) + a.other_pct == _D("100.0")
