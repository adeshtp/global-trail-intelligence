from __future__ import annotations

import unittest

from app.routes import discovery
from app.services import postpass


class TilingTests(unittest.TestCase):
    def test_small_area_is_a_single_tile(self) -> None:
        tiles, plan = discovery._tile_plan((76.0, 9.5, 76.5, 10.0))
        self.assertEqual(len(tiles), 1)
        self.assertFalse(plan["tiled"])
        self.assertEqual(plan["tiles_total"], 1)

    def test_large_area_is_partitioned_not_sampled(self) -> None:
        bbox = (5.96, 45.82, 10.49, 47.81)
        tiles, plan = discovery._tile_plan(bbox)
        self.assertTrue(plan["tiled"])
        self.assertGreater(len(tiles), 1)
        self.assertLessEqual(len(tiles), discovery.MAX_TILES)
        self.assertEqual(plan["tiles_total"], len(tiles))

        # A partition covers the whole area with no gaps: the union of tile
        # bounds must reach every edge of the requested box.
        self.assertAlmostEqual(min(t[0] for t in tiles), bbox[0], places=9)
        self.assertAlmostEqual(min(t[1] for t in tiles), bbox[1], places=9)
        self.assertAlmostEqual(max(t[2] for t in tiles), bbox[2], places=9)
        self.assertAlmostEqual(max(t[3] for t in tiles), bbox[3], places=9)

        # Total area is conserved, so nothing is double counted or dropped.
        total = sum(
            (t[2] - t[0]) * (t[3] - t[1]) for t in tiles
        )
        expected = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        self.assertAlmostEqual(total, expected, places=9)

    def test_tiling_is_deterministic(self) -> None:
        bbox = (5.96, 45.82, 10.49, 47.81)
        first, _ = discovery._tile_plan(bbox)
        second, _ = discovery._tile_plan(bbox)
        self.assertEqual(first, second)

    def test_tile_count_stays_bounded_for_huge_areas(self) -> None:
        # A continental-scale box must still be bounded.
        tiles, plan = discovery._tile_plan((-10.0, 35.0, 30.0, 60.0))
        self.assertLessEqual(len(tiles), discovery.MAX_TILES)
        self.assertGreater(len(tiles), 1)
        self.assertEqual(plan["tiles_total"], len(tiles))

    def test_tile_plan_reports_accounting_fields(self) -> None:
        _tiles, plan = discovery._tile_plan((76.0, 9.5, 77.5, 10.5))
        for field in (
            "tiled",
            "tiles_total",
            "tile_grid",
            "tiles_queried",
            "tiles_failed",
            "tiles_skipped",
        ):
            self.assertIn(field, plan)


class TileDedupeTests(unittest.TestCase):
    def test_identity_dedupe_keeps_distinct_same_name_objects(self) -> None:
        class Row:
            def __init__(self, way_id: int, name: str) -> None:
                self.way_id = way_id
                self.name = name

        # A way that crosses a tile boundary is returned by both tiles. It
        # must collapse to one identity, while two genuinely different ways
        # that share a name must both survive.
        rows = [
            Row(1, "Chemin panorama alpin"),
            Row(1, "Chemin panorama alpin"),
            Row(2, "Chemin panorama alpin"),
        ]
        deduped: dict[int, Row] = {}
        for row in rows:
            deduped.setdefault(row.way_id, row)
        self.assertEqual(sorted(deduped), [1, 2])
        self.assertEqual(
            len({r.way_id for r in deduped.values()}),
            2,
        )


