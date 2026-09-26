"""Single-flight guard + generation token for a background refresh cycle.

Pure, Flet-independent logic factored out of ui/app_flet.py's Dashboard
refresh() so it can be unit-tested without a live ft.Page. Encapsulates
exactly three responsibilities:

  1. Prevent uncontrolled parallel refreshes (a second refresh() call while
     one is already in flight is a no-op).
  2. Let a caller detect whether its own background result is still the
     latest one by the time it's ready to apply (a "generation" token) —
     a superseded/stale result is discarded, never applied out of order.
  3. Guarantee the in-progress flag is always released (success, exception,
     or stale-discard) so a future refresh is never permanently blocked.
"""
from __future__ import annotations

from typing import Optional


class RefreshGuard:
    """Single-flight guard for one logical "refresh" operation."""

    def __init__(self) -> None:
        self._in_progress = False
        self._generation = 0

    def try_begin(self) -> Optional[int]:
        """Attempt to start a new refresh cycle.

        Returns the generation token to carry through the background work,
        or None if a refresh is already in progress — the caller should
        treat that as a no-op (skip starting new work).
        """
        if self._in_progress:
            return None
        self._in_progress = True
        self._generation += 1
        return self._generation

    def is_current(self, generation: int) -> bool:
        """True if *generation* is still the latest started refresh.

        False means a newer refresh has started since *generation* began —
        its result is stale and must not be applied.
        """
        return generation == self._generation

    def end(self) -> None:
        """Mark the in-flight refresh as finished.

        Always call this when a refresh cycle ends — on success, on
        exception, and when discarding a stale result — so the guard never
        gets stuck permanently blocking future refreshes.
        """
        self._in_progress = False

    @property
    def in_progress(self) -> bool:
        return self._in_progress
