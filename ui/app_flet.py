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
from core.services import icon_service

# ── Icons — served from user data dir (~/.ledger_app/icons/) ─────────────────
icon_service.setup()   # migrate bundled icons + create dir
_ICONS_DIR = icon_service.ICONS_DIR
_STABLECOINS = frozenset({"USDC", "USDT"})

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
    import time as _time
    _t_impl_start = _time.perf_counter()
    _blog("CHECKPOINT 5 - _main_view_impl entered")

    def _tick(label: str) -> None:
        ms = (_time.perf_counter() - _t_impl_start) * 1000
        _blog(f"TIMING +{ms:7.1f}ms  {label}")

    # ── Page base settings (must precede first page.add) ──────────────────────
    page.title = "CryptoLedger"
    page.bgcolor = BG
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.spacing = 0
    page.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
    page.window.width = 1200
    page.window.height = 760

    # ── Phase 1: load config + probe DB ───────────────────────────────────────
    ctx = create_app_context()
    _tick("create_app_context() done")

    # ── Fatal error guard ──────────────────────────────────────────────────────
    if ctx.error:
        page.controls.clear()
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
        page.update()
        return

    # ── DB onboarding (DB_MISSING — auto-create and continue) ──────────────────
    if ctx.db_state == "DB_MISSING":
        create_db(ctx.db_path)
        ctx = create_app_context()
        if ctx.error or ctx.db_state != "OK":
            page.controls.clear()
            page.add(ft.Text(ctx.error or "Failed to create database.", color=RED, size=14))
            page.update()
            return

    db_path = ctx.db_path
    _price_provider = ctx.price_provider
    _price_fiat = ctx.fiat

    # ── Phase 2: build interface ───────────────────────────────────────────────

    # ── State ──────────────────────────────────────────────────────────────────
    raw: list = []
    state = {"sort_field": "roi", "sort_asc": False}  # default: ROI Total DESC
    snap_holder: list = [None]   # last DashboardSnapshotDTO
    privacy = [False]  # privacy mode — hides sensitive KPI values

    # ── KPI widgets ────────────────────────────────────────────────────────────
    w_val = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_PRI)
    w_pnl = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_roi = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)

    # Dynamic regions
    pills_row   = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO)
    cards_col   = ft.Column(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)   # asset position cards

    _HIDDEN = "••••••"

    # ── KPI update ─────────────────────────────────────────────────────────────
    def update_kpis() -> None:
        if privacy[0]:
            w_val.value = _HIDDEN; w_val.color = T_MUT
            w_pnl.value = _HIDDEN; w_pnl.color = T_MUT
            w_roi.value = _HIDDEN; w_roi.color = T_MUT
            return

        total_cost = sum((p.cost_basis for p in raw), Decimal("0"))

        vals = [p.value for p in raw if p.value is not None]
        if vals:
            tv = sum(vals, Decimal("0"))
            pnl = tv - total_cost
            w_val.value = _czk(tv)
            w_val.color = T_PRI
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
            w_val.value = "—"; w_val.color = T_PRI
            w_pnl.value = "—"; w_pnl.color = T_MUT
            w_roi.value = "—"; w_roi.color = T_MUT

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

            glow = None
            if bc != BORDER:
                glow = ft.BoxShadow(
                    spread_radius=0,
                    blur_radius=32,
                    color=bc + "33",
                    offset=ft.Offset(0, 0),
                    blur_style=ft.BlurStyle.OUTER,
                )

            is_stable = p.asset.upper() in _STABLECOINS
            avg_buy_label = "Avg Buy*" if is_stable else "Avg Buy"

            stat_items = [
                stat("Amount",         _amt(p.quantity, p.asset)),
                stat(avg_buy_label,    _czk(p.wac)),
                stat("Net Invested",   _czk(p.cost_basis)),
                stat("Spot Price",     _czk(p.spot_price)),
                stat("Value",          _czk(p.value)),
                stat("ROI (Realized)", _pct_pts(roi_real)),
            ]

            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Row(
                                    [
                                        *(
                                            [ft.Image(src=f"icons/{p.asset.lower()}.png", width=28, height=28, fit=ft.BoxFit.CONTAIN)]
                                            if (_ICONS_DIR / f"{p.asset.lower()}.png").exists()
                                            else []
                                        ),
                                        ft.Text(p.asset, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                                    ],
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    tight=True,
                                ),
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
                        ft.Divider(height=1, color="#1f2a3a"),
                        ft.Row(stat_items, spacing=40),
                        *(
                            [ft.Text(
                                "* U stablecoinů může Avg Buy zahrnovat přenesený cost basis ze swapů, "
                                "proto nemusí odpovídat nominální ceně 1 USD.",
                                size=10,
                                color=T_MUT,
                                italic=True,
                            )]
                            if is_stable else []
                        ),
                    ],
                    spacing=12,
                ),
                bgcolor=BG_CARD,
                border=ft.border.all(1, "#223046"),
                border_radius=12,
                padding=16,
                shadow=glow,
            )

        positions_to_show = raw

        if positions_to_show:
            cards_col.controls = [
                make_card(p)
                for p in _sort(
                    positions_to_show,
                    f"{state['sort_field']}_{'asc' if state['sort_asc'] else 'desc'}",
                )
            ] + [ft.Container(height=32)]
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

    # ── Refresh ────────────────────────────────────────────────────────────────
    def refresh(e=None) -> None:
        import time as _time
        nonlocal raw
        _t0 = _time.perf_counter()
        snap = get_dashboard_snapshot(db_path, _price_provider, _price_fiat)
        _t1 = _time.perf_counter()
        _blog(f"TIMING  refresh: get_dashboard_snapshot() took {(_t1-_t0)*1000:.1f}ms  positions={len(snap.positions)}")
        snap_holder[0] = snap
        raw = snap.positions

        update_kpis()
        build_pills()
        build_cards()
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
        height=int(page.window.height) - 54,
        padding=ft.padding.only(left=24, right=24, top=24, bottom=0),
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
                ft.Container(height=12),
                cards_col,  # scrolls independently, KPI+pills stay fixed
            ],
            spacing=0,
        ),
    )

    # ── Other modules ──────────────────────────────────────────────────────────
    from ui.modules.reports import build_reports_view as _build_rv
    _tick("import ui.modules.reports done")
    _reports_view, _run_report = _build_rv(page, db_path)
    _tick("build_reports_view() done")

    from ui.modules.positions_view import build_positions_view as _build_pv
    _tick("import ui.modules.positions_view done")
    _positions_wac_view, _run_positions = _build_pv(page, db_path, price_provider=_price_provider, fiat=_price_fiat)
    _tick("build_positions_view() done")

    from ui.modules.health_view import build_health_view as _build_hv
    _tick("import ui.modules.health_view done")
    _health_view, _run_health = _build_hv(page, db_path)
    _tick("build_health_view() done")

    from ui.modules.venue_view import build_venue_view as _build_vv
    _tick("import ui.modules.venue_view done")
    _venues_view, _run_venues = _build_vv(page, db_path, price_provider=_price_provider, fiat=_price_fiat)
    _tick("build_venue_view() done")

    from ui.modules.add_trade_dialog import open_add_trade_dialog
    from ui.modules.export_dialog import open_export_dialog
    from ui.modules.import_dialog import open_import_dialog
    from ui.modules.reversal_dialog import open_reversal_dialog
    _tick("import all dialogs done")

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
        elif idx == 4:
            _content.content = _venues_view
            _run_venues()
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
        (ft.Icons.ACCOUNT_BALANCE_OUTLINED,  ft.Icons.ACCOUNT_BALANCE,       "Venues"),
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
        elif active is _venues_view:
            _run_venues()
        elif active is _ledger_view:
            _run_ledger()

    from ui.modules.ledger_view import build_ledger_view as _build_lv
    _tick("import ui.modules.ledger_view done")
    _ledger_view, _run_ledger = _build_lv(page, db_path, on_after_reverse=_refresh_all)
    _tick("build_ledger_view() done")

    def on_add_trade(e) -> None:
        open_add_trade_dialog(page, db_path, _refresh_all)

    def on_import(e) -> None:
        open_import_dialog(page, db_path, _refresh_all)

    def on_reverse(e) -> None:
        open_reversal_dialog(page, db_path, _refresh_all)

    def on_export(e) -> None:
        open_export_dialog(page, db_path)

    _eye_btn = ft.IconButton(icon=ft.Icons.VISIBILITY, icon_color=T_MUT, icon_size=18, tooltip="Skryť/zobraziť hodnoty")

    def on_toggle_privacy(e) -> None:
        privacy[0] = not privacy[0]
        _eye_btn.icon = ft.Icons.VISIBILITY_OFF if privacy[0] else ft.Icons.VISIBILITY
        update_kpis()
        page.update()

    _eye_btn.on_click = on_toggle_privacy

    # ── Header ────────────────────────────────────────────────────────────────
    _header = ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.Image(
                        src="logo_cryptoledger_full.png",
                        height=42,
                        fit=ft.BoxFit.CONTAIN,
                    ),
                    bgcolor="#000000",
                    border_radius=4,
                    padding=ft.padding.symmetric(0, 6),
                ),
                ft.Row(
                    [
                        _eye_btn,
                        ft.TextButton("Add Trade", on_click=on_add_trade, style=ft.ButtonStyle(color=GREEN)),
                        ft.TextButton("Reverse", on_click=on_reverse, style=ft.ButtonStyle(color=T_MUT)),
                        ft.TextButton("Import", on_click=on_import, style=ft.ButtonStyle(color=T_MUT)),
                        ft.TextButton("Export", on_click=on_export, style=ft.ButtonStyle(color=T_MUT)),
                        ft.TextButton("Refresh", on_click=refresh, style=ft.ButtonStyle(color=T_MUT)),
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
        _dashboard_view.height = h
        page.update()

    _tick("nav + header widgets built")

    # ── Splash + real UI in a Stack — splash hides after price fetch ─────────
    _sw, _sh = int(page.window.width), int(page.window.height)
    _splash_img = ft.Image(src="splash.gif", fit=ft.BoxFit.COVER, width=_sw, height=_sh)
    _splash = ft.Container(content=_splash_img, bgcolor=BG, width=_sw, height=_sh, visible=True)
    _real_ui = ft.Column([_header, _body_row], spacing=0, expand=True)
    _stack = ft.Stack([_real_ui, _splash], expand=True)
    page.add(_stack)

    # Extend resize handler so splash always covers the full window while visible
    def _on_resize(e=None) -> None:
        h = int(page.window.height) - _HEADER_H
        _body_row.height = h
        _nav_host.height = h
        _dashboard_view.height = h
        if _splash.visible:
            w, wh = int(page.window.width), int(page.window.height)
            _splash.width = w
            _splash.height = wh
            _splash_img.width = w
            _splash_img.height = wh
        page.update()

    page.on_resize = _on_resize
    # NOTE: main_view() returns after start() so the asyncio event loop can
    # flush the send queue and deliver the layout to Flutter.

    _SPLASH_MIN_S = 5.5   # minimum splash display time in seconds

    def _load_prices() -> None:
        _t0 = _time.perf_counter()
        _snap = None
        _err = None
        try:
            _snap = get_dashboard_snapshot(db_path, _price_provider, _price_fiat)
            _blog(f"TIMING  get_dashboard_snapshot() {(_time.perf_counter()-_t0)*1000:.1f}ms  positions={len(_snap.positions)}")
            # Auto-download icons for any new assets (silent, best-effort)
            icon_service.download_missing([p.asset for p in _snap.positions])
        except Exception as exc:
            _err = exc
            _blog(f"ERROR in _load_prices: {exc}")

        # Ensure splash plays for at least _SPLASH_MIN_S seconds
        elapsed = _time.perf_counter() - _t0
        remaining = _SPLASH_MIN_S - elapsed
        if remaining > 0:
            _time.sleep(remaining)

        async def _finish_on_ui_thread() -> None:
            nonlocal raw
            if _snap is not None:
                snap_holder[0] = _snap
                raw = _snap.positions
                update_kpis()
                build_pills()
                build_cards()
                _content.content = _dashboard_view
            _splash.visible = False
            page.update()
            _tick("dashboard visible — startup complete")

        page.run_task(_finish_on_ui_thread)

    threading.Thread(target=_load_prices, daemon=True).start()
    _tick("background price-fetch thread started — main_view returning")


def run_ui() -> None:
    _blog("CHECKPOINT 2 - run_ui entered")
    _blog("CHECKPOINT 3 - about to call ft.run(target=main_view)")
    # Serve assets from user data dir so downloaded icons are reachable by Flet
    _assets_dir = str(icon_service.USER_DATA_DIR)
    # Migrate splash.gif + venue_icons to user data dir if needed
    _splash_src = Path(__file__).parent.parent / "assets" / "splash.gif"
    _splash_dst = icon_service.USER_DATA_DIR / "splash.gif"
    if _splash_src.exists() and not _splash_dst.exists():
        _splash_dst.write_bytes(_splash_src.read_bytes())
    _logo_src = Path(__file__).parent.parent / "assets" / "logo_cryptoledger_full.png"
    _logo_dst = icon_service.USER_DATA_DIR / "logo_cryptoledger_full.png"
    if _logo_src.exists():
        _logo_dst.write_bytes(_logo_src.read_bytes())
    ft.run(main_view, assets_dir=_assets_dir)
    _blog("CHECKPOINT 3b - ft.run returned (window closed)")


if __name__ == "__main__":
    _blog("CHECKPOINT 0 - __main__ block reached (python -m ui.app_flet)")
    run_ui()