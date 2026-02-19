#!/usr/bin/env python3
"""LedgerApp – Flet desktop dashboard (read-only).

Run:
    python ui/app_flet.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from typing import Optional

import flet as ft

from core.config import load_config
from ui.adapters import load_positions_view

# ── Color palette ──────────────────────────────────────────────────────────────
BG       = "#0b0f14"
BG_CARD  = "#131922"
BG_HDR   = "#0d1117"
BORDER   = "#1e293b"
T_PRI    = "#e2e8f0"
T_MUT    = "#64748b"
GREEN    = "#22c55e"
RED      = "#ef4444"
BLUE     = "#1d4ed8"

# ── Format helpers ─────────────────────────────────────────────────────────────

def _czk(v: Optional[Decimal], sign: bool = False) -> str:
    if v is None:
        return "—"
    n = f"{abs(v):,.0f}".replace(",", "\u00a0")  # narrow-space thousands separator
    if v < 0:
        return f"-{n} CZK"
    return f"+{n} CZK" if sign else f"{n} CZK"


def _pct(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{float(v) * 100:.2f}%"


def _amt(v: Decimal, asset: str) -> str:
    s = f"{v:.8f}".rstrip("0").rstrip(".")
    return f"{s} {asset}"


def _color(v: Optional[Decimal]) -> str:
    if v is None:
        return T_MUT
    return GREEN if v >= 0 else RED


# ── Sort logic ─────────────────────────────────────────────────────────────────

SORTS = [
    ("ROI ↓",    "roi_desc"),
    ("ROI ↑",    "roi_asc"),
    ("PnL ↓",    "pnl_desc"),
    ("PnL ↑",    "pnl_asc"),
    ("Value ↓",  "val_desc"),
    ("Value ↑",  "val_asc"),
    ("A–Z",      "az"),
    ("Z–A",      "za"),
]


def _sort(items: list, key: str) -> list:
    _r = lambda p: p.get("roi")        or Decimal("-999999")
    _p = lambda p: p.get("unrealized") or Decimal("0")
    _v = lambda p: p.get("value")      or Decimal("0")
    cfg = {
        "roi_desc": (_r, True),   "roi_asc":  (_r, False),
        "pnl_desc": (_p, True),   "pnl_asc":  (_p, False),
        "val_desc": (_v, True),   "val_asc":  (_v, False),
        "az":       (lambda p: p["asset"], False),
        "za":       (lambda p: p["asset"], True),
    }
    fn, rev = cfg.get(key, (lambda p: p["asset"], False))
    return sorted(items, key=fn, reverse=rev)


# ── Main ───────────────────────────────────────────────────────────────────────

def main(page: ft.Page) -> None:
    cfg     = load_config()
    db_path = cfg["db_path"]

    page.title       = "LedgerApp"
    page.bgcolor     = BG
    page.theme_mode  = ft.ThemeMode.DARK
    page.padding     = 0
    page.window.width  = 1200
    page.window.height = 760

    # ── State ──────────────────────────────────────────────────────────────────
    raw: list = []
    state = {"sort": "roi_desc"}

    # ── KPI text widgets ───────────────────────────────────────────────────────
    w_cost = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_PRI)
    w_val  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_PRI)
    w_pnl  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_roi  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)

    # Dynamic regions (populated by render functions)
    pills_row = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO)
    cards_col = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    # ── KPI update ─────────────────────────────────────────────────────────────
    def update_kpis() -> None:
        total_cost = sum(p["cost"] for p in raw)
        w_cost.value = _czk(total_cost)

        vals = [p["value"] for p in raw if p.get("value") is not None]
        if vals:
            tv  = sum(vals)
            pnl = tv - total_cost
            w_val.value  = _czk(tv)
            w_pnl.value  = _czk(pnl, sign=True)
            w_pnl.color  = _color(pnl)
            if total_cost > 0:
                roi = pnl / total_cost
                w_roi.value = _pct(roi)
                w_roi.color = _color(roi)
            else:
                w_roi.value = "—"
                w_roi.color = T_MUT
        else:
            w_val.value  = "—"
            w_pnl.value  = "—";  w_pnl.color = T_MUT
            w_roi.value  = "—";  w_roi.color = T_MUT

    # ── Sort pills ─────────────────────────────────────────────────────────────
    def build_pills() -> None:
        def make_pill(label: str, key: str) -> ft.Container:
            active = state["sort"] == key

            def on_click(e, k=key) -> None:
                state["sort"] = k
                build_pills()
                build_cards()
                page.update()

            return ft.Container(
                content=ft.Text(
                    label, size=12,
                    color=T_PRI if active else T_MUT,
                    weight=ft.FontWeight.W_600 if active else ft.FontWeight.NORMAL,
                ),
                bgcolor=BLUE if active else BORDER,
                border_radius=20,
                padding=ft.padding.symmetric(6, 14),
                on_click=on_click,
                ink=True,
            )

        pills_row.controls = [make_pill(l, k) for l, k in SORTS]
        pills_row.update()

    # ── Position cards ─────────────────────────────────────────────────────────
    def build_cards() -> None:
        def make_card(p: dict) -> ft.Container:
            unr = p.get("unrealized")
            roi = p.get("roi")
            bc  = GREEN if (unr is not None and unr >= 0) else \
                  RED   if (unr is not None)              else BORDER

            def on_detail(e, a=p["asset"]) -> None:
                page.open(ft.SnackBar(
                    ft.Text(f"Detail: {a} (TODO)"), duration=2000
                ))

            def stat(label: str, value: str) -> ft.Column:
                return ft.Column([
                    ft.Text(label, size=11, color=T_MUT),
                    ft.Text(value, size=13, color=T_PRI),
                ], spacing=2, tight=True)

            return ft.Container(
                content=ft.Column([
                    # Top row: asset name | unrealized + roi% + Detail
                    ft.Row([
                        ft.Text(
                            p["asset"], size=18,
                            weight=ft.FontWeight.BOLD, color=T_PRI,
                        ),
                        ft.Row([
                            ft.Text(_czk(unr, sign=True), size=14,
                                    weight=ft.FontWeight.W_600, color=_color(unr)),
                            ft.Text(_pct(roi), size=14,
                                    weight=ft.FontWeight.W_600, color=_color(roi)),
                            ft.TextButton(
                                "Detail", on_click=on_detail,
                                style=ft.ButtonStyle(
                                    color=T_MUT,
                                    padding=ft.padding.symmetric(0, 4),
                                ),
                            ),
                        ], spacing=10, tight=True),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    ft.Divider(height=1, color=BORDER),
                    # Bottom row: stats
                    ft.Row([
                        stat("Amount",     _amt(p["amount"], p["asset"])),
                        stat("Avg Buy",    _czk(p["avg_price"])),
                        stat("Cost Basis", _czk(p["cost"])),
                        stat("Spot Price", _czk(p.get("spot_price"))),
                        stat("Value",      _czk(p.get("value"))),
                    ], spacing=40),
                ], spacing=12),
                bgcolor=BG_CARD,
                border=ft.border.all(1, bc),
                border_radius=10,
                padding=16,
                shadow=ft.BoxShadow(
                    spread_radius=0,
                    blur_radius=10,
                    color=bc if bc != BORDER else "#00000000",
                    offset=ft.Offset(0, 0),
                    blur_style=ft.ShadowBlurStyle.OUTER,
                ) if bc != BORDER else None,
            )

        cards_col.controls = [make_card(p) for p in _sort(raw, state["sort"])]
        cards_col.update()

    # ── Refresh ────────────────────────────────────────────────────────────────
    def refresh(e=None) -> None:
        nonlocal raw
        raw = load_positions_view(db_path)
        update_kpis()
        build_pills()
        build_cards()
        page.update()

    def on_export(e) -> None:
        page.open(ft.SnackBar(ft.Text("Export (TODO)"), duration=2000))

    # ── Layout helpers ─────────────────────────────────────────────────────────
    def kpi_box(label: str, widget: ft.Text) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(label, size=12, color=T_MUT), widget],
                spacing=4, tight=True,
            ),
            expand=True,
            padding=ft.padding.symmetric(14, 20),
        )

    # ── Build views ────────────────────────────────────────────────────────────
    _positions_view = ft.Column([
        ft.Container(
            content=ft.Row([
                kpi_box("Total Cost Basis", w_cost),
                kpi_box("Total Value",      w_val),
                kpi_box("Total PnL",        w_pnl),
                kpi_box("Total ROI",        w_roi),
            ], spacing=0),
            bgcolor=BG_HDR,
            padding=ft.padding.symmetric(10, 24),
            border=ft.border.only(bottom=ft.BorderSide(1, BORDER)),
        ),
        ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("Positions", size=16,
                            weight=ft.FontWeight.BOLD, color=T_PRI),
                    pills_row,
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
                cards_col,
            ], spacing=12, expand=True),
            padding=ft.padding.symmetric(16, 24),
            expand=True,
        ),
    ], spacing=0, expand=True)

    from ui.modules.reports import build_reports_view as _build_rv
    _reports_view, _run_report = _build_rv(page, db_path)

    from ui.modules.add_trade_dialog import open_add_trade_dialog

    _content = ft.Column([_positions_view], spacing=0, expand=True)

    def _on_nav(e) -> None:
        idx = e.control.selected_index
        if idx == 0:
            _content.controls = [_positions_view]
            refresh()
        else:
            _content.controls = [_reports_view]
            _run_report()
        page.update()

    _nav = ft.NavigationRail(
        selected_index=0,
        on_change=_on_nav,
        min_width=72,
        bgcolor=BG_HDR,
        indicator_color=BLUE,
        destinations=[
            ft.NavigationRailDestination(icon="dashboard_outlined",  label="Dashboard"),
            ft.NavigationRailDestination(icon="bar_chart_outlined",  label="Reports"),
        ],
    )

    def on_add_trade(e) -> None:
        def _refresh_all() -> None:
            refresh()
            if _content.controls and _content.controls[0] is _reports_view:
                _run_report()
        open_add_trade_dialog(page, db_path, _refresh_all)

    # ── Page layout ────────────────────────────────────────────────────────────
    page.add(
        ft.Container(
            content=ft.Row([
                ft.Text("LedgerApp", size=20,
                        weight=ft.FontWeight.BOLD, color=T_PRI),
                ft.Row([
                    ft.TextButton("Add Trade", on_click=on_add_trade,
                                  style=ft.ButtonStyle(color=GREEN)),
                    ft.TextButton("Export", on_click=on_export,
                                  style=ft.ButtonStyle(color=T_MUT)),
                    ft.TextButton("Refresh", on_click=refresh,
                                  style=ft.ButtonStyle(color=T_PRI)),
                ], spacing=0),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            bgcolor=BG_HDR,
            padding=ft.padding.symmetric(12, 24),
        ),
        ft.Row([
            _nav,
            ft.VerticalDivider(width=1, color=BORDER),
            _content,
        ], expand=True, spacing=0),
    )

    refresh()


if __name__ == "__main__":
    ft.run(main)
