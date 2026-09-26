"""Phase 2 — get_asset_proceeds() facade function + HYPE gross/net UI fixture.

Verifies:
  - neutral field naming (quote_amount / fee / cash_impact) — never "gross"/
    "net" — matching the Phase 1 decision to not assume Revolut semantics.
  - the real HYPE scenario rendered through BOTH interpretations without
    the UI/facade layer asserting which one is correct.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.services.ui_facade import AddTradeRequestDTO, add_trade, get_asset_proceeds

_TS_BUY = datetime(2026, 8, 1, 10, 0, 0)
_TS_SELL = datetime(2026, 9, 10, 9, 31, 0)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "test.db")


def _seed_buy(db_path):
    add_trade(AddTradeRequestDTO(
        type="BUY", timestamp=_TS_BUY, asset="HYPE", amount=Decimal("8"),
        currency="CZK", price=Decimal("1200"), venue="revolut",
        account="Osobní CZK",
    ), db_path)


def test_proceeds_row_shape_is_neutral(db):
    _seed_buy(db)
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", price=Decimal("1709.625"),  # -> quote_amount 3419.25
        venue="revolut", fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)

    proceeds = get_asset_proceeds(db, "HYPE")
    assert len(proceeds) == 1
    row = proceeds[0]
    # Field names must be neutral — this assertion documents the contract.
    assert hasattr(row, "quote_amount")
    assert hasattr(row, "fee")
    assert hasattr(row, "cash_impact")
    assert not hasattr(row, "gross_proceeds")
    assert not hasattr(row, "net_proceeds")
    assert row.cash_account == "Osobní CZK"
    assert row.quantity_sold == Decimal("2")


def test_hype_fixture_interpretation_A_net_reported_amount(db):
    """3 344,25 CZK read as NET -> quote_amount grossed up to 3 419,25."""
    _seed_buy(db)
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", quote_amount=Decimal("3419.25"), price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)

    row = get_asset_proceeds(db, "HYPE")[0]
    assert row.quote_amount == Decimal("3419.25")
    assert row.fee == Decimal("75")
    assert row.cash_impact == Decimal("3344.25")  # matches the reported figure


def test_hype_fixture_interpretation_B_gross_reported_amount(db):
    """3 344,25 CZK read as GROSS -> quote_amount used as-is."""
    _seed_buy(db)
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", quote_amount=Decimal("3344.25"), price=Decimal("1"),
        venue="revolut", fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    ), db)

    row = get_asset_proceeds(db, "HYPE")[0]
    assert row.quote_amount == Decimal("3344.25")
    assert row.fee == Decimal("75")
    assert row.cash_impact == Decimal("3269.25")  # less than the reported figure


def test_proceeds_with_no_fee_has_fee_none_not_zero(db):
    _seed_buy(db)
    add_trade(AddTradeRequestDTO(
        type="SELL", timestamp=_TS_SELL, asset="HYPE", amount=Decimal("2"),
        currency="CZK", quote_amount=Decimal("3344.25"), price=Decimal("1"),
        venue="revolut", account="Osobní CZK",
    ), db)

    row = get_asset_proceeds(db, "HYPE")[0]
    assert row.fee is None
    assert row.cash_impact == row.quote_amount
