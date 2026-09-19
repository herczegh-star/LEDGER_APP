"""Cash Accounts view — read-only native-currency cash balances + drill-down.

Public API:
    build_cash_view(page, db_path) -> (ft.Container view, callable refresh_fn)

State machine:
    view_state[0] == "overview"  →  two tables:
        1. TRACKED cash accounts (account IS NOT NULL) — Venue / Account /
           Currency / Native Balance. This is the real Cash Reserve data.
        2. LEGACY / UNRECONCILED FIAT FLOWS (account IS NULL) — same
           columns, clearly labelled and explained, NEVER presented as a
           current balance (see core.reports.cash.compute_tracked_cash_reserve()
           for why account=None rows cannot be trusted as a balance).
    view_state[0] == "detail"    →  ledger movements for one (venue, account, currency)
        (works for a row from either section above)

Read-only: no writes, no WAC, no cost basis. `external` is never listed here
(see core.reports.cash.EXTERNAL_VENUE). "Unassigned Cash" is a DISPLAY
label applied only in this module for account=None rows — it is never
written to the database.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

import flet as ft

from core.reconciliation import DIFFERENCE, MATCH, STALE
from core.services.ui_facade import (
    get_cash_account_movements,
    get_cash_accounts_view,
    get_legacy_unassigned_fiat_flows,
    get_reconciliation_history,
)
from ui.modules.reconciliation_dialog import open_reconciliation_dialog

# ── Color palette (mirrors app_flet.py / venue_view.py) ─────────────────────
BG      = "#0b0f14"
BG_CARD = "#0f1621"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#7b8799"
GREEN   = "#16a34a"
RED     = "#ef4444"

_ZERO = Decimal("0")


def _money(v: Decimal, currency: str) -> str:
    n = f"{abs(v):,.2f}".replace(",", " ")
    sign = "-" if v < _ZERO else ""
    return f"{sign}{n} {currency}"


def _account_label(account: Optional[str]) -> str:
    """Display-only fallback label. account stays None in the data model —
    this string is never written back to the ledger."""
    return account if account else "Unassigned Cash"


_STATUS_COLOR = {MATCH: GREEN, DIFFERENCE: RED, STALE: "#f97316"}
_STATUS_LABEL = {MATCH: "✓ MATCH", DIFFERENCE: "⚠ DIFFERENCE", STALE: "⏱ STALE"}


def build_cash_view(page: ft.Page, db_path: str):
    """Build the Cash Accounts view.

    Returns:
        (view_container, refresh_fn)
    """
    view_state: list = ["overview"]          # "overview" | "detail"
    selected: list = [None]                  # (venue, account, currency)

    content_col = ft.Column(spacing=0)
    scroll_col = ft.Column(
        [
            ft.Text("Cash Accounts", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Container(height=12),
            content_col,
            ft.Container(height=80),
        ],
        spacing=0,
        scroll=ft.ScrollMode.AUTO,
    )

    # ── Navigation ───────────────────────────────────────────────────────────

    def _open_detail(venue: str, account: Optional[str], currency: str, _e=None) -> None:
        view_state[0] = "detail"
        selected[0] = (venue, account, currency)
        _render()

    def _back_to_overview(_e=None) -> None:
        view_state[0] = "overview"
        selected[0] = None
        _render()

    # ── Overview table ───────────────────────────────────────────────────────

    def _build_overview() -> ft.Control:
        def _row_click(venue, account, currency):
            return lambda e: _open_detail(venue, account, currency)

        def _table(rows_dto: list, empty_msg: str) -> ft.DataTable:
            col_defs = [
                ft.DataColumn(ft.Text("Venue", color=T_MUT, size=11)),
                ft.DataColumn(ft.Text("Account", color=T_MUT, size=11)),
                ft.DataColumn(ft.Text("Currency", color=T_MUT, size=11)),
                ft.DataColumn(ft.Text("Native Balance", color=T_MUT, size=11), numeric=True),
            ]
            data_rows = [
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(r.venue.title(), size=12, color=T_PRI)),
                        ft.DataCell(ft.Text(
                            _account_label(r.account), size=12,
                            color=T_PRI if r.account else T_MUT,
                            italic=not bool(r.account),
                        )),
                        ft.DataCell(ft.Text(r.currency, size=12, color=T_MUT)),
                        ft.DataCell(ft.Text(
                            _money(r.balance, r.currency), size=12,
                            color=GREEN if r.balance >= _ZERO else RED,
                            text_align=ft.TextAlign.RIGHT,
                        )),
                    ],
                    on_select_change=_row_click(r.venue, r.account, r.currency),
                )
                for r in rows_dto
            ]
            if not data_rows:
                data_rows = [ft.DataRow(cells=[
                    ft.DataCell(ft.Text(empty_msg, color=T_MUT)),
                    *[ft.DataCell(ft.Text("")) for _ in range(3)],
                ])]
            return ft.DataTable(
                columns=col_defs,
                rows=data_rows,
                border=ft.border.all(1, BORDER),
                border_radius=8,
                vertical_lines=ft.BorderSide(1, BORDER),
                heading_row_color=BG_HDR,
                data_row_color={"hovered": "#1e2a3a"},
                column_spacing=32,
                horizontal_margin=16,
                data_text_style=ft.TextStyle(size=12),
            )

        # ── Primary section: TRACKED cash accounts (account IS NOT NULL) ──────
        tracked_rows = get_cash_accounts_view(db_path)
        tracked_section = ft.Container(
            content=ft.Column(
                [ft.Row([_table(tracked_rows, "Žádné cash účty zatím nejsou v ledgeru.")],
                        scroll=ft.ScrollMode.AUTO)],
                spacing=12,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, "#223046"),
            border_radius=12,
            padding=16,
        )

        # ── Secondary section: LEGACY / UNRECONCILED fiat flows (account=None) ─
        # Historical fiat flows without a known cash account / starting
        # balance. NOT included in Cash Reserve — see
        # core.reports.cash.compute_tracked_cash_reserve() for why.
        legacy_rows = get_legacy_unassigned_fiat_flows(db_path)
        legacy_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("LEGACY / UNRECONCILED FIAT FLOWS", size=12,
                            weight=ft.FontWeight.W_600, color=T_MUT),
                    ft.Text(
                        "Historical fiat flows without a known cash account / "
                        "starting balance. Not included in Cash Reserve.",
                        size=11, color=T_MUT, italic=True,
                    ),
                    ft.Container(height=4),
                    ft.Row([_table(legacy_rows, "Žádné nepřiřazené fiat toky.")],
                           scroll=ft.ScrollMode.AUTO),
                ],
                spacing=8,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, "#223046"),
            border_radius=12,
            padding=16,
        )

        return ft.Column(
            [tracked_section, ft.Container(height=16), legacy_section],
            spacing=0,
        )

    # ── Reconciliation section (tracked accounts only, account is not None) ──

    def _build_reconciliation(venue: str, account: str, currency: str) -> ft.Control:
        """Calculated/Reported/Difference/Last Reconciled/Status summary +
        history table + 'Add Reconciliation Snapshot' action. Read-only
        except for the dedicated add-snapshot dialog — never reachable from
        the generic Add Trade dialog. See core/reconciliation.py."""
        history = get_reconciliation_history(db_path, venue, account, currency)

        def _open_add_snapshot(_e=None) -> None:
            open_reconciliation_dialog(
                page, db_path, venue, account, currency, on_success=_render,
            )

        add_button = ft.TextButton(
            "+ Add Reconciliation Snapshot",
            on_click=_open_add_snapshot,
            style=ft.ButtonStyle(color=GREEN),
        )

        if not history:
            summary = ft.Column(
                [
                    ft.Text("No reconciliation snapshot yet.", size=12, color=T_MUT, italic=True),
                    ft.Container(height=8),
                    add_button,
                ],
                spacing=4,
            )
        else:
            latest = history[-1]
            summary = ft.Column(
                [
                    ft.Row([
                        ft.Column([
                            ft.Text("Calculated Balance", size=11, color=T_MUT),
                            ft.Text(_money(latest.calculated_balance, currency), size=14, color=T_PRI),
                        ], spacing=2),
                        ft.Column([
                            ft.Text("Reported Balance", size=11, color=T_MUT),
                            ft.Text(_money(latest.reported_balance, currency), size=14, color=T_PRI),
                        ], spacing=2),
                        ft.Column([
                            ft.Text("Difference", size=11, color=T_MUT),
                            ft.Text(
                                _money(latest.difference, currency), size=14,
                                color=GREEN if latest.difference == _ZERO else RED,
                            ),
                        ], spacing=2),
                        ft.Column([
                            ft.Text("Last Reconciled", size=11, color=T_MUT),
                            ft.Text(latest.as_of.strftime("%Y-%m-%d %H:%M"), size=14, color=T_PRI),
                        ], spacing=2),
                        ft.Column([
                            ft.Text("Status", size=11, color=T_MUT),
                            ft.Text(
                                _STATUS_LABEL.get(latest.status, latest.status), size=14,
                                color=_STATUS_COLOR.get(latest.status, T_PRI), weight=ft.FontWeight.BOLD,
                            ),
                        ], spacing=2),
                    ], spacing=32, wrap=True),
                    ft.Container(height=8),
                    add_button,
                ],
                spacing=8,
            )

        history_col_defs = [
            ft.DataColumn(ft.Text("As Of", color=T_MUT, size=11)),
            ft.DataColumn(ft.Text("Calculated", color=T_MUT, size=11), numeric=True),
            ft.DataColumn(ft.Text("Reported", color=T_MUT, size=11), numeric=True),
            ft.DataColumn(ft.Text("Difference", color=T_MUT, size=11), numeric=True),
            ft.DataColumn(ft.Text("Status", color=T_MUT, size=11)),
            ft.DataColumn(ft.Text("Created At", color=T_MUT, size=11)),
            ft.DataColumn(ft.Text("Note", color=T_MUT, size=11)),
        ]
        history_rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(h.as_of.strftime("%Y-%m-%d %H:%M"), size=11, color=T_PRI)),
                ft.DataCell(ft.Text(_money(h.calculated_balance, currency), size=11, color=T_MUT,
                                     text_align=ft.TextAlign.RIGHT)),
                ft.DataCell(ft.Text(_money(h.reported_balance, currency), size=11, color=T_PRI,
                                     text_align=ft.TextAlign.RIGHT)),
                ft.DataCell(ft.Text(_money(h.difference, currency), size=11,
                                     color=GREEN if h.difference == _ZERO else RED,
                                     text_align=ft.TextAlign.RIGHT)),
                ft.DataCell(ft.Text(_STATUS_LABEL.get(h.status, h.status), size=11,
                                     color=_STATUS_COLOR.get(h.status, T_PRI))),
                ft.DataCell(ft.Text(h.created_at.strftime("%Y-%m-%d %H:%M"), size=10, color=T_MUT)),
                ft.DataCell(ft.Text(h.note or "", size=11, color=T_MUT)),
            ])
            for h in reversed(history)  # most recent first
        ]
        if not history_rows:
            history_rows = [ft.DataRow(cells=[
                ft.DataCell(ft.Text("Žádné reconciliation snapshoty.", color=T_MUT)),
                *[ft.DataCell(ft.Text("")) for _ in range(6)],
            ])]

        history_table = ft.Container(
            content=ft.Row(
                [ft.DataTable(
                    columns=history_col_defs,
                    rows=history_rows,
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                    vertical_lines=ft.BorderSide(1, BORDER),
                    heading_row_color=BG_HDR,
                    data_row_color={"hovered": "#1e2a3a"},
                    column_spacing=24,
                    horizontal_margin=16,
                    data_text_style=ft.TextStyle(size=11),
                )],
                scroll=ft.ScrollMode.AUTO,
            ),
        )

        return ft.Container(
            content=ft.Column(
                [
                    ft.Text("RECONCILIATION", size=12, weight=ft.FontWeight.W_600, color=T_MUT),
                    ft.Container(height=8),
                    summary,
                    ft.Container(height=12),
                    ft.Text("Reconciliation History", size=11, color=T_MUT),
                    ft.Container(height=4),
                    history_table,
                ],
                spacing=0,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, "#223046"),
            border_radius=12,
            padding=16,
        )

    # ── Detail: ledger movements for one account ────────────────────────────

    def _build_detail() -> ft.Control:
        venue, account, currency = selected[0]
        movements = sorted(
            get_cash_account_movements(db_path, venue, account, currency),
            key=lambda r: r.timestamp,
        )

        header = ft.Row(
            [
                ft.TextButton("← Back", on_click=_back_to_overview,
                              style=ft.ButtonStyle(color=T_MUT)),
                ft.Text(
                    f"{venue.title()} / {_account_label(account)} — {currency}",
                    size=18, weight=ft.FontWeight.BOLD, color=T_PRI,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        col_defs = [
            ft.DataColumn(ft.Text("Date", color=T_MUT, size=11)),
            ft.DataColumn(ft.Text("Type", color=T_MUT, size=11)),
            ft.DataColumn(ft.Text("Amount", color=T_MUT, size=11), numeric=True),
            ft.DataColumn(ft.Text("Trade ID", color=T_MUT, size=11)),
            ft.DataColumn(ft.Text("Note", color=T_MUT, size=11)),
        ]

        data_rows = [
            ft.DataRow(cells=[
                ft.DataCell(ft.Text(r.timestamp.strftime("%Y-%m-%d %H:%M"), size=11, color=T_PRI)),
                ft.DataCell(ft.Text(r.type, size=11, color=T_PRI)),
                ft.DataCell(ft.Text(
                    _money(r.amount, currency), size=11,
                    color=GREEN if r.amount >= _ZERO else RED,
                    text_align=ft.TextAlign.RIGHT,
                )),
                ft.DataCell(ft.Text(r.id or "", size=10, color=T_MUT)),
                ft.DataCell(ft.Text(r.note or "", size=11, color=T_MUT)),
            ])
            for r in movements
        ]

        if not data_rows:
            data_rows = [ft.DataRow(cells=[
                ft.DataCell(ft.Text("Žádné pohyby.", color=T_MUT)),
                *[ft.DataCell(ft.Text("")) for _ in range(4)],
            ])]

        table = ft.Container(
            content=ft.Row(
                [ft.DataTable(
                    columns=col_defs,
                    rows=data_rows,
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                    vertical_lines=ft.BorderSide(1, BORDER),
                    heading_row_color=BG_HDR,
                    data_row_color={"hovered": "#1e2a3a"},
                    column_spacing=24,
                    horizontal_margin=16,
                    data_text_style=ft.TextStyle(size=11),
                )],
                scroll=ft.ScrollMode.AUTO,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, "#223046"),
            border_radius=12,
            padding=16,
        )

        # Reconciliation only applies to TRACKED accounts (account IS NOT
        # NULL) — Legacy/Unassigned Cash is excluded from Cash Reserve by
        # design, so it has nothing meaningful to reconcile against.
        sections = [header, ft.Container(height=12)]
        if account is not None:
            sections += [_build_reconciliation(venue, account, currency), ft.Container(height=16)]
        sections.append(table)

        return ft.Column(sections, spacing=0)

    # ── Render dispatch ──────────────────────────────────────────────────────

    def _render() -> None:
        if view_state[0] == "overview":
            content_col.controls = [_build_overview()]
        else:
            content_col.controls = [_build_detail()]
        page.update()

    view = ft.Container(expand=True, padding=24, content=scroll_col)

    def refresh_fn() -> None:
        view_state[0] = "overview"
        selected[0] = None
        _render()

    return view, refresh_fn
