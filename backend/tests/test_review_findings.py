"""
Findings from the review of the whole branch, each pinned by a test.

* a harvest with a failed query must never be remembered
* a circuit breaker admits one probe after its cooldown, not every caller
* a geometry that cannot be tested is not treated as a duplicate
* route-weather coordinates keep their precision and never use exponents
* a route that collapses to one sample is still read at its elevation
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.routes import discovery, trails
from app.services import postpass, weather
from app.services.postpass import BboxRows
from app.services.rate_limit import CircuitBreaker
from test_harvest_cache import BROAD, SMALL, _patched, _Providers, _run


class _WaysFailOnce(_Providers):
    """Relations always answer; the first ways query raises."""

    async def relations(self, tile, *, limit):
        self.relation_calls.append(tile)
        return BboxRows()

    async def ways(self, tile, *, limit):
        self.way_calls.append(tile)
        if len(self.way_calls) == 1:
            raise RuntimeError("Postpass timed out")
        return BboxRows()


class _RequeryFails(_Providers):
    """The first relations query is capped and its first re-query raises."""

    async def relations(self, tile, *, limit):
        self.relation_calls.append(tile)
        if len(self.relation_calls) == 2:
            raise RuntimeError("Postpass down")
        rows = BboxRows()
        rows.truncated = len(self.relation_calls) == 1
        return rows


class PartialFailureIsNotCachedTests(unittest.TestCase):
    def setUp(self) -> None:
        discovery._HARVEST_CACHE.clear()
        discovery._HARVEST_INFLIGHT.clear()

    def _second_search_reads_providers_again(self, providers, bbox) -> bool:
        with _patched(providers):
            asyncio.run(_run(bbox=bbox))
            after_first = providers.total
            asyncio.run(_run(bbox=bbox))
        return providers.total > after_first

    def test_a_tile_where_only_one_query_failed(self) -> None:
        self.assertTrue(
            self._second_search_reads_providers_again(_WaysFailOnce(), BROAD)
        )

    def test_a_failed_requery_of_a_capped_tile(self) -> None:
        self.assertTrue(
            self._second_search_reads_providers_again(_RequeryFails(), SMALL)
        )

    def test_the_search_says_it_is_not_complete(self) -> None:
        with _patched(_WaysFailOnce()):
            coverage = asyncio.run(_run(bbox=BROAD))["coverage"]
        self.assertFalse(coverage["coverage_complete"])

    def test_a_clean_search_is_still_cached(self) -> None:
        self.assertFalse(
            self._second_search_reads_providers_again(
                _Providers(), SMALL
            )
        )


class BreakerProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1000.0
        self.breaker = CircuitBreaker(
            "test",
            failure_threshold=3,
            cooldown_seconds=30.0,
            clock=lambda: self.now,
        )
        for _ in range(3):
            self.breaker.record_failure()

    def test_only_one_caller_probes_after_the_cooldown(self) -> None:
        self.now += 31
        admitted = [self.breaker.allow() for _ in range(5)]
        self.assertEqual(admitted, [True, False, False, False, False])

    def test_a_failed_probe_reopens_and_the_next_probe_comes_later(self) -> None:
        self.now += 31
        self.assertTrue(self.breaker.allow())
        self.breaker.record_failure()
        self.assertFalse(self.breaker.allow())
        self.now += 31
        self.assertTrue(self.breaker.allow())

    def test_a_probe_that_never_reports_does_not_wedge_it(self) -> None:
        self.now += 31
        self.assertTrue(self.breaker.allow())
        self.assertFalse(self.breaker.allow())
        self.now += 31
        self.assertTrue(self.breaker.allow())

    def test_a_successful_probe_admits_everyone_again(self) -> None:
        self.now += 31
        self.assertTrue(self.breaker.allow())
        self.breaker.record_success()
        self.assertTrue(all(self.breaker.allow() for _ in range(5)))


class LiesAlongTests(unittest.TestCase):
    def test_an_untestable_geometry_is_not_a_duplicate(self) -> None:
        line = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
        with patch.object(
            discovery, "shape", side_effect=ValueError("invalid geometry")
        ):
            self.assertFalse(discovery._lies_along(line, line, {}))


class RouteWeatherCoordinateTests(unittest.TestCase):
    def _params(self, points) -> dict:
        captured: list[dict] = []

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return {"current": {}, "hourly": {"time": []}}

        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                return None

            async def get(self, url, params=None):
                captured.append(params)
                return _Response()

        with patch.object(weather.httpx, "AsyncClient", _Client):
            try:
                asyncio.run(weather._fetch_route_weather_uncached(points))
            except Exception:
                pass  # an empty payload is fine; only the request matters
        return captured[0]

    def test_precision_is_kept_and_no_exponent_is_sent(self) -> None:
        params = self._params(
            [
                {"labels": ["start"], "latitude": 0.00001,
                 "longitude": 123.456789, "elevation_m": 10.0},
                {"labels": ["end"], "latitude": -45.9,
                 "longitude": 6.87, "elevation_m": 20.0},
            ]
        )
        self.assertEqual(params["latitude"], "0.00001,-45.9")
        self.assertEqual(params["longitude"], "123.45679,6.87")


class CollapsedRouteTests(unittest.TestCase):
    def test_one_sample_is_still_read_at_its_elevation(self) -> None:
        coordinates = [[6.87, 45.9], [6.8701, 45.9]]
        way = postpass.PostpassWay(
            way_id=7, name="Loop", route=None, highway="path",
            sac_scale=None, trail_visibility=None, surface=None,
            smoothness=None, tracktype=None, access=None, incline=None,
            incline_direction=None, width=None, assisted_trail=None,
            aliases=[], geometry_type="LineString", point_count=2,
            length_km=0.05,
            geometry={"type": "LineString", "coordinates": coordinates},
        )
        profile = [
            {"latitude": 45.9, "longitude": 6.87, "distance_km": 0.0,
             "elevation_m": 2400.0},
            {"latitude": 45.9, "longitude": 6.87, "distance_km": 0.05,
             "elevation_m": 2400.0},
        ]
        terrain = {
            "source": "Open-Meteo",
            "profile": profile,
            "metrics": {"max_elevation_m": 2400.0, "elevation_gain_m": 0.0},
        }
        reading = weather.normalize_weather_response(
            {"current": {"temperature_2m": 1.0, "weather_code": 0},
             "hourly": {"time": []}}
        )
        merged = weather.aggregate_route_weather(
            [({"labels": ["start"], "latitude": 45.9, "longitude": 6.87,
               "elevation_m": 2400.0}, reading)]
        )
        route_weather = AsyncMock(return_value=merged)
        midpoint = AsyncMock(return_value=reading)
        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=way)
        ), patch.object(
            trails, "get_elevation_profile", new=AsyncMock(return_value=terrain)
        ), patch.object(
            trails, "get_route_weather", new=route_weather
        ), patch.object(trails, "get_weather", new=midpoint):
            payload = asyncio.run(
                trails.get_selected_trail_intelligence(
                    trails.TrailIntelligenceRequest(
                        trail={"osm_type": "way", "osm_id": 7,
                               "map_ready": True, "geometry": way.geometry}
                    )
                )
            )
        route_weather.assert_awaited_once()
        (points,), _ = route_weather.call_args
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["elevation_m"], 2400.0)
        # One point is not "the worst case across" anything.
        self.assertNotIn(
            "worst case", payload["weather_coordinate"]["basis"].lower()
        )


if __name__ == "__main__":
    unittest.main()
