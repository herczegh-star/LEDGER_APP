"""Ledger view: audit table of canonical RawRow ledger rows.

Public API:
    build_ledger_view(page, db_path, on_after_reverse) -> (view, run_fn)

    filter_and_sort_ledger(rows, search, type_filter, venue_filter, sort_dir)
        -> list[RawRow]
        Pure helper — testable without a Flet page.

UI only. Computation: LedgerService (read) + reverse_trade (write).
No data-analysis logic lives here.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional, Tuple

import flet as ft

from core.services.ui_facade import get_ledger_rows, reverse_trade

# ── Color palette ────────────────────────────────────────────────────────────
BG_CARD = "#131922"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#64748b"
GREEN   = "#22c55e"
RED     = "#ef4444"
ORANGE  = "#f97316"
BLUE    = "#1d4ed8"

# Type → display color
_TYPE_COLORS = {
    "BUY":      GREEN,
    "SELL":     RED,
    "REVERSAL": ORANGE,
    "TRANSFER": BLUE,
    "FEE":      T_MUT,
}

_ZERO = Decimal("0")


# ── Pure filter/sort helper ──────────────────────────────────────────────────

def filter_and_sort_ledger(
    rows: list,
    search: str = "",
    type_filter: str = "",    # "" = all types
    venue_filter: str = "",   # "" = all venues
    sort_dir: str = "desc",   # "desc" | "asc"  (by timestamp)
) -> list:
    """Filter and sort a list of RawRow objects.

    Pure function — no UI dependencies, safe to unit-test directly.

    Args:
        rows:         All ledger rows (any order).
        search:       Case-insensitive substring filter across asset, trade_id
                      (row.id), venue, note, and type.  Empty = no filter.
        type_filter:  Keep only rows with this exact type.  Empty = all.
        venue_filter: Keep only rows with this exact venue.  Empty = all.
        sort_dir:     "desc" = newest first (default), "asc" = oldest first.

    Returns:
        New list — input is not mutated.
    """
    result = list(rows)

    # ── Search: substring across key text fields ──────────────────────────────
    if search:
        needle = search.strip().lower()
        result = [
            r for r in result
            if (
                needle in (r.asset or "").lower()
                or needle in (r.id or "").lower()
                or needle in (r.venue or "").lower()
                or needle in (r.note or "").lower()
                or needle in (r.type or "").lower()
            )
        ]

    # ── Type filter ───────────────────────────────────────────────────────────
    if type_filter:
        result = [r for r in result if r.type == type_filter]

    # ── Venue filter ──────────────────────────────────────────────────────────
    if venue_filter:
        result = [r for r in result if r.venue == venue_filter]

    # ── Sort by timestamp ─────────────────────────────────────────────────────
    reverse_sort = sort_dir == "desc"
    result.sort(
        key=lambda r: (r.timestamp, r.id or ""),
        reverse=reverse_sort,
    )

    return result


# ── Display helpers ──────────────────────────────────────────────────────────

def _fmt_amount(val: Decimal) -> str:
    s = f"{val:,.8f}".rstrip("0")
    if "." in s:
        _, dec = s.split(".")
        if len(dec) < 2:
            s = f"{val:,.2f}"
    return s


def _short_id(trade_id: Optional[str], n: int = 8) -> str:
    if not trade_id:
        return "—"
    return trade_id[:n] + "…" if len(trade_id) > n else trade_id


# ── View builder ─────────────────────────────────────────────────────────────

def build_ledger_view(
    page: ft.Page,
    db_path: str,
    on_after_reverse: Optional[Callable] = None,
) -> Tuple[ft.Column, Callable]:
    """Return (view_control, run_fn) for the Ledger tab.

    Args:
        page:              Flet page instance.
        db_path:           SQLite ledger path.
        on_after_reverse:  Optional callback invoked after a successful reversal
                           so other views (Reports, Positions, Health, Dashboard)
                           can refresh.  Called in addition to reloading this view.
    """
    # ── Cached rows (loaded by run_rows, filtered by _render) ────────────────
    _rows_holder: list = []

    # ── Controls ─────────────────────────────────────────────────────────────
    tf_search = ft.TextField(
        hint_text="Search asset, trade ID, venue, note, type…",
        prefix_icon="search",
        bgcolor=BG_CARD,
        border_color=BORDER,
        color=T_PRI,
        hint_style=ft.TextStyle(color=T_MUT),
        text_size=13,
        height=40,
        content_padding=ft.padding.symmetric(0, 12),
        expand=True,
    )

    dd_type = ft.Dropdown(
        label="Type",
        width=130,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[ft.dropdown.Option("", "All types")],
        value="",
    )

    dd_venue = ft.Dropdown(
        label="Venue",
        width=140,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[ft.dropdown.Option("", "All venues")],
        value="",
    )

    dd_sort = ft.Dropdown(
        label="Sort",
        width=160,
        bgcolor=BG_CARD,
        border_color=BORDER,
        text_style=ft.TextStyle(color=T_PRI, size=13),
        options=[
            ft.dropdown.Option("desc", "Newest first"),
            ft.dropdown.Option("asc",  "Oldest first"),
        ],
        value="desc",
    )

    status_txt = ft.Text("", size=12, color=T_MUT)
    table_area = ft.Column([], scroll=ft.ScrollMode.AUTO, expand=True)

    # ── Confirmation dialog for Reverse ───────────────────────────────────────
    def _confirm_reverse(trade_id: str, run_fn: Callable) -> None:
        """Open a confirmation dialog; on confirm call reverse_trade()."""
        confirm_dlg: ft.AlertDialog

        def _do_reverse(_e=None) -> None:
            confirm_dlg.open = False
            page.update()
            try:
                rev_rows = reverse_trade(db_path, trade_id)
            except ValueError as exc:
                page.show_dialog(ft.SnackBar(ft.Text(str(exc)), duration=3000))
                return
            page.show_dialog(ft.SnackBar(
                ft.Text(f"{len(rev_rows)} reversal row(s) appended"),
                duration=3000,
            ))
            run_fn()                 # reload this ledger view
            if on_after_reverse:
                on_after_reverse()   # refresh Dashboard / Reports / Positions / Health

        def _cancel(_e=None) -> None:
            confirm_dlg.open = False
            page.update()

        confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(
                "Reverse trade?",
                color=ORANGE,
                size=15,
                weight=ft.FontWeight.BOLD,
            ),
            bgcolor=BG_HDR,
            content=ft.Column(
                [
                    ft.Text(
                        f"Trade ID: {_short_id(trade_id, 12)}",
                        size=12,
                        color=T_PRI,
                        font_family="monospace",
                    ),
                    ft.Text(
                        "REVERSAL rows negate the original.\n"
                        "Original rows are never modified.",
                        size=12,
                        color=T_MUT,
                    ),
                ],
                spacing=8,
                tight=True,
                width=360,
            ),
            actions=[
                ft.TextButton(
                    "Cancel",
                    on_click=_cancel,
                    style=ft.ButtonStyle(color=T_MUT),
                ),
                ft.TextButton(
                    "Confirm",
                    on_click=_do_reverse,
                    style=ft.ButtonStyle(color=ORANGE),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(confirm_dlg)

    # ── Render: apply filter/sort and rebuild table ───────────────────────────
    def _render() -> None:
        visible = filter_and_sort_ledger(
            _rows_holder,
            search=tf_search.value or "",
            type_filter=dd_type.value or "",
            venue_filter=dd_venue.value or "",
            sort_dir=dd_sort.value or "desc",
        )

        if not visible:
            msg = (
                "No matching rows."
                if _rows_holder
                else "Ledger is empty."
            )
            table_area.controls = [ft.Text(msg, color=T_MUT, size=13)]
            status_txt.value = (
                f"0 of {len(_rows_holder)} rows"
                if _rows_holder
                else "0 rows"
            )
            page.update()
            return

        # ── Column headers ────────────────────────────────────────────────────
        col_defs = [
            ft.DataColumn(ft.Text("Timestamp",  color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Type",       color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Asset",      color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Amount",     color=T_MUT, size=11, weight=ft.FontWeight.BOLD), numeric=True),
            ft.DataColumn(ft.Text("Currency",   color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Trade ID",   color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Venue",      color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Note",       color=T_MUT, size=11, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("",           color=T_MUT, size=11)),  # action column
        ]

        # ── Data rows ─────────────────────────────────────────────────────────
        data_rows = []
        for row in visible:
            type_color  = _TYPE_COLORS.get(row.type, T_MUT)
            amt_color   = GREEN if row.amount > _ZERO else (RED if row.amount < _ZERO else T_MUT)
            ts_str      = row.timestamp.strftime("%Y-%m-%d %H:%M:%S") if row.timestamp else "—"
            note_display = (row.note or "")[:40] + ("…" if len(row.note or "") > 40 else "")

            # Reverse button — hidden for REVERSAL rows (no point reversing a reversal)
            if row.type != "REVERSAL" and row.id:
                tid = row.id  # capture for closure

                def _make_reverse_cb(t: str) -> Callable:
                    def _cb(_e=None) -> None:
                        _confirm_reverse(t, run_rows)
                    return _cb

                action_cell = ft.DataCell(
                    ft.TextButton(
                        "Reverse",
                        on_click=_make_reverse_cb(tid),
                        style=ft.ButtonStyle(
                            color=ORANGE,
                            padding=ft.padding.symmetric(0, 4),
                        ),
                    )
                )
            else:
                action_cell = ft.DataCell(ft.Text(""))

            data_rows.append(ft.DataRow(cells=[
                ft.DataCell(ft.Text(ts_str,                  color=T_MUT,      size=11, font_family="monospace")),
                ft.DataCell(ft.Text(row.type,                color=type_color,  size=11, weight=ft.FontWeight.W_600)),
                ft.DataCell(ft.Text(row.asset,               color=T_PRI,       size=11, weight=ft.FontWeight.W_600)),
                ft.DataCell(ft.Text(_fmt_amount(row.amount), color=amt_color,   size=11, font_family="monospace")),
                ft.DataCell(ft.Text(row.currency or "—",     color=T_MUT,       size=11)),
                ft.DataCell(ft.Text(_short_id(row.id),       color=T_MUT,       size=11, font_family="monospace")),
                ft.DataCell(ft.Text(row.venue or "—",        color=T_MUT,       size=11)),
                ft.DataCell(ft.Text(note_display or "—",     color=T_MUT,       size=11)),
                action_cell,
            ]))

        table_area.controls = [
            ft.SingleChildScrollView(
                content=ft.DataTable(
                    columns=col_defs,
                    rows=data_rows,
                    border=ft.border.all(1, BORDER),
                    border_radius=8,
                    vertical_lines=ft.BorderSide(1, BORDER),
                    heading_row_color=BG_HDR,
                    data_row_color={"hovered": "#1e2a3a"},
                    column_spacing=16,
                    data_text_style=ft.TextStyle(size=11),
                ),
                scroll_direction=ft.ScrollMode.HORIZONTAL,
            )
        ]

        shown = len(visible)
        total = len(_rows_holder)
        status_txt.value = (
            f"{shown} of {total} row{'s' if total != 1 else ''}"
            if shown != total
            else f"{total} row{'s' if total != 1 else ''}"
        )
        page.update()

    # ── Control callbacks (filter/sort only — no DB re-fetch) ─────────────────
    tf_search.on_change = lambda _e: _render()
    dd_type.on_change   = lambda _e: _render()
    dd_venue.on_change  = lambda _e: _render()
    dd_sort.on_change   = lambda _e: _render()

    # ── Main run function (fetches from DB, updates dropdowns, renders) ────────
    def run_rows(e=None) -> None:
        nonlocal _rows_holder
        _rows_holder = get_ledger_rows(db_path)

        # Rebuild type dropdown from distinct types in data
        types = sorted({r.type for r in _rows_holder})
        dd_type.options = [ft.dropdown.Option("", "All types")] + [
            ft.dropdown.Option(t, t) for t in types
        ]
        if dd_type.value and dd_type.value not in types:
            dd_type.value = ""

        # Rebuild venue dropdown from distinct venues in data
        venues = sorted({r.venue for r in _rows_holder if r.venue})
        dd_venue.options = [ft.dropdown.Option("", "All venues")] + [
            ft.dropdown.Option(v, v) for v in venues
        ]
        if dd_venue.value and dd_venue.value not in venues:
            dd_venue.value = ""

        _render()

    # ── View layout ───────────────────────────────────────────────────────────
    controls_row = ft.Row(
        [
            tf_search,
            dd_type,
            dd_venue,
            dd_sort,
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    view = ft.Column(
        [
            ft.Text("Ledger", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
            ft.Divider(height=1, color=BORDER),
            controls_row,
            status_txt,
            table_area,
        ],
        spacing=12,
        expand=True,
    )

    return view, run_rows
