"""
Weather for a route should track the trip, not only the present.

The weather was the reading right now plus a 24 h rain total, so a route
that is warm at the start and freezing by the top (or calm now and windy
by noon) was prepared for as the start. The route now has a time window
(estimated walking time), the worst hourly values inside it, and a
freezing-level comparison against its highest point. All of it is inferred
from the forecast at sample points, and labelled that way.

Open-Meteo returns freezing_level_height, snow_depth, wind_gusts_10m and
snowfall hourly and honours per-location elevation (verified live: snow depth
0 m at 1,400 m and 1.04 m at 4,200 m on the same day).
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app.services import weather

CURRENT_INDEX = 84  # 2026-09-29T12:00 when the hourly series starts 3 days back
START = datetime(2026, 9, 26, 0, 0)


def _hourly(**overrides) -> dict:
    hours = 120
    times = [(START + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(hours)]
    base = {
        "time": times,
        "precipitation": [0.0] * hours,
        "rain": [0.0] * hours,
        "precipitation_probability": [10] * hours,
        "temperature_2m": [10.0] * hours,
        "wind_speed_10m": [10.0] * hours,
        "wind_gusts_10m": [15.0] * hours,
        "snowfall": [0.0] * hours,
        "snow_depth": [0.0] * hours,
        "freezing_level_height": [3000.0] * hours,
    }
    base.update(overrides)
    return base


def _data(hourly: dict, **current) -> dict:
    cur = {
        "time": "2026-09-29T12:30",
        "temperature_2m": 10.0,
        "relative_humidity_2m": 50,
        "precipitation": 0.0,
        "rain": 0.0,
        "showers": 0.0,
        "snowfall": 0.0,
        "weather_code": 0,
        "wind_speed_10m": 10.0,
    }
    cur.update(current)
    return {"latitude": 45.9, "longitude": 6.87, "timezone": "Europe/Paris",
            "current": cur, "hourly": hourly}


def _series(default: float, at: dict[int, float]) -> list[float]:
    values = [default] * 120
    for index, value in at.items():
        values[index] = value
    return values


class WalkingTimeTests(unittest.TestCase):
    def test_naismith_rule(self) -> None:
        # 5 km/h on the flat plus one hour per 600 m of ascent.
        self.assertAlmostEqual(weather.estimate_walking_hours(10.0, 600.0), 3.0)
        self.assertAlmostEqual(weather.estimate_walking_hours(20.0, 0.0), 4.0)

    def test_unknown_ascent_uses_the_distance_alone(self) -> None:
        self.assertAlmostEqual(weather.estimate_walking_hours(10.0, None), 2.0)

    def test_it_is_bounded_to_what_the_forecast_can_cover(self) -> None:
        self.assertEqual(weather.estimate_walking_hours(0.1, 0.0), 1.0)
        self.assertEqual(weather.estimate_walking_hours(166.0, 9000.0), 24.0)

    def test_no_distance_gives_no_window(self) -> None:
        self.assertIsNone(weather.estimate_walking_hours(None, 500.0))
        self.assertIsNone(weather.estimate_walking_hours(0.0, 500.0))


class WindowTests(unittest.TestCase):
    def _window(self, hours: float = 6.0, **series) -> dict:
        normalised = weather.normalize_weather_response(
            _data(_hourly(**series)), window_hours=hours
        )
        return normalised["window"]

    def test_the_worst_hourly_values_inside_the_window_are_taken(self) -> None:
        window = self._window(
            6.0,
            temperature_2m=_series(10.0, {CURRENT_INDEX + 5: -4.0}),
            wind_speed_10m=_series(10.0, {CURRENT_INDEX + 3: 45.0}),
            wind_gusts_10m=_series(15.0, {CURRENT_INDEX + 3: 70.0}),
            precipitation=_series(0.0, {CURRENT_INDEX + 2: 2.5}),
        )
        self.assertEqual(window["hours"], 6.0)
        self.assertEqual(window["min_temperature"], -4.0)
        self.assertEqual(window["max_wind_speed"], 45.0)
        self.assertEqual(window["max_wind_gust"], 70.0)
        self.assertEqual(window["max_precipitation_mm"], 2.5)

    def test_hours_after_the_window_are_ignored(self) -> None:
        window = self._window(
            3.0, temperature_2m=_series(10.0, {CURRENT_INDEX + 8: -20.0})
        )
        self.assertEqual(window["min_temperature"], 10.0)

    def test_hours_before_now_are_not_the_window(self) -> None:
        window = self._window(
            3.0, temperature_2m=_series(10.0, {CURRENT_INDEX - 2: -20.0})
        )
        self.assertEqual(window["min_temperature"], 10.0)

    def test_snow_and_freezing_level(self) -> None:
        window = self._window(
            6.0,
            snowfall=_series(0.0, {CURRENT_INDEX + 2: 0.2, CURRENT_INDEX + 4: 0.3}),
            snow_depth=_series(0.03, {CURRENT_INDEX + 1: 0.12}),
            freezing_level_height=_series(3000.0, {CURRENT_INDEX + 4: 2400.0}),
        )
        self.assertAlmostEqual(window["snowfall_cm"], 0.5)
        self.assertEqual(window["max_snow_depth_m"], 0.12)
        self.assertEqual(window["min_freezing_level_m"], 2400.0)

    def test_recent_snowfall_is_the_three_days_before_now(self) -> None:
        window = self._window(
            6.0,
            snowfall=_series(
                0.0,
                {CURRENT_INDEX - 10: 1.0, CURRENT_INDEX - 30: 2.0,
                 CURRENT_INDEX + 2: 9.0},  # the future is not "recent"
            ),
        )
        self.assertAlmostEqual(window["recent_snowfall_cm_72h"], 3.0)

    def test_without_a_window_nothing_changes(self) -> None:
        normalised = weather.normalize_weather_response(_data(_hourly()))
        self.assertNotIn("window", normalised)

    def test_missing_series_are_none_not_zero(self) -> None:
        hourly = _hourly()
        for key in ("snow_depth", "freezing_level_height", "wind_gusts_10m"):
            del hourly[key]
        window = weather.normalize_weather_response(
            _data(hourly), window_hours=6.0
        )["window"]
        self.assertIsNone(window["max_snow_depth_m"])
        self.assertIsNone(window["min_freezing_level_m"])
        self.assertIsNone(window["max_wind_gust"])


def _sample_with_window(temp, freezing, snow_depth=0.0, snowfall=0.0,
                        recent=0.0, wind=10.0) -> dict:
    hourly = _hourly(
        temperature_2m=_series(temp, {}),
        wind_speed_10m=_series(wind, {}),
        freezing_level_height=_series(freezing, {}),
        snow_depth=_series(snow_depth, {}),
        snowfall=_series(0.0, {CURRENT_INDEX + 1: snowfall, CURRENT_INDEX - 5: recent}),
    )
    return weather.normalize_weather_response(_data(hourly), window_hours=6.0)


def _point(label: str, elevation: float) -> dict:
    return {"labels": [label], "latitude": 45.9, "longitude": 6.87,
            "elevation_m": elevation}


class InferenceTests(unittest.TestCase):
    def _merge(self, readings, highest=3000.0, hours=6.0):
        return weather.aggregate_route_weather(
            readings, window_hours=hours, highest_point_m=highest
        )

    def test_the_window_is_the_worst_across_points(self) -> None:
        merged = self._merge(
            [
                (_point("start", 1400), _sample_with_window(14.0, 3500.0)),
                (_point("highest", 3000), _sample_with_window(-3.0, 3500.0, wind=40.0)),
            ]
        )
        self.assertEqual(merged["window"]["min_temperature"], -3.0)
        self.assertEqual(merged["window"]["max_wind_speed"], 40.0)

    def test_a_route_that_climbs_above_the_freezing_level_says_so(self) -> None:
        merged = self._merge(
            [(_point("highest", 3000), _sample_with_window(1.0, 2400.0))],
            highest=3000.0,
        )
        inference = merged["inference"]
        self.assertTrue(inference["upper_route_above_freezing_level"])
        self.assertEqual(inference["freezing_level_m"], 2400.0)
        self.assertEqual(inference["highest_point_m"], 3000.0)
        self.assertEqual(inference["margin_m"], 600.0)

    def test_a_route_below_the_freezing_level_does_not(self) -> None:
        merged = self._merge(
            [(_point("highest", 2000), _sample_with_window(12.0, 3500.0))],
            highest=2000.0,
        )
        self.assertFalse(merged["inference"]["upper_route_above_freezing_level"])
        self.assertFalse(merged["inference"]["snow_on_route_likely"])

    def test_snow_lying_at_a_sample_makes_snow_likely(self) -> None:
        merged = self._merge(
            [(_point("highest", 3000), _sample_with_window(-2.0, 2400.0, snow_depth=0.4))]
        )
        inference = merged["inference"]
        self.assertTrue(inference["snow_on_route_likely"])
        self.assertTrue(any("snow" in reason for reason in inference["snow_reasons"]))

    def test_snow_expected_in_the_window_makes_snow_likely(self) -> None:
        merged = self._merge(
            [(_point("highest", 3000), _sample_with_window(0.0, 2400.0, snowfall=1.5))]
        )
        self.assertTrue(merged["inference"]["snow_on_route_likely"])

    def test_recent_snow_needs_a_freezing_level_below_the_top(self) -> None:
        cold = self._merge(
            [(_point("highest", 3000), _sample_with_window(0.0, 2400.0, recent=4.0))]
        )
        warm = self._merge(
            [(_point("highest", 3000), _sample_with_window(8.0, 3600.0, recent=4.0))]
        )
        self.assertTrue(cold["inference"]["snow_on_route_likely"])
        self.assertFalse(warm["inference"]["snow_on_route_likely"])

    def test_it_is_labelled_as_a_forecast_inference(self) -> None:
        merged = self._merge(
            [(_point("highest", 3000), _sample_with_window(1.0, 2400.0))]
        )
        inference = merged["inference"]
        self.assertIn("forecast", inference["basis"].lower())
        self.assertIn("not observed", inference["basis"].lower())
        self.assertIn("naismith", inference["window_basis"].lower())
        self.assertEqual(inference["window_hours"], 6.0)

    def test_without_a_window_there_is_no_inference(self) -> None:
        merged = weather.aggregate_route_weather(
            [(_point("start", 1400), _sample_with_window(10.0, 3000.0))]
        )
        self.assertNotIn("inference", merged)
        self.assertNotIn("window", merged)

    def test_samples_carry_their_own_window_readings(self) -> None:
        merged = self._merge(
            [
                (_point("start", 1400), _sample_with_window(14.0, 3500.0)),
                (_point("highest", 3000), _sample_with_window(-3.0, 3500.0)),
            ]
        )
        self.assertEqual(merged["samples"][1]["window_min_temperature"], -3.0)


class RequestTests(unittest.TestCase):
    def setUp(self) -> None:
        weather._WEATHER_CACHE.clear()
        weather._WEATHER_INFLIGHT.clear()
        weather.open_meteo_breaker.reset()

    def _fetch(self, window_hours):
        captured: list[dict] = []
        payload = [_data(_hourly()), _data(_hourly())]

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return payload

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

        points = [_point("start", 1400), _point("highest", 3000)]
        with patch.object(weather.httpx, "AsyncClient", _Client):
            result = asyncio.run(
                weather.get_route_weather(
                    points, window_hours=window_hours
                )
            )
        return captured, result

    def test_the_forecast_variables_and_horizon_are_requested(self) -> None:
        captured, result = self._fetch(6.0)
        hourly = captured[0]["hourly"].split(",")
        for name in ("temperature_2m", "wind_speed_10m", "wind_gusts_10m",
                     "snowfall", "snow_depth", "freezing_level_height"):
            self.assertIn(name, hourly)
        self.assertGreaterEqual(captured[0]["forecast_days"], 2)
        self.assertEqual(result["window"]["hours"], 6.0)
        self.assertEqual(result["inference"]["highest_point_m"], 3000.0)

    def test_different_windows_are_not_served_from_one_cache_entry(self) -> None:
        self._fetch(3.0)
        _, result = self._fetch(9.0)
        self.assertEqual(result["window"]["hours"], 9.0)


class RouteWiringTests(unittest.TestCase):
    """The intelligence route turns distance and ascent into a window."""

    def _run(self, terrain):
        from unittest.mock import AsyncMock

        from app.routes import trails
        from app.services import postpass

        line = [[6.87, 45.9 + 0.01 * i] for i in range(11)]
        way = postpass.PostpassWay(
            way_id=7, name="Col Route", route=None, highway="path",
            sac_scale="mountain_hiking", trail_visibility=None, surface=None,
            smoothness=None, tracktype=None, access=None, incline=None,
            incline_direction=None, width=None, assisted_trail=None,
            aliases=[], geometry_type="LineString", point_count=11,
            length_km=11.1, geometry={"type": "LineString", "coordinates": line},
        )
        route_weather = AsyncMock(return_value={
            "source": "Open-Meteo", "current": {"temperature": 5.0, "wind_speed": 5.0},
            "aggregation": "worst_case", "sample_count": 2, "samples": [],
        })
        single = AsyncMock(return_value={
            "source": "Open-Meteo", "current": {"temperature": 5.0, "wind_speed": 5.0},
        })
        elevation = (
            AsyncMock(return_value=terrain)
            if terrain is not None
            else AsyncMock(side_effect=RuntimeError("down"))
        )
        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=way)
        ), patch.object(
            trails, "get_elevation_profile", new=elevation
        ), patch.object(
            trails, "get_route_weather", new=route_weather
        ), patch.object(trails, "get_weather", new=single):
            asyncio.run(
                trails.get_selected_trail_intelligence(
                    trails.TrailIntelligenceRequest(
                        trail={"osm_type": "way", "osm_id": 7,
                               "map_ready": True, "geometry": way.geometry}
                    )
                )
            )
        return route_weather, single, way

    def _profile(self):
        return [
            {"distance_km": 0.0, "latitude": 45.90, "longitude": 6.87, "elevation_m": 1500.0},
            {"distance_km": 5.5, "latitude": 45.95, "longitude": 6.87, "elevation_m": 3000.0},
            {"distance_km": 11.1, "latitude": 46.00, "longitude": 6.87, "elevation_m": 1200.0},
        ]

    def test_the_route_window_uses_distance_and_ascent(self) -> None:
        terrain = {"source": "Open-Meteo", "profile": self._profile(),
                   "metrics": {"elevation_gain_m": 1500.0, "max_elevation_m": 3000.0}}
        route_weather, _, _ = self._run(terrain)
        hours = route_weather.await_args.kwargs["window_hours"]
        # 11.1 km / 5 + 1500 m / 600 = 2.22 + 2.5
        self.assertAlmostEqual(hours, 11.1 / 5 + 2.5, places=1)

    def test_the_fallback_reading_uses_distance_alone(self) -> None:
        # Ascent is unknown until elevation arrives, and the fallback starts
        # before that, so it says so by using the distance only.
        terrain = {"source": "Open-Meteo", "profile": self._profile(),
                   "metrics": {"elevation_gain_m": 1500.0}}
        _, single, _ = self._run(terrain)
        self.assertAlmostEqual(
            single.await_args.kwargs["window_hours"], 11.1 / 5, places=1
        )

    def test_without_elevation_the_fallback_still_has_a_window(self) -> None:
        route_weather, single, _ = self._run(None)
        route_weather.assert_not_awaited()
        self.assertGreater(single.await_args.kwargs["window_hours"], 1.0)


if __name__ == "__main__":
    unittest.main()
