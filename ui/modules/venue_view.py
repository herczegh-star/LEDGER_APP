"""Venues page — dedicated view for per-venue breakdown and filtered holdings.

Public API:
    build_venue_view(page, db_path, price_provider=None, fiat="CZK")
        -> (ft.Container view, callable refresh_fn)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

import flet as ft

from core.services.ui_facade import get_dashboard_snapshot

# ── Color palette (mirrors app_flet.py) ─────────────────────────────────────
BG        = "#0b0f14"
BG_CARD   = "#0f1621"
BG_HDR    = "#0d1117"
BORDER    = "#1e293b"
T_PRI     = "#e2e8f0"
T_MUT     = "#7b8799"
GREEN     = "#16a34a"
RED       = "#ef4444"
BLUE      = "#1d4ed8"

_ZERO = Decimal("0")


def _czk(v: Optional[Decimal], sign: bool = False) -> str:
    if v is None:
        return "—"
    a = abs(v)
    places = 0 if a >= 1000 else (2 if a >= 1 else 4)
    n = f"{a:,.{places}f}".replace(",", "\u00a0")
    if v < 0:
        return f"-{n} CZK"
    return f"+{n} CZK" if sign else f"{n} CZK"


def _pct(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{float(v) * 100:.2f}%"


def _pct_pts(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v}%"


def _amt(v: Decimal, asset: str) -> str:
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return f"{s} {asset}"


def _color(v: Optional[Decimal]) -> str:
    if v is None:
        return T_MUT
    return GREEN if v >= 0 else RED


def build_venue_view(
    page: ft.Page,
    db_path: str,
    price_provider=None,
    fiat: str = "CZK",
):
    """Build the Venues dedicated view.

    Returns:
        (view_container, refresh_fn)
    """
    # ── State ──────────────────────────────────────────────────────────────
    snap_holder: list = [None]
    venue_filter: list = [None]
    raw: list = []          # full PositionDTO list from snapshot

    # ── Dynamic regions ────────────────────────────────────────────────────
    venue_col  = ft.Column(spacing=8)
    cards_col  = ft.Column(spacing=16)
    _filter_label = ft.Text("", size=12, color=T_PRI)

    filter_bar = ft.Container(
        visible=False,
        content=ft.Row(
            [
                ft.Text("Venue filter:", size=11, color=T_MUT),
                _filter_label,
                ft.TextButton(
                    "x clear",
                    on_click=lambda e: _clear_venue_filter(),
                    style=ft.ButtonStyle(color=T_MUT, padding=ft.padding.symmetric(0, 4)),
                ),
            ],
            spacing=8,
            tight=True,
        ),
        bgcolor="#162030",
        border_radius=6,
        padding=ft.padding.symmetric(4, 12),
    )

    # ── Asset card builder ─────────────────────────────────────────────────
    def make_card(p, physical_qty: Optional[Decimal] = None) -> ft.Container:
        unr      = p.unrealized_pnl
        roi_total = p.roi_total
        roi_real  = p.roi_realized
        bc = GREEN if (unr is not None and unr >= 0) else RED if unr is not None else BORDER

        glow = None
        if bc != BORDER:
            glow = ft.BoxShadow(
                spread_radius=0,
                blur_radius=32,
                color=bc + "33",
                offset=ft.Offset(0, 0),
                blur_style=ft.BlurStyle.OUTER,
            )

        def stat(label: str, value: str) -> ft.Column:
            return ft.Column(
                [ft.Text(label, size=11, color=T_MUT), ft.Text(value, size=13, color=T_PRI)],
                spacing=2,
                tight=True,
            )

        stat_items = [stat("Amount", _amt(p.quantity, p.asset))]
        if physical_qty is not None and venue_filter[0]:
            stat_items.append(
                ft.Column(
                    [
                        ft.Text(f"Na {venue_filter[0].upper()}", size=11, color=BLUE),
                        ft.Text(_amt(physical_qty, p.asset), size=13, color=T_PRI),
                    ],
                    spacing=2,
                    tight=True,
                )
            )
        stat_items += [
            stat("Avg Buy",      _czk(p.wac)),
            stat("Net Invested", _czk(p.cost_basis)),
            stat("Spot Price",   _czk(p.spot_price)),
            stat("Value",        _czk(p.value)),
            stat("ROI (Realized)", _pct_pts(roi_real)),
        ]

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(p.asset, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                            ft.Row(
                                [
                                    ft.Text(_czk(unr, sign=True), size=14,
                                            weight=ft.FontWeight.W_600, color=_color(unr)),
                                    ft.Text(_pct(roi_total), size=14,
                                            weight=ft.FontWeight.W_600, color=_color(roi_total)),
                                ],
                                spacing=10,
                                tight=True,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=1, color="#1f2a3a"),
                    ft.Row(stat_items, spacing=40),
                ],
                spacing=12,
            ),
            bgcolor=BG_CARD,
            border=ft.border.all(1, "#223046"),
            border_radius=12,
            padding=16,
            shadow=glow,
        )

    # ── Cards builder ──────────────────────────────────────────────────────
    def _build_cards() -> None:
        physical_qtys: dict = {}
        if venue_filter[0] is not None and snap_holder[0] is not None:
            vdto = snap_holder[0].by_venue.get(venue_filter[0])
            if vdto and vdto.holdings:
                held_assets = {a for a, q in vdto.holdings.items() if q > _ZERO}
                positions_to_show = [p for p in raw if p.asset in held_assets]
                physical_qtys = {a: q for a, q in vdto.holdings.items() if q > _ZERO}
            else:
                positions_to_show = vdto.positions if vdto else []
        else:
            positions_to_show = []   # no filter = no asset cards (just venue overview)

        if positions_to_show:
            cards_col.controls = [
                make_card(p, physical_qty=physical_qtys.get(p.asset))
                for p in sorted(positions_to_show, key=lambda p: p.asset)
            ]
        elif venue_filter[0]:
            cards_col.controls = [
                ft.Container(
                    content=ft.Text("No assets held at this venue.", size=13, color=T_MUT),
                    padding=ft.padding.symmetric(16, 0),
                )
            ]
        else:
            cards_col.controls = []

    # ── Venue filter helpers ───────────────────────────────────────────────
    def _update_filter_bar() -> None:
        if venue_filter[0]:
            filter_bar.visible = True
            _filter_label.value = venue_filter[0].upper()
        else:
            filter_bar.visible = False
            _filter_label.value = ""

    def _rebuild_venue_col() -> None:
        if snap_holder[0] is None:
            return
        from ui.modules.venue_breakdown_widget import build_venue_breakdown
        section = build_venue_breakdown(
            snap_holder[0].by_venue,
            on_venue_click=_on_venue_click,
            active_venue=venue_filter[0],
        )
        venue_col.controls = section.controls

    def _on_venue_click(v: str) -> None:
        venue_filter[0] = None if venue_filter[0] == v else v
        _update_filter_bar()
        _rebuild_venue_col()
        _build_cards()
        page.update()

    def _clear_venue_filter() -> None:
        venue_filter[0] = None
        _update_filter_bar()
        _rebuild_venue_col()
        _build_cards()
        page.update()

    # ── Refresh ────────────────────────────────────────────────────────────
    def refresh(e=None) -> None:
        nonlocal raw
        snap = get_dashboard_snapshot(db_path, price_provider, fiat)
        snap_holder[0] = snap
        raw = snap.positions
        _rebuild_venue_col()
        _build_cards()
        _update_filter_bar()
        page.update()

    # ── Layout ────────────────────────────────────────────────────────────
    view = ft.Container(
        expand=True,
        padding=ft.padding.only(left=24, right=24, top=24, bottom=0),
        content=ft.Column(
            [
                ft.Text("Venues", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
                ft.Container(height=12),
                venue_col,
                ft.Container(height=8),
                filter_bar,
                ft.Container(height=4),
                cards_col,
                ft.Container(height=32),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    return view, refresh
