"""
Gear follows the trip, and says what it followed.

Warm at the trailhead and freezing by the top used to be prepared for as the
trailhead. Cold, wind and snow are read from the worst of the estimated walking
window; heat still comes from now. The reasons name their source ("coldest
forecast temperature over the next 6 h") so a forecast is never described as a
current reading.
"""

from __future__ import annotations

import unittest

from app.services.intelligence import condition_likelihood, gear_recommendations


def _weather(temp=18.0, wind=5.0, window=None, inference=None) -> dict:
    payload = {
        "current": {"temperature": temp, "wind_speed": wind, "snowfall": 0.0,
                    "precipitation": 0.0},
        "recent_rain": {"24h_mm": 0.0},
        "forecast": {"precipitation_probability_max": 5},
        "source": "Test",
    }
    if window is not None:
        payload["window"] = window
    if inference is not None:
        payload["inference"] = inference
    return payload


def _trail(gain=900.0, slope=12.0) -> dict:
    return {
        "surface": "gravel",
        "terrain": {"metrics": {"elevation_gain_m": gain, "elevation_loss_m": gain,
                                "elevation_range_m": 700.0, "max_slope_percent": slope}},
    }


def _items(weather: dict, trail: dict | None = None) -> dict[str, dict]:
    trail = trail or _trail()
    condition = condition_likelihood(trail, trail["terrain"], weather)
    result = gear_recommendations(trail, {"distance_km": 12.0}, weather, condition)
    return {item["need"]: item for item in result["items"]}


WINDOW_COLD = {"hours": 6.0, "min_temperature": -4.0, "max_wind_speed": 12.0}


class ColdInTheWindowTests(unittest.TestCase):
    def test_warm_now_but_freezing_later_still_gets_cold_gear(self) -> None:
        items = _items(_weather(temp=18.0, window=WINDOW_COLD))
        self.assertIn("thermal_layer", items)
        self.assertIn("insulation", items)

    def test_the_reason_names_the_forecast_and_the_window(self) -> None:
        item = _items(_weather(temp=18.0, window=WINDOW_COLD))["insulation"]
        self.assertIn("coldest forecast temperature", item["reason"])
        self.assertIn("next 6 h", item["reason"])
        self.assertIn("-4.0", item["reason"])
        self.assertTrue(any(
            e.startswith("coldest forecast temperature") for e in item["evidence"]
        ))
        self.assertFalse(any("current temperature: -4" in e for e in item["evidence"]))

    def test_the_reasons_read_as_one_sentence(self) -> None:
        window = {"hours": 5.3, "min_temperature": 2.0, "max_wind_speed": 45.0}
        items = _items(_weather(temp=14.0, wind=4.0, window=window))
        for need in ("thermal_layer", "insulation", "wind_layer"):
            reason = items[need]["reason"]
            self.assertNotIn("on the route on this route", reason, need)
            self.assertLessEqual(
                reason.count("on this route") + reason.count("on the route"),
                1,
                need,
            )
        self.assertIn("over the next 5 h on this route is 2.0", items["thermal_layer"]["reason"])

    def test_without_a_window_the_wording_is_unchanged(self) -> None:
        item = _items(_weather(temp=3.0))["insulation"]
        self.assertIn("The current temperature is 3.0", item["reason"])
        self.assertIn("current temperature: 3.0 °C", item["evidence"])

    def test_the_current_reading_still_wins_when_it_is_colder(self) -> None:
        window = {"hours": 6.0, "min_temperature": 2.0}
        item = _items(_weather(temp=-1.0, window=window))["insulation"]
        self.assertIn("The current temperature is -1.0", item["reason"])

    def test_a_warm_window_adds_no_cold_gear(self) -> None:
        items = _items(_weather(temp=18.0, window={"hours": 6.0, "min_temperature": 15.0}))
        self.assertNotIn("thermal_layer", items)
        self.assertNotIn("insulation", items)

    def test_expedition_cold_is_read_from_the_window_too(self) -> None:
        window = {"hours": 8.0, "min_temperature": -14.0, "max_wind_speed": 30.0}
        item = _items(_weather(temp=2.0, window=window))["insulation"]
        self.assertIn("Expedition insulation", item["item"])
        self.assertIn("coldest forecast temperature", item["reason"])


