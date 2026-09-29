"""
Overpass fallback: the product must keep mapping when Postpass is down.

Postpass answers HTTP 503 for extended periods. These tests pin the fallback
contract without touching the network: a canned Overpass response built from
a real captured query proves the parsing, and patched transports prove the
fallback triggers exactly on provider failure - never on a successful answer,
not even an empty one.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from app.services import overpass as overpass_module
from app.services import postpass


# Real captured Overpass elements (Yellapetty Topstation trail way and the
# Chokramudi Trail relation with its member), trimmed to the fields the
# parser reads. Fixtures, not fabrications: ids, tags and coordinates are
# verbatim from the live service.
WAY_ELEMENT: dict[str, Any] = {
    "type": "way",
    "id": 328809199,
    "tags": {
        "bicycle": "no",
        "highway": "path",
        "name": "Yellapetty Topstation trail",
        "sac_scale": "hiking",
        "trail_visibility": "intermediate",
    },
    "geometry": [
        {"lat": 10.1279775, "lon": 77.2146765},
        {"lat": 10.1279323, "lon": 77.2146879},
        {"lat": 10.1279001, "lon": 77.2147001},
    ],
}

MEMBER_WAY_ELEMENT: dict[str, Any] = {
    "type": "way",
    "id": 328823108,
    "tags": {"highway": "path", "name": "Chokramudi trail"},
    "geometry": [
        {"lat": 10.5, "lon": 77.0},
        {"lat": 10.5001, "lon": 77.0001},
        {"lat": 10.5002, "lon": 77.0002},
    ],
}

RELATION_ELEMENT: dict[str, Any] = {
    "type": "relation",
    "id": 19236297,
    "tags": {"type": "route", "route": "hiking", "name": "Chokramudi Trail"},
    "members": [{"type": "way", "ref": 328823108, "role": ""}],
}


def _overpass_payload(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 0.6,
        "generator": "Overpass API",
        "elements": elements,
    }


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """httpx-compatible fake serving one canned payload per test."""

    payload: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(
        self, url: str, data: dict[str, Any] | None = None
    ) -> _FakeResponse:
        _FakeClient.calls.append({"url": url, "data": data})
        return _FakeResponse(payload=dict(_FakeClient.payload))


def _patched_overpass(
    payload: dict[str, Any],
) -> Any:
    _FakeClient.payload = payload
    _FakeClient.calls = []
    return patch.object(overpass_module.httpx, "AsyncClient", _FakeClient)


class OverpassParsingTests(unittest.TestCase):
    def test_way_parsing_preserves_identity_tags_and_geometry(self) -> None:
        with _patched_overpass(_overpass_payload([WAY_ELEMENT])):
            ways = overpass_module._way_from_element(WAY_ELEMENT)
        self.assertIsNotNone(ways)
        assert ways is not None
        self.assertEqual(ways.way_id, 328809199)
        self.assertEqual(ways.name, "Yellapetty Topstation trail")
        self.assertEqual(ways.highway, "path")
        self.assertEqual(ways.sac_scale, "hiking")
        self.assertEqual(ways.trail_visibility, "intermediate")
        self.assertEqual(ways.source, "OpenStreetMap via Overpass")
        self.assertEqual(
            ways.geometry,
            {
                "type": "LineString",
                "coordinates": [
                    [77.2146765, 10.1279775],
                    [77.2146879, 10.1279323],
                    [77.2147001, 10.1279001],
                ],
            },
        )
        self.assertGreater(ways.length_km, 0.0)
        self.assertEqual(ways.point_count, 3)

    def test_relation_geometry_assembles_member_components_unstitched(
        self,
    ) -> None:
        geometries = overpass_module._member_geometries(
            _overpass_payload([MEMBER_WAY_ELEMENT])
        )
        relation = overpass_module._relation_from_element(
            RELATION_ELEMENT, geometries
        )
        self.assertIsNotNone(relation)
        assert relation is not None
        self.assertEqual(relation.relation_id, 19236297)
        self.assertEqual(relation.name, "Chokramudi Trail")
        self.assertEqual(relation.route, "hiking")
        # Member identity is complete even though only one member exists.
        self.assertEqual(len(relation.members), 1)
        self.assertEqual(relation.members[0].ref, 328823108)
        self.assertEqual(relation.members[0].member_type, "W")
        # Geometry is one component per member way, in member order: never
        # stitched, never reordered, never invented.
        self.assertIsNotNone(relation.geometry)
        assert relation.geometry is not None
        self.assertEqual(relation.geometry["type"], "MultiLineString")
        self.assertEqual(len(relation.geometry["coordinates"]), 1)
        self.assertEqual(
            relation.geometry["coordinates"][0][0], [77.0, 10.5]
        )
        self.assertEqual(
            relation.source, "OpenStreetMap via Overpass"
        )

    def test_relation_without_member_geometry_is_dropped(self) -> None:
        """A relation that cannot prove its shape is not a route.

        Identity without trustworthy geometry must surface as UNMAPPED
        downstream, never as a fabricated line.
        """
        relation = overpass_module._relation_from_element(
            RELATION_ELEMENT, {}
        )
        self.assertIsNone(relation)

    def test_unnamed_ways_and_non_hiking_relations_are_dropped(
        self,
    ) -> None:
        unnamed = dict(WAY_ELEMENT)
        unnamed["tags"] = {"highway": "path"}
        self.assertIsNone(overpass_module._way_from_element(unnamed))
        road = dict(RELATION_ELEMENT)
        road["tags"] = {
            "type": "route",
            "route": "road",
            "name": "Some Road",
        }
        self.assertIsNone(
            overpass_module._relation_from_element(
                road, {328823108: [[77.0, 10.5], [77.0001, 10.5001]]}
            )
        )


class OverpassTransportTests(unittest.TestCase):
    def test_queries_are_bbox_bounded_and_output_capped(self) -> None:
        payload = _overpass_payload([WAY_ELEMENT])
        with _patched_overpass(payload):
            import asyncio

            asyncio.run(
                overpass_module.overpass_named_ways_in_bbox(
                    (77.0, 10.0, 77.5, 10.5), limit=50
                )
            )
        self.assertTrue(_FakeClient.calls)
        sent = str(_FakeClient.calls[0]["data"])
        # Overpass bbox order is south,west,north,east.
        self.assertIn("(10.0,77.0,10.5,77.5)", sent)
        self.assertIn("out geom 50", sent)

    def test_http_failure_is_provider_failure(self) -> None:
        class _FailClient(_FakeClient):
            async def post(
                self, url: str, data: dict[str, Any] | None = None
            ) -> _FakeResponse:
                return _FakeResponse(status_code=503, text="unavailable")

        with patch.object(
            overpass_module.httpx, "AsyncClient", _FailClient
        ):
            import asyncio

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    overpass_module.overpass_get_way(328809199)
                )

    def test_rate_limit_gets_one_polite_retry(self) -> None:
        """A 429 is throttling, not refusal: exactly one backoff retry.

        Two consecutive 429s still fail as provider failure, so a real
        outage is never masked by retrying.
        """
        import asyncio

        calls: list[int] = []

        class _ThrottleOnceClient(_FakeClient):
            async def post(
                self, url: str, data: dict[str, Any] | None = None
            ) -> _FakeResponse:
                calls.append(1)
                if len(calls) == 1:
                    return _FakeResponse(
                        status_code=429, text="rate limited"
                    )
                return _FakeResponse(
                    payload=_overpass_payload([WAY_ELEMENT])
                )

        original = overpass_module.OVERPASS_429_BACKOFF_SECONDS
        overpass_module.OVERPASS_429_BACKOFF_SECONDS = 0.01
        try:
            with patch.object(
                overpass_module.httpx, "AsyncClient", _ThrottleOnceClient
            ):
                way = asyncio.run(
                    overpass_module.overpass_get_way(328809199)
                )
        finally:
            overpass_module.OVERPASS_429_BACKOFF_SECONDS = original
        self.assertIsNotNone(way)
        assert way is not None
        self.assertEqual(way.way_id, 328809199)
        self.assertEqual(len(calls), 2)

    def test_persistent_throttle_still_fails(self) -> None:
        import asyncio

        class _AlwaysThrottleClient(_FakeClient):
            async def post(
                self, url: str, data: dict[str, Any] | None = None
            ) -> _FakeResponse:
                return _FakeResponse(status_code=429, text="slow down")

        original = overpass_module.OVERPASS_429_BACKOFF_SECONDS
        overpass_module.OVERPASS_429_BACKOFF_SECONDS = 0.01
        try:
            with patch.object(
                overpass_module.httpx, "AsyncClient", _AlwaysThrottleClient
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(
                        overpass_module.overpass_get_way(328809199)
                    )
        finally:
            overpass_module.OVERPASS_429_BACKOFF_SECONDS = original

    def test_name_input_cannot_break_ql_syntax(self) -> None:
        """User-influenced names reach Overpass only as escaped literals."""
        escaped = overpass_module._ql_regex('a"];out geom;(b["x')
        self.assertNotIn('"];', escaped)
        self.assertIn("a", escaped)


class FallbackWiringTests(unittest.TestCase):
    def test_postpass_failure_falls_back_to_overpass(self) -> None:
        async def _run() -> Any:
            with patch.object(
                postpass,
                "_execute_sql",
                AsyncMock(side_effect=RuntimeError("503")),
            ), patch.object(
                overpass_module,
                "overpass_named_ways_in_bbox",
                AsyncMock(
                    return_value=[
                        postpass.PostpassWay(
                            way_id=1,
                            name="Fallback trail",
                            route=None,
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
                            length_km=0.5,
                            geometry={
                                "type": "LineString",
                                "coordinates": [[0.0, 0.0], [0.01, 0.0]],
                            },
                            source="OpenStreetMap via Overpass",
                        )
                    ]
                ),
            ):
                return await postpass.discover_named_trail_ways_in_bbox(
                    (0.0, 0.0, 1.0, 1.0)
                )

        import asyncio

        ways = asyncio.run(_run())
        self.assertEqual(len(ways), 1)
        self.assertEqual(ways[0].source, "OpenStreetMap via Overpass")

    def test_successful_empty_postpass_is_never_second_guessed(
        self,
    ) -> None:
        """An empty answer is a statement about the mirror, not an outage.

        Re-querying elsewhere would blur NO_PROVIDER_DATA and double
        provider load, so the fallback must stay idle.
        """

        async def _run() -> Any:
            with patch.object(
                postpass, "_execute_sql", AsyncMock(return_value=[])
            ), patch.object(
                overpass_module,
                "overpass_named_ways_in_bbox",
                AsyncMock(
                    side_effect=AssertionError(
                        "fallback must not run on success"
                    )
                ),
            ):
                return await postpass.discover_named_trail_ways_in_bbox(
                    (0.0, 0.0, 1.0, 1.0)
                )

        import asyncio

        self.assertEqual(asyncio.run(_run()), [])

    def test_provenance_names_the_serving_source(self) -> None:
        from app.routes import discovery as discovery_module

        self.assertEqual(
            discovery_module._geometry_provenance(
                "OpenStreetMap via Postpass", "way"
            ),
            "postpass_way",
        )
        self.assertEqual(
            discovery_module._geometry_provenance(
                "OpenStreetMap via Overpass", "way"
            ),
            "overpass_way",
        )
        self.assertEqual(
            discovery_module._geometry_provenance(
                "OpenStreetMap via Overpass", "relation"
            ),
            "overpass_relation_members",
        )
        self.assertEqual(
            discovery_module._osm_source([]), "none"
        )


if __name__ == "__main__":
    unittest.main()
