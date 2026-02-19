"""Reports view: time-series report table with selectors.

UI only. No computations here.
Delegates entirely to core/services/report_service.py.
"""
from decimal import Decimal
from typing import Callable, Tuple

import flet as ft

from core.service import LedgerService
from core.services.report_service import ReportKind, get_report

# ── Color palette (same as app_flet.py) ────────────────────────────────────────
BG      = "#0b0f14"
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
BLUE    = "#1d4ed8"

# ── Metric column definitions per report kind ───────────────────────────────────
_COLS = {
    ReportKind.CASHFLOW: [
        ("net_amount", "Net Amount"),
    ],
    ReportKind.NETTO_INVESTED: [
        ("invested_amount", "Invested"),
        ("inflow_amount",   "Inflow"),
        ("net_flow",        "Net Flow"),
    ],
}


def _fmt(v: Decimal) -> str:
    """Format Decimal with thousands separator and explicit sign."""
    n = f"{abs(v):,.0f}".replace(",", "\u00a0")
    if v < 0:
        return f"-{n}"
    if v > 0:
        return f"+{n}"
    return n


def build_reports_view(page: ft.Page, db_path: str) -> Tuple[ft.Column, Callable]:
    """Build the Reports view.

    Returns:
        (view_control, run_fn) — call run_fn() to trigger initial load
        when the view becomes visible.
    """
    # ── State ──────────────────────────────────────────────────────────────────
    state = {
        "kind":     ReportKind.CASHFLOW,
        "bucket":   "month",
        "fiat_eur": True,
        "fiat_czk": True,
    }

    # ── Output areas ───────────────────────────────────────────────────────────
    totals_row = ft.Row([], spacing=12, wrap=True)
    table_area = ft.Column([], scroll=ft.ScrollMode.AUTO, expand=True)
    status_txt = ft.Text("", size=11, color=T_MUT)

    # ── Run report ──────────────────────────────────────────────────────────────
    def run_report(e=None) -> None:
        svc = LedgerService(db_path)
        try:
            rows = svc.timeline()
        finally:
            svc.close()

        fiat: set = set()
        if state["fiat_eur"]:
            fiat.add("EUR")
        if state["fiat_czk"]:
            fiat.add("CZK")
        if not fiat:
            fiat = {"EUR", "CZK"}

        report = get_report(rows, state["kind"], bucket=state["bucket"], fiat=fiat)

        # ── Totals ──
        totals_row.controls.clear()
        if report.totals:
            for cur in sorted(report.totals.keys()):
                metrics = report.totals[cur]
                items = [ft.Text(cur, size=13, weight=ft.FontWeight.BOLD, color=T_PRI)]
                for k, v in metrics.items():
                    label = k.replace("_", " ").title()
                    col = GREEN if v >= 0 else RED
                    items.append(ft.Text(f"{label}: {_fmt(v)}", size=12, color=col))
                totals_row.controls.append(
                    ft.Container(
                        content=ft.Column(items, spacing=3, tight=True),
                        bgcolor=BG_CARD,
                        border=ft.border.all(1, BORDER),
                        border_radius=8,
                        padding=ft.padding.symmetric(10, 16),
                    )
                )
        else:
            totals_row.controls.append(ft.Text("(no data)", size=12, color=T_MUT))

        # ── Table ──
        metric_defs = _COLS[state["kind"]]
        col_headers = [
            ft.DataColumn(ft.Text("Date",     size=12, color=T_MUT)),
            ft.DataColumn(ft.Text("Currency", size=12, color=T_MUT)),
        ] + [
            ft.DataColumn(ft.Text(label, size=12, color=T_MUT), numeric=True)
            for _, label in metric_defs
        ]

        data_rows = []
        for r in report.rows:
            cells = [
                ft.DataCell(ft.Text(r.date,     size=12, color=T_PRI)),
                ft.DataCell(ft.Text(r.currency, size=12, color=T_PRI)),
            ]
            for key, _ in metric_defs:
                v = r.values.get(key, Decimal("0"))
                cells.append(ft.DataCell(
                    ft.Text(_fmt(v), size=12, color=GREEN if v >= 0 else RED)
                ))
            data_rows.append(ft.DataRow(cells=cells))

        table_area.controls.clear()
        if data_rows:
            table_area.controls.append(
                ft.Container(
                    content=ft.DataTable(
                        columns=col_headers,
                        rows=data_rows,
                        column_spacing=24,
                        heading_row_color=BG_HDR,
                    ),
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                )
            )
        else:
            table_area.controls.append(ft.Text("(no rows)", size=12, color=T_MUT))

        status_txt.value = f"{len(report.rows)} rows · {state['bucket']} buckets"
        page.update()

    # ── Control callbacks ───────────────────────────────────────────────────────
    def on_kind_change(e) -> None:
        state["kind"] = ReportKind(e.control.value)
        run_report()

    def on_bucket_change(e) -> None:
        state["bucket"] = e.control.value
        run_report()

    def on_fiat_eur(e) -> None:
        state["fiat_eur"] = e.control.value
        run_report()

    def on_fiat_czk(e) -> None:
        state["fiat_czk"] = e.control.value
        run_report()

    # ── Selector controls ───────────────────────────────────────────────────────
    dd_kind = ft.Dropdown(
        width=170,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[
            ft.dropdown.Option("cashflow",       "Cashflow"),
            ft.dropdown.Option("netto_invested", "Netto Invested"),
        ],
        value="cashflow",
        on_change=on_kind_change,
    )

    dd_bucket = ft.Dropdown(
        width=110,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[
            ft.dropdown.Option("month", "Month"),
            ft.dropdown.Option("week",  "Week"),
            ft.dropdown.Option("day",   "Day"),
        ],
        value="month",
        on_change=on_bucket_change,
    )

    cb_eur = ft.Checkbox(label="EUR", value=True, on_change=on_fiat_eur)
    cb_czk = ft.Checkbox(label="CZK", value=True, on_change=on_fiat_czk)

    # ── Layout ─────────────────────────────────────────────────────────────────
    view = ft.Column(
        [
            # Controls bar
            ft.Container(
                content=ft.Row([
                    ft.Column([
                        ft.Text("Report", size=11, color=T_MUT),
                        dd_kind,
                    ], spacing=4, tight=True),
                    ft.Column([
                        ft.Text("Bucket", size=11, color=T_MUT),
                        dd_bucket,
                    ], spacing=4, tight=True),
                    ft.Column([
                        ft.Text("Fiat", size=11, color=T_MUT),
                        ft.Row([cb_eur, cb_czk], spacing=4),
                    ], spacing=4, tight=True),
                    status_txt,
                ], spacing=20, vertical_alignment=ft.CrossAxisAlignment.END),
                padding=ft.padding.only(bottom=12),
            ),
            ft.Divider(height=1, color=BORDER),
            # Totals panel
            ft.Container(
                content=ft.Column([
                    ft.Text("Totals", size=12,
                            weight=ft.FontWeight.BOLD, color=T_MUT),
                    totals_row,
                ], spacing=8),
                padding=ft.padding.symmetric(10, 0),
            ),
            ft.Divider(height=1, color=BORDER),
            # Data table
            table_area,
        ],
        spacing=0,
        expand=True,
    )

    return view, run_report
