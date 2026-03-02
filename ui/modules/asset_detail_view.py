"""Asset Detail view – per-asset WAC position + venue breakdown.

Public API:
    build_asset_detail_view(page, db_path, price_provider, fiat, asset, on_back) -> ft.Control

UI only. All data fetching delegated to core.services.ui_facade.get_asset_detail().
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

import flet as ft

from core.services.ui_facade import get_asset_detail

# ── Color palette (consistent with app_flet.py) ────────────────────────────
BG_CARD = "#0f1621"
BG_HDR  = "#0d1117"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#7b8799"
GREEN   = "#16a34a"
RED     = "#ef4444"

_ZERO = Decimal("0")


# ── Format helpers ─────────────────────────────────────────────────────────

def _czk(v: Optional[Decimal], sign: bool = False) -> str:
    if v is None:
        return "—"
    n = f"{abs(v):,.0f}".replace(",", "\u00a0")
    if v < _ZERO:
        return f"-{n} CZK"
    return f"+{n} CZK" if sign else f"{n} CZK"


def _amt(v: Decimal, asset: str) -> str:
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return f"{s} {asset}"


def _pct(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{float(v) * 100:.2f}%"


def _color(v: Optional[Decimal]) -> str:
    if v is None:
        return T_MUT
    return GREEN if v >= 0 else RED


# ── View builder ──────────────────────────────────────────────────────────

def build_asset_detail_view(
    page: ft.Page,
    db_path: str,
    price_provider,
    fiat: str,
    asset: str,
    on_back: Callable[[], None],
) -> ft.Control:
    """Build and return the Asset Detail view as a single ft.Control.

    Fetches data via get_asset_detail() and renders:
    - Header with Back button
    - Aggregated position summary card
    - Venue breakdown table
    """
    detail = get_asset_detail(db_path, asset, price_provider=price_provider, fiat=fiat)
    pos = detail.position

    # ── Header ───────────────────────────────────────────────────────────────
    header = ft.Row(
        [
            ft.TextButton(
                "← Back",
                on_click=lambda _: on_back(),
                style=ft.ButtonStyle(color=T_MUT),
            ),
            ft.Text(
                f"{detail.asset}  –  Detail",
                size=20,
                weight=ft.FontWeight.BOLD,
                color=T_PRI,
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ── Summary card ──────────────────────────────────────────────────────────
    def stat(label: str, value: str, color: str = T_PRI) -> ft.Column:
        return ft.Column(
            [
                ft.Text(label, size=11, color=T_MUT),
                ft.Text(value, size=13, color=color),
            ],
            spacing=2,
            tight=True,
        )

    summary_card = ft.Container(
        content=ft.Row(
            [
                stat("Quantity",   _amt(pos.quantity, detail.asset)),
                stat("Avg Entry",  _czk(pos.wac)),
                stat("Net Invested", _czk(pos.cost_basis)),
                stat("Spot Price", _czk(pos.spot_price)),
                stat("Value",      _czk(pos.value)),
                stat("PnL",        _czk(pos.unrealized_pnl, sign=True),
                     color=_color(pos.unrealized_pnl)),
                stat("ROI",        _pct(pos.roi_total),
                     color=_color(pos.roi_total)),
            ],
            spacing=32,
            wrap=True,
        ),
        bgcolor=BG_CARD,
        border=ft.border.all(1, "#223046"),
        border_radius=12,
        padding=16,
    )

    # ── Venue breakdown table ─────────────────────────────────────────────────
    venue_col_defs = [
        ft.DataColumn(ft.Text("Venue",      color=T_MUT, size=11)),
        ft.DataColumn(ft.Text("Quantity",   color=T_MUT, size=11), numeric=True),
        ft.DataColumn(ft.Text("Avg Entry",  color=T_MUT, size=11), numeric=True),
        ft.DataColumn(ft.Text("Net Invested", color=T_MUT, size=11), numeric=True),
        ft.DataColumn(ft.Text("Value",      color=T_MUT, size=11), numeric=True),
        ft.DataColumn(ft.Text("PnL",        color=T_MUT, size=11), numeric=True),
        ft.DataColumn(ft.Text("ROI",        color=T_MUT, size=11), numeric=True),
    ]

    def _cell(text: str, color: str = T_PRI) -> ft.DataCell:
        return ft.DataCell(ft.Text(text, size=11, color=color))

    def _ncell(text: str, color: str = T_PRI) -> ft.DataCell:
        """Right-aligned numeric cell."""
        return ft.DataCell(
            ft.Text(text, size=11, color=color, text_align=ft.TextAlign.RIGHT)
        )

    venue_data_rows = []
    for vp in detail.venue_positions:
        venue_data_rows.append(ft.DataRow(cells=[
            _cell(vp.venue),
            _ncell(_amt(vp.quantity, detail.asset)),
            _ncell(_czk(vp.wac)),
            _ncell(_czk(vp.cost_basis)),
            _ncell(_czk(vp.value)),
            _ncell(_czk(vp.unrealized_pnl, sign=True), color=_color(vp.unrealized_pnl)),
            _ncell(_pct(vp.roi_total),                  color=_color(vp.roi_total)),
        ]))

    if not venue_data_rows:
        venue_data_rows = [ft.DataRow(cells=[
            _cell("No venue data", color=T_MUT),
            *[ft.DataCell(ft.Text("")) for _ in range(6)],
        ])]

    venue_table = ft.Container(
        content=ft.Column(
            [
                ft.Text("Venue Breakdown", size=14,
                        weight=ft.FontWeight.W_600, color=T_PRI),
                ft.Row(
                    [ft.DataTable(
                        columns=venue_col_defs,
                        rows=venue_data_rows,
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
            ],
            spacing=12,
        ),
        bgcolor=BG_CARD,
        border=ft.border.all(1, "#223046"),
        border_radius=12,
        padding=16,
    )

    # ── Main layout ────────────────────────────────────────────────────────────
    return ft.Container(
        expand=True,
        padding=24,
        content=ft.Column(
            [
                header,
                ft.Container(height=8),
                summary_card,
                ft.Container(height=16),
                venue_table,
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
