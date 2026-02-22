"""Add Trade dialog: Flet modal for entering a new BUY/SELL trade.

Public API:
    open_add_trade_dialog(page, db_path, on_success) -> None

UI only. All computation delegated to core/services/trade_service.py.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

import flet as ft

from core.constants import TRADE_TYPES
from core.services.ui_facade import AddTradeRequestDTO, add_trade

# ── Color palette (same as app_flet.py) ────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
BLUE    = "#1d4ed8"


def open_add_trade_dialog(
    page: ft.Page,
    db_path: str,
    on_success: Callable[[], None],
) -> None:
    """Build and open the Add Trade modal dialog."""

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── Form fields ─────────────────────────────────────────────────────────
    _DIALOG_TYPES = [t for t in TRADE_TYPES if t in ("BUY", "SELL")]
    dd_type = ft.Dropdown(
        label="Type",
        width=120,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[ft.dropdown.Option(t, t) for t in _DIALOG_TYPES],
        value=_DIALOG_TYPES[0],
    )

    tf_timestamp = ft.TextField(
        label="Date / Time",
        value=now_str,
        hint_text="YYYY-MM-DDTHH:MM:SS",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_base_asset = ft.TextField(
        label="Base Asset",
        hint_text="BTC",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        width=120,
        capitalization=ft.TextCapitalization.CHARACTERS,
    )

    tf_base_amount = ft.TextField(
        label="Base Amount",
        hint_text="0.5",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    dd_quote_currency = ft.Dropdown(
        label="Quote Currency",
        width=130,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[
            ft.dropdown.Option("EUR", "EUR"),
            ft.dropdown.Option("CZK", "CZK"),
        ],
        value="EUR",
    )

    tf_quote_amount = ft.TextField(
        label="Quote Amount",
        hint_text="25000",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_venue = ft.TextField(
        label="Venue",
        hint_text="kraken",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_fee_amount = ft.TextField(
        label="Fee Amount (optional)",
        hint_text="5",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_fee_currency = ft.TextField(
        label="Fee Currency (optional)",
        hint_text="EUR / BTC / …",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_note = ft.TextField(
        label="Note (optional)",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    error_text = ft.Text("", color=RED, size=12)

    # ── Dialog reference (needed to close it) ───────────────────────────────
    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        dlg.open = False
        page.update()

    def _submit(_e=None) -> None:
        error_text.value = ""
        page.update()

        # Parse fields
        try:
            ts = datetime.fromisoformat(tf_timestamp.value.strip())
        except ValueError:
            error_text.value = "Invalid date/time – use YYYY-MM-DDTHH:MM:SS"
            page.update()
            return

        try:
            base_amount = Decimal(tf_base_amount.value.strip())
        except InvalidOperation:
            error_text.value = "Invalid base amount"
            page.update()
            return

        try:
            quote_amount = Decimal(tf_quote_amount.value.strip())
        except InvalidOperation:
            error_text.value = "Invalid quote amount"
            page.update()
            return

        fee_amount: "Decimal | None" = None
        raw_fee = tf_fee_amount.value.strip()
        if raw_fee:
            try:
                fee_amount = Decimal(raw_fee)
            except InvalidOperation:
                error_text.value = "Invalid fee amount"
                page.update()
                return

        fee_currency: "str | None" = tf_fee_currency.value.strip() or None

        request = AddTradeRequestDTO(
            type=dd_type.value,
            timestamp=ts,
            asset=tf_base_asset.value.strip(),
            amount=base_amount,
            currency=dd_quote_currency.value,
            price=None,
            venue=tf_venue.value.strip(),
            quote_amount=quote_amount,
            fee_amount=fee_amount,
            fee_currency=fee_currency,
            note=tf_note.value.strip() or None,
        )

        result = add_trade(request, db_path)

        if not result.success:
            error_text.value = result.error_message or "Unknown error"
            page.update()
            return

        _close()
        if result.n_rows_added > 0:
            page.show_dialog(ft.SnackBar(
                ft.Text(f"Trade saved — {result.n_rows_added} row(s) inserted", color=GREEN),
                duration=3000,
            ))
        else:
            page.show_dialog(ft.SnackBar(
                ft.Text("All rows were duplicates — nothing new added", color="#f97316"),
                duration=4000,
            ))
        on_success()

    # ── Layout ──────────────────────────────────────────────────────────────
    form = ft.Column(
        [
            ft.Row([dd_type, tf_timestamp], spacing=12),
            ft.Row([tf_base_asset, tf_base_amount], spacing=12),
            ft.Row([dd_quote_currency, tf_quote_amount], spacing=12),
            tf_venue,
            ft.Row([tf_fee_amount, tf_fee_currency], spacing=12),
            tf_note,
            error_text,
        ],
        spacing=12,
        width=480,
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Add Trade", size=16, weight=ft.FontWeight.BOLD, color=T_PRI),
        bgcolor=BG_HDR,
        content=form,
        actions=[
            ft.TextButton(
                "Cancel",
                on_click=_close,
                style=ft.ButtonStyle(color=T_MUT),
            ),
            ft.TextButton(
                "Add",
                on_click=_submit,
                style=ft.ButtonStyle(color=GREEN),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    page.show_dialog(dlg)
