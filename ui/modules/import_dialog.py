"""Import Unified File dialog: Flet modal for importing a unified_format_raw file.

Public API:
    open_import_dialog(page, db_path, on_success) -> None

UI only. All file processing delegated to
core/services/unified_import_service.import_unified_file().
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from core.services.ui_facade import import_file, ImportResultDTO

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
BLUE    = "#1d4ed8"


def open_import_dialog(
    page: ft.Page,
    db_path: str,
    on_success: Callable[[], None],
) -> None:
    """Build and open the Import Unified File modal dialog."""

    # ── Mutable state (closure-captured) ────────────────────────────────────
    selected_path: list = [None]   # [str | None]

    # ── Widgets ─────────────────────────────────────────────────────────────
    file_label = ft.Text(
        "No file selected",
        size=12,
        color=T_MUT,
        expand=True,
        no_wrap=True,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    btn_browse = ft.TextButton(
        "Browse…",
        style=ft.ButtonStyle(color=BLUE),
    )

    tf_sheet = ft.TextField(
        label="Sheet name (optional)",
        hint_text="raw  /  trades  /  Sheet1  …",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    status_text = ft.Text("", size=12, color=T_MUT)

    # ── File picker (native OS dialog) ──────────────────────────────────────
    def on_file_picked(e: ft.FilePickerResultEvent) -> None:
        if not e.files:
            return
        path = e.files[0].path
        selected_path[0] = path
        file_label.value = path
        file_label.color = T_PRI
        status_text.value = ""
        page.update()

    file_picker = ft.FilePicker(on_result=on_file_picked)
    page.overlay.append(file_picker)
    page.update()

    def on_browse(_e=None) -> None:
        file_picker.pick_files(
            dialog_title="Select unified_format_raw file",
            allowed_extensions=["csv", "xlsx", "xlsm"],
            allow_multiple=False,
        )

    btn_browse.on_click = on_browse

    # ── Dialog reference (used in closures below) ────────────────────────────
    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        dlg.open = False
        page.update()

    def _import(_e=None) -> None:
        path = selected_path[0]
        if not path:
            status_text.value = "Please select a file first."
            status_text.color = RED
            page.update()
            return

        # Disable button while the service call runs
        btn_import.disabled = True
        btn_import.text = "Importing…"
        status_text.value = ""
        page.update()

        sheet = tf_sheet.value.strip() or None
        result: ImportResultDTO = import_file(db_path, path, sheet_name=sheet)

        if not result.success:
            btn_import.disabled = False
            btn_import.text = "Import"
            status_text.value = result.error_message or "Import failed."
            status_text.color = RED
            page.update()
            return

        _close()
        page.show_dialog(ft.SnackBar(
            ft.Text(
                f"{result.rows_added} row(s) added, "
                f"{result.rows_skipped} skipped (duplicates)"
            ),
            duration=3000,
        ))
        on_success()

    # ── Layout ──────────────────────────────────────────────────────────────
    form = ft.Column(
        [
            ft.Column(
                [
                    ft.Text("File", size=11, color=T_MUT),
                    ft.Row(
                        [file_label, btn_browse],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=4,
            ),
            ft.Divider(height=1, color=BORDER),
            tf_sheet,
            status_text,
        ],
        spacing=12,
        width=460,
    )

    btn_import = ft.TextButton(
        "Import",
        on_click=_import,
        style=ft.ButtonStyle(color=GREEN),
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(
            "Import Unified File",
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
            btn_import,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