class HeatIsStillNowTests(unittest.TestCase):
    def test_a_cold_window_does_not_remove_sun_protection_when_hot_now(self) -> None:
        window = {"hours": 6.0, "min_temperature": 8.0}
        items = _items(_weather(temp=31.0, window=window))
        self.assertIn("sun_protection", items)


class WindInTheWindowTests(unittest.TestCase):
    def test_calm_now_but_windy_later_gets_a_wind_layer(self) -> None:
        window = {"hours": 6.0, "min_temperature": 12.0, "max_wind_speed": 48.0}
        item = _items(_weather(temp=14.0, wind=4.0, window=window))["wind_layer"]
        self.assertIn("strongest forecast wind", item["reason"])
        self.assertIn("48.0", item["reason"])

    def test_current_wind_wording_is_unchanged(self) -> None:
        item = _items(_weather(temp=14.0, wind=35.0))["wind_layer"]
        self.assertIn("Current wind on this route is 35.0 km/h", item["reason"])

    def test_a_calm_window_adds_nothing(self) -> None:
        window = {"hours": 6.0, "min_temperature": 12.0, "max_wind_speed": 10.0}
        self.assertNotIn("wind_layer", _items(_weather(temp=14.0, wind=4.0, window=window)))


SNOWY = {
    "snow_on_route_likely": True,
    "snow_reasons": ["about 40 cm of snow lying at the high points"],
    "basis": "Forecast read at points along the route. This is inferred from the "
             "forecast; conditions on the trail itself are not observed.",
}


class InferredSnowTests(unittest.TestCase):
    def test_inferred_snow_on_a_steep_climb_asks_for_traction(self) -> None:
        item = _items(
            _weather(temp=6.0, window={"hours": 6.0}, inference=SNOWY),
            _trail(gain=900.0, slope=40.0),
        )["winter_equipment"]
        self.assertEqual(item["priority"], "essential")
        self.assertIn("Ice axe or crampons", item["item"])
        self.assertIn("likely", item["reason"])
        self.assertIn("inferred from the forecast", item["reason"])
        self.assertIn("about 40 cm of snow lying at the high points", item["evidence"])

    def test_inferred_snow_on_easy_ground_asks_for_boots_and_layers(self) -> None:
        item = _items(
            _weather(temp=6.0, window={"hours": 6.0}, inference=SNOWY),
            _trail(gain=80.0, slope=8.0),
        )["winter_equipment"]
        self.assertEqual(item["priority"], "recommended")
        self.assertIn("Waterproof boots", item["item"])

    def test_no_inferred_snow_means_no_winter_gear(self) -> None:
        quiet = dict(SNOWY, snow_on_route_likely=False, snow_reasons=[])
        items = _items(_weather(temp=6.0, window={"hours": 6.0}, inference=quiet),
                       _trail(slope=40.0))
        self.assertNotIn("winter_equipment", items)

    def test_current_snow_and_the_forecast_are_both_cited(self) -> None:
        weather = _weather(temp=-2.0, window={"hours": 16.0}, inference=SNOWY)
        weather["current"]["snowfall"] = 0.6
        item = _items(weather, _trail(gain=900.0, slope=40.0))["winter_equipment"]
        self.assertIn("current snowfall: 0.6 cm", item["evidence"])
        self.assertIn("about 40 cm of snow lying at the high points", item["evidence"])
        # Snow is falling now, so it is reported and not merely inferred.
        self.assertIn("Snow is reported on this route", item["reason"])

    def test_snow_falling_now_keeps_its_own_wording(self) -> None:
        weather = _weather(temp=-1.0)
        weather["current"]["snowfall"] = 0.6
        item = _items(weather, _trail(gain=900.0, slope=40.0))["winter_equipment"]
        self.assertIn("Snow is reported on this route", item["reason"])
        self.assertIn("current snowfall: 0.6 cm", item["evidence"])


if __name__ == "__main__":
    unittest.main()
