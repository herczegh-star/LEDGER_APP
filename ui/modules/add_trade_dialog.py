"""Add Trade dialog: Flet modal for entering a new trade (BUY/SELL/TRANSFER/FEE).

Public API:
    open_add_trade_dialog(page, db_path, on_success) -> None

UI only. All computation and validation delegated to core/services/ui_facade.add_trade().
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


def open_add_trade_dialog(
    page: ft.Page,
    db_path: str,
    on_success: Callable[[], None],
) -> None:
    """Build and open the Add Trade modal dialog."""

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── Form fields ─────────────────────────────────────────────────────────
    # All TRADE_TYPES except REVERSAL (REVERSAL uses the dedicated Reverse button).
    _DIALOG_TYPES = [t for t in TRADE_TYPES if t != "REVERSAL"]

    dd_type = ft.Dropdown(
        label="Type",
        width=140,
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
        label="Asset",
        hint_text="BTC",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        width=120,
        capitalization=ft.TextCapitalization.CHARACTERS,
    )

    tf_base_amount = ft.TextField(
        label="Amount",
        hint_text="0.5",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_currency = ft.TextField(
        label="Currency",
        hint_text="EUR / CZK / BTC …",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        width=140,
        capitalization=ft.TextCapitalization.CHARACTERS,
    )

    tf_price = ft.TextField(
        label="Unit Price",
        hint_text="e.g. 4 500 000",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
    )

    tf_total = ft.TextField(
        label="Total",
        hint_text="e.g. 4 500",
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

    fee_hint = ft.Text(
        "Fee will be stored as a separate FEE row with the same Trade ID.",
        size=11,
        color=T_MUT,
        italic=True,
        visible=False,
    )

    error_text = ft.Text("", color=RED, size=12)

    # ── Dialog reference (needed to close it) ───────────────────────────────
    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        page.pop_dialog()

    def _submit(_e=None) -> None:
        error_text.value = ""
        page.update()

        # Parse timestamp
        try:
            ts = datetime.fromisoformat(tf_timestamp.value.strip())
        except ValueError:
            error_text.value = "Invalid date/time – use YYYY-MM-DDTHH:MM:SS"
            page.update()
            return

        # Parse amount
        try:
            base_amount = Decimal(tf_base_amount.value.strip())
        except InvalidOperation:
            error_text.value = "Invalid amount"
            page.update()
            return

        # Parse price (default 0 when blank)
        raw_price = tf_price.value.strip()
        try:
            price = Decimal(raw_price) if raw_price else Decimal("0")
        except InvalidOperation:
            error_text.value = "Invalid price"
            page.update()
            return

        # Parse optional fee amount
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
            currency=tf_currency.value.strip(),
            price=price,
            quote_amount=None,   # facade derives amount*price for BUY/SELL
            fee_amount=fee_amount,
            fee_currency=fee_currency,
            note=tf_note.value.strip() or None,
            venue=tf_venue.value.strip(),
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

    # ── Auto-calculation helpers ─────────────────────────────────────────────
    def _recalc_total(_e=None) -> None:
        """Unit Price changed → Total = Amount × Unit Price."""
        try:
            amount = Decimal(tf_base_amount.value.strip())
            price  = Decimal(tf_price.value.strip().replace(" ", ""))
            tf_total.value = format((amount * price).normalize(), 'f')
            tf_total.update()
        except (InvalidOperation, Exception):
            pass

    def _recalc_unit_price(_e=None) -> None:
        """Total changed → Unit Price = Total / Amount."""
        try:
            amount = Decimal(tf_base_amount.value.strip())
            total  = Decimal(tf_total.value.strip().replace(" ", ""))
            if amount != 0:
                tf_price.value = format((total / amount).normalize(), 'f')
                tf_price.update()
        except (InvalidOperation, Exception):
            pass

    def _recalc_on_amount(_e=None) -> None:
        """Amount changed → keep whichever of Total/Unit Price is filled."""
        if tf_price.value.strip():
            _recalc_total()
        elif tf_total.value.strip():
            _recalc_unit_price()

    def _on_type_change(_e=None) -> None:
        t = dd_type.value
        is_transfer = (t == "TRANSFER")
        is_fee_type = (t == "FEE")
        fee_hint.visible = is_transfer
        # FEE type IS the fee — bundled fee fields are not applicable
        tf_fee_amount.disabled   = is_fee_type
        tf_fee_currency.disabled = is_fee_type
        if is_fee_type:
            tf_fee_amount.value   = ""
            tf_fee_currency.value = ""
        page.update()

    dd_type.on_change        = _on_type_change
    tf_price.on_change       = _recalc_total
    tf_total.on_change       = _recalc_unit_price
    tf_base_amount.on_change = _recalc_on_amount

    # ── Layout ──────────────────────────────────────────────────────────────
    form = ft.Column(
        [
            ft.Row([dd_type, tf_timestamp], spacing=12),
            ft.Row([tf_base_asset, tf_base_amount], spacing=12),
            ft.Row([tf_currency, tf_price, tf_total], spacing=12),
            tf_venue,
            ft.Row([tf_fee_amount, tf_fee_currency], spacing=12),
            fee_hint,
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
