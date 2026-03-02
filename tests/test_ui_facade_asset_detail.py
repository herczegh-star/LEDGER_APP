"""Tests for ui_facade.get_asset_detail().

Verifies:
  1) Aggregated position equals compute_positions on all rows.
  2) Venue breakdown has correct per-venue quantities and WAC.
  3) WAC per venue equals compute_positions called with venue-filtered rows.
  4) rows field equals timeline_filtered for the asset.
  5) Aggregated WAC equals compute_positions on all rows.
  6) Unknown asset returns zero-filled DTO (no exception).
  7) Case normalization: lowercase asset yields same result as uppercase.
  8) Price enrichment via stub price_provider populates spot/value/unrealized/roi.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List

import pytest

from core.ledger_store import LedgerStore
from core.reports.positions import compute_positions
from core.services.ui_facade import (
    AddTradeRequestDTO,
    AssetDetailDTO,
    VenuePositionDTO,
    add_trade,
    get_asset_detail,
)

# ── Shared timestamps ──────────────────────────────────────────────────────────

_TS1 = datetime(2026, 1, 10, 12, 0, 0)
_TS2 = datetime(2026, 1, 11, 12, 0, 0)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Path to a fresh temp SQLite DB."""
    return str(tmp_path / "test_detail.db")


@pytest.fixture
def two_venue_db(db):
    """DB with two BTC BUYs — one on kraken, one on trezor.

    kraken: 0.5 BTC for 25 000 CZK  → WAC = 50 000
    trezor: 0.3 BTC for 18 000 CZK  → WAC = 60 000
    aggregated: 0.8 BTC, cost = 43 000 → WAC = 53 750
    """
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS1,
        asset="BTC", amount=Decimal("0.5"),
        currency="CZK", price=Decimal("50000"),
        venue="kraken",
    ), db)
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS2,
        asset="BTC", amount=Decimal("0.3"),
        currency="CZK", price=Decimal("60000"),
        venue="trezor",
    ), db)
    return db


# ── Helper ─────────────────────────────────────────────────────────────────────

def _store_timeline(db_path: str):
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


def _store_timeline_filtered(db_path: str, asset: str):
    store = LedgerStore(db_path)
    try:
        return store.timeline_filtered(asset=asset.upper())
    finally:
        store.close()


# ── 1) Aggregated quantity matches compute_positions on all rows ───────────────

