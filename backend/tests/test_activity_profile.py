"""
Gear must know what kind of trip it is preparing for.

A day walk, a 170 km multi-day trek, a 3,000 m summit and a via ferrata are
different trips, but gear was derived from distance, ascent, surface and the
weather right now, so all of them got the same list. The activity is decided
from recorded and measured signals only, and each answer carries its reasons.
"""

from __future__ import annotations

import unittest

from app.services import products
from app.services.intelligence import (
    ActivityType,
    classify_activity,
    condition_likelihood,
    gear_recommendations,
)


def _trail(
    *,
    sac: str | None = None,
    highway: str | None = None,
    assisted: str | None = None,
    max_elevation: float | None = None,
    members: list[dict] | None = None,
) -> dict:
    trail: dict = {
        "surface": "gravel",
        "source_difficulty": sac,
        "highway_type": highway,
        "assisted_trail": assisted,
        "terrain": {
            "metrics": {
                "elevation_gain_m": 300.0,
                "elevation_loss_m": 300.0,
                "elevation_range_m": 250.0,
                "max_slope_percent": 12.0,
                "max_elevation_m": max_elevation,
            }
        },
    }
    if members is not None:
        trail["member_trails"] = members
    return trail


def _kind(trail: dict, distance: float = 6.0) -> ActivityType:
    return classify_activity(trail, {"distance_km": distance}).type


class ClassifyActivityTests(unittest.TestCase):
    def test_an_ordinary_walk_is_a_day_hike(self) -> None:
        self.assertEqual(
            _kind(_trail(sac="hiking", max_elevation=1800.0)),
            ActivityType.DAY_HIKE,
        )

    def test_a_long_route_is_a_multi_day_trek(self) -> None:
        profile = classify_activity(_trail(), {"distance_km": 172.0})
        self.assertEqual(profile.type, ActivityType.MULTI_DAY_TREK)
        self.assertTrue(any("172" in reason for reason in profile.reasons))

    def test_just_under_the_length_threshold_is_still_a_day_hike(self) -> None:
        self.assertEqual(_kind(_trail(), distance=24.0), ActivityType.DAY_HIKE)

    def test_a_high_summit_is_a_high_altitude_trek(self) -> None:
        profile = classify_activity(
            _trail(max_elevation=3096.0), {"distance_km": 9.0}
        )
        self.assertEqual(profile.type, ActivityType.HIGH_ALTITUDE_TREK)
        self.assertTrue(any("3096" in reason for reason in profile.reasons))

    def test_a_demanding_alpine_grade_is_technical(self) -> None:
        for grade in ("demanding_alpine_hiking", "difficult_alpine_hiking"):
            with self.subTest(grade=grade):
                self.assertEqual(
                    _kind(_trail(sac=grade, max_elevation=1500.0)),
                    ActivityType.TECHNICAL_ALPINE,
                )

    def test_alpine_hiking_alone_is_not_technical(self) -> None:
        # T4 is exposed walking, not climbing.
        self.assertEqual(
            _kind(_trail(sac="alpine_hiking", max_elevation=2200.0)),
            ActivityType.DAY_HIKE,
        )

    def test_a_via_ferrata_is_technical(self) -> None:
        self.assertEqual(
            _kind(_trail(highway="via_ferrata")), ActivityType.TECHNICAL_ALPINE
        )
        self.assertEqual(
            _kind(_trail(assisted="yes")), ActivityType.TECHNICAL_ALPINE
        )

    def test_a_route_is_judged_by_its_member_ways(self) -> None:
        # A relation often carries no grade of its own, but its ways do.
        members = [
            {"sac_scale": "hiking", "assisted_trail": None},
            {"sac_scale": "demanding_alpine_hiking", "assisted_trail": None},
        ]
        self.assertEqual(
            _kind(_trail(members=members)), ActivityType.TECHNICAL_ALPINE
        )
        members = [{"sac_scale": None, "assisted_trail": "yes"}]
        self.assertEqual(
            _kind(_trail(members=members)), ActivityType.TECHNICAL_ALPINE
        )

    def test_the_most_demanding_signal_wins(self) -> None:
        self.assertEqual(
            _kind(_trail(max_elevation=3200.0), distance=172.0),
            ActivityType.HIGH_ALTITUDE_TREK,
        )
        self.assertEqual(
            _kind(_trail(max_elevation=3200.0, highway="via_ferrata"), 172.0),
            ActivityType.TECHNICAL_ALPINE,
        )

    def test_missing_terrain_and_distance_default_to_a_day_hike(self) -> None:
        profile = classify_activity({}, {})
        self.assertEqual(profile.type, ActivityType.DAY_HIKE)
        self.assertTrue(profile.reasons)


