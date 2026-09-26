"""Integration-style tests for the Dashboard refresh() background pattern.

ui/app_flet.py's refresh() is a closure nested inside main_view(), which
requires a live ft.Page to instantiate — not independently unit-testable
without a much larger refactor (explicitly out of scope for this fix). This
file instead exercises the EXACT SAME pattern refresh() uses — a background
thread computing a result, handed back to the "UI thread" via a
page.run_task()-style async bridge, guarded by RefreshGuard — against a
minimal fake Page that faithfully reproduces Flet's run_task() contract
(schedule a coroutine onto the page's own event loop from any thread).

This proves the PATTERN is correct (non-blocking caller, exception-safe,
stale-result-safe, single-flight) using the real ui.refresh_guard.RefreshGuard
class. It does not click through the live app_flet.py UI (see the Phase 2.5
notes on why GUI screenshot automation was abandoned in this environment).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from ui.refresh_guard import RefreshGuard


class FakePage:
    """Minimal stand-in for ft.Page's run_task(): schedules a coroutine onto
    this page's own asyncio event loop, callable safely from any thread —
    exactly Flet's real contract for page.run_task()."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_task(self, coro_func) -> None:
        asyncio.run_coroutine_threadsafe(coro_func(), self.loop)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)


@pytest.fixture
def page():
    p = FakePage()
    yield p
    p.stop()


def make_refresh(page: FakePage, guard: RefreshGuard, fetch_fn, apply_fn, on_error=None):
    """Reproduces the exact structure of ui/app_flet.py's refresh():
    try_begin -> background thread -> fetch -> async apply (guarded by
    is_current) -> finally end(). Used so tests exercise the real pattern,
    not a paraphrase of it.
    """

    def refresh() -> None:
        generation = guard.try_begin()
        if generation is None:
            return  # concurrent-click guard: no-op while one is in flight

        def _worker() -> None:
            result = None
            try:
                result = fetch_fn()
            except Exception as exc:  # noqa: BLE001 - mirrors refresh()'s broad catch
                if on_error:
                    on_error(exc)

            async def _apply() -> None:
                try:
                    if not guard.is_current(generation):
                        return  # stale — a newer refresh superseded this one
                    if result is not None:
                        apply_fn(result)
                finally:
                    guard.end()

            try:
                page.run_task(_apply)
            except Exception:
                guard.end()

        threading.Thread(target=_worker, daemon=True).start()

    return refresh


# ── 1. Non-blocking caller, even with a slow fetch ──────────────────────────

def test_refresh_call_returns_immediately_despite_slow_fetch(page):
    """The historical bug: get_dashboard_snapshot() ran synchronously on the
    UI thread. This proves the FIXED pattern's refresh() call itself returns
    near-instantly regardless of how slow the underlying fetch is — the
    'Working...' freeze this fix targets."""
    guard = RefreshGuard()
    applied = threading.Event()
    results = []

    def slow_fetch():
        time.sleep(0.5)  # stands in for a slow/blocked price_provider call
        return "snapshot-data"

    def apply(result):
        results.append(result)
        applied.set()

    refresh = make_refresh(page, guard, slow_fetch, apply)

    t0 = time.perf_counter()
    refresh()
    call_duration = time.perf_counter() - t0

    assert call_duration < 0.1, (
        f"refresh() call itself took {call_duration*1000:.1f}ms — "
        "it must return immediately, not block on the fetch"
    )

    assert applied.wait(timeout=2), "background result was never applied"
    assert results == ["snapshot-data"]


# ── 2. Simulated slow price_provider doesn't block the caller's thread ─────

def test_simulated_slow_price_provider_does_not_block_caller(page):
    """Directly mirrors spec test 5: a pathologically slow price_provider
    (here: 1s) must not stall whoever calls refresh()."""
    guard = RefreshGuard()
    applied = threading.Event()

    def pathological_price_provider_fetch():
        time.sleep(1.0)
        return {"positions": 3}

    def apply(result):
        applied.set()

    refresh = make_refresh(page, guard, pathological_price_provider_fetch, apply)

    t0 = time.perf_counter()
    refresh()
    assert time.perf_counter() - t0 < 0.1

    assert applied.wait(timeout=3)


# ── 3. Exception during refresh does not leave a stuck loading state ───────

