"""
A provider that is down must cost one failure, not one failure per call.

Postpass retries each query three times with backoff, and Overpass allows one
request at a time with a 200 s timeout, so during an outage every tile of every
search paid the full failure cost before falling back. A breaker opens after a
few consecutive outage failures and fails the next calls immediately, then
lets one probe through after a cooldown.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.routes import search
from app.services import overpass, postpass
from app.services.rate_limit import CircuitBreaker, ProviderOutage


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class CircuitBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.breaker = CircuitBreaker(
            "test", failure_threshold=3, cooldown_seconds=30.0, clock=self.clock
        )

    def test_it_opens_after_consecutive_failures(self) -> None:
        for _ in range(2):
            self.breaker.record_failure()
            self.assertTrue(self.breaker.allow())
        self.breaker.record_failure()
        self.assertFalse(self.breaker.allow())

    def test_a_success_resets_the_count(self) -> None:
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.breaker.record_success()
        self.breaker.record_failure()
        self.breaker.record_failure()
        self.assertTrue(self.breaker.allow())

    def test_it_admits_one_probe_after_the_cooldown(self) -> None:
        for _ in range(3):
            self.breaker.record_failure()
        self.clock.now += 31
        self.assertTrue(self.breaker.allow())
        # The probe fails: it reopens at once, without three more failures.
        self.breaker.record_failure()
        self.assertFalse(self.breaker.allow())

    def test_a_successful_probe_closes_it(self) -> None:
        for _ in range(3):
            self.breaker.record_failure()
        self.clock.now += 31
        self.assertTrue(self.breaker.allow())
        self.breaker.record_success()
        for _ in range(2):
            self.breaker.record_failure()
        self.assertTrue(self.breaker.allow())


class _Response:
    def __init__(self, status: int, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self) -> dict:
        return self._payload


def _client_factory(behaviour, calls: list[int]):
    """An httpx.AsyncClient stand-in whose requests follow ``behaviour``."""

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object):
            calls.append(1)
            return behaviour()

        async def get(self, *args: object, **kwargs: object):
            calls.append(1)
            return behaviour()

    return _Client


def _down() -> _Response:
    raise httpx.ConnectError("connection refused")


class PostpassBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        postpass.postpass_breaker.reset()
        postpass._CACHE.clear()

    def tearDown(self) -> None:
        postpass.postpass_breaker.reset()

    def _query(self, calls: list[int], behaviour) -> str:
        with patch.object(
            postpass.httpx, "AsyncClient", _client_factory(behaviour, calls)
        ), patch.object(postpass.asyncio, "sleep", new=AsyncMock()):
            try:
                asyncio.run(postpass._execute_sql_uncached("SELECT 1"))
            except RuntimeError as exc:
                return str(exc)
        return ""

    def test_an_outage_stops_costing_full_retries(self) -> None:
        calls: list[int] = []
        for _ in range(8):
            self._query(calls, _down)
        attempts = postpass.POSTPASS_SERVER_RETRIES + 1
        # Three queries pay for every attempt; the other five are refused
        # without touching the network.
        self.assertEqual(len(calls), 3 * attempts)

    def test_a_refused_query_says_why(self) -> None:
        calls: list[int] = []
        for _ in range(3):
            self._query(calls, _down)
        message = self._query(calls, _down)
        self.assertIn("temporarily skipped", message)

    def test_a_client_error_is_not_an_outage(self) -> None:
        calls: list[int] = []
        for _ in range(6):
            self._query(calls, lambda: _Response(400))
        # The server answered every time, so nothing was ever refused.
        self.assertEqual(len(calls), 6)

    def test_an_open_breaker_still_falls_back_to_overpass(self) -> None:
        for _ in range(3):
            postpass.postpass_breaker.record_failure()
        calls: list[int] = []
        fallback = AsyncMock(return_value=[])
        with patch.object(
            postpass.httpx, "AsyncClient", _client_factory(_down, calls)
        ), patch.object(
            postpass.overpass_fallback, "overpass_relations_in_bbox", fallback
        ):
            asyncio.run(
                postpass.discover_relations_in_bbox((6.0, 45.0, 6.5, 45.5))
            )
        self.assertEqual(calls, [])
        fallback.assert_awaited_once()


class OverpassBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        overpass.overpass_breaker.reset()

    def tearDown(self) -> None:
        overpass.overpass_breaker.reset()

    def test_an_outage_stops_costing_a_request_per_call(self) -> None:
        calls: list[int] = []
        with patch.object(
            overpass.httpx, "AsyncClient", _client_factory(_down, calls)
        ):
            for _ in range(8):
                with self.assertRaises(RuntimeError):
                    asyncio.run(overpass._overpass_post("[out:json];"))
        self.assertEqual(len(calls), 3)


class NominatimRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        search._SEARCH_CACHE.clear()

    def test_one_transient_failure_is_retried(self) -> None:
        payload = [
            {
                "lat": "45.92",
                "lon": "6.87",
                "display_name": "Chamonix, France",
                "name": "Chamonix",
            }
        ]
        outcomes = [
            lambda: (_ for _ in ()).throw(httpx.ConnectError("dropped")),
            lambda: type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: payload,
                },
            )(),
        ]
        calls: list[int] = []

        def behaviour():
            return outcomes[len(calls) - 1]()

        with patch.object(
            search.httpx, "AsyncClient", _client_factory(behaviour, calls)
        ), patch.object(search.asyncio, "sleep", new=AsyncMock()):
            result = asyncio.run(search._search_uncached("Chamonix"))
        self.assertEqual(result["results"][0]["name"], "Chamonix")
        self.assertEqual(len(calls), 2)

    def test_a_persistent_failure_is_still_reported(self) -> None:
        calls: list[int] = []
        with patch.object(
            search.httpx, "AsyncClient", _client_factory(_down, calls)
        ), patch.object(search.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaises(RuntimeError):
                asyncio.run(search._search_uncached("Chamonix"))
        # One try and one retry, never a storm against a policy-limited API.
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
