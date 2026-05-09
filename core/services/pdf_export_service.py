"""Dashboard PDF export service.

Generates a structured, text-based PDF from a DashboardSnapshotDTO.
Uses fpdf2 (pure Python, no system dependencies).

All content is rendered from pre-computed snapshot data — no DB calls,
no HTTP calls, no WAC computation.  Built-in Helvetica font is used so
the output is portable and ASCII-safe.

Public API:
    export_dashboard_pdf(snap, out_path) -> str
"""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.services.ui_facade import DashboardSnapshotDTO

_ZERO = Decimal("0")
_STABLECOINS = frozenset({"USDC", "USDT"})

# ── Page geometry — A4 portrait, 10 mm side margins ─────────────────────────
# usable width = 210 - 10 - 10 = 190 mm
_PAGE_W = 190
_ROW_H  = 6   # mm — data row height
_HDR_H  = 7   # mm — table header row height

# ── Global positions table — 8 columns, no Weight % (venue×asset reuses this) ─
# Widths must sum to _PAGE_W = 190.
_POS_COLS = [
    ("Asset",         18, "L"),
    ("Qty",           30, "R"),
    ("Avg Buy",       22, "R"),
    ("Spot",          22, "R"),
    ("Value",         25, "R"),
    ("Cost Basis",    25, "R"),
    ("PnL",           26, "R"),
    ("ROI",           22, "R"),
]

# ── Global positions table — 10 columns (dashboard page only) ────────────────
# _POS_COLS is kept unchanged so the Venue×Asset section can reuse it as-is.
# 2 mm shaved from each of the 7 non-Asset columns + 1 mm from one more
# → frees 15 mm for "Recovery".
_POS_W_COLS = [
    ("Asset",         18, "L"),
    ("Qty",           26, "R"),
    ("Avg Buy",       18, "R"),
    ("Spot",          18, "R"),
    ("Value",         21, "R"),
    ("Wt %",          13, "R"),
    ("Cost Basis",    21, "R"),
    ("PnL",           22, "R"),
    ("ROI",           18, "R"),
    ("Recovery",      15, "R"),
]

# ── Venue breakdown column definitions ────────────────────────────────────────
_VEN_COLS = [
    ("Venue",         50, "L"),
    ("Assets",        16, "R"),
    ("Net Inv.(CZK)", 38, "R"),
    ("Value (CZK)",   38, "R"),
    ("PnL (CZK)",     26, "R"),
    ("ROI",           22, "R"),
]

assert sum(c[1] for c in _POS_COLS)   == _PAGE_W, "Position columns must sum to page width"
assert sum(c[1] for c in _POS_W_COLS) == _PAGE_W, "Position+weight columns must sum to page width"
assert sum(c[1] for c in _VEN_COLS)   == _PAGE_W, "Venue columns must sum to page width"

# Venue x Asset total row: first 4 _POS_COLS merged into one label cell, last 4 kept.
_VA_LABEL_W = sum(c[1] for c in _POS_COLS[:4])  # 18+30+22+22 = 92


# ── Formatting helpers (ASCII-safe, no CZK/EUR suffix in table cells) ─────────

def _fmt(v: Optional[Decimal], sign: bool = False) -> str:
    """Format Decimal as a compact number string suitable for table cells."""
    if v is None:
        return "-"
    a = abs(v)
    places = 0 if a >= 1000 else (2 if a >= 1 else 4)
    n = f"{a:,.{places}f}"
    if v < 0:
        return f"-{n}"
    return f"+{n}" if sign else n


def _pct(v: Optional[Decimal]) -> str:
    """Format a fraction (0.25 -> '+25.00%')."""
    if v is None:
        return "-"
    return f"{'+' if v >= 0 else ''}{float(v) * 100:.2f}%"


def _pct_pts(v: Optional[Decimal]) -> str:
    """Format a value already in percentage points (25.00 -> '+25.00%')."""
    if v is None:
        return "-"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v}%"


def _qty(v: Decimal) -> str:
    return f"{v:.8f}".rstrip("0").rstrip(".")


def _weight_pct(value: Optional[Decimal], total_value: Optional[Decimal]) -> str:
    """Format position value as % of portfolio total (e.g. '15.64%').

    Returns '-' when either value is missing or total_value is zero.
    """
    if value is None or total_value is None or total_value == _ZERO:
        return "-"
    return f"{float(value / total_value * 100):.2f}%"


