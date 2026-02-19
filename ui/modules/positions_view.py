"""Positions view: WAC-based per-asset table rendered from TableReport DTO.

Public API:
    build_positions_view(page, db_path) -> (ft.Column, Callable)

UI only. All computation delegated to core/services/report_service.get_positions_report().
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Tuple

import flet as ft

from core.service import LedgerService
from core.services.report_service import get_positions_report

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"

# Column definitions: (values dict key, display header)
_COLS = [
    ("quantity",     "Quantity"),
    ("wac",          "WAC"),
    ("cost_basis",   "Cost Basis"),
    ("realized_pnl", "Realized P&L"),
]


def _fmt(val) -> str:
    """Format a Decimal value for display.

    Trims trailing zeros but always shows at least 2 decimal places.
    Returns "—" for None.
    """
    if val is None:
        return "—"
    d = Decimal(str(val))
    # Format with 8 decimal places then strip trailing zeros
    s = f"{d:,.8f}".rstrip("0")
    # Ensure at least 2 decimal places after the dot
    if "." in s:
        integer_part, dec_part = s.split(".")
        if len(dec_part) < 2:
            s = f"{d:,.2f}"
    return s


def build_positions_view(page: ft.Page, db_path: str) -> Tuple[ft.Column, Callable]:
    """Return (view_control, run_fn) for the Positions tab.

    The view is built once; run_fn is called each time the tab becomes active
    or data changes (e.g. after Add Trade or Import).
    """
    status_txt = ft.Text("", size=12, color=T_MUT)
    table_area = ft.Column([], scroll=ft.ScrollMode.AUTO, expand=True)

    def run_report(e=None) -> None:
        svc = LedgerService(db_path)
        try:
            rows = svc.timeline()
        finally:
            svc.close()

        report = get_positions_report(rows)

        # ── Column headers ────────────────────────────────────────────────
        col_defs = [
            ft.DataColumn(
                ft.Text("Asset", color=T_MUT, size=12, weight=ft.FontWeight.BOLD)
            )
        ]
        for _, label in _COLS:
            col_defs.append(ft.DataColumn(
                ft.Text(label, color=T_MUT, size=12, weight=ft.FontWeight.BOLD),
                numeric=True,
            ))

        # ── Data rows ─────────────────────────────────────────────────────
        data_rows = []
        for tr in report.rows:
            cells = [ft.DataCell(ft.Text(tr.key, color=T_PRI, size=12))]
            for key, _ in _COLS:
                val = tr.values.get(key)
                color = T_PRI
                if key == "realized_pnl" and val is not None:
                    color = GREEN if val >= Decimal("0") else RED
                cells.append(ft.DataCell(ft.Text(_fmt(val), color=color, size=12)))
            data_rows.append(ft.DataRow(cells=cells))

        if data_rows:
            table_area.controls = [
                ft.DataTable(
                    columns=col_defs,
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
            table_area.controls = [
                ft.Text("No positions yet.", color=T_MUT, size=13)
            ]

        n = len(report.rows)
        status_txt.value = f"{n} asset{'s' if n != 1 else ''}"
        page.update()

    view = ft.Column(
        [
            ft.Text("Positions", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Divider(height=1, color=BORDER),
            status_txt,
            table_area,
        ],
        spacing=12,
        expand=True,
    )

    return view, run_report
