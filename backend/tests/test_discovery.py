from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routes import discovery, trails
from app.services import postpass, trail_discovery


def line(coordinates: list[list[float]]) -> dict:
    return {
        "type": "LineString",
        "coordinates": coordinates,
    }


def way(
    way_id: int,
    name: str,
    *,
    route: str | None = None,
    highway: str = "path",
    length_km: float = 1.0,
    aliases: list[str] | None = None,
    geometry: dict | None = None,
    sac_scale: str | None = None,
    trail_visibility: str | None = None,
) -> postpass.PostpassWay:
    return postpass.PostpassWay(
        way_id=way_id,
        name=name,
        route=route,
        highway=highway,
        sac_scale=sac_scale,
        trail_visibility=trail_visibility,
        surface=None,
        smoothness=None,
        tracktype=None,
        access=None,
        incline=None,
        incline_direction=None,
        width=None,
        assisted_trail=None,
        aliases=aliases or [],
        geometry_type="LineString",
        point_count=2,
        length_km=length_km,
        geometry=geometry or line([[0.0, 0.0], [0.01, 0.01]]),
    )


class DiscoveryTests(unittest.TestCase):
    def test_segment_crossing_bbox_is_detected_without_inside_vertex(self) -> None:
        geometry = line([[-1.0, -1.0], [2.0, 2.0]])
        self.assertTrue(
            discovery._geometry_intersects_bbox(
                geometry,
                (0.0, 0.0, 1.0, 1.0),
            )
        )
        self.assertFalse(
            discovery._geometry_intersects_bbox(
                geometry,
                (20.0, 20.0, 21.0, 21.0),
            )
        )

    def test_alias_and_diacritic_matching(self) -> None:
        score = discovery._name_match_score(
            ["Lakshmi Hills Trek"],
            ["Lakshmi Hills", "Lachmi Hills"],
        )
        self.assertGreaterEqual(score, 78.0)
        self.assertEqual(
            discovery._normalise_name("Mésápulimala Trek"),
            "mesapulimala trek",
        )

    def test_degenerate_bbox_is_expanded_instead_of_returning_false_empty(self) -> None:
        tiny = (77.2043831, 10.0875088, 77.2044831, 10.0876088)
        expanded, was_expanded = discovery._enforce_minimum_bbox(tiny)
        self.assertTrue(was_expanded)
        west, south, east, north = expanded
        self.assertLessEqual(west, tiny[0])
        self.assertLessEqual(south, tiny[1])
        self.assertGreaterEqual(east, tiny[2])
        self.assertGreaterEqual(north, tiny[3])
        self.assertGreater(
            (north - south) * 111.32,
            discovery.MIN_SEARCH_BBOX_KM,
        )

    def test_usable_bbox_is_left_untouched(self) -> None:
        wide = (75.773149, 11.451361, 76.4435318, 11.9786826)
        result, was_expanded = discovery._enforce_minimum_bbox(wide)
        self.assertFalse(was_expanded)
        self.assertEqual(result, wide)

    def test_transliterated_names_match_without_claiming_unrelated_identity(self) -> None:
        self.assertGreaterEqual(
            discovery._name_match_score(
                ["चेम्ब्रा"],
                ["Chembra"],
            ),
            78.0,
        )
        self.assertLess(
            discovery._name_match_score(
                ["चेम्ब्रा"],
                ["Kakkadampoyil"],
            ),
            78.0,
        )

    def test_editorial_web_headings_are_not_semantic_trails(self) -> None:
        self.assertFalse(
            trail_discovery._looks_like_candidate(
                "7 Best Treks in Wayanad",
                "Wayanad",
            )
        )
        self.assertFalse(
            trail_discovery._looks_like_candidate(
                "Adventure & Nature",
                "Wayanad",
            )
        )
        self.assertTrue(
            trail_discovery._looks_like_candidate(
                "Chembra Peak",
                "Wayanad",
            )
        )

    def test_hiking_evidence_rejects_ordinary_infrastructure(self) -> None:
        accepted, _score, reasons, _cls = discovery._named_way_evidence(
            way(
                1,
                "Al Azhar College Path",
                length_km=0.5,
            ),
            place="Munnar",
        )
        self.assertFalse(accepted)
        self.assertTrue(reasons)

    def test_hiking_evidence_rejects_tagged_infrastructure_names(self) -> None:
        accepted, _score, reasons, _cls = discovery._named_way_evidence(
            way(
                7,
                "School Main Entrance",
                route="hiking",
                length_km=0.5,
            ),
            place="Munnar",
        )
        self.assertFalse(accepted)
        self.assertIn("ordinary access/infrastructure name", reasons)

    def test_small_named_route_is_not_rejected_by_length_alone(self) -> None:
        accepted, _score, _reasons, _cls = discovery._named_way_evidence(
            way(
                8,
                "Lakshmi Hills",
                length_km=0.01,
            ),
            place="Munnar",
        )
        self.assertTrue(accepted)

    def test_religious_access_path_without_route_evidence_is_rejected(self) -> None:
        accepted, _score, reasons, _cls = discovery._named_way_evidence(
            way(
                9,
                "Jain Temple Pathway",
                length_km=0.2,
            ),
            place="Munnar",
        )
        self.assertFalse(accepted)
        self.assertIn(
            "religious site access without route evidence",
            reasons,
        )

    def test_generic_descriptor_paths_are_not_accepted_without_evidence(self) -> None:
        for name in (
            "dead end path",
            "electricity officie way",
            "harbour roadd",
        ):
            with self.subTest(name=name):
                accepted, _score, reasons, _cls = discovery._named_way_evidence(
                    way(11, name, length_km=0.6),
                    place="Kozhikode",
                )
                self.assertFalse(accepted)
                self.assertTrue(reasons)
                # The reason may be name-specific (for example a stated dead
                # end) or the generic insufficient-evidence reason.
                self.assertTrue(
                    any(
                        marker in " ".join(reasons)
                        for marker in (
                            "insufficient hiking evidence",
                            "dead end",
                            "road-oriented name",
                            "non-descriptive name",
                        )
                    ),
                    reasons,
                )

    def test_destination_named_path_is_accepted_without_english_keywords(self) -> None:
        accepted, _score, reasons, _cls = discovery._named_way_evidence(
            way(
                12,
                "Kurishumala path",
                sac_scale="H3",
                length_km=1.1,
            ),
            place="Munnar",
        )
        self.assertTrue(accepted)
        self.assertIn("sac_scale=H3", reasons)

    def test_hiking_evidence_accepts_structured_and_semantic_ways(self) -> None:
        accepted, _score, _reasons, _cls = discovery._named_way_evidence(
            way(
                2,
                "Anamudi",
                length_km=3.0,
            ),
            place="Munnar",
            semantic_score=100.0,
        )
        self.assertTrue(accepted)

        accepted, _score, _reasons, _cls = discovery._named_way_evidence(
            way(
                3,
                "Local footway",
                route="walking",
                length_km=0.2,
            ),
            place="Munnar",
        )
        self.assertTrue(accepted)

    def test_walking_relation_is_supported(self) -> None:
        relation = postpass._relation_from_row(
            {
                "relation_id": 99,
                "name": "Local Walking Route",
                "route": "walking",
                "network": "lwn",
                "description": None,
                "sac_scale": "hiking",
                "surface": "gravel",
                "trail_visibility": "good",
                "members": [
                    {"type": "W", "ref": 501, "role": ""},
                    {"type": "W", "ref": 502, "role": "alternate"},
                ],
                "name:en": "Local Walking Route",
                "int_name": None,
                "alt_name": "Walk Route",
                "official_name": None,
                "loc_name": None,
                "short_name": None,
                "geometry_type": "LineString",
                "point_count": 2,
                "length_km": 2.5,
                "geometry": json.dumps(line([[0.0, 0.0], [0.1, 0.1]])),
            }
        )
        self.assertIsNotNone(relation)
        self.assertEqual(relation.route, "walking")
        self.assertIn("Walk Route", relation.aliases)
        self.assertEqual(
            [member.ref for member in relation.members],
            [501, 502],
        )
        self.assertEqual(
            relation.members[1].role,
            "alternate",
        )

    def test_connected_component_preserves_all_members_without_osm_id(self) -> None:
        candidates = [
            discovery._way_to_trail(
                way(
                    10,
                    "Forest Route",
                    route="hiking",
                    length_km=1.0,
                    geometry=line([[0.0, 0.0], [0.01, 0.0]]),
                ),
                latitude=0.0,
                longitude=0.0,
                place="Test",
                discovery_source="test",
                match_score=100.0,
            ),
            discovery._way_to_trail(
                way(
                    11,
                    "Forest Route",
                    route="hiking",
                    length_km=1.0,
                    geometry=line([[0.01, 0.0], [0.02, 0.0]]),
                ),
                latitude=0.0,
                longitude=0.0,
                place="Test",
                discovery_source="test",
                match_score=100.0,
            ),
        ]

        collapsed = discovery._collapse_connected_named_ways(candidates)
        self.assertEqual(len(collapsed), 1)
        component = collapsed[0]
        self.assertEqual(component["osm_type"], "component")
        self.assertIsNone(component["osm_id"])
        self.assertEqual(component["member_way_ids"], [10, 11])
        self.assertEqual(
            component["geometry"]["type"],
            "MultiLineString",
        )
        self.assertEqual(len(component["geometry"]["coordinates"]), 2)
        self.assertTrue(component["geometry_hash"])

    def test_same_named_member_way_folds_into_its_relation(self) -> None:
        """Every member of an accepted relation is a section, not a second trail.

        Decided by OSM identity (membership) alone, never by name agreement
        or geometry proximity: way 101 shares the relation's name and way 102
        carries its own distinct name, but both are listed by the relation,
        so the single relation card represents the trail. Both members stay
        reachable through the relation's member_way_ids, which preserve IDs,
        order and roles — no identity is invented or removed.
        """
        geom = line([[0.0, 0.0], [0.01, 0.01]])
        relation = {
            "osm_type": "relation",
            "osm_id": 1,
            "trail_id": "relation:1",
            "name": "Chokramudi Trail",
            "aliases": [],
            "member_way_ids": [101, 102],
            "geometry": geom,
        }
        same_name_member = {
            "osm_type": "way",
            "osm_id": 101,
            "trail_id": "way:101",
            "name": "Chokramudi trail",
            "aliases": [],
            "member_way_ids": [101],
            "geometry": geom,
        }
        distinct_member = {
            "osm_type": "way",
            "osm_id": 102,
            "trail_id": "way:102",
            "name": "Kallar path",
            "aliases": [],
            "member_way_ids": [102],
            "geometry": geom,
        }
        collapsed = discovery._collapse_connected_named_ways(
            [relation, same_name_member, distinct_member]
        )
        remaining = {
            (item["osm_type"], item.get("osm_id")) for item in collapsed
        }
        self.assertIn(("relation", 1), remaining)
        self.assertNotIn(("way", 101), remaining)
        self.assertNotIn(("way", 102), remaining)
        kept = next(
            item for item in collapsed if item["osm_type"] == "relation"
        )
        self.assertIn(101, kept["member_way_ids"])
        self.assertIn(102, kept["member_way_ids"])

    def test_component_members_fold_the_same_way(self) -> None:
        """A connected component absorbs its own same-named sections too."""
        geom = line([[0.0, 0.0], [0.01, 0.01]])
        component = {
            "osm_type": "component",
            "osm_id": None,
            "trail_id": "component:abc",
            "name": "Ridge Walk",
            "aliases": [],
            "member_way_ids": [201, 202],
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [[0.0, 0.0], [0.01, 0.01]],
                    [[0.01, 0.01], [0.02, 0.02]],
                ],
            },
        }
        section = {
            "osm_type": "way",
            "osm_id": 201,
            "trail_id": "way:201",
            "name": "Ridge Walk",
            "aliases": [],
            "member_way_ids": [201],
            "geometry": geom,
        }
        collapsed = discovery._collapse_connected_named_ways(
            [component, section]
        )
        remaining = {
            (item["osm_type"], item.get("osm_id")) for item in collapsed
        }
        self.assertNotIn(("way", 201), remaining)

    def test_short_member_way_is_retained_through_its_relation(self) -> None:
        """A short member is not discarded for its length.

        Way 103 is 50 m of unremarkable path with no hiking tags of its
        own, so it could never stand as a primary trail — but it is listed
        by the relation, so it folds into the single route card and stays
        reachable in member_way_ids rather than vanishing.
        """
        geom = line([[0.0, 0.0], [0.01, 0.01]])
        relation = {
            "osm_type": "relation",
            "osm_id": 1,
            "trail_id": "relation:1",
            "name": "Chokramudi Trail",
            "aliases": [],
            "member_way_ids": [101, 103],
            "geometry": geom,
        }
        short_member = {
            "osm_type": "way",
            "osm_id": 103,
            "trail_id": "way:103",
            "name": "Estate shortcut",
            "aliases": [],
            "member_way_ids": [103],
            "geometry": geom,
        }
        collapsed = discovery._collapse_connected_named_ways(
            [relation, short_member]
        )
        self.assertEqual(len(collapsed), 1)
        kept = collapsed[0]
        self.assertEqual(kept["trail_id"], "relation:1")
        self.assertIn(103, kept["member_way_ids"])

    def test_two_same_name_relations_remain_separate_trails(self) -> None:
        """Membership decides sections; it never merges distinct relations."""
        collapsed = discovery._collapse_connected_named_ways(
            [
                {
                    "osm_type": "relation",
                    "osm_id": 1,
                    "trail_id": "relation:1",
                    "name": "Alp Loop",
                    "aliases": [],
                    "member_way_ids": [101],
                    "geometry": line([[0.0, 0.0], [0.01, 0.01]]),
                },
                {
                    "osm_type": "relation",
                    "osm_id": 2,
                    "trail_id": "relation:2",
                    "name": "Alp Loop",
                    "aliases": [],
                    "member_way_ids": [202],
                    "geometry": line([[5.0, 5.0], [5.01, 5.01]]),
                },
            ]
        )
        remaining = {
            (item["osm_type"], item.get("osm_id")) for item in collapsed
        }
        self.assertIn(("relation", 1), remaining)
        self.assertIn(("relation", 2), remaining)

    def test_two_same_name_unconnected_ways_remain_separate(self) -> None:
        """Ways that share a name but no endpoint are distinct trails."""
        collapsed = discovery._collapse_connected_named_ways(
            [
                {
                    "osm_type": "way",
                    "osm_id": 7,
                    "trail_id": "way:7",
                    "name": "Ridge path",
                    "aliases": [],
                    "member_way_ids": [7],
                    "geometry": line([[0.0, 0.0], [0.01, 0.01]]),
                },
                {
                    "osm_type": "way",
                    "osm_id": 8,
                    "trail_id": "way:8",
                    "name": "Ridge path",
                    "aliases": [],
                    "member_way_ids": [8],
                    "geometry": line([[5.0, 5.0], [5.01, 5.01]]),
                },
            ]
        )
        remaining = {
            (item["osm_type"], item.get("osm_id")) for item in collapsed
        }
        self.assertIn(("way", 7), remaining)
        self.assertIn(("way", 8), remaining)

    def test_disconnected_relation_geometry_is_preserved_whole(self) -> None:
        """One route may be several disconnected pieces; all are kept as-is."""
        geometry = {
            "type": "MultiLineString",
            "coordinates": [
                [[0.0, 0.0], [1.0, 0.0]],
                [[10.0, 0.0], [11.0, 0.0]],
            ],
        }
        relation = {
            "osm_type": "relation",
            "osm_id": 1,
            "trail_id": "relation:1",
            "name": "Split Traverse",
            "aliases": [],
            "member_way_ids": [101, 102],
            "geometry": geometry,
        }
        collapsed = discovery._collapse_connected_named_ways([relation])
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["geometry"], geometry)

    def test_collapse_invents_no_geometry_or_identity(self) -> None:
        """Every output coordinate and identity must come from the input."""
        geom = line([[0.0, 0.0], [0.01, 0.01]])
        relation = {
            "osm_type": "relation",
            "osm_id": 1,
            "trail_id": "relation:1",
            "name": "Chokramudi Trail",
            "aliases": [],
            "member_way_ids": [101, 102],
            "geometry": geom,
        }
        members = [
            {
                "osm_type": "way",
                "osm_id": way_id,
                "trail_id": f"way:{way_id}",
                "name": name,
                "aliases": [],
                "member_way_ids": [way_id],
                "geometry": geom,
            }
            for way_id, name in (
                (101, "Chokramudi trail"),
                (102, "Kallar path"),
            )
        ]
        collapsed = discovery._collapse_connected_named_ways(
            [relation, *members]
        )
        self.assertEqual(
            [item["trail_id"] for item in collapsed],
            ["relation:1"],
        )
        self.assertEqual(collapsed[0]["geometry"], geom)
        self.assertEqual(
            list(collapsed[0]["member_way_ids"]),
            [101, 102],
        )

    def test_unmapped_candidate_has_no_fake_osm_identity(self) -> None:
        item = trail_discovery.DiscoveredTrail(
            name="Example Trek",
            aliases=[],
            osm_references=[],
            sources=[],
            location_context="Example",
        )
        unmapped = discovery._unmapped_trail(item)
        self.assertEqual(unmapped["state"], "UNMAPPED")
        self.assertIsNone(unmapped["osm_type"])
        self.assertIsNone(unmapped["osm_id"])
        self.assertIsNone(unmapped["geometry"])

    def test_selected_geometry_analysis_preserves_disconnected_parts(self) -> None:
        result = trails.normalize_selected_geometry(
            {
                "type": "MultiLineString",
                "coordinates": [
                    [[0.0, 0.0], [1.0, 0.0]],
                    [[10.0, 0.0], [11.0, 0.0]],
                ],
            }
        )
        self.assertEqual(result["component_count"], 2)
        self.assertEqual(result["geometry_type"], "MultiLineString")
        coordinates = result["geometry"]["coordinates"]
        self.assertEqual(len(coordinates), 2)
        self.assertNotEqual(coordinates[0][-1], coordinates[1][0])

    def test_gemini_relation_and_way_ids_are_preserved(self) -> None:
        response_payload = {
            "trails": [
                {
                    "name": "Example Trail",
                    "aliases": ["Example Route"],
                    "osm_relation_id": 123,
                    "osm_way_id": 456,
                    "source_urls": [
                        "https://www.openstreetmap.org/relation/123",
                        "https://www.openstreetmap.org/way/456",
                    ],
                }
            ]
        }

        class FakeResponse:
            text = json.dumps(response_payload)

        class FakeModels:
            async def generate_content(self, **_kwargs):
                return FakeResponse()

        class FakeAio:
            models = FakeModels()

        class FakeClient:
            aio = FakeAio()

            def __init__(self, **_kwargs):
                pass

            def close(self):
                return None

        fake_google = types.ModuleType("google")
        fake_genai = types.ModuleType("google.genai")
        fake_types = types.ModuleType("google.genai.types")
        fake_google.genai = fake_genai
        fake_types.GenerateContentConfig = lambda **_kwargs: None
        fake_genai.Client = FakeClient
        fake_genai.types = fake_types

        with patch.dict(
            sys.modules,
            {
                "google": fake_google,
                "google.genai": fake_genai,
                "google.genai.types": fake_types,
            },
        ):
            with patch.object(trail_discovery, "GEMINI_API_KEY", "test"):
                candidates = asyncio.run(
                    trail_discovery._run_gemini(
                        "Example",
                        [
                            {
                                "title": "Example Trail",
                                "url": "https://www.openstreetmap.org/relation/123",
                            },
                            {
                                "title": "Example Route",
                                "url": "https://www.openstreetmap.org/way/456",
                            },
                        ],
                    )
                )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            {
                (reference.type, reference.id)
                for reference in candidates[0].osm_references
            },
            {("relation", 123), ("way", 456)},
        )