class EvidenceClassTests(unittest.TestCase):
    """Weak MAP_READY must mean weak relevance, never weak geometry."""

    def _way(self, **kwargs):
        from app.services.postpass import PostpassWay

        base = dict(
            way_id=1,
            name="Local Path",
            route=None,
            highway="path",
            sac_scale=None,
            trail_visibility=None,
            surface="unpaved",
            smoothness=None,
            tracktype=None,
            access=None,
            incline=None,
            incline_direction=None,
            width=None,
            assisted_trail=None,
            aliases=[],
            geometry_type="LINESTRING",
            point_count=10,
            length_km=1.2,
            geometry={"type": "LineString", "coordinates": []},
        )
        base.update(kwargs)
        return PostpassWay(**base)

    def test_local_path_without_tags_is_weak_but_verified(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(self._way(), place="Munnar")
        )
        self.assertTrue(accepted)
        self.assertEqual(evidence_class, "weak")

    def test_sac_scale_makes_it_strong(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(sac_scale="hiking"),
                place="Munnar",
            )
        )
        self.assertTrue(accepted)
        self.assertEqual(evidence_class, "strong")

    def test_hut_named_trail_survives_with_sac_scale(self) -> None:
        # A real trail named after the hut it serves must not be rejected as a
        # structure-access stub when it carries hiking metadata.
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(
                    name="Panoramaweg Monte Rosa Huette",
                    sac_scale="alpine_hiking",
                ),
                place="Zermatt",
            )
        )
        self.assertTrue(accepted)
        self.assertEqual(evidence_class, "strong")

    def test_shed_access_stub_is_rejected(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(name="Path to BL shed mainroad"),
                place="Munnar",
            )
        )
        self.assertFalse(accepted)
        self.assertEqual(evidence_class, "none")

    def test_road_named_way_is_rejected(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(name="North Giri Veethi", highway="pedestrian", surface="asphalt"),
                place="Munnar",
            )
        )
        self.assertFalse(accepted)
        self.assertEqual(evidence_class, "none")

    def test_paved_surface_blocks_weak_tier_only(self) -> None:
        weak_blocked = discovery._named_way_evidence(
            self._way(surface="concrete"),
            place="Munnar",
        )
        self.assertFalse(weak_blocked[0])

        durable_allowed = discovery._named_way_evidence(
            self._way(surface="concrete", sac_scale="hiking"),
            place="Munnar",
        )
        self.assertTrue(durable_allowed[0])

    def test_multilingual_local_name_is_accepted(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(
                    name="காட்டழகர் கோவில் பாதை",
                    highway="footway",
                    length_km=4.4,
                ),
                place="Munnar",
            )
        )
        self.assertTrue(accepted)
        self.assertIn(evidence_class, {"weak", "strong"})

    def test_short_generic_stub_is_rejected(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(name="Trail", length_km=0.05),
                place="Munnar",
            )
        )
        self.assertFalse(accepted)
        self.assertEqual(evidence_class, "none")

    def test_substantial_generic_trail_is_not_rejected_on_name_alone(self) -> None:
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(name="Trail", length_km=4.2),
                place="Munnar",
            )
        )
        self.assertTrue(accepted)

    def test_numeric_or_single_character_names_are_rejected(self) -> None:
        # Observed in a real Kenya bbox: a way tagged name="1" on highway=path.
        for junk in ("1", "12", "7", "-", "..."):
            accepted, _score, _reasons, evidence_class = (
                discovery._named_way_evidence(
                    self._way(name=junk, length_km=3.0),
                    place="Nanyuki",
                )
            )
            self.assertFalse(accepted, f"{junk!r} should be rejected")
            self.assertEqual(evidence_class, "none")

    def test_dead_end_names_are_rejected_at_any_length(self) -> None:
        # Regression: a 0.4 km way named "dead end path" previously passed the
        # weak-evidence tier, which is exactly the stub class to exclude.
        for junk in (
            "dead end path",
            "dead-end path",
            "Dead End Path",
            "no through route",
            "cul-de-sac path",
            "blind lane",
        ):
            accepted, _score, _reasons, evidence_class = (
                discovery._named_way_evidence(
                    self._way(name=junk, length_km=0.4),
                    place="Munnar",
                )
            )
            self.assertFalse(accepted, f"{junk!r} should be rejected")
            self.assertEqual(evidence_class, "none")

    def test_a_named_local_trail_is_recovered_by_corroborated_evidence(
        self,
    ) -> None:
        """
        The measured recall gap is now closed, on evidence rather than a
        relaxed threshold.

        A live Wayanad search rejected genuine named local trails purely for
        falling under MIN_LOCAL_PATH_STRONG_KM: "pilakkavu path" (0.56 km),
        "athikkal kaithodu1" (0.49 km) and "kumaranpath" (1.08 km), all with
        verified geometry.

        The first fix - a lower length bar keyed on name distinctiveness - was
        reverted because it admitted "electricity officie way" and "harbour
        roadd". The fix that works asks a different question: does the name
        identify a real PLACE, and is there physical evidence it is walked?
        Facility and function words disqualify a name outright, a misspelt road
        word disqualifies it, and a built-up surface or an urban highway
        disqualifies it. That admits the trails and keeps the noise out.
        """
        for name, length in (
            ("pilakkavu path", 0.56),
            ("athikkal kaithodu1", 0.49),
            ("kumaranpath", 1.08),
            ("Amnity Walkway", 0.35),
        ):
            with self.subTest(name=name):
                accepted, _s, _reasons, evidence_class = (
                    discovery._named_way_evidence(
                        self._way(
                            name=name,
                            length_km=length,
                            surface=None,
                        ),
                        place="Wayanad",
                    )
                )
                self.assertTrue(
                    accepted,
                    f"{name!r} is a real named local trail and was lost",
                )
                # Admitted on thin relevance evidence, so it must be reported
                # as weak rather than as a confirmed route.
                self.assertEqual(evidence_class, "weak")

    def test_a_real_trail_named_after_a_road_is_still_a_trail(self) -> None:
        """
        The opposite error, and the reason the rule is about places.

        "Anamudi Ghat Road path" contains "road" and names a summit. Rejecting
        it on the word "road" would lose a genuine route, so a place word
        alongside an explicit route word carries it.
        """
        accepted, _s, _r, evidence_class = (
            discovery._named_way_evidence(
                self._way(
                    name="Anamudi Ghat Road path",
                    length_km=0.9,
                    surface=None,
                ),
                place="Munnar",
            )
        )
        self.assertTrue(accepted)
        self.assertIn(evidence_class, ("strong", "weak"))

    def test_the_recovery_rule_does_not_admit_facility_or_road_noise(
        self,
    ) -> None:
        """
        The whole point of the evidence rule, in one test.

        Every name here was rejected correctly before, and every one of them
        shares the shape that defeated the naive relaxation: a non-generic
        word next to a path word. They must stay rejected.
        """
        cases = (
            ("electricity officie way", 0.6, {}),
            ("harbour roadd", 0.9, {}),
            ("Al Azhar College Path", 0.5, {}),
            ("guest quarters road", 0.5, {}),
            ("School Main Entrance", 0.6, {}),
            ("Jain Temple Pathway", 0.4, {}),
            ("Local Path", 1.2, {}),
            ("Path to BL shed mainroad", 1.2, {}),
            (
                "North Giri Veethi",
                1.2,
                {"highway": "pedestrian", "surface": "asphalt"},
            ),
            ("Chelode Estate road", 1.1, {}),
        )
        for name, length, overrides in cases:
            with self.subTest(name=name):
                fields = {"surface": None}
                fields.update(overrides)
                accepted, _s, reasons, evidence_class = (
                    discovery._named_way_evidence(
                        self._way(name=name, length_km=length, **fields),
                        place="Wayanad",
                    )
                )
                self.assertFalse(
                    accepted,
                    f"{name!r} is not a trail and was admitted",
                )
                self.assertEqual(evidence_class, "none")
                self.assertTrue(reasons)

    def test_a_too_short_stub_is_never_a_trail_however_it_is_named(
        self,
    ) -> None:
        """
        The length floor still applies.

        A 10 m fragment named "pilakkavu footpath" is a piece of something
        else, not a route in its own right, and presenting it would pad the
        mapped count with geometry that is not a trail.
        """
        accepted, _s, reasons, evidence_class = (
            discovery._named_way_evidence(
                self._way(
                    name="pilakkavu footpath",
                    length_km=0.01,
                    surface=None,
                ),
                place="Wayanad",
            )
        )
        self.assertFalse(accepted)
        self.assertEqual(evidence_class, "none")
        self.assertIn("too short", reasons[-1])

    def test_facility_descriptors_are_never_trails(self) -> None:
        """
        The precision side of the trade documented above.

        A non-generic word next to "way" is not evidence of a trail, which is
        why the recall relaxation was not kept.
        """
        for name in (
            "electricity officie way",
            "harbour roadd",
            "guest quarters road",
            "School Main Entrance",
            "Family Health Centre Kottathara Entry",
        ):
            with self.subTest(name=name):
                accepted, _s, reasons, _c = discovery._named_way_evidence(
                    self._way(
                        name=name,
                        length_km=0.6,
                        surface=None,
                    ),
                    place="Wayanad",
                )
                self.assertFalse(accepted, f"{name!r} is not a trail")
                self.assertTrue(reasons)

    def test_evidence_reason_strings_report_real_tag_values(self) -> None:
        _a, _s, reasons, _c = discovery._named_way_evidence(
            self._way(sac_scale="H3"),
            place="Munnar",
        )
        self.assertIn("sac_scale=H3", reasons)


