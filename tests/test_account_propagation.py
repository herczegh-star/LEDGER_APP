"""Phase 1 — account propagation through trade_service.build_trade_rows().

Verifies:
  - crypto leg never carries an account (belongs to venue/wallet holdings).
  - fiat quote leg carries the destination cash account, for BUY and SELL alike.
  - fee leg carries the same account ONLY when denominated in the quote currency.
  - a fee in a different currency (e.g. crypto network fee) gets account=None.
  - omitting account entirely reproduces today's behaviour (account=None
    everywhere) — no regression for existing callers that never pass it.
  - account propagation does not alter any amount/price/sign — purely additive.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.services.trade_service import AddTradeInput, build_trade_rows


_TS = datetime(2026, 9, 10, 9, 31, 0)


def test_sell_quote_leg_gets_destination_account():
    inp = AddTradeInput(
        type="SELL", timestamp=_TS, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3419.25"), venue="revolut",
        account="Osobní CZK",
    )
    rows = build_trade_rows(inp)
    base, quote = rows[0], rows[1]
    assert base.asset == "HYPE"
    assert base.account is None
    assert quote.asset == "CZK"
    assert quote.account == "Osobní CZK"


def test_buy_quote_leg_also_gets_account_symmetrically():
    """BUY debits the cash account just as SELL credits it — account
    propagation must not be SELL-only."""
    inp = AddTradeInput(
        type="BUY", timestamp=_TS, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
        account="Trading CZK",
    )
    rows = build_trade_rows(inp)
    base, quote = rows[0], rows[1]
    assert base.account is None
    assert quote.account == "Trading CZK"


def test_fee_in_quote_currency_inherits_account():
    inp = AddTradeInput(
        type="SELL", timestamp=_TS, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3419.25"), venue="revolut",
        fee_amount=Decimal("75"), fee_currency="CZK",
        account="Osobní CZK",
    )
    rows = build_trade_rows(inp)
    base, quote, fee = rows
    assert fee.type == "FEE"
    assert fee.asset == "CZK"
    assert fee.account == "Osobní CZK"


def test_fee_in_different_currency_gets_no_account():
    """A fee not denominated in the cash account's currency does not affect
    that account's balance and must not be tagged to it."""
    inp = AddTradeInput(
        type="SELL", timestamp=_TS, base_asset="BTC", base_amount=Decimal("0.01"),
        quote_currency="CZK", quote_amount=Decimal("25000"), venue="kraken",
        fee_amount=Decimal("0.00001"), fee_currency="BTC",
        account="Trading CZK",
    )
    rows = build_trade_rows(inp)
    fee = rows[2]
    assert fee.asset == "BTC"
    assert fee.account is None


def test_no_account_provided_matches_legacy_behaviour():
    """Omitting account reproduces today's exact behaviour — every row's
    account is None, identical to pre-Phase-1 rows."""
    inp = AddTradeInput(
        type="SELL", timestamp=_TS, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3419.25"), venue="revolut",
        fee_amount=Decimal("75"),
    )
    rows = build_trade_rows(inp)
    assert all(r.account is None for r in rows)


def test_account_does_not_change_amounts_or_signs():
    """Account propagation is purely additive metadata — amounts, prices,
    and signs must be bit-for-bit identical with or without an account."""
    base_kwargs = dict(
        type="SELL", timestamp=_TS, base_asset="HYPE", base_amount=Decimal("2"),
        quote_currency="CZK", quote_amount=Decimal("3419.25"), venue="revolut",
        fee_amount=Decimal("75"),
    )
    rows_no_account = build_trade_rows(AddTradeInput(**base_kwargs))
    rows_with_account = build_trade_rows(AddTradeInput(**base_kwargs, account="Osobní CZK"))

    for r1, r2 in zip(rows_no_account, rows_with_account):
        assert r1.amount == r2.amount
        assert r1.price == r2.price
        assert r1.asset == r2.asset
        assert r1.type == r2.type
        assert r1.currency == r2.currency
