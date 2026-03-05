"""LedgerApp – Flet desktop UI.

Entrypoint: call run_ui() from main.py.
"""

import threading
from decimal import Decimal
from pathlib import Path
from typing import Optional

import flet as ft

# ── Boot log (ASCII only, survives cp1250) ─────────────────────────────────
_BOOT_LOG = Path(__file__).parent.parent / "ui_boot.log"


def _blog(msg: str) -> None:
    """Append ASCII-safe boot checkpoint to ui_boot.log."""
    with _BOOT_LOG.open("a", encoding="ascii", errors="replace") as f:
        import datetime

        f.write(f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}\n")


_blog("CHECKPOINT 1 - import reached (ui.app_flet module loaded)")

from core.services.ui_facade import create_app_context, create_db, get_dashboard_snapshot, set_db_path

# ── Color palette (decent / less neon) ──────────────────────────────────────
BG = "#0b0f14"
BG_CARD = "#0f1621"      # darker card
BG_HDR = "#0d1117"
BORDER = "#1e293b"

T_PRI = "#e2e8f0"
T_MUT = "#7b8799"        # slightly brighter muted text for legibility

GREEN = "#16a34a"        # less neon than #22c55e
RED = "#ef4444"
BLUE = "#1d4ed8"


# ── Format helpers ─────────────────────────────────────────────────────────────
def _czk(v: Optional[Decimal], sign: bool = False) -> str:
    if v is None:
        return "—"
    a = abs(v)
    places = 0 if a >= 1000 else (2 if a >= 1 else 4)
    n = f"{a:,.{places}f}".replace(",", "\u00a0")  # narrow-space thousands separator
    if v < 0:
        return f"-{n} CZK"
    return f"+{n} CZK" if sign else f"{n} CZK"


def _pct(v: Optional[Decimal]) -> str:
    """Format a fraction (0.25 → '+25.00%'). Used for live-price derived ROI."""
    if v is None:
        return "—"
    return f"{'+' if v >= 0 else ''}{float(v) * 100:.2f}%"


def _pct_pts(v: Optional[Decimal]) -> str:
    """Format a value already in percentage points (25.00 → '+25.00%').
    Used for roi_realized which comes pre-multiplied from the core report.
    """
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


# ── Sort logic ─────────────────────────────────────────────────────────────────
_SORT_FIELDS = [
    ("roi",  "ROI Total"),
    ("pnl",  "PnL"),
    ("val",  "Value"),
    ("name", "Name"),
]


def _sort(items: list, key: str) -> list:
    _r = lambda p: p.roi_total or Decimal("-999999")
    _p = lambda p: p.unrealized_pnl or Decimal("0")
    _v = lambda p: p.value or Decimal("0")
    cfg = {
        "roi_desc":  (_r, True),
        "roi_asc":   (_r, False),
        "pnl_desc":  (_p, True),
        "pnl_asc":   (_p, False),
        "val_desc":  (_v, True),
        "val_asc":   (_v, False),
        "name_asc":  (lambda p: p.asset, False),
        "name_desc": (lambda p: p.asset, True),
    }
    fn, rev = cfg.get(key, (lambda p: p.asset, False))
    return sorted(items, key=fn, reverse=rev)


# ── Main ───────────────────────────────────────────────────────────────────────
def main_view(page: ft.Page) -> None:
    _blog("CHECKPOINT 4 - main_view entered (Flet called the target)")
    try:
        _main_view_impl(page)
    except Exception:
        import traceback

        _blog("CHECKPOINT 4-ERR - exception in _main_view_impl")
        traceback.print_exc()
        raise


