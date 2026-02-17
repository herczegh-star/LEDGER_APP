#!/usr/bin/env python3
"""Testy WAC positions engine (5 specifikovaných + 2 doplňkové)."""
import sys
import os
from datetime import datetime
from decimal import Decimal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.model import RawRow
from ledger_engine.fx_provider import DictFxProvider
from ledger_engine.positions_engine import compute_positions


# FX provider bez EUR kurzů – pro CZK-only testy
CZK_FX = DictFxProvider({})

# FX provider s jedním EUR kurzem
EUR_FX = DictFxProvider({"2026-01-01": Decimal("25")})


def make_row(
    type_: str,
    asset: str,
    amount: str,
    currency: str,
    price: str,
    ts: str = "2026-01-01T12:00:00",
    venue: str = "test",
) -> RawRow:
    return RawRow(
        timestamp=datetime.fromisoformat(ts),
        type=type_,
        asset=asset,
        amount=Decimal(amount),
        currency=currency,
        price=Decimal(price),
        venue=venue,
    )


# ── 5 specifikovaných testů ───────────────────────────────


def test_buy_single():
    """TEST 1: BUY 1 BTC @ 50 000 CZK → qty=1, cost_basis=50000."""
    rows = [make_row("BUY", "BTC", "1", "CZK", "50000")]
    s = compute_positions(rows, CZK_FX)["BTC"]
    assert s.qty == Decimal("1"), f"qty: {s.qty}"
    assert s.cost_basis_czk == Decimal("50000"), f"cost_basis: {s.cost_basis_czk}"
    print(f"  qty={s.qty}, cost_basis={s.cost_basis_czk}")


def test_buy_wac():
    """TEST 2: BUY 1 BTC @ 50k + BUY 1 BTC @ 70k → qty=2, avg=60000."""
    rows = [
        make_row("BUY", "BTC", "1", "CZK", "50000", "2026-01-01T12:00:00"),
        make_row("BUY", "BTC", "1", "CZK", "70000", "2026-01-02T12:00:00"),
    ]
    s = compute_positions(rows, CZK_FX)["BTC"]
    assert s.qty == Decimal("2"), f"qty: {s.qty}"
    assert s.cost_basis_czk == Decimal("120000"), f"cost_basis: {s.cost_basis_czk}"
    assert s.avg_buy_czk == Decimal("60000"), f"avg_buy: {s.avg_buy_czk}"
    print(f"  qty={s.qty}, cost_basis={s.cost_basis_czk}, avg_buy={s.avg_buy_czk}")


def test_sell_realized_pnl():
    """TEST 3: Po dvou nákupech SELL 1 BTC @ 80k → qty=1, cost_basis=60k, realized=20k."""
    rows = [
        make_row("BUY", "BTC", "1", "CZK", "50000", "2026-01-01T12:00:00"),
        make_row("BUY", "BTC", "1", "CZK", "70000", "2026-01-02T12:00:00"),
        make_row("SELL", "BTC", "-1", "CZK", "80000", "2026-01-03T12:00:00"),
    ]
    s = compute_positions(rows, CZK_FX)["BTC"]
    assert s.qty == Decimal("1"), f"qty: {s.qty}"
    assert s.cost_basis_czk == Decimal("60000"), f"cost_basis: {s.cost_basis_czk}"
    assert s.realized_pnl_czk == Decimal("20000"), f"realized_pnl: {s.realized_pnl_czk}"
    print(f"  qty={s.qty}, cost_basis={s.cost_basis_czk}, realized_pnl={s.realized_pnl_czk}")


def test_buy_eur_fx():
    """TEST 4: BUY 1 BTC @ 1000 EUR, FX=25 → cost_basis=25000 CZK."""
    rows = [make_row("BUY", "BTC", "1", "EUR", "1000")]
    s = compute_positions(rows, EUR_FX)["BTC"]
    assert s.cost_basis_czk == Decimal("25000"), f"cost_basis: {s.cost_basis_czk}"
    print(f"  cost_basis={s.cost_basis_czk} CZK (1000 EUR × 25)")


