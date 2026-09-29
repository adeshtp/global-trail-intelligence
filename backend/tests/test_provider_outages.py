"""
Outages measured against hung and refused providers (a local server that
accepts and never answers, and a closed port):

  Postpass hangs        94.6 s per search (3 attempts x 30 s + backoff)
  Open-Meteo hangs      50-52 s per intelligence request, every request
  Nominatim hangs       21 s per search (a retry doubled the 10 s timeout)
  Tavily hangs          60 s per product request, every request

A timeout is not retried: a hung public host does not answer on an immediate
second try, and the fallback is the better use of the time. Providers whose
failure was not remembered now share the breaker treatment.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.routes import search
from app.services import elevation, postpass, products, weather
from app.services.rate_limit import (
    is_provider_outage,
    open_meteo_breaker,
)


class _Response:
    def __init__(self, status: int = 200, payload=None) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""
        self.request = httpx.Request("GET", "http://provider.test")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=self.request, response=httpx.Response(self.status_code, request=self.request)
            )

    def json(self):
        return self._payload


def _client(behaviour, calls: list[int]):
    class _C:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> "_C":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def get(self, *a: object, **k: object):
            calls.append(1)
            return behaviour()

        async def post(self, *a: object, **k: object):
            calls.append(1)
            return behaviour()

    return _C


def _hang():
    raise httpx.ReadTimeout("no answer")


def _refused():
    raise httpx.ConnectError("refused")


class OutageClassificationTests(unittest.TestCase):
    def test_what_counts_as_an_outage(self) -> None:
        request = httpx.Request("GET", "http://x")
        self.assertTrue(is_provider_outage(httpx.ConnectError("x")))
        self.assertTrue(is_provider_outage(httpx.ReadTimeout("x")))
        for status in (429, 500, 503):
            error = httpx.HTTPStatusError(
                "x", request=request, response=httpx.Response(status, request=request)
            )
            self.assertTrue(is_provider_outage(error), status)

    def test_a_refusal_or_bad_payload_is_not_an_outage(self) -> None:
        request = httpx.Request("GET", "http://x")
        error = httpx.HTTPStatusError(
            "x", request=request, response=httpx.Response(404, request=request)
        )
        self.assertFalse(is_provider_outage(error))
        self.assertFalse(is_provider_outage(ValueError("bad json")))


class PostpassTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        postpass.postpass_breaker.reset()

    def tearDown(self) -> None:
        postpass.postpass_breaker.reset()

    def _query(self, calls: list[int], behaviour) -> None:
        with patch.object(
            postpass.httpx, "AsyncClient", _client(behaviour, calls)
        ), patch.object(postpass.asyncio, "sleep", new=AsyncMock()) as sleep:
            try:
                asyncio.run(postpass._execute_sql_uncached("SELECT 1"))
            except RuntimeError:
                pass
        self.last_sleeps = sleep.await_count

    def test_a_timeout_is_not_retried(self) -> None:
        calls: list[int] = []
        self._query(calls, _hang)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.last_sleeps, 0)

    def test_a_dropped_connection_is_still_retried(self) -> None:
        calls: list[int] = []
        self._query(calls, _refused)
        self.assertEqual(len(calls), postpass.POSTPASS_SERVER_RETRIES + 1)

    def test_timeouts_open_the_breaker(self) -> None:
        calls: list[int] = []
        for _ in range(6):
            self._query(calls, _hang)
        self.assertEqual(len(calls), 3)


class NominatimTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        search._SEARCH_CACHE.clear()
        search.nominatim_breaker.reset()

    def tearDown(self) -> None:
        search.nominatim_breaker.reset()

    def _search(self, calls: list[int], behaviour) -> None:
        with patch.object(
            search.httpx, "AsyncClient", _client(behaviour, calls)
        ), patch.object(search.asyncio, "sleep", new=AsyncMock()):
            try:
                asyncio.run(search._search_uncached("Chamonix"))
            except RuntimeError:
                pass

    def test_a_timeout_is_not_retried(self) -> None:
        calls: list[int] = []
        self._search(calls, _hang)
        self.assertEqual(len(calls), 1)

    def test_a_dropped_connection_is_retried_once(self) -> None:
        calls: list[int] = []
        self._search(calls, _refused)
        self.assertEqual(len(calls), 2)

    def test_repeated_outages_stop_costing_a_request_each(self) -> None:
        calls: list[int] = []
        for _ in range(6):
            self._search(calls, _hang)
        self.assertEqual(len(calls), 3)

    def test_a_refusal_never_opens_it(self) -> None:
        calls: list[int] = []
        for _ in range(6):
            self._search(calls, lambda: _Response(404))
        self.assertEqual(len(calls), 6)


class OpenMeteoBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        open_meteo_breaker.reset()
        weather._WEATHER_CACHE.clear()
        weather._WEATHER_INFLIGHT.clear()

    def tearDown(self) -> None:
        open_meteo_breaker.reset()

    def _weather(self, calls: list[int], behaviour, lat: float = 45.9) -> int:
        with patch.object(
            weather.httpx, "AsyncClient", _client(behaviour, calls)
        ):
            try:
                asyncio.run(weather._fetch_weather_uncached(lat, 6.87))
            except HTTPException as exc:
                return exc.status_code
        return 200

    def _elevation(self, calls: list[int], behaviour) -> int:
        geometry = {
            "type": "LineString",
            "coordinates": [[6.87, 45.9], [6.88, 45.91]],
        }
        with patch.object(
            elevation.httpx, "AsyncClient", _client(behaviour, calls)
        ):
            try:
                asyncio.run(elevation._fetch_elevation_uncached(geometry))
            except HTTPException as exc:
                return exc.status_code
        return 200

    def test_weather_and_elevation_share_one_breaker(self) -> None:
        calls: list[int] = []
        # Two elevation failures and one weather failure open it for both.
        self._elevation(calls, _hang)
        self._elevation(calls, _hang)
        self._weather(calls, _hang)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self._weather(calls, _hang), 502)
        self.assertEqual(self._elevation(calls, _hang), 502)
        self.assertEqual(len(calls), 3)

    def test_route_weather_is_covered_too(self) -> None:
        for _ in range(3):
            open_meteo_breaker.record_failure()
        calls: list[int] = []
        points = [
            {"labels": ["start"], "latitude": 45.9, "longitude": 6.87, "elevation_m": 1500.0},
            {"labels": ["end"], "latitude": 45.95, "longitude": 6.87, "elevation_m": 900.0},
        ]
        with patch.object(weather.httpx, "AsyncClient", _client(_hang, calls)):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(weather.get_route_weather(points))
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(calls, [])

    def test_a_refusal_does_not_open_it(self) -> None:
        calls: list[int] = []
        for _ in range(6):
            self._weather(calls, lambda: _Response(404))
        self.assertEqual(len(calls), 6)

    def test_a_good_answer_closes_it_again(self) -> None:
        open_meteo_breaker.record_failure()
        open_meteo_breaker.record_failure()
        payload = {
            "latitude": 45.9, "longitude": 6.87, "timezone": "UTC",
            "current": {"time": "2026-09-29T12:00", "temperature_2m": 10.0},
            "hourly": {"time": ["2026-09-29T12:00"], "precipitation": [0.0]},
        }
        calls: list[int] = []
        self.assertEqual(self._weather(calls, lambda: _Response(200, payload)), 200)
        open_meteo_breaker.record_failure()
        open_meteo_breaker.record_failure()
        self.assertTrue(open_meteo_breaker.allow())


class TavilyBreakerTests(unittest.TestCase):
    def setUp(self) -> None:
        products.tavily_breaker.reset()

    def tearDown(self) -> None:
        products.tavily_breaker.reset()

    def _search(self, calls: list[int], behaviour):
        with patch.object(
            products.httpx, "AsyncClient", _client(behaviour, calls)
        ), patch.object(products, "settings") as fake_settings, patch.dict(
            "os.environ", {"TAVILY_API_KEY": "test-key"}
        ):
            fake_settings.TAVILY_API_KEY = "test-key"
            return asyncio.run(products._search_web("trekking poles buy online"))

    def test_a_hung_search_stops_costing_a_request_per_item(self) -> None:
        calls: list[int] = []
        errors = [self._search(calls, _hang)[2] for _ in range(8)]
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(errors))

    def test_a_refused_search_still_reports_unavailable(self) -> None:
        calls: list[int] = []
        _, _, error = self._search(calls, _hang)
        self.assertIn("unavailable", error)

    def test_a_refusal_does_not_open_it(self) -> None:
        calls: list[int] = []
        for _ in range(6):
            self._search(calls, lambda: _Response(401))
        self.assertEqual(len(calls), 6)


if __name__ == "__main__":
    unittest.main()
