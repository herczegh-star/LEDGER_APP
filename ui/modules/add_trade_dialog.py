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
from core.services.ui_facade import AddTradeRequestDTO, add_trade, get_known_account_labels
from ui.amount_mode import resolve_buy_order_total, resolve_gross_fee_net

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

    # ── SELL / BUY Amount Type (Phase 3B, extended to BUY by Phase BUY-FIX) ──
    # Removes the ambiguous "Total" field for SELL and BUY: the user must say
    # explicitly whether the number they're entering is the trade's own
    # value (before fee) or the actual cash amount that moved (after fee —
    # e.g. what a Revolut transaction detail screen shows). Whichever mode
    # is picked, the accounting core still only ever receives the pre-fee
    # quote_amount — cost_basis/cash_impact continue to be computed
    # downstream exactly as before (core/services/trade_service.py,
    # core/reports/positions.py — both unchanged). SELL and BUY resolve the
    # same "gross"/"net" mode strings through different pure functions
    # (ui/amount_mode.py) because fee direction inverts: for SELL, fee
    # subtracts from proceeds (net < gross); for BUY, fee adds to cash
    # outflow (net > gross) — see resolve_buy_order_total()'s docstring.
    dd_amount_mode = ft.Dropdown(
        label="Amount Type",
        width=240,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[
            ft.dropdown.Option("gross", "Gross (before fee)"),
            ft.dropdown.Option("net", "Net (cash received)"),
        ],
        value="gross",  # default matches today's existing behaviour exactly
        visible=False,  # SELL / BUY only
    )

    gross_net_preview = ft.Text("", size=11, color=T_MUT, visible=False)

    def _compute_amount_breakdown():
        """For SELL: returns (gross, fee, net). For BUY: returns
        (order_value, fee, total_cash_debited). Decimals from the current
        field values per the selected Amount Type, or None if the total is
        missing/invalid. fee defaults to 0 (unparseable fee is treated as 0
        for preview purposes only — submit-time validation is separate)."""
        raw_total = tf_total.value.strip() if tf_total.value else ""
        if not raw_total:
            return None
        try:
            total_val = Decimal(raw_total)
        except InvalidOperation:
            return None
        raw_fee = tf_fee_amount.value.strip() if tf_fee_amount.value else ""
        try:
            fee_val = Decimal(raw_fee) if raw_fee else Decimal("0")
        except InvalidOperation:
            fee_val = Decimal("0")

        if dd_type.value == "BUY":
            return resolve_buy_order_total(total_val, fee_val, dd_amount_mode.value)
        return resolve_gross_fee_net(total_val, fee_val, dd_amount_mode.value)

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

    # ── Cash account fields (Phase 2) ─────────────────────────────────────────
    # Combobox pattern: a dropdown of KNOWN existing account labels (queried
    # from the ledger via get_known_account_labels()) plus a plain text field
    # for typing a brand-new one. If the text field is filled it wins — we
    # never invent an account name, and leaving both empty stores account=None
    # (legacy/unassigned), exactly like today.
    _known_labels = get_known_account_labels(db_path)

    dd_account = ft.Dropdown(
        label="Source Cash Account",  # matches default dd_type value "BUY"
        hint_text="vyber existující účet",
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[ft.dropdown.Option(a, a) for a in _known_labels],
        expand=True,
        visible=True,
    )
    tf_account_new = ft.TextField(
        label="…nebo nový účet",
        hint_text="Osobní CZK",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
        visible=True,
    )

    dd_to_account = ft.Dropdown(
        label="Destination Account",
        hint_text="vyber existující účet",
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[ft.dropdown.Option(a, a) for a in _known_labels],
        expand=True,
        visible=False,
    )
    tf_to_account_new = ft.TextField(
        label="…nebo nový účet",
        hint_text="Investment CZK",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        label_style=ft.TextStyle(color=T_MUT),
        expand=True,
        visible=False,
    )

    def _effective_account(dd: ft.Dropdown, tf_new: ft.TextField) -> "str | None":
        """Typed value wins over dropdown selection. Empty -> None (never
        a guessed/default account name)."""
        typed = tf_new.value.strip() if tf_new.value else ""
        if typed:
            return typed
        return dd.value or None

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
            _COL_W = [50, 55, 100, 70, 85, 70]
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

    def _update_gross_net_preview(_e=None) -> None:
        """Live breakdown readout for SELL/BUY — makes the Amount Type
        interpretation explicit before the user hits Add, instead of a
        silently ambiguous single 'Amount' number."""
        if dd_type.value not in ("SELL", "BUY"):
            gross_net_preview.visible = False
            return
        gross_net_preview.visible = True
        result = _compute_amount_breakdown()
        if result is None:
            gross_net_preview.value = "Vyplňte Amount a Gross/Net částku pro náhled"
            gross_net_preview.color = T_MUT
        else:
            a, fee, b = result
            if dd_type.value == "SELL":
                gross_net_preview.value = (
                    f"Gross Amount: {a}    Fee: {fee}    Net Cash Impact: {b}"
                )
            else:  # BUY
                gross_net_preview.value = (
                    f"Order Value: {a}    Fee: {fee}    Total Cash Debited: {b}"
                )
            gross_net_preview.color = T_PRI

    def _on_amount_mode_change(_e=None) -> None:
        if dd_type.value == "SELL":
            tf_total.label = "Gross Amount" if dd_amount_mode.value == "gross" else "Net Cash Received"
        elif dd_type.value == "BUY":
            tf_total.label = "Order Value" if dd_amount_mode.value == "gross" else "Total Cash Debited"
        _update_gross_net_preview()
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

            # To Venue — required for TRANSFER, ignored for all other types.
            # Same-venue transfers ARE allowed when the account differs (e.g.
            # Revolut / Osobní CZK -> Revolut / Investment CZK) — the facade
            # validates (venue, account) identity; no client-side venue-only
            # equality check here (that would incorrectly block a legitimate
            # same-venue, different-account transfer).
            to_venue_raw = tf_to_venue.value.strip() if dd_type.value == "TRANSFER" else ""
            if dd_type.value == "TRANSFER" and not to_venue_raw:
                error_text.value = "To Venue is required for TRANSFER"
                page.update()
                return

            # Cash account (BUY/SELL/TRANSFER only — see _on_type_change).
            account_val = (
                _effective_account(dd_account, tf_account_new)
                if dd_type.value in ("BUY", "SELL", "TRANSFER") else None
            )
            to_account_val = (
                _effective_account(dd_to_account, tf_to_account_new)
                if dd_type.value == "TRANSFER" else None
            )

            # SELL/BUY: resolve Amount Type (gross/net) to an explicit
            # pre-fee quote_amount. The accounting core
            # (trade_service/compute_positions) is untouched — it always
            # computes cost_basis/cash_impact from quote_amount ± fee, so it
            # must always receive the pre-fee figure regardless of which way
            # the user entered it. TRANSFER/FEE keep today's exact behaviour
            # (quote_amount=None -> facade derives amount*price).
            quote_amount_override = None
            if dd_type.value == "SELL":
                if (
                    dd_amount_mode.value == "net"
                    and fee_amount is not None
                    and fee_currency
                    and fee_currency.upper() != tf_currency.value.strip().upper()
                ):
                    error_text.value = (
                        "Net mode assumes the fee is in the same currency as "
                        "the quote currency — switch to Gross mode otherwise"
                    )
                    page.update()
                    return

                gfn = _compute_amount_breakdown()
                if gfn is None:
                    error_text.value = "Invalid or missing Gross/Net amount"
                    page.update()
                    return
                gross, _fee_preview, _net_preview = gfn
                if gross <= Decimal("0"):
                    error_text.value = "Gross amount must be > 0"
                    page.update()
                    return
                quote_amount_override = gross
            elif dd_type.value == "BUY":
                if (
                    dd_amount_mode.value == "net"
                    and fee_amount is not None
                    and fee_currency
                    and fee_currency.upper() != tf_currency.value.strip().upper()
                ):
                    error_text.value = (
                        "Total Cash Debited mode assumes the fee is in the same "
                        "currency as the quote currency — switch to Order Value "
                        "mode otherwise"
                    )
                    page.update()
                    return

                bfn = _compute_amount_breakdown()
                if bfn is None:
                    error_text.value = "Invalid or missing Order Value / Total Cash Debited amount"
                    page.update()
                    return
                order_value, _fee_preview, _total_cash_preview = bfn
                if order_value <= Decimal("0"):
                    error_text.value = "Order value must be > 0 (check Total Cash Debited vs. fee)"
                    page.update()
                    return
                quote_amount_override = order_value

            request = AddTradeRequestDTO(
                type=dd_type.value,
                timestamp=ts,
                asset=tf_base_asset.value.strip(),
                amount=base_amount,
                currency=tf_currency.value.strip(),
                price=price,
                quote_amount=quote_amount_override,  # SELL/BUY: explicit pre-fee amount; else facade derives amount*price
                fee_amount=fee_amount,
                fee_currency=fee_currency,
                note=tf_note.value.strip() or None,
                venue=tf_venue.value.strip(),
                to_venue=to_venue_raw or None,
                account=account_val,
                to_account=to_account_val,
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

        # Cash account fields (Phase 2): BUY/SELL/TRANSFER only — SWAP and
        # standalone FEE entries have no single unambiguous cash-account leg
        # in this dialog's current simple form.
        is_buy_or_sell = t in ("BUY", "SELL")
        show_account = is_buy_or_sell or is_transfer
        dd_account.visible = show_account
        tf_account_new.visible = show_account
        if t == "SELL":
            dd_account.label = "Destination Cash Account"
        elif t == "BUY":
            dd_account.label = "Source Cash Account"
        elif is_transfer:
            dd_account.label = "Source Account"
        dd_to_account.visible = is_transfer
        tf_to_account_new.visible = is_transfer

        # SWAP: show to_asset + received_amount; hide currency/price/total
        tf_to_asset.visible        = is_swap
        tf_received_amount.visible = is_swap
        tf_currency.visible        = not is_swap
        tf_price.visible           = not is_swap
        tf_total.visible           = not is_swap

        # SELL / BUY Amount Type: explicit gross/net toggle + live preview.
        # Option labels and the Total field's label switch per type; the
        # underlying "gross"/"net" mode value is shared and carries over
        # when switching type (see resolve_buy_order_total() docstring for
        # why the same mode strings are meaningful for both).
        is_sell = (t == "SELL")
        is_buy_type = (t == "BUY")
        dd_amount_mode.visible = is_sell or is_buy_type
        if is_sell:
            dd_amount_mode.options = [
                ft.dropdown.Option("gross", "Gross (before fee)"),
                ft.dropdown.Option("net", "Net (cash received)"),
            ]
            tf_total.label = "Gross Amount" if dd_amount_mode.value == "gross" else "Net Cash Received"
        elif is_buy_type:
            dd_amount_mode.options = [
                ft.dropdown.Option("gross", "Order Value (before fee)"),
                ft.dropdown.Option("net", "Total Cash Debited (incl. fee)"),
            ]
            tf_total.label = "Order Value" if dd_amount_mode.value == "gross" else "Total Cash Debited"
        else:
            tf_total.label = "Total"
        _update_gross_net_preview()

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
    dd_amount_mode.on_select  = _on_amount_mode_change

    def _on_price_change(_e=None) -> None:
        _recalc_total()
        _update_gross_net_preview()
        page.update()

    def _on_total_change(_e=None) -> None:
        _recalc_unit_price()
        _update_gross_net_preview()
        page.update()

    def _on_fee_amount_change_combined(_e=None) -> None:
        _update_preview()          # SWAP preview
        _update_gross_net_preview()
        page.update()

    tf_price.on_change       = _on_price_change
    tf_total.on_change       = _on_total_change

    # amount on_change: recalc for BUY/SELL + update preview for SWAP
    def _on_base_amount_change(_e=None) -> None:
        _recalc_on_amount()
        _update_preview()
        _update_gross_net_preview()
        page.update()

    tf_base_amount.on_change    = _on_base_amount_change
    tf_base_asset.on_change     = _update_preview
    tf_to_asset.on_change       = _update_preview
    tf_received_amount.on_change = _update_preview
    tf_venue.on_change          = _update_preview
    tf_fee_amount.on_change     = _on_fee_amount_change_combined
    tf_fee_currency.on_change   = _update_preview

    # ── Layout ──────────────────────────────────────────────────────────────
    form = ft.Column(
        [
            ft.Row([dd_type, tf_timestamp], spacing=12),
            ft.Row([tf_base_asset, tf_base_amount], spacing=12),
            ft.Row([tf_to_asset, tf_received_amount], spacing=12),   # SWAP only
            ft.Row([tf_currency, tf_price, tf_total], spacing=12),   # hidden for SWAP
            ft.Row([dd_amount_mode], spacing=12),                    # SELL only
            gross_net_preview,                                       # SELL only
            tf_venue,
            tf_to_venue,
            ft.Row([dd_account, tf_account_new], spacing=12),
            ft.Row([dd_to_account, tf_to_account_new], spacing=12),
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
