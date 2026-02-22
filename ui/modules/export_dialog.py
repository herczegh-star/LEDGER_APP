"""Export dialog: Flet modal for exporting ledger data to CSV.

Public API:
    open_export_dialog(page, db_path) -> None

UI only. All export logic delegated to core/services/export_service.py.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable

import flet as ft

from core.services.ui_facade import (
    export_cashflow_to_csv,
    export_ledger_to_csv,
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
    ft.dropdown.Option("ledger",        "Ledger (all rows)"),
    ft.dropdown.Option("cashflow",      "Cashflow"),
    ft.dropdown.Option("netto",         "Netto Invested"),
    ft.dropdown.Option("positions",     "Positions (WAC)"),
]

_BUCKETS = [
    ft.dropdown.Option("month", "Month"),
    ft.dropdown.Option("week",  "Week"),
    ft.dropdown.Option("day",   "Day"),
]

_EXPORTS_DIR = os.path.join(os.getcwd(), "exports")


def _auto_filename(export_type: str, bucket: str | None) -> str:
    """Generate a timestamped filename for the export."""
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    if export_type == "ledger":
        name = f"ledger_{ts}.csv"
    elif export_type == "cashflow":
        name = f"cashflow_{bucket}_{ts}.csv"
    elif export_type == "netto":
        name = f"netto_invested_{bucket}_{ts}.csv"
    elif export_type == "positions":
        name = f"positions_{ts}.csv"
    else:
        name = f"export_{ts}.csv"
    return os.path.join(_EXPORTS_DIR, name)


def open_export_dialog(page: ft.Page, db_path: str) -> None:
    """Build and open the Export modal dialog."""

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

    error_text = ft.Text("", color=RED, size=12)

    dlg: ft.AlertDialog

    def _on_type_change(_e=None) -> None:
        needs_bucket = dd_type.value in ("cashflow", "netto")
        dd_bucket.visible = needs_bucket
        fiat_row.visible = needs_bucket
        page.update()

    dd_type.on_change = _on_type_change

    def _close(_e=None) -> None:
        dlg.open = False
        page.update()

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

        out_path = _auto_filename(export_type, bucket)

        try:
            if export_type == "ledger":
                saved = export_ledger_to_csv(db_path, out_path)
            elif export_type == "cashflow":
                saved = export_cashflow_to_csv(db_path, out_path, bucket=bucket, fiat=fiat)
            elif export_type == "netto":
                saved = export_netto_invested_to_csv(db_path, out_path, bucket=bucket, fiat=fiat)
            elif export_type == "positions":
                saved = export_positions_to_csv(db_path, out_path)
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
