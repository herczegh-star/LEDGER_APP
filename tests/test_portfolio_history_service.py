"""Integration tests for core/services/portfolio_history_service.py.

Covers:
  - maybe_save_portfolio_snapshot(): time-gate, headline formula, PRESALE
    exclusion in the header vs. completeness in per-asset positions,
    net_flow_czk extraction, never-raises on missing schema.
  - get_portfolio_value_trend(): a full historical-reconstruction scenario
    (BUY BTC, later BUY ETH, SELL part of BTC, seeded historical prices)
    verifying quantity/cost_basis/portfolio_value/unrealized_pnl/roi per
    day, the backfill/live_snapshot hybrid combination, the PRESALE
    exclusion, and the missing-price asymmetry that mirrors
    ui/app_flet.py's update_kpis() on purpose (not a new definition).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from core.historical_price_store import HistoricalPrice, HistoricalPriceStore
from core.ledger_store import LedgerStore
from core.portfolio_snapshot_store import (
    PortfolioSnapshotPositionRecord,
    PortfolioSnapshotRecord,
    PortfolioSnapshotStore,
)
from core.services.portfolio_history_service import (
    get_portfolio_value_trend,
    maybe_save_portfolio_snapshot,
)
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import DashboardSnapshotDTO, PositionDTO

_T0 = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture
def db(tmp_path):
    """A fully-initialized DB (ledger + both new side-tables)."""
    db_path = str(tmp_path / "test.db")
    LedgerStore(db_path).close()
    HistoricalPriceStore(db_path).ensure_schema()
    PortfolioSnapshotStore(db_path).ensure_schema()
    return db_path


def _snap(positions, net_flow=None):
    return DashboardSnapshotDTO(
        invested={}, net_flow=net_flow or {}, assets_held=len(positions),
        top_position=None, realized_roi=None, positions=positions,
    )


def _pos(asset, qty, cost_basis, spot_price=None):
    p = PositionDTO(
        asset=asset, quantity=Decimal(qty), wac=Decimal(cost_basis) / Decimal(qty),
        cost_basis=Decimal(cost_basis), realized_pnl=Decimal("0"),
    )
    if spot_price is not None:
        p.spot_price = Decimal(spot_price)
        p.value = p.quantity * p.spot_price
        if p.cost_basis > 0:
            p.unrealized_pnl = p.value - p.cost_basis
            p.roi_total = p.unrealized_pnl / p.cost_basis
    return p


# ── maybe_save_portfolio_snapshot(): time gate ──────────────────────────────

def test_first_call_saves_a_snapshot(db):
    snap = _snap([_pos("BTC", "1", "2000000", "2500000")])
    pk = maybe_save_portfolio_snapshot(db, snap, now=_T0)
    assert pk is not None
    store = PortfolioSnapshotStore(db)
    assert store.count() == 1
    store.close()


def test_second_call_before_interval_does_not_save(db):
    snap = _snap([_pos("BTC", "1", "2000000", "2500000")])
    maybe_save_portfolio_snapshot(db, snap, now=_T0)
    pk2 = maybe_save_portfolio_snapshot(db, snap, now=_T0 + timedelta(hours=1))
    assert pk2 is None
    store = PortfolioSnapshotStore(db)
    assert store.count() == 1  # no duplicate
    store.close()


def test_call_after_interval_saves_a_new_one(db):
    snap = _snap([_pos("BTC", "1", "2000000", "2500000")])
    maybe_save_portfolio_snapshot(db, snap, now=_T0)
    pk2 = maybe_save_portfolio_snapshot(db, snap, now=_T0 + timedelta(hours=6, seconds=1))
    assert pk2 is not None
    store = PortfolioSnapshotStore(db)
    assert store.count() == 2
    store.close()


def test_custom_min_interval_respected(db):
    snap = _snap([_pos("BTC", "1", "2000000", "2500000")])
    maybe_save_portfolio_snapshot(db, snap, now=_T0, min_interval=timedelta(minutes=30))
    pk2 = maybe_save_portfolio_snapshot(db, snap, now=_T0 + timedelta(minutes=10), min_interval=timedelta(minutes=30))
    assert pk2 is None
    pk3 = maybe_save_portfolio_snapshot(db, snap, now=_T0 + timedelta(minutes=31), min_interval=timedelta(minutes=30))
    assert pk3 is not None


# ── maybe_save_portfolio_snapshot(): headline formula + data shape ─────────

def test_headline_formula_matches_hand_computation(db):
    positions = [
        _pos("BTC", "1", "2000000", "2500000"),   # value=2,500,000
        _pos("ETH", "10", "800000", "82000"),      # value=820,000
    ]
    snap = _snap(positions, net_flow={"CZK": Decimal("-2800000")})
    maybe_save_portfolio_snapshot(db, snap, now=_T0)

    latest = PortfolioSnapshotStore(db).get_latest_snapshot()
    assert latest.cost_basis == Decimal("2800000")
    assert latest.portfolio_value == Decimal("3320000")       # 2,500,000+820,000
    assert latest.unrealized_pnl == Decimal("520000")          # 3,320,000-2,800,000
    assert latest.roi_total == Decimal("520000") / Decimal("2800000")
    assert latest.net_flow_czk == Decimal("-2800000")


def test_presale_excluded_from_header_but_present_in_positions(db):
    positions = [
        _pos("BTC", "1", "2000000", "2500000"),
        _pos("TICS", "1000", "10000", "10"),   # PRESALE_ASSETS — value=10,000
    ]
    snap = _snap(positions)
    maybe_save_portfolio_snapshot(db, snap, now=_T0)

    latest = PortfolioSnapshotStore(db).get_latest_snapshot()
    # Header totals exclude TICS entirely:
    assert latest.cost_basis == Decimal("2000000")
    assert latest.portfolio_value == Decimal("2500000")
    # But per-asset detail keeps BOTH assets:
    assets_in_detail = {p.asset for p in latest.positions}
    assert assets_in_detail == {"BTC", "TICS"}


def test_no_priced_position_yields_none_not_zero(db):
    """Mirrors update_kpis(): if nothing has a known price, Value/PnL/ROI
    are all None together — never a misleading 0."""
    positions = [_pos("BTC", "1", "2000000", spot_price=None)]
    snap = _snap(positions)
    maybe_save_portfolio_snapshot(db, snap, now=_T0)

    latest = PortfolioSnapshotStore(db).get_latest_snapshot()
    assert latest.portfolio_value is None
    assert latest.unrealized_pnl is None
    assert latest.roi_total is None
    assert latest.cost_basis == Decimal("2000000")  # still always known


def test_schema_not_initialized_returns_none_not_raise(tmp_path):
    """Called against a DB that never ran create_db() for this store —
    must degrade silently, never crash the caller (ui/app_flet.py's
    refresh(), which calls this right after get_dashboard_snapshot())."""
    db_path = str(tmp_path / "bare.db")
    LedgerStore(db_path).close()
    snap = _snap([_pos("BTC", "1", "2000000", "2500000")])
    result = maybe_save_portfolio_snapshot(db_path, snap)  # must not raise
    assert result is None


# ── get_portfolio_value_trend(): full historical reconstruction scenario ────

_D1 = date(2026, 1, 1)   # BUY 1 BTC @ 2,000,000 + BUY 100 TICS @ 10,000 (presale)
_D2 = date(2026, 1, 2)   # no trades
_D3 = date(2026, 1, 3)   # BUY 10 ETH @ 800,000
_D4 = date(2026, 1, 4)   # no trades
_D5 = date(2026, 1, 5)   # SELL 0.4 BTC for 1,000,000
_D6 = date(2026, 1, 6)   # no trades; ETH price deliberately NOT seeded


@pytest.fixture
def reconstruction_db(db):
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1, 10, 0), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("2000000"),
        venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1, 11, 0), base_asset="TICS",
        base_amount=Decimal("100"), quote_currency="CZK", quote_amount=Decimal("10000"),
        venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 3, 10, 0), base_asset="ETH",
        base_amount=Decimal("10"), quote_currency="CZK", quote_amount=Decimal("800000"),
        venue="kraken",
    ))
    add_trade(db, AddTradeInput(
        type="SELL", timestamp=datetime(2026, 1, 5, 9, 0), base_asset="BTC",
        base_amount=Decimal("0.4"), quote_currency="CZK", quote_amount=Decimal("1000000"),
        venue="kraken",
    ))

    prices = HistoricalPriceStore(db)
    seed = [
        ("BTC", _D1, "2000000"), ("BTC", _D2, "2100000"), ("BTC", _D3, "2200000"),
        ("BTC", _D4, "2150000"), ("BTC", _D5, "2300000"), ("BTC", _D6, "2300000"),
        ("ETH", _D3, "80000"), ("ETH", _D4, "82000"), ("ETH", _D5, "85000"),
        # ETH on _D6 deliberately NOT seeded — tests the missing-price asymmetry.
        ("TICS", _D1, "100"), ("TICS", _D2, "100"), ("TICS", _D3, "100"),
        ("TICS", _D4, "100"), ("TICS", _D5, "100"), ("TICS", _D6, "100"),
    ]
    for asset, d, price in seed:
        prices.set_price(HistoricalPrice(asset=asset, price_date=d, price_czk=Decimal(price), fetched_at=datetime.now()))
    prices.close()
    return db


def test_day_before_any_trade_is_empty_not_error(reconstruction_db):
    points = get_portfolio_value_trend(reconstruction_db, date(2025, 12, 31), date(2025, 12, 31))
    assert len(points) == 1
    p = points[0]
    assert p.portfolio_value is None
    assert p.unrealized_pnl is None
    assert p.roi_total is None
    assert p.cost_basis == Decimal("0")
    assert p.source == "backfill"


def test_day1_btc_only_at_cost_pnl_zero(reconstruction_db):
    points = get_portfolio_value_trend(reconstruction_db, _D1, _D1)
    p = points[0]
    assert p.cost_basis == Decimal("2000000")           # TICS excluded (presale)
    assert p.portfolio_value == Decimal("2000000")
    assert p.unrealized_pnl == Decimal("0")
    assert p.roi_total == Decimal("0")


def test_day2_btc_price_moved_no_new_trade(reconstruction_db):
    points = get_portfolio_value_trend(reconstruction_db, _D2, _D2)
    p = points[0]
    assert p.cost_basis == Decimal("2000000")
    assert p.portfolio_value == Decimal("2100000")
    assert p.unrealized_pnl == Decimal("100000")
    assert p.roi_total == Decimal("100000") / Decimal("2000000")


def test_day3_eth_purchased_both_assets_counted(reconstruction_db):
    points = get_portfolio_value_trend(reconstruction_db, _D3, _D3)
    p = points[0]
    assert p.cost_basis == Decimal("2800000")            # 2,000,000 + 800,000
    assert p.portfolio_value == Decimal("3000000")       # 2,200,000 + 800,000
    assert p.unrealized_pnl == Decimal("200000")
    assert p.roi_total == Decimal("200000") / Decimal("2800000")


def test_day4_both_assets_price_moved(reconstruction_db):
    points = get_portfolio_value_trend(reconstruction_db, _D4, _D4)
    p = points[0]
    assert p.cost_basis == Decimal("2800000")
    assert p.portfolio_value == Decimal("2970000")       # 2,150,000 + 820,000
    assert p.unrealized_pnl == Decimal("170000")
    assert p.roi_total == Decimal("170000") / Decimal("2800000")


def test_day5_partial_btc_sell_reduces_quantity_and_cost_basis(reconstruction_db):
    """SELL 0.4 BTC at WAC 2,000,000/unit removes 800,000 cost basis,
    leaving BTC quantity=0.6, cost_basis=1,200,000 — the SELL happens on
    this exact day and the end-of-day cutoff must include it."""
    points = get_portfolio_value_trend(reconstruction_db, _D5, _D5)
    p = points[0]
    assert p.cost_basis == Decimal("2000000")            # 1,200,000 (BTC) + 800,000 (ETH)
    assert p.portfolio_value == Decimal("2230000")        # 0.6*2,300,000 + 10*85,000 = 1,380,000+850,000
    assert p.unrealized_pnl == Decimal("230000")
    assert p.roi_total == Decimal("230000") / Decimal("2000000")


def test_day6_missing_eth_price_mirrors_dashboard_asymmetry(reconstruction_db):
    """ETH has no cached price on _D6 — per update_kpis()'s own existing
    behaviour, its cost_basis is still counted in total_cost, but its
    value contributes nothing to portfolio_value. This is intentionally
    reproduced, not fixed — see portfolio_history_service.py's docstring."""
    points = get_portfolio_value_trend(reconstruction_db, _D6, _D6)
    p = points[0]
    assert p.cost_basis == Decimal("2000000")             # 1,200,000 (BTC) + 800,000 (ETH) — ETH counted
    assert p.portfolio_value == Decimal("1380000")        # only BTC: 0.6*2,300,000 — ETH excluded (no price)
    assert p.unrealized_pnl == Decimal("1380000") - Decimal("2000000")
    assert p.roi_total == (Decimal("1380000") - Decimal("2000000")) / Decimal("2000000")
    assert p.roi_total < 0


