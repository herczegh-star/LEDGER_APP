"""Add Trade dialog: Flet modal for entering a new trade (BUY/SELL/TRANSFER/FEE/SWAP).

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
    # SWAP is a UX-only type decomposed to SELL+BUY by the facade.
    _DIALOG_TYPES = [t for t in TRADE_TYPES if t != "REVERSAL"] + ["SWAP"]

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

    # ── SWAP-only fields (hidden by default) ─────────────────────────────────
    tf_to_asset = ft.TextField(
        label="To Asset",
        hint_text="USDC",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        width=120,
        capitalization=ft.TextCapitalization.CHARACTERS,
        visible=False,
    )

    tf_received_amount = ft.TextField(
        label="Received Amount",
        hint_text="95000",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
        visible=False,
    )

    # ── Standard fields ───────────────────────────────────────────────────────
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

    tf_to_venue = ft.TextField(
        label="To Venue",
        hint_text="trezor",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
        visible=False,
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

    # ── SWAP preview ──────────────────────────────────────────────────────────
    preview_col = ft.Column([], spacing=4)
    preview_container = ft.Container(
        content=ft.Column(
            [
                ft.Text("Preview — výsledné řádky v ledgeru", size=11, color=T_MUT),
                ft.Container(height=4),
                preview_col,
            ],
            spacing=4,
        ),
        bgcolor="#0a0f18",
        border=ft.border.all(1, BORDER),
        border_radius=6,
        padding=10,
        visible=False,
    )

    # ── Dialog reference (needed to close it) ───────────────────────────────
    dlg: ft.AlertDialog

    def _close(_e=None) -> None:
        page.pop_dialog()

    # ── Preview helpers ───────────────────────────────────────────────────────
    def _build_preview_rows() -> list:
        """Compute preview row dicts for SWAP without touching the DB."""
        try:
            fa  = tf_base_asset.value.strip().upper()     or "FROM"
            ta  = tf_to_asset.value.strip().upper()       or "TO"
            fam = Decimal(tf_base_amount.value.strip())
            ram = Decimal(tf_received_amount.value.strip())
            ven = tf_venue.value.strip().lower()           or "venue"
            if fam <= 0 or ram <= 0:
                return []
        except (InvalidOperation, Exception):
            return []

        rate = ram / fam
        rows = [
            {"type": "SELL", "asset": fa,  "amount": f"-{fam}", "currency": ta,  "price": f"{rate:.6f}", "venue": ven},
            {"type": "BUY",  "asset": ta,  "amount": f"+{ram}", "currency": ta,  "price": "1",           "venue": ven},
        ]

        raw_fee = tf_fee_amount.value.strip()
        if raw_fee:
            try:
                fa_amt = Decimal(raw_fee)
                if fa_amt > 0:
                    fee_cur = tf_fee_currency.value.strip().upper() or ta
                    rows.append({"type": "FEE", "asset": fee_cur, "amount": f"-{fa_amt}", "currency": fee_cur, "price": "1", "venue": ven})
            except InvalidOperation:
                pass

        return rows

    def _update_preview(_e=None) -> None:
        if dd_type.value != "SWAP":
            return
        rows = _build_preview_rows()
        if not rows:
            preview_col.controls = [
                ft.Text(
                    "Vyplňte From Asset, From Amount, To Asset, Received Amount",
                    size=11, color=T_MUT, italic=True,
                )
            ]
        else:
            _COL_W = [55, 65, 110, 80, 100, 75]
            _HDRS  = ["TYPE", "ASSET", "AMOUNT", "CURRENCY", "PRICE", "VENUE"]
            _TYPE_COLOR = {"SELL": RED, "BUY": GREEN, "FEE": T_MUT}

            header = ft.Row(
                [ft.Text(h, size=10, color=T_MUT, width=w) for h, w in zip(_HDRS, _COL_W)],
                spacing=6,
            )
            data_rows = [
                ft.Row(
                    [
                        ft.Text(r["type"],     size=11, color=_TYPE_COLOR.get(r["type"], T_PRI), width=_COL_W[0]),
                        ft.Text(r["asset"],    size=11, color=T_PRI, width=_COL_W[1]),
                        ft.Text(r["amount"],   size=11, color=T_PRI, width=_COL_W[2]),
                        ft.Text(r["currency"], size=11, color=T_MUT, width=_COL_W[3]),
                        ft.Text(r["price"],    size=11, color=T_MUT, width=_COL_W[4]),
                        ft.Text(r["venue"],    size=11, color=T_MUT, width=_COL_W[5]),
                    ],
                    spacing=6,
                )
                for r in rows
            ]
            preview_col.controls = [header, ft.Divider(height=1, color=BORDER), *data_rows]
        page.update()

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

        # Parse amount (from_amount for SWAP, base amount otherwise)
        try:
            base_amount = Decimal(tf_base_amount.value.strip())
        except InvalidOperation:
            error_text.value = "Invalid amount"
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

        # ── SWAP branch ───────────────────────────────────────────────────────
        if dd_type.value == "SWAP":
            to_asset_val = tf_to_asset.value.strip()
            if not to_asset_val:
                error_text.value = "To Asset is required for SWAP"
                page.update()
                return
            try:
                received_amt = Decimal(tf_received_amount.value.strip())
            except InvalidOperation:
                error_text.value = "Invalid received amount"
                page.update()
                return
            if received_amt <= Decimal("0"):
                error_text.value = "Received amount must be > 0"
                page.update()
                return

            request = AddTradeRequestDTO(
                type="SWAP",
                timestamp=ts,
                asset=tf_base_asset.value.strip(),
                amount=base_amount,
                currency="",
                price=Decimal("0"),
                quote_amount=None,
                fee_amount=fee_amount,
                fee_currency=fee_currency,
                note=tf_note.value.strip() or None,
                venue=tf_venue.value.strip(),
                to_venue=None,
                to_asset=to_asset_val,
                received_amount=received_amt,
            )

        # ── All other types ───────────────────────────────────────────────────
        else:
            # Parse price (default 0 when blank)
            raw_price = tf_price.value.strip()
            try:
                price = Decimal(raw_price) if raw_price else Decimal("0")
            except InvalidOperation:
                error_text.value = "Invalid price"
                page.update()
                return

            # To Venue — required for TRANSFER, ignored for all other types
            to_venue_raw = tf_to_venue.value.strip() if dd_type.value == "TRANSFER" else ""
            if dd_type.value == "TRANSFER":
                if not to_venue_raw:
                    error_text.value = "To Venue is required for TRANSFER"
                    page.update()
                    return
                if to_venue_raw.lower() == tf_venue.value.strip().lower():
                    error_text.value = "To Venue must differ from From Venue"
                    page.update()
                    return

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
                to_venue=to_venue_raw or None,
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
        t           = dd_type.value
        is_transfer = (t == "TRANSFER")
        is_fee_type = (t == "FEE")
        is_swap     = (t == "SWAP")

        # TRANSFER: show To Venue field, rename Venue → From Venue
        tf_to_venue.visible = is_transfer
        tf_venue.label = "From Venue" if is_transfer else "Venue"

        # SWAP: show to_asset + received_amount; hide currency/price/total
        tf_to_asset.visible        = is_swap
        tf_received_amount.visible = is_swap
        tf_currency.visible        = not is_swap
        tf_price.visible           = not is_swap
        tf_total.visible           = not is_swap

        # Relabel base fields for SWAP
        tf_base_asset.label  = "From Asset"  if is_swap else "Asset"
        tf_base_amount.label = "From Amount" if is_swap else "Amount"

        # Fee hint only for TRANSFER
        fee_hint.visible = is_transfer

        # FEE type IS the fee — bundled fee fields are not applicable
        tf_fee_amount.disabled   = is_fee_type
        tf_fee_currency.disabled = is_fee_type
        if is_fee_type:
            tf_fee_amount.value   = ""
            tf_fee_currency.value = ""

        # Preview only for SWAP
        preview_container.visible = is_swap
        if is_swap:
            _update_preview()

        page.update()

    # ── Hook assignments ─────────────────────────────────────────────────────
    dd_type.on_select        = _on_type_change
    tf_price.on_change       = _recalc_total
    tf_total.on_change       = _recalc_unit_price

    # amount on_change: recalc for BUY/SELL + update preview for SWAP
    def _on_base_amount_change(_e=None) -> None:
        _recalc_on_amount()
        _update_preview()

    tf_base_amount.on_change    = _on_base_amount_change
    tf_base_asset.on_change     = _update_preview
    tf_to_asset.on_change       = _update_preview
    tf_received_amount.on_change = _update_preview
    tf_venue.on_change          = _update_preview
    tf_fee_amount.on_change     = _update_preview
    tf_fee_currency.on_change   = _update_preview

    # ── Layout ──────────────────────────────────────────────────────────────
    form = ft.Column(
        [
            ft.Row([dd_type, tf_timestamp], spacing=12),
            ft.Row([tf_base_asset, tf_base_amount], spacing=12),
            ft.Row([tf_to_asset, tf_received_amount], spacing=12),   # SWAP only
            ft.Row([tf_currency, tf_price, tf_total], spacing=12),   # hidden for SWAP
            tf_venue,
            tf_to_venue,
            ft.Row([tf_fee_amount, tf_fee_currency], spacing=12),
            fee_hint,
            tf_note,
            preview_container,
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
