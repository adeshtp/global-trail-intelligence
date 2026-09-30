"""
The status must describe the same trip the gear is prepared for.

Gear read the estimated walking window (cold, wind and snow) while the
condition and suitability status read only the current reading, so a route that
was warm at the trailhead and freezing by the top could be "favorable" directly
above expedition insulation. The condition engine now reads the same window and
names its source in every factor. Without a window nothing changes.
"""

from __future__ import annotations

import unittest

from app.services.intelligence import (
    condition_likelihood,
    gear_recommendations,
    route_suitability_context,
)


def _weather(temp=18.0, wind=5.0, window=None, inference=None) -> dict:
    payload = {
        "current": {"temperature": temp, "wind_speed": wind, "snowfall": 0.0,
                    "precipitation": 0.0, "weather_code": 0},
        "recent_rain": {"24h_mm": 0.0},
        "recent_precipitation": {"24h_mm": 0.0},
        "forecast": {"precipitation_probability_max": 5},
        "source": "Test",
    }
    if window is not None:
        payload["window"] = window
    if inference is not None:
        payload["inference"] = inference
    return payload


def _trail(gain=900.0) -> dict:
    return {
        "surface": "gravel",
        "terrain": {"metrics": {"elevation_gain_m": gain, "elevation_loss_m": gain,
                                "elevation_range_m": 700.0, "max_slope_percent": 12.0}},
    }


def _condition(weather: dict, trail: dict | None = None) -> dict:
    trail = trail or _trail()
    return condition_likelihood(trail, trail["terrain"], weather)


def _factors(condition: dict) -> dict[str, dict]:
    return {f["factor"]: f for f in condition["factors"]}


COLD_LATER = {"hours": 6.0, "min_temperature": -4.0, "max_wind_speed": 12.0}
SNOWY = {
    "snow_on_route_likely": True,
    "snow_reasons": ["about 40 cm of snow lying at the high points"],
}


class ColdInTheWindowTests(unittest.TestCase):
    def test_warm_now_but_freezing_later_is_not_favorable(self) -> None:
        condition = _condition(_weather(temp=18.0, window=COLD_LATER))
        self.assertNotEqual(condition["status"], "favorable")
        self.assertEqual(_factors(condition)["cold"]["state"], "freezing")

    def test_the_factor_names_its_source(self) -> None:
        detail = _factors(_condition(_weather(temp=18.0, window=COLD_LATER)))["cold"]["detail"]
        self.assertIn("Coldest forecast temperature over the next 6 h", detail)
        self.assertIn("-4.0", detail)
        self.assertNotIn("Current temperature", detail)

    def test_without_a_window_the_wording_is_unchanged(self) -> None:
        detail = _factors(_condition(_weather(temp=3.0)))["cold"]["detail"]
        self.assertEqual(detail, "Current temperature is 3.0 °C")

    def test_a_colder_current_reading_still_wins(self) -> None:
        condition = _condition(
            _weather(temp=-1.0, window={"hours": 6.0, "min_temperature": 2.0})
        )
        self.assertEqual(
            _factors(condition)["cold"]["detail"], "Current temperature is -1.0 °C"
        )

    def test_a_mild_window_changes_nothing(self) -> None:
        condition = _condition(
            _weather(temp=18.0, window={"hours": 6.0, "min_temperature": 15.0})
        )
        self.assertEqual(condition["status"], "favorable")
        self.assertNotIn("cold", _factors(condition))

    def test_the_exposure_factor_uses_the_walk_too(self) -> None:
        factors = _factors(_condition(_weather(temp=18.0, window=COLD_LATER), _trail(gain=900.0)))
        self.assertIn("coldest forecast temperature over the next 6 h", factors["exposure"]["detail"])


class WindInTheWindowTests(unittest.TestCase):
    def test_calm_now_but_windy_later_is_a_wind_factor(self) -> None:
        window = {"hours": 6.0, "min_temperature": 15.0, "max_wind_speed": 48.0}
        factor = _factors(_condition(_weather(wind=4.0, window=window)))["wind"]
        self.assertEqual(factor["state"], "strong")
        self.assertIn("Strongest forecast wind over the next 6 h", factor["detail"])

    def test_current_wind_wording_is_unchanged(self) -> None:
        factor = _factors(_condition(_weather(wind=45.0)))["wind"]
        self.assertEqual(factor["detail"], "Current wind speed is 45.0 km/h")


