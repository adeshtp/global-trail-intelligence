from __future__ import annotations

import unittest

from app.services.difficulty import source_difficulty
from app.services.intelligence import (
    condition_likelihood,
    gear_recommendations,
)


class GearEnvironmentMatrixTests(unittest.TestCase):
    """
    Gear must follow the measured route and the observed weather, for any
    trail anywhere. Every case is fully synthetic and offline: no provider
    is called and no place is named, so the rules can only be reading the
    numbers.
    """

    @staticmethod
    def _trail(
        *,
        surface="gravel",
        gain=100.0,
        loss=100.0,
        rng=120.0,
        slope=12.0,
    ):
        return {
            "surface": surface,
            "terrain": {
                "metrics": {
                    "elevation_gain_m": gain,
                    "elevation_loss_m": loss,
                    "elevation_range_m": rng,
                    "max_slope_percent": slope,
                }
            },
        }

    @staticmethod
    def _weather(
        *,
        temp=None,
        wind=None,
        snow=None,
        precip=None,
        rain24=None,
        code=None,
        prob=None,
    ):
        current = {}
        for key, value in (
            ("temperature", temp),
            ("wind_speed", wind),
            ("snowfall", snow),
            ("precipitation", precip),
            ("weather_code", code),
            ("precipitation_probability", prob),
        ):
            if value is not None:
                current[key] = value
        payload = {"current": current, "source": "Test"}
        if rain24 is not None:
            payload["recent_rain"] = {"24h_mm": rain24}
        if prob is not None:
            payload["forecast"] = {
                "precipitation_probability_max": prob
            }
        return payload

    def _run(self, trail, weather, distance=5.0):
        condition = condition_likelihood(
            trail,
            trail.get("terrain"),
            weather,
        )
        result = gear_recommendations(
            trail,
            {"distance_km": distance},
            weather,
            condition,
        )
        needs = {item["need"] for item in result["items"]}
        return needs, result, condition

    def test_normal_dry_moderate_route_gets_no_weather_gear(self) -> None:
        needs, result, _ = self._run(
            self._trail(),
            self._weather(temp=18.0, wind=6.0),
        )
        weather_only = {
            "rain_shell",
            "wet_footwear",
            "thermal_layer",
            "insulation",
            "wind_layer",
            "sun_protection",
            "winter_equipment",
        }
        self.assertEqual(needs & weather_only, set())
        # Baseline items follow from the activity, not the weather.
        self.assertIn("trail_footwear", needs)
        self.assertIn("navigation", needs)
        for item in result["items"]:
            self.assertTrue(item["reason"])
            self.assertTrue(item["evidence"])

    def test_hot_dry_route_adds_sun_and_heat_hydration(self) -> None:
        needs, result, _ = self._run(
            self._trail(),
            self._weather(temp=35.0, wind=4.0),
        )
        self.assertIn("sun_protection", needs)
        self.assertNotIn("rain_shell", needs)
        self.assertNotIn("insulation", needs)
        # Heat is an independent reason to carry water, not a length effect.
        hydration = next(
            item
            for item in result["items"]
            if item["need"] == "hydration"
        )
        self.assertEqual(hydration["priority"], "essential")
        self.assertTrue(
            any("35" in value for value in hydration["evidence"]),
            hydration["evidence"],
        )

    def test_rainy_tropical_route_answers_with_rain_protection(self) -> None:
        needs, result, _ = self._run(
            self._trail(surface="earth"),
            self._weather(
                temp=26.0, wind=8.0, precip=3.2, rain24=28.0, prob=90
            ),
        )
        self.assertIn("rain_shell", needs)
        self.assertIn("wet_footwear", needs)
        # 26 C is below the sun threshold, and rain is falling regardless.
        self.assertNotIn("sun_protection", needs)
        shell = next(
            item for item in result["items"] if item["need"] == "rain_shell"
        )
        self.assertEqual(shell["priority"], "essential")
        self.assertTrue(
            any("28.0" in value for value in shell["evidence"]),
            shell["evidence"],
        )

    def test_cold_windy_route_adds_layers_and_windproofing(self) -> None:
        needs, _, _ = self._run(
            self._trail(surface="rock"),
            self._weather(temp=2.0, wind=45.0),
        )
        self.assertIn("thermal_layer", needs)
        self.assertIn("insulation", needs)
        self.assertIn("wind_layer", needs)
        self.assertNotIn("rain_shell", needs)
        self.assertNotIn("sun_protection", needs)

    def test_snow_on_steep_alpine_route_asks_for_traction(self) -> None:
        needs, result, _ = self._run(
            self._trail(surface="rock", gain=600.0, slope=35.0, rng=400.0),
            self._weather(temp=-4.0, wind=35.0, snow=6.0, code=71),
            distance=7.0,
        )
        self.assertIn("winter_equipment", needs)
        winter = next(
            item
            for item in result["items"]
            if item["need"] == "winter_equipment"
        )
        self.assertEqual(winter["priority"], "essential")
        self.assertIn("crampons", winter["item"].lower())

    def test_snow_reported_only_by_weather_code_still_counts(self) -> None:
        """
        Snow can be reported by a weather code with no measured depth.
        The condition engine derives a snow factor from it, so gear must
        read that factor rather than depending on a numeric snowfall field.
        """
        needs, _, condition = self._run(
            self._trail(surface="rock", gain=500.0, slope=30.0),
            self._weather(temp=-3.0, wind=30.0, snow=0.0, code=71),
        )
        self.assertIn("snow", {f["factor"] for f in condition["factors"]})
        self.assertIn("winter_equipment", needs)

    def test_snow_on_a_flat_route_does_not_ask_for_crampons(self) -> None:
        """
        Traction gear is justified by slope, not by snow alone. Snow on a
        flat path still needs snow-aware boots and layers, but an ice axe
        is not a route requirement there.
        """
        _needs, result, _ = self._run(
            self._trail(surface="gravel", gain=40.0, slope=3.0, rng=20.0),
            self._weather(temp=-2.0, wind=8.0, snow=4.0),
        )
        winter = next(
            item
            for item in result["items"]
            if item["need"] == "winter_equipment"
        )
        self.assertNotIn("crampons", winter["item"].lower())
        self.assertEqual(winter["priority"], "recommended")

    def test_extreme_cold_escalates_beyond_an_insulated_jacket(self) -> None:
        needs, result, _ = self._run(
            self._trail(surface="snow", gain=50.0, slope=5.0, rng=20.0),
            self._weather(temp=-27.0, wind=55.0, snow=2.0),
            distance=9.0,
        )
        insulation = next(
            item
            for item in result["items"]
            if item["need"] == "insulation"
        )
        self.assertIn("balaclava", insulation["item"].lower())
        self.assertIn("wind_layer", needs)
        self.assertNotIn("rain_shell", needs)
        # -27 C is a serious exposure, not a day for sun protection.
        self.assertNotIn("sun_protection", needs)

    def test_rain_payload_without_temperature_still_counts_as_wet(self) -> None:
        """
        Rain protection is gated on rain, not on a temperature reading.
        A sparse provider payload carrying rainfall and no temperature used
        to produce no rain gear at all.
        """
        weather = {
            "current": {"precipitation": 4.0},
            "recent_rain": {"24h_mm": 20.0},
            "source": "Test",
        }
        needs, _, _ = self._run(
            self._trail(surface="earth"),
            weather,
        )
        self.assertIn("rain_shell", needs)

    def test_sun_protection_is_suppressed_while_rain_is_falling(self) -> None:
        needs, _, _ = self._run(
            self._trail(),
            self._weather(temp=28.0, wind=8.0, precip=4.0, rain24=25.0),
        )
        self.assertIn("rain_shell", needs)
        self.assertNotIn("sun_protection", needs)

    def test_no_weather_yields_no_weather_gear_and_says_so(self) -> None:
        needs, result, _ = self._run(
            self._trail(),
            None,
        )
        weather_only = {
            "rain_shell",
            "thermal_layer",
            "insulation",
            "wind_layer",
            "sun_protection",
            "winter_equipment",
        }
        self.assertEqual(needs & weather_only, set())
        self.assertIn("current weather", result["missing_evidence"])

    def test_every_item_carries_a_reason_and_evidence(self) -> None:
        for weather in (
            self._weather(temp=35.0, wind=4.0),
            self._weather(temp=-27.0, wind=55.0, snow=2.0),
            self._weather(temp=26.0, precip=3.2, rain24=28.0),
        ):
            _needs, result, _ = self._run(self._trail(), weather)
            for item in result["items"]:
                self.assertTrue(item["reason"], item)
                self.assertTrue(item["evidence"], item)
                self.assertIn(
                    item["priority"],
                    {"essential", "recommended", "conditional"},
                )