class ScatteredNetworkTests(unittest.TestCase):
    """
    "Core Paths" is 236 km in 140 pieces, the largest holding 5%. It is a
    collection of local paths, not a 236 km trek, and must not be prepared for
    as one.
    """

    NETWORK = {
        "distance_km": 236.0,
        "completeness": {"status": "separate_pieces", "main_chain_share": 0.05},
    }

    def test_a_scattered_network_is_not_a_multi_day_trek(self) -> None:
        profile = classify_activity(_trail(), self.NETWORK)
        self.assertEqual(profile.type, ActivityType.DAY_HIKE)

    def test_a_network_gets_no_overnight_or_resupply_plan(self) -> None:
        weather = {"current": {"temperature": 18.0, "wind_speed": 6.0}}
        trail = _trail()
        condition = condition_likelihood(trail, trail["terrain"], weather)
        needs = {
            item["need"]
            for item in gear_recommendations(
                trail, self.NETWORK, weather, condition
            )["items"]
        }
        self.assertNotIn("overnight", needs)
        self.assertNotIn("resupply", needs)

    def test_the_gear_reasons_state_the_largest_piece_not_the_total(self) -> None:
        # 236 km of pieces, the largest 118 km: the plan must say 118.
        analysis = {
            "distance_km": 236.0,
            "completeness": {"status": "separate_pieces", "main_chain_share": 0.5},
        }
        weather = {"current": {"temperature": 18.0, "wind_speed": 6.0}}
        trail = _trail()
        condition = condition_likelihood(trail, trail["terrain"], weather)
        items = {
            item["need"]: item
            for item in gear_recommendations(
                trail, analysis, weather, condition
            )["items"]
        }
        for need in ("overnight", "resupply"):
            text = items[need]["reason"] + " ".join(items[need]["evidence"])
            self.assertIn("118", text, need)
            self.assertNotIn("236", text, need)

    def test_a_long_route_with_gaps_is_still_a_multi_day_trek(self) -> None:
        analysis = {
            "distance_km": 172.0,
            "completeness": {"status": "gaps", "main_chain_share": 0.93},
        }
        self.assertEqual(
            classify_activity(_trail(), analysis).type,
            ActivityType.MULTI_DAY_TREK,
        )

    def test_the_largest_piece_can_still_be_a_multi_day_trek(self) -> None:
        # 236 km of pieces, but the biggest piece alone is 118 km.
        analysis = {
            "distance_km": 236.0,
            "completeness": {"status": "separate_pieces", "main_chain_share": 0.5},
        }
        profile = classify_activity(_trail(), analysis)
        self.assertEqual(profile.type, ActivityType.MULTI_DAY_TREK)
        self.assertTrue(any("118" in reason for reason in profile.reasons))


class GearByActivityTests(unittest.TestCase):
    def _gear(self, trail: dict, distance: float = 6.0) -> dict:
        weather = {"current": {"temperature": 18.0, "wind_speed": 6.0}}
        condition = condition_likelihood(trail, trail.get("terrain"), weather)
        return gear_recommendations(
            trail, {"distance_km": distance}, weather, condition
        )

    @staticmethod
    def _needs(result: dict) -> dict[str, str]:
        return {item["need"]: item["priority"] for item in result["items"]}

    def test_a_day_hike_gets_no_activity_specific_gear(self) -> None:
        needs = self._needs(self._gear(_trail(max_elevation=1500.0)))
        for need in (
            "overnight",
            "resupply",
            "acclimatisation",
            "helmet",
            "experience",
            "harness",
        ):
            self.assertNotIn(need, needs)

    def test_a_multi_day_trek_plans_for_nights_and_food(self) -> None:
        result = self._gear(_trail(), distance=172.0)
        needs = self._needs(result)
        self.assertIn("overnight", needs)
        self.assertIn("resupply", needs)
        self.assertEqual(needs["light"], "recommended")
        self.assertEqual(result["activity"]["type"], "multi_day_trek")

    def test_a_high_summit_plans_for_altitude_even_on_a_mild_day(self) -> None:
        needs = self._needs(self._gear(_trail(max_elevation=3400.0), 9.0))
        self.assertIn("acclimatisation", needs)
        self.assertIn("insulation", needs)
        self.assertIn("sun_protection", needs)

    def test_a_very_high_route_makes_acclimatisation_essential(self) -> None:
        needs = self._needs(self._gear(_trail(max_elevation=4600.0), 9.0))
        self.assertEqual(needs["acclimatisation"], "essential")

    def test_a_via_ferrata_needs_a_harness_and_a_helmet(self) -> None:
        needs = self._needs(self._gear(_trail(highway="via_ferrata")))
        self.assertEqual(needs["helmet"], "essential")
        self.assertEqual(needs["harness"], "essential")
        self.assertEqual(needs["experience"], "essential")

    def test_a_demanding_alpine_grade_needs_experience_not_a_harness(self) -> None:
        result = self._gear(_trail(sac="demanding_alpine_hiking"))
        needs = self._needs(result)
        self.assertEqual(needs["experience"], "essential")
        self.assertEqual(needs["helmet"], "recommended")
        self.assertNotIn("harness", needs)

    def test_every_activity_item_states_its_evidence(self) -> None:
        result = self._gear(
            _trail(highway="via_ferrata", max_elevation=3400.0), 172.0
        )
        for item in result["items"]:
            self.assertTrue(item["reason"])
            self.assertTrue(item["evidence"])


