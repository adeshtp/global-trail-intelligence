from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.routes import discovery, trails
from app.services import elevation, postpass, trail_discovery, weather


class ResilienceTests(unittest.TestCase):
    def test_weather_cache_deduplicates_repeated_reads(self) -> None:
        weather._WEATHER_CACHE.clear()
        weather._WEATHER_INFLIGHT.clear()
        payload = {
            "source": "test",
            "latitude": 10.0,
            "longitude": 77.0,
            "timezone": "UTC",
            "current": {
                "time": "2026-01-01T00:00",
                "temperature": 20.0,
                "humidity": 50.0,
                "precipitation": 0.0,
                "rain": 0.0,
                "showers": 0.0,
                "snowfall": 0.0,
                "precipitation_probability": 0.0,
                "wind_speed": 1.0,
                "weather_code": 0,
                "weather_condition": "Clear sky",
            },
            "recent_precipitation": {},
            "recent_rain": {},
            "forecast": {},
        }
        with patch.object(
            weather,
            "_fetch_weather_uncached",
            new=AsyncMock(return_value=payload),
        ) as fetch:
            first = asyncio.run(weather.get_weather(10.0, 77.0))
            second = asyncio.run(weather.get_weather(10.0, 77.0))
        self.assertEqual(first, second)
        self.assertEqual(fetch.await_count, 1)

    def test_elevation_cache_deduplicates_repeated_reads(self) -> None:
        elevation._ELEVATION_CACHE.clear()
        elevation._ELEVATION_INFLIGHT.clear()
        geometry = {
            "type": "LineString",
            "coordinates": [[0.0, 0.0], [0.01, 0.0]],
        }
        payload = {
            "source": "test",
            "sampled_points": 2,
            "component_count": 1,
            "sampled_component_count": 1,
            "all_components_covered": True,
            "route_distance_km": 1.0,
            "profile": [],
            "metrics": {},
        }
        with patch.object(
            elevation,
            "_fetch_elevation_uncached",
            new=AsyncMock(return_value=payload),
        ) as fetch:
            first = asyncio.run(elevation.get_elevation_profile(geometry))
            second = asyncio.run(elevation.get_elevation_profile(geometry))
        self.assertEqual(first, second)
        self.assertEqual(fetch.await_count, 1)

    def test_a_transient_server_error_is_retried(self) -> None:
        """
        A 5xx from a free public mirror is a transient condition.

        Observed live: Postpass answered "503 Service Unavailable - No server
        is available to handle this request" for a real area, and the area was
        then reported as successfully searched and empty. The retry absorbs a
        short server-side outage without changing what is verified, and a
        client error is never retried because it cannot succeed.
        """
        from unittest.mock import patch

        from app.services import postpass

        attempts: list[int] = []

        class _Response:
            def __init__(self, status: int) -> None:
                self.status_code = status
                self.text = "No server is available to handle this request."

            def json(self) -> dict:
                return {"result": [{"ok": 1}]}

        class _Client:
            def __init__(self, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def post(self, *a: object, **k: object) -> _Response:
                attempts.append(1)
                # First two attempts are the server being busy; then it works.
                return _Response(503 if len(attempts) <= 2 else 200)

        original = postpass.POSTPASS_SERVER_BACKOFF_SECONDS
        postpass.POSTPASS_SERVER_BACKOFF_SECONDS = 0.01
        try:
            with patch.object(
                postpass.httpx, "AsyncClient", _Client
            ):
                rows = asyncio.run(
                    postpass._execute_sql("SELECT 1")
                )
        finally:
            postpass.POSTPASS_SERVER_BACKOFF_SECONDS = original

        self.assertEqual(rows, [{"ok": 1}])
        self.assertEqual(
            len(attempts), 3, "a transient 503 should be retried"
        )

    def test_a_client_error_is_never_retried(self) -> None:
        """A bad query fails identically every time; retrying wastes quota."""
        from unittest.mock import patch

        from app.services import postpass

        attempts: list[int] = []

        class _Response:
            status_code = 400
            text = "syntax error"

            def json(self) -> dict:
                return {}

        class _Client:
            def __init__(self, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def post(self, *a: object, **k: object) -> _Response:
                attempts.append(1)
                return _Response()

        with patch.object(postpass.httpx, "AsyncClient", _Client):
            with self.assertRaises(RuntimeError):
                asyncio.run(postpass._execute_sql("SELECT bad"))

        self.assertEqual(len(attempts), 1)

    def test_a_persistent_server_error_surfaces_as_a_failure(self) -> None:
        """A real outage must not be masked by retrying."""
        from unittest.mock import patch

        from app.services import postpass

        attempts: list[int] = []

        class _Response:
            status_code = 503
            text = "No server is available to handle this request."

            def json(self) -> dict:
                return {}

        class _Client:
            def __init__(self, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def post(self, *a: object, **k: object) -> _Response:
                attempts.append(1)
                return _Response()

        original = postpass.POSTPASS_SERVER_BACKOFF_SECONDS
        postpass.POSTPASS_SERVER_BACKOFF_SECONDS = 0.01
        try:
            with patch.object(
                postpass.httpx, "AsyncClient", _Client
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    asyncio.run(postpass._execute_sql("SELECT 1"))
        finally:
            postpass.POSTPASS_SERVER_BACKOFF_SECONDS = original

        self.assertIn("503", str(ctx.exception))
        self.assertEqual(
            len(attempts), postpass.POSTPASS_SERVER_RETRIES + 1
        )

    def test_discovery_reports_provider_failure_without_success_empty(self) -> None:
        async def unavailable(*_args, **_kwargs):
            raise RuntimeError("provider down")

        # Every Postpass/OSM entry point is stubbed, including semantic name
        # resolution, so this test can never reach the network.
        with patch.object(
            discovery,
            "discover_trail_candidates",
            new=unavailable,
        ):
            with patch.object(
                discovery,
                "discover_relations_in_bbox",
                new=unavailable,
            ):
                with patch.object(
                    discovery,
                    "discover_named_trail_ways_in_bbox",
                    new=unavailable,
                ):
                    with patch.object(
                        discovery,
                        "find_relations_by_names",
                        new=unavailable,
                    ):
                        result = asyncio.run(
                            discovery.discover_trails(
                                latitude=10.0,
                                longitude=77.0,
                                search_query="Test Place",
                                location_name="Test Place",
                                scope="local",
                                bbox=None,
                            )
                        )
                        enriched = asyncio.run(
                            discovery.enrich_trails(
                                latitude=10.0,
                                longitude=77.0,
                                search_query="Test Place",
                                location_name="Test Place",
                                scope="local",
                                bbox=None,
                            )
                        )
        # A provider that could not be reached is NOT an empty area. The
        # previous behaviour reported "empty" here, which claimed the search
        # succeeded and found nothing - a fact the system has no way to know,
        # because it never reached the source. Found live as a Postpass 503
        # for a real area, where the response said the area was searched and
        # coverage was complete.
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["coverage"]["provider_failed"])
        self.assertFalse(result["coverage"]["coverage_complete"])
        self.assertIn(
            "could not be reached", result["coverage"]["note"]
        )
        self.assertEqual(
            result["providers"]["postpass_relations"]["status"],
            "failed",
        )
        # Stage 1 does not run semantic discovery at all, so it reports it
        # as pending rather than claiming a failure or a success.
        self.assertEqual(
            result["providers"]["semantic"]["status"],
            "pending",
        )
        # The real semantic failure is still surfaced honestly by stage 2.
        # The enrichment stage reports the same fact the same way.
        self.assertEqual(enriched["status"], "unavailable")
        self.assertEqual(
            enriched["providers"]["semantic"]["status"],
            "unavailable",
        )

    def test_a_provider_that_returns_nothing_is_not_an_empty_area(self) -> None:
        """
        A provider that answers with zero rows for the whole searched area is
        a coverage gap in its own data, not evidence that the area has no
        trails. Reporting that as a successful empty search would be a claim
        the system cannot support, so it gets its own status and coverage is
        not claimed.
        """
        import asyncio

        async def empty(*args: object, **kwargs: object) -> list:
            return []

        with patch.object(
            discovery,
            "discover_relations_in_bbox",
            empty,
        ), patch.object(
            discovery,
            "discover_named_trail_ways_in_bbox",
            empty,
        ):
            result = asyncio.run(
                discovery.discover_trails(
                    latitude=10.0,
                    longitude=77.0,
                    search_query="Test Place",
                    location_name="Test Place",
                    scope="local",
                    place_kind="area",
                    bbox="76.9,76.8,77.4,77.4",
                )
            )

        self.assertEqual(result["status"], "no_provider_data")
        self.assertEqual(result["trails"], [])
        self.assertEqual(result["mapped_count"], 0)
        self.assertFalse(result["coverage"]["coverage_complete"])
        self.assertTrue(
            result["coverage"]["provider_returned_no_rows"]
        )
        note = result["coverage"]["note"]
        # The note must state what is true, and only that. Two causes produce
        # this signal and the system cannot separate them, so it may not pick
        # one: an absent mirror area and a mis-pointed search box are
        # indistinguishable from the response alone.
        self.assertIn("neither means the area has no trails", note)
        self.assertIn("mirror", note)
        self.assertIn("coordinates", note)
        # The providers are reported as having answered, because they did. It
        # is their data that is empty, not their availability.
        self.assertEqual(
            result["providers"]["postpass_ways"]["status"], "ok"
        )

    def test_a_partial_provider_result_is_not_a_coverage_gap(self) -> None:
        """One real row is enough to prove the provider covers the area."""
        import asyncio

        async def fake_relations(bbox, *, limit=100):
            return []

        async def fake_ways(bbox, *, limit=2000):
            return [
                postpass.PostpassWay(
                    way_id=4242,
                    name="Test Ridge Trek",
                    route=None,
                    highway="path",
                    sac_scale="hiking",
                    trail_visibility=None,
                    surface=None,
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
                    length_km=2.0,
                    geometry={
                        "type": "LineString",
                        "coordinates": [[76.9, 10.0], [76.92, 10.02]],
                    },
                )
            ]

        with patch.object(
            discovery,
            "discover_relations_in_bbox",
            fake_relations,
        ), patch.object(
            discovery,
            "discover_named_trail_ways_in_bbox",
            fake_ways,
        ):
            result = asyncio.run(
                discovery.discover_trails(
                    latitude=10.0,
                    longitude=77.0,
                    search_query="Test Place",
                    location_name="Test Place",
                    scope="local",
                    place_kind="area",
                    bbox="76.8,9.9,77.1,10.1",
                )
            )

        self.assertNotEqual(result["status"], "no_provider_data")
        self.assertFalse(
            result["coverage"]["provider_returned_no_rows"]
        )

    def test_verified_stage_is_a_subset_of_full_enrichment(self) -> None:
        # Stage 1 must only ever contain verified OSM results, and every
        # trail id it returns must still exist in the full result, so a
        # client can render stage 1 and then replace it with stage 2.
        async def fake_relations(bbox, *, limit=100):
            return []

        async def fake_ways(bbox, *, limit=2000):
            return [
                postpass.PostpassWay(
                    way_id=4242,
                    name="Test Ridge Trek",
                    route=None,
                    highway="path",
                    sac_scale="hiking",
                    trail_visibility=None,
                    surface=None,
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
                    length_km=2.0,
                    geometry={
                        "type": "LineString",
                        "coordinates": [[76.9, 10.0], [76.92, 10.02]],
                    },
                )
            ]

        async def fake_semantic(*, place, latitude, longitude):
            return trail_discovery.TrailDiscoveryResult(
                place=place,
                trails=[
                    trail_discovery.DiscoveredTrail(
                        name="Unresolved Local Trek"
                    )
                ],
                agent_available=True,
                provider="test",
                provider_status="ok",
                search_result_count=1,
            )

        async def fake_names(names, *, bbox=None, limit_per_name=5):
            return {name: [] for name in names}

        args = dict(
            latitude=10.0,
            longitude=76.9,
            search_query="Test Place",
            location_name="Test Place",
            scope="local",
            bbox=None,
        )
        with patch.object(
            discovery, "discover_relations_in_bbox", new=fake_relations
        ), patch.object(
            discovery, "discover_named_trail_ways_in_bbox", new=fake_ways
        ), patch.object(
            discovery, "discover_trail_candidates", new=fake_semantic
        ), patch.object(
            discovery, "find_relations_by_names", new=fake_names
        ):
            stage1 = asyncio.run(discovery.discover_trails(**args))
            stage2 = asyncio.run(discovery.enrich_trails(**args))

        # Stage 1 contains only verified geometry and no UNMAPPED entries.
        self.assertTrue(stage1["enrichment_pending"])
        self.assertEqual(stage1["unmapped_count"], 0)
        self.assertGreater(stage1["mapped_count"], 0)
        for trail in stage1["trails"]:
            self.assertEqual(trail["state"], "MAP_READY")
            self.assertIsNotNone(trail["geometry"])

        # Stage 1 trail ids survive into the full result.
        stage1_ids = {t["trail_id"] for t in stage1["trails"]}
        stage2_ids = {t["trail_id"] for t in stage2["trails"]}
        self.assertTrue(stage1_ids.issubset(stage2_ids))
        self.assertFalse(stage2["enrichment_pending"])
        self.assertGreaterEqual(stage2["unmapped_count"], 1)

    def test_gemini_result_survives_tavily_fallback(self) -> None:
        # A degraded search backend (SearXNG down -> Tavily) must not
        # discard a successful Gemini extraction.
        gemini = [trail_discovery.DiscoveredTrail(name="Chokramudi Trail")]
        deterministic = [trail_discovery.DiscoveredTrail(name="Det Only Route")]

        with patch.object(
            trail_discovery, "_run_searxng", new=AsyncMock(return_value=[])
        ), patch.object(
            trail_discovery,
            "_run_tavily",
            new=AsyncMock(
                return_value=[
                    {"url": "https://example.org/a", "title": "a", "content": "trail"}
                ]
            ),
        ), patch.object(
            trail_discovery, "_run_gemini", new=AsyncMock(return_value=gemini)
        ), patch.object(
            trail_discovery,
            "_deterministic_candidates",
            return_value=deterministic,
        ), patch.object(
            trail_discovery, "GEMINI_API_KEY", "test-key"
        ):
            result = asyncio.run(
                trail_discovery._discover_uncached("munnar")
            )

        names = [trail.name for trail in result.trails]
        self.assertIn("Chokramudi Trail", names)
        self.assertIn("Det Only Route", names)
        self.assertEqual(result.provider_status, "degraded")
        self.assertEqual(result.provider, "searxng+tavily+gemini")

    def test_deterministic_fallback_only_when_gemini_fails(self) -> None:
        deterministic = [trail_discovery.DiscoveredTrail(name="Det Only Route")]

        with patch.object(
            trail_discovery, "_run_searxng", new=AsyncMock(return_value=[])
        ), patch.object(
            trail_discovery,
            "_run_tavily",
            new=AsyncMock(
                return_value=[
                    {"url": "https://example.org/a", "title": "a", "content": "trail"}
                ]
            ),
        ), patch.object(
            trail_discovery,
            "_run_gemini",
            new=AsyncMock(side_effect=RuntimeError("gemini boom")),
        ), patch.object(
            trail_discovery,
            "_deterministic_candidates",
            return_value=deterministic,
        ), patch.object(
            trail_discovery, "GEMINI_API_KEY", "test-key"
        ):
            result = asyncio.run(
                trail_discovery._discover_uncached("munnar")
            )

        self.assertEqual(
            [trail.name for trail in result.trails],
            ["Det Only Route"],
        )
        self.assertIn("Gemini extraction failed", result.error or "")

    def test_postpass_retry_is_bounded(self) -> None:
        self.assertGreaterEqual(postpass.POSTPASS_RETRIES, 0)
        self.assertLessEqual(postpass.POSTPASS_RETRIES, 2)

    def test_resolution_concurrency_is_bounded(self) -> None:
        self.assertLessEqual(discovery.AGENT_RESOLUTION_CONCURRENCY, 8)
        self.assertLessEqual(discovery.NAME_RESOLUTION_CONCURRENCY, 6)
        self.assertGreaterEqual(discovery.AGENT_RESOLUTION_CONCURRENCY, 1)

    def test_sql_name_variants_are_not_transliterated(self) -> None:
        # Variants become ILIKE patterns matched against raw OSM tags, so
        # they must stay in the stored script instead of being folded to
        # Latin. A non-Latin name must yield no variants so the caller
        # short-circuits instead of issuing a Postpass query that can
        # never match.
        self.assertEqual(
            postpass._name_variants("Chokramudi Peak Trek"),
            ["chokramudi peak trek", "chokramudi"],
        )
        self.assertEqual(
            postpass._name_variants("Café Trail"),
            ["cafe trail", "cafe"],
        )
        self.assertEqual(postpass._name_variants("മീസാപുലിമല"), [])

    def test_transliteration_is_still_used_for_python_side_matching(self) -> None:
        self.assertEqual(postpass._norm("चेम्ब्रा"), "cembraa")
        self.assertGreaterEqual(
            discovery._name_match_score(["चेम्ब्रा"], ["Chembra"]),
            78.0,
        )

    def test_missing_postpass_objects_are_negatively_cached(self) -> None:
        postpass._CACHE.clear()
        postpass._SQL_INFLIGHT.clear()
        with patch.object(
            postpass,
            "_execute_sql",
            new=AsyncMock(return_value=[]),
        ) as execute:
            first = asyncio.run(postpass.get_relation(999999))
            second = asyncio.run(postpass.get_relation(999999))
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(execute.await_count, 1)

    def test_selected_geometry_is_verified_against_postpass(self) -> None:
        geometry = {
            "type": "LineString",
            "coordinates": [[77.0, 10.0], [77.01, 10.0]],
        }
        way = postpass.PostpassWay(
            way_id=42,
            name="Verified Way",
            route="hiking",
            highway="path",
            sac_scale=None,
            trail_visibility=None,
            surface=None,
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
            length_km=1.0,
            geometry=geometry,
        )
        with patch.object(
            trails,
            "get_way",
            new=AsyncMock(return_value=way),
        ):
            verified = asyncio.run(
                trails.verify_selected_trail(
                    {
                        "trail_id": "way:42",
                        "osm_type": "way",
                        "osm_id": 42,
                        "map_ready": True,
                        "geometry": geometry,
                        "geometry_hash": trails._geometry_hash(geometry),
                    }
                )
            )
            self.assertEqual(verified["osm_type"], "way")
            with self.assertRaises(Exception) as context:
                asyncio.run(
                    trails.verify_selected_trail(
                        {
                            "trail_id": "way:42",
                            "osm_type": "way",
                            "osm_id": 42,
                            "map_ready": True,
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[0.0, 0.0], [1.0, 1.0]],
                            },
                            "geometry_hash": "tampered",
                        }
                    )
                )
        self.assertEqual(context.exception.status_code, 409)

    def test_intelligence_remains_useful_when_optional_providers_fail(self) -> None:
        selected = {
            "trail_id": "way:1",
            "osm_type": "way",
            "osm_id": 1,
            "name": "Test Trail",
            "map_ready": True,
            "geometry": {
                "type": "LineString",
                "coordinates": [[77.0, 10.0], [77.01, 10.0]],
            },
            "length_km": 1.0,
            "source_difficulty": "hiking",
            "route_type": "hiking",
            "highway_type": "path",
            "surface": "ground",
        }
        verified_way = postpass.PostpassWay(
            way_id=1,
            name="Test Trail",
            route="hiking",
            highway="path",
            sac_scale="hiking",
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
            point_count=2,
            length_km=1.0,
            geometry=selected["geometry"],
        )
        with patch.object(
            trails,
            "get_way",
            new=AsyncMock(return_value=verified_way),
        ):
            with patch.object(
                trails,
                "get_weather",
                new=AsyncMock(side_effect=RuntimeError("weather down")),
            ):
                with patch.object(
                    trails,
                    "get_elevation_profile",
                    new=AsyncMock(side_effect=RuntimeError("elevation down")),
                ):
                    result = asyncio.run(
                        trails.get_selected_trail_intelligence(
                            trails.TrailIntelligenceRequest(trail=selected)
                        )
                    )
        self.assertEqual(result["providers"]["weather"], "unavailable")
        self.assertEqual(result["providers"]["elevation"], "unavailable")
        self.assertEqual(result["condition"]["likelihood"], "unknown")
        self.assertEqual(
            result["difficulty"]["source"]["class"], "Walkable trail"
        )
        self.assertGreater(len(result["gear"]["items"]), 0)


if __name__ == "__main__":
    unittest.main()
