"""Phase 2 — pure-function UI helpers in ui/modules/cash_view.py.

Covers the two spec-required UI behaviours that don't need a live ft.Page:
  - Unassigned Cash rendering (display-only label for account=None)
  - legacy account=None UI behavior (never crashes, never invents a name)

Note: open_add_trade_dialog()'s account-effective-value logic and
build_cash_view()'s table construction live inside Flet closures that
require a live ft.Page/event loop to instantiate — consistent with how the
rest of this codebase already tests UI (no existing dialog/view in this
repo has closure-level unit tests either; business logic is verified at the
facade layer instead, which tests/test_facade_*.py and
tests/test_cash_view_facade.py already cover for exactly this data).
"""
from __future__ import annotations

from decimal import Decimal

from ui.modules.cash_view import _account_label, _money


def test_account_label_none_renders_as_unassigned_cash():
    assert _account_label(None) == "Unassigned Cash"


def test_account_label_real_account_passthrough():
    assert _account_label("Osobní CZK") == "Osobní CZK"


def test_account_label_never_returns_empty_string_for_none():
    # Guards against a future refactor accidentally rendering a blank cell
    # instead of the explicit "Unassigned Cash" label.
    assert _account_label(None) != ""


def test_money_formats_positive_and_negative():
    # Non-breaking space as thousands separator, matching the convention
    # already used by ui/modules/venue_view.py and asset_detail_view.py.
    assert _money(Decimal("3344.25"), "CZK") == "3 344.25 CZK"
    assert _money(Decimal("-50000"), "CZK") == "-50 000.00 CZK"
