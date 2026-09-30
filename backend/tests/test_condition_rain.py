"""
Ordinary rain is not an adverse walk.

Every rain reading used to add its own points (rain in the last 24 h, anything
above 0.0 mm falling now, the weather code saying rain, any amount of forecast
rain, any chance of rain of 70% or more, and a fixed penalty for any natural
surface) until a drizzle of 0.1 mm on a 25 C day scored 61 and the trail was
"Not suitable for this walk". Monsoon regions, where 100% chance of rain is a
normal day, showed it on almost every trail.

Rain is now scored once per fact, by how much of it there is: raining now by
intensity (the worse of the measured rate and the weather code), rain expected
by amount with the chance as a modifier, and a wet surface only when it is
actually wet. Real hazards (heavy rain, storm, snow, freezing) are unchanged.
"""

from __future__ import annotations

import unittest

from app.services.intelligence import condition_likelihood


def _weather(
    *,
    temp=25.0,
    wind=8.0,
    now=0.0,
    interval=900,
    code=0,
    rain24=0.0,
    rain72=0.0,
    forecast_rain=0.0,
    chance=0,
    snow=0.0,
    window=None,
) -> dict:
    payload = {
        "current": {
            "temperature": temp,
            "wind_speed": wind,
            "precipitation": now,
            "precipitation_interval_s": interval,
            "weather_code": code,
            "snowfall": snow,
        },
        "recent_rain": {"24h_mm": rain24, "72h_mm": rain72},
        "recent_precipitation": {"24h_mm": rain24, "72h_mm": rain72},
        "forecast": {
            "rain_mm": forecast_rain,
            "precipitation_probability_max": chance,
        },
        "source": "Test",
    }
    if window is not None:
        payload["window"] = window
    return payload


def _condition(weather: dict, *, surface="ground", slope=33.5) -> dict:
    terrain = {
        "metrics": {"max_slope_percent": slope, "elevation_gain_m": 603.0}
    }
    return condition_likelihood({"surface": surface}, terrain, weather)


