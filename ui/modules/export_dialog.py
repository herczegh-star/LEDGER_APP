"""Export dialog: Flet modal for exporting ledger data to CSV.

Public API:
    open_export_dialog(page, db_path) -> None

UI only. All export logic delegated to core/services/export_service.py.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time
from typing import Callable

import flet as ft

from core.services.ui_facade import (
    export_cashflow_to_csv,
    export_dashboard_pdf_to_path,
    export_ledger_to_csv,
    export_ledger_to_csv_range,
    export_netto_invested_to_csv,
    export_positions_to_csv,
)

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
BLUE    = "#1d4ed8"

# Export type options
_EXPORT_TYPES = [
    ft.dropdown.Option("ledger",         "Ledger (all rows)"),
    ft.dropdown.Option("ledger_tax",     "LEDGER_TAX"),
    ft.dropdown.Option("cashflow",       "Cashflow"),
    ft.dropdown.Option("netto",          "Netto Invested"),
    ft.dropdown.Option("positions",      "Positions (WAC)"),
    ft.dropdown.Option("dashboard_pdf",  "Dashboard PDF"),
]

_BUCKETS = [
    ft.dropdown.Option("month", "Month"),
    ft.dropdown.Option("week",  "Week"),
    ft.dropdown.Option("day",   "Day"),
]

_EXPORTS_DIR = os.path.join(os.getcwd(), "exports")


def _auto_filename(export_type: str, bucket: str | None, date_range: tuple | None = None) -> str:
    """Generate a timestamped filename for the export.

    date_range: optional (date_from, date_to) — used by "ledger_tax" to
    encode the exported period in the filename.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    if export_type == "ledger":
        name = f"ledger_{ts}.csv"
    elif export_type == "ledger_tax":
        date_from, date_to = date_range
        name = f"ledger_tax_{date_from.isoformat()}_{date_to.isoformat()}_{ts}.csv"
    elif export_type == "cashflow":
        name = f"cashflow_{bucket}_{ts}.csv"
    elif export_type == "netto":
        name = f"netto_invested_{bucket}_{ts}.csv"
    elif export_type == "positions":
        name = f"positions_{ts}.csv"
    elif export_type == "dashboard_pdf":
        name = f"dashboard_{ts}.pdf"
    else:
        name = f"export_{ts}.csv"
    return os.path.join(_EXPORTS_DIR, name)


