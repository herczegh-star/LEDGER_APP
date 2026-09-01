"""Negative non-fiat FEE support in the WAC positions engine.

Rule (compute_positions + compute_transfer_costs):
  type == FEE, amount < 0, asset not in fiat  ->
    quantity   -= abs(amount)
    cost_basis -= current_WAC * abs(amount)
    realized_pnl unchanged; no cost_transfer created; not a SELL/REVERSAL.
Fiat (EUR/CZK) FEE keeps its existing fee_by_id treatment.
"""
from datetime import datetime
from decimal import Decimal
from typing import List

from core.model import RawRow
from core.reports.positions import (
    compute_positions,
    compute_transfer_costs,
)


def _r(ts_day, type_, asset, amount, currency, venue="anycoin", id_="t", price="0"):
    return RawRow(
        id=id_,
        timestamp=datetime(2026, 1, ts_day, 12, 0, 0),
        type=type_,
        asset=asset,
        amount=Decimal(str(amount)),
        currency=currency,
        price=Decimal(str(price)),
        venue=venue,
    )


def _buy(day, asset, qty, czk, venue="anycoin", id_=None):
    id_ = id_ or f"buy_{asset}_{day}"
    return [
        _r(day, "BUY", asset, qty, "CZK", venue, id_, price=str(Decimal(str(czk)) / Decimal(str(qty)))),
        _r(day, "BUY", "CZK", f"-{czk}", "CZK", venue, id_, price="1"),
    ]


def _pos(rows: List[RawRow], asset: str):
    for p in compute_positions(rows):
        if p.asset == asset:
            return p
    return None


# ── 1. crypto FEE reduces qty and cost basis at WAC ─────────────────────────
def test_crypto_fee_reduces_qty_and_cost_at_wac():
    rows = _buy(1, "WOO", "10", "1000")            # wac = 100
    rows.append(_r(2, "FEE", "WOO", "-1", "WOO", id_="fee1"))
    p = _pos(rows, "WOO")
    assert p.quantity == Decimal("9")
    assert p.cost_basis == Decimal("900")
    assert p.wac == Decimal("100")
    assert p.realized_pnl == Decimal("0")


# ── 2. WAC unchanged when no activity after the fee ─────────────────────────
def test_crypto_fee_wac_unchanged_when_no_later_activity():
    base = _buy(1, "WOO", "10", "1234.5678")
    wac_before = _pos(base, "WOO").wac
    with_fee = base + [_r(5, "FEE", "WOO", "-0.37", "WOO", id_="fee1")]
    p = _pos(with_fee, "WOO")
    assert p.wac == wac_before
    assert p.quantity == Decimal("10") - Decimal("0.37")


# ── 3. fee before a later BUY correctly changes blended WAC ─────────────────
def test_crypto_fee_before_later_buy_changes_blended_wac():
    rows = _buy(1, "X", "1", "100")                       # wac 100
    rows.append(_r(2, "FEE", "X", "-0.1", "X", id_="fee1"))  # remove 0.1 @ 100 -> -10
    rows += _buy(3, "X", "1", "200", id_="buy_X_3")       # +1 @ 200
    p = _pos(rows, "X")
    assert p.quantity == Decimal("1.9")
    assert p.cost_basis == Decimal("290")                 # 100 - 10 + 200
    assert p.wac == (Decimal("290") / Decimal("1.9"))


# ── 4. fiat FEE unchanged (still folded into the linked trade) ──────────────
def test_fiat_fee_unchanged_folds_into_trade_cost():
    rows = [
        _r(1, "BUY", "FET", "100", "CZK", id_="k1", price="10"),
        _r(1, "BUY", "CZK", "-1000", "CZK", id_="k1", price="1"),
        _r(1, "FEE", "CZK", "-24.29", "CZK", id_="k1", price="1"),
    ]
    p = _pos(rows, "FET")
    assert p.quantity == Decimal("100")
    assert p.cost_basis == Decimal("1024.29")             # 1000 + 24.29 fiat fee
    # CZK is fiat -> never a position
    assert _pos(rows, "CZK") is None


