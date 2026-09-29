"""
HTTP response contracts, exercised through the real application.

These run the actual FastAPI app in-process and call the actual routes, with
only the outbound provider functions replaced. That is deliberate: a test that
calls a service function directly proves the service, not the wire format the
browser receives. Every key asserted here is a key the TypeScript client
declares, so a backend rename that the interface has not adopted fails the
suite instead of failing silently in the browser.

Nothing here contacts a provider. ``offline_guard`` proves it.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.routes import search as search_module
from app.services import postpass
from app.services.trail_discovery import TrailDiscoveryResult


# A real mapped way, used as the selected selection. The geometry is a real
# two-part shape so the component-aware paths are exercised.
SELECTED_WAY: dict[str, Any] = {
    "trail_id": "way:338242023",
    "osm_type": "way",
    "osm_id": 338242023,
    "name": "Way to Kalhatty Falls",
    "candidate_type": "named_hiking_way",
    "route_type": None,
    "highway_type": "path",
    "source_difficulty": None,
    "difficulty": None,
    "surface": "earth",
    "trail_visibility": "yes",
    "length_km": 0.26,
    "distance_km": 0.26,
    "distance_from_search_km": 1.2,
    "map_ready": True,
    "state": "MAP_READY",
    "geometry": {
        "type": "LineString",
        "coordinates": [
            [76.680, 11.477],
            [76.682, 11.478],
        ],
    },
    "geometry_hash": "a" * 64,
    "geometry_provenance": "postpass_way",
    "member_way_ids": [338242023],
    "ordered_way_ids": [338242023],
}

WEATHER = {
    "source": "Open-Meteo",
    "latitude": 11.4775,
    "longitude": 76.681,
    "timezone": "Asia/Kolkata",
    "current": {
        "time": "2026-09-28T06:00",
        "temperature": 18.2,
        "humidity": 80.0,
        "precipitation": 0.0,
        "rain": 0.0,
        "showers": 0.0,
        "snowfall": 0.0,
        "precipitation_probability": 10,
        "wind_speed": 5.0,
        "weather_code": 0,
        "weather_condition": "Clear sky",
    },
    "recent_precipitation": {"24h_mm": 0.0, "48h_mm": 0.0, "72h_mm": 1.2},
    "recent_rain": {"24h_mm": 0.0, "48h_mm": 0.0, "72h_mm": 1.0},
    "forecast": {
        "precipitation_mm": 0.0,
        "rain_mm": 0.0,
        "precipitation_probability_max": 20,
    },
}

ELEVATION = {
    "source": "Open-Meteo",
    "sampled_points": 2,
    "component_count": 1,
    "sampled_component_count": 1,
    "all_components_covered": True,
    "route_distance_km": 0.26,
    "profile": [
        {
            "component_index": 0,
            "component_distance_km": 0.0,
            "distance_km": 0.0,
            "longitude": 76.680,
            "latitude": 11.477,
            "elevation_m": 1575.0,
        },
        {
            "component_index": 0,
            "component_distance_km": 0.26,
            "distance_km": 0.26,
            "longitude": 76.682,
            "latitude": 11.478,
            "elevation_m": 1583.0,
        },
    ],
    "metrics": {
        "min_elevation_m": 1575.0,
        "max_elevation_m": 1583.0,
        "elevation_range_m": 8.0,
        "elevation_gain_m": 8.0,
        "elevation_loss_m": 7.0,
        "average_slope_percent": 2.41,
        "max_slope_percent": 4.64,
        "terrain_available": True,
    },
}


def _client() -> TestClient:
    return TestClient(app)


class HealthContractTests(unittest.TestCase):
    def test_health_reports_configuration_not_unverified_health(self) -> None:
        payload = _client().get("/api/health").json()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("database", payload)
        self.assertIn("providers", payload)
        for provider in payload["providers"].values():
            self.assertIn("configured", provider)
            # Availability is never claimed without a check.
            self.assertEqual(provider["availability"], "not_checked")
        self.assertFalse(payload["database"]["used_by_request_path"])
        # The health response names the serving capabilities, so a stale
        # server answering with outdated code is distinguishable from the
        # current one instead of hiding behind the same 200.
        capabilities = payload["capabilities"]
        self.assertTrue(capabilities["overpass_fallback"])
        self.assertTrue(capabilities["route_aggregation"])
        self.assertTrue(
            capabilities["overpass_url"].startswith("https://")
        )
        self.assertIn("overpass_fallback", payload["providers"])

    def test_the_serving_stack_loads_no_orm(self) -> None:
        """
        Serving must not pull in a database stack.

        The PostGIS persistence layer was removed: no endpoint ever read
        from or wrote to it. Importing the app must therefore never load
        an ORM, geometry types or a database driver.
        """
        import subprocess
        import sys

        code = (
            "import sys;"
            "from app.main import app;"
            "assert not [m for m in sys.modules if m.startswith('app.models')],"
            " 'schema models imported while serving';"
            "leaked = [m for m in ('sqlmodel', 'sqlalchemy', 'geoalchemy2',"
            " 'psycopg') if m in sys.modules];"
            "assert not leaked, f'database stack imported while serving: {leaked}';"
            "print('ok')"
        )
        environment = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": "backend",
        }
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            timeout=120,
        )
        self.assertEqual(
            result.returncode, 0, result.stderr[-2000:]
        )
        self.assertIn("ok", result.stdout)


class SearchContractTests(unittest.TestCase):
    def test_search_returns_the_fields_the_client_declares(self) -> None:
        # Nominatim returns boundingbox as [south, north, west, east], and
        # the route re-emits it as [west, south, east, north]. A malformed
        # box is dropped rather than passed through, so a swapped or partial
        # box cannot quietly misplace the search.
        nominatim = [
            {
                "lat": "11.4775",
                "lon": "76.681",
                "display_name": "Kalpetta, Kerala, India",
                "name": "Kalpetta",
                "osm_type": "relation",
                "osm_id": 1234,
                "category": "boundary",
                "type": "administrative",
                "addresstype": "town",
                "boundingbox": ["11.3", "11.6", "76.5", "76.9"],
                "address": {"town": "Kalpetta"},
                "importance": 0.4,
            }
        ]

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> list[dict[str, Any]]:
                return nominatim

        class _Client:
            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def get(self, *a: object, **k: object) -> _Response:
                return _Response()

        with patch.object(
            search_module.httpx,
            "AsyncClient",
            lambda **kwargs: _Client(),
        ):
            payload = _client().get(
                "/api/search", params={"q": "Kalpetta"}
            ).json()

        self.assertIn("query", payload)
        self.assertEqual(len(payload["results"]), 1)
        result = payload["results"][0]
        for key in (
            "name",
            "display_name",
            "latitude",
            "longitude",
            "osm_type",
            "osm_id",
            "class",
            "type",
            "addresstype",
            "boundingbox",
            "address",
            "importance",
        ):
            self.assertIn(key, result)
        self.assertEqual(result["latitude"], 11.4775)
        self.assertEqual(result["boundingbox"], [76.5, 11.3, 76.9, 11.6])

    def test_a_malformed_bounding_box_is_dropped_not_guessed(self) -> None:
        self.assertIsNone(
            search_module._parse_boundingbox(["76.5", "11.3"])
        )
        # Latitudes in the longitude slot cannot pass the range check.
        self.assertIsNone(
            search_module._parse_boundingbox(
                ["11.3", "11.6", "200.0", "201.0"]
            )
        )
        self.assertEqual(
            search_module._parse_boundingbox(
                ["11.3", "11.6", "76.5", "76.9"]
            ),
            [76.5, 11.3, 76.9, 11.6],
        )


class DiscoveryContractTests(unittest.TestCase):
    def test_discovery_returns_every_field_the_client_declares(self) -> None:
        async def _ways(*a: object, **k: object) -> list:
            return []

        async def _relations(*a: object, **k: object) -> list:
            return []

        async def _semantic(*a: object, **k: object):
            return TrailDiscoveryResult(
                place="Test Place",
                trails=[],
                agent_available=False,
                provider="searxng",
                provider_status="unavailable",
            )

        with patch(
            "app.routes.discovery.discover_named_trail_ways_in_bbox",
            _ways,
        ), patch(
            "app.routes.discovery.discover_relations_in_bbox",
            _relations,
        ), patch(
            "app.routes.discovery.find_relations_by_names",
            AsyncMock(return_value={}),
        ), patch(
            "app.routes.discovery.discover_trail_candidates",
            _semantic,
        ):
            payload = _client().get(
                "/api/osm/trails/discover",
                params={
                    "latitude": 11.4775,
                    "longitude": 76.681,
                    "search_query": "Test Place",
                    "location_name": "Test Place",
                    "scope": "local",
                    "place_kind": "area",
                },
            ).json()

        for key in (
            "status",
            "trails",
            "mapped_count",
            "returned_mapped_count",
            "unmapped_count",
            "enrichment_pending",
            "result_counts",
            "peak_search",
            "pagination",
            "coverage",
            "providers",
            "diagnostics",
        ):
            self.assertIn(key, payload)

        for key in (
            "definition",
            "relevance_accepted",
            "mapped",
            "unmapped",
            "ranked",
            "shown",
            "shown_unmapped",
            "weak_evidence",
        ):
            self.assertIn(key, payload["result_counts"])

        for key in (
            "page",
            "page_size",
            "total_ranked",
            "returned",
            "has_more",
            "next_page",
            "unmapped_returned",
        ):
            self.assertIn(key, payload["pagination"])

        coverage = payload["coverage"]
        for key in (
            "area_considered",
            "area_considered_source",
            "area_km2",
            "area_queried",
            "tiled",
            "tiles_total",
            "tiles_queried",
            "tiles_failed",
            "tiles_skipped",
            "coverage_complete",
            "provider_returned_no_rows",
            "candidates_found",
            "candidates_accepted",
            "candidates_verified",
            "candidates_ranked",
            "candidates_returned",
        ):
            self.assertIn(key, coverage)

        # Zero provider rows is a coverage gap, and it is reported as one.
        self.assertEqual(payload["status"], "no_provider_data")
        self.assertFalse(coverage["coverage_complete"])
        self.assertTrue(coverage["provider_returned_no_rows"])
        self.assertIn("no_provider_data", payload["status"])
        # The box actually queried is always reported, so an implausible
        # search area is diagnosable rather than mysterious.
        self.assertEqual(len(coverage["area_considered"]), 4)
        # The note must not assert a cause the system cannot distinguish:
        # an absent mirror area and a mis-pointed search box look identical.
        note = coverage["note"].lower()
        self.assertIn("mirror", note)
        self.assertIn("coordinates", note)


class IntelligenceContractTests(unittest.TestCase):
    def _intelligence(self, payload: dict[str, Any]) -> dict[str, Any]:
        way = postpass.PostpassWay(
            way_id=338242023,
            name="Way to Kalhatty Falls",
            route=None,
            highway="path",
            sac_scale=None,
            trail_visibility="yes",
            surface="earth",
            smoothness=None,
            tracktype=None,
            access=None,
            incline=None,
            incline_direction=None,
            width=None,
            assisted_trail=None,
            aliases=[],
            geometry_type="LineString",
            point_count=2,
            length_km=0.26,
            geometry=SELECTED_WAY["geometry"],
        )
        with patch(
            "app.routes.trails.get_way", AsyncMock(return_value=way)
        ), patch(
            "app.routes.trails.get_weather",
            AsyncMock(return_value=WEATHER),
        ), patch(
            "app.routes.trails.get_elevation_profile",
            AsyncMock(return_value=ELEVATION),
        ):
            response = _client().post(
                "/api/trails/intelligence", json=payload
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_intelligence_returns_every_field_the_client_declares(self) -> None:
        body = self._intelligence({"trail": SELECTED_WAY})

        for key in (
            "generated_at",
            "trail",
            "weather_coordinate",
            "analysis",
            "terrain",
            "weather",
            "difficulty",
            "condition",
            "suitability",
            "gear",
            "route_complexity",
            "providers",
        ):
            self.assertIn(key, body)

        analysis = body["analysis"]
        for key in (
            "geometry_type",
            "geometry_status",
            "component_count",
            "coordinate_count",
            "distance_km",
            "analysis_distance_km",
            "start_coordinate",
            "end_coordinate",
            "midpoint_coordinate",
            "bbox",
        ):
            self.assertIn(key, analysis)

        for key in (
            "source",
            "ml",
            "reconciliation",
            "display",
            "display_provenance",
            "model_readiness",
        ):
            self.assertIn(key, body["difficulty"])
        for key in (
            "sac_scale",
            "class",
            "mapping",
            "authoritative",
        ):
            self.assertIn(key, body["difficulty"]["source"])
        for key in (
            "available",
            "estimate",
            "estimate_tier",
            "estimate_label",
            "estimate_description",
            "estimate_recorded_grade",
            "confidence",
            "confidence_kind",
            "probabilities",
            "feature_coverage",
            "missing_features",
            "model_name",
            "model_version",
            "feature_contract",
            "observation_unit",
            "terrain_features_used",
            "authoritative_for_complete_route",
            "prediction_basis",
            "reason",
            "reliability",
        ):
            self.assertIn(key, body["difficulty"]["ml"])

        # The estimate carries its own qualification, so a client cannot
        # render a difficulty without the number that bounds it.
        ml = body["difficulty"]["ml"]
        reliability = ml["reliability"]
        self.assertIsNotNone(reliability["held_out_accuracy"])
        self.assertIsNotNone(reliability["majority_baseline_accuracy"])
        self.assertTrue(reliability["beats_majority_baseline"])
        self.assertTrue(reliability["per_class"])
        # The tier is one the runtime actually serves, and the recorded
        # grades it came from travel with it.
        self.assertIn(
            ml["estimate_tier"],
            ["walking", "mountain", "alpine"],
        )
        self.assertIn("hiking", ml["estimate_recorded_grade"])

        for key in (
            "available",
            "status",
            "likelihood",
            "score",
            "observation_type",
            "summary",
            "evidence",
            "missing_evidence",
            "factors",
        ):
            self.assertIn(key, body["condition"])
        for key in (
            "level",
            "headline",
            "condition_status",
            "route_complexity_score",
            "assessment_scope",
            "factors",
        ):
            self.assertIn(key, body["suitability"])
        for key in (
            "items",
            "groups",
            "essential_count",
            "recommended_count",
            "conditional_count",
            "basis",
            "missing_evidence",
        ):
            self.assertIn(key, body["gear"])
        for key in (
            "available",
            "score",
            "label",
            "observation_unit",
            "method",
            "components",
            "missing_evidence",
        ):
            self.assertIn(key, body["route_complexity"])

        self.assertEqual(
            body["providers"], {"weather": "ok", "elevation": "ok"}
        )

    def test_weather_is_located_on_the_route_not_the_searched_place(self) -> None:
        body = self._intelligence({"trail": SELECTED_WAY})
        coordinate = body["weather_coordinate"]
        self.assertAlmostEqual(coordinate["latitude"], 11.4775, places=2)
        self.assertAlmostEqual(coordinate["longitude"], 76.681, places=2)
        self.assertIn("midpoint", coordinate["basis"].lower())
        # It must not silently be the searched place.
        self.assertNotAlmostEqual(coordinate["latitude"], 76.681, places=0)

    def test_a_route_without_verified_geometry_is_refused(self) -> None:
        unverified = dict(SELECTED_WAY)
        unverified["map_ready"] = False
        unverified["geometry"] = None
        response = _client().post(
            "/api/trails/intelligence", json={"trail": unverified}
        )
        self.assertEqual(response.status_code, 422)

    def test_a_relation_is_answered_from_its_members_not_its_totals(
        self,
    ) -> None:
        """A route selection gets a member-vote answer over the wire.

        Three verified member ways are served through the real intelligence
        route with every provider mocked. The difficulty must carry the
        aggregation (not a way-level prediction of route totals), and the
        per-member split must be present so the client can show it.
        """
        relation_geometry = {
            "type": "LineString",
            "coordinates": [
                [76.680, 11.477],
                [76.684, 11.479],
                [76.688, 11.481],
            ],
        }
        relation = postpass.PostpassRelation(
            relation_id=9001,
            name="Test Ridge Traverse",
            route="hiking",
            network=None,
            ref=None,
            description=None,
            sac_scale=None,
            surface=None,
            trail_visibility=None,
            members=[
                postpass.PostpassRelationMember(
                    member_type="W", ref=101, role=""
                ),
                postpass.PostpassRelationMember(
                    member_type="W", ref=102, role=""
                ),
                postpass.PostpassRelationMember(
                    member_type="W", ref=103, role=""
                ),
            ],
            aliases=[],
            geometry_type="LineString",
            point_count=3,
            length_km=1.1,
            geometry=relation_geometry,
        )

        def _member_way(way_id: int) -> postpass.PostpassWay:
            base_lon = 76.680 + (way_id - 101) * 0.004
            return postpass.PostpassWay(
                way_id=way_id,
                name=f"Section {way_id}",
                route=None,
                highway="path",
                sac_scale=None,
                trail_visibility=None,
                surface="ground",
                smoothness=None,
                tracktype=None,
                access=None,
                incline=None,
                incline_direction=None,
                width=None,
                assisted_trail=None,
                aliases=[],
                geometry_type="LineString",
                point_count=4,
                length_km=0.4,
                geometry={
                    "type": "LineString",
                    "coordinates": [
                        [base_lon, 11.477],
                        [base_lon + 0.001, 11.4775],
                        [base_lon + 0.002, 11.478],
                        [base_lon + 0.003, 11.4785],
                    ],
                },
            )

        async def _get_way(way_id: int) -> postpass.PostpassWay:
            return _member_way(int(way_id))

        async def _get_ways(way_ids) -> dict:
            return {int(i): _member_way(int(i)) for i in way_ids}

        trail = {
            "trail_id": "relation:9001",
            "osm_type": "relation",
            "osm_id": 9001,
            "name": "Test Ridge Traverse",
            "map_ready": True,
            "state": "MAP_READY",
            "geometry": relation_geometry,
            "geometry_hash": "b" * 64,
            "geometry_provenance": "postpass_relation",
            "member_way_ids": [101, 102, 103],
            "ordered_way_ids": [101, 102, 103],
        }
        with patch(
            "app.routes.trails.get_relation",
            AsyncMock(return_value=relation),
        ), patch(
            "app.routes.trails.get_way", _get_way
        ), patch(
            "app.routes.trails.get_ways", _get_ways
        ), patch(
            "app.routes.trails.get_weather",
            AsyncMock(return_value=WEATHER),
        ), patch(
            "app.routes.trails.get_elevation_profile",
            AsyncMock(return_value=ELEVATION),
        ):
            response = _client().post(
                "/api/trails/intelligence", json={"trail": trail}
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        ml = body["difficulty"]["ml"]
        self.assertTrue(ml["available"], ml.get("reason"))
        aggregation = ml["aggregation"]
        self.assertIsNotNone(aggregation)
        self.assertEqual(aggregation["scored_members"], 3)
        self.assertEqual(aggregation["total_members"], 3)
        self.assertEqual(
            sum(aggregation["tier_counts"].values()), 3
        )
        self.assertIn(
            ml["estimate_tier"], ("walking", "mountain", "alpine")
        )
        # Route totals must never reach the model: the answer is a vote.
        self.assertEqual(
            ml["observation_unit"], "osm_way_aggregated_to_route"
        )


class WeatherAndElevationContractTests(unittest.TestCase):
    def test_weather_endpoint_passes_through_the_normalised_shape(self) -> None:
        with patch(
            "app.routes.weather.get_weather",
            AsyncMock(return_value=WEATHER),
        ):
            body = _client().get(
                "/api/weather",
                params={"latitude": 11.4775, "longitude": 76.681},
            ).json()
        self.assertEqual(body["source"], "Open-Meteo")
        self.assertIn("current", body)
        self.assertIn("recent_rain", body)
        self.assertIn("recent_precipitation", body)
        self.assertIn("forecast", body)

    def test_elevation_endpoint_keeps_components_and_metrics(self) -> None:
        with patch(
            "app.routes.elevation.get_elevation_profile",
            AsyncMock(return_value=ELEVATION),
        ):
            body = _client().post(
                "/api/elevation",
                json={"geometry": SELECTED_WAY["geometry"]},
            ).json()
        self.assertEqual(body["component_count"], 1)
        self.assertEqual(len(body["profile"]), 2)
        for key in (
            "min_elevation_m",
            "max_elevation_m",
            "elevation_gain_m",
            "elevation_loss_m",
            "elevation_range_m",
            "average_slope_percent",
            "max_slope_percent",
            "terrain_available",
        ):
            self.assertIn(key, body["metrics"])

    def test_elevation_rejects_an_unsupported_geometry(self) -> None:
        response = _client().post(
            "/api/elevation",
            json={"geometry": {"type": "Point", "coordinates": [0, 0]}},
        )
        self.assertEqual(response.status_code, 422)


class ProductContractTests(unittest.TestCase):
    def test_products_response_shape_when_the_provider_is_absent(self) -> None:
        import os
        from unittest.mock import patch as _patch

        from app.services import products as service

        service._CACHE.clear()
        service._INFLIGHT.clear()
        with _patch.dict(os.environ, {"TAVILY_API_KEY": ""}), _patch.object(
            service.settings, "TAVILY_API_KEY", ""
        ):
            body = _client().post(
                "/api/trails/products",
                json={
                    "intelligence": {
                        "gear": {
                            "items": [
                                {
                                    "category": "footwear",
                                    "item": "Broken-in trail shoes",
                                    "priority": "essential",
                                    "reason": "Surface evidence",
                                }
                            ]
                        },
                        "condition": {"likelihood": "favorable"},
                    }
                },
            ).json()

        self.assertEqual(body["status"], "unavailable")
        self.assertEqual(body["provider"], "tavily")
        group = body["groups"][0]
        # Every field the TypeScript client declares must be present.
        for key in (
            "category",
            "item",
            "priority",
            "reason",
            "query",
            "status",
            "product_results",
            "related_results",
            "rejected_results",
            "search_link",
            "result_source",
            "image_available",
            "message",
        ):
            self.assertIn(key, group)
        for key in ("title", "url", "source"):
            self.assertIn(key, group["search_link"])
        # An absent provider is not a successful search.
        self.assertEqual(
            group["result_source"], "provider_unavailable"
        )


class AssistantContractTests(unittest.TestCase):
    def test_assistant_response_does_not_ship_a_debug_payload(self) -> None:
        from app.services import assistant as service

        with patch.object(service, "_api_key", lambda: ""):
            body = _client().post(
                "/api/trails/assistant",
                json={
                    "question": "Where did this route come from?",
                    "trail": SELECTED_WAY,
                    "intelligence": {
                        "analysis": {"distance_km": 0.26},
                        "terrain": ELEVATION,
                        "weather": WEATHER,
                        "condition": {
                            "available": True,
                            "status": "favorable",
                            "summary": "No adverse signal.",
                            "factors": [],
                            "evidence": [],
                            "missing_evidence": [],
                        },
                    },
                },
            ).json()

        self.assertIn("status", body)
        self.assertIn("answer", body)
        self.assertIn("grounded", body)
        # The grounded context is internal. Shipping the whole intelligence
        # payload on every answer is dead weight and needless exposure.
        self.assertNotIn("context", body)
        self.assertIn("retrieved_sources", body)
        self.assertIn("corpus_size", body)


class DifficultyReadinessContractTests(unittest.TestCase):
    def test_readiness_describes_the_model_that_is_actually_loaded(
        self,
    ) -> None:
        body = _client().get(
            "/api/trails/difficulty/readiness"
        ).json()
        self.assertTrue(body["ready"])
        for key in (
            "model_name",
            "model_version",
            "task",
            "classes",
            "artifact",
            "feature_contract",
            "model_features",
            "base_features",
            "derived_features",
            "categorical_features",
            "observation_unit",
            "evaluation",
            "majority_baseline",
            "usefulness_gate_passed",
            "per_class",
            "natural_sample_holdout",
            "recorded_grades",
            "grade_to_tier",
            "tier_labels",
            "reliability",
            "limitations",
            "estimator",
        ):
            self.assertIn(key, body)
        self.assertEqual(body["observation_unit"], "osm_way")
        self.assertFalse(body["authoritative_for_complete_route"])
        self.assertEqual(
            body["classes"], ["walking", "mountain", "alpine"]
        )

        # The usefulness gate is published, not just satisfied silently.
        self.assertTrue(body["usefulness_gate_passed"])
        for metric in ("accuracy", "macro_f1", "balanced_accuracy"):
            self.assertGreater(
                body["evaluation"][metric],
                body["majority_baseline"][metric],
                metric,
            )

        # Official and estimated difficulty share one vocabulary, so the two
        # are comparable and the mapping is inspectable.
        self.assertEqual(
            body["grade_to_tier"]["hiking"], "walking"
        )
        self.assertEqual(
            body["grade_to_tier"]["difficult_alpine_hiking"], "alpine"
        )
        self.assertEqual(
            set(body["tier_labels"]), set(body["classes"])
        )


if __name__ == "__main__":
    unittest.main()
