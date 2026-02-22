"""Reverse Trade dialog: Flet modal for reversing an existing trade group.

Public API:
    open_reversal_dialog(page, db_path, on_success) -> None

UI only. All logic delegated to core/services/reversal_service.reverse_trade().
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from core.services.ui_facade import reverse_trade

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
ORANGE  = "#f97316"


def open_reversal_dialog(
    page: ft.Page,
    db_path: str,
    on_success: Callable[[], None],
) -> None:
    """Build and open the Reverse Trade modal dialog."""

    tf_trade_id = ft.TextField(
        label="Trade ID",
        hint_text="UUID of the trade to reverse",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        width=480,
        autofocus=True,
    )

    error_text = ft.Text("", color=RED, size=12)

    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        dlg.open = False
        page.update()

    def _submit(_e=None) -> None:
        error_text.value = ""
        trade_id = tf_trade_id.value.strip()
        if not trade_id:
            error_text.value = "Trade ID is required."
            page.update()
            return

        try:
            rows = reverse_trade(db_path, trade_id)
        except ValueError as exc:
            error_text.value = str(exc)
            page.update()
            return

        _close()
        page.show_dialog(ft.SnackBar(
            ft.Text(f"{len(rows)} reversal row(s) appended to ledger"),
            duration=3000,
        ))
        on_success()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(
            "Reverse Trade",
            size=16,
            weight=ft.FontWeight.BOLD,
            color=ORANGE,
        ),
        bgcolor=BG_HDR,
        content=ft.Column(
            [
                ft.Text(
                    "Creates REVERSAL rows that exactly negate the original trade.\n"
                    "The original rows are never modified.",
                    size=12,
                    color=T_MUT,
                ),
                tf_trade_id,
                error_text,
            ],
            spacing=12,
            tight=True,
            width=480,
        ),
        actions=[
            ft.TextButton(
                "Cancel",
                on_click=_close,
                style=ft.ButtonStyle(color=T_MUT),
            ),
            ft.TextButton(
                "Reverse",
                on_click=_submit,
                style=ft.ButtonStyle(color=ORANGE),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