def _main_view_impl(page: ft.Page) -> None:
    _blog("CHECKPOINT 5 - _main_view_impl entered")
    ctx = create_app_context()

    page.title = "LedgerApp 1.0.0"
    page.bgcolor = BG
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.spacing = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.window.width = 1200
    page.window.height = 760

    # ── Fatal error guard ──────────────────────────────────────────────────────
    if ctx.error:
        page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text("Startup Error", size=20, weight=ft.FontWeight.BOLD, color=RED),
                        ft.Text(ctx.error, size=14, color=T_PRI),
                        ft.Text("Check ledger.ini and restart.", size=12, color=T_MUT),
                    ],
                    spacing=12,
                ),
                padding=40,
            )
        )
        return

    # ── DB onboarding (DB_MISSING — auto-create and continue) ──────────────────
    if ctx.db_state == "DB_MISSING":
        create_db(ctx.db_path)
        ctx = create_app_context()
        if ctx.error or ctx.db_state != "OK":
            page.add(ft.Text(ctx.error or "Failed to create database.", color=RED, size=14))
            return

    db_path = ctx.db_path
    _price_provider = ctx.price_provider
    _price_fiat = ctx.fiat

    # ── State ──────────────────────────────────────────────────────────────────
    raw: list = []
    state = {"sort_field": "roi", "sort_asc": False}  # default: ROI Total DESC
    snap_holder: list = [None]   # last DashboardSnapshotDTO
    venue_filter: list = [None]  # active venue name, or None = all venues

    # ── KPI widgets ────────────────────────────────────────────────────────────
    w_val = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_PRI)
    w_pnl = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_roi = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)

    # Dynamic regions
    pills_row   = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO)
    cards_col   = ft.Column(spacing=16)   # asset position cards
    venue_col   = ft.Column(spacing=8)    # venue breakdown section

    # Venue filter indicator bar (hidden when no filter active)
    _filter_label = ft.Text("", size=12, color=T_PRI)
    filter_bar = ft.Container(
        visible=False,
        content=ft.Row(
            [
                ft.Text("Venue filter:", size=11, color=T_MUT),
                _filter_label,
                ft.TextButton(
                    "× clear",
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

    # ── KPI update ─────────────────────────────────────────────────────────────
    def update_kpis() -> None:
        total_cost = sum((p.cost_basis for p in raw), Decimal("0"))

        vals = [p.value for p in raw if p.value is not None]
        if vals:
            tv = sum(vals, Decimal("0"))
            pnl = tv - total_cost
            w_val.value = _czk(tv)
            w_pnl.value = _czk(pnl, sign=True)
            w_pnl.color = _color(pnl)
            if total_cost > 0:
                roi = pnl / total_cost
                w_roi.value = _pct(roi)
                w_roi.color = _color(roi)
            else:
                w_roi.value = "—"
                w_roi.color = T_MUT
        else:
            w_val.value = "—"
            w_pnl.value = "—"
            w_pnl.color = T_MUT
            w_roi.value = "—"
            w_roi.color = T_MUT

    # ── Sort pills ─────────────────────────────────────────────────────────────
    def build_pills() -> None:
        def make_pill(field: str, label: str) -> ft.Container:
            active = state["sort_field"] == field

            def on_click(e, f=field) -> None:
                if state["sort_field"] == f:
                    state["sort_asc"] = not state["sort_asc"]
                else:
                    state["sort_field"] = f
                    state["sort_asc"] = False
                build_pills()
                build_cards()
                page.update()

            return ft.Container(
                content=ft.Text(
                    label,
                    size=12,
                    color=T_PRI if active else T_MUT,
                    weight=ft.FontWeight.W_600 if active else ft.FontWeight.NORMAL,
                ),
                bgcolor=BLUE if active else "#162030",
                border_radius=20,
                padding=ft.padding.symmetric(6, 14),
                on_click=on_click,
                ink=True,
            )

        pills_row.controls = [make_pill(f, l) for f, l in _SORT_FIELDS]

    # ── Cards ──────────────────────────────────────────────────────────────────
    def build_cards() -> None:
        def make_card(p) -> ft.Container:
            unr = p.unrealized_pnl
            roi_total = p.roi_total      # fraction
            roi_real = p.roi_realized    # % points from core
            bc = GREEN if (unr is not None and unr >= 0) else RED if (unr is not None) else BORDER

            def on_detail(e, a=p.asset) -> None:
                from ui.modules.asset_detail_view import build_asset_detail_view
                _content.content = build_asset_detail_view(
                    page=page,
                    db_path=db_path,
                    price_provider=_price_provider,
                    fiat=_price_fiat,
                    asset=a,
                    on_back=lambda: set_view(0),
                )
                page.update()

            def stat(label: str, value: str) -> ft.Column:
                return ft.Column(
                    [ft.Text(label, size=11, color=T_MUT), ft.Text(value, size=13, color=T_PRI)],
                    spacing=2,
                    tight=True,
                )

            # Softer, ambient glow (more blur + transparency), not neon outline
            glow = None
            if bc != BORDER:
                glow = ft.BoxShadow(
                    spread_radius=0,
                    blur_radius=32,     # CHANGED (was 26)
                    color=bc + "33",    # CHANGED (was "55")
                    offset=ft.Offset(0, 0),
                    blur_style=ft.BlurStyle.OUTER,
                )

            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(p.asset, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                                ft.Row(
                                    [
                                        ft.Text(
                                            _czk(unr, sign=True),
                                            size=14,
                                            weight=ft.FontWeight.W_600,
                                            color=_color(unr),
                                        ),
                                        ft.Text(
                                            _pct(roi_total),
                                            size=14,
                                            weight=ft.FontWeight.W_600,
                                            color=_color(roi_total),
                                        ),
                                        ft.TextButton(
                                            "Detail",
                                            on_click=on_detail,
                                            style=ft.ButtonStyle(color=T_MUT, padding=ft.padding.symmetric(0, 4)),
                                        ),
                                    ],
                                    spacing=10,
                                    tight=True,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        ft.Divider(height=1, color="#1f2a3a"),  # CHANGED (was "#243244")
                        ft.Row(
                            [
                                stat("Amount", _amt(p.quantity, p.asset)),
                                stat("Avg Buy", _czk(p.wac)),
                                stat("Net Invested", _czk(p.cost_basis)),
                                stat("Spot Price", _czk(p.spot_price)),
                                stat("Value", _czk(p.value)),
                                stat("ROI (Realized)", _pct_pts(roi_real)),
                            ],
                            spacing=40,
                        ),
                    ],
                    spacing=12,
                ),
                bgcolor=BG_CARD,
                border=ft.border.all(1, "#223046"),      # consistent, less “status-outline”
                border_radius=12,
                padding=16,
                shadow=glow,
            )

        # Use venue-filtered positions when a venue filter is active
        if venue_filter[0] is not None and snap_holder[0] is not None:
            vdto = snap_holder[0].by_venue.get(venue_filter[0])
            positions_to_show = vdto.positions if vdto else []
        else:
            positions_to_show = raw

        if positions_to_show:
            cards_col.controls = [make_card(p) for p in _sort(
                positions_to_show,
                f"{state['sort_field']}_{'asc' if state['sort_asc'] else 'desc'}",
            )]
        else:
            cards_col.controls = [
                ft.Container(
                    content=ft.Text("No data yet. Add a trade or import a file.", size=14, color=T_MUT),
                    padding=ft.padding.symmetric(32, 0),
                )
            ]

        _blog(f"build_cards: raw={len(raw)} cards={len(cards_col.controls)}")

    # ── Venue filter helpers ────────────────────────────────────────────────────
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
        venue_section = build_venue_breakdown(
            snap_holder[0].by_venue,
            on_venue_click=_on_venue_click,
            active_venue=venue_filter[0],
        )
        venue_col.controls = venue_section.controls

    def _on_venue_click(v: str) -> None:
        venue_filter[0] = None if venue_filter[0] == v else v
        _update_filter_bar()
        build_cards()
        _rebuild_venue_col()
        page.update()

    def _clear_venue_filter() -> None:
        venue_filter[0] = None
        _update_filter_bar()
        build_cards()
        _rebuild_venue_col()
        page.update()

    # ── Refresh ────────────────────────────────────────────────────────────────
    def refresh(e=None) -> None:
        nonlocal raw
        snap = get_dashboard_snapshot(db_path, _price_provider, _price_fiat)
        snap_holder[0] = snap
        raw = snap.positions
        venue_filter[0] = None   # reset filter on full data refresh

        update_kpis()
        build_pills()
        build_cards()
        _update_filter_bar()
        _rebuild_venue_col()
        page.update()

    # ── Layout helper ──────────────────────────────────────────────────────────
    def kpi_box(label: str, widget: ft.Text) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(label, size=12, color=T_MUT), widget],
                spacing=4,
                tight=True,
            ),
            expand=True,
            padding=ft.padding.symmetric(14, 20),
            bgcolor=BG_CARD,
            border=ft.border.all(1, "#223046"),
            border_radius=12,
        )

    # ── Dashboard view (REAL) ──────────────────────────────────────────────────
    _dashboard_view = ft.Container(
        expand=True,
        padding=24,
        content=ft.Column(
            [
                ft.Row(
                    [
                        kpi_box("Portfolio Value", w_val),
                        kpi_box("Unrealized PnL", w_pnl),
                        kpi_box("ROI", w_roi),
                    ],
                    spacing=16,
                ),
                ft.Container(height=16),
                pills_row,
                ft.Container(height=8),
                filter_bar,
                ft.Container(height=4),
                ft.Column(
                    [cards_col, ft.Container(height=24), venue_col],
                    spacing=0,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        ),
    )

    # ── Other modules ──────────────────────────────────────────────────────────
    from ui.modules.reports import build_reports_view as _build_rv
    _reports_view, _run_report = _build_rv(page, db_path)

    from ui.modules.positions_view import build_positions_view as _build_pv
    _positions_wac_view, _run_positions = _build_pv(page, db_path, price_provider=_price_provider, fiat=_price_fiat)

    from ui.modules.health_view import build_health_view as _build_hv
    _health_view, _run_health = _build_hv(page, db_path)

    from ui.modules.add_trade_dialog import open_add_trade_dialog
    from ui.modules.export_dialog import open_export_dialog
    from ui.modules.import_dialog import open_import_dialog
    from ui.modules.reversal_dialog import open_reversal_dialog

    _ledger_view = None
    _run_ledger = None

    # ── Center content host ────────────────────────────────────────────────────
    _content = ft.Container(expand=True, bgcolor=BG)

    def set_view(idx: int) -> None:
        nonlocal _ledger_view, _run_ledger
        if idx == 0:
            _content.content = _dashboard_view
            refresh()
        elif idx == 1:
            _content.content = _reports_view
            _run_report()
        elif idx == 2:
            _content.content = _positions_wac_view
            _run_positions()
        elif idx == 3:
            _content.content = _health_view
            _run_health()
        else:
            _content.content = _ledger_view
            _run_ledger()
        page.update()
        # Note: _rebuild_nav_col() is called by _build_nav_btn click handler before set_view.

    # ── Custom vertical nav (replaces ft.NavigationRail — too buggy in Flet 0.80) ──
    _NAV_ITEMS = [
        (ft.Icons.DASHBOARD_OUTLINED,        ft.Icons.DASHBOARD,             "Dashboard"),
        (ft.Icons.BAR_CHART_OUTLINED,        ft.Icons.BAR_CHART,             "Reports"),
        (ft.Icons.TABLE_CHART_OUTLINED,      ft.Icons.TABLE_CHART,           "Positions"),
        (ft.Icons.HEALTH_AND_SAFETY_OUTLINED,ft.Icons.HEALTH_AND_SAFETY,     "Health"),
        (ft.Icons.LIST_ALT_OUTLINED,         ft.Icons.LIST_ALT,              "Ledger"),
    ]
    _nav_idx = [0]
    _nav_col = ft.Column(spacing=2, tight=True)

    def _build_nav_btn(idx: int) -> ft.Container:
        active = _nav_idx[0] == idx
        icon_off, icon_on, label = _NAV_ITEMS[idx]

        def _click(e, i=idx):
            _nav_idx[0] = i
            _rebuild_nav_col()
            set_view(i)

        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(icon_on if active else icon_off,
                            color=T_PRI if active else T_MUT, size=20),
                    ft.Text(label, size=9, color=T_PRI if active else T_MUT,
                            text_align=ft.TextAlign.CENTER, width=72),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=3,
                tight=True,
            ),
            padding=ft.padding.symmetric(10, 4),
            bgcolor=BLUE + "55" if active else "transparent",
            border_radius=8,
            on_click=_click,
            ink=True,
            width=80,
            margin=ft.margin.symmetric(2, 4),
        )

    def _rebuild_nav_col() -> None:
        _nav_col.controls = [_build_nav_btn(i) for i in range(len(_NAV_ITEMS))]
        # page.update() is called by the outer set_view — no need to update here.

    _rebuild_nav_col()

    def _refresh_all() -> None:
        refresh()
        active = _content.content
        if active is _reports_view:
            _run_report()
        elif active is _positions_wac_view:
            _run_positions()
        elif active is _health_view:
            _run_health()
        elif active is _ledger_view:
            _run_ledger()

    from ui.modules.ledger_view import build_ledger_view as _build_lv
    _ledger_view, _run_ledger = _build_lv(page, db_path, on_after_reverse=_refresh_all)

    def on_add_trade(e) -> None:
        open_add_trade_dialog(page, db_path, _refresh_all)

    def on_import(e) -> None:
        open_import_dialog(page, db_path, _refresh_all)

    def on_reverse(e) -> None:
        open_reversal_dialog(page, db_path, _refresh_all)

    def on_export(e) -> None:
        open_export_dialog(page, db_path)

    # ── Header ────────────────────────────────────────────────────────────────
    _header = ft.Container(
        content=ft.Row(
            [
                ft.Text("LedgerApp 1.0.0", size=20, weight=ft.FontWeight.BOLD, color=T_PRI),
                ft.Row(
                    [
                        ft.TextButton("Add Trade", on_click=on_add_trade, style=ft.ButtonStyle(color=GREEN)),
                        ft.TextButton("Reverse", on_click=on_reverse, style=ft.ButtonStyle(color="#f97316")),
                        ft.TextButton("Import", on_click=on_import, style=ft.ButtonStyle(color=BLUE)),
                        ft.TextButton("Export", on_click=on_export, style=ft.ButtonStyle(color=T_MUT)),
                        ft.TextButton("Refresh", on_click=refresh, style=ft.ButtonStyle(color=T_PRI)),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=BG_HDR,
        padding=ft.padding.symmetric(12, 24),
    )

    # ── Body ──────────────────────────────────────────────────────────────────
    # NavigationRail MUST be wrapped in a Container — bare Row child breaks Flutter layout.
    # expand=True inside a Container is invalid Flutter (Expanded requires Flex parent),
    # so we give both the Container AND the Row an explicit pixel height instead.
    _HEADER_H = 54   # approximate header height (px)
    _body_h = int(page.window.height) - _HEADER_H

    _nav_host = ft.Container(
        width=88,
        height=_body_h,
        bgcolor=BG_HDR,
        content=ft.Column(
            [ft.Container(height=8), _nav_col],
            spacing=0,
            tight=True,
        ),
        padding=0,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    _body_row = ft.Row(
        [_nav_host, ft.VerticalDivider(width=1, color=BORDER), _content],
        height=_body_h,
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    def _on_resize(e=None) -> None:
        h = int(page.window.height) - _HEADER_H
        _body_row.height = h
        _nav_host.height = h
        page.update()

    page.on_resize = _on_resize

    page.controls.clear()
    _blog(f"BODY: adding _header + _body_row(h={_body_row.height})  nav_host in slot[0]={_body_row.controls[0] is _nav_host}")
    page.add(_header, _body_row)
    _blog(f"BODY: page.controls={len(page.controls)}  window={page.window.width}x{page.window.height}")

    # Explicit initial view (NavigationRail does NOT fire on startup)
    set_view(0)


def run_ui() -> None:
    _blog("CHECKPOINT 2 - run_ui entered")
    _blog("CHECKPOINT 3 - about to call ft.run(target=main_view)")
    ft.run(main_view)
    _blog("CHECKPOINT 3b - ft.run returned (window closed)")


if __name__ == "__main__":
    _blog("CHECKPOINT 0 - __main__ block reached (python -m ui.app_flet)")
    run_ui()