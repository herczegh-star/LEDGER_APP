"""Portfolio Value Trend — a minimal reusable flet.canvas line chart.

Public API:
    build_portfolio_value_chart(trend_points, date_from, date_to, width, height) -> ft.Control

No data fetching or portfolio computation happens here — the caller
passes an already-fetched List[PortfolioValueTrendPointDTO] (from
core.services.ui_facade.get_portfolio_value_trend()). This module only
turns that DTO list into pixels.

Flet 0.80.5 has no built-in chart widget (confirmed during the Etapa 1
spike — grep for "class.*Chart" across the installed package returns
zero matches). flet.canvas (Line/Circle/Text + Paint) is the lowest-
level drawing primitive available, so this is a hand-built line chart,
not a general chart framework — just what this one chart needs.

compute_chart_layout() is a pure, Flet-free function (dataclasses +
Decimal math only) so the pixel-layout logic is unit-testable without a
running Flet page — see tests/test_portfolio_value_chart.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

import flet as ft
import flet.canvas as cv

# ── Color palette (mirrors app_flet.py / analysis_view.py) ──────────────────
BG_CARD   = "#0f1621"
BORDER    = "#1e293b"
T_PRI     = "#e2e8f0"
T_MUT     = "#7b8799"
GRID      = "#1f2a3a"
LINE_COLOR = "#1d4ed8"   # BLUE — backfill (historical, daily CoinGecko price) line
LIVE_COLOR = "#f59e0b"   # distinct accent — live snapshot point + its connector

_PAD_LEFT = 54
_PAD_RIGHT = 12
_PAD_TOP = 16
_PAD_BOTTOM = 26
_MAX_X_LABELS = 6
_Y_RANGE_PADDING_FRACTION = Decimal("0.08")  # 8% breathing room above max / below min


@dataclass
class _PlottedPoint:
    x: float
    y: float
    date: date
    value: Decimal
    source: str  # "backfill" | "live_snapshot"


@dataclass
class ChartLayout:
    """Pure pixel-layout result — no Flet objects, fully comparable/assertable in tests."""

    points: List[_PlottedPoint]
    last_backfill: Optional[_PlottedPoint]  # point the dashed live-connector starts from
    live_point: Optional[_PlottedPoint]     # None when no live_snapshot day is in range
    y_min: Decimal
    y_max: Decimal
    x_labels: List[Tuple[float, str]]
    y_labels: List[Tuple[float, str]]


def _fmt_czk_axis(v: Decimal) -> str:
    """Compact Y-axis tick label, e.g. '1.02 M' / '891 k' / '320'."""
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= Decimal("1000000"):
        return f"{sign}{av / Decimal('1000000'):.2f} M"
    if av >= Decimal("1000"):
        return f"{sign}{av / Decimal('1000'):.0f} k"
    return f"{sign}{av:.0f}"


def _fmt_czk(v: Decimal) -> str:
    """Full-precision CZK label for the live marker's value callout —
    deliberately NOT compacted, unlike the Y-axis ticks (see _fmt_czk_axis)."""
    return f"{v:,.0f}".replace(",", " ") + " CZK"


def compute_chart_layout(
    trend_points,  # List[PortfolioValueTrendPointDTO]
    date_from: date,
    date_to: date,
    width: float,
    height: float,
) -> Optional[ChartLayout]:
    """Pure layout computation. Returns None when there is nothing
    plottable (caller renders the "No portfolio history available yet."
    placeholder instead).

    Points whose portfolio_value is None are skipped entirely — never
    plotted as 0 (per the data contract: missing data stays missing).
    The X axis is time-proportional across [date_from, date_to] so an
    irregular gap in the middle does not distort spacing.
    """
    plottable = [p for p in trend_points if p.portfolio_value is not None]
    if not plottable:
        return None

    values = [p.portfolio_value for p in plottable]
    y_min_raw, y_max_raw = min(values), max(values)
    if y_min_raw == y_max_raw:
        # Flat series or a single point — fabricate a small range so the
        # plot isn't a zero-height line sitting exactly on the axis.
        pad = (abs(y_min_raw) * Decimal("0.05")) if y_min_raw != 0 else Decimal("1")
        y_min_raw, y_max_raw = y_min_raw - pad, y_max_raw + pad
    value_range = y_max_raw - y_min_raw
    y_pad = value_range * _Y_RANGE_PADDING_FRACTION
    y_min, y_max = y_min_raw - y_pad, y_max_raw + y_pad

    plot_w = max(width - _PAD_LEFT - _PAD_RIGHT, 1.0)
    plot_h = max(height - _PAD_TOP - _PAD_BOTTOM, 1.0)
    total_days = max((date_to - date_from).days, 1)
    y_span = y_max - y_min

    def to_xy(p) -> _PlottedPoint:
        x = _PAD_LEFT + (p.date - date_from).days / total_days * plot_w
        frac = float((p.portfolio_value - y_min) / y_span)
        y = _PAD_TOP + (1 - frac) * plot_h
        return _PlottedPoint(x=x, y=y, date=p.date, value=p.portfolio_value, source=p.source)

    plotted = [to_xy(p) for p in plottable]

    live_point = plotted[-1] if plottable[-1].source == "live_snapshot" else None
    last_backfill = plotted[-2] if (live_point is not None and len(plotted) >= 2) else None

    n = len(plotted)
    if n > 1:
        idxs = sorted({round(i * (n - 1) / max(_MAX_X_LABELS - 1, 1)) for i in range(_MAX_X_LABELS)})
    else:
        idxs = [0]
    x_labels = [(plotted[i].x, plotted[i].date.strftime("%d.%m")) for i in idxs]

    y_labels = [
        (_PAD_TOP, _fmt_czk_axis(y_max)),
        (_PAD_TOP + plot_h / 2, _fmt_czk_axis((y_max + y_min) / 2)),
        (_PAD_TOP + plot_h, _fmt_czk_axis(y_min)),
    ]

    return ChartLayout(
        points=plotted, last_backfill=last_backfill, live_point=live_point,
        y_min=y_min, y_max=y_max, x_labels=x_labels, y_labels=y_labels,
    )


def _build_canvas(layout: ChartLayout, width: float, height: float) -> ft.Control:
    shapes: list = []

    # Horizontal gridlines at each Y label
    for y_px, _ in layout.y_labels:
        shapes.append(cv.Line(
            _PAD_LEFT, y_px, width - _PAD_RIGHT, y_px,
            paint=ft.Paint(color=GRID, stroke_width=1),
        ))

    # Solid backfill line — everything except the final dashed live segment
    line_points = layout.points[:-1] if layout.live_point is not None else layout.points
    for p1, p2 in zip(line_points, line_points[1:]):
        shapes.append(cv.Line(
            p1.x, p1.y, p2.x, p2.y,
            paint=ft.Paint(color=LINE_COLOR, stroke_width=2, style=ft.PaintingStyle.STROKE),
        ))

    # Dashed connector into the live point — must not look like an ordinary
    # daily move (see validation report's "DAILY PRICE BOUNDARY" finding).
    if layout.live_point is not None and layout.last_backfill is not None:
        shapes.append(cv.Line(
            layout.last_backfill.x, layout.last_backfill.y,
            layout.live_point.x, layout.live_point.y,
            paint=ft.Paint(color=LIVE_COLOR, stroke_width=2, stroke_dash_pattern=[4, 4]),
        ))

    # Single-point series: nothing else would render — draw one marker.
    if layout.live_point is None and len(layout.points) == 1:
        p = layout.points[0]
        shapes.append(cv.Circle(p.x, p.y, 4, paint=ft.Paint(color=LINE_COLOR, style=ft.PaintingStyle.FILL)))

    # Live marker, clearly distinct from the backfill line.
    if layout.live_point is not None:
        lp = layout.live_point
        shapes.append(cv.Circle(lp.x, lp.y, 5, paint=ft.Paint(color=LIVE_COLOR, style=ft.PaintingStyle.FILL)))
        label_x = max(_PAD_LEFT, min(lp.x - 50, width - _PAD_RIGHT - 100))
        shapes.append(cv.Text(
            label_x, max(lp.y - 22, 2), _fmt_czk(lp.value),
            style=ft.TextStyle(size=11, color=LIVE_COLOR, weight=ft.FontWeight.W_600),
        ))

    # Axis labels
    for x_px, label in layout.x_labels:
        shapes.append(cv.Text(
            max(x_px - 14, 0), height - _PAD_BOTTOM + 6, label,
            style=ft.TextStyle(size=10, color=T_MUT),
        ))
    for y_px, label in layout.y_labels:
        shapes.append(cv.Text(2, y_px - 6, label, style=ft.TextStyle(size=10, color=T_MUT)))

    return cv.Canvas(shapes, width=width, height=height)


def build_portfolio_value_chart(
    trend_points,  # List[PortfolioValueTrendPointDTO]
    date_from: date,
    date_to: date,
    width: float = 880,
    height: float = 280,
) -> ft.Control:
    """Build the full chart control (or a placeholder) for the given
    already-fetched trend points. Never raises on empty/sparse data."""
    layout = compute_chart_layout(trend_points, date_from, date_to, width, height)
    if layout is None:
        return ft.Container(
            content=ft.Text("No portfolio history available yet.", size=13, color=T_MUT),
            alignment=ft.Alignment.CENTER,
            width=width,
            height=height,
        )
    return _build_canvas(layout, width, height)
