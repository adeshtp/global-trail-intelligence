"""
When time runs out, the tiles left unsearched should be the far ones.

Tiles were queued in grid order from the south-west corner, so a country-sized
search that hit its time budget skipped the north-east whatever place the user
had actually searched for. Tiles are now searched outward from that place.
"""

from __future__ import annotations

import asyncio
import math
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from app.routes import discovery
from app.services.postpass import BboxRows
from app.services.trail_discovery import TrailDiscoveryResult

Tile = tuple[float, float, float, float]
BROAD = (0.0, 0.0, 6.0, 6.0)


def _centre(tile: Tile) -> tuple[float, float]:
    return ((tile[0] + tile[2]) / 2, (tile[1] + tile[3]) / 2)


def _distance(tile: Tile, lon: float, lat: float) -> float:
    cx, cy = _centre(tile)
    return math.hypot((cx - lon) * math.cos(math.radians(lat)), cy - lat)


class _Providers:
    def __init__(self, delay: float = 0.0) -> None:
        self.tiles: list[Tile] = []
        self.delay = delay

    async def relations(self, tile: Tile, *, limit: int) -> BboxRows:
        self.tiles.append(tile)
        if self.delay:
            await asyncio.sleep(self.delay)
        return BboxRows()

    async def ways(self, tile: Tile, *, limit: int) -> BboxRows:
        if self.delay:
            await asyncio.sleep(self.delay)
        return BboxRows()


async def _semantic(*args: object, **kwargs: object):
    return TrailDiscoveryResult(
        place="Test", trails=[], agent_available=False,
        provider="searxng", provider_status="unavailable",
    )


def _run(providers: _Providers, lat: float, lon: float, budget=None):
    stack = ExitStack()
    stack.enter_context(patch.object(discovery, "discover_relations_in_bbox", new=providers.relations))
    stack.enter_context(patch.object(discovery, "discover_named_trail_ways_in_bbox", new=providers.ways))
    stack.enter_context(patch.object(discovery, "discover_trail_candidates", new=_semantic))
    stack.enter_context(patch.object(discovery, "find_relations_by_names", new=AsyncMock(return_value={})))
    if budget is not None:
        stack.enter_context(patch.object(discovery, "DISCOVERY_TIME_BUDGET_SECONDS", budget))
    with stack:
        return asyncio.run(
            discovery._run_discovery(
                latitude=lat, longitude=lon, place="Region", scope="area",
                search_bbox=BROAD, bbox_source="requested_bbox",
                include_semantic=False,
            )
        )


class TileOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        discovery._HARVEST_CACHE.clear()
        discovery._HARVEST_INFLIGHT.clear()

    def test_tiles_are_searched_outward_from_the_place(self) -> None:
        providers = _Providers()
        _run(providers, lat=5.5, lon=5.5)
        distances = [_distance(t, 5.5, 5.5) for t in providers.tiles[:36]]
        self.assertEqual(distances, sorted(distances))
        self.assertEqual(len(providers.tiles), 36)

    def test_a_search_in_the_south_west_starts_there(self) -> None:
        providers = _Providers()
        _run(providers, lat=0.4, lon=0.4)
        first = _centre(providers.tiles[0])
        self.assertLess(first[0], 1.0)
        self.assertLess(first[1], 1.0)

    def test_when_time_runs_out_the_far_tiles_are_the_ones_skipped(self) -> None:
        providers = _Providers(delay=0.03)
        payload = _run(providers, lat=5.5, lon=5.5, budget=0.05)
        coverage = payload["coverage"]
        self.assertGreater(coverage["tiles_skipped"], 0)
        searched = {t for t in providers.tiles if t[2] - t[0] < 1.01}
        self.assertGreater(len(searched), 0)
        all_tiles, _ = discovery._tile_plan(BROAD)
        skipped = [t for t in all_tiles if t not in searched]
        near_searched = max(_distance(t, 5.5, 5.5) for t in searched)
        near_skipped = min(_distance(t, 5.5, 5.5) for t in skipped)
        self.assertLessEqual(near_searched, near_skipped + 1e-9)

    def test_the_same_area_from_another_place_is_a_different_search(self) -> None:
        providers = _Providers()
        _run(providers, lat=5.5, lon=5.5)
        once = len(providers.tiles)
        _run(providers, lat=0.4, lon=0.4)
        self.assertGreater(len(providers.tiles), once)

    def test_paging_the_same_search_still_reads_the_providers_once(self) -> None:
        providers = _Providers()
        _run(providers, lat=5.5, lon=5.5)
        once = len(providers.tiles)
        _run(providers, lat=5.5, lon=5.5)
        self.assertEqual(len(providers.tiles), once)

    def test_a_single_tile_search_does_not_depend_on_where_in_it_you_are(self) -> None:
        providers = _Providers()
        small = (0.0, 0.0, 0.5, 0.5)

        def run_small(lat, lon):
            stack = ExitStack()
            stack.enter_context(patch.object(discovery, "discover_relations_in_bbox", new=providers.relations))
            stack.enter_context(patch.object(discovery, "discover_named_trail_ways_in_bbox", new=providers.ways))
            stack.enter_context(patch.object(discovery, "discover_trail_candidates", new=_semantic))
            stack.enter_context(patch.object(discovery, "find_relations_by_names", new=AsyncMock(return_value={})))
            with stack:
                return asyncio.run(discovery._run_discovery(
                    latitude=lat, longitude=lon, place="Region", scope="area",
                    search_bbox=small, bbox_source="requested_bbox", include_semantic=False))

        run_small(0.1, 0.1)
        once = len(providers.tiles)
        run_small(0.4, 0.4)
        self.assertEqual(len(providers.tiles), once)


if __name__ == "__main__":
    unittest.main()
