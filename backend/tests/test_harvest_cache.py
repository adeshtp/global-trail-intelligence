"""
Every page of one search must rank the same set of trails.

A live Switzerland search with identical parameters returned three different
result sets: 13,412 ranked on page 1, 20,292 on page 2 and 23,639 in
enrichment. Each request re-ran discovery from scratch, hit the time budget at
a different point, and ranked what it had; five stage-1 trails were missing
from stage 2, and each request cost 84-113 s. The provider rows and tile plan
are now kept per search, so pages and enrichment read one harvest.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.routes import discovery
from app.services.postpass import BboxRows
from app.services.trail_discovery import TrailDiscoveryResult

Tile = tuple[float, float, float, float]
SMALL = (0.0, 0.0, 0.5, 0.5)
OTHER = (10.0, 10.0, 10.5, 10.5)
BROAD = (0.0, 0.0, 6.0, 6.0)


class _Providers:
    """Fake providers. The first full-size query is capped; later ones are not,
    so an uncached second request would report a different split count."""

    def __init__(self, fail_first: bool = False) -> None:
        self.relation_calls: list[Tile] = []
        self.way_calls: list[Tile] = []
        self.fail_first = fail_first

    async def relations(self, tile: Tile, *, limit: int) -> BboxRows:
        self.relation_calls.append(tile)
        if self.fail_first and len(self.relation_calls) == 1:
            raise RuntimeError("Postpass down")
        rows = BboxRows()
        rows.truncated = len(self.relation_calls) == 1 and (tile[2] - tile[0]) > 0.4
        return rows

    async def ways(self, tile: Tile, *, limit: int) -> BboxRows:
        self.way_calls.append(tile)
        if self.fail_first and len(self.way_calls) == 1:
            raise RuntimeError("Postpass down")
        return BboxRows()

    @property
    def total(self) -> int:
        return len(self.relation_calls) + len(self.way_calls)


async def _semantic(*args: object, **kwargs: object):
    return TrailDiscoveryResult(
        place="Test",
        trails=[],
        agent_available=False,
        provider="searxng",
        provider_status="unavailable",
    )


def _patched(providers: _Providers):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch.object(discovery, "discover_relations_in_bbox", new=providers.relations)
    )
    stack.enter_context(
        patch.object(discovery, "discover_named_trail_ways_in_bbox", new=providers.ways)
    )
    stack.enter_context(
        patch.object(discovery, "discover_trail_candidates", new=_semantic)
    )
    stack.enter_context(
        patch.object(discovery, "find_relations_by_names", new=AsyncMock(return_value={}))
    )
    return stack


def _run(bbox=SMALL, page=1, include_semantic=False, scope="area"):
    return discovery._run_discovery(
        latitude=0.25,
        longitude=0.25,
        place="Test",
        scope=scope,
        search_bbox=bbox,
        bbox_source="requested_bbox",
        include_semantic=include_semantic,
        page=page,
    )


class HarvestCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        discovery._HARVEST_CACHE.clear()
        discovery._HARVEST_INFLIGHT.clear()

    def test_a_second_page_makes_no_provider_calls(self) -> None:
        providers = _Providers()
        with _patched(providers):
            asyncio.run(_run(page=1))
            after_first = providers.total
            asyncio.run(_run(page=2))
        self.assertGreater(after_first, 0)
        self.assertEqual(providers.total, after_first)

    def test_every_page_reports_the_same_coverage(self) -> None:
        providers = _Providers()
        with _patched(providers):
            first = asyncio.run(_run(page=1))["coverage"]
            second = asyncio.run(_run(page=2))["coverage"]
        self.assertEqual(first["tiles_split"], 1)
        for key in ("tiles_split", "rows_truncated", "candidates_found", "tiles_total"):
            self.assertEqual(first[key], second[key], key)

    def test_stage_two_reads_the_harvest_stage_one_ranked(self) -> None:
        providers = _Providers()
        with _patched(providers):
            stage1 = asyncio.run(_run(include_semantic=False))
            calls_after_stage1 = providers.total
            stage2 = asyncio.run(_run(include_semantic=True))
        self.assertEqual(providers.total, calls_after_stage1)
        self.assertEqual(
            stage1["coverage"]["candidates_found"],
            stage2["coverage"]["candidates_found"],
        )
        self.assertTrue(
            {t["trail_id"] for t in stage1["trails"]}
            <= {t["trail_id"] for t in stage2["trails"]}
        )

    def test_a_different_area_is_a_different_search(self) -> None:
        providers = _Providers()
        with _patched(providers):
            asyncio.run(_run(bbox=SMALL))
            once = providers.total
            asyncio.run(_run(bbox=OTHER))
        self.assertGreater(providers.total, once)

    def test_a_different_scope_is_a_different_search(self) -> None:
        providers = _Providers()
        with _patched(providers):
            asyncio.run(_run(scope="area"))
            once = providers.total
            asyncio.run(_run(scope="local"))
        self.assertGreater(providers.total, once)

    def test_a_failed_harvest_is_not_remembered(self) -> None:
        providers = _Providers(fail_first=True)
        with _patched(providers):
            first = asyncio.run(_run())
            calls_after_first = providers.total
            second = asyncio.run(_run())
        self.assertTrue(first["coverage"]["provider_failed"])
        self.assertGreater(providers.total, calls_after_first)
        self.assertFalse(second["coverage"]["provider_failed"])

    def test_an_expired_harvest_is_read_again(self) -> None:
        providers = _Providers()
        with _patched(providers), patch.object(
            discovery, "HARVEST_CACHE_TTL_SECONDS", 0.0
        ):
            asyncio.run(_run())
            once = providers.total
            asyncio.run(_run())
        self.assertGreater(providers.total, once)

    def test_identical_requests_at_the_same_time_share_one_harvest(self) -> None:
        providers = _Providers()

        async def both():
            return await asyncio.gather(_run(page=1), _run(include_semantic=True))

        with _patched(providers):
            asyncio.run(both())
            together = providers.total
        providers2 = _Providers()
        discovery._HARVEST_CACHE.clear()
        with _patched(providers2):
            asyncio.run(_run())
        self.assertEqual(together, providers2.total)

    def test_the_cache_is_bounded(self) -> None:
        providers = _Providers()
        with _patched(providers):
            for index in range(discovery.HARVEST_CACHE_MAX_ENTRIES + 3):
                base = float(index * 3)
                asyncio.run(_run(bbox=(base, 0.0, base + 0.5, 0.5)))
        self.assertLessEqual(
            len(discovery._HARVEST_CACHE), discovery.HARVEST_CACHE_MAX_ENTRIES
        )

    def test_a_substituted_provider_never_reads_anothers_rows(self) -> None:
        first = _Providers()
        with _patched(first):
            asyncio.run(_run())
        second = _Providers()
        with _patched(second):
            asyncio.run(_run())
        self.assertGreater(second.total, 0)


class BroadAreaPagingTests(unittest.TestCase):
    """The reported case: a region that fills the grid, paged twice."""

    def setUp(self) -> None:
        discovery._HARVEST_CACHE.clear()
        discovery._HARVEST_INFLIGHT.clear()

    def test_paging_a_broad_area_does_not_search_it_again(self) -> None:
        providers = _Providers()
        with _patched(providers):
            asyncio.run(_run(bbox=BROAD))
            once = providers.total
            for page in (2, 3):
                asyncio.run(_run(bbox=BROAD, page=page))
        self.assertGreaterEqual(once, 36)
        self.assertEqual(providers.total, once)


class NearestDistanceTests(unittest.TestCase):
    """
    Distance from the search point ranks every candidate, so it is computed on
    a cheap planar comparison and measured exactly once. It must agree with a
    haversine over every point, including across the antimeridian.
    """

    @staticmethod
    def _brute(geometry, lat, lon):
        points = discovery._geometry_points(geometry)
        return min(
            discovery._haversine_km(lat, lon, p[1], p[0]) for p in points
        )

    def test_it_agrees_with_a_haversine_over_every_point(self) -> None:
        import random

        random.seed(3)
        for _ in range(40):
            lat = random.uniform(-70, 70)
            lon = random.uniform(-170, 170)
            line = [
                [lon + random.uniform(-2, 2), lat + random.uniform(-2, 2)]
                for _ in range(60)
            ]
            geometry = {"type": "LineString", "coordinates": line}
            self.assertAlmostEqual(
                discovery._distance_from_search(geometry, lat, lon),
                self._brute(geometry, lat, lon),
                delta=0.002,  # 2 m
            )

    def test_it_finds_the_nearer_side_across_the_antimeridian(self) -> None:
        geometry = {
            "type": "LineString",
            "coordinates": [[170.0, 10.0], [-179.9, 10.0]],
        }
        distance = discovery._distance_from_search(geometry, 10.0, 179.9)
        self.assertAlmostEqual(distance, self._brute(geometry, 10.0, 179.9), delta=0.002)
        self.assertLess(distance, 30.0)  # 0.2 degrees away, not 9.9

    def test_a_geometry_without_points_has_no_distance(self) -> None:
        self.assertIsNone(discovery._distance_from_search(None, 0.0, 0.0))
        self.assertIsNone(
            discovery._distance_from_search(
                {"type": "LineString", "coordinates": []}, 0.0, 0.0
            )
        )


if __name__ == "__main__":
    unittest.main()