class RelationCompletenessTests(unittest.TestCase):
    """
    How completely OpenStreetMap maps a named route.

    The application never extends or stitches a route, so the only honest
    thing it can do is report how much of it is actually mapped. These tests
    pin that the state is derived from real relation attributes and never
    guessed, including the case that must NOT claim a route is partial.
    """

    def _relation(self, **overrides):
        fields = {
            "relation_id": 9001,
            "name": "Test Trail",
            "route": "hiking",
            "network": None,
            "ref": None,
            "description": None,
            "sac_scale": None,
            "surface": None,
            "trail_visibility": None,
            "members": [],
            "aliases": [],
            "geometry_type": "MultiLineString",
            "point_count": 2,
            "length_km": 1.0,
            "geometry": None,
            "source": "postpass_relation",
        }
        fields.update(overrides)
        return postpass.PostpassRelation(**fields)

    def test_a_multi_member_route_does_not_establish_completeness(
        self,
    ) -> None:
        result = discovery._relation_completeness(
            self._relation(), [1, 2, 3]
        )
        self.assertEqual(result["state"], "multi_member")
        self.assertEqual(result["member_count"], 3)
        # A relation split into many member ways is equally consistent with a
        # partially drawn trail, so it must not be reported as complete.
        self.assertFalse(result["establishes_complete_route"])
        self.assertIsNone(result["note"])

    def test_no_relation_state_ever_establishes_a_complete_route(self) -> None:
        for overrides, members in (
            ({}, [1, 2, 3]),
            ({}, [7]),
            ({"network": "Kerala Tourism"}, [7]),
            ({"route": None, "network": None}, [1, 2]),
        ):
            with self.subTest(state=overrides, members=members):
                result = discovery._relation_completeness(
                    self._relation(**overrides), members
                )
                self.assertFalse(
                    result["establishes_complete_route"],
                    "object type is not evidence that a route is fully mapped",
                )

    def test_a_single_member_route_discloses_that_it_is_mapped_only(
        self,
    ) -> None:
        result = discovery._relation_completeness(self._relation(), [7])
        self.assertEqual(result["state"], "single_member_route")
        self.assertIn("mapped", result["note"].lower())
        # The note must not invent a total length for the whole trail.
        self.assertNotIn("total", result["note"].lower())

    def test_a_single_member_inside_a_named_network_is_the_weakest(
        self,
    ) -> None:
        result = discovery._relation_completeness(
            self._relation(network="Kerala Tourism"), [7]
        )
        self.assertEqual(
            result["state"], "single_member_within_a_named_network"
        )
        self.assertIn("part of the real trail", result["note"])

    def test_a_non_route_relation_makes_no_completeness_claim(self) -> None:
        result = discovery._relation_completeness(
            self._relation(route=None, network=None), [1, 2]
        )
        self.assertEqual(result["state"], "not_a_route_relation")
        self.assertIsNone(result["note"])


if __name__ == "__main__":
    unittest.main()
