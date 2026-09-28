from __future__ import annotations

import time
from collections import OrderedDict


class FixedWindowRateLimiter:
    """Small process-local limiter for expensive public enrichment endpoints."""

    def __init__(self, max_entries: int = 4096) -> None:
        self.max_entries = max_entries
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
    ) -> bool:
        now = time.monotonic()
        started, count = self._windows.get(
            key,
            (now, 0),
        )
        if now - started >= window_seconds:
            started = now
            count = 0
        if count >= limit:
            self._windows[key] = (started, count)
            self._windows.move_to_end(key)
            return False
        self._windows[key] = (started, count + 1)
        self._windows.move_to_end(key)
        while len(self._windows) > self.max_entries:
            self._windows.popitem(last=False)
        return True


discovery_limiter = FixedWindowRateLimiter()
search_limiter = FixedWindowRateLimiter()
intelligence_limiter = FixedWindowRateLimiter()
enrichment_limiter = FixedWindowRateLimiter()
