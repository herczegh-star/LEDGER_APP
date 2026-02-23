"""Health view: ledger integrity checks rendered from TableReport DTO.

Public API:
    build_health_view(page, db_path) -> (ft.Column, Callable)

UI only. All checks are delegated to core/services/health_service.health_report().
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Tuple

import flet as ft

from core.constants import HEALTH_ERROR as ERROR, HEALTH_WARNING as WARNING
from core.services.ui_facade import export_table_report_to_csv, get_health_report

# ── Color palette ────────────────────────────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
ORANGE  = "#f97316"

_EXPORTS_DIR = os.path.join(os.getcwd(), "exports")

# Issue table column definitions: (values dict key, display label, numeric, width)
_COLS = [
    ("severity",  "Severity",  False, 80),
    ("kind",      "Kind",      False, 180),
    ("trade_id",  "Trade ID",  False, 160),
    ("asset",     "Asset",     False, 80),
    ("timestamp", "Timestamp", False, 155),
    ("message",   "Message",   False, 320),
    ("hint",      "Hint",      False, 200),
]


def _sev_color(severity: str) -> str:
    if severity == ERROR:
        return RED
    if severity == WARNING:
        return ORANGE
    return T_MUT


def _short(value: str, maxlen: int = 30) -> str:
    """Truncate a long string for display."""
    if not value:
        return "—"
    return value if len(value) <= maxlen else value[:maxlen - 1] + "…"


def build_health_view(page: ft.Page, db_path: str) -> Tuple[ft.Column, Callable]:
    """Return (view_control, run_fn) for the Health tab.

    The view is built once; run_fn is called each time the tab becomes active
    or the ledger changes.
    """
    # ── Summary cards ────────────────────────────────────────────────────────
    err_count_txt  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=RED)
    warn_count_txt = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=ORANGE)
    status_txt     = ft.Text("", size=12, color=T_MUT)

    def _summary_card(label: str, widget: ft.Text) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(label, size=11, color=T_MUT), widget],
                spacing=4, tight=True,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, BORDER),
            border_radius=8,
            padding=ft.padding.symmetric(10, 16),
            expand=True,
        )

    summary_row = ft.Row([
        _summary_card("Errors",   err_count_txt),
        _summary_card("Warnings", warn_count_txt),
    ], spacing=12)

    table_area = ft.Column([], scroll=ft.ScrollMode.AUTO, expand=True)

    # ── Export button ────────────────────────────────────────────────────────
    # Hold the last computed report so export can use it directly.
    _report_holder: list = [None]

    def _on_export(_e=None) -> None:
        report = _report_holder[0]
        if report is None or not report.rows:
            page.show_dialog(ft.SnackBar(ft.Text("No issues to export."), duration=2000))
            return
        try:
            os.makedirs(_EXPORTS_DIR, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M")
            out_path = os.path.join(_EXPORTS_DIR, f"health_issues_{ts}.csv")
            saved = export_table_report_to_csv(report, out_path)
            page.show_dialog(ft.SnackBar(ft.Text(f"Saved: {saved}"), duration=4000))
        except Exception as exc:  # noqa: BLE001
            page.show_dialog(ft.SnackBar(ft.Text(f"Export failed: {exc}"), duration=4000))

    export_btn = ft.TextButton(
        "Export Issues CSV",
        on_click=_on_export,
        style=ft.ButtonStyle(color=T_MUT),
    )

    def run_report(e=None) -> None:
        report = get_health_report(db_path)
        _report_holder[0] = report

        errors   = sum(1 for r in report.rows if r.values.get("severity") == ERROR)
        warnings = sum(1 for r in report.rows if r.values.get("severity") == WARNING)
        total    = len(report.rows)

        err_count_txt.value  = str(errors)
        warn_count_txt.value = str(warnings)
        status_txt.value = (
            f"{total} issue{'s' if total != 1 else ''} found"
            if total else "No issues — ledger is healthy."
        )

        # ── Build DataTable ────────────────────────────────────────────────
        if not report.rows:
            table_area.controls = [
                ft.Container(
                    content=ft.Row([
                        ft.Icon("check_circle_outline", color=GREEN, size=20),
                        ft.Text("No integrity issues found.", color=GREEN, size=13),
                    ], spacing=8),
                    padding=16,
                )
            ]
            page.update()
            return

        col_defs = [
            ft.DataColumn(
                ft.Text(label, color=T_MUT, size=11, weight=ft.FontWeight.BOLD),
                numeric=numeric,
            )
            for _, label, numeric, _ in _COLS
        ]

        data_rows = []
        for tr in report.rows:
            sev = tr.values.get("severity", "")
            sev_color = _sev_color(sev)
            cells = []
            for key, _, _, maxlen in _COLS:
                raw = tr.values.get(key, "")
                display = _short(str(raw) if raw else "", maxlen)
                color = sev_color if key == "severity" else T_PRI
                if key == "severity":
                    # Show a colored chip for severity
                    cells.append(ft.DataCell(
                        ft.Container(
                            content=ft.Text(
                                display.upper(), size=10,
                                weight=ft.FontWeight.BOLD, color=sev_color,
                            ),
                            bgcolor=f"{sev_color}22",  # translucent background
                            border=ft.border.all(1, sev_color),
                            border_radius=4,
                            padding=ft.padding.symmetric(2, 6),
                        )
                    ))
                elif key in ("trade_id",):
                    # Truncate UUIDs: show first 8 chars
                    short_id = (str(raw)[:8] + "…") if len(str(raw)) > 8 else str(raw)
                    cells.append(ft.DataCell(
                        ft.Text(short_id or "—", color=T_MUT, size=11,
                                font_family="monospace")
                    ))
                elif key == "message":
                    cells.append(ft.DataCell(
                        ft.Text(str(raw) if raw else "—", color=T_PRI, size=11)
                    ))
                elif key == "hint":
                    cells.append(ft.DataCell(
                        ft.Text(str(raw) if raw else "—", color=T_MUT, size=11)
                    ))
                else:
                    cells.append(ft.DataCell(
                        ft.Text(str(raw) if raw else "—", color=color, size=11)
                    ))
            data_rows.append(ft.DataRow(cells=cells))

        table_area.controls = [
            ft.Row(
                [ft.DataTable(
                    columns=col_defs,
                    rows=data_rows,
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                    vertical_lines=ft.BorderSide(1, BORDER),
                    heading_row_color=BG_HDR,
                    data_row_color={"hovered": "#1e2a3a"},
                    column_spacing=16,
                )],
                scroll=ft.ScrollMode.AUTO,
            )
        ]
        page.update()

    view = ft.Column(
        [
            ft.Row([
                ft.Text("Data Health", size=20,
                        weight=ft.FontWeight.BOLD, color=T_PRI),
                export_btn,
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Divider(height=1, color=BORDER),
            summary_row,
            status_txt,
            table_area,
        ],
        spacing=12,
        expand=True,
    )

    return view, run_report