# ── 5. FEE with no prior position -> truthful negative quantity ─────────────
def test_fee_without_prior_position_creates_negative_qty():
    rows = [_r(1, "FEE", "USDT", "-0.5902", "USDT", venue="kraken", id_="feeu")]
    p = _pos(rows, "USDT")
    assert p is not None
    assert p.quantity == Decimal("-0.5902")
    assert p.cost_basis == Decimal("0")
    assert p.realized_pnl == Decimal("0")


# ── 6. realized PnL is never touched by a fee ──────────────────────────────
def test_fee_does_not_change_realized_pnl():
    rows = _buy(1, "X", "10", "1000")                     # wac 100
    rows += [
        _r(2, "SELL", "X", "-4", "CZK", id_="s1", price="150"),
        _r(2, "SELL", "CZK", "600", "CZK", id_="s1", price="1"),
    ]
    realized_after_sell = _pos(rows, "X").realized_pnl
    rows.append(_r(3, "FEE", "X", "-0.5", "X", id_="fee1"))
    assert _pos(rows, "X").realized_pnl == realized_after_sell


# ── 7. Health oversell surfaces for a bare fee with no position ────────────
def test_health_reports_oversell_for_bare_fee():
    from core.services.health_service import health_report
    rows = [_r(1, "FEE", "USDT", "-0.5902", "USDT", venue="kraken", id_="feeu")]
    kinds = {row.values["kind"] for row in health_report(rows).rows}
    assert "oversell" in kinds


# ── 8. global and venue-local FEE logic are consistent ────────────────────
def test_global_and_venue_local_fee_consistent():
    rows = _buy(1, "WOO", "10", "1000", venue="anycoin")
    rows.append(_r(2, "FEE", "WOO", "-1", "WOO", venue="anycoin", id_="fee1"))
    g = _pos(rows, "WOO")
    tc = compute_transfer_costs(rows)
    vl = [p for p in compute_positions(rows, initial_cost_transfer=tc) if p.asset == "WOO"][0]
    assert vl.quantity == g.quantity == Decimal("9")
    assert vl.cost_basis == g.cost_basis == Decimal("900")
    assert vl.wac == g.wac == Decimal("100")


# ── 9. integration: WOO withdrawal fee + transfer to Ledger wallet ────────
def test_woo_withdrawal_fee_plus_transfer_integration():
    from core.reports.holdings import compute_venue_holdings

    rows: List[RawRow] = []
    # 10 historical WOO buys collapsed into one for the unit test
    rows += _buy(1, "WOO", "60536.27682432", "112165.9999971750445", venue="anycoin", id_="woo_buy")
    base = _pos(rows, "WOO")
    assert base.quantity == Decimal("60536.27682432")

    tid = "20260901_000000_ANYCOIN_TRANSFER_001"
    rows += [
        _r(20, "FEE", "WOO", "-35", "WOO", venue="anycoin", id_="20260901_000000_ANYCOIN_WOO_FEE_001"),
        _r(20, "TRANSFER", "WOO", "-60501.27682432", "WOO", venue="anycoin", id_=tid),
        _r(20, "TRANSFER", "WOO", "60501.27682432", "WOO", venue="ledger wallet", id_=tid),
    ]
    p = _pos(rows, "WOO")
    assert p.quantity == Decimal("60501.27682432")
    # cost basis reduced by exactly 35 * baseline WAC
    assert abs(p.cost_basis - Decimal("112101.14946116844355354372")) < Decimal("1e-6")
    assert abs(p.wac - Decimal("1.8528724573314556")) < Decimal("1e-12")
    assert p.realized_pnl == Decimal("0")

    h = compute_venue_holdings(rows)
    assert h.get("anycoin", {}).get("WOO") is None            # 0 -> pruned
    assert h["ledger wallet"]["WOO"] == Decimal("60501.27682432")
