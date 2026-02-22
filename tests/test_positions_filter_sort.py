"""Unit tests for the pure filter_and_sort_rows helper in positions_view.py.

No Flet page or database required — the helper is pure Python.
"""
from decimal import Decimal
from typing import List

import pytest

from core.services.ui_facade import PositionDTO
from ui.modules.positions_view import filter_and_sort_rows


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _row(asset: str, qty: str, cost: str = "0", pnl: str = "0") -> PositionDTO:
    return PositionDTO(
        asset=asset,
        quantity=Decimal(qty),
        wac=Decimal("0"),
        cost_basis=Decimal(cost),
        realized_pnl=Decimal(pnl),
    )


_SAMPLE: List[PositionDTO] = [
    _row("BTC",  "1",    cost="40000", pnl="500"),
    _row("ETH",  "5",    cost="8000",  pnl="-100"),
    _row("SOL",  "0",    cost="0",     pnl="0"),
    _row("USDT", "1000", cost="1000",  pnl="0"),
    _row("BNB",  "0",    cost="0",     pnl="200"),
]


def _keys(rows) -> List[str]:
    return [r.asset for r in rows]


# ── No filter, no sort (az default) ──────────────────────────────────────────

def test_no_filter_returns_all():
    result = filter_and_sort_rows(_SAMPLE)
    assert len(result) == len(_SAMPLE)


def test_default_sort_is_alphabetical():
    result = filter_and_sort_rows(_SAMPLE)
    keys = _keys(result)
    assert keys == sorted(keys)


def test_does_not_mutate_input():
    original = list(_SAMPLE)
    filter_and_sort_rows(_SAMPLE, sort_key="cost_desc")
    assert _keys(_SAMPLE) == _keys(original)


# ── Search filter ─────────────────────────────────────────────────────────────

def test_search_exact_match():
    result = filter_and_sort_rows(_SAMPLE, search="BTC")
    assert _keys(result) == ["BTC"]


def test_search_case_insensitive():
    result = filter_and_sort_rows(_SAMPLE, search="btc")
    assert _keys(result) == ["BTC"]


def test_search_mixed_case():
    result = filter_and_sort_rows(_SAMPLE, search="Eth")
    assert _keys(result) == ["ETH"]


def test_search_substring():
    result = filter_and_sort_rows(_SAMPLE, search="B")
    # Should match BTC, BNB
    assert set(_keys(result)) == {"BTC", "BNB"}


def test_search_empty_string_no_filter():
    result = filter_and_sort_rows(_SAMPLE, search="")
    assert len(result) == len(_SAMPLE)


def test_search_no_match_returns_empty():
    result = filter_and_sort_rows(_SAMPLE, search="XYZ")
    assert result == []


def test_search_whitespace_stripped():
    result = filter_and_sort_rows(_SAMPLE, search="  BTC  ")
    assert _keys(result) == ["BTC"]


# ── Hide zeros filter ─────────────────────────────────────────────────────────

def test_hide_zeros_removes_zero_quantity():
    result = filter_and_sort_rows(_SAMPLE, hide_zeros=True)
    for r in result:
        assert r.quantity != Decimal("0")


def test_hide_zeros_keeps_nonzero():
    result = filter_and_sort_rows(_SAMPLE, hide_zeros=True)
    assert "BTC" in _keys(result)
    assert "ETH" in _keys(result)
    assert "USDT" in _keys(result)


def test_hide_zeros_removes_sol_and_bnb():
    result = filter_and_sort_rows(_SAMPLE, hide_zeros=True)
    assert "SOL" not in _keys(result)
    assert "BNB" not in _keys(result)


def test_hide_zeros_false_keeps_zeros():
    result = filter_and_sort_rows(_SAMPLE, hide_zeros=False)
    assert "SOL" in _keys(result)


# ── Sort options ──────────────────────────────────────────────────────────────

def test_sort_az():
    result = filter_and_sort_rows(_SAMPLE, sort_key="az")
    keys = _keys(result)
    assert keys == sorted(keys)


def test_sort_cost_desc():
    result = filter_and_sort_rows(_SAMPLE, sort_key="cost_desc")
    costs = [r.cost_basis for r in result]
    assert costs == sorted(costs, reverse=True)


def test_sort_qty_desc():
    result = filter_and_sort_rows(_SAMPLE, sort_key="qty_desc")
    qtys = [r.quantity for r in result]
    assert qtys == sorted(qtys, reverse=True)


def test_sort_pnl_desc():
    result = filter_and_sort_rows(_SAMPLE, sort_key="pnl_desc")
    pnls = [r.realized_pnl for r in result]
    assert pnls == sorted(pnls, reverse=True)


def test_sort_unknown_key_stable():
    """Unknown sort key should not crash and return all rows."""
    result = filter_and_sort_rows(_SAMPLE, sort_key="nonexistent")
    assert len(result) == len(_SAMPLE)


# ── Combined filter + sort ────────────────────────────────────────────────────

def test_search_and_hide_zeros_combined():
    result = filter_and_sort_rows(_SAMPLE, search="B", hide_zeros=True)
    # "B" matches BTC and BNB; BNB has qty=0 so hidden
    assert _keys(result) == ["BTC"]


def test_search_and_sort_combined():
    result = filter_and_sort_rows(_SAMPLE, search="B", sort_key="cost_desc")
    # BTC cost=40000, BNB cost=0 → BTC first
    assert _keys(result)[0] == "BTC"


def test_all_filters_combined():
    result = filter_and_sort_rows(
        _SAMPLE, search="", hide_zeros=True, sort_key="qty_desc"
    )
    # SOL and BNB hidden, remaining sorted by qty desc
    assert "SOL" not in _keys(result)
    assert "BNB" not in _keys(result)
    qtys = [r.quantity for r in result]
    assert qtys == sorted(qtys, reverse=True)


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_input():
    assert filter_and_sort_rows([]) == []


def test_single_row():
    rows = [_row("BTC", "1", cost="40000")]
    assert filter_and_sort_rows(rows) == rows


def test_all_zeros_hidden():
    rows = [_row("A", "0"), _row("B", "0")]
    result = filter_and_sort_rows(rows, hide_zeros=True)
    assert result == []


def test_returns_new_list_not_same_object():
    result = filter_and_sort_rows(_SAMPLE)
    assert result is not _SAMPLE
