"""Venue breakdown widget — shows a per-venue portfolio summary card for the dashboard.

Public API:
    build_venue_breakdown(by_venue, on_venue_click=None, active_venue=None) -> ft.Column

    on_venue_click(venue_name: str) — called when user clicks a venue card.
    active_venue — currently selected venue name (None = all venues).
    Clicking the active venue again clears the filter (handled by caller).

Asset chips show PHYSICAL quantity from VenueDashboardDTO.holdings (TRANSFER-aware).
If holdings is empty, falls back to WAC positions list.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, Optional

import flet as ft

from core.services import icon_service

# ── Color palette (matches app_flet.py) ─────────────────────────────────────
BG_CARD    = "#0f1621"
BG_ACTIVE  = "#0d1e30"   # slightly lighter tint for the active venue card
BORDER     = "#1e293b"
BORDER_ACT = "#1d4ed8"   # blue border when venue is active
T_PRI      = "#e2e8f0"
T_MUT      = "#7b8799"
GREEN      = "#16a34a"
RED        = "#ef4444"
BLUE       = "#1d4ed8"

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


def _color(v: Optional[Decimal]) -> str:
    if v is None:
        return T_MUT
    return GREEN if v >= 0 else RED


def _fmt_qty(q: Decimal) -> str:
    """Format asset quantity — strips trailing zeros, max 8 decimal places."""
    return f"{q:.8f}".rstrip("0").rstrip(".")


def _stat(label: str, value: str, value_color: str = T_PRI) -> ft.Column:
    return ft.Column(
        [
            ft.Text(label, size=10, color=T_MUT),
            ft.Text(value, size=12, color=value_color),
        ],
        spacing=2,
        tight=True,
    )


def build_venue_breakdown(
    by_venue: dict,
    on_venue_click: Optional[Callable[[str], None]] = None,
    active_venue: Optional[str] = None,
) -> ft.Column:
    """Build the venue breakdown section.

    Args:
        by_venue:        dict[str, VenueDashboardDTO] from DashboardSnapshotDTO.by_venue.
        on_venue_click:  Called with venue name when user clicks a card.
                         Caller handles toggle logic (active → None, or set new active).
        active_venue:    Currently selected venue name for highlight.

    Returns:
        ft.Column — empty Column when by_venue is empty.
    """
    if not by_venue:
        return ft.Column([])

    venue_cards = []
    for venue_name, vdto in sorted(by_venue.items()):
        if vdto.assets_held == 0 and not vdto.holdings:
            continue

        is_active = (venue_name == active_venue)
        unr = vdto.unrealized_pnl

        # ── Asset chips — use physical holdings when available ────────────────
        # holdings: {asset: physical_qty}  (TRANSFER-aware)
        # Falls back to WAC positions list when holdings is empty.
        if vdto.holdings:
            chip_items = sorted(
                ((a, q) for a, q in vdto.holdings.items() if q > _ZERO),
                key=lambda x: x[0],
            )
            chips = [
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text(asset, size=10, color=T_MUT),
                            ft.Text(_fmt_qty(qty), size=10, color=T_PRI),
                        ],
                        spacing=4,
                        tight=True,
                    ),
                    bgcolor="#162030",
                    border_radius=4,
                    padding=ft.padding.symmetric(2, 6),
                )
                for asset, qty in chip_items
            ]
        else:
            chips = [
                ft.Container(
                    content=ft.Text(p.asset, size=10, color=T_MUT),
                    bgcolor="#162030",
                    border_radius=4,
                    padding=ft.padding.symmetric(2, 6),
                )
                for p in vdto.positions
            ]

        asset_chips = ft.Row(chips, spacing=4, wrap=True)

        def _click(e, v=venue_name) -> None:
            if on_venue_click:
                on_venue_click(v)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    *(
                                        [ft.Image(
                                            src=f"venue_icons/{venue_name.lower()}.png",
                                            width=22, height=22,
                                            fit=ft.BoxFit.CONTAIN,
                                        )]
                                        if icon_service.has_venue_icon(venue_name) else []
                                    ),
                                    ft.Text(
                                        venue_name.upper(),
                                        size=13,
                                        weight=ft.FontWeight.BOLD,
                                        color=T_PRI if not is_active else ft.Colors.WHITE,
                                    ),
                                    *(
                                        [ft.Container(
                                            content=ft.Text("active", size=9, color=BLUE),
                                            bgcolor=BLUE + "22",
                                            border_radius=4,
                                            padding=ft.padding.symmetric(2, 6),
                                        )]
                                        if is_active else []
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                tight=True,
                            ),
                            ft.Row(
                                [
                                    _stat("Assets", str(vdto.assets_held)),
                                    *(
                                        [_stat("Net Invested", _czk(vdto.cost_basis_total))]
                                        if vdto.cost_basis_total > _ZERO else []
                                    ),
                                    _stat("Value", _czk(vdto.value_total)),
                                    *(
                                        [_stat(
                                            "Unrealized PnL",
                                            _czk(unr, sign=True),
                                            value_color=_color(unr),
                                        )]
                                        if unr is not None else []
                                    ),
                                ],
                                spacing=28,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    asset_chips,
                ],
                spacing=8,
            ),
            bgcolor=BG_ACTIVE if is_active else BG_CARD,
            border=ft.border.all(1, BORDER_ACT if is_active else BORDER),
            border_radius=10,
            padding=14,
            on_click=_click if on_venue_click else None,
            ink=True,
        )
        venue_cards.append(card)

    if not venue_cards:
        return ft.Column([])

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Container(height=2, bgcolor="#2d3f55", expand=True),
                    ft.Container(
                        content=ft.Text("Venue Breakdown", size=12, color=T_MUT, weight=ft.FontWeight.W_500),
                        padding=ft.padding.symmetric(0, 12),
                    ),
                    ft.Container(height=2, bgcolor="#2d3f55", expand=True),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Container(
                content=ft.Column(venue_cards, spacing=8),
                bgcolor="#0a1018",
                border_radius=12,
                padding=ft.padding.symmetric(16, 16),
            ),
        ],
        spacing=12,
    )
