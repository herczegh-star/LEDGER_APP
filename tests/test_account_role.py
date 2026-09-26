"""Phase Model B — core/account_role.py: pure role resolution logic."""
from __future__ import annotations

from datetime import datetime, timedelta

from core.account_role import INVESTMENT_CASH, LEGACY_UNASSIGNED, PERSONAL, resolve_role
from core.account_role_store import AccountRole

_T0 = datetime(2026, 1, 1)
_T1 = datetime(2026, 6, 1)
_T2 = datetime(2026, 9, 1)


def _role(role, effective_at, created_at=None, venue="revolut", account="Osobní CZK", currency="CZK"):
    return AccountRole(
        id=f"r-{effective_at.isoformat()}", venue=venue, account=account, currency=currency,
        role=role, effective_at=effective_at, created_at=created_at or effective_at,
    )


# ── account IS NULL -> LEGACY_UNASSIGNED, never consults roles ─────────────

def test_account_none_is_always_legacy_unassigned():
    roles = [_role(INVESTMENT_CASH, _T0)]  # irrelevant, must be ignored
    result = resolve_role("revolut", None, "CZK", roles, as_of=_T2)
    assert result == LEGACY_UNASSIGNED


# ── no role declared -> fail closed (None) ──────────────────────────────────

def test_no_role_declared_returns_none():
    result = resolve_role("revolut", "Osobní CZK", "CZK", [], as_of=_T2)
    assert result is None


def test_no_role_at_this_point_in_time_returns_none():
    """A role declared only in the FUTURE relative to as_of must not apply."""
    roles = [_role(PERSONAL, _T2)]
    result = resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T0)
    assert result is None


# ── temporal lookup: latest effective_at <= as_of wins ──────────────────────

def test_latest_effective_at_wins():
    roles = [_role(PERSONAL, _T0), _role(INVESTMENT_CASH, _T1)]
    assert resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T0) == PERSONAL
    assert resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T1) == INVESTMENT_CASH
    assert resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T2) == INVESTMENT_CASH


def test_later_role_change_does_not_rewrite_earlier_interpretation():
    """The core requirement: a role declared TODAY with effective_at=today
    must not silently reinterpret a report run with as_of=yesterday."""
    roles = [_role(PERSONAL, _T0)]
    before_change = resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T1)

    # Later: role is revised, backdated only to _T1 (not to _T0).
    roles_after_revision = roles + [_role(INVESTMENT_CASH, _T1, created_at=_T2)]
    still_before_the_revision_point = resolve_role(
        "revolut", "Osobní CZK", "CZK", roles_after_revision, as_of=_T0 + timedelta(days=1)
    )
    after_the_revision_point = resolve_role(
        "revolut", "Osobní CZK", "CZK", roles_after_revision, as_of=_T1
    )
    assert before_change == PERSONAL
    assert still_before_the_revision_point == PERSONAL  # unchanged by the later revision
    assert after_the_revision_point == INVESTMENT_CASH


def test_tiebreak_on_identical_effective_at_uses_created_at():
    roles = [
        _role(PERSONAL, _T1, created_at=_T1),
        _role(INVESTMENT_CASH, _T1, created_at=_T2),  # written later, corrects the first
    ]
    assert resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T1) == INVESTMENT_CASH


# ── same account, different currency -> independent roles ──────────────────

def test_same_account_different_currency_independent():
    roles = [
        _role(PERSONAL, _T0, currency="CZK"),
        _role(INVESTMENT_CASH, _T0, currency="EUR"),
    ]
    assert resolve_role("revolut", "Osobní CZK", "CZK", roles, as_of=_T2) == PERSONAL
    assert resolve_role("revolut", "Osobní CZK", "EUR", roles, as_of=_T2) == INVESTMENT_CASH


def test_same_account_different_venue_independent():
    roles = [
        _role(PERSONAL, _T0, venue="revolut", account="Investment Cash CZK"),
        _role(INVESTMENT_CASH, _T0, venue="air_bank", account="Investment Cash CZK"),
    ]
    assert resolve_role("revolut", "Investment Cash CZK", "CZK", roles, as_of=_T2) == PERSONAL
    assert resolve_role("air_bank", "Investment Cash CZK", "CZK", roles, as_of=_T2) == INVESTMENT_CASH
