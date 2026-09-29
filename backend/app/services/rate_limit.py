from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

import httpx


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


class ProviderOutage(RuntimeError):
    """
    A provider could not be reached or answered with a server error.

    Distinct from a request the provider understood and refused (a 4xx): only
    an outage says anything about whether the provider is available, so only
    an outage counts towards opening a ``CircuitBreaker``.
    """


def is_provider_outage(exc: BaseException) -> bool:
    """
    True when a failure says the provider is not serving us.

    A dropped or timed-out connection, a server error, or throttling counts. A
    request the provider understood and refused (a 4xx) and a body that could
    not be parsed do not: the server answered, so nothing about its
    availability has changed.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status == 429
    return False


class CircuitBreaker:
    """
    Stop calling a provider that keeps failing, then check on it later.

    After ``failure_threshold`` consecutive outage failures the breaker opens
    and ``allow()`` is False for ``cooldown_seconds``, so callers fail at once
    (and fall back) instead of each paying the full retry cost. When the
    cooldown ends one probe is admitted: a success closes the breaker, a
    failure reopens it immediately. Process-local, like the limiters above.
    """

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._failures = 0
        self._open_until: float | None = None

    def allow(self) -> bool:
        if self._open_until is None:
            return True
        if self._clock() < self._open_until:
            return False
        # Cooldown over: admit a probe. One more failure reopens at once.
        self._open_until = None
        self._failures = self.failure_threshold - 1
        return True

    def record_success(self) -> None:
        self.reset()

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = self._clock() + self.cooldown_seconds


# One breaker for the whole Open-Meteo host: elevation and weather are both
# read from it, one after the other, so a hung host must not be paid for twice.
open_meteo_breaker = CircuitBreaker("open-meteo")

discovery_limiter = FixedWindowRateLimiter()
search_limiter = FixedWindowRateLimiter()
intelligence_limiter = FixedWindowRateLimiter()
enrichment_limiter = FixedWindowRateLimiter()
