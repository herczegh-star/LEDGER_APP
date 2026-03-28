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

from core.constants import TRADE_TYPES
from core.services.ui_facade import get_ledger_rows, reverse_trade, delete_trade

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
    # Matches direct venue rows AND TRANSFER rows where note = destination venue.
    if venue_filter:
        vf = venue_filter.strip().lower()
        result = [
            r for r in result
            if (r.venue or "").lower() == vf
            or (r.type == "TRANSFER" and (r.note or "").strip().lower() == vf)
        ]

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

    # ── Column widths (px) ────────────────────────────────────────────────────
    _CW = [160, 75, 70, 110, 85, 130, 90, 0, 120]  # Ts,Type,Asset,Amt,Cur,ID,Venue,Note(expand),Actions

    def _hcell(label: str, w: int, right: bool = False, expand: bool = False) -> ft.Container:
        return ft.Container(
            ft.Text(label, color=T_MUT, size=12, weight=ft.FontWeight.BOLD,
                    text_align=ft.TextAlign.RIGHT if right else ft.TextAlign.LEFT,
                    no_wrap=True),
            expand=expand if expand else None,
            width=w if not expand else None,
            padding=ft.padding.symmetric(0, 8),
        )

    def _dcell(text: str, w: int, color: str = T_MUT,
               right: bool = False, mono: bool = False, expand: bool = False) -> ft.Container:
        return ft.Container(
            ft.Text(text, color=color, size=12,
                    font_family="monospace" if mono else None,
                    text_align=ft.TextAlign.RIGHT if right else ft.TextAlign.LEFT,
                    no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            expand=expand if expand else None,
            width=w if not expand else None,
            padding=ft.padding.symmetric(0, 8),
        )

    header_row = ft.Container(
        content=ft.Row([
            _hcell("Timestamp",  _CW[0]),
            _hcell("Type",       _CW[1]),
            _hcell("Asset",      _CW[2]),
            _hcell("Amount",     _CW[3], right=True),
            _hcell("Currency",   _CW[4]),
            _hcell("Trade ID",   _CW[5]),
            _hcell("Venue",      _CW[6]),
            _hcell("Note",       _CW[7], expand=True),
            ft.Container(width=_CW[8]),
        ], spacing=0),
        bgcolor=BG_HDR,
        padding=ft.padding.symmetric(10, 8),
        border=ft.border.only(bottom=ft.BorderSide(2, BORDER)),
    )

    data_col  = ft.Column([], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True,
                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    table_area = ft.Column([header_row, data_col], expand=True)

    _PAGE_SIZE = 50
    _page_idx  = [0]   # mutable so closures can mutate it

    # ── Confirmation dialog for Reverse ───────────────────────────────────────
    def _confirm_reverse(trade_id: str, run_fn: Callable) -> None:
        """Open a confirmation dialog; on confirm call reverse_trade()."""
        confirm_dlg: ft.AlertDialog

        def _do_reverse(_e=None) -> None:
            page.pop_dialog()
            try:
                rev_rows = reverse_trade(db_path, trade_id)
            except ValueError as exc:
                page.show_dialog(ft.SnackBar(ft.Text(str(exc)), duration=3000))
                return
            run_fn()
            if on_after_reverse:
                on_after_reverse()

        def _cancel(_e=None) -> None:
            page.pop_dialog()

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

    # ── Confirmation dialog for Delete ────────────────────────────────────────
    def _confirm_delete(trade_id: str, run_fn: Callable) -> None:
        """Potvrzení před hard DELETE — stejný vzor jako _confirm_reverse."""
        confirm_dlg: ft.AlertDialog

        def _do_delete(_e=None) -> None:
            page.pop_dialog()
            try:
                delete_trade(db_path, trade_id)
            except Exception:
                pass
            run_fn()
            if on_after_reverse:
                on_after_reverse()

        def _cancel(_e=None) -> None:
            page.pop_dialog()

        confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Smazat záznam?", color=RED, size=15, weight=ft.FontWeight.BOLD),
            bgcolor=BG_HDR,
            content=ft.Column(
                [
                    ft.Text(f"Trade ID: {_short_id(trade_id, 12)}", size=12, color=T_PRI, font_family="monospace"),
                    ft.Text(
                        "Řádek bude TRVALE odstraněn z databáze.\n"
                        "Tato akce je nevratná. Použij pouze pro opravy překlepů.",
                        size=12, color=RED,
                    ),
                ],
                spacing=8, tight=True, width=360,
            ),
            actions=[
                ft.TextButton("Zrušit", on_click=_cancel, style=ft.ButtonStyle(color=T_MUT)),
                ft.TextButton("Smazat natrvalo", on_click=_do_delete, style=ft.ButtonStyle(color=RED)),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(confirm_dlg)

    # ── Render: apply filter/sort and rebuild table ───────────────────────────
    def _render(reset_page: bool = False) -> None:
        if reset_page:
            _page_idx[0] = 0
        # Precompute set of already-reversed trade IDs from loaded rows.
        # A trade is reversed if a REVERSAL row exists with note "REVERSAL of {id}...".
        reversed_ids: set = set()
        for r in _rows_holder:
            if r.type == "REVERSAL" and r.note and r.note.startswith("REVERSAL of "):
                orig_id = r.note[len("REVERSAL of "):].split(";")[0].strip()
                reversed_ids.add(orig_id)

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
            data_col.controls = [ft.Text(msg, color=T_MUT, size=13)]
            status_txt.value = (
                f"0 of {len(_rows_holder)} rows"
                if _rows_holder
                else "0 rows"
            )
            page.update()
            return

        # ── Pagination slice ──────────────────────────────────────────────────
        total_pages = max(1, (len(visible) + _PAGE_SIZE - 1) // _PAGE_SIZE)
        _page_idx[0] = max(0, min(_page_idx[0], total_pages - 1))
        start = _page_idx[0] * _PAGE_SIZE
        page_rows = visible[start: start + _PAGE_SIZE]

        # ── Group consecutive rows by trade id ───────────────────────────────────
        # Rows with the same id belong to one trade (double-entry pair / group).
        # Each group is rendered as a single bordered block.
        groups: list = []
        for row in page_rows:
            if groups and groups[-1][0] == row.id:
                groups[-1][1].append(row)
            else:
                groups.append([row.id, [row]])

        # ── Data rows (grouped into transaction blocks) ───────────────────────────
        _GROUP_BORDER = "#4e5d6c"   # light gray — visible on dark bg, not aggressive
        _ROW_SEP      = "#1e2a38"   # inner separator between rows within a group

        row_widgets = []
        for group_id, group_rows in groups:
            inner = []
            for j, row in enumerate(group_rows):
                type_color   = _TYPE_COLORS.get(row.type, T_MUT)
                amt_color    = GREEN if row.amount > _ZERO else (RED if row.amount < _ZERO else T_MUT)
                ts_str       = row.timestamp.strftime("%Y-%m-%d %H:%M:%S") if row.timestamp else "—"
                note_display = (row.note or "")[:30] + ("…" if len(row.note or "") > 30 else "")
                tid = row.id

                def _make_reverse_cb(t: str) -> Callable:
                    def _cb(_e=None) -> None:
                        _confirm_reverse(t, run_rows)
                    return _cb

                def _make_delete_cb(t: str) -> Callable:
                    def _cb(_e=None) -> None:
                        _confirm_delete(t, run_rows)
                    return _cb

                btns = []
                if row.type != "REVERSAL" and row.id and row.id not in reversed_ids:
                    btns.append(ft.TextButton(
                        "Reverse",
                        on_click=_make_reverse_cb(tid),
                        style=ft.ButtonStyle(color=T_MUT, padding=ft.padding.symmetric(0, 4)),
                    ))
                if row.id:
                    btns.append(ft.IconButton(
                        ft.Icons.MORE_VERT,
                        on_click=_make_delete_cb(tid),
                        icon_color=T_MUT,
                        icon_size=16,
                        tooltip="Smazat záznam",
                        style=ft.ButtonStyle(padding=ft.padding.all(4)),
                    ))

                # Thin separator between rows inside the group (not after the last)
                row_border = (
                    ft.border.only(bottom=ft.BorderSide(1, _ROW_SEP))
                    if j < len(group_rows) - 1
                    else None
                )
                inner.append(ft.Container(
                    content=ft.Row([
                        _dcell(ts_str,                   _CW[0], T_MUT,       mono=True),
                        _dcell(row.type,                 _CW[1], type_color),
                        _dcell(row.asset,                _CW[2], T_PRI),
                        _dcell(_fmt_amount(row.amount),  _CW[3], amt_color,   right=True, mono=True),
                        _dcell(row.currency or "—",      _CW[4], T_MUT),
                        _dcell(_short_id(row.id),        _CW[5], T_MUT,       mono=True),
                        _dcell(row.venue or "—",         _CW[6], T_MUT),
                        _dcell(note_display or "—",      _CW[7], T_MUT,       expand=True),
                        ft.Container(
                            content=ft.Row(btns, spacing=0, tight=True),
                            width=_CW[8],
                        ),
                    ], spacing=0),
                    border=row_border,
                    padding=ft.padding.symmetric(9, 8),
                ))

            row_widgets.append(ft.Container(
                content=ft.Column(inner, spacing=0),
                bgcolor=BG_CARD,
                border=ft.border.all(1, _GROUP_BORDER),
                border_radius=7,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                expand=True,
            ))

        data_col.controls = row_widgets

        shown = len(visible)
        total = len(_rows_holder)
        status_txt.value = (
            f"{shown} of {total} row{'s' if total != 1 else ''}"
            if shown != total
            else f"{total} row{'s' if total != 1 else ''}"
        )

        # ── Pagination controls ───────────────────────────────────────────────
        def _prev(_e=None):
            _page_idx[0] = max(0, _page_idx[0] - 1)
            _render()

        def _next(_e=None):
            _page_idx[0] = min(total_pages - 1, _page_idx[0] + 1)
            _render()

        pagination = ft.Row(
            [
                ft.IconButton(ft.Icons.CHEVRON_LEFT,  on_click=_prev, disabled=_page_idx[0] == 0, icon_color=T_PRI),
                ft.Text(f"{_page_idx[0]+1} / {total_pages}", size=12, color=T_MUT),
                ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=_next, disabled=_page_idx[0] >= total_pages - 1, icon_color=T_PRI),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        ) if total_pages > 1 else ft.Container(height=0)

        data_col.controls.append(pagination)
        page.update()

    # ── Control callbacks (filter/sort only — no DB re-fetch) ─────────────────
    tf_search.on_change = lambda _e: _render(reset_page=True)
    dd_type.on_change   = lambda _e: _render(reset_page=True)
    dd_venue.on_change  = lambda _e: _render(reset_page=True)
    dd_sort.on_change   = lambda _e: _render(reset_page=True)

    # ── Main run function (fetches from DB, updates dropdowns, renders) ────────
    def run_rows(e=None) -> None:
        nonlocal _rows_holder
        _rows_holder = get_ledger_rows(db_path)

        # Type dropdown always shows all known types (not just those in current data)
        dd_type.options = [ft.dropdown.Option("", "All types")] + [
            ft.dropdown.Option(t, t) for t in TRADE_TYPES
        ]

        # Rebuild venue dropdown — include TRANSFER destinations from note field
        direct_venues = {r.venue for r in _rows_holder if r.venue}
        dest_venues = {
            r.note.strip().lower()
            for r in _rows_holder
            if r.type == "TRANSFER" and r.note and " " not in r.note.strip()
        }
        venues = sorted(direct_venues | dest_venues)
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
