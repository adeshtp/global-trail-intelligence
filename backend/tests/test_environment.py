from __future__ import annotations

import unittest

from app.services import elevation, weather


class EnvironmentTests(unittest.TestCase):
    def test_weather_rainfall_contract_is_top_level(self) -> None:
        result = weather.normalize_weather_response(
            {
                "latitude": 10.0,
                "longitude": 77.0,
                "timezone": "Asia/Kolkata",
                "current": {
                    "time": "2026-01-02T03:00",
                    "temperature_2m": 20.0,
                    "relative_humidity_2m": 80.0,
                    "precipitation": 1.0,
                    "rain": 1.0,
                    "showers": 0.0,
                    "snowfall": 0.0,
                    "weather_code": 61,
                    "wind_speed_10m": 5.0,
                },
                "hourly": {
                    "time": [
                        "2026-01-01T00:00",
                        "2026-01-01T01:00",
                        "2026-01-02T03:00",
                    ],
                    "precipitation_probability": [10, 20, 60],
                    "precipitation": [1.0, 2.0, 1.0],
                    "rain": [1.0, 2.0, 1.0],
                },
            }
        )
        self.assertEqual(result["recent_rain"]["72h_mm"], 4.0)
        self.assertNotIn("recent_rain", result["current"])
        self.assertEqual(
            result["current"]["precipitation_probability"],
            60,
        )

    def test_disconnected_geometry_does_not_create_elevation_gain(self) -> None:
        profile = [
            {
                "component_index": 0,
                "component_distance_km": 0.0,
                "distance_km": 0.0,
                "elevation_m": 100.0,
            },
            {
                "component_index": 0,
                "component_distance_km": 1.0,
                "distance_km": 1.0,
                "elevation_m": 110.0,
            },
            {
                "component_index": 1,
                "component_distance_km": 0.0,
                "distance_km": 1.0,
                "elevation_m": 100.0,
            },
            {
                "component_index": 1,
                "component_distance_km": 1.0,
                "distance_km": 2.0,
                "elevation_m": 105.0,
            },
        ]
        metrics = elevation._terrain_metrics(profile)
        self.assertEqual(metrics["elevation_gain_m"], 15.0)
        self.assertEqual(metrics["elevation_loss_m"], 0.0)
        self.assertTrue(metrics["terrain_available"])

    def test_geometry_segments_are_not_flattened(self) -> None:
        segments = elevation._geometry_segments(
            {
                "type": "MultiLineString",
                "coordinates": [
                    [[0.0, 0.0], [1.0, 0.0]],
                    [[10.0, 0.0], [11.0, 0.0]],
                ],
            }
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0][-1], [1.0, 0.0])
        self.assertEqual(segments[1][0], [10.0, 0.0])

    def _profile(
        self,
        elevations: list,
        components: int = 1,
    ) -> list:
        profile = []
        distance = 0.0
        per = max(1, len(elevations) // components)
        for index, value in enumerate(elevations):
            component = min(index // per, components - 1)
            profile.append(
                {
                    "component_index": component,
                    "component_distance_km": round(distance, 3),
                    "distance_km": round(distance, 3),
                    "elevation_m": value,
                }
            )
            distance += 0.5
        return profile

    def test_strong_endpoint_evidence_allows_low_to_high(self) -> None:
        result = elevation._profile_orientation(
            self._profile([1500.0, 1520.0, 1560.0])
        )
        self.assertEqual(result["profile_orientation"], "low_to_high")
        self.assertFalse(result["reversal_needed"])
        self.assertEqual(
            result["orientation_basis"]["endpoint_difference_m"],
            60.0,
        )

    def test_low_to_high_marks_reversal_when_mapped_runs_high(self) -> None:
        result = elevation._profile_orientation(
            self._profile([1560.0, 1520.0, 1500.0])
        )
        self.assertEqual(result["profile_orientation"], "low_to_high")
        self.assertTrue(result["reversal_needed"])

    def test_small_endpoint_difference_preserves_mapped_order(self) -> None:
        result = elevation._profile_orientation(
            self._profile([1500.0, 1502.0, 1504.0])
        )
        self.assertEqual(result["profile_orientation"], "as_mapped")
        self.assertFalse(result["reversal_needed"])
        self.assertIsNotNone(result["orientation_basis"])

    def test_missing_endpoint_elevation_preserves_mapped_order(self) -> None:
        result = elevation._profile_orientation(
            self._profile([None, 1520.0, 1560.0])
        )
        self.assertEqual(result["profile_orientation"], "as_mapped")
        self.assertIsNone(result["orientation_basis"])

    def test_multi_component_profile_preserves_component_order(self) -> None:
        result = elevation._profile_orientation(
            self._profile(
                [1400.0, 1420.0, 1500.0, 1600.0],
                components=2,
            )
        )
        self.assertEqual(result["profile_orientation"], "as_mapped")
        self.assertFalse(result["reversal_needed"])

    def test_all_null_elevations_stay_honestly_unavailable(self) -> None:
        metrics = elevation._terrain_metrics(
            self._profile([None, None, None])
        )
        self.assertFalse(metrics["terrain_available"])
        self.assertIsNone(metrics["elevation_gain_m"])
        self.assertIsNone(metrics["min_elevation_m"])
        self.assertIsNone(metrics["max_elevation_m"])


if __name__ == "__main__":
    unittest.main()
