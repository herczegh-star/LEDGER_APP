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

from core.prices.coingecko import CoinGeckoPriceProvider
from core.services.portfolio_pulse_service import compute_portfolio_24h_change
from core.services.relative_strength_service import compute_relative_strength
from core.services.ui_facade import (
    compute_allocation, compute_headline_value, get_dashboard_snapshot,
    get_earliest_ledger_date, get_portfolio_contributors, get_portfolio_value_trend,
    run_sell_simulation,
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
_ALLOCATION_BAR_WIDTH = 160

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


def _pct_raw(v: Optional[Decimal]) -> str:
    """Like _pct(), but for a value that's already a percentage NUMBER
    (CoinGecko's 24h-change convention — 1.23 means +1.23%), not a
    fraction to be multiplied by 100."""
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{float(v):.2f}%"


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

    # ── Portfolio Pulse (top of Analysis) ───────────────────────────────────────
    # Portfolio Value / 24H Change are always live/current (independent of the
    # period selector below) — only Best/Worst Contributor tracks the shared
    # period. Best/Worst reuse the SAME get_portfolio_contributors() result the
    # Contributors section already fetches for that period (see
    # contributors_cache, populated by _render_contributors() further down) —
    # never a second fetch for the same data.
    _pulse_price_provider = CoinGeckoPriceProvider(ttl_seconds=60)  # dedicated
    # instance for the 24h-change capability, same pattern as Relative Strength.
    contributors_cache: list = [[]]

    pulse_value_text = ft.Text("—", size=20, weight=ft.FontWeight.BOLD, color=T_PRI)
    pulse_24h_text = ft.Text("—", size=20, weight=ft.FontWeight.BOLD, color=T_MUT)
    pulse_best_text = ft.Text("—", size=16, weight=ft.FontWeight.BOLD, color=T_MUT)
    pulse_worst_text = ft.Text("—", size=16, weight=ft.FontWeight.BOLD, color=T_MUT)

    def _pulse_card(label: str, value_text: ft.Text) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(label, size=11, color=T_MUT), ft.Container(height=6), value_text],
                spacing=0,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, BORDER),
            border_radius=10,
            padding=16,
            width=220,
        )

    def _render_pulse_live(snap) -> None:
        """Portfolio Value + 24H Change — always current, independent of
        the period selector. No new fetch beyond one live
        get_prices_with_24h_change() call (same cost as Relative
        Strength's own live 24h call)."""
        portfolio_value = compute_headline_value(snap.positions)
        pulse_value_text.value = _czk(portfolio_value)
        pulse_value_text.color = T_PRI if portfolio_value is not None else T_MUT

        change = compute_portfolio_24h_change(snap.positions, _pulse_price_provider, fiat)
        pulse_24h_text.value = _pct_raw(change.change_pct)
        pulse_24h_text.color = _color(change.change_pct)

    def _render_pulse_contributors() -> None:
        """Best/Worst Contributor — a plain max()/min() pick over the
        already-fetched contributors_cache for the shared period. No new
        computation, no new fetch (mirrors the Contributors section's
        own existing Top-5/Worst-5 selection pattern)."""
        valid = [c for c in contributors_cache[0] if c.contribution_czk is not None]
        if not valid:
            pulse_best_text.value = "—"
            pulse_best_text.color = T_MUT
            pulse_worst_text.value = "—"
            pulse_worst_text.color = T_MUT
            return
        best = max(valid, key=lambda c: c.contribution_czk)
        worst = min(valid, key=lambda c: c.contribution_czk)
        pulse_best_text.value = f"{display_asset(best.asset)} {_czk(best.contribution_czk, sign=True)}"
        pulse_best_text.color = _color(best.contribution_czk)
        pulse_worst_text.value = f"{display_asset(worst.asset)} {_czk(worst.contribution_czk, sign=True)}"
        pulse_worst_text.color = _color(worst.contribution_czk)

    pulse_section = ft.Column(
        [
            ft.Row(
                [
                    _pulse_card("Portfolio Value", pulse_value_text),
                    _pulse_card("24H Change", pulse_24h_text),
                    _pulse_card("Best Contributor", pulse_best_text),
                    _pulse_card("Worst Contributor", pulse_worst_text),
                ],
                spacing=16,
                run_spacing=16,
                wrap=True,
            ),
        ],
        spacing=0,
    )

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
        _render_pulse_contributors()
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
        contributors_cache[0] = contributors  # Portfolio Pulse's Best/Worst reads this — no second fetch
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

    # ── Allocation (shares snap_holder with the Sell Simulator below) ──────────
    allocation_rows_col = ft.Column(spacing=10)
    allocation_concentration_row = ft.Row(spacing=24)
    allocation_note = ft.Text("", size=10, color=T_MUT, italic=True, visible=False)

    def _allocation_row(asset: str, pct: Decimal, muted: bool = False) -> ft.Row:
        bar_color = T_MUT if muted else BLUE
        filled = float(pct) / 100.0 * _ALLOCATION_BAR_WIDTH
        filled = min(max(filled, 2.0), _ALLOCATION_BAR_WIDTH)
        return ft.Row(
            [
                ft.Text(display_asset(asset), size=13, color=T_MUT if muted else T_PRI,
                        weight=ft.FontWeight.W_600, width=64),
                ft.Stack(
                    [
                        ft.Container(width=_ALLOCATION_BAR_WIDTH, height=8, bgcolor=BORDER, border_radius=4),
                        ft.Container(width=filled, height=8, bgcolor=bar_color, border_radius=4),
                    ],
                    width=_ALLOCATION_BAR_WIDTH,
                    height=8,
                ),
                ft.Text(f"{pct:.1f} %", size=13, color=T_MUT, width=56, text_align=ft.TextAlign.RIGHT),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _render_allocation(snap) -> None:
        alloc = compute_allocation(snap.positions, top_n=5)

        row_controls = [_allocation_row(r.asset, r.pct) for r in alloc.rows]
        if alloc.other_pct > _ZERO:
            row_controls.append(_allocation_row("Other", alloc.other_pct, muted=True))

        allocation_rows_col.controls = row_controls if row_controls else [
            ft.Text("No data available.", size=12, color=T_MUT)
        ]

        allocation_concentration_row.controls = [
            ft.Text(f"Top 1: {alloc.top1_pct:.1f} %", size=12, color=T_MUT),
            ft.Text(f"Top 3: {alloc.top3_pct:.1f} %", size=12, color=T_MUT),
        ] if alloc.rows else []

        if alloc.excluded_assets:
            allocation_note.value = f"Excluded (no live price): {', '.join(alloc.excluded_assets)}"
            allocation_note.visible = True
        else:
            allocation_note.visible = False

    allocation_card = ft.Container(
        content=ft.Column(
            [
                allocation_rows_col,
                ft.Container(height=12),
                allocation_concentration_row,
            ],
            spacing=0,
        ),
        bgcolor=BG_CARD,
        border=ft.border.all(1, BORDER),
        border_radius=10,
        padding=16,
    )

    allocation_section = ft.Column(
        [
            ft.Text("Allocation", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Container(height=8),
            allocation_card,
            ft.Container(height=4),
            allocation_note,
        ],
        spacing=4,
    )

    # ── Relative Strength (shares snap_holder with Allocation / Sell Simulator) ─
    # Dedicated CoinGecko-only provider instance, held directly for the
    # 24h-change capability — see core/prices/coingecko.py's module
    # docstring and core/services/relative_strength_service.py's
    # docstring for why this is NOT the generic *price_provider*
    # (CompositePriceProvider has no equivalent to merge from two
    # sources, so the live 24h figure stays CoinGecko-specific).
    _rs_price_provider = CoinGeckoPriceProvider(ttl_seconds=60)

    # Top-10/Bottom-10 collapse is pure presentation over an already-fetched,
    # already-sorted row list — rs_rows_cache holds that one fetch; toggling
    # only re-renders from the cache, never calls compute_relative_strength()
    # again (no new fetch, no new API call).
    _RS_HALF = 10
    _RS_SHOW_ALL_THRESHOLD = 2 * _RS_HALF  # <= this many rows: always show all, no toggle
    rs_rows_cache: list = [[]]
    rs_expanded: list = [False]
    rs_content_col = ft.Column(spacing=12)
    rs_table_container = ft.Container(content=rs_content_col)
    rs_note = ft.Text("", size=10, color=T_MUT, italic=True, visible=False)

    def _rs_toggle_clicked(e=None) -> None:
        rs_expanded[0] = not rs_expanded[0]
        _rs_render_content()
        page.update()

    rs_toggle_btn = ft.TextButton("", on_click=_rs_toggle_clicked, style=ft.ButtonStyle(color=T_MUT))

    def _rs_build_table(rows_slice) -> ft.DataTable:
        rs_hdr = lambda t: ft.Text(t, size=11, color=T_MUT)
        data_rows = []
        for r in rows_slice:
            # Very subtle highlight for outperforming-BTC rows only — no
            # icons, no semaphore, same green/red values as before.
            row_color = "#132a1d" if (r.vs_btc_7d is not None and r.vs_btc_7d > _ZERO) else None
            data_rows.append(ft.DataRow(
                color=row_color,
                cells=[
                    ft.DataCell(ft.Text(display_asset(r.asset), size=13, color=T_PRI,
                                        weight=ft.FontWeight.W_600)),
                    ft.DataCell(ft.Text(_pct_raw(r.change_24h_pct), size=12, color=_color(r.change_24h_pct))),
                    ft.DataCell(ft.Text(_pct(r.return_7d), size=12, color=_color(r.return_7d))),
                    ft.DataCell(ft.Text(_pct(r.return_30d), size=12, color=_color(r.return_30d))),
                    ft.DataCell(ft.Text(_pct(r.vs_btc_7d), size=12, color=_color(r.vs_btc_7d))),
                ],
            ))
        return ft.DataTable(
            columns=[
                ft.DataColumn(rs_hdr("Asset")),
                ft.DataColumn(rs_hdr("24H"), numeric=True),
                ft.DataColumn(rs_hdr("7D"), numeric=True),
                ft.DataColumn(rs_hdr("30D"), numeric=True),
                ft.DataColumn(rs_hdr("vs BTC (7D)"), numeric=True),
            ],
            rows=data_rows,
            column_spacing=24,
            horizontal_margin=16,
            heading_row_color=BG_HDR,
            border=ft.border.all(1, BORDER),
            border_radius=8,
        )

    def _rs_render_content() -> None:
        rows = rs_rows_cache[0]
        n = len(rows)

        if n == 0:
            rs_content_col.controls = [ft.Text("No data available.", size=12, color=T_MUT)]
            return

        if n <= _RS_SHOW_ALL_THRESHOLD or rs_expanded[0]:
            controls = [_rs_build_table(rows)]
            if n > _RS_SHOW_ALL_THRESHOLD:  # expanded view of a long list -> offer to collapse back
                rs_toggle_btn.text = "Show less"
                controls.append(ft.Row([rs_toggle_btn], alignment=ft.MainAxisAlignment.CENTER))
            rs_content_col.controls = controls
            return

        top = rows[:_RS_HALF]
        top_assets = {r.asset for r in top}
        bottom = [r for r in rows[-_RS_HALF:] if r.asset not in top_assets]  # defensive de-dup

        rs_toggle_btn.text = f"Show all {n}"
        rs_content_col.controls = [
            _rs_build_table(top),
            ft.Row([rs_toggle_btn], alignment=ft.MainAxisAlignment.CENTER),
            _rs_build_table(bottom),
        ]

    def _render_relative_strength(snap) -> None:
        rows = compute_relative_strength(db_path, snap.positions, _rs_price_provider, fiat)
        rs_rows_cache[0] = rows
        rs_expanded[0] = False  # a fresh fetch always starts collapsed
        _rs_render_content()

        # Only headline assets that WOULD be in scope but lack the
        # historical price needed for 7D/30D — never the scope exclusions
        # themselves (BTC/stablecoins/presale need no explanation here).
        missing = sorted(r.asset for r in rows if r.return_7d is None or r.return_30d is None)
        if missing:
            rs_note.value = f"Missing historical data: {', '.join(missing)}"
            rs_note.visible = True
        else:
            rs_note.visible = False

    relative_strength_section = ft.Column(
        [
            ft.Text("Relative Strength", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Container(height=8),
            rs_table_container,
            ft.Container(height=4),
            rs_note,
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
            pulse_section,
            ft.Container(height=28),
            trend_section,
            ft.Container(height=28),
            pnl_roi_section,
            ft.Container(height=28),
            contributors_section,
            ft.Container(height=28),
            allocation_section,
            ft.Container(height=28),
            relative_strength_section,
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
        _render_pulse_live(snap)
        _render_allocation(snap)
        _render_relative_strength(snap)
        page.update()

    return view, run
