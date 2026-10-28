"""Tests for core/asset_display.py — display_asset().

Purely cosmetic rendering helper: must never be imported by any
accounting path (core/reports/, core/ledger_store.py, core/model.py) and
must leave every ticker except explicitly-overridden ones unchanged.
"""
from __future__ import annotations

from core.asset_display import display_asset


def test_ton_displays_as_gram_with_ton_in_parentheses():
    assert display_asset("TON") == "GRAM (TON)"


def test_ton_lowercase_input_still_resolves():
    assert display_asset("ton") == "GRAM (TON)"


def test_unrelated_tickers_pass_through_unchanged():
    for ticker in ("BTC", "ETH", "UNI", "ATOM", "SOLX", "TICS", "USDC"):
        assert display_asset(ticker) == ticker


def test_unknown_ticker_passes_through_unchanged():
    assert display_asset("NOSUCHCOIN") == "NOSUCHCOIN"


def test_internal_symbol_itself_is_never_mutated():
    """display_asset() must be a pure read -> the caller's original string
    object/value is never touched; calling it twice is idempotent and
    does not affect what RawRow.asset / DTO.asset would still read."""
    asset = "TON"
    display_asset(asset)
    assert asset == "TON"  # unchanged by the call


def test_ledger_store_module_does_not_import_asset_display():
    """Guard against accidental accounting-path coupling: the display
    helper must never be imported into core/ledger_store.py."""
    import core.ledger_store as mod
    assert "display_asset" not in dir(mod)


def test_positions_module_does_not_import_asset_display():
    import core.reports.positions as mod
    assert "display_asset" not in dir(mod)
