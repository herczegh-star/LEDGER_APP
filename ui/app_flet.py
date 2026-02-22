"""LedgerApp – Flet desktop UI.

Entrypoint: call run_ui() from main.py.
"""

from decimal import Decimal
from typing import Optional

import flet as ft

from core.services.ui_facade import create_app_context, get_dashboard_snapshot

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

SORTS = [
    ("ROI Total ↓",  "roi_desc"),
    ("ROI Total ↑",  "roi_asc"),
    ("PnL ↓",        "pnl_desc"),
    ("PnL ↑",        "pnl_asc"),
    ("Value ↓",      "val_desc"),
    ("Value ↑",      "val_asc"),
    ("A–Z",          "az"),
    ("Z–A",          "za"),
]


def _sort(items: list, key: str) -> list:
    _r = lambda p: p.roi_total or Decimal("-999999")
    _p = lambda p: p.unrealized_pnl or Decimal("0")
    _v = lambda p: p.value or Decimal("0")
    cfg = {
        "roi_desc": (_r, True),   "roi_asc":  (_r, False),
        "pnl_desc": (_p, True),   "pnl_asc":  (_p, False),
        "val_desc": (_v, True),   "val_asc":  (_v, False),
        "az":       (lambda p: p.asset, False),
        "za":       (lambda p: p.asset, True),
    }
    fn, rev = cfg.get(key, (lambda p: p.asset, False))
    return sorted(items, key=fn, reverse=rev)


# ── Main ───────────────────────────────────────────────────────────────────────

def main_view(page: ft.Page) -> None:
    try:
      _main_view_impl(page)
    except Exception:
        import traceback
        traceback.print_exc()
        raise