def open_export_dialog(
    page: ft.Page,
    db_path: str,
    snap_holder: list | None = None,
) -> None:
    """Build and open the Export modal dialog.

    Args:
        page:        Flet page reference.
        db_path:     Path to the SQLite ledger database.
        snap_holder: Optional list[DashboardSnapshotDTO | None] shared with
                     the main app.  When provided, Dashboard PDF export reuses
                     the already-loaded snapshot instead of loading a new one.
    """

    # ── Form controls ───────────────────────────────────────────────────────
    dd_type = ft.Dropdown(
        label="Export type",
        width=280,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=_EXPORT_TYPES,
        value="ledger",
    )

    dd_bucket = ft.Dropdown(
        label="Bucket",
        width=130,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=_BUCKETS,
        value="month",
        visible=False,
    )

    # Fiat checkboxes (shown only for cashflow / netto)
    cb_eur = ft.Checkbox(label="EUR", value=True, check_color=T_PRI,
                         active_color=BLUE)
    cb_czk = ft.Checkbox(label="CZK", value=True, check_color=T_PRI,
                         active_color=BLUE)
    fiat_row = ft.Row([
        ft.Text("Fiat:", size=12, color=T_MUT),
        cb_eur,
        cb_czk,
    ], spacing=8, visible=False)

    # Date range (shown only for LEDGER_TAX) — both calendar days included whole.
    tf_date_from = ft.TextField(
        label="Date from (YYYY-MM-DD)",
        width=200,
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
    )
    tf_date_to = ft.TextField(
        label="Date to (YYYY-MM-DD)",
        width=200,
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
    )
    date_range_row = ft.Row([tf_date_from, tf_date_to], spacing=12, visible=False)

    error_text = ft.Text("", color=RED, size=12)

    dlg: ft.AlertDialog

    def _on_type_change(_e=None) -> None:
        needs_bucket = dd_type.value in ("cashflow", "netto")
        dd_bucket.visible = needs_bucket
        fiat_row.visible = needs_bucket
        date_range_row.visible = dd_type.value == "ledger_tax"
        page.update()

    dd_type.on_select = _on_type_change

    def _close(_e=None) -> None:
        page.pop_dialog()

    def _submit(_e=None) -> None:
        error_text.value = ""
        page.update()

        export_type = dd_type.value or "ledger"
        bucket = dd_bucket.value or "month"

        fiat: set[str] = set()
        if cb_eur.value:
            fiat.add("EUR")
        if cb_czk.value:
            fiat.add("CZK")
        if not fiat and export_type in ("cashflow", "netto"):
            fiat = {"EUR", "CZK"}  # default fallback

        date_from: date | None = None
        date_to: date | None = None
        if export_type == "ledger_tax":
            from_str = (tf_date_from.value or "").strip()
            to_str = (tf_date_to.value or "").strip()
            if not from_str or not to_str:
                error_text.value = "Date from and Date to are required."
                page.update()
                return
            try:
                date_from = date.fromisoformat(from_str)
                date_to = date.fromisoformat(to_str)
            except ValueError:
                error_text.value = "Invalid date – use YYYY-MM-DD"
                page.update()
                return
            if date_from > date_to:
                error_text.value = "Date from must be on or before Date to."
                page.update()
                return

        out_path = _auto_filename(export_type, bucket, date_range=(date_from, date_to))

        try:
            if export_type == "ledger":
                saved = export_ledger_to_csv(db_path, out_path)
            elif export_type == "ledger_tax":
                time_from = datetime.combine(date_from, time.min)
                time_to = datetime.combine(date_to, time.max)
                saved = export_ledger_to_csv_range(db_path, out_path, time_from, time_to)
            elif export_type == "cashflow":
                saved = export_cashflow_to_csv(db_path, out_path, bucket=bucket, fiat=fiat)
            elif export_type == "netto":
                saved = export_netto_invested_to_csv(db_path, out_path, bucket=bucket, fiat=fiat)
            elif export_type == "positions":
                saved = export_positions_to_csv(db_path, out_path)
            elif export_type == "dashboard_pdf":
                snap = snap_holder[0] if snap_holder else None
                if snap is None:
                    error_text.value = (
                        "Dashboard data not loaded yet. "
                        "Please wait for the dashboard to finish loading, then try again."
                    )
                    page.update()
                    return
                saved = export_dashboard_pdf_to_path(snap, out_path)
            else:
                raise ValueError(f"Unknown export type: {export_type!r}")
        except Exception as exc:  # noqa: BLE001
            error_text.value = str(exc)
            page.update()
            return

        _close()
        page.show_dialog(ft.SnackBar(
            ft.Text(f"Saved: {saved}"),
            duration=4000,
        ))

    # ── Layout ──────────────────────────────────────────────────────────────
    form = ft.Column(
        [
            ft.Text(
                "Choose what to export. Files are saved in ./exports/ with a "
                "timestamp in the filename.",
                size=12,
                color=T_MUT,
            ),
            ft.Row([dd_type, dd_bucket], spacing=12),
            fiat_row,
            date_range_row,
            error_text,
        ],
        spacing=12,
        tight=True,
        width=480,
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(
            "Export to CSV",
            size=16,
            weight=ft.FontWeight.BOLD,
            color=T_PRI,
        ),
        bgcolor=BG_HDR,
        content=form,
        actions=[
            ft.TextButton(
                "Cancel",
                on_click=_close,
                style=ft.ButtonStyle(color=T_MUT),
            ),
            ft.TextButton(
                "Export",
                on_click=_submit,
                style=ft.ButtonStyle(color=GREEN),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
