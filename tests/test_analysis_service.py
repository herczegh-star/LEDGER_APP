"""Tests for core.services.analysis_service — Sell Simulator.

Verifies filtering logic, total aggregation, edge cases, and that the
service is strictly read-only (no LedgerStore / RawRow usage).
"""
from __future__ import annotations

from decimal import Decimal

from core.services.analysis_service import simulate_sell
from core.services.ui_facade import PositionDTO, VenueDashboardDTO

_ZERO = Decimal("0")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pos(
    asset: str,
    qty: str,
    wac: str,
    cost_basis: str,
    *,
    spot: str | None = None,
    value: str | None = None,
    pnl: str | None = None,
) -> PositionDTO:
    return PositionDTO(
        asset=asset,
        quantity=Decimal(qty),
        wac=Decimal(wac),
        cost_basis=Decimal(cost_basis),
        realized_pnl=_ZERO,
        spot_price=Decimal(spot) if spot is not None else None,
        value=Decimal(value) if value is not None else None,
        unrealized_pnl=Decimal(pnl) if pnl is not None else None,
    )


def _vdto(positions: list, venue: str = "bybit") -> VenueDashboardDTO:
    return VenueDashboardDTO(
        venue=venue,
        positions=positions,
        holdings={},
        cost_basis_total=sum(p.cost_basis for p in positions),
        assets_held=len([p for p in positions if p.quantity > _ZERO]),
        invested={},
        net_flow={},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_profitable_only_excludes_loss_position():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000", spot="1500", value="1500", pnl="500"),
        _pos("ETH", "2", "500",  "1000", spot="400",  value="800",  pnl="-200"),
    ])
    result = simulate_sell(vdto, mode="profitable_only")
    assert len(result.rows) == 1
    assert result.rows[0].asset == "BTC"


def test_loss_only_excludes_profitable_position():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000", spot="1500", value="1500", pnl="500"),
        _pos("ETH", "2", "500",  "1000", spot="400",  value="800",  pnl="-200"),
    ])
    result = simulate_sell(vdto, mode="loss_only")
    assert len(result.rows) == 1
    assert result.rows[0].asset == "ETH"


def test_all_includes_both_directions():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000", spot="1500", value="1500", pnl="500"),
        _pos("ETH", "2", "500",  "1000", spot="400",  value="800",  pnl="-200"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert len(result.rows) == 2


def test_totals_correctly_summed():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000", spot="1500", value="1500", pnl="500"),
        _pos("ETH", "2", "500",  "1000", spot="400",  value="800",  pnl="-200"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert result.total_cost_basis    == Decimal("2000")
    assert result.total_value         == Decimal("2300")
    assert result.total_simulated_pnl == Decimal("300")


def test_zero_quantity_excluded():
    vdto = _vdto([
        _pos("BTC", "0", "1000", "0",   spot="1500", value="0",   pnl="0"),
        _pos("ETH", "1", "500",  "500", spot="600",  value="600", pnl="100"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert not any(r.asset == "BTC" for r in result.rows)
    assert len(result.rows) == 1


def test_negative_quantity_excluded():
    vdto = _vdto([
        _pos("BTC", "-1", "1000", "0",   spot="1500"),
        _pos("ETH", "1",  "500",  "500", spot="600", value="600", pnl="100"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert not any(r.asset == "BTC" for r in result.rows)
    assert len(result.rows) == 1


def test_wallet_only_roi_is_none_when_cost_basis_zero():
    vdto = _vdto([
        _pos("SOL", "10", "0", "0", spot="200", value="2000", pnl="2000"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert len(result.rows) == 1
    assert result.rows[0].roi is None


def test_missing_price_in_all_mode_included_and_flagged():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000"),                              # no spot
        _pos("ETH", "1", "500",  "500", spot="600", value="600", pnl="100"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert len(result.rows) == 2
    assert "BTC" in result.missing_prices
    btc_row = next(r for r in result.rows if r.asset == "BTC")
    assert btc_row.simulated_pnl is None


def test_missing_price_excluded_in_profitable_only():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000"),                              # no spot
        _pos("ETH", "1", "500",  "500", spot="600", value="600", pnl="100"),
    ])
    result = simulate_sell(vdto, mode="profitable_only")
    assert len(result.rows) == 1
    assert result.rows[0].asset == "ETH"


def test_missing_price_excluded_in_loss_only():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000"),                              # no spot
        _pos("ETH", "1", "500",  "500", spot="400", value="400", pnl="-100"),
    ])
    result = simulate_sell(vdto, mode="loss_only")
    assert len(result.rows) == 1
    assert result.rows[0].asset == "ETH"


def test_rows_sorted_alphabetically():
    vdto = _vdto([
        _pos("SOL", "1", "100", "100", spot="150", value="150", pnl="50"),
        _pos("BTC", "1", "100", "100", spot="150", value="150", pnl="50"),
        _pos("ETH", "1", "100", "100", spot="150", value="150", pnl="50"),
    ])
    result = simulate_sell(vdto, mode="all")
    assert [r.asset for r in result.rows] == ["BTC", "ETH", "SOL"]


def test_empty_venue_returns_empty_result():
    vdto = _vdto([])
    result = simulate_sell(vdto, mode="all")
    assert result.rows == []
    assert result.total_cost_basis == _ZERO
    assert result.total_value is None
    assert result.total_simulated_pnl is None


def test_partial_totals_when_some_prices_missing():
    vdto = _vdto([
        _pos("BTC", "1", "1000", "1000"),                              # no price
        _pos("ETH", "1", "500",  "500", spot="600", value="600", pnl="100"),
    ])
    result = simulate_sell(vdto, mode="all")
    # totals are partial (only ETH counted)
    assert result.total_value         == Decimal("600")
    assert result.total_simulated_pnl == Decimal("100")
    assert result.total_cost_basis    == Decimal("1500")   # BTC cost still counted


def test_no_ledger_writes():
    import inspect
    import re
    import core.services.analysis_service as svc
    src = inspect.getsource(svc)
    # Strip docstrings and inline comments before checking for forbidden symbols.
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"'''.*?'''", "", code, flags=re.DOTALL)
    code = re.sub(r"#.*", "", code)
    assert "LedgerStore" not in code
    assert "RawRow" not in code
    assert "import_rows" not in code
