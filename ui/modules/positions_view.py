"""Positions view: WAC-based per-asset table rendered from TableReport DTO.

Public API:
    build_positions_view(page, db_path) -> (ft.Column, Callable)

    filter_and_sort_rows(rows, search, hide_zeros, sort_key) -> list[TableRow]
        Pure helper — usable in tests without a Flet page.

UI only. All computation delegated to core/services/report_service.get_positions_report().
"""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import Callable, Tuple

import flet as ft

from core.services.ui_facade import (
    export_table_report_to_csv,
    get_positions_table_report,
)

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
ORANGE  = "#f97316"

_EXPORTS_DIR = os.path.join(os.getcwd(), "exports")

# Column definitions: (values dict key, display header)
_COLS = [
    ("quantity",     "Quantity"),
    ("wac",          "WAC"),
    ("cost_basis",   "Cost Basis"),
    ("realized_pnl", "Realized P&L"),
]

# Sort option definitions: (dropdown key, label)
_SORT_OPTIONS = [
    ("az",         "Asset A→Z"),
    ("cost_desc",  "Cost Basis ↓"),
    ("qty_desc",   "Quantity ↓"),
    ("pnl_desc",   "Realized P&L ↓"),
]

_ZERO = Decimal("0")


# ── Pure filter/sort helper ──────────────────────────────────────────────────

def filter_and_sort_rows(
    rows: list,
    search: str = "",
    hide_zeros: bool = False,
    sort_key: str = "az",
) -> list:
    """Filter and sort a list of TableRow objects.

    This is a pure function with no UI dependencies — it can be unit-tested
    independently from the Flet view.

    Args:
        rows:       TableRow list from a TableReport (e.g. positions report).
        search:     Case-insensitive substring filter on ``row.key`` (asset name).
                    Empty string disables the filter.
        hide_zeros: If True, rows where ``values["quantity"] == 0`` are excluded.
        sort_key:   One of "az", "cost_desc", "qty_desc", "pnl_desc".

    Returns:
        New list — input is not mutated.
    """
    result = list(rows)

    # ── Filter: search ────────────────────────────────────────────────────────
    if search:
        needle = search.strip().lower()
        result = [r for r in result if needle in r.key.lower()]

    # ── Filter: hide zero positions ───────────────────────────────────────────
    if hide_zeros:
        result = [
            r for r in result
            if r.values.get("quantity", _ZERO) != _ZERO
        ]

    # ── Sort ──────────────────────────────────────────────────────────────────
    if sort_key == "az":
        result.sort(key=lambda r: r.key)
    elif sort_key == "cost_desc":
        result.sort(
            key=lambda r: r.values.get("cost_basis", _ZERO),
            reverse=True,
        )
    elif sort_key == "qty_desc":
        result.sort(
            key=lambda r: r.values.get("quantity", _ZERO),
            reverse=True,
        )
    elif sort_key == "pnl_desc":
        result.sort(
            key=lambda r: r.values.get("realized_pnl", _ZERO),
            reverse=True,
        )
    # Unknown sort_key falls back to list order (which is already "az" from report)

    return result


# ── Display helpers ──────────────────────────────────────────────────────────

def _fmt(val) -> str:
    """Format a Decimal value for display.

    Trims trailing zeros but always shows at least 2 decimal places.
    Returns "—" for None.
    """
    if val is None:
        return "—"
    d = Decimal(str(val))
    s = f"{d:,.8f}".rstrip("0")
    if "." in s:
        _, dec_part = s.split(".")
        if len(dec_part) < 2:
            s = f"{d:,.2f}"
    return s


# ── View builder ─────────────────────────────────────────────────────────────

