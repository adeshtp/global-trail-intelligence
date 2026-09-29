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
    surface: str | None = None,
) -> postpass.PostpassWay:
    return postpass.PostpassWay(
        way_id=way_id,
        name=name,
        route=route,
        highway=highway,
        sac_scale=sac_scale,
        trail_visibility=trail_visibility,
        surface=surface,
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


class SearchabilityTests(unittest.TestCase):
    """Candidate-lifecycle guarantees that were actually broken.

    Every provider is mocked. These are small deterministic checks, not
    end-to-end runs: the point is that a candidate is neither lost nor
    invented at each stage.
    """

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def relation(
        self,
        relation_id: int,
        name: str,
        *,
        aliases: list[str] | None = None,
        geometry: dict | None = None,
    ) -> postpass.PostpassRelation:
        return postpass.PostpassRelation(
            relation_id=relation_id,
            name=name,
            route="hiking",
            network=None,
            ref=None,
            description=None,
            sac_scale=None,
            surface=None,
            trail_visibility=None,
            members=[
                postpass.PostpassRelationMember("way", 900 + relation_id, "")
            ],
            aliases=aliases or [],
            geometry_type="LineString",
            point_count=2,
            length_km=2.0,
            geometry=geometry or line([[0.0, 0.0], [0.01, 0.01]]),
        )

    def semantic(
        self,
        trails_list,
        *,
        status: str = "ok",
        error: str | None = None,
    ):
        return trail_discovery.TrailDiscoveryResult(
            place="Munnar",
            trails=trails_list,
            agent_available=status in {"ok", "degraded"},
            provider="searxng+gemini",
            provider_status=status,
            search_result_count=len(trails_list),
            error=error,
        )

    def assemble(
        self,
        *,
        relations=None,
        ways=None,
        semantic=None,
        include_semantic: bool = True,
        page: int = 1,
        page_size: int = discovery.MAX_MAP_READY_RESULTS,
        place_kind: str = "area",
        place: str = "Munnar",
    ):
        """Run the real assembly stage with providers mocked out."""

        async def _run() -> dict:
            with patch.object(
                discovery,
                "find_relations_by_names",
                return_value={},
            ):
                return await discovery._assemble_discovery_result(
                    place=place,
                    scope="area",
                    latitude=0.0,
                    longitude=0.0,
                    search_bbox=(-0.5, -0.5, 0.5, 0.5),
                    bbox_source="radius",
                    semantic_result=semantic or self.semantic([]),
                    relation_result=relations if relations is not None else [],
                    way_result=ways if ways is not None else [],
                    include_semantic=include_semantic,
                    agent_result=None,
                    tile_plan={"tiles_total": 1, "tiles_queried": 1},
                    place_kind=place_kind,
                    page=page,
                    page_size=page_size,
                )

        return asyncio.run(_run())

    def candidate(self, name: str, **kwargs):
        return trail_discovery.DiscoveredTrail(name=name, **kwargs)

    # ------------------------------------------------------------------
    # 1. the user's short query drives semantic search
    # ------------------------------------------------------------------

    def test_short_user_query_is_used_for_semantic_search(self) -> None:
        """
        The semantic phrase must come from what the user typed, not from a
        resolved postal address. A long `display_name` appended to a search
        phrase matches almost nothing on the open web.
        """
        place, _bbox, _source = discovery._resolve_discovery_input(
            10.0,
            76.9,
            "Munnar",
            "Munnar, Devikulam, Idukki, Kerala, 685612, India",
            "local",
            None,
        )
        self.assertEqual(place, "Munnar")

        queries = trail_discovery._search_queries(place)
        self.assertTrue(queries)
        for query in queries:
            self.assertIn('"Munnar"', query)
            self.assertNotIn("685612", query)
            self.assertNotIn("Idukki", query)
            self.assertNotIn("India", query)
        self.assertIn('"Munnar" hiking trails trekking routes', queries)

    def test_resolved_address_is_still_used_when_no_query_is_given(self) -> None:
        """The fallback keeps working and still shortens to the locality."""
        place, _bbox, _source = discovery._resolve_discovery_input(
            10.0,
            76.9,
            "",
            "Munnar, Devikulam, Idukki, Kerala, 685612, India",
            "local",
            None,
        )
        self.assertEqual(place, "Munnar")

    # ------------------------------------------------------------------
    # 3. truncation must be reported, never denied
    # ------------------------------------------------------------------

    def test_truncation_is_reported_instead_of_claimed_as_zero(self) -> None:
        """
        Regression: with more verified trails than fit on a page, the rest
        are held back. The response used to report `mapped_truncated: 0`
        unconditionally, so a short page was indistinguishable from a
        complete result set.
        """
        relations = [
            self.relation(1000 + index, f"Trek {index:03d}")
            for index in range(12)
        ]
        result = self.assemble(relations=relations, page=1, page_size=5)

        self.assertEqual(result["result_counts"]["mapped"], 12)
        self.assertEqual(result["returned_mapped_count"], 5)
        self.assertEqual(len(result["trails"]), 5)
        self.assertEqual(result["mapped_truncated_count"], 7)
        self.assertEqual(result["diagnostics"]["mapped_truncated"], 7)
        self.assertEqual(
            result["result_counts"]["mapped_truncated"], 7
        )
        self.assertEqual(result["pagination"]["mapped_truncated"], 7)
        self.assertTrue(result["pagination"]["has_more"])

    def test_untruncated_result_reports_zero_truncation(self) -> None:
        relations = [self.relation(2000 + i, f"Trek {i}") for i in range(3)]
        result = self.assemble(relations=relations, page=1, page_size=5)
        self.assertEqual(result["mapped_truncated_count"], 0)
        self.assertFalse(result["pagination"]["has_more"])

    def test_second_page_reports_the_remainder_not_the_original_total(
        self,
    ) -> None:
        relations = [
            self.relation(3000 + index, f"Trek {index:03d}")
            for index in range(12)
        ]
        first = self.assemble(relations=relations, page=1, page_size=5)
        second = self.assemble(relations=relations, page=2, page_size=5)
        last = self.assemble(relations=relations, page=3, page_size=5)

        # 12 ranked, 5 per page: each page reports how many mapped trails are
        # NOT on it, so the count shrinks as paging advances and reaches the
        # two it actually carries.
        self.assertEqual(first["mapped_truncated_count"], 7)
        self.assertEqual(second["mapped_truncated_count"], 7)
        self.assertEqual(last["mapped_truncated_count"], 10)
        self.assertEqual(len(last["trails"]), 2)
        self.assertFalse(last["pagination"]["has_more"])

        first_ids = {t["trail_id"] for t in first["trails"]}
        second_ids = {t["trail_id"] for t in second["trails"]}
        self.assertEqual(first_ids & second_ids, set())

        # Every ranked trail is reachable across the pages exactly once.
        seen = first_ids | second_ids | {t["trail_id"] for t in last["trails"]}
        self.assertEqual(len(seen), 12)

    def test_unmapped_candidates_are_never_paginated_away(self) -> None:
        """Truncation applies to mapped results only."""
        relations = [
            self.relation(4000 + index, f"Trek {index:03d}")
            for index in range(12)
        ]
        candidates = [
            self.candidate(f"Discovered Trail {index}")
            for index in range(4)
        ]
        result = self.assemble(
            relations=relations,
            semantic=self.semantic(candidates),
            page=1,
            page_size=5,
        )
        self.assertEqual(result["unmapped_count"], 4)
        self.assertEqual(result["mapped_truncated_count"], 7)
        self.assertEqual(len(result["trails"]), 9)

    # ------------------------------------------------------------------
    # 4. alternate-name evidence, including old_name
    # ------------------------------------------------------------------

    def _row(self, **overrides) -> dict:
        row = {
            "way_id": 4242,
            "name": "Chokramudi Ridge Trail",
            "name_en": "",
            "int_name": "",
            "alt_name": "",
            "official_name": "",
            "loc_name": "",
            "short_name": "",
            "old_name": "Chokramudi Forest Trail",
            "route": "hiking",
            "highway": "path",
            "geometry_type": "LineString",
            "point_count": 2,
            "length_km": 2.0,
            "geometry": json.dumps(
                line([[0.0, 0.0], [0.01, 0.01]])
            ),
        }
        row.update(overrides)
        return row

    def test_old_name_from_a_real_row_reaches_name_matching(self) -> None:
        """
        Regression: `old_name` was never selected by the Postpass queries and
        never added to the alias list, so a trail renamed upstream could not
        be found by the name people actually know.
        """
        built = postpass._way_from_row(self._row())
        self.assertIsNotNone(built)
        self.assertIn("Chokramudi Forest Trail", built.aliases)

        # It is real evidence about this exact feature...
        score = discovery._way_score_for_item(
            self.candidate("Chokramudi Forest Trail"), built
        )
        self.assertGreaterEqual(score, 78.0)

        # ...but it must not relabel the route with a name it no longer has.
        self.assertEqual(built.name, "Chokramudi Ridge Trail")

    def test_old_name_is_selected_by_every_name_bearing_query(self) -> None:
        source = Path(postpass.__file__).read_text()
        # relation bbox, relation single, relation fallback, way bbox,
        # way single
        self.assertEqual(source.count("AS old_name"), 5)
        # And it must be read back into aliases on both builders.
        self.assertEqual(source.count('"old_name",'), 2)
        # It must never be a fallback for the primary name.
        self.assertNotIn("NULLIF(r.tags->>'old_name', '')", source)

    def test_int_name_and_english_name_still_match(self) -> None:
        built = postpass._way_from_row(
            self._row(
                name="Kolukkumai Thiruvilai",
                int_name="Kolukkumai Trail",
                old_name="",
            )
        )
        score = discovery._way_score_for_item(
            self.candidate("Kolukkumai Trail"), built
        )
        self.assertGreaterEqual(score, 78.0)

    # ------------------------------------------------------------------
    # 5. explicit OSM evidence reaches verification
    # ------------------------------------------------------------------

    def test_explicit_relation_id_reaches_verification(self) -> None:
        """Gemini's osm_relation_id is a hint, and Postpass is the authority."""
        target = self.relation(555, "Kolukkumai Trail")
        item = self.candidate(
            "Kolukkumai Trail",
            osm_references=[
                trail_discovery.OsmReference("relation", 555)
            ],
        )

        async def _resolve():
            async def _fake_get_relation(relation_id):
                return target if relation_id == 555 else None

            with patch.object(
                discovery, "get_relation", _fake_get_relation
            ), patch.object(
                discovery, "get_way", return_value=None
            ):
                return await discovery._resolve_agent_item(
                    item,
                    latitude=0.0,
                    longitude=0.0,
                    place="Munnar",
                    bbox=(-0.5, -0.5, 0.5, 0.5),
                    name_mapping={},
                    postpass_ways=[],
                    diagnostics={},
                )

        resolved = asyncio.run(_resolve())
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved["map_ready"])
        self.assertEqual(resolved["osm_id"], 555)
        self.assertEqual(resolved["osm_type"], "relation")
        self.assertEqual(
            resolved["geometry_provenance"].split("_")[0], "postpass"
            if resolved["geometry_provenance"].startswith("postpass")
            else resolved["geometry_provenance"],
        )

    def test_explicit_way_id_reaches_verification(self) -> None:
        target = way(
            777,
            "Meesonakudi Ridge",
            route="hiking",
            length_km=3.0,
        )
        item = self.candidate(
            "Meesonakudi Ridge",
            osm_references=[trail_discovery.OsmReference("way", 777)],
        )

        async def _resolve():
            async def _fake_get_way(way_id):
                return target if way_id == 777 else None

            with patch.object(
                discovery, "get_relation", return_value=None
            ), patch.object(
                discovery, "get_way", _fake_get_way
            ):
                return await discovery._resolve_agent_item(
                    item,
                    latitude=0.0,
                    longitude=0.0,
                    place="Munnar",
                    bbox=(-0.5, -0.5, 0.5, 0.5),
                    name_mapping={},
                    postpass_ways=[],
                    diagnostics={},
                )

        resolved = asyncio.run(_resolve())
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved["map_ready"])
        self.assertEqual(resolved["osm_id"], 777)
        self.assertEqual(resolved["osm_type"], "way")

    # ------------------------------------------------------------------
    # recall: a place search must actually find that place's trails
    # ------------------------------------------------------------------

    LISTICLE = {
        "title": "Top 6 Trekking Trails in Rania, Kerala | Adventure Awaits",
        "url": "https://example.invalid/riania-trekking",
        "content": (
            "Rania is a paradise for trekkers. Rania Ridge Trail is the "
            "signature walk. Cutthroat Trail suits experienced hikers. "
            "Meesonakudi Waterfall Loop begins near the tea estate. "
            "Pack water and check the forecast before you go. Munnar Bus "
            "Stand is the usual starting point."
        ),
    }

    def test_named_trails_in_a_listicle_body_are_recovered(self) -> None:
        """
        Regression: search engines answer a place query with enumeration
        pages whose TITLES are editorial. Every trail they name is in the
        body, so reading only the title returned nothing at all for a place
        search.
        """
        found = trail_discovery._names_from_snippet(
            self.LISTICLE["content"], "Rania"
        )
        for expected in (
            "Rania Ridge Trail",
            "Cutthroat Trail",
            "Meesonakudi Waterfall Loop",
        ):
            self.assertIn(expected, found)

    def test_snippet_scan_does_not_promote_business_or_facility_names(self) -> None:
        """
        The body of an enumeration page also names buses, hotels and
        operators. Those are not trails and must not become candidates.
        """
        found = trail_discovery._names_from_snippet(
            self.LISTICLE["content"], "Rania"
        )
        for junk in ("Munnar Bus Stand",):
            self.assertNotIn(junk, found)
        # A sentence-initial verb must not be read as a name.
        self.assertNotIn("Pack", found)
        self.assertNotIn("Check", found)

    def test_a_name_with_no_route_word_of_its_own_is_left_to_gemini(self) -> None:
        """
        Requiring a route word in the phrase itself is what keeps a long
        page's incidental proper nouns out. Bare place names are Gemini's
        job, since its instructions already allow them.
        """
        self.assertEqual(
            trail_discovery._names_from_snippet(
                "Visit Kolukkumai. Stay at Shola Crown Resort.", "Rania"
            ),
            [],
        )

    def test_deterministic_extraction_reads_the_body_not_only_titles(self) -> None:
        candidates = trail_discovery._deterministic_candidates(
            "Rania", [self.LISTICLE]
        )
        names = {candidate.name for candidate in candidates}
        self.assertIn("Rania Ridge Trail", names)
        self.assertIn("Cutthroat Trail", names)
        # Every candidate keeps its real source so it can be shown.
        for candidate in candidates:
            self.assertTrue(candidate.sources)
            self.assertEqual(
                candidate.sources[0].url,
                "https://example.invalid/riania-trekking",
            )

    def test_place_and_exact_trail_queries_use_different_radii(self) -> None:
        """
        Regression: every non-peak query got a flat 25 km circle, so a
        hill station or district search was confined to a town-sized area
        and lost the destinations the place is known for. An exact-trail
        query must still stay tight.
        """
        place, _bbox, source = discovery._resolve_discovery_input(
            10.0, 77.0, "Rania", "Rania", "local", None, "area"
        )
        self.assertEqual(source, "place_association_radius")

        for trail_query in (
            "Rania Ridge Trail",
            "Meesonakudi Footpath",
        ):
            _place, _bbox, source = discovery._resolve_discovery_input(
                10.0, 77.0, trail_query, "x", "local", None, "area"
            )
            self.assertEqual(
                source,
                "local_radius",
                f"{trail_query} should stay local",
            )

    def test_peak_search_keeps_its_own_tight_radius(self) -> None:
        _place, _bbox, source = discovery._resolve_discovery_input(
            10.0, 77.0, "Anamudi", "Anamudi", "local", None, "peak"
        )
        self.assertEqual(source, "peak_radius")

    def test_broad_area_scope_is_still_widest(self) -> None:
        _place, _bbox, source = discovery._resolve_discovery_input(
            10.0, 77.0, "Kerala", "Kerala", "area", None, "area"
        )
        self.assertEqual(source, "area_radius")

    def test_exact_trail_detection_is_word_based_not_name_based(self) -> None:
        """
        The detector must generalise from words, never from a list of known
        trail names. A bare place name is a place; a name plus a route word
        is a route.
        """
        self.assertFalse(
            discovery._looks_like_exact_trail_query("Rania")
        )
        self.assertTrue(
            discovery._looks_like_exact_trail_query("Rania Ridge Trail")
        )
        # A bare route word is not a route name either.
        self.assertFalse(discovery._looks_like_exact_trail_query("Trek"))

    def test_place_radius_actually_contains_a_distant_destination(self) -> None:
        """
        The widened place radius has to cover a real associated
        destination, or the fix is cosmetic. Anamudi is ~41 km from Munnar
        town, outside the old 25 km circle.
        """
        import math

        munnar = (9.9639, 77.2422)
        bbox = discovery._bbox_from_radius(
            munnar[0], munnar[1], trail_discovery.PLACE_ASSOCIATION_RADIUS_M
        )
        anamudi = (10.1733, 77.5540)
        west, south, east, north = bbox
        self.assertTrue(
            south <= anamudi[0] <= north and west <= anamudi[1] <= east
        )

    def test_search_queries_are_not_all_one_intent(self) -> None:
        """
        Regression: the query list led with two near-identical generic
        phrasings and was truncated, so the whole budget went to one kind
        of page. Distinct intents are what reach different trails.
        """
        original = trail_discovery.SEARCH_QUERIES_PER_PLACE
        trail_discovery.SEARCH_QUERIES_PER_PLACE = 6
        try:
            queries = trail_discovery._search_queries("Rania")
        finally:
            trail_discovery.SEARCH_QUERIES_PER_PLACE = original
        self.assertEqual(len(queries), 6)
        self.assertEqual(len(set(queries)), 6)

    # ------------------------------------------------------------------
    # the word "footpath" is a route type, not a judgement
    # ------------------------------------------------------------------

    def test_legitimate_named_footpath_is_accepted_as_strong_when_tagged(
        self,
    ) -> None:
        """
        A real named footpath carrying hiking metadata is a strong trail.
        The route word is never the reason it would be rejected.
        """
        candidate = way(
            1,
            "Thirunelli Footpath",
            route="hiking",
            sac_scale="hiking",
            length_km=3.0,
        )
        accepted, score, _reasons, evidence_class = (
            discovery._named_way_evidence(candidate, place="Wayanad")
        )
        self.assertTrue(accepted)
        self.assertEqual(evidence_class, "strong")

    def test_every_route_type_word_is_accepted_for_a_named_trail(self) -> None:
        """
        footpath / path / trail / walk / route / track are all real OSM
        route types. A named route containing one must be kept, and no
        blacklist is consulted to decide that.
        """
        for name in (
            "Neelimala Footpath",
            "Neelimala Path",
            "Neelimala Trail",
            "Neelimala Walk",
            "Neelimala Route",
            "Neelimala Track",
            "Neelimala Nature Trail",
        ):
            candidate = way(2, name, route="hiking", length_km=3.0)
            accepted, _score, _reasons, _cls = (
                discovery._named_way_evidence(candidate, place="Wayanad")
            )
            self.assertTrue(
                accepted, f"{name!r} rejected because of its route word"
            )

    def test_facility_named_footpaths_are_rejected_by_facility_evidence(
        self,
    ) -> None:
        """
        The control: the same route word on a name that identifies a
        facility or a function. Rejected on the facility evidence, which is
        a different mechanism from the route-word rule.
        """
        for name in (
            "School Footpath",
            "College Footpath",
            "Hospital Footpath",
            "Temple Footpath",
            "Parking Footpath",
            "Bus Stand Footpath",
        ):
            candidate = way(3, name, length_km=3.0)
            accepted, _score, _reasons, _cls = (
                discovery._named_way_evidence(candidate, place="Wayanad")
            )
            self.assertFalse(
                accepted, f"{name!r} should be rejected as facility access"
            )

    def test_utility_named_paths_never_reach_the_strong_tier(self) -> None:
        """
        A known, deliberate limitation, pinned so it cannot drift.

        "Power House Footpath" names a facility rather than a destination,
        and nothing lexical separates it from a genuine untagged local path:
        both are long, verified and untagged. So it reaches the weak tier.
        The assertion is deliberately only that it is not STRONG, because
        weak is reported separately with a weaker-evidence label. Closing
        this properly needs a corpus of real rejected facility names, and must
        not be done by dropping the evidence genuine untagged trails rely on.
        """
        for name in ("Power House Footpath", "Substation Footpath"):
            candidate = way(4, name, length_km=3.0)
            _accepted, _score, _reasons, evidence_class = (
                discovery._named_way_evidence(candidate, place="Wayanad")
            )
            self.assertNotEqual(evidence_class, "strong")

    def test_weak_tier_still_admits_an_untagged_generic_local_path(self) -> None:
        """
        Pins the behaviour the recall work must not break. "Local Path"
        names no place, so requiring a place word would reject a genuine
        untagged trail. Kept as weak-but-verified on physical evidence.
        """
        candidate = way(
            5, "Local Path", length_km=1.2, surface="unpaved"
        )
        accepted, _score, _reasons, evidence_class = (
            discovery._named_way_evidence(candidate, place="Munnar")
        )
        self.assertTrue(accepted)
        self.assertEqual(evidence_class, "weak")

    def test_unverified_reference_never_invents_geometry(self) -> None:
        """A hint that does not resolve yields no trail at all."""
        item = self.candidate(
            "Ghost Trail",
            osm_references=[trail_discovery.OsmReference("relation", 1)],
        )

        async def _resolve():
            with patch.object(
                discovery, "get_relation", return_value=None
            ), patch.object(
                discovery, "get_way", return_value=None
            ):
                return await discovery._resolve_agent_item(
                    item,
                    latitude=0.0,
                    longitude=0.0,
                    place="Munnar",
                    bbox=(-0.5, -0.5, 0.5, 0.5),
                    name_mapping={},
                    postpass_ways=[],
                    diagnostics={},
                )

        self.assertIsNone(asyncio.run(_resolve()))

    # ------------------------------------------------------------------
    # 2. relevant candidates survive as UNMAPPED
    # ------------------------------------------------------------------

    def test_relevant_candidate_becomes_unmapped_not_absent(self) -> None:
        """Failing geometry verification must not delete the candidate."""
        candidates = [
            self.candidate("Kolukkumai Trail"),
            self.candidate("Meesonakudi Ridge"),
        ]

        async def _never_resolves(*_a, **_kw):
            return None

        original = discovery._resolve_agent_item
        with patch.object(
            discovery, "_resolve_agent_item", _never_resolves
        ):
            result = self.assemble(
                relations=[], semantic=self.semantic(candidates)
            )

        self.assertIsNotNone(original)
        self.assertEqual(result["unmapped_count"], 2)
        names = {t["name"] for t in result["trails"]}
        self.assertEqual(names, {"Kolukkumai Trail", "Meesonakudi Ridge"})
        for trail in result["trails"]:
            self.assertEqual(trail["state"], "UNMAPPED")
            self.assertFalse(trail["map_ready"])
            self.assertIsNone(trail["geometry"])
            self.assertIsNone(trail["osm_id"])
            self.assertEqual(trail["unmapped_kind"], "no_verified_geometry")
            # It never claims the trail exists at a mapped location.
            self.assertEqual(trail["verification_state"], "UNVERIFIED")
            self.assertEqual(trail["map_state"], "NOT_MAP_READY")

    def test_unmapped_result_is_not_reported_as_successful_empty(self) -> None:
        candidates = [self.candidate("Kolukkumai Trail")]

        async def _never_resolves(*_a, **_kw):
            return None

        with patch.object(
            discovery, "_resolve_agent_item", _never_resolves
        ):
            result = self.assemble(
                relations=[], semantic=self.semantic(candidates)
            )

        # Providers answered with zero rows AND the semantic layer
        # contributed, so this is not "this area has no trails".
        self.assertEqual(result["status"], "no_provider_data")
        self.assertEqual(result["unmapped_count"], 1)
        self.assertTrue(
            result["coverage"]["provider_returned_no_rows"]
        )

    # ------------------------------------------------------------------
    # 6. scope behaviour
    # ------------------------------------------------------------------

    def test_exact_trail_query_keeps_aliases(self) -> None:
        """An exact-trail query resolves via a real OSM alternate name."""
        relation = self.relation(
            888, "Kolukkumai Thiruvilai", aliases=["Kolukkumai Trail"]
        )
        item = self.candidate("Kolukkumai Trail")
        self.assertGreaterEqual(
            discovery._relation_score_for_item(item, relation), 78.0
        )

    def test_peak_search_reports_measured_association(self) -> None:
        """A summit query ranks routes by measured proximity, not assumption."""
        near = self.relation(
            9001,
            "Summit Trail",
            geometry=line([[0.0, 0.0], [0.0005, 0.0005]]),
        )
        far = self.relation(
            9002,
            "Far Valley Trail",
            geometry=line([[0.4, 0.4], [0.41, 0.41]]),
        )
        result = self.assemble(
            relations=[far, near], place_kind="peak", place="Anamudi"
        )
        self.assertTrue(result["peak_search"]["is_peak_search"])
        self.assertEqual(result["trails"][0]["name"], "Summit Trail")
        self.assertIn(
            result["trails"][0]["peak_association"],
            {"summit_route", "peak_approach"},
        )
        # The distant route is kept, just ranked below.
        self.assertEqual(len(result["trails"]), 2)

    def test_local_place_search_rejects_infrastructure_junk(self) -> None:
        """Real infrastructure in the bbox must not become a trail."""
        junk = [
            way(6001, "School Footpath", highway="footway"),
            way(6002, "Main Entrance", highway="footway"),
            way(6003, "Hospital Access Road", highway="path"),
            way(6004, "Overbridge Footpath", highway="footway"),
            way(6005, "Temple Access Path", highway="path"),
            way(6006, "Short Cut", highway="footway"),
            way(6007, "Subway Passage", highway="footway"),
            way(6008, "Emergency Exit Path", highway="footway"),
            way(6009, "Parking Approach", highway="footway"),
        ]
        result = self.assemble(relations=[], ways=junk, place="Munnar")
        self.assertEqual(result["trails"], [])
        self.assertEqual(
            result["providers"]["postpass_ways"]["accepted"], 0
        )
        self.assertEqual(
            result["providers"]["postpass_ways"]["rejected"], len(junk)
        )

    def test_legitimate_named_footpath_is_kept(self) -> None:
        """
        The word "footpath" is a real OSM route type, not a judgement. A
        named local footpath is a genuine trail and must not be rejected
        because of the word alone. No special case is made for this name:
        it is accepted for the same structural reason any named route is.
        """
        named = [
            way(7001, "Kolukkumai Footpath", route="hiking", length_km=4.0),
            way(7002, "Thirunelli Footpath", route="hiking", length_km=3.0),
            way(7003, "Panchavankaduva Footpath", highway="path",
                length_km=2.5),
        ]
        result = self.assemble(relations=[], ways=named, place="Wayanad")
        names = {trail["name"] for trail in result["trails"]}
        self.assertEqual(
            names,
            {
                "Kolukkumai Footpath",
                "Thirunelli Footpath",
                "Panchavankaduva Footpath",
            },
        )
        for trail in result["trails"]:
            self.assertTrue(trail["map_ready"])

    def test_named_footpath_is_kept_even_with_no_hiking_tags(self) -> None:
        """
        An untagged but real, named, long local path still qualifies. The
        rule is evidence-based: the name identifies a place and the length
        is walkable.
        """
        untagged = way(7100, "Neelimala Footpath", length_km=3.0)
        accepted, score, _reasons, evidence_class = (
            discovery._named_way_evidence(untagged, place="Wayanad")
        )
        self.assertTrue(accepted)
        self.assertEqual(evidence_class, "weak")

    def test_route_word_alone_never_rejects_a_named_trail(self) -> None:
        """
        "footpath", "path", "trail", "walk", "route" and "track" are route
        types. None of them, alone, is grounds for rejection.
        """
        for name in (
            "Kolukkumai Footpath",
            "Kolukkumai Path",
            "Kolukkumai Trail",
            "Kolukkumai Walk",
            "Kolukkumai Route",
            "Kolukkumai Track",
            "Kolukkumai Nature Trail",
        ):
            candidate = way(
                7200, name, route="hiking", length_km=4.0
            )
            accepted, _score, _reasons, _cls = (
                discovery._named_way_evidence(
                    candidate, place="Wayanad"
                )
            )
            self.assertTrue(
                accepted, f"{name!r} was rejected for its route word"
            )

    def test_local_place_search_keeps_a_real_named_trail(self) -> None:
        """Junk filtering must not swallow a genuine route in the same box."""
        good = way(
            6100,
            "Kolukkumai Trail",
            route="hiking",
            length_km=4.0,
            geometry=line([[0.0, 0.0], [0.02, 0.02]]),
        )
        result = self.assemble(
            relations=[],
            ways=[good, way(6101, "School Footpath", highway="footway")],
            place="Munnar",
        )
        self.assertEqual([t["name"] for t in result["trails"]],
                         ["Kolukkumai Trail"])
        self.assertTrue(result["trails"][0]["map_ready"])

    # ------------------------------------------------------------------
    # 8. provider failure is not an empty area
    # ------------------------------------------------------------------

    def test_provider_failure_is_distinguishable_from_no_results(
        self,
    ) -> None:
        failure = RuntimeError("postpass unreachable")
        result = self.assemble(relations=failure, ways=[])
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["coverage"]["provider_failed"])
        self.assertFalse(result["coverage"]["coverage_complete"])
        self.assertEqual(
            result["providers"]["postpass_relations"]["status"], "failed"
        )
        self.assertIn(
            "could not be reached", result["coverage"]["note"]
        )

    def test_semantic_provider_failure_reports_degradation(self) -> None:
        result = self.assemble(
            relations=[self.relation(7001, "Kolukkumai Trail")],
            semantic=self.semantic(
                [], status="unavailable", error="Semantic search failed"
            ),
        )
        self.assertEqual(result["status"], "partial")
        semantic = result["providers"]["semantic"]
        # The distinction a client needs is the status, not the prose: a
        # degraded semantic layer must never read as a successful search.
        self.assertEqual(semantic["status"], "unavailable")
        self.assertEqual(semantic["candidates_found"], 0)
        self.assertFalse(semantic["matched"])
        self.assertTrue(result["trails"])
        self.assertFalse(result["coverage"]["coverage_complete"])

    def test_semantic_candidates_keep_relevant_unmapped_ones(self) -> None:
        """
        Junk must still be rejected. A candidate is kept only because it
        survived extraction as a real trail-shaped name, not because it was
        merely mentioned.
        """
        kept = trail_discovery.DiscoveredTrail(name="Kolukkumai Trail")
        dropped = trail_discovery.DiscoveredTrail(name="10 Best Things To Do")
        candidates = [
            trail_discovery.DiscoveredTrail(
                name=item.name,
                aliases=item.aliases,
                osm_references=item.osm_references,
                sources=item.sources,
                location_context=item.location_context,
            )
            for item in (kept, dropped)
            if trail_discovery._looks_like_candidate(
                item.name, "Munnar"
            )
        ]
        self.assertEqual([c.name for c in candidates], ["Kolukkumai Trail"])

    def test_genuine_empty_area_is_reported_as_such(self) -> None:
        result = self.assemble(relations=[], ways=[])
        self.assertEqual(result["status"], "no_provider_data")
        self.assertTrue(result["coverage"]["provider_returned_no_rows"])
        self.assertFalse(result["coverage"]["provider_failed"])

    # ------------------------------------------------------------------
    # 12 / 13. folding and contract
    # ------------------------------------------------------------------

    def test_relation_membership_folding_still_works(self) -> None:
        """Unchanged by this task, and the most easily broken guarantee."""
        parent = self.relation(8001, "Kolukkumai Trail")
        member = way(
            8002,
            "Kolukkumai Trail",
            route=None,
            length_km=1.0,
            geometry=parent.geometry,
        )
        collapsed = discovery._collapse_connected_named_ways(
            [discovery._way_to_trail(
                member,
                latitude=0.0,
                longitude=0.0,
                place="Munnar",
                discovery_source="OpenStreetMap via Postpass",
                match_score=90.0,
            )]
        )
        self.assertEqual(len(collapsed), 1)

    def test_response_contract_exposes_both_states(self) -> None:
        """The frontend relies on these keys staying present."""
        candidates = [self.candidate("Kolukkumai Trail")]

        async def _never_resolves(*_a, **_kw):
            return None

        with patch.object(
            discovery, "_resolve_agent_item", _never_resolves
        ):
            result = self.assemble(
                relations=[self.relation(8500, "Verified Trail")],
                semantic=self.semantic(candidates),
            )

        for key in (
            "trails",
            "result_counts",
            "pagination",
            "coverage",
            "providers",
            "diagnostics",
            "mapped_count",
            "unmapped_count",
            "mapped_truncated_count",
        ):
            self.assertIn(key, result)

        states = {trail["state"] for trail in result["trails"]}
        self.assertEqual(states, {"MAP_READY", "UNMAPPED"})
        for trail in result["trails"]:
            # Map-ready entries carry real geometry; unmapped never do.
            if trail["state"] == "MAP_READY":
                self.assertTrue(trail["geometry"])
            else:
                self.assertIsNone(trail["geometry"])
                self.assertIsNone(trail["osm_id"])


if __name__ == "__main__":
    unittest.main()
