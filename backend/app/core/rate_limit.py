"""In-process sliding-window rate limiting.

The panel is designed to run as a single local uvicorn process, which makes an
in-memory limiter sufficient and dependency-free. If the deployment later adds
multiple workers, this must move to a shared store (for example Redis).
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def _cleanup(self, now: float) -> None:
        # Bound memory growth without a background task.
        if now - self._last_cleanup < 60:
            return
        cutoff = now - self.window_seconds
        stale = [key for key, events in self._events.items() if not events or events[-1] < cutoff]
        for key in stale:
            self._events.pop(key, None)
        self._last_cleanup = now

    def hit(self, key: str) -> tuple[bool, int]:
        """Record an attempt.

        Returns ``(allowed, retry_after_seconds)``. Rejected attempts are not
        recorded, so the window drains naturally.
        """

        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            self._cleanup(now)
            events = self._events.setdefault(key, deque())
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - events[0])) + 1)
                return False, retry_after
            events.append(now)
            return True, 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
