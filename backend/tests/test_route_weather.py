"""
Weather for a route must come from along the route, not one point on it.

Live check: at the same moment Chamonix (1,035 m) read 17.9 C, 3,800 m read
2.5 C and 4,805 m read -5.0 C. Weather was taken at the route's midpoint with
the provider's own idea of that point's height, so a route that climbs into
the cold was prepared for as if it stayed in the valley.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.routes import trails
from app.services import weather
from app.services.intelligence import condition_likelihood, gear_recommendations


def _point(distance: float, lat: float, elevation: float | None) -> dict:
    return {
        "distance_km": distance,
        "latitude": lat,
        "longitude": 6.87,
        "elevation_m": elevation,
    }


# Starts at 1,500 m, climbs to 3,000 m, ends low at 1,200 m.
PROFILE = [
    _point(0.0, 45.90, 1500.0),
    _point(4.0, 45.92, 2200.0),
    _point(8.0, 45.94, 3000.0),
    _point(12.0, 45.96, 2000.0),
    _point(16.0, 45.98, 1200.0),
]


def _sample(temp, wind, snow=0.0, precip=0.0, code=0, rain24=0.0) -> dict:
    return {
        "source": "Open-Meteo",
        "latitude": 45.9,
        "longitude": 6.87,
        "timezone": "Europe/Paris",
        "current": {
            "time": "2026-09-29T12:00",
            "temperature": temp,
            "humidity": 50.0,
            "precipitation": precip,
            "rain": 0.0,
            "showers": 0.0,
            "snowfall": snow,
            "precipitation_probability": 10,
            "wind_speed": wind,
            "weather_code": code,
            "weather_condition": weather.weather_code_description(code),
        },
        "recent_precipitation": {"24h_mm": rain24, "48h_mm": 0.0, "72h_mm": 0.0},
        "recent_rain": {"24h_mm": rain24, "48h_mm": 0.0, "72h_mm": 0.0},
        "forecast": {
            "precipitation_mm": 0.0,
            "rain_mm": 0.0,
            "precipitation_probability_max": 20,
        },
    }


class SelectRoutePointsTests(unittest.TestCase):
    def test_it_samples_start_highest_lowest_end_and_midpoint(self) -> None:
        points = weather.select_route_points(PROFILE)
        by_label = {
            label: point for point in points for label in point["labels"]
        }
        self.assertEqual(by_label["start"]["elevation_m"], 1500.0)
        self.assertEqual(by_label["highest"]["elevation_m"], 3000.0)
        # The end is also the lowest point, so it is one sample, two labels.
        self.assertEqual(by_label["lowest"]["elevation_m"], 1200.0)
        self.assertIs(by_label["lowest"], by_label["end"])
        self.assertIn("midpoint", by_label)

    def test_every_sample_carries_the_elevation_it_is_read_at(self) -> None:
        for point in weather.select_route_points(PROFILE):
            self.assertIsNotNone(point["elevation_m"])

    def test_points_that_are_effectively_the_same_place_collapse(self) -> None:
        flat = [
            _point(0.0, 45.9000, 1575.0),
            _point(0.13, 45.9005, 1577.0),
            _point(0.26, 45.9010, 1583.0),
        ]
        self.assertEqual(len(weather.select_route_points(flat)), 1)

    def test_a_profile_without_elevations_gives_no_points(self) -> None:
        self.assertEqual(
            weather.select_route_points([_point(0.0, 45.9, None)] * 3), []
        )
        self.assertEqual(weather.select_route_points([]), [])


class AggregateTests(unittest.TestCase):
    def test_the_route_is_as_cold_and_windy_as_its_worst_point(self) -> None:
        valley = {"labels": ["start"], "latitude": 45.9, "longitude": 6.87,
                  "elevation_m": 1035.0}
        summit = {"labels": ["highest"], "latitude": 45.86, "longitude": 6.86,
                  "elevation_m": 3800.0}
        merged = weather.aggregate_route_weather(
            [
                (valley, _sample(17.9, 2.5, code=1)),
                (summit, _sample(-5.0, 40.0, snow=0.6, precip=0.6, code=73,
                                 rain24=3.0)),
            ]
        )
        current = merged["current"]
        self.assertEqual(current["temperature"], -5.0)
        self.assertEqual(current["wind_speed"], 40.0)
        self.assertEqual(current["snowfall"], 0.6)
        self.assertEqual(current["weather_code"], 73)
        self.assertEqual(current["weather_condition"], "Moderate snowfall")
        self.assertEqual(merged["recent_rain"]["24h_mm"], 3.0)
        self.assertEqual(merged["aggregation"], "worst_case")
        self.assertEqual(len(merged["samples"]), 2)
        self.assertEqual(merged["samples"][1]["elevation_m"], 3800.0)
        self.assertEqual(merged["samples"][1]["temperature"], -5.0)

    def test_the_weather_code_is_the_most_severe_not_the_highest_number(self) -> None:
        point = {"labels": ["start"], "latitude": 1.0, "longitude": 1.0,
                 "elevation_m": 100.0}
        # 80 (slight rain showers) is numerically above 73 (moderate snow),
        # but snow is the harsher weather.
        merged = weather.aggregate_route_weather(
            [(point, _sample(2.0, 5.0, code=80)), (point, _sample(-3.0, 5.0, code=73))]
        )
        self.assertEqual(merged["current"]["weather_code"], 73)
        self.assertEqual(merged["current"]["weather_condition"], "Moderate snowfall")

    def test_a_thunderstorm_outranks_everything(self) -> None:
        point = {"labels": ["start"], "latitude": 1.0, "longitude": 1.0,
                 "elevation_m": 100.0}
        merged = weather.aggregate_route_weather(
            [(point, _sample(2.0, 5.0, code=75)), (point, _sample(9.0, 5.0, code=95))]
        )
        self.assertEqual(merged["current"]["weather_code"], 95)

    def test_a_missing_reading_does_not_hide_another_points_reading(self) -> None:
        a = _sample(None, None)
        b = _sample(4.0, 12.0)
        point = {"labels": ["start"], "latitude": 1.0, "longitude": 1.0,
                 "elevation_m": 100.0}
        merged = weather.aggregate_route_weather([(point, a), (point, b)])
        self.assertEqual(merged["current"]["temperature"], 4.0)
        self.assertEqual(merged["current"]["wind_speed"], 12.0)

    def test_gear_prepares_for_the_cold_summit_not_the_warm_valley(self) -> None:
        valley = {"labels": ["start"], "latitude": 45.9, "longitude": 6.87,
                  "elevation_m": 1035.0}
        summit = {"labels": ["highest"], "latitude": 45.86, "longitude": 6.86,
                  "elevation_m": 3800.0}
        merged = weather.aggregate_route_weather(
            [(valley, _sample(17.9, 2.5)), (summit, _sample(-5.0, 30.0))]
        )
        trail = {"terrain": {"metrics": {"elevation_gain_m": 900.0}}}
        condition = condition_likelihood(trail, trail["terrain"], merged)
        needs = {
            item["need"]
            for item in gear_recommendations(
                trail, {"distance_km": 10.0}, merged, condition
            )["items"]
        }
        self.assertTrue(needs & {"thermal_layer", "insulation"})
        self.assertIn("wind_layer", needs)


class _Response:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def _location_payload(temp: float, elevation: float) -> dict:
    return {
        "latitude": 45.9,
        "longitude": 6.87,
        "elevation": elevation,
        "timezone": "Europe/Paris",
        "current": {
            "time": "2026-09-29T12:00",
            "temperature_2m": temp,
            "relative_humidity_2m": 50,
            "precipitation": 0.0,
            "rain": 0.0,
            "showers": 0.0,
            "snowfall": 0.0,
            "weather_code": 0,
            "wind_speed_10m": 5.0,
        },
        "hourly": {
            "time": ["2026-09-29T12:00"],
            "precipitation": [0.0],
            "rain": [0.0],
            "precipitation_probability": [10],
        },
    }


class GetRouteWeatherTests(unittest.TestCase):
    def setUp(self) -> None:
        weather._WEATHER_CACHE.clear()
        weather._WEATHER_INFLIGHT.clear()

    def test_all_points_cost_one_provider_call_at_their_own_elevations(self) -> None:
        requests: list[dict] = []

        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                return None

            async def get(self, url, params=None):
                requests.append(params)
                return _Response(
                    [
                        _location_payload(15.0, 1500.0),
                        _location_payload(-4.0, 3000.0),
                    ]
                )

        points = [
            {"labels": ["start"], "latitude": 45.90, "longitude": 6.87,
             "elevation_m": 1500.0},
            {"labels": ["highest"], "latitude": 45.94, "longitude": 6.87,
             "elevation_m": 3000.0},
        ]
        with patch.object(weather.httpx, "AsyncClient", _Client):
            result = asyncio.run(weather.get_route_weather(points))

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["elevation"], "1500,3000")
        self.assertEqual(requests[0]["latitude"], "45.9,45.94")
        self.assertEqual(result["current"]["temperature"], -4.0)
        self.assertEqual(result["sample_count"], 2)

    def test_a_provider_failure_is_reported_like_any_weather_failure(self) -> None:
        class _Client:
            def __init__(self, *a, **k) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> None:
                return None

            async def get(self, url, params=None):
                raise httpx.ConnectError("down")

        points = [
            {"labels": ["start"], "latitude": 45.9, "longitude": 6.87,
             "elevation_m": 1500.0},
            {"labels": ["end"], "latitude": 45.95, "longitude": 6.87,
             "elevation_m": 900.0},
        ]
        with patch.object(weather.httpx, "AsyncClient", _Client):
            with self.assertRaises(Exception) as caught:
                asyncio.run(weather.get_route_weather(points))
        self.assertEqual(getattr(caught.exception, "status_code", None), 502)


class IntelligenceUsesRouteWeatherTests(unittest.TestCase):
    def test_a_climbing_route_is_weathered_along_the_route(self) -> None:
        from app.services import postpass

        way = postpass.PostpassWay(
            way_id=7, name="Col Route", route=None, highway="path",
            sac_scale="mountain_hiking", trail_visibility=None, surface=None,
            smoothness=None, tracktype=None, access=None, incline=None,
            incline_direction=None, width=None, assisted_trail=None,
            aliases=[], geometry_type="LineString", point_count=5,
            length_km=16.0,
            geometry={
                "type": "LineString",
                "coordinates": [[6.87, p["latitude"]] for p in PROFILE],
            },
        )
        terrain = {
            "source": "Open-Meteo",
            "profile": PROFILE,
            "metrics": {"max_elevation_m": 3000.0, "elevation_gain_m": 1500.0},
        }
        merged = weather.aggregate_route_weather(
            [
                ({"labels": ["start"], "latitude": 45.9, "longitude": 6.87,
                  "elevation_m": 1500.0}, _sample(15.0, 3.0)),
                ({"labels": ["highest"], "latitude": 45.94, "longitude": 6.87,
                  "elevation_m": 3000.0}, _sample(-3.0, 35.0)),
            ]
        )
        route_weather = AsyncMock(return_value=merged)
        single = AsyncMock(return_value=_sample(15.0, 3.0))
        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=way)
        ), patch.object(
            trails, "get_elevation_profile", new=AsyncMock(return_value=terrain)
        ), patch.object(
            trails, "get_route_weather", new=route_weather
        ), patch.object(trails, "get_weather", new=single):
            payload = asyncio.run(
                trails.get_selected_trail_intelligence(
                    trails.TrailIntelligenceRequest(
                        trail={
                            "osm_type": "way",
                            "osm_id": 7,
                            "map_ready": True,
                            "geometry": way.geometry,
                        }
                    )
                )
            )
        route_weather.assert_awaited_once()
        # The midpoint reading is started alongside elevation as a fallback and
        # is not used once the route has real points to read.
        self.assertEqual(payload["weather"]["current"]["temperature"], -3.0)
        self.assertEqual(len(payload["weather_samples"]), 2)
        basis = payload["weather_coordinate"]["basis"].lower()
        self.assertIn("midpoint", basis)
        self.assertIn("worst case", basis)
        needs = {item["need"] for item in payload["gear"]["items"]}
        self.assertTrue(needs & {"thermal_layer", "insulation"})


class ParallelFetchTests(unittest.TestCase):
    """
    Elevation says where to read the weather, but a hung provider must not be
    waited on twice: measured 50 s for elevation then weather, against 30 s when
    they ran together.
    """

    def _way(self):
        from app.services import postpass

        return postpass.PostpassWay(
            way_id=7, name="Col Route", route=None, highway="path",
            sac_scale="mountain_hiking", trail_visibility=None, surface=None,
            smoothness=None, tracktype=None, access=None, incline=None,
            incline_direction=None, width=None, assisted_trail=None,
            aliases=[], geometry_type="LineString", point_count=2,
            length_km=1.0,
            geometry={"type": "LineString", "coordinates": [[6.87, 45.9], [6.88, 45.91]]},
        )

    def test_a_slow_failing_provider_is_waited_on_once_not_twice(self) -> None:
        import time

        async def slow_failure(*args, **kwargs):
            await asyncio.sleep(0.4)
            raise RuntimeError("provider hung")

        async def instant_failure(*args, **kwargs):
            raise RuntimeError("provider down")

        way = self._way()
        request = trails.TrailIntelligenceRequest(
            trail={
                "osm_type": "way",
                "osm_id": 7,
                "map_ready": True,
                "geometry": way.geometry,
            }
        )
        # Warm the difficulty model so its one-off load is not timed.
        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=way)
        ), patch.object(
            trails, "get_elevation_profile", new=instant_failure
        ), patch.object(trails, "get_weather", new=instant_failure):
            asyncio.run(trails.get_selected_trail_intelligence(request))

        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=way)
        ), patch.object(
            trails, "get_elevation_profile", new=slow_failure
        ), patch.object(trails, "get_weather", new=slow_failure):
            started = time.perf_counter()
            payload = asyncio.run(
                trails.get_selected_trail_intelligence(request)
            )
            elapsed = time.perf_counter() - started
        self.assertEqual(
            payload["providers"],
            {"weather": "unavailable", "elevation": "unavailable"},
        )
        self.assertLess(elapsed, 0.7)

    def test_a_working_provider_still_uses_route_weather_once(self) -> None:
        merged = weather.aggregate_route_weather(
            [
                ({"labels": ["start"], "latitude": 45.9, "longitude": 6.87,
                  "elevation_m": 1500.0}, _sample(15.0, 3.0)),
                ({"labels": ["highest"], "latitude": 45.94, "longitude": 6.87,
                  "elevation_m": 3000.0}, _sample(-3.0, 35.0)),
            ]
        )
        terrain = {"source": "Open-Meteo", "profile": PROFILE,
                   "metrics": {"max_elevation_m": 3000.0}}
        way = self._way()
        route_weather = AsyncMock(return_value=merged)
        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=way)
        ), patch.object(
            trails, "get_elevation_profile", new=AsyncMock(return_value=terrain)
        ), patch.object(
            trails, "get_route_weather", new=route_weather
        ), patch.object(
            trails, "get_weather", new=AsyncMock(return_value=_sample(15.0, 3.0))
        ):
            payload = asyncio.run(
                trails.get_selected_trail_intelligence(
                    trails.TrailIntelligenceRequest(
                        trail={
                            "osm_type": "way",
                            "osm_id": 7,
                            "map_ready": True,
                            "geometry": way.geometry,
                        }
                    )
                )
            )
        route_weather.assert_awaited_once()
        self.assertEqual(payload["weather"]["aggregation"], "worst_case")


if __name__ == "__main__":
    unittest.main()