def test_reversal():
    """TEST 5: BUY 1 BTC pak REVERSAL -1 BTC → qty=0, cost_basis=0."""
    rows = [
        make_row("BUY",      "BTC", "1",  "CZK", "50000", "2026-01-01T12:00:00"),
        make_row("REVERSAL", "BTC", "-1", "CZK", "50000", "2026-01-02T12:00:00"),
    ]
    s = compute_positions(rows, CZK_FX)["BTC"]
    assert s.qty == Decimal("0"), f"qty: {s.qty}"
    assert s.cost_basis_czk == Decimal("0"), f"cost_basis: {s.cost_basis_czk}"
    print(f"  qty={s.qty}, cost_basis={s.cost_basis_czk}")


# ── Doplňkové testy ───────────────────────────────────────


def test_fiat_rows_ignored():
    """TEST 6: EUR a CZK řádky (double-entry protistrana) se ignorují."""
    rows = [
        make_row("BUY", "BTC", "1",      "CZK", "50000"),
        make_row("BUY", "EUR", "-50000", "CZK", "1"),   # protistrana, ignorovat
    ]
    result = compute_positions(rows, CZK_FX)
    assert "EUR" not in result, "EUR nesmí být v positions"
    assert result["BTC"].qty == Decimal("1")
    print(f"  EUR ignorováno, BTC qty={result['BTC'].qty}")


def test_price_provider():
    """TEST 7: Volitelný price_provider dopočítá unrealized P&L a ROI."""
    rows = [make_row("BUY", "BTC", "1", "CZK", "50000")]
    result = compute_positions(
        rows, CZK_FX, price_provider=lambda _: Decimal("75000")
    )
    s = result["BTC"]
    assert s.unrealized_pnl_czk == Decimal("25000"), f"unrealized: {s.unrealized_pnl_czk}"
    assert s.roi == Decimal("0.5"), f"roi: {s.roi}"
    print(f"  unrealized={s.unrealized_pnl_czk}, roi={s.roi}")


def test_price_provider_none_price():
    """TEST 8: price_provider vrátí None → ValueError."""
    rows = [make_row("BUY", "BTC", "1", "CZK", "50000")]
    try:
        compute_positions(rows, CZK_FX, price_provider=lambda _: None)
        assert False, "Mělo vyhodit ValueError"
    except ValueError as e:
        assert "BTC" in str(e)
        print(f"  ValueError raised: {e}")


def test_no_price_provider():
    """TEST 9: Bez price_provider → unrealized=None, roi=None."""
    rows = [make_row("BUY", "BTC", "1", "CZK", "50000")]
    s = compute_positions(rows, CZK_FX)["BTC"]
    assert s.unrealized_pnl_czk is None
    assert s.roi is None
    print(f"  unrealized={s.unrealized_pnl_czk}, roi={s.roi}")


def test_zero_qty_after_sell():
    """TEST 10: Po plném prodeji qty=0 → avg_buy=0, roi=None."""
    rows = [
        make_row("BUY",  "BTC", "1",  "CZK", "50000", "2026-01-01T12:00:00"),
        make_row("SELL", "BTC", "-1", "CZK", "50000", "2026-01-02T12:00:00"),
    ]
    s = compute_positions(rows, CZK_FX)["BTC"]
    assert s.qty == Decimal("0")
    assert s.avg_buy_czk == Decimal("0")
    assert s.roi is None
    print(f"  qty={s.qty}, avg_buy={s.avg_buy_czk}, roi={s.roi}")


# ── Runner ────────────────────────────────────────────────


def main():
    tests = [
        ("TEST 1: BUY single",              test_buy_single),
        ("TEST 2: WAC two buys",            test_buy_wac),
        ("TEST 3: SELL realized P&L",       test_sell_realized_pnl),
        ("TEST 4: BUY EUR + FX",            test_buy_eur_fx),
        ("TEST 5: REVERSAL",                test_reversal),
        ("TEST 6: Fiat rows ignored",       test_fiat_rows_ignored),
        ("TEST 7: Price provider",          test_price_provider),
        ("TEST 8: Price provider None",     test_price_provider_none_price),
        ("TEST 9: No price provider",       test_no_price_provider),
        ("TEST 10: Zero qty after sell",    test_zero_qty_after_sell),
    ]

    for name, test_fn in tests:
        print(f"\n{name}")
        try:
            test_fn()
            print("  PASS")
        except AssertionError as e:
            print(f"  FAIL: {e}")
            raise

    print("\n" + "=" * 60)
    print(f"  ALL {len(tests)} WAC ENGINE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