class FallbackCaptureTests(unittest.TestCase):
    """A real fallback-sourced discovery response, replayed offline.

    Captured live from Meesapulimala during a Postpass outage: every row
    came from Overpass, and the area still mapped. Replaying it pins the
    contract a fallback response must satisfy — real OSM ids, honest
    provenance naming the serving source, MAP_READY only with geometry —
    without spending a provider call.
    """

    FIXTURE = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "discovery"
        / "meesapulimala.json"
    )

    def test_fallback_capture_maps_real_trails_with_honest_provenance(
        self,
    ) -> None:
        payload = json.loads(self.FIXTURE.read_text())
        self.assertEqual(payload["status"], "success")
        trails = payload["trails"]
        self.assertGreaterEqual(len(trails), 1)
        for trail in trails:
            self.assertEqual(trail["state"], "MAP_READY")
            self.assertTrue(trail["map_ready"])
            self.assertIn(trail["osm_type"], ("way", "relation", "component"))
            # Ways and relations carry their own OSM id; components
            # aggregate verified members instead of having one id.
            if trail["osm_type"] in ("way", "relation"):
                self.assertIsInstance(trail["osm_id"], int)
                self.assertGreater(trail["osm_id"], 0)
            else:
                self.assertTrue(trail["member_way_ids"])
            self.assertIn(
                trail["geometry_provenance"],
                (
                    "overpass_way",
                    "overpass_relation_members",
                    "connected_named_osm_ways",
                ),
            )
            self.assertIn("Overpass", trail["source"])
            geometry = trail["geometry"]
            self.assertIn(
                geometry["type"], ("LineString", "MultiLineString")
            )
            self.assertTrue(geometry["coordinates"])
        providers = payload["providers"]
        self.assertEqual(
            providers["postpass_ways"]["source"], "overpass"
        )


if __name__ == "__main__":
    unittest.main()
