"""Model C: USDC/USDT as asset (not fiat) — implementation tests.

Coverage:
  1.  BUY USDC for EUR → accepted, USDC position created
  2.  BUY USDC for CZK → accepted, USDC position + correct cost basis
  3.  BUY HYPE for USDC via fiat-buy path → rejected (USDC not fiat)
  4.  USDC→HYPE SWAP → cost basis inherited
  5.  HYPE→USDC SWAP → cost basis inherited (transitively)
  6.  USDC→CZK SELL → realized P&L correct
  7.  Full EUR→USDC→HYPE→USDC→CZK chain → P&L = 2000 CZK, no zero cost basis
  8.  Legacy rows (currency=USDC in BUY/SELL) → stablecoin_quote_legacy WARNING
  9.  No false warning for clean Model-C USDC BUY (currency=CZK) or SWAP rows
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.reports.positions import compute_positions
from core.services.health_service import WARNING, health_report
from core.services.ui_facade import AddTradeRequestDTO, add_trade

_TS = datetime(2026, 5, 1, 12, 0, 0)


# ── Fixtures & helpers ────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "model_c.db")


def _rows_from_db(db_path: str) -> List[RawRow]:
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


def _req(**overrides) -> AddTradeRequestDTO:
    defaults = dict(
        type="BUY",
        timestamp=_TS,
        asset="USDC",
        amount=Decimal("1000"),
        currency="CZK",
        price=Decimal("23"),
        venue="kraken",
    )
    defaults.update(overrides)
    return AddTradeRequestDTO(**defaults)


def _make_buy(tid, asset, amount, quote_currency, quote_amount, ts=1):
    """Two-row BUY trade: base row + fiat quote row."""
    dt = datetime(2026, 5, ts, 12, 0, 0)
    return [
        RawRow(id=tid, timestamp=dt, type="BUY", asset=asset,
               amount=Decimal(str(amount)), currency=quote_currency,
               price=Decimal(str(quote_amount)) / Decimal(str(amount)), venue="kraken"),
        RawRow(id=tid, timestamp=dt, type="BUY", asset=quote_currency,
               amount=-Decimal(str(quote_amount)), currency=quote_currency,
               price=Decimal("1"), venue="kraken"),
    ]


def _make_swap(tid, from_asset, from_amt, to_asset, to_amt, ts=2):
    """SWAP decomposition: SELL from_asset + BUY to_asset, no fiat quote."""
    dt = datetime(2026, 5, ts, 12, 0, 0)
    return [
        RawRow(id=tid, timestamp=dt, type="SELL", asset=from_asset,
               amount=-Decimal(str(from_amt)), currency=from_asset,
               price=Decimal("1"), venue="kraken"),
        RawRow(id=tid, timestamp=dt, type="BUY", asset=to_asset,
               amount=Decimal(str(to_amt)), currency=to_asset,
               price=Decimal("1"), venue="kraken"),
    ]


def _make_sell(tid, asset, amount, quote_currency, quote_amount, ts=3):
    """Two-row SELL trade: base row + fiat quote row."""
    dt = datetime(2026, 5, ts, 12, 0, 0)
    return [
        RawRow(id=tid, timestamp=dt, type="SELL", asset=asset,
               amount=-Decimal(str(amount)), currency=quote_currency,
               price=Decimal(str(quote_amount)) / Decimal(str(amount)), venue="kraken"),
        RawRow(id=tid, timestamp=dt, type="SELL", asset=quote_currency,
               amount=Decimal(str(quote_amount)), currency=quote_currency,
               price=Decimal("1"), venue="kraken"),
    ]


# ── 1. BUY USDC for EUR → accepted ───────────────────────────────────────────

def test_buy_usdc_for_eur_accepted(db):
    # conftest autouse fixture patches _cnb → EUR→CZK normalization works in tests
    result = add_trade(_req(currency="EUR", price=Decimal("23")), db)
    assert result.success, result.error_message


def test_buy_usdc_for_eur_usdc_row_exists(db):
    add_trade(_req(currency="EUR", price=Decimal("23")), db)
    rows = _rows_from_db(db)
    assert any(r.asset == "USDC" and r.amount > 0 for r in rows)


def test_buy_usdc_for_eur_no_eur_in_db(db):
    add_trade(_req(currency="EUR", price=Decimal("23")), db)
    assets = {r.asset for r in _rows_from_db(db)}
    assert "EUR" not in assets


# ── 2. BUY USDC for CZK → accepted ───────────────────────────────────────────

def test_buy_usdc_for_czk_accepted(db):
    result = add_trade(_req(currency="CZK", price=Decimal("23")), db)
    assert result.success, result.error_message


def test_buy_usdc_for_czk_usdc_appears_as_position(db):
    add_trade(_req(currency="CZK", price=Decimal("23")), db)
    rows = _rows_from_db(db)
    positions = compute_positions(rows)
    assert any(p.asset == "USDC" for p in positions)


def test_buy_usdc_for_czk_cost_basis_nonzero(db):
    add_trade(_req(currency="CZK", price=Decimal("23"), amount=Decimal("1000")), db)
    rows = _rows_from_db(db)
    positions = compute_positions(rows)
    usdc = next(p for p in positions if p.asset == "USDC")
    assert usdc.cost_basis == Decimal("23000"), f"Expected 23000 CZK, got {usdc.cost_basis}"


# ── 3. BUY HYPE for USDC via fiat-buy path → rejected ────────────────────────

def test_buy_hype_for_usdc_rejected(db):
    """USDC is not fiat → USDC as quote_currency must be rejected."""
    result = add_trade(
        AddTradeRequestDTO(
            type="BUY",
            timestamp=_TS,
            asset="HYPE",
            amount=Decimal("500"),
            currency="USDC",
            price=Decimal("4"),
            venue="kraken",
        ),
        db,
    )
    assert not result.success
    assert result.error_message


def test_buy_hype_for_usdc_nothing_written(db):
    add_trade(
        AddTradeRequestDTO(
            type="BUY", timestamp=_TS, asset="HYPE",
            amount=Decimal("500"), currency="USDC",
            price=Decimal("4"), venue="kraken",
        ),
        db,
    )
    store = LedgerStore(db)
    try:
        assert store.count() == 0
    finally:
        store.close()


def test_sell_hype_for_usdc_rejected(db):
    """SELL HYPE for USDC (USDC as quote) must also be rejected."""
    result = add_trade(
        AddTradeRequestDTO(
            type="SELL",
            timestamp=_TS,
            asset="HYPE",
            amount=Decimal("500"),
            currency="USDC",
            price=Decimal("5"),
            venue="kraken",
        ),
        db,
    )
    assert not result.success


# ── 4 & 5. USDC↔HYPE SWAP → cost basis propagated via cost_transfer ──────────

def test_usdc_hype_swap_hype_position_appears():
    """After USDC→HYPE SWAP, HYPE must appear as a position."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
    )
    positions = compute_positions(rows)
    assert any(p.asset == "HYPE" for p in positions)


