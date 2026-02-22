"""TTL cache for price provider responses.

The clock function is injectable so unit tests can control time
without sleeping.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple


class TtlCache:
    """Simple in-memory TTL cache.

    Args:
        ttl_seconds: How long entries live (default 60 s).
        clock:       Callable returning a monotonic timestamp (seconds).
                     Defaults to time.monotonic; inject a fake for tests.
    """

    def __init__(
        self,
        ttl_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._store: Dict[Any, Tuple[Any, float]] = {}  # key → (value, expires_at)

    def get(self, key: Any) -> Optional[Any]:
        """Return cached value or None if absent / expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._clock() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        """Store value with TTL."""
        self._store[key] = (value, self._clock() + self._ttl)

    def clear(self) -> None:
        """Evict all entries (useful for tests)."""
        self._store.clear()
