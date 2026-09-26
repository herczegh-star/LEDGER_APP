"""Unit tests for ui/refresh_guard.py — pure logic, no Flet dependency.

Covers the three guarantees RefreshGuard exists to provide:
  1. try_begin() rejects a second concurrent refresh (returns None).
  2. is_current() lets a late/stale result identify itself and be discarded.
  3. end() always releases the in-progress flag, so a future refresh is
     never permanently blocked — including after an "exception" (simulated
     by the caller simply calling end() in its own except/finally, exactly
     as ui/app_flet.py's refresh() does).
"""
from __future__ import annotations

from ui.refresh_guard import RefreshGuard


def test_first_try_begin_returns_generation_1():
    guard = RefreshGuard()
    gen = guard.try_begin()
    assert gen == 1
    assert guard.in_progress is True


def test_try_begin_while_in_progress_returns_none():
    guard = RefreshGuard()
    guard.try_begin()
    assert guard.try_begin() is None  # second concurrent call is rejected


def test_end_releases_guard_for_next_refresh():
    guard = RefreshGuard()
    gen1 = guard.try_begin()
    guard.end()
    assert guard.in_progress is False
    gen2 = guard.try_begin()
    assert gen2 is not None
    assert gen2 != gen1


def test_generation_increments_each_cycle():
    guard = RefreshGuard()
    gen1 = guard.try_begin()
    guard.end()
    gen2 = guard.try_begin()
    guard.end()
    gen3 = guard.try_begin()
    assert (gen1, gen2, gen3) == (1, 2, 3)


def test_is_current_true_for_the_active_generation():
    guard = RefreshGuard()
    gen = guard.try_begin()
    assert guard.is_current(gen) is True


def test_is_current_false_after_a_newer_generation_started():
    """Simulates: refresh #1 is still in flight in the background when its
    result is ready to apply, but a newer refresh #2 has already started —
    #1's result must be recognised as stale."""
    guard = RefreshGuard()
    gen1 = guard.try_begin()
    guard.end()  # #1 finished (e.g. superseded by a user click before its
    gen2 = guard.try_begin()  # own callback ran — simulate #2 starting)
    assert guard.is_current(gen1) is False
    assert guard.is_current(gen2) is True


def test_end_is_safe_to_call_when_not_in_progress():
    """Defensive: end() must never raise, even if called twice or without a
    matching try_begin (mirrors the exception-safety requirement — end()
    is always called in a finally, regardless of what happened before)."""
    guard = RefreshGuard()
    guard.end()  # no matching try_begin — must not raise
    guard.end()  # double-end — must not raise
    assert guard.in_progress is False


def test_end_after_exception_unblocks_future_refresh():
    """Simulates ui/app_flet.py's exception-safety contract: even if the
    background work raised, end() (called in a finally) must still release
    the guard so the NEXT refresh() call is not permanently blocked."""
    guard = RefreshGuard()
    gen = guard.try_begin()
    try:
        raise RuntimeError("simulated failure in background worker")
    except RuntimeError:
        pass
    finally:
        guard.end()

    assert guard.in_progress is False
    assert guard.try_begin() is not None  # future refresh is not blocked