def test_full_range_returns_one_point_per_day_in_order(reconstruction_db):
    points = get_portfolio_value_trend(reconstruction_db, _D1, _D6)
    assert [pt.date for pt in points] == [_D1, _D2, _D3, _D4, _D5, _D6]
    assert all(pt.source == "backfill" for pt in points)


def test_date_from_after_date_to_raises_value_error(reconstruction_db):
    with pytest.raises(ValueError):
        get_portfolio_value_trend(reconstruction_db, _D3, _D1)


# ── get_portfolio_value_trend(): hybrid backfill + live_snapshot ───────────

def test_live_snapshot_overrides_backfill_for_its_day(reconstruction_db):
    """A saved portfolio_snapshots row for _D5 must be returned AS-IS
    (source='live_snapshot'), even though its values deliberately differ
    from what backfill reconstruction would compute — proving the trend
    actually prefers the live row, not just happening to match it."""
    store = PortfolioSnapshotStore(reconstruction_db)
    store.save_snapshot(PortfolioSnapshotRecord(
        pk=None, timestamp=datetime.combine(_D5, datetime.min.time()).replace(hour=14),
        portfolio_value=Decimal("9999999"), cost_basis=Decimal("1111111"),
        unrealized_pnl=Decimal("8888888"), roi_total=Decimal("8"), net_flow_czk=None,
        positions=[PortfolioSnapshotPositionRecord(
            asset="BTC", quantity=Decimal("0.6"), spot_price=Decimal("2300000"),
            value=Decimal("1380000"), cost_basis=Decimal("1200000"),
        )],
    ))
    store.close()

    points = get_portfolio_value_trend(reconstruction_db, _D4, _D6)
    by_date = {p.date: p for p in points}
    assert by_date[_D5].source == "live_snapshot"
    assert by_date[_D5].portfolio_value == Decimal("9999999")
    assert by_date[_D4].source == "backfill"
    assert by_date[_D6].source == "backfill"


