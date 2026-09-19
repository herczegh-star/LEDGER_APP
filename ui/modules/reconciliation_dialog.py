"""Add Reconciliation Snapshot dialog: Flet modal for recording a real
account balance against one tracked cash account.

Public API:
    open_reconciliation_dialog(page, db_path, venue, account, currency, on_success) -> None

UI only. All validation/persistence delegated to
core/services/ui_facade.add_reconciliation_snapshot(). This is a dedicated,
separate action — NOT reachable from the generic Add Trade dialog (see
core/reconciliation.py: a reconciliation snapshot is a diagnostic fact, not
an accounting transaction).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

import flet as ft

from core.services.ui_facade import add_reconciliation_snapshot

# ── Color palette (same as add_trade_dialog.py) ─────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"


def open_reconciliation_dialog(
    page: ft.Page,
    db_path: str,
    venue: str,
    account: str,
    currency: str,
    on_success: Callable[[], None],
) -> None:
    """Build and open the Add Reconciliation Snapshot modal dialog for one
    tracked (venue, account, currency)."""

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    header = ft.Text(
        f"{venue.title()} / {account} — {currency}",
        size=13, color=T_MUT,
    )

    tf_reported_balance = ft.TextField(
        label="Reported Balance (skutečný zůstatek z účtu)",
        hint_text="12345.00",
        bgcolor=BG_CARD, border_color=BORDER, color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT), expand=True,
    )
    tf_as_of = ft.TextField(
        label="As Of (kdy reported balance platí)",
        value=now_str, hint_text="YYYY-MM-DDTHH:MM:SS",
        bgcolor=BG_CARD, border_color=BORDER, color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT), expand=True,
    )
    tf_note = ft.TextField(
        label="Note (optional)",
        bgcolor=BG_CARD, border_color=BORDER, color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT), expand=True,
    )
    error_text = ft.Text("", color=RED, size=12)

    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        page.pop_dialog()

    def _submit(_e=None) -> None:
        error_text.value = ""
        page.update()

        raw_balance = (tf_reported_balance.value or "").strip()
        try:
            reported_balance = Decimal(raw_balance)
        except InvalidOperation:
            error_text.value = "Invalid reported balance"
            page.update()
            return

        try:
            as_of = datetime.fromisoformat((tf_as_of.value or "").strip())
        except ValueError:
            error_text.value = "Invalid date/time – use YYYY-MM-DDTHH:MM:SS"
            page.update()
            return

        note: Optional[str] = (tf_note.value or "").strip() or None

        result = add_reconciliation_snapshot(
            db_path=db_path, venue=venue, account=account, currency=currency,
            reported_balance=reported_balance, as_of=as_of, note=note,
        )
        if not result.success:
            error_text.value = result.error_message or "Unknown error"
            page.update()
            return

        _close()
        page.show_dialog(ft.SnackBar(
            ft.Text("Reconciliation snapshot saved", color=GREEN),
            duration=3000,
        ))
        on_success()

    form = ft.Column(
        [
            header,
            ft.Container(height=8),
            tf_reported_balance,
            tf_as_of,
            tf_note,
            error_text,
        ],
        spacing=12,
        width=440,
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Add Reconciliation Snapshot", size=16, weight=ft.FontWeight.BOLD, color=T_PRI),
        bgcolor=BG_HDR,
        content=form,
        actions=[
            ft.TextButton("Cancel", on_click=_close, style=ft.ButtonStyle(color=T_MUT)),
            ft.TextButton("Save", on_click=_submit, style=ft.ButtonStyle(color=GREEN)),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