def _recovery_pct(wac: Decimal, spot: Optional[Decimal]) -> str:
    """Return the upside % needed to reach break-even when spot < wac.

    Formula: (wac - spot) / spot
    Returns '-' for profitable/break-even positions and when prices are absent.
    Always prefixed with '+' when shown (recovery is always a positive move).
    """
    if spot is None or spot <= _ZERO or wac <= _ZERO:
        return "-"
    if spot >= wac:
        return "-"
    return f"+{float((wac - spot) / spot * 100):.2f}%"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _table_row(pdf, col_defs: list, values: list, bold: bool = False, fill: bool = False) -> None:
    """Write one table row using the given column definitions."""
    pdf.set_font("Helvetica", "B" if bold else "", 9)
    if fill:
        pdf.set_fill_color(228, 236, 248)   # light blue-gray header
    else:
        pdf.set_fill_color(255, 255, 255)
    h = _HDR_H if bold else _ROW_H
    last = len(col_defs) - 1
    for i, (col, val) in enumerate(zip(col_defs, values)):
        _, w, align = col
        is_last = (i == last)
        pdf.cell(
            w=w, h=h, text=str(val), border=1, align=align, fill=fill,
            new_x="LMARGIN" if is_last else "RIGHT",
            new_y="NEXT"    if is_last else "TOP",
        )


def _section(pdf, title: str) -> None:
    """Print a bold section heading."""
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _kv(pdf, label: str, value: str, lw: int = 60) -> None:
    """Print a single key → value line."""
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(lw, 6, label + ":", new_x="RIGHT", new_y="TOP")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(_PAGE_W - lw, 6, value, new_x="LMARGIN", new_y="NEXT")


def _va_venue_header(pdf, venue_name: str) -> None:
    """Dark-filled banner row showing the venue name as a subsection header."""
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(45, 75, 115)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, f"  {venue_name}", border=0, fill=True,
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


def _va_total_row(
    pdf,
    value: Optional[Decimal],
    cost_basis: Decimal,
    pnl: Optional[Decimal],
    roi_str: str,
) -> None:
    """Subtotal row for one venue in the Venue x Asset section.

    The first four _POS_COLS (Asset/Qty/Avg Buy/Spot) are merged into a single
    'Total' label cell; the remaining four data columns keep their widths.
    """
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(228, 236, 248)
    _, w_val,  a_val  = _POS_COLS[4]
    _, w_cost, a_cost = _POS_COLS[5]
    _, w_pnl,  a_pnl  = _POS_COLS[6]
    _, w_roi,  a_roi  = _POS_COLS[7]
    pdf.cell(_VA_LABEL_W, _HDR_H, "Total", border=1, align="L", fill=True,
             new_x="RIGHT", new_y="TOP")
    pdf.cell(w_val,  _HDR_H, _fmt(value),          border=1, align=a_val,  fill=True, new_x="RIGHT", new_y="TOP")
    pdf.cell(w_cost, _HDR_H, _fmt(cost_basis),     border=1, align=a_cost, fill=True, new_x="RIGHT", new_y="TOP")
    pdf.cell(w_pnl,  _HDR_H, _fmt(pnl, sign=True), border=1, align=a_pnl,  fill=True, new_x="RIGHT", new_y="TOP")
    pdf.cell(w_roi,  _HDR_H, roi_str,              border=1, align=a_roi,  fill=True,
             new_x="LMARGIN", new_y="NEXT")


# ── Public API ────────────────────────────────────────────────────────────────