def _main_view_impl(page: ft.Page) -> None:
    ctx = create_app_context()
    db_path        = ctx.db_path
    _price_provider = ctx.price_provider
    _price_fiat    = ctx.fiat

    page.title       = "LedgerApp"
    page.bgcolor     = BG
    page.theme_mode  = ft.ThemeMode.DARK
    page.padding     = 0
    page.window.width  = 1200
    page.window.height = 760

    # ── State ──────────────────────────────────────────────────────────────────
    raw: list = []
    state = {"sort": "roi_desc"}

    # ── KPI text widgets (Value / PnL / ROI) ───────────────────────────────────
    w_val  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_PRI)
    w_pnl  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_roi  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)

    # Dynamic regions (populated by render functions)
    pills_row = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO)
    cards_col = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)

    # ── KPI update ─────────────────────────────────────────────────────────────
    def update_kpis() -> None:
        total_cost = sum(p.cost_basis for p in raw)

        vals = [p.value for p in raw if p.value is not None]
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
        def make_card(p) -> ft.Container:
            unr       = p.unrealized_pnl
            roi_total = p.roi_total      # live-price: (value-cost)/cost fraction
            roi_real  = p.roi_realized   # core: realized_pnl/cost_basis in %pts
            bc  = GREEN if (unr is not None and unr >= 0) else \
                  RED   if (unr is not None)              else BORDER

            def on_detail(e, a=p.asset) -> None:
                page.show_dialog(ft.SnackBar(
                    ft.Text(f"Detail: {a} (TODO)"), duration=2000
                ))

            def stat(label: str, value: str) -> ft.Column:
                return ft.Column([
                    ft.Text(label, size=11, color=T_MUT),
                    ft.Text(value, size=13, color=T_PRI),
                ], spacing=2, tight=True)

            return ft.Container(
                content=ft.Column([
                    # Top row: asset | unrealized PnL + Total ROI + Detail
                    ft.Row([
                        ft.Text(
                            p.asset, size=18,
                            weight=ft.FontWeight.BOLD, color=T_PRI,
                        ),
                        ft.Row([
                            ft.Text(_czk(unr, sign=True), size=14,
                                    weight=ft.FontWeight.W_600, color=_color(unr)),
                            ft.Text(_pct(roi_total), size=14,
                                    weight=ft.FontWeight.W_600, color=_color(roi_total)),
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
                        stat("Amount",          _amt(p.quantity, p.asset)),
                        stat("Avg Buy",         _czk(p.wac)),
                        stat("Cost Basis",      _czk(p.cost_basis)),
                        stat("Spot Price",      _czk(p.spot_price)),
                        stat("Value",           _czk(p.value)),
                        stat("ROI (Realized)",  _pct_pts(roi_real)),
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
                    blur_style=ft.BlurStyle.OUTER,
                ) if bc != BORDER else None,
            )

        cards_col.controls = [make_card(p) for p in _sort(raw, state["sort"])]
        cards_col.update()

    # ── Refresh ────────────────────────────────────────────────────────────────
    def refresh(e=None) -> None:
        nonlocal raw
        snap = get_dashboard_snapshot(db_path, _price_provider, _price_fiat)
        raw = snap.positions

        update_kpis()
        build_pills()
        build_cards()
        page.update()

    def on_export(e) -> None:
        open_export_dialog(page, db_path)

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
                kpi_box("Total Value", w_val),
                kpi_box("Total PnL",  w_pnl),
                kpi_box("Total ROI",  w_roi),
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

    from ui.modules.positions_view import build_positions_view as _build_pv
    _positions_wac_view, _run_positions = _build_pv(
        page, db_path, price_provider=_price_provider, fiat=_price_fiat
    )

    from ui.modules.health_view import build_health_view as _build_hv
    _health_view, _run_health = _build_hv(page, db_path)

    from ui.modules.add_trade_dialog import open_add_trade_dialog
    from ui.modules.export_dialog import open_export_dialog
    from ui.modules.import_dialog import open_import_dialog
    from ui.modules.reversal_dialog import open_reversal_dialog

    # Ledger view is built AFTER _refresh_all (needs it as a callback).
    # Closures read from the enclosing scope cell, so they see the final value.
    _ledger_view = None
    _run_ledger  = None

    _content = ft.Column([_positions_view], spacing=0, expand=True)

    def _on_nav(e) -> None:
        idx = e.control.selected_index
        if idx == 0:
            _content.controls = [_positions_view]
            refresh()
        elif idx == 1:
            _content.controls = [_reports_view]
            _run_report()
        elif idx == 2:
            _content.controls = [_positions_wac_view]
            _run_positions()
        elif idx == 3:
            _content.controls = [_health_view]
            _run_health()
        else:  # idx == 4
            _content.controls = [_ledger_view]
            _run_ledger()
        page.update()

    _nav = ft.NavigationRail(
        selected_index=0,
        on_change=_on_nav,
        min_width=72,
        bgcolor=BG_HDR,
        indicator_color=BLUE,
        destinations=[
            ft.NavigationRailDestination(icon="dashboard_outlined",        label="Dashboard"),
            ft.NavigationRailDestination(icon="bar_chart_outlined",        label="Reports"),
            ft.NavigationRailDestination(icon="table_chart_outlined",      label="Positions"),
            ft.NavigationRailDestination(icon="health_and_safety_outlined", label="Health"),
            ft.NavigationRailDestination(icon="table_rows_outlined",        label="Ledger"),
        ],
    )

    def _refresh_all() -> None:
        refresh()
        active = _content.controls[0] if _content.controls else None
        if active is _reports_view:
            _run_report()
        elif active is _positions_wac_view:
            _run_positions()
        elif active is _health_view:
            _run_health()
        elif active is _ledger_view:
            _run_ledger()

    # Build ledger view now that _refresh_all exists
    from ui.modules.ledger_view import build_ledger_view as _build_lv
    _ledger_view, _run_ledger = _build_lv(page, db_path, on_after_reverse=_refresh_all)

    def on_add_trade(e) -> None:
        open_add_trade_dialog(page, db_path, _refresh_all)

    def on_import(e) -> None:
        open_import_dialog(page, db_path, _refresh_all)

    def on_reverse(e) -> None:
        open_reversal_dialog(page, db_path, _refresh_all)

    # ── Page layout ────────────────────────────────────────────────────────────
    page.add(
        ft.Container(
            content=ft.Row([
                ft.Text("LedgerApp", size=20,
                        weight=ft.FontWeight.BOLD, color=T_PRI),
                ft.Row([
                    ft.TextButton("Add Trade", on_click=on_add_trade,
                                  style=ft.ButtonStyle(color=GREEN)),
                    ft.TextButton("Reverse", on_click=on_reverse,
                                  style=ft.ButtonStyle(color="#f97316")),
                    ft.TextButton("Import", on_click=on_import,
                                  style=ft.ButtonStyle(color=BLUE)),
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


def run_ui() -> None:
    ft.run(main_view)