def build_positions_view(page: ft.Page, db_path: str) -> Tuple[ft.Column, Callable]:
    """Return (view_control, run_fn) for the Positions tab.

    The view is built once; run_fn is called each time the tab becomes active
    or data changes (e.g. after Add Trade or Import).  Filter/sort controls
    update the rendered table without re-fetching from the database.
    """
    # ── State: cached report rows ────────────────────────────────────────────
    # Holds the last fetched report so filter/sort can re-render without a
    # database roundtrip.
    _report_holder: list = [None]   # [TableReport | None]

    # ── Controls ─────────────────────────────────────────────────────────────
    tf_search = ft.TextField(
        hint_text="Search asset…",
        prefix_icon="search",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        hint_style=ft.TextStyle(color=T_MUT),
        text_size=13,
        height=40,
        content_padding=ft.padding.symmetric(0, 12),
        expand=True,
    )

    cb_hide_zeros = ft.Checkbox(
        label="Hide zero positions",
        value=False,
        check_color=T_PRI,
        active_color="#1d4ed8",
        label_style=ft.TextStyle(color=T_MUT, size=13),
    )

    dd_sort = ft.Dropdown(
        label="Sort by",
        width=180,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[ft.dropdown.Option(k, label) for k, label in _SORT_OPTIONS],
        value="az",
    )

    status_txt = ft.Text("", size=12, color=T_MUT)
    table_area = ft.Column([], scroll=ft.ScrollMode.AUTO, expand=True)

    # ── Column header definitions (built once) ────────────────────────────────
    _col_defs = [
        ft.DataColumn(
            ft.Text("Asset", color=T_MUT, size=12, weight=ft.FontWeight.BOLD)
        )
    ]
    for _, label in _COLS:
        _col_defs.append(ft.DataColumn(
            ft.Text(label, color=T_MUT, size=12, weight=ft.FontWeight.BOLD),
            numeric=True,
        ))

    # ── Render: apply filter/sort and rebuild table ───────────────────────────
    def _render() -> None:
        report = _report_holder[0]
        source_rows = report.rows if report is not None else []
        total_source = len(source_rows)

        visible = filter_and_sort_rows(
            source_rows,
            search=tf_search.value or "",
            hide_zeros=cb_hide_zeros.value or False,
            sort_key=dd_sort.value or "az",
        )

        data_rows = []
        for tr in visible:
            cells = [ft.DataCell(ft.Text(tr.key, color=T_PRI, size=12))]
            for key, _ in _COLS:
                val = tr.values.get(key)
                color = T_PRI
                if key == "realized_pnl" and val is not None:
                    color = GREEN if val >= _ZERO else RED
                cells.append(ft.DataCell(ft.Text(_fmt(val), color=color, size=12)))
            data_rows.append(ft.DataRow(cells=cells))

        if data_rows:
            table_area.controls = [
                ft.DataTable(
                    columns=_col_defs,
                    rows=data_rows,
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                    vertical_lines=ft.BorderSide(1, BORDER),
                    heading_row_color=BG_HDR,
                    data_row_color={"hovered": "#1e2a3a"},
                    column_spacing=24,
                )
            ]
        else:
            msg = (
                "No matching positions."
                if total_source > 0
                else "No positions yet."
            )
            table_area.controls = [ft.Text(msg, color=T_MUT, size=13)]

        shown = len(visible)
        if total_source != shown:
            status_txt.value = f"{shown} of {total_source} asset{'s' if total_source != 1 else ''}"
        else:
            status_txt.value = f"{total_source} asset{'s' if total_source != 1 else ''}"
        page.update()

    # ── Control callbacks (filter/sort only — no DB re-fetch) ─────────────────
    def _on_search(_e=None) -> None:
        _render()

    def _on_hide_zeros(_e=None) -> None:
        _render()

    def _on_sort(_e=None) -> None:
        _render()

    tf_search.on_change    = _on_search
    cb_hide_zeros.on_change = _on_hide_zeros
    dd_sort.on_change      = _on_sort

    # ── Export button ─────────────────────────────────────────────────────────
    def _on_export(_e=None) -> None:
        report = _report_holder[0]
        if report is None or not report.rows:
            page.show_dialog(ft.SnackBar(ft.Text("No positions to export."), duration=2000))
            return
        try:
            os.makedirs(_EXPORTS_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            out_path = os.path.join(_EXPORTS_DIR, f"positions_{ts}.csv")
            saved = export_table_report_to_csv(report, out_path)
            page.show_dialog(ft.SnackBar(ft.Text(f"Saved: {saved}"), duration=4000))
        except Exception as exc:  # noqa: BLE001
            page.show_dialog(ft.SnackBar(ft.Text(f"Export failed: {exc}"), duration=4000))

    export_btn = ft.TextButton(
        "Export CSV",
        on_click=_on_export,
        style=ft.ButtonStyle(color=T_MUT),
    )

    # ── Main run function (fetches from DB, then renders) ─────────────────────
    def run_report(e=None) -> None:
        _report_holder[0] = get_positions_table_report(db_path)
        _render()

    # ── View layout ──────────────────────────────────────────────────────────
    controls_row = ft.Row(
        [
            tf_search,
            cb_hide_zeros,
            dd_sort,
            export_btn,
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    view = ft.Column(
        [
            ft.Row([
                ft.Text("Positions", size=20,
                        weight=ft.FontWeight.BOLD, color=T_PRI),
            ]),
            ft.Divider(height=1, color=BORDER),
            controls_row,
            status_txt,
            table_area,
        ],
        spacing=12,
        expand=True,
    )

    return view, run_report