def _weights(condition: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for factor in condition["factors"]:
        out[factor["factor"]] = out.get(factor["factor"], 0) + factor["weight"]
    return out


class OrdinaryRainIsNotAdverseTests(unittest.TestCase):
    def test_the_reported_trail(self) -> None:
        # Easy trail, 25 C: 0.9 mm in 24 h, 0.1 mm now, rain code, 100% chance
        # of 2.4 mm.
        condition = _condition(
            _weather(
                now=0.1, code=61, rain24=0.9, rain72=12.0,
                forecast_rain=2.4, chance=100,
            )
        )
        self.assertNotEqual(condition["status"], "adverse", condition["factors"])

    def test_a_rainy_week_of_light_showers_is_at_most_caution(self) -> None:
        condition = _condition(
            _weather(
                now=0.1, code=61, rain24=0.9, rain72=25.0,
                forecast_rain=2.4, chance=100,
            )
        )
        self.assertIn(condition["status"], {"favorable", "caution"})

    def test_drizzle_is_favorable(self) -> None:
        condition = _condition(
            _weather(
                now=0.2, code=51, rain24=0.2, rain72=1.0,
                forecast_rain=0.5, chance=80,
            )
        )
        self.assertEqual(condition["status"], "favorable", condition["factors"])

    def test_a_chance_of_rain_with_no_rain_expected_is_not_a_factor(self) -> None:
        condition = _condition(_weather(forecast_rain=0.3, chance=100))
        self.assertEqual(condition["status"], "favorable")
        self.assertNotIn("forecast", _weights(condition))

    def test_a_dry_day_on_natural_ground_is_favorable(self) -> None:
        for surface in ("ground", "dirt", "gravel", "mud"):
            with self.subTest(surface=surface):
                condition = _condition(_weather(), surface=surface)
                self.assertEqual(condition["status"], "favorable")
                self.assertEqual(_weights(condition).get("surface", 0), 0)


class RealHazardsStayAdverseTests(unittest.TestCase):
    def test_heavy_rain(self) -> None:
        condition = _condition(
            _weather(
                now=3.0, code=65, rain24=25.0, rain72=60.0,
                forecast_rain=20.0, chance=100,
            )
        )
        self.assertEqual(condition["status"], "adverse")

    def test_thunderstorm(self) -> None:
        condition = _condition(
            _weather(
                now=4.0, code=95, rain24=5.0, rain72=8.0,
                forecast_rain=15.0, chance=100,
            )
        )
        self.assertEqual(condition["status"], "adverse")

    def test_snow_falling(self) -> None:
        condition = _condition(_weather(temp=-2.0, snow=3.0, code=73))
        self.assertEqual(condition["status"], "adverse")

    def test_freezing_over_the_walk(self) -> None:
        condition = _condition(
            _weather(
                temp=8.0,
                window={"hours": 16, "min_temperature": -10.5,
                        "max_wind_speed": 30.0},
            )
        )
        self.assertIn(condition["status"], {"adverse", "caution"})
        self.assertIn("cold", _weights(condition))

    def test_strong_wind_is_a_caution_on_its_own(self) -> None:
        for wind in (45.0, 65.0):
            with self.subTest(wind=wind):
                condition = _condition(_weather(wind=wind, code=3))
                self.assertIn(condition["status"], {"adverse", "caution"})

    def test_a_thunderstorm_is_adverse_on_its_own(self) -> None:
        condition = _condition(_weather(code=95))
        self.assertEqual(condition["status"], "adverse")

    def test_saturated_ground_and_steady_rain_is_adverse(self) -> None:
        condition = _condition(
            _weather(
                now=0.8, code=63, rain24=30.0, rain72=60.0,
                forecast_rain=6.0, chance=80,
            ),
            surface="mud", slope=30.0,
        )
        self.assertEqual(condition["status"], "adverse")


class RainIsScoredOncePerFactTests(unittest.TestCase):
    def test_raining_now_is_one_factor(self) -> None:
        condition = _condition(_weather(now=0.5, code=61))
        names = [f["factor"] for f in condition["factors"]]
        self.assertEqual(names.count("precipitation"), 1)
        self.assertNotIn("weather_code", names)

    def test_the_weather_code_counts_when_no_rate_is_measured(self) -> None:
        # Heavy rain by code is not dismissed because the last 15 minutes read 0.
        condition = _condition(_weather(now=0.0, code=65))
        self.assertGreater(_weights(condition).get("precipitation", 0), 0)
        self.assertNotEqual(condition["status"], "favorable")

    def test_intensity_scales_the_points(self) -> None:
        light = _weights(_condition(_weather(now=0.1)))["precipitation"]
        moderate = _weights(_condition(_weather(now=0.8)))["precipitation"]
        heavy = _weights(_condition(_weather(now=3.0)))["precipitation"]
        self.assertLess(light, moderate)
        self.assertLess(moderate, heavy)

    def test_a_15_minute_reading_is_read_as_a_rate(self) -> None:
        # 2.0 mm in 15 minutes is 8 mm/h, heavy; the same number over an hour
        # is light.
        quarter_hour = _weights(_condition(_weather(now=2.0, interval=900)))
        hourly = _weights(_condition(_weather(now=2.0, interval=3600)))
        self.assertGreater(
            quarter_hour["precipitation"], hourly["precipitation"]
        )

    def test_the_walk_window_supplies_the_peak_hourly_rate(self) -> None:
        condition = _condition(
            _weather(now=0.0, window={"hours": 6, "max_precipitation_mm": 9.0})
        )
        self.assertGreaterEqual(_weights(condition)["precipitation"], 24)

    def test_expected_rain_is_one_factor_and_the_chance_only_modifies_it(self) -> None:
        dry_chance = _weights(_condition(_weather(forecast_rain=6.0, chance=10)))
        wet_chance = _weights(_condition(_weather(forecast_rain=6.0, chance=90)))
        self.assertEqual(
            [f["factor"] for f in _condition(
                _weather(forecast_rain=6.0, chance=90))["factors"]].count("forecast"),
            1,
        )
        self.assertGreater(wet_chance["forecast"], dry_chance["forecast"])

    def test_a_wet_surface_counts_only_when_it_is_wet(self) -> None:
        dry = _weights(_condition(_weather(), surface="mud"))
        wet = _weights(_condition(_weather(rain24=12.0), surface="mud"))
        self.assertEqual(dry.get("surface", 0), 0)
        self.assertGreater(wet["surface"], 0)


if __name__ == "__main__":
    unittest.main()