class IntelligenceTests(unittest.TestCase):
    def test_source_sac_is_separate_from_ml(self) -> None:
        source = source_difficulty(
            {"source_difficulty": "demanding_mountain_hiking"}
        )
        self.assertEqual(source["sac_scale"], "demanding_mountain_hiking")
        # The recorded grade is kept verbatim, and reported in the same tier
        # vocabulary the model uses so the two are directly comparable.
        self.assertEqual(source["tier"], "mountain")
        self.assertEqual(source["class"], "Mountain trail")
        self.assertTrue(source["authoritative"])

    def test_condition_is_explicitly_an_inference(self) -> None:
        result = condition_likelihood(
            {"surface": "mud"},
            {
                "metrics": {
                    "max_slope_percent": 20.0,
                }
            },
            {
                "recent_rain": {"24h_mm": 12.0, "72h_mm": 30.0},
                "recent_precipitation": {
                    "24h_mm": 12.0,
                    "72h_mm": 30.0,
                },
                "current": {
                    "precipitation": 0.5,
                    "temperature": 15.0,
                },
                "forecast": {
                    "rain_mm": 2.0,
                    "precipitation_probability_max": 60.0,
                },
            },
        )
        self.assertEqual(result["observation_type"], "inference")
        self.assertIn(
            result["status"],
            {"favorable", "caution", "adverse", "unknown"},
        )
        self.assertTrue(result["factors"], "condition must expose its evidence")
        self.assertNotIn(
            "The trail is muddy",
            result["summary"],
        )

    def test_gear_changes_with_route_and_conditions(self) -> None:
        result = gear_recommendations(
            {
                "surface": "mud",
                "terrain": {
                    "metrics": {
                        "elevation_gain_m": 700.0,
                    }
                },
            },
            {"distance_km": 10.0},
            {
                "current": {
                    "temperature": 8.0,
                    "wind_speed": 35.0,
                    "precipitation": 2.0,
                },
                "recent_rain": {"24h_mm": 22.0},
            },
            {
                "available": True,
                "likelihood": "high",
                "summary": "High likelihood of difficult surface conditions.",
                "factors": [
                    {
                        "factor": "wetness",
                        "state": "saturated",
                        "detail": "22.0 mm of rain fell in the last 24 h",
                        "weight": 34,
                    }
                ],
            },
        )
        categories = {item["category"] for item in result["items"]}
        items = " ".join(item["item"] for item in result["items"]).lower()
        self.assertIn("clothing", categories)
        self.assertIn("waterproof", items)
        self.assertIn("mid layer", items)
        self.assertIn("windproof", items)
        self.assertGreaterEqual(len(result["items"]), 6)
        self.assertEqual(result["missing_evidence"], [])
        for item in result["items"]:
            self.assertTrue(item["reason"])
            self.assertTrue(item["evidence"])

    def test_rain_gear_requires_rain_evidence_not_a_verdict(self) -> None:
        """
        A cautious verdict is not the same as a wet route.

        Cold, wind and steep ground all raise the condition verdict, so
        treating `caution`/`adverse` as "wet" previously produced a
        waterproof shell on a dry freezing route whose own reason claimed
        the conditions were wet. Wet gear now needs measured or
        factor-reported rain, never the verdict alone.
        """
        result = gear_recommendations(
            {
                "surface": "rock",
                "terrain": {
                    "metrics": {
                        "elevation_gain_m": 500.0,
                        "max_slope_percent": 35.0,
                    }
                },
            },
            {"distance_km": 6.0},
            {
                "current": {
                    "temperature": -12.0,
                    "wind_speed": 45.0,
                    "precipitation": 0.0,
                },
                "recent_rain": {"24h_mm": 0.0},
            },
            {
                "available": True,
                "status": "adverse",
                "summary": "Adverse conditions from cold and wind.",
                "factors": [
                    {
                        "factor": "cold",
                        "state": "freezing",
                        "detail": "Current temperature is -12.0 C",
                        "weight": 26,
                    }
                ],
            },
        )
        needs = {item["need"] for item in result["items"]}
        self.assertNotIn("rain_shell", needs)
        self.assertNotIn("wet_footwear", needs)
        self.assertIn("insulation", needs)
        self.assertIn("wind_layer", needs)

    def test_gear_basis_never_claims_unavailable_evidence(self) -> None:
        result = gear_recommendations(
            {"surface": "gravel", "terrain": None},
            {"distance_km": 2.0},
            None,
            {
                "available": False,
                "likelihood": "unknown",
                "summary": "Condition likelihood is unavailable.",
            },
        )
        self.assertNotIn(
            "current temperature and wind",
            result["basis"],
        )
        self.assertNotIn(
            "condition likelihood inference",
            result["basis"],
        )
        self.assertNotIn("sampled elevation profile", result["basis"])
        self.assertIn("elevation profile", result["missing_evidence"])
        self.assertIn("current weather", result["missing_evidence"])
        self.assertIn("condition likelihood", result["missing_evidence"])

    def test_gear_does_not_invent_cold_or_wind_items_without_weather(self) -> None:
        result = gear_recommendations(
            {"surface": "gravel", "terrain": None},
            {"distance_km": 2.0},
            None,
            {
                "available": False,
                "likelihood": "unknown",
                "summary": "Condition likelihood is unavailable.",
            },
        )
        categories = {item["category"] for item in result["items"]}
        self.assertNotIn("cold_weather", categories)
        self.assertNotIn("weather_protection", categories)
        self.assertNotIn("rain_protection", categories)

    def test_gear_never_invents_weather_items_without_weather(self) -> None:
        result = gear_recommendations(
            {"surface": "earth", "terrain": None},
            {"distance_km": 4.0},
            None,
            {
                "available": False,
                "status": "unknown",
                "summary": "Live weather unavailable.",
            },
        )
        items = " ".join(item["item"] for item in result["items"]).lower()
        for forbidden in (
            "waterproof",
            "insulated",
            "windproof",
            "gloves",
            "ice axe",
            "crampons",
        ):
            self.assertNotIn(forbidden, items)
        self.assertTrue(result["missing_evidence"])

    def test_route_complexity_is_ordered_and_explainable(self) -> None:
        from app.services.route_complexity import route_complexity

        easy = route_complexity(
            {"distance_km": 2.0, "component_count": 1},
            {
                "metrics": {
                    "elevation_gain_m": 40.0,
                    "max_slope_percent": 6.0,
                    "elevation_range_m": 50.0,
                }
            },
        )
        hard = route_complexity(
            {"distance_km": 18.0, "component_count": 3},
            {
                "metrics": {
                    "elevation_gain_m": 1200.0,
                    "max_slope_percent": 55.0,
                    "elevation_range_m": 1400.0,
                }
            },
        )
        self.assertTrue(easy["available"])
        self.assertLess(easy["score"], hard["score"])
        self.assertEqual(easy["label"], "low")
        for part in hard["components"]:
            self.assertTrue(part["measured"])

    def test_route_complexity_reports_missing_evidence(self) -> None:
        from app.services.route_complexity import route_complexity

        result = route_complexity({"distance_km": 1.0, "component_count": 1}, None)
        self.assertIn("elevation_gain", result["missing_evidence"])
        self.assertIn("max_slope", result["missing_evidence"])

    def test_condition_covers_snow_cold_and_wind(self) -> None:
        def weather(current: dict, forecast: dict) -> dict:
            return {
                "recent_rain": {"24h_mm": 0.0, "72h_mm": 0.0},
                "recent_precipitation": {"24h_mm": 0.0, "72h_mm": 0.0},
                "current": {
                    "precipitation": 0.0,
                    "wind_speed": 10.0,
                    "snowfall": 0.0,
                    "time": "2026-01-01T00:00",
                    **current,
                },
                "forecast": {
                    "rain_mm": 0.0,
                    "precipitation_probability_max": 0.0,
                    **forecast,
                },
                "source": "Open-Meteo",
            }

        snowy = condition_likelihood(
            {"surface": "snow"},
            None,
            weather(
                {"temperature": -4.0, "snowfall": 3.0, "weather_code": 73},
                {},
            ),
        )
        self.assertEqual(snowy["status"], "adverse")
        states = {factor["state"] for factor in snowy["factors"]}
        self.assertIn("snow", states)
        self.assertIn("freezing", states)

        windy = condition_likelihood(
            {"surface": "earth"},
            None,
            weather(
                {"temperature": 20.0, "wind_speed": 50.0, "weather_code": 3},
                {},
            ),
        )
        self.assertEqual(windy["status"], "caution")
        self.assertIn(
            "strong",
            {factor["state"] for factor in windy["factors"]},
        )

        thunderous = condition_likelihood(
            {"surface": "earth"},
            None,
            weather(
                {"temperature": 20.0, "wind_speed": 5.0, "weather_code": 95},
                {},
            ),
        )
        self.assertEqual(thunderous["status"], "adverse")

    def test_condition_is_unknown_when_weather_is_missing(self) -> None:
        result = condition_likelihood({"surface": "earth"}, None, None)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["available"])
        self.assertIn("live_weather", result["missing_evidence"])
        self.assertIn("not a statement", result["summary"])

    def test_partial_weather_never_reports_favorable(self) -> None:
        result = condition_likelihood(
            {"surface": "earth"},
            None,
            {
                "current": {"temperature": 20.0},
                "source": "Open-Meteo",
            },
        )
        self.assertEqual(result["status"], "unknown")

    def test_suitability_combines_route_and_current_condition(self) -> None:
        from app.services.intelligence import route_suitability_context

        adverse = condition_likelihood(
            {"surface": "mud"},
            {"metrics": {"max_slope_percent": 30.0}},
            {
                "recent_rain": {"24h_mm": 30.0, "72h_mm": 60.0},
                "recent_precipitation": {"24h_mm": 30.0, "72h_mm": 60.0},
                "current": {
                    "precipitation": 1.0,
                    "temperature": 12.0,
                    "wind_speed": 5.0,
                    "snowfall": 0.0,
                    "weather_code": 63,
                    "time": "2026-01-01T00:00",
                },
                "forecast": {
                    "rain_mm": 6.0,
                    "precipitation_probability_max": 80.0,
                },
                "source": "Open-Meteo",
            },
        )
        result = route_suitability_context(
            {"length_km": 9.0},
            {"distance_km": 9.0},
            {"metrics": {"elevation_gain_m": 500.0, "max_slope_percent": 30.0}},
            adverse,
        )
        self.assertEqual(result["level"], "currently_unfavorable")
        self.assertNotIn("safe", str(result).lower())
        categories = {
            factor.get("category") for factor in result["factors"]
        }
        self.assertIn("current_condition", categories)

    def test_suitability_is_insufficient_data_without_weather(self) -> None:
        from app.services.intelligence import route_suitability_context

        unknown = condition_likelihood({"surface": "earth"}, None, None)
        result = route_suitability_context(
            {"length_km": 3.0},
            {"distance_km": 3.0},
            {"metrics": {"elevation_gain_m": 100.0}},
            unknown,
        )
        self.assertEqual(result["level"], "insufficient_data")
        self.assertIn("age", result["assessment_scope"])
        self.assertIn("fitness", result["assessment_scope"])


    def test_official_scale_always_supersedes_the_learned_estimate(
        self,
    ) -> None:
        from app.services.difficulty import (
            reconcile_difficulty,
            source_difficulty,
        )

        # The learned model put a way whose recorded OSM scale is "hiking"
        # in the alpine tier. The recorded value must win and the
        # contradiction must be stated, not hidden.
        source = source_difficulty({"source_difficulty": "hiking"})
        self.assertEqual(source["tier"], "walking")
        result = reconcile_difficulty(
            source,
            {
                "available": True,
                "estimate_tier": "alpine",
                "estimate_label": "Alpine / scrambling",
            },
        )
        self.assertEqual(
            result["status"],
            "superseded_by_official_scale",
        )
        self.assertEqual(result["authoritative_tier"], "walking")
        self.assertEqual(result["display"], "Walkable trail")
        self.assertIsNone(result["ml_agrees_with_official"])
        self.assertEqual(
            result["display_provenance"], "official_osm_sac_scale"
        )
        self.assertIn("not shown", result["message"])

    def test_agreement_is_reported(self) -> None:
        from app.services.difficulty import (
            reconcile_difficulty,
            source_difficulty,
        )

        source = source_difficulty(
            {"source_difficulty": "alpine_hiking"}
        )
        result = reconcile_difficulty(
            source,
            {
                "available": True,
                "estimate_tier": "alpine",
                "estimate_label": "Alpine / scrambling",
            },
        )
        self.assertTrue(result["ml_agrees_with_official"])
        self.assertEqual(result["authoritative_tier"], "alpine")
        self.assertEqual(result["display"], "Alpine / scrambling")

    def test_estimate_is_used_only_when_no_official_scale_exists(
        self,
    ) -> None:
        from app.services.difficulty import (
            reconcile_difficulty,
            source_difficulty,
        )

        result = reconcile_difficulty(
            source_difficulty({}),
            {
                "available": True,
                "estimate_tier": "mountain",
                "estimate_label": "Mountain trail",
            },
        )
        self.assertEqual(result["status"], "model_estimate_only")
        self.assertIsNone(result["authoritative_tier"])
        self.assertEqual(result["display"], "Mountain trail")
        self.assertEqual(result["display_provenance"], "model_estimated")
        self.assertIn("not a recorded value", result["message"])

    def test_no_difficulty_signal_is_reported_as_unavailable(self) -> None:
        from app.services.difficulty import (
            reconcile_difficulty,
            source_difficulty,
        )

        result = reconcile_difficulty(
            source_difficulty({}),
            {"available": False, "estimate": None},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["authoritative_class"])


if __name__ == "__main__":
    unittest.main()
