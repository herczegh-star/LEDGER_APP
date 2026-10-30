"""Tests for ui/modules/portfolio_value_chart.py — compute_chart_layout().

Pure, Flet-free layout math (dataclasses + Decimal) — no running Flet page
needed. The actual ft.canvas widget construction (build_portfolio_value_chart)
is exercised separately by a lightweight smoke test at the bottom of this
file; its pixel output is not asserted (that would just re-test Flet itself).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.services.portfolio_history_service import PortfolioValueTrendPointDTO
from ui.modules.portfolio_value_chart import build_portfolio_value_chart, compute_chart_layout

_D1 = date(2026, 10, 1)
_D2 = date(2026, 10, 2)
_D3 = date(2026, 10, 3)
_D4 = date(2026, 10, 4)
_D5 = date(2026, 10, 5)


def _point(d, value, source="backfill"):
    return PortfolioValueTrendPointDTO(
        date=d,
        portfolio_value=value,
        cost_basis=Decimal("100000"),
        unrealized_pnl=(value - Decimal("100000")) if value is not None else None,
        roi_total=None,
        source=source,
    )


# ── Empty / sparse data ──────────────────────────────────────────────────────

def test_empty_trend_returns_none_layout():
    layout = compute_chart_layout([], _D1, _D5, 880, 280)
    assert layout is None


def test_all_points_missing_value_returns_none_layout():
    """Every day in range has portfolio_value=None (e.g. no priced
    positions at all) — nothing plottable, not a crash."""
    points = [_point(_D1, None), _point(_D2, None)]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280)
    assert layout is None


def test_single_point_produces_a_valid_layout():
    points = [_point(_D1, Decimal("900000"))]
    layout = compute_chart_layout(points, _D1, _D1, 880, 280)
    assert layout is not None
    assert len(layout.points) == 1
    assert layout.points[0].value == Decimal("900000")
    # y_min/y_max must not collapse to a zero-height range for a flat/single series
    assert layout.y_max > layout.y_min


def test_missing_day_is_skipped_not_inserted_as_zero():
    """A day with portfolio_value=None must be absent from plotted points —
    never silently turned into a 0 value point."""
    points = [
        _point(_D1, Decimal("900000")),
        _point(_D2, None),   # gap
        _point(_D3, Decimal("950000")),
    ]
    layout = compute_chart_layout(points, _D1, _D3, 880, 280)
    plotted_values = [p.value for p in layout.points]
    assert Decimal("0") not in plotted_values
    assert len(layout.points) == 2  # only the two known days
    assert layout.points[0].date == _D1
    assert layout.points[1].date == _D3


# ── backfill vs live_snapshot distinction ───────────────────────────────────

def test_no_live_snapshot_means_live_point_is_none():
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("910000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280)
    assert layout.live_point is None
    assert layout.last_backfill is None


def test_trailing_live_snapshot_is_identified_as_live_point():
    points = [
        _point(_D1, Decimal("900000"), source="backfill"),
        _point(_D2, Decimal("910000"), source="backfill"),
        _point(_D3, Decimal("935000"), source="live_snapshot"),
    ]
    layout = compute_chart_layout(points, _D1, _D3, 880, 280)
    assert layout.live_point is not None
    assert layout.live_point.date == _D3
    assert layout.live_point.source == "live_snapshot"
    assert layout.last_backfill is not None
    assert layout.last_backfill.date == _D2


def test_live_point_with_no_preceding_backfill_point_has_no_connector():
    """Only one point total, and it's live — nothing to draw a dashed
    connector from; must not crash or fabricate a second point."""
    points = [_point(_D1, Decimal("900000"), source="live_snapshot")]
    layout = compute_chart_layout(points, _D1, _D1, 880, 280)
    assert layout.live_point is not None
    assert layout.last_backfill is None


def test_backfill_only_history_renders_normally_without_live_point():
    """Pure backfill history (no refresh since deployment, or querying a
    past range that ends before any live snapshot) — must still produce
    a normal layout, not be treated as an error."""
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("905000")), _point(_D3, Decimal("901000"))]
    layout = compute_chart_layout(points, _D1, _D3, 880, 280)
    assert layout is not None
    assert layout.live_point is None
    assert len(layout.points) == 3


# ── Y-axis range (no forced zero floor) ──────────────────────────────────────

def test_y_axis_does_not_force_zero_minimum():
    points = [_point(_D1, Decimal("980000")), _point(_D2, Decimal("1010000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280)
    assert layout.y_min > Decimal("0")
    assert layout.y_min < Decimal("980000")  # some padding below the real min
    assert layout.y_max > Decimal("1010000")  # some padding above the real max


def test_flat_series_does_not_collapse_y_range():
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("900000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280)
    assert layout.y_max > layout.y_min


# ── Pixel sanity ─────────────────────────────────────────────────────────────

def test_points_stay_within_canvas_bounds():
    points = [_point(_D1, Decimal("900000")), _point(_D3, Decimal("950000")), _point(_D5, Decimal("920000"))]
    width, height = 880, 280
    layout = compute_chart_layout(points, _D1, _D5, width, height)
    for p in layout.points:
        assert 0 <= p.x <= width
        assert 0 <= p.y <= height


def test_x_labels_do_not_exceed_max_label_count():
    points = [_point(_D1 .replace(day=d), Decimal("900000") + d) for d in range(1, 32)]
    layout = compute_chart_layout(points, points[0].date, points[-1].date, 880, 280)
    assert len(layout.x_labels) <= 6


# ── build_portfolio_value_chart(): smoke test (widget construction only) ────

def test_build_chart_returns_placeholder_control_on_empty_data():
    ctrl = build_portfolio_value_chart([], _D1, _D5)
    assert ctrl is not None  # a Container placeholder, not a crash


def test_build_chart_returns_canvas_control_with_data():
    import flet as ft
    import flet.canvas as cv
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("910000"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D2)
    # Stack([canvas, GestureDetector]) -- the GestureDetector wraps a
    # full-size transparent Container (not the canvas) so a click fires
    # across the whole chart rectangle, not just where a shape is painted.
    assert isinstance(ctrl, ft.Stack)
    assert isinstance(ctrl.controls[0], cv.Canvas)
    assert isinstance(ctrl.controls[1], ft.GestureDetector)


# ── metric="pnl" / "roi" — PnL/ROI Trend (shares the same layout engine) ────

def _pr_point(d, value=None, pnl=None, roi=None, source="backfill"):
    """Like _point(), but lets unrealized_pnl / roi_total be set
    independently of portfolio_value — needed for PnL/ROI-specific tests."""
    return PortfolioValueTrendPointDTO(
        date=d, portfolio_value=value, cost_basis=Decimal("100000"),
        unrealized_pnl=pnl, roi_total=roi, source=source,
    )


def test_default_metric_is_value_unchanged():
    """Backward compatibility: callers that don't pass metric= (e.g. the
    existing Portfolio Value Trend call site) must keep plotting
    portfolio_value exactly as before."""
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("950000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280)
    assert [p.value for p in layout.points] == [Decimal("900000"), Decimal("950000")]


def test_pnl_metric_plots_unrealized_pnl_field_not_portfolio_value():
    points = [
        _pr_point(_D1, value=Decimal("900000"), pnl=Decimal("-100000")),
        _pr_point(_D2, value=Decimal("950000"), pnl=Decimal("-50000")),
    ]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="pnl")
    assert [p.value for p in layout.points] == [Decimal("-100000"), Decimal("-50000")]


def test_roi_metric_plots_roi_total_field():
    points = [
        _pr_point(_D1, roi=Decimal("-0.10")),
        _pr_point(_D2, roi=Decimal("0.05")),
    ]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="roi")
    assert [p.value for p in layout.points] == [Decimal("-0.10"), Decimal("0.05")]


def test_pnl_metric_skips_days_with_no_pnl_not_zero():
    points = [
        _pr_point(_D1, pnl=Decimal("10000")),
        _pr_point(_D2, pnl=None),   # no priced position that day
        _pr_point(_D3, pnl=Decimal("20000")),
    ]
    layout = compute_chart_layout(points, _D1, _D3, 880, 280, metric="pnl")
    assert len(layout.points) == 2
    assert Decimal("0") not in [p.value for p in layout.points]


def test_pnl_axis_labels_are_signed():
    points = [_pr_point(_D1, pnl=Decimal("-50000")), _pr_point(_D2, pnl=Decimal("30000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="pnl")
    labels = [text for _, text in layout.y_labels]
    assert any(t.startswith("+") for t in labels)
    assert any(t.startswith("-") for t in labels)


def test_roi_axis_labels_use_percent():
    points = [_pr_point(_D1, roi=Decimal("-0.10")), _pr_point(_D2, roi=Decimal("0.05"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="roi")
    labels = [text for _, text in layout.y_labels]
    assert all("%" in t for t in labels)


def test_zero_line_present_when_pnl_crosses_zero():
    points = [_pr_point(_D1, pnl=Decimal("-50000")), _pr_point(_D2, pnl=Decimal("30000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="pnl")
    assert layout.zero_y is not None
    assert 0 <= layout.zero_y <= 280


def test_zero_line_absent_when_pnl_stays_entirely_positive():
    points = [_pr_point(_D1, pnl=Decimal("10000")), _pr_point(_D2, pnl=Decimal("30000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="pnl")
    assert layout.zero_y is None


def test_zero_line_absent_when_pnl_stays_entirely_negative():
    points = [_pr_point(_D1, pnl=Decimal("-10000")), _pr_point(_D2, pnl=Decimal("-30000"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="pnl")
    assert layout.zero_y is None


def test_zero_line_crossing_also_detected_for_roi():
    points = [_pr_point(_D1, roi=Decimal("-0.05")), _pr_point(_D2, roi=Decimal("0.03"))]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="roi")
    assert layout.zero_y is not None


def test_pnl_live_point_still_detected_same_as_value_metric():
    points = [
        _pr_point(_D1, pnl=Decimal("10000"), source="backfill"),
        _pr_point(_D2, pnl=Decimal("15000"), source="live_snapshot"),
    ]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280, metric="pnl")
    assert layout.live_point is not None
    assert layout.live_point.date == _D2


def test_build_chart_roi_metric_returns_canvas_control():
    import flet as ft
    import flet.canvas as cv
    points = [_pr_point(_D1, roi=Decimal("-0.1")), _pr_point(_D2, roi=Decimal("0.05"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D2, metric="roi")
    assert isinstance(ctrl, ft.Stack)
    assert isinstance(ctrl.controls[0], cv.Canvas)
    assert isinstance(ctrl.controls[1], ft.GestureDetector)


def test_build_chart_pnl_metric_placeholder_on_no_data():
    ctrl = build_portfolio_value_chart([], _D1, _D2, metric="pnl")
    assert ctrl is not None


# ── Click selection: nearest-point lookup + persistent marker/tooltip ──────
# CLICK = selection, HOVER = nothing (by explicit spec). Selection always
# snaps to one real daily point (never interpolated); see find_nearest_point().

class _FakeOffset:
    def __init__(self, x, y=0):
        self.x = x
        self.y = y


class _FakeTapDownEvent:
    def __init__(self, x, y=0):
        self.local_position = _FakeOffset(x, y)


def _canvas_of(ctrl):
    return ctrl.controls[0]  # Stack([canvas, GestureDetector])


def _gesture_of(ctrl):
    return ctrl.controls[1]


def _text_values(canvas) -> list:
    import flet.canvas as cv
    return [s.value for s in canvas.shapes if isinstance(s, cv.Text)]


def test_find_nearest_point_snaps_to_closer_of_two_points():
    from ui.modules.portfolio_value_chart import find_nearest_point
    points = [_point(_D1, Decimal("900000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    x = layout.points[0].x + (layout.points[1].x - layout.points[0].x) * 0.3
    nearest = find_nearest_point(layout, x)
    assert nearest.date == layout.points[0].date


def test_find_nearest_point_on_no_points_returns_none():
    from ui.modules.portfolio_value_chart import ChartLayout, find_nearest_point
    empty_layout = ChartLayout(
        points=[], last_backfill=None, live_point=None,
        y_min=Decimal("0"), y_max=Decimal("1"), x_labels=[], y_labels=[],
        zero_y=None, label_fmt=lambda v: str(v),
    )
    assert find_nearest_point(empty_layout, 100) is None


def test_no_hover_handler_exists_moving_mouse_does_nothing():
    """Explicit spec: hover must not be wired at all. The GestureDetector
    has no on_hover/on_exit handler, so mouse movement alone can never
    trigger anything."""
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("910000"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D2)
    gesture = _gesture_of(ctrl)
    assert gesture.on_hover is None
    assert gesture.on_exit is None
    assert gesture.on_tap_down is not None


def test_click_on_first_point_shows_its_date():
    points = [_point(_D1, Decimal("900000")), _point(_D3, Decimal("950000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D5)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(layout.points[0].x))
    assert any("01.10.2026" in t for t in _text_values(_canvas_of(ctrl)))


def test_click_on_last_point_shows_its_date():
    points = [_point(_D1, Decimal("900000")), _point(_D3, Decimal("950000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D5)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(layout.points[-1].x))
    assert any("05.10.2026" in t for t in _text_values(_canvas_of(ctrl)))


def test_click_between_two_points_snaps_to_nearer_one_not_interpolated():
    points = [_point(_D1, Decimal("900000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D5)
    x = layout.points[0].x + (layout.points[1].x - layout.points[0].x) * 0.3
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(x))
    texts = _text_values(_canvas_of(ctrl))
    assert any("01.10.2026" in t for t in texts)
    assert not any("05.10.2026" in t for t in texts)


def test_click_far_above_the_line_still_picks_correct_point_by_x():
    """Y must never gate or change which point is selected — clicking
    anywhere vertically over a date's X position must resolve to that
    date, even dozens of px above the drawn line."""
    points = [_point(_D1, Decimal("900000")), _point(_D3, Decimal("950000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D5)
    mid_x = layout.points[1].x
    far_above_y = max(layout.points[1].y - 150, 0)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(mid_x, far_above_y))
    assert any("03.10.2026" in t for t in _text_values(_canvas_of(ctrl)))


def test_click_far_below_the_line_still_picks_correct_point_by_x():
    points = [_point(_D1, Decimal("900000")), _point(_D3, Decimal("950000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D5)
    mid_x = layout.points[1].x
    far_below_y = layout.points[1].y + 150
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(mid_x, far_below_y))
    assert any("03.10.2026" in t for t in _text_values(_canvas_of(ctrl)))


def test_click_tooltip_formats_czk_value():
    points = [_point(_D1, Decimal("935510"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D1)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(440))
    assert any("935 510 CZK" in t for t in _text_values(_canvas_of(ctrl)))


def test_click_tooltip_formats_roi_percentage():
    points = [_pr_point(_D1, roi=Decimal("-0.309"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D1, metric="roi")
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(440))
    texts = _text_values(_canvas_of(ctrl))
    assert any("%" in t and "-30" in t for t in texts)


def test_click_on_live_point_shows_live_tag():
    points = [
        _point(_D1, Decimal("900000"), source="backfill"),
        _point(_D2, Decimal("910000"), source="live_snapshot"),
    ]
    layout = compute_chart_layout(points, _D1, _D2, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D2)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(layout.live_point.x))
    assert "Live" in _text_values(_canvas_of(ctrl))


def test_selection_is_persistent_no_handler_clears_it():
    """No on_exit (or any other handler) exists to clear the selection --
    it must survive indefinitely once set, matching the persistent-
    selection spec (no hover-driven clearing)."""
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("910000"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D2)
    canvas = _canvas_of(ctrl)
    base_count = len(canvas.shapes)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(100))
    after_click_count = len(canvas.shapes)
    assert after_click_count > base_count
    # No exit handler to call -- simulate "mouse leaves / time passes" by
    # simply re-reading the shapes; nothing should change them.
    assert len(canvas.shapes) == after_click_count
    assert gesture_has_no_exit_handler(ctrl)


def gesture_has_no_exit_handler(ctrl) -> bool:
    return _gesture_of(ctrl).on_exit is None


def test_second_click_moves_selection_to_new_point_not_additive():
    points = [_point(_D1, Decimal("900000")), _point(_D3, Decimal("950000")), _point(_D5, Decimal("920000"))]
    layout = compute_chart_layout(points, _D1, _D5, 880, 280)
    ctrl = build_portfolio_value_chart(points, _D1, _D5)
    canvas = _canvas_of(ctrl)
    gesture = _gesture_of(ctrl)

    gesture.on_tap_down(_FakeTapDownEvent(layout.points[0].x))
    count_after_first_click = len(canvas.shapes)
    assert any("01.10.2026" in t for t in _text_values(canvas))

    gesture.on_tap_down(_FakeTapDownEvent(layout.points[-1].x))
    texts = _text_values(canvas)
    assert any("05.10.2026" in t for t in texts)
    assert not any("01.10.2026" in t for t in texts)  # old selection replaced, not stacked
    assert len(canvas.shapes) == count_after_first_click  # same shape count, not accumulating


def test_click_hit_area_is_a_fully_transparent_full_size_container():
    """The GestureDetector must wrap an opaque-for-hit-testing (even if
    visually transparent) full-size Container -- not the canvas itself --
    otherwise Flutter only fires on_tap_down where a shape is painted."""
    import flet as ft
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("910000"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D2, width=880, height=280)
    gesture = _gesture_of(ctrl)
    assert isinstance(gesture.content, ft.Container)
    assert gesture.content.width == 880 and gesture.content.height == 280
    assert gesture.content.bgcolor == ft.Colors.TRANSPARENT


def test_click_does_not_trigger_any_new_data_fetch_or_recompute():
    """Click selection must work purely off already-computed pixel
    positions -- no portfolio recomputation, no new service call."""
    points = [_point(_D1, Decimal("900000")), _point(_D2, Decimal("910000"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D2)
    calls = {"n": 0}

    def _spy(*args, **kwargs):
        calls["n"] += 1
        return compute_chart_layout(*args, **kwargs)

    import ui.modules.portfolio_value_chart as pvc
    original = pvc.compute_chart_layout
    pvc.compute_chart_layout = _spy
    try:
        _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(100))
        _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(200))
    finally:
        pvc.compute_chart_layout = original
    assert calls["n"] == 0  # click never re-enters layout computation


def test_click_on_single_point_chart_does_not_crash():
    points = [_point(_D1, Decimal("900000"))]
    ctrl = build_portfolio_value_chart(points, _D1, _D1)
    _gesture_of(ctrl).on_tap_down(_FakeTapDownEvent(50))  # must not raise


def test_click_on_empty_chart_returns_plain_placeholder_without_interaction():
    import flet as ft
    ctrl = build_portfolio_value_chart([], _D1, _D5)
    assert isinstance(ctrl, ft.Container)