def test_exception_in_fetch_releases_guard_and_skips_apply(page):
    """Spec test 6: a simulated exception during refresh must not leave the
    'in progress' / loading state stuck — the guard must release, and a
    subsequent refresh must be able to start."""
    guard = RefreshGuard()
    errors = []
    apply_calls = []
    worker_done = threading.Event()

    def failing_fetch():
        raise RuntimeError("simulated get_dashboard_snapshot() failure")

    def apply(result):
        apply_calls.append(result)

    def on_error(exc):
        errors.append(exc)
        worker_done.set()

    refresh = make_refresh(page, guard, failing_fetch, apply, on_error=on_error)
    refresh()

    assert worker_done.wait(timeout=2)
    # Give the (nonexistent, since result is None) apply-scheduling a moment;
    # end() happens in the worker's exception path here since result stays None
    # and _apply is still scheduled to run (result is None -> apply_fn skipped,
    # but guard.end() still executes in its finally).
    time.sleep(0.2)

    assert len(errors) == 1
    assert apply_calls == []  # apply_fn must never run with no result
    assert guard.in_progress is False, "guard must be released after an exception"

    # A subsequent refresh must not be blocked by the failed one.
    assert guard.try_begin() is not None


# ── 4. Late/stale background result is discarded ────────────────────────────

def test_stale_result_is_discarded_when_superseded(page):
    """Spec test 7: if a newer refresh cycle starts before an older one's
    background result is applied, the older (now-stale) result must be
    silently discarded, never applied out of order."""
    guard = RefreshGuard()
    apply_calls = []

    def apply(result):
        apply_calls.append(result)

    # Manually drive one generation through to the point where its result is
    # ready, but delay calling guard.end() to simulate "still mid-flight"
    # when a newer generation starts (this is the concrete scenario
    # RefreshGuard.is_current() exists to protect against).
    gen1 = guard.try_begin()
    assert gen1 == 1

    # Simulate generation 1 finishing (its worker calls end() when its
    # apply-coroutine runs) BEFORE generation 2 starts — but if generation
    # 1's apply were delayed past generation 2's start, is_current(gen1)
    # must be False:
    guard.end()
    gen2 = guard.try_begin()
    assert gen2 == 2

    # Generation 1's (late) apply callback checks is_current(gen1):
    assert guard.is_current(gen1) is False
    if guard.is_current(gen1):
        apply("stale-result-from-gen1")  # must NOT happen
    assert apply_calls == []

    # Generation 2's apply is legitimate:
    assert guard.is_current(gen2) is True
    guard.end()


# ── 5. Concurrent rapid calls do not spawn multiple overlapping workers ────

def test_rapid_concurrent_calls_only_run_one_fetch(page):
    """Spec: 'více rychlých kliknutí na Dashboard nesmí spustit
    nekontrolovaně více paralelních refreshů'."""
    guard = RefreshGuard()
    fetch_call_count = [0]
    fetch_started = threading.Event()
    release_fetch = threading.Event()

    def blocking_fetch():
        fetch_call_count[0] += 1
        fetch_started.set()
        release_fetch.wait(timeout=2)
        return "data"

    def apply(result):
        pass

    refresh = make_refresh(page, guard, blocking_fetch, apply)

    refresh()  # starts the one-and-only in-flight fetch
    assert fetch_started.wait(timeout=1)

    # Simulate 5 rapid extra clicks while the first fetch is still running.
    for _ in range(5):
        refresh()

    release_fetch.set()
    time.sleep(0.3)

    assert fetch_call_count[0] == 1, (
        f"expected exactly 1 fetch, got {fetch_call_count[0]} — "
        "concurrent clicks must not spawn parallel fetches"
    )


# ── 6. "Initial load" equivalent — same pattern, first call succeeds ───────

def test_first_ever_call_applies_correctly(page):
    """Equivalent of 'initial Dashboard load stále funguje': the very first
    refresh() call (generation 1, nothing in flight yet) must fetch and
    apply correctly. ui/app_flet.py's actual initial load uses the same
    threading.Thread + page.run_task() idiom via _load_prices() /
    _finish_on_ui_thread(), which this fix did not modify."""
    guard = RefreshGuard()
    applied = threading.Event()
    results = []

    def fetch():
        return {"snapshot": True}

    def apply(result):
        results.append(result)
        applied.set()

    refresh = make_refresh(page, guard, fetch, apply)
    refresh()

    assert applied.wait(timeout=2)
    assert results == [{"snapshot": True}]
    assert guard.in_progress is False