def test_most_recent_snapshot_of_a_day_wins_when_multiple_saved(reconstruction_db):
    store = PortfolioSnapshotStore(reconstruction_db)
    base_ts = datetime.combine(_D5, datetime.min.time())
    store.save_snapshot(PortfolioSnapshotRecord(
        pk=None, timestamp=base_ts.replace(hour=9), portfolio_value=Decimal("1111"),
        cost_basis=Decimal("1"), unrealized_pnl=Decimal("1"), roi_total=Decimal("1"),
        net_flow_czk=None, positions=[],
    ))
    store.save_snapshot(PortfolioSnapshotRecord(
        pk=None, timestamp=base_ts.replace(hour=18), portfolio_value=Decimal("2222"),
        cost_basis=Decimal("2"), unrealized_pnl=Decimal("2"), roi_total=Decimal("2"),
        net_flow_czk=None, positions=[],
    ))
    store.close()

    points = get_portfolio_value_trend(reconstruction_db, _D5, _D5)
    assert points[0].portfolio_value == Decimal("2222")  # the later (18h) snapshot wins


def test_no_historical_prices_schema_degrades_to_none_not_error(db):
    """Ledger has trades but historical_prices/backfill was never run for
    this asset — must yield cost_basis-only points, not crash."""
    add_trade(db, AddTradeInput(
        type="BUY", timestamp=datetime(2026, 1, 1, 10, 0), base_asset="BTC",
        base_amount=Decimal("1"), quote_currency="CZK", quote_amount=Decimal("2000000"),
        venue="kraken",
    ))
    points = get_portfolio_value_trend(db, date(2026, 1, 1), date(2026, 1, 1))
    p = points[0]
    assert p.cost_basis == Decimal("2000000")
    assert p.portfolio_value is None
    assert p.source == "backfill"
