"""Analysis page — Portfolio Value Trend + Sell Simulator.

Public API:
    build_analysis_view(page, db_path, price_provider=None, fiat="CZK")
        -> (ft.Container view, callable run_fn)

Portfolio Value Trend (top section) renders core.services.ui_facade.
get_portfolio_value_trend() through ui.modules.portfolio_value_chart — this
module does NOT compute portfolio value itself, only period-range picking
(7D/30D/90D/1Y/ALL -> date_from/date_to) and rendering.

The Sell Simulator (bottom section, unchanged) projects simulated PnL if
all matching positions in a selected venue were sold at the current spot
price.  Read-only: no ledger writes.  Uses PositionDTO.unrealized_pnl
(= value − cost_basis) directly.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import flet as ft

from core.services.ui_facade import (
    get_dashboard_snapshot, get_earliest_ledger_date, get_portfolio_contributors,
    get_portfolio_value_trend, run_sell_simulation,
)
from ui.modules.portfolio_value_chart import build_portfolio_value_chart
from core.asset_display import display_asset

# ── Color palette (mirrors app_flet.py) ─────────────────────────────────────
BG        = "#0b0f14"
BG_CARD   = "#0f1621"
BG_HDR    = "#0d1117"
BORDER    = "#1e293b"
T_PRI     = "#e2e8f0"
T_MUT     = "#7b8799"
GREEN     = "#16a34a"
RED       = "#ef4444"
BLUE      = "#1d4ed8"

_ZERO = Decimal("0")
_STABLECOINS = frozenset({"USDC", "USDT"})

_MODES = [
    ("all",             "All Positions"),
    ("profitable_only", "Profitable Only"),
    ("loss_only",       "Loss Only"),
]

_PNL_ROI_METRICS = [
    ("pnl", "PnL CZK"),
    ("roi", "ROI %"),
]
_DEFAULT_PNL_ROI_METRIC = "pnl"

# (period key, trailing day count; None = "since the ledger's earliest row")
_TREND_PERIODS = [
    ("7D",  7),
    ("30D", 30),
    ("90D", 90),
    ("1Y",  365),
    ("ALL", None),
]
_DEFAULT_TREND_PERIOD = "30D"


def _trend_period_range(period_key: str, earliest_ledger_date) -> tuple:
    """Pure date-range picking — not a portfolio calculation. ALL falls
    back to today (empty range) when the ledger has no rows at all."""
    today = date.today()
    days = dict(_TREND_PERIODS).get(period_key)
    if days is None:  # "ALL"
        return (earliest_ledger_date or today), today
    return today - timedelta(days=days - 1), today


# ── Format helpers ────────────────────────────────────────────────────────────

def _czk(v: Optional[Decimal], sign: bool = False) -> str:
    if v is None:
        return "—"
    a = abs(v)
    places = 0 if a >= 1000 else (2 if a >= 1 else 4)
    n = f"{a:,.{places}f}".replace(",", " ")
    if v < 0:
        return f"-{n} CZK"
    return f"+{n} CZK" if sign else f"{n} CZK"


def _pct(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{float(v) * 100:.2f}%"


def _amt(v: Decimal, asset: str) -> str:
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return f"{s} {asset}"


def _color(v: Optional[Decimal]) -> str:
    if v is None:
        return T_MUT
    return GREEN if v >= 0 else RED


# ── View builder ──────────────────────────────────────────────────────────────

def build_analysis_view(
    page: ft.Page,
    db_path: str,
    price_provider=None,
    fiat: str = "CZK",
):
    """Build the Analysis / Sell Simulator view.

    Returns:
        (view_container, run_fn)
        run_fn() is called when the tab is selected; it refreshes venue list.
    """
    snap_holder: list = [None]

    # ── Portfolio Value Trend ────────────────────────────────────────────────
    period_dd = ft.Dropdown(
        label="Period",
        options=[ft.dropdown.Option(k, k) for k, _ in _TREND_PERIODS],
        value=_DEFAULT_TREND_PERIOD,
        width=130,
        text_size=13,
    )
    trend_chart_container = ft.Container()
    # Shared period state for BOTH Portfolio Value Trend and PnL/ROI Trend —
    # a period change re-fetches once and redraws both charts; a metric
    # change (PnL CZK <-> ROI %) only re-renders the second chart from this
    # already-fetched cache, no extra get_portfolio_value_trend() call.
    trend_state = {"points": [], "date_from": None, "date_to": None}

    def _load_trend(e=None) -> None:
        period = period_dd.value or _DEFAULT_TREND_PERIOD
        earliest = get_earliest_ledger_date(db_path)
        date_from, date_to = _trend_period_range(period, earliest)
        trend_points = get_portfolio_value_trend(db_path, date_from, date_to)
        trend_state["points"] = trend_points
        trend_state["date_from"] = date_from
        trend_state["date_to"] = date_to
        trend_chart_container.content = build_portfolio_value_chart(trend_points, date_from, date_to, metric="value")
        _render_pnl_roi_chart()
        _render_contributors()
        page.update()

    period_dd.on_select = _load_trend  # ft.Dropdown fires on_select, not on_change (Flet 0.80.5)

    trend_section = ft.Column(
        [
            ft.Row(
                [
                    ft.Text("Portfolio Value Trend", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
                    period_dd,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Container(height=8),
            ft.Container(
                content=trend_chart_container,
                bgcolor=BG_CARD,
                border=ft.border.all(1, BORDER),
                border_radius=10,
                padding=16,
            ),
            ft.Container(height=4),
            ft.Text(
                "Historical = daily prices • Latest = live value",
                size=10,
                color=T_MUT,
                italic=True,
            ),
        ],
        spacing=4,
    )

    # ── PnL / ROI Trend (shares period_dd / trend_state with the section above) ─
    metric_dd = ft.Dropdown(
        label="Metric",
        options=[ft.dropdown.Option(k, v) for k, v in _PNL_ROI_METRICS],
        value=_DEFAULT_PNL_ROI_METRIC,
        width=150,
        text_size=13,
    )
    pnl_roi_chart_container = ft.Container()

    def _render_pnl_roi_chart() -> None:
        metric = metric_dd.value or _DEFAULT_PNL_ROI_METRIC
        pnl_roi_chart_container.content = build_portfolio_value_chart(
            trend_state["points"], trend_state["date_from"], trend_state["date_to"], metric=metric,
        )

    def _on_metric_change(e=None) -> None:
        _render_pnl_roi_chart()
        page.update()

    metric_dd.on_select = _on_metric_change  # re-render only — reuses trend_state, no new fetch

    pnl_roi_section = ft.Column(
        [
            ft.Row(
                [
                    ft.Text("PnL / ROI Trend", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
                    metric_dd,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Container(height=8),
            ft.Container(
                content=pnl_roi_chart_container,
                bgcolor=BG_CARD,
                border=ft.border.all(1, BORDER),
                border_radius=10,
                padding=16,
            ),
        ],
        spacing=4,
    )

    # ── Portfolio Contributors (shares period_dd / trend_state) ─────────────────
    contributors_top_col = ft.Column(spacing=8)
    contributors_worst_col = ft.Column(spacing=8)
    contributors_note = ft.Text("", size=10, color=T_MUT, italic=True, visible=False)

    def _contributor_row(c) -> ft.Row:
        color = GREEN if c.contribution_czk >= 0 else RED
        return ft.Row(
            [
                ft.Text(display_asset(c.asset), size=13, color=T_PRI, weight=ft.FontWeight.W_600),
                ft.Text(_czk(c.contribution_czk, sign=True), size=13, color=color, weight=ft.FontWeight.W_600),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    def _render_contributors() -> None:
        date_from, date_to = trend_state["date_from"], trend_state["date_to"]
        if date_from is None:
            return
        contributors = get_portfolio_contributors(db_path, date_from, date_to)
        valid = [c for c in contributors if c.contribution_czk is not None]
        excluded = sorted(c.asset for c in contributors if c.contribution_czk is None)

        top5 = sorted(valid, key=lambda c: c.contribution_czk, reverse=True)[:5]
        worst5 = sorted(valid, key=lambda c: c.contribution_czk)[:5]

        no_data = ft.Text("No data available.", size=12, color=T_MUT)
        contributors_top_col.controls = [_contributor_row(c) for c in top5] if top5 else [no_data]
        contributors_worst_col.controls = [_contributor_row(c) for c in worst5] if worst5 else [no_data]

        if excluded:
            contributors_note.value = f"Excluded due to missing historical price: {', '.join(excluded)}"
            contributors_note.visible = True
        else:
            contributors_note.visible = False

    def _contributors_card(title: str, col: ft.Column) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=14, weight=ft.FontWeight.W_600, color=T_PRI),
                    ft.Container(height=8),
                    col,
                ],
                spacing=0,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, BORDER),
            border_radius=10,
            padding=16,
            expand=True,
        )

    contributors_section = ft.Column(
        [
            ft.Text("Portfolio Contributors", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Container(height=8),
            ft.Row(
                [
                    _contributors_card("Top Contributors", contributors_top_col),
                    ft.Container(width=16),
                    _contributors_card("Worst Contributors", contributors_worst_col),
                ],
            ),
            ft.Container(height=4),
            contributors_note,
        ],
        spacing=4,
    )

    # ── Input controls (Sell Simulator) ─────────────────────────────────────────
    venue_dd = ft.Dropdown(
        label="Venue",
        options=[],
        value=None,
        width=220,
        text_size=13,
    )
    mode_dd = ft.Dropdown(
        label="Mode",
        options=[ft.dropdown.Option(k, v) for k, v in _MODES],
        value="all",
        width=190,
        text_size=13,
    )

    # ── Result area ───────────────────────────────────────────────────────────
    result_col = ft.Column(spacing=12)

    def _stat_col(label: str, value: str, color: str = T_PRI) -> ft.Column:
        return ft.Column(
            [
                ft.Text(label, size=11, color=T_MUT),
                ft.Text(value, size=16, weight=ft.FontWeight.W_600, color=color),
            ],
            spacing=3,
            tight=True,
        )

    def _render_result(result) -> None:
        result_col.controls.clear()

        if not result.rows:
            result_col.controls.append(
                ft.Container(
                    content=ft.Text(
                        "No positions match the selected criteria.",
                        size=13,
                        color=T_MUT,
                    ),
                    padding=ft.padding.symmetric(16, 0),
                )
            )
            return

        has_stable = any(r.asset.upper() in _STABLECOINS for r in result.rows)
        avg_col_label = "Avg Buy*" if has_stable else "Avg Buy"

        # ── Summary box ───────────────────────────────────────────────────────
        pnl_color = _color(result.total_simulated_pnl)
        result_col.controls.append(
            ft.Container(
                content=ft.Row(
                    [
                        _stat_col("Total Value",        _czk(result.total_value)),
                        _stat_col("Total Cost Basis",   _czk(result.total_cost_basis)),
                        _stat_col(
                            "Total Simulated PnL",
                            _czk(result.total_simulated_pnl, sign=True),
                            pnl_color,
                        ),
                    ],
                    spacing=40,
                ),
                bgcolor=BG_CARD,
                border=ft.border.all(1, BORDER),
                border_radius=10,
                padding=16,
            )
        )

        # ── Missing prices warning ─────────────────────────────────────────────
        if result.missing_prices:
            result_col.controls.append(
                ft.Text(
                    f"Spot price unavailable for: {', '.join(sorted(result.missing_prices))}. "
                    "These positions are excluded from totals.",
                    size=11,
                    color=T_MUT,
                    italic=True,
                )
            )

        # ── Data table ────────────────────────────────────────────────────────
        hdr = lambda t: ft.Text(t, size=11, color=T_MUT)
        table_rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(display_asset(r.asset), size=13, color=T_PRI,
                                    weight=ft.FontWeight.W_600)),
                ft.DataCell(ft.Text(_amt(r.quantity, r.asset), size=12, color=T_PRI)),
                ft.DataCell(ft.Text(_czk(r.wac), size=12, color=T_MUT)),
                ft.DataCell(ft.Text(_czk(r.spot_price), size=12, color=T_MUT)),
                ft.DataCell(ft.Text(_czk(r.value), size=12, color=T_PRI)),
                ft.DataCell(ft.Text(_czk(r.cost_basis), size=12, color=T_MUT)),
                ft.DataCell(ft.Text(
                    _czk(r.simulated_pnl, sign=True), size=12,
                    color=_color(r.simulated_pnl), weight=ft.FontWeight.W_600,
                )),
                ft.DataCell(ft.Text(_pct(r.roi), size=12, color=_color(r.roi))),
            ])
            for r in result.rows
        ]

        table = ft.DataTable(
            columns=[
                ft.DataColumn(hdr("Asset")),
                ft.DataColumn(hdr("Qty")),
                ft.DataColumn(hdr(avg_col_label), numeric=True),
                ft.DataColumn(hdr("Spot"), numeric=True),
                ft.DataColumn(hdr("Value"), numeric=True),
                ft.DataColumn(hdr("Cost Basis"), numeric=True),
                ft.DataColumn(hdr("Sim. PnL"), numeric=True),
                ft.DataColumn(hdr("ROI"), numeric=True),
            ],
            rows=table_rows,
            column_spacing=24,
            horizontal_margin=0,
            heading_row_color=BG_HDR,
            border=ft.border.all(1, BORDER),
            border_radius=8,
        )

        result_col.controls.append(ft.Row([table], scroll=ft.ScrollMode.AUTO))

        # ── Stablecoin footnote ───────────────────────────────────────────────
        if has_stable:
            result_col.controls.append(
                ft.Text(
                    "* U stablecoinů může Avg Buy zahrnovat přenesený cost basis ze swapů, "
                    "proto nemusí odpovídat nominální ceně 1 USD.",
                    size=10,
                    color=T_MUT,
                    italic=True,
                )
            )

    # ── Simulate handler ──────────────────────────────────────────────────────
    def _on_simulate(e=None) -> None:
        venue = venue_dd.value
        if not venue or snap_holder[0] is None:
            return
        mode = mode_dd.value or "all"
        result = run_sell_simulation(snap_holder[0], venue, mode)
        _render_result(result)
        page.update()

    simulate_btn = ft.ElevatedButton(
        "Simulate",
        on_click=_on_simulate,
        style=ft.ButtonStyle(color=T_PRI, bgcolor=BLUE),
    )

    # ── Controls row ──────────────────────────────────────────────────────────
    controls_row = ft.Row(
        [
            venue_dd,
            ft.Container(width=12),
            mode_dd,
            ft.Container(width=16),
            simulate_btn,
        ],
        vertical_alignment=ft.CrossAxisAlignment.END,
    )

    # ── Full view ─────────────────────────────────────────────────────────────
    scroll_col = ft.Column(
        [
            trend_section,
            ft.Container(height=28),
            pnl_roi_section,
            ft.Container(height=28),
            contributors_section,
            ft.Container(height=28),
            ft.Divider(height=1, color=BORDER),
            ft.Container(height=20),
            ft.Text("Sell Simulator", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Container(height=4),
            ft.Text(
                "Projekce realizovaného zisku/ztráty při prodeji vybraných pozic za aktuální spot cenu.",
                size=12,
                color=T_MUT,
            ),
            ft.Container(height=16),
            controls_row,
            ft.Divider(height=1, color=BORDER),
            ft.Container(height=4),
            result_col,
            ft.Container(height=80),
        ],
        spacing=8,
        scroll=ft.ScrollMode.AUTO,
    )

    view = ft.Container(
        expand=True,
        padding=ft.padding.only(left=24, right=24, top=24, bottom=0),
        content=scroll_col,
    )

    # ── Run / refresh ─────────────────────────────────────────────────────────
    def _populate_venues(snap) -> None:
        venues = [
            v for v, vdto in sorted(snap.by_venue.items())
            if vdto.assets_held > 0 or vdto.positions
        ]
        venue_dd.options = [ft.dropdown.Option(v, v.upper()) for v in venues]
        if venues and venue_dd.value not in venues:
            venue_dd.value = venues[0]

    def run(e=None) -> None:
        _load_trend()
        snap = get_dashboard_snapshot(db_path, price_provider, fiat)
        snap_holder[0] = snap
        _populate_venues(snap)
        page.update()

    return view, run