def export_dashboard_pdf(
    snap: "DashboardSnapshotDTO",
    out_path: str,
) -> str:
    """Generate a text-based portfolio dashboard PDF.

    All data is taken from *snap* — no DB or network calls.
    fpdf2 is imported lazily to keep module import fast.

    Args:
        snap:     DashboardSnapshotDTO from get_dashboard_snapshot().
        out_path: Destination file path (should end with .pdf).

    Returns:
        Absolute path to the written file.
    """
    from fpdf import FPDF  # lazy — keeps startup fast when fpdf2 unused

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    now = datetime.now()

    # ── PDF class with header/footer ──────────────────────────────────────────
    class _PDF(FPDF):
        def header(self) -> None:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(140, 140, 140)
            self.cell(
                _PAGE_W // 2, 5, "CryptoLedger - Portfolio Dashboard",
                new_x="RIGHT", new_y="TOP",
            )
            self.cell(
                _PAGE_W // 2, 5, f"Generated: {now.strftime('%Y-%m-%d %H:%M')}",
                align="R", new_x="LMARGIN", new_y="NEXT",
            )
            self.set_text_color(0, 0, 0)
            self.ln(3)

        def footer(self) -> None:
            self.set_y(-12)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 6, f"Page {self.page_no()}", align="C")
            self.set_text_color(0, 0, 0)

    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=10, top=15, right=10)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # ── Title ─────────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 11, "Portfolio Dashboard", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(
        0, 6, f"Export date: {now.strftime('%Y-%m-%d  %H:%M:%S')}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(5)

    # ── Portfolio Summary ─────────────────────────────────────────────────────
    _section(pdf, "Portfolio Summary")
    total_cost = sum((p.cost_basis for p in snap.positions), _ZERO)

    _kv(pdf, "Assets held",      str(snap.assets_held))
    _kv(pdf, "Total value",
        f"{_fmt(snap.total_value)} CZK" if snap.total_value is not None else "-")
    _kv(pdf, "Total cost basis", f"{_fmt(total_cost)} CZK")
    _kv(pdf, "Unrealized PnL",
        f"{_fmt(snap.unrealized_pnl, sign=True)} CZK"
        if snap.unrealized_pnl is not None else "-")
    _kv(pdf, "ROI (total)",      _pct(snap.roi_total))
    _kv(pdf, "Realized ROI",     _pct_pts(snap.realized_roi))
    pdf.ln(6)

    # ── Position table ────────────────────────────────────────────────────────
    positions_sorted = sorted(snap.positions, key=lambda p: p.asset)
    _section(pdf, f"Positions ({len(positions_sorted)})")

    _table_row(pdf, _POS_W_COLS, [c[0] for c in _POS_W_COLS], bold=True, fill=True)

    has_stable = False
    for pos in positions_sorted:
        is_stable = pos.asset.upper() in _STABLECOINS
        if is_stable:
            has_stable = True
        avg_buy = _fmt(pos.wac) + ("*" if is_stable else "")
        _table_row(pdf, _POS_W_COLS, [
            pos.asset,
            _qty(pos.quantity),
            avg_buy,
            _fmt(pos.spot_price),
            _fmt(pos.value),
            _weight_pct(pos.value, snap.total_value),
            _fmt(pos.cost_basis),
            _fmt(pos.unrealized_pnl, sign=True),
            _pct(pos.roi_total),
            _recovery_pct(pos.wac, pos.spot_price),
        ])

    if not positions_sorted:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(130, 130, 130)
        pdf.cell(0, 7, "No open positions.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    if has_stable:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(130, 130, 130)
        pdf.multi_cell(
            0, 5,
            "* For stablecoins, Avg Buy may include inherited cost basis from "
            "asset swaps and may not reflect the nominal USD price.",
        )
        pdf.set_text_color(0, 0, 0)

    pdf.ln(6)

    # ── Venue Breakdown ───────────────────────────────────────────────────────
    active_venues = {
        v: vdto for v, vdto in snap.by_venue.items()
        if vdto.assets_held > 0 or vdto.positions
    }
    if active_venues:
        _section(pdf, f"Venue Breakdown ({len(active_venues)} venues)")
        _table_row(pdf, _VEN_COLS, [c[0] for c in _VEN_COLS], bold=True, fill=True)
        for venue, vdto in sorted(active_venues.items()):
            invested_czk = vdto.invested.get("CZK") or vdto.invested.get("czk")
            _table_row(pdf, _VEN_COLS, [
                venue.upper(),
                str(vdto.assets_held),
                _fmt(invested_czk) if invested_czk else "-",
                _fmt(vdto.value_total),
                _fmt(vdto.unrealized_pnl, sign=True),
                _pct(vdto.unrealized_pnl / vdto.cost_basis_total)
                if vdto.unrealized_pnl is not None and vdto.cost_basis_total > _ZERO
                else "-",
            ])

    # ── Venue x Asset Breakdown ───────────────────────────────────────────────
    if active_venues:
        pdf.ln(6)
        _section(pdf, "Venue x Asset Breakdown")
        va_has_stable = False

        for venue, vdto in sorted(active_venues.items()):
            v_positions = sorted(
                [p for p in vdto.positions if p.quantity > _ZERO],
                key=lambda p: p.asset,
            )
            if not v_positions:
                continue

            _va_venue_header(pdf, venue.upper())
            _table_row(pdf, _POS_COLS, [c[0] for c in _POS_COLS], bold=True, fill=True)

            for pos in v_positions:
                is_stable = pos.asset.upper() in _STABLECOINS
                if is_stable:
                    va_has_stable = True
                avg_buy = _fmt(pos.wac) + ("*" if is_stable else "")
                _table_row(pdf, _POS_COLS, [
                    pos.asset,
                    _qty(pos.quantity),
                    avg_buy,
                    _fmt(pos.spot_price),
                    _fmt(pos.value),
                    _fmt(pos.cost_basis),
                    _fmt(pos.unrealized_pnl, sign=True),
                    _pct(pos.roi_total),
                ])

            # Per-venue subtotals (partial sums — skip assets without a price)
            v_cost = sum((p.cost_basis for p in v_positions), _ZERO)
            v_val_items  = [p.value          for p in v_positions if p.value          is not None]
            v_pnl_items  = [p.unrealized_pnl for p in v_positions if p.unrealized_pnl is not None]
            v_value = sum(v_val_items, _ZERO) if v_val_items else None
            v_pnl   = sum(v_pnl_items, _ZERO) if v_pnl_items else None
            v_roi_str = (
                _pct(v_pnl / v_cost)
                if v_pnl is not None and v_cost > _ZERO
                else "-"
            )
            _va_total_row(pdf, v_value, v_cost, v_pnl, v_roi_str)
            pdf.ln(3)

        if va_has_stable:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(130, 130, 130)
            pdf.multi_cell(
                0, 5,
                "* For stablecoins, Avg Buy may include inherited cost basis from "
                "asset swaps and may not reflect the nominal USD price.",
            )
            pdf.set_text_color(0, 0, 0)

    pdf.output(out_path)
    return os.path.abspath(out_path)