class InferredSnowTests(unittest.TestCase):
    def test_snow_expected_on_the_route_is_a_factor_of_its_own(self) -> None:
        condition = _condition(
            _weather(temp=6.0, window={"hours": 6.0}, inference=SNOWY)
        )
        factor = _factors(condition)["snow_forecast"]
        self.assertEqual(factor["state"], "snow")
        self.assertIn("inferred from the forecast", factor["detail"])
        self.assertIn("about 40 cm of snow lying at the high points", factor["detail"])

    def test_it_is_not_mistaken_for_snow_falling_now(self) -> None:
        condition = _condition(
            _weather(temp=6.0, window={"hours": 6.0}, inference=SNOWY)
        )
        self.assertNotIn("snow", _factors(condition))

    def test_snow_falling_now_is_not_counted_twice(self) -> None:
        weather = _weather(temp=-1.0, window={"hours": 6.0}, inference=SNOWY)
        weather["current"]["snowfall"] = 0.6
        factors = _factors(_condition(weather))
        self.assertIn("snow", factors)
        self.assertNotIn("snow_forecast", factors)

    def test_no_inferred_snow_adds_nothing(self) -> None:
        quiet = {"snow_on_route_likely": False, "snow_reasons": []}
        condition = _condition(_weather(temp=6.0, window={"hours": 6.0}, inference=quiet))
        self.assertNotIn("snow_forecast", _factors(condition))


class SummaryWordingTests(unittest.TestCase):
    def test_a_window_is_described_as_the_walk_not_right_now(self) -> None:
        favourable = _condition(_weather(window={"hours": 6.0, "min_temperature": 15.0}))
        self.assertIn("over the estimated walk", favourable["summary"])
        self.assertNotIn("right now", favourable["summary"])

    def test_without_a_window_the_summary_is_unchanged(self) -> None:
        self.assertIn("right now", _condition(_weather())["summary"])


class AssessedOverTests(unittest.TestCase):
    """The response says what it assessed, and the wording follows it."""

    def _suitability(self, weather: dict) -> dict:
        trail = _trail()
        condition = _condition(weather, trail)
        return route_suitability_context(
            trail, {"distance_km": 12.0}, trail["terrain"], condition
        )

    def test_a_window_is_reported_as_the_walk(self) -> None:
        weather = _weather(temp=18.0, window=COLD_LATER)
        self.assertEqual(_condition(weather)["assessed_over"], "walk")
        self.assertEqual(self._suitability(weather)["assessed_over"], "walk")

    def test_no_window_is_reported_as_now(self) -> None:
        weather = _weather(temp=18.0)
        self.assertEqual(_condition(weather)["assessed_over"], "now")
        self.assertEqual(self._suitability(weather)["assessed_over"], "now")

    def test_unavailable_weather_defaults_to_now(self) -> None:
        self.assertEqual(_condition(None)["assessed_over"], "now")

    def test_an_adverse_walk_is_not_called_unsuitable_right_now(self) -> None:
        window = {"hours": 16.0, "min_temperature": -12.0, "max_wind_speed": 45.0}
        weather = _weather(temp=-2.0, window=window)
        weather["current"]["precipitation"] = 3.0
        suitability = self._suitability(weather)
        self.assertEqual(suitability["level"], "currently_unfavorable")
        self.assertIn("during the estimated walk", suitability["headline"])
        self.assertNotIn("right now", suitability["headline"])

    def test_a_cautionary_walk_names_the_walk(self) -> None:
        suitability = self._suitability(_weather(temp=18.0, window=COLD_LATER))
        self.assertEqual(suitability["level"], "caution")
        self.assertIn("estimated walk", suitability["headline"])

    def test_the_scope_note_names_the_forecast_when_it_used_one(self) -> None:
        walk = self._suitability(_weather(temp=18.0, window=COLD_LATER))
        now = self._suitability(_weather(temp=18.0))
        self.assertIn("forecast over the estimated walk", walk["assessment_scope"])
        self.assertIn("observed current conditions", now["assessment_scope"])

    def test_wording_without_a_window_is_unchanged(self) -> None:
        weather = _weather(temp=-2.0)
        weather["current"]["precipitation"] = 3.0
        weather["current"]["wind_speed"] = 65.0
        suitability = self._suitability(weather)
        self.assertEqual(suitability["level"], "currently_unfavorable")
        self.assertEqual(
            suitability["headline"],
            "Current observed and forecast weather indicates this route may "
            "be unsuitable right now.",
        )


class StatusAndGearAgreeTests(unittest.TestCase):
    def test_cold_later_gives_caution_and_the_gear_for_it(self) -> None:
        weather = _weather(temp=18.0, window=COLD_LATER)
        trail = _trail()
        condition = _condition(weather, trail)
        needs = {
            i["need"]
            for i in gear_recommendations(trail, {"distance_km": 12.0}, weather, condition)["items"]
        }
        self.assertNotEqual(condition["status"], "favorable")
        self.assertIn("insulation", needs)

    def test_suitability_carries_the_forecast_factor(self) -> None:
        weather = _weather(temp=18.0, window=COLD_LATER)
        trail = _trail()
        condition = _condition(weather, trail)
        suitability = route_suitability_context(
            trail, {"distance_km": 12.0}, trail["terrain"], condition
        )
        cold = [f for f in suitability["factors"] if f["factor"] == "condition_cold"]
        self.assertTrue(cold)
        self.assertIn("Coldest forecast temperature", cold[0].get("detail", "") or str(cold[0]))


if __name__ == "__main__":
    unittest.main()