def test_usdc_hype_swap_cost_basis_inherited():
    """USDC→HYPE SWAP: HYPE must inherit USDC's full CZK cost basis."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
    )
    positions = compute_positions(rows)
    hype = next(p for p in positions if p.asset == "HYPE")
    assert hype.cost_basis == Decimal("23000"), f"Expected 23000, got {hype.cost_basis}"


def test_usdc_hype_swap_no_pnl_at_swap_time():
    """USDC→HYPE SWAP: P&L must be zero (deferred until final SELL for fiat)."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
    )
    positions = compute_positions(rows)
    for p in positions:
        assert p.realized_pnl == Decimal("0"), (
            f"Unexpected P&L for {p.asset}: {p.realized_pnl}"
        )


def test_hype_usdc_swap_cost_basis_propagated():
    """HYPE→USDC SWAP: USDC must inherit HYPE's cost basis (originally from USDC→HYPE)."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
        + _make_swap("t3", "HYPE", 500, "USDC", 1100, ts=3)
    )
    positions = compute_positions(rows)
    usdc = next(p for p in positions if p.asset == "USDC")
    assert usdc.cost_basis == Decimal("23000"), f"Expected 23000, got {usdc.cost_basis}"


# ── 6. USDC→CZK SELL → realized P&L in CZK ──────────────────────────────────

def test_usdc_sell_pnl_correct():
    """SELL USDC for CZK: realized P&L = proceeds − cost_basis."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_sell("t2", "USDC", 1000, "CZK", 25000, ts=2)
    )
    positions = compute_positions(rows)
    usdc = next(p for p in positions if p.asset == "USDC")
    assert usdc.realized_pnl == Decimal("2000"), f"Expected 2000, got {usdc.realized_pnl}"


def test_usdc_sell_quantity_zero():
    """After selling all USDC, quantity must be zero."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_sell("t2", "USDC", 1000, "CZK", 25000, ts=2)
    )
    positions = compute_positions(rows)
    usdc = next(p for p in positions if p.asset == "USDC")
    assert usdc.quantity == Decimal("0")


# ── 7. Full chain: EUR→USDC→HYPE→USDC→CZK ───────────────────────────────────

def test_full_chain_pnl_end_to_end():
    """Cost basis propagates: buy 1000 USDC for 23000 CZK, swap twice, sell for 25000 CZK.
    P&L = 25000 − 23000 = 2000 CZK, realized at the final SELL only."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
        + _make_swap("t3", "HYPE", 500, "USDC", 1100, ts=3)
        + _make_sell("t4", "USDC", 1100, "CZK", 25000, ts=4)
    )
    positions = compute_positions(rows)
    usdc = next(p for p in positions if p.asset == "USDC")
    assert usdc.realized_pnl == Decimal("2000"), (
        f"Expected 2000 CZK P&L, got {usdc.realized_pnl}"
    )


