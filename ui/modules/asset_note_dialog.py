"""Asset Note dialog: Flet modal for adding/editing/deleting a per-asset note.

Public API:
    open_asset_note_dialog(page, db_path, asset, existing_note, on_success) -> None

UI only. All logic delegated to core/services/ui_facade.set_asset_note() /
delete_asset_note() — pure metadata, no effect on quantity/cost_basis/WAC/
realized PnL/the RAW ledger.
"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from core.services.ui_facade import delete_asset_note, set_asset_note

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
BLUE    = "#1d4ed8"
RED     = "#ef4444"


def open_asset_note_dialog(
    page: ft.Page,
    db_path: str,
    asset: str,
    existing_note: Optional[str],
    on_success: Callable[[], None],
) -> None:
    """Build and open the Asset Note modal dialog."""

    tf_note = ft.TextField(
        label="Note",
        value=existing_note or "",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        width=480,
        multiline=True,
        min_lines=3,
        max_lines=6,
        autofocus=True,
    )

    error_text = ft.Text("", color=RED, size=12)

    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        page.pop_dialog()

    def _save(_e=None) -> None:
        error_text.value = ""
        result = set_asset_note(db_path, asset, tf_note.value or "")
        if not result.success:
            error_text.value = result.error_message
            page.update()
            return
        _close()
        on_success()

    def _delete(_e=None) -> None:
        error_text.value = ""
        delete_asset_note(db_path, asset)
        _close()
        on_success()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Asset note", size=16, weight=ft.FontWeight.BOLD, color=T_PRI),
        bgcolor=BG_HDR,
        content=ft.Column(
            [
                ft.Text(f"Asset: {asset}", size=13, color=T_MUT),
                tf_note,
                error_text,
            ],
            spacing=12,
            tight=True,
            width=480,
        ),
        actions=[
            ft.TextButton(
                "Delete",
                on_click=_delete,
                style=ft.ButtonStyle(color=RED),
                disabled=not existing_note,
            ),
            ft.TextButton(
                "Cancel",
                on_click=_close,
                style=ft.ButtonStyle(color=T_MUT),
            ),
            ft.TextButton(
                "Save",
                on_click=_save,
                style=ft.ButtonStyle(color=BLUE),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