def test_aggregated_quantity(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    assert detail.position.quantity == Decimal("0.8")


def test_aggregated_cost_basis(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    assert detail.position.cost_basis == Decimal("43000")


def test_aggregated_wac(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    expected = Decimal("43000") / Decimal("0.8")
    assert detail.position.wac == expected


def test_aggregated_matches_compute_positions(two_venue_db):
    """Aggregated PositionDTO must equal compute_positions on the full ledger."""
    all_rows = _store_timeline(two_venue_db)
    pos_list = compute_positions(all_rows)
    expected = next(p for p in pos_list if p.asset == "BTC")

    detail = get_asset_detail(two_venue_db, "BTC")
    assert detail.position.quantity   == expected.quantity
    assert detail.position.wac        == expected.wac
    assert detail.position.cost_basis == expected.cost_basis


# ── 2) Venue breakdown count and names ────────────────────────────────────────

def test_venue_count(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    assert len(detail.venue_positions) == 2


def test_venue_names_sorted(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    venues = [vp.venue for vp in detail.venue_positions]
    assert venues == sorted(venues)


def test_venue_names_correct(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    venues = {vp.venue for vp in detail.venue_positions}
    assert venues == {"kraken", "trezor"}


# ── 3) Per-venue quantities and WAC ──────────────────────────────────────────

def test_kraken_quantity(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    kraken = next(vp for vp in detail.venue_positions if vp.venue == "kraken")
    assert kraken.quantity == Decimal("0.5")


def test_trezor_quantity(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    trezor = next(vp for vp in detail.venue_positions if vp.venue == "trezor")
    assert trezor.quantity == Decimal("0.3")


def test_kraken_wac(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    kraken = next(vp for vp in detail.venue_positions if vp.venue == "kraken")
    assert kraken.wac == Decimal("50000")


def test_trezor_wac(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    trezor = next(vp for vp in detail.venue_positions if vp.venue == "trezor")
    assert trezor.wac == Decimal("60000")


# ── 4) WAC per venue matches compute_positions on venue-filtered rows ─────────

def test_kraken_wac_matches_compute_positions(two_venue_db):
    all_rows = _store_timeline(two_venue_db)
    kraken_rows = [r for r in all_rows if r.venue == "kraken"]
    expected = next(
        p for p in compute_positions(kraken_rows) if p.asset == "BTC"
    )
    detail = get_asset_detail(two_venue_db, "BTC")
    kraken = next(vp for vp in detail.venue_positions if vp.venue == "kraken")
    assert kraken.wac        == expected.wac
    assert kraken.quantity   == expected.quantity
    assert kraken.cost_basis == expected.cost_basis


def test_trezor_wac_matches_compute_positions(two_venue_db):
    all_rows = _store_timeline(two_venue_db)
    trezor_rows = [r for r in all_rows if r.venue == "trezor"]
    expected = next(
        p for p in compute_positions(trezor_rows) if p.asset == "BTC"
    )
    detail = get_asset_detail(two_venue_db, "BTC")
    trezor = next(vp for vp in detail.venue_positions if vp.venue == "trezor")
    assert trezor.wac        == expected.wac
    assert trezor.quantity   == expected.quantity
    assert trezor.cost_basis == expected.cost_basis


# ── 5) rows field matches timeline_filtered ───────────────────────────────────

def test_rows_count(two_venue_db):
    expected_rows = _store_timeline_filtered(two_venue_db, "BTC")
    detail = get_asset_detail(two_venue_db, "BTC")
    assert len(detail.rows) == len(expected_rows)


def test_rows_all_asset_btc(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC")
    assert all(r.asset == "BTC" for r in detail.rows)


def test_rows_match_timeline_filtered(two_venue_db):
    expected = _store_timeline_filtered(two_venue_db, "BTC")
    detail = get_asset_detail(two_venue_db, "BTC")
    # Compare by (timestamp, type, asset, amount, venue) to avoid id noise
    def _key(r):
        return (r.timestamp, r.type, r.asset, r.amount, r.venue)
    assert sorted(detail.rows, key=_key) == sorted(expected, key=_key)


# ── 6) Unknown asset → zero-filled DTO, no exception ─────────────────────────

def test_unknown_asset_no_exception(db):
    detail = get_asset_detail(db, "UNKNOWN")
    assert isinstance(detail, AssetDetailDTO)


def test_unknown_asset_zero_quantity(db):
    detail = get_asset_detail(db, "UNKNOWN")
    assert detail.position.quantity == Decimal("0")


def test_unknown_asset_zero_cost_basis(db):
    detail = get_asset_detail(db, "UNKNOWN")
    assert detail.position.cost_basis == Decimal("0")


def test_unknown_asset_empty_venue_positions(db):
    detail = get_asset_detail(db, "UNKNOWN")
    assert detail.venue_positions == []


def test_unknown_asset_empty_rows(db):
    detail = get_asset_detail(db, "UNKNOWN")
    assert detail.rows == []


# ── 7) Case normalization: "btc" == "BTC" ────────────────────────────────────

def test_lowercase_asset_quantity(two_venue_db):
    detail_upper = get_asset_detail(two_venue_db, "BTC")
    detail_lower = get_asset_detail(two_venue_db, "btc")
    assert detail_lower.position.quantity == detail_upper.position.quantity


def test_lowercase_asset_wac(two_venue_db):
    detail_upper = get_asset_detail(two_venue_db, "BTC")
    detail_lower = get_asset_detail(two_venue_db, "btc")
    assert detail_lower.position.wac == detail_upper.position.wac


def test_lowercase_asset_venue_count(two_venue_db):
    detail_upper = get_asset_detail(two_venue_db, "BTC")
    detail_lower = get_asset_detail(two_venue_db, "btc")
    assert len(detail_lower.venue_positions) == len(detail_upper.venue_positions)


# ── 8) Price enrichment via stub price_provider ───────────────────────────────

class _FakePriceProvider:
    """Minimal stub: returns a fixed spot price for BTC."""

    def __init__(self, btc_price: Decimal):
        self._price = btc_price

    def get_prices(self, assets: List[str], fiat: str) -> dict:
        return {a: self._price for a in assets if a == "BTC"}


def test_price_enrichment_spot_price(two_venue_db):
    provider = _FakePriceProvider(Decimal("55000"))
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=provider, fiat="CZK")
    assert detail.position.spot_price == Decimal("55000")


def test_price_enrichment_value(two_venue_db):
    provider = _FakePriceProvider(Decimal("55000"))
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=provider, fiat="CZK")
    expected_value = Decimal("0.8") * Decimal("55000")
    assert detail.position.value == expected_value


def test_price_enrichment_unrealized_pnl(two_venue_db):
    provider = _FakePriceProvider(Decimal("55000"))
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=provider, fiat="CZK")
    expected = Decimal("0.8") * Decimal("55000") - Decimal("43000")
    assert detail.position.unrealized_pnl == expected


def test_price_enrichment_roi_total(two_venue_db):
    provider = _FakePriceProvider(Decimal("55000"))
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=provider, fiat="CZK")
    cost = Decimal("43000")
    value = Decimal("0.8") * Decimal("55000")
    expected_roi = (value - cost) / cost
    assert detail.position.roi_total == expected_roi


def test_price_enrichment_venue_spot_price(two_venue_db):
    provider = _FakePriceProvider(Decimal("55000"))
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=provider, fiat="CZK")
    for vp in detail.venue_positions:
        assert vp.spot_price == Decimal("55000")


def test_price_enrichment_venue_value(two_venue_db):
    provider = _FakePriceProvider(Decimal("55000"))
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=provider, fiat="CZK")
    kraken = next(vp for vp in detail.venue_positions if vp.venue == "kraken")
    assert kraken.value == Decimal("0.5") * Decimal("55000")


def test_no_price_provider_fields_are_none(two_venue_db):
    detail = get_asset_detail(two_venue_db, "BTC", price_provider=None)
    assert detail.position.spot_price    is None
    assert detail.position.value         is None
    assert detail.position.unrealized_pnl is None
    assert detail.position.roi_total     is None