class ProductQueryActivityTests(unittest.TestCase):
    def _query(self, activity: dict | None) -> str:
        intelligence: dict = {"gear": {}}
        if activity is not None:
            intelligence["gear"]["activity"] = activity
        return products._query_for_item(
            {"item": "Trekking poles", "category": "equipment"},
            intelligence,
        )

    def test_the_search_uses_the_activity_term(self) -> None:
        self.assertIn(
            "mountaineering", self._query({"query_term": "mountaineering"})
        )
        self.assertNotIn(
            "hiking", self._query({"query_term": "mountaineering"})
        )

    def test_without_an_activity_it_still_says_hiking(self) -> None:
        self.assertIn("hiking", self._query(None))

    def test_plans_and_experience_are_not_searched_in_a_shop(self) -> None:
        gear = {
            "items": [
                {"need": need, "item": f"{need} item", "category": need,
                 "priority": "recommended", "reason": "r", "evidence": ["e"]}
                for need in (
                    "overnight",
                    "resupply",
                    "acclimatisation",
                    "experience",
                    "helmet",
                    "harness",
                )
            ]
        }
        searched = {item["item"] for item in products._gear_items({"gear": gear})}
        self.assertEqual(searched, {"helmet item", "harness item"})


class ActivitySignalsReachClassifierTests(unittest.TestCase):
    """The signals exist in OSM; verification must not drop them."""

    @staticmethod
    def _way(**overrides):
        from app.services.postpass import PostpassWay

        values = dict(
            way_id=7,
            name="Ferrata dei Test",
            route=None,
            highway="via_ferrata",
            sac_scale="demanding_alpine_hiking",
            trail_visibility=None,
            surface=None,
            smoothness=None,
            tracktype=None,
            access=None,
            incline=None,
            incline_direction=None,
            width=None,
            assisted_trail="yes",
            aliases=[],
            geometry_type="LineString",
            point_count=2,
            length_km=1.0,
            geometry={
                "type": "LineString",
                "coordinates": [[6.0, 45.0], [6.01, 45.01]],
            },
        )
        values.update(overrides)
        return PostpassWay(**values)

    def test_a_verified_way_keeps_its_assisted_trail_tag(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.routes import trails

        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=self._way())
        ):
            verified = asyncio.run(
                trails.verify_selected_trail(
                    {"osm_type": "way", "osm_id": 7}
                )
            )
        self.assertEqual(verified["assisted_trail"], "yes")
        self.assertEqual(
            classify_activity(verified, {"distance_km": 1.0}).type,
            ActivityType.TECHNICAL_ALPINE,
        )

    def test_member_trails_keep_their_recorded_grade(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.routes import trails

        with patch.object(
            trails,
            "get_ways",
            new=AsyncMock(return_value={7: self._way()}),
        ):
            members = asyncio.run(
                trails._verified_member_trails({"member_way_ids": [7]})
            )
        self.assertEqual(members[0]["sac_scale"], "demanding_alpine_hiking")

    def test_the_intelligence_response_carries_the_activity(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.routes import trails

        geometry = {
            "type": "LineString",
            "coordinates": [[6.0, 45.0], [6.01, 45.01]],
        }
        with patch.object(
            trails, "get_way", new=AsyncMock(return_value=self._way())
        ), patch.object(
            trails, "get_weather", new=AsyncMock(side_effect=RuntimeError)
        ), patch.object(
            trails,
            "get_elevation_profile",
            new=AsyncMock(side_effect=RuntimeError),
        ):
            payload = asyncio.run(
                trails.get_selected_trail_intelligence(
                    trails.TrailIntelligenceRequest(
                        trail={
                            "osm_type": "way",
                            "osm_id": 7,
                            "map_ready": True,
                            "geometry": geometry,
                        }
                    )
                )
            )
        self.assertEqual(
            payload["gear"]["activity"]["type"], "technical_alpine"
        )
        self.assertEqual(payload["activity"], payload["gear"]["activity"])


if __name__ == "__main__":
    unittest.main()