def test_full_chain_no_zero_cost_basis_while_holding():
    """During the chain, any asset with a positive quantity must have cost_basis > 0."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
    )
    positions = compute_positions(rows)
    for p in positions:
        if p.quantity > Decimal("0"):
            assert p.cost_basis > Decimal("0"), (
                f"{p.asset}: qty={p.quantity} but cost_basis=0"
            )


def test_full_chain_pnl_realized_only_at_final_sell():
    """P&L must be zero after the swap steps; only realized after fiat SELL."""
    rows_mid = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
        + _make_swap("t3", "HYPE", 500, "USDC", 1100, ts=3)
    )
    pos_mid = compute_positions(rows_mid)
    for p in pos_mid:
        assert p.realized_pnl == Decimal("0"), (
            f"Expected 0 P&L before final SELL, got {p.realized_pnl} for {p.asset}"
        )


# ── 8. Legacy rows diagnostic ─────────────────────────────────────────────────

def _legacy_usdc_quoted(tid):
    """Historical 'BUY BTC for USDC' trade — pre-Model-C misclassification."""
    dt = datetime(2026, 1, 1, 12, 0, 0)
    return [
        RawRow(id=tid, timestamp=dt, type="BUY", asset="BTC",
               amount=Decimal("0.5"), currency="USDC",
               price=Decimal("50000"), venue="bybit"),
        RawRow(id=tid, timestamp=dt, type="BUY", asset="USDC",
               amount=Decimal("-25000"), currency="USDC",
               price=Decimal("1"), venue="bybit"),
    ]


def test_legacy_usdc_quote_triggers_warning():
    """BUY/SELL with currency=USDC must produce stablecoin_quote_legacy WARNING."""
    rows = _legacy_usdc_quoted("legacy-t1")
    report = health_report(rows)
    kinds = [r.values["kind"] for r in report.rows]
    assert "stablecoin_quote_legacy" in kinds


def test_legacy_usdc_quote_severity_is_warning():
    rows = _legacy_usdc_quoted("legacy-t1")
    report = health_report(rows)
    sc_issues = [r.values for r in report.rows if r.values["kind"] == "stablecoin_quote_legacy"]
    assert sc_issues, "Expected stablecoin_quote_legacy issue"
    assert all(i["severity"] == WARNING for i in sc_issues)


def test_legacy_usdt_quote_triggers_warning():
    """USDT as quote currency is also a stablecoin_quote_legacy issue."""
    dt = datetime(2026, 1, 1, 12, 0, 0)
    rows = [
        RawRow(id="t-usdt", timestamp=dt, type="BUY", asset="ETH",
               amount=Decimal("2"), currency="USDT",
               price=Decimal("2000"), venue="bybit"),
        RawRow(id="t-usdt", timestamp=dt, type="BUY", asset="USDT",
               amount=Decimal("-4000"), currency="USDT",
               price=Decimal("1"), venue="bybit"),
    ]
    report = health_report(rows)
    kinds = [r.values["kind"] for r in report.rows]
    assert "stablecoin_quote_legacy" in kinds


def test_legacy_report_hint_is_nonempty():
    rows = _legacy_usdc_quoted("legacy-t2")
    report = health_report(rows)
    sc_issues = [r.values for r in report.rows if r.values["kind"] == "stablecoin_quote_legacy"]
    assert sc_issues[0]["hint"]


def test_legacy_report_trade_id_matches():
    rows = _legacy_usdc_quoted("legacy-t3")
    report = health_report(rows)
    sc_issues = [r.values for r in report.rows if r.values["kind"] == "stablecoin_quote_legacy"]
    assert sc_issues[0]["trade_id"] == "legacy-t3"


# ── 9. No false warnings for clean Model-C usage ─────────────────────────────

def test_buy_usdc_czk_no_stablecoin_warning():
    """BUY USDC with currency=CZK: no stablecoin_quote_legacy warning."""
    rows = _make_buy("t-clean", "USDC", 1000, "CZK", 23000, ts=1)
    report = health_report(rows)
    kinds = [r.values["kind"] for r in report.rows]
    assert "stablecoin_quote_legacy" not in kinds


def test_swap_usdc_hype_no_stablecoin_warning():
    """USDC→HYPE SWAP rows (currency=USDC on USDC's own row): no false warning."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_swap("t2", "USDC", 1000, "HYPE", 500, ts=2)
    )
    report = health_report(rows)
    kinds = [r.values["kind"] for r in report.rows]
    assert "stablecoin_quote_legacy" not in kinds


def test_usdc_sell_czk_no_stablecoin_warning():
    """SELL USDC for CZK: no stablecoin_quote_legacy warning."""
    rows = (
        _make_buy("t1", "USDC", 1000, "CZK", 23000, ts=1)
        + _make_sell("t2", "USDC", 1000, "CZK", 25000, ts=2)
    )
    report = health_report(rows)
    kinds = [r.values["kind"] for r in report.rows]
    assert "stablecoin_quote_legacy" not in kinds
