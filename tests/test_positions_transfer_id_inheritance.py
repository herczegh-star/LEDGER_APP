"""A positive leg (BUY / STAKING / REVERSAL) that merely shares a trade id with a
TRANSFER must not inherit that transfer's cost_transfer seed as if it were an
asset->asset swap-in.  Legitimate asset->asset swap inheritance must still work.
"""
from datetime import datetime
from decimal import Decimal
from typing import List

from core.model import RawRow
from core.reports.positions import compute_positions, compute_transfer_costs


def _r(day, type_, asset, amount, venue, id_, currency=None, price="0"):
    return RawRow(
        id=id_,
        timestamp=datetime(2026, 3, day, 12, 0, 0),
        type=type_,
        asset=asset,
        amount=Decimal(str(amount)),
        currency=currency or asset,
        price=Decimal(str(price)),
        venue=venue,
    )


def _buy(day, asset, qty, czk, venue, id_):
    return [
        _r(day, "BUY", asset, qty, venue, id_, currency="CZK",
           price=str(Decimal(str(czk)) / Decimal(str(qty)))),
        _r(day, "BUY", "CZK", f"-{czk}", venue, id_, currency="CZK", price="1"),
    ]


def _pos(rows, asset, ct=None):
    it = compute_positions(rows, initial_cost_transfer=ct) if ct is not None else compute_positions(rows)
    for p in it:
        if p.asset == asset:
            return p
    return None


# ── Reproduces the kraken USDT phantom ─────────────────────────────────────
def test_reversal_sharing_transfer_id_does_not_inherit_transfer_cost():
    TID = "20260325_170034_KRAKEN_TRANSFER_001"
    rows: List[RawRow] = []
    rows += _buy(1, "USDC", "100", "1670", venue="kraken", id_="usdc_buy")
    rows += [
        # 100 USDC moved kraken -> revolut  (outflow seeds cost_transfer[TID])
        _r(25, "TRANSFER", "USDC", "-100", "kraken", TID, price="1"),
        _r(25, "TRANSFER", "USDC", "100", "revolut", TID, price="1"),
        # withdrawal fee mis-booked in USDT, then corrected
        _r(25, "FEE", "USDT", "-0.5902", "kraken", TID, price="1"),
        _r(25, "REVERSAL", "USDT", "0.5902", "kraken", TID, price="1"),
        _r(25, "FEE", "USDC", "-0.5902", "kraken", TID, price="1"),
    ]

    # global
    g = _pos(rows, "USDT")
    assert g is not None and g.quantity == Decimal("0")
    assert g.cost_basis == Decimal("0")
    assert g.wac == Decimal("0")

    # venue-local (kraken) — the branch where the bug lived
    tc = compute_transfer_costs(rows)
    kraken_rows = [r for r in rows if r.venue == "kraken"]
    v = _pos(kraken_rows, "USDT", ct=tc)
    assert v is not None and v.quantity == Decimal("0")
    assert v.cost_basis == Decimal("0")          # was ~1670 before the fix
    assert v.wac == Decimal("0")


# ── Legitimate asset->asset swap inheritance still works ───────────────────
def test_asset_to_asset_swap_still_inherits_cost_basis():
    rows: List[RawRow] = []
    rows += _buy(1, "TAO", "1", "5000", venue="kraken", id_="tao_buy")   # TAO wac 5000
    SWAP = "20260310_000000_KRAKEN_SWAP_001"
    rows += [
        _r(10, "SELL", "TAO", "-0.32", "kraken", SWAP, currency="USDC"),
        _r(10, "BUY", "USDC", "112.1439", "kraken", SWAP, currency="USDC"),
    ]
    # global: USDC inherits TAO cost = 5000 * 0.32 = 1600
    u = _pos(rows, "USDC")
    assert u.quantity == Decimal("112.1439")
    assert u.cost_basis == Decimal("1600")

    # venue-local: same inheritance via the cost_transfer seed
    tc = compute_transfer_costs(rows)
    kr = [r for r in rows if r.venue == "kraken"]
    uv = _pos(kr, "USDC", ct=tc)
    assert uv.quantity == Decimal("112.1439")
    assert uv.cost_basis == Decimal("1600")


# ── Legitimate cross-venue TRANSFER cost flow still works ─────────────────
def test_transfer_inflow_still_carries_cost_between_venues():
    rows: List[RawRow] = []
    rows += _buy(1, "ETH", "2", "160000", venue="anycoin", id_="eth_buy")  # wac 80000
    TR = "20260305_000000_ANYCOIN_TRANSFER_001"
    rows += [
        _r(5, "TRANSFER", "ETH", "-2", "anycoin", TR, price="1"),
        _r(5, "TRANSFER", "ETH", "2", "trust wallet", TR, price="1"),
    ]
    tc = compute_transfer_costs(rows)
    tw = [r for r in rows if r.venue == "trust wallet"]
    p = _pos(tw, "ETH", ct=tc)
    assert p.quantity == Decimal("2")
    assert p.cost_basis == Decimal("160000")     # full cost carried across
    assert p.wac == Decimal("80000")
