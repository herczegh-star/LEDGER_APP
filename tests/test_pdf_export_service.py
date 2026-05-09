"""Tests for core/services/pdf_export_service.py.

Verifies that export_dashboard_pdf() produces a valid text-based PDF
file from a DashboardSnapshotDTO without touching the ledger or network.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

from core.services.pdf_export_service import export_dashboard_pdf
from core.services.ui_facade import DashboardSnapshotDTO, PositionDTO, VenueDashboardDTO

_ZERO = Decimal("0")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pos(
    asset: str,
    qty: str,
    wac: str,
    cost: str,
    *,
    spot: str | None = None,
    value: str | None = None,
    pnl: str | None = None,
) -> PositionDTO:
    return PositionDTO(
        asset=asset,
        quantity=Decimal(qty),
        wac=Decimal(wac),
        cost_basis=Decimal(cost),
        realized_pnl=_ZERO,
        spot_price=Decimal(spot)  if spot  is not None else None,
        value=Decimal(value)      if value is not None else None,
        unrealized_pnl=Decimal(pnl) if pnl is not None else None,
    )


def _snap(positions=None, by_venue=None) -> DashboardSnapshotDTO:
    pos = positions or []
    total_cost = sum(p.cost_basis for p in pos)
    vals = [p.value for p in pos if p.value is not None]
    total_val = sum(vals, _ZERO) if vals else None
    pnls = [p.unrealized_pnl for p in pos if p.unrealized_pnl is not None]
    unr = sum(pnls, _ZERO) if pnls else None
    return DashboardSnapshotDTO(
        invested={"CZK": total_cost},
        net_flow={"CZK": -total_cost},
        assets_held=len([p for p in pos if p.quantity > _ZERO]),
        top_position=None,
        realized_roi=None,
        positions=pos,
        total_value=total_val,
        unrealized_pnl=unr,
        roi_total=(unr / total_cost if unr is not None and total_cost > _ZERO else None),
        by_venue=by_venue or {},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_export_creates_pdf_file():
    snap = _snap([_pos("BTC", "1", "50000", "50000", spot="60000", value="60000", pnl="10000")])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "dashboard.pdf")
        result = export_dashboard_pdf(snap, out)
        assert os.path.isfile(result)
        assert result.endswith(".pdf")


def test_output_is_valid_pdf():
    snap = _snap([_pos("ETH", "2", "1000", "2000", spot="1200", value="2400", pnl="400")])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test.pdf")
        export_dashboard_pdf(snap, out)
        with open(out, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-", "File must start with %PDF- magic bytes"


def test_pdf_nonzero_size():
    snap = _snap([_pos("BTC", "1", "50000", "50000")])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "test.pdf")
        export_dashboard_pdf(snap, out)
        assert os.path.getsize(out) > 1500, "PDF should be larger than 1.5 KB"


def test_empty_positions_does_not_crash():
    snap = _snap([])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "empty.pdf")
        result = export_dashboard_pdf(snap, out)
        assert os.path.isfile(result)
        with open(out, "rb") as f:
            assert f.read(4) == b"%PDF"


def test_missing_spot_prices_handled():
    snap = _snap([
        _pos("BTC", "1", "50000", "50000"),   # no spot
        _pos("ETH", "2", "1000",  "2000", spot="1200", value="2400", pnl="400"),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "noprice.pdf")
        export_dashboard_pdf(snap, out)   # must not raise
        assert os.path.isfile(out)


def test_stablecoin_does_not_crash():
    snap = _snap([
        _pos("USDC", "1000", "22", "22000", spot="23", value="23000", pnl="1000"),
        _pos("BTC",  "1",    "50000", "50000", spot="60000", value="60000", pnl="10000"),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "stable.pdf")
        export_dashboard_pdf(snap, out)
        assert os.path.isfile(out)


def test_venue_breakdown_included():
    vdto = VenueDashboardDTO(
        venue="bybit",
        positions=[_pos("BTC", "1", "50000", "50000", spot="60000", value="60000", pnl="10000")],
        holdings={"BTC": Decimal("1")},
        cost_basis_total=Decimal("50000"),
        assets_held=1,
        invested={"CZK": Decimal("50000")},
        net_flow={"CZK": Decimal("-50000")},
        value_total=Decimal("60000"),
        unrealized_pnl=Decimal("10000"),
    )
    snap = _snap(
        positions=[_pos("BTC", "1", "50000", "50000", spot="60000", value="60000", pnl="10000")],
        by_venue={"bybit": vdto},
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "venue.pdf")
        export_dashboard_pdf(snap, out)
        assert os.path.isfile(out)


def test_venue_asset_breakdown_multiple_venues():
    """Venue x Asset section renders without crash for two venues with different assets."""
    def _vdto(venue, asset, qty, wac, cost, spot, value, pnl):
        pos = _pos(asset, qty, wac, cost, spot=spot, value=value, pnl=pnl)
        return VenueDashboardDTO(
            venue=venue,
            positions=[pos],
            holdings={asset: Decimal(qty)},
            cost_basis_total=Decimal(cost),
            assets_held=1,
            invested={"CZK": Decimal(cost)},
            net_flow={"CZK": -Decimal(cost)},
            value_total=Decimal(value),
            unrealized_pnl=Decimal(pnl),
        )

    vdto_bybit  = _vdto("bybit",  "BTC", "1",  "50000", "50000", "60000", "60000", "10000")
    vdto_kraken = _vdto("kraken", "ETH", "5",  "1000",  "5000",  "1200",  "6000",  "1000")
    snap = _snap(
        positions=[
            _pos("BTC", "1", "50000", "50000", spot="60000", value="60000", pnl="10000"),
            _pos("ETH", "5", "1000",  "5000",  spot="1200",  value="6000",  pnl="1000"),
        ],
        by_venue={"bybit": vdto_bybit, "kraken": vdto_kraken},
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "multi_venue.pdf")
        export_dashboard_pdf(snap, out)
        assert os.path.isfile(out)
        with open(out, "rb") as f:
            assert f.read(4) == b"%PDF"


def test_venue_asset_breakdown_no_spot_price():
    """Venue x Asset section handles positions with no spot price gracefully."""
    pos_no_price = _pos("BTC", "1", "50000", "50000")   # no spot
    vdto = VenueDashboardDTO(
        venue="bybit",
        positions=[pos_no_price],
        holdings={"BTC": Decimal("1")},
        cost_basis_total=Decimal("50000"),
        assets_held=1,
        invested={"CZK": Decimal("50000")},
        net_flow={"CZK": Decimal("-50000")},
        value_total=None,
        unrealized_pnl=None,
    )
    snap = _snap(positions=[pos_no_price], by_venue={"bybit": vdto})
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "no_spot.pdf")
        export_dashboard_pdf(snap, out)
        assert os.path.isfile(out)


def test_returns_absolute_path():
    snap = _snap([_pos("SOL", "10", "100", "1000")])
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "sub", "test.pdf")
        result = export_dashboard_pdf(snap, out)
        assert os.path.isabs(result)
        assert os.path.isfile(result)


def test_no_ledger_writes():
    import inspect
    import re
    import core.services.pdf_export_service as svc
    src = inspect.getsource(svc)
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"#.*", "", code)
    assert "LedgerStore" not in code
    assert "import_rows" not in code
