"""
Row caps must never silently hide trails.

A live Chamonix search found 639 named hiking relations in OpenStreetMap but
could see only 376 of them: each tile's query returned at most ``LIMIT`` rows,
ordered by name, and nothing said rows had been cut. These tests pin the
fix: a capped query says so, a capped tile is re-queried as quadrants inside
the existing tile budget, and whatever still cannot be resolved is reported.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from app.routes import discovery
from app.services import postpass
from app.services.postpass import BboxRows
from app.services.trail_discovery import TrailDiscoveryResult

Tile = tuple[float, float, float, float]


def _relation_row(index: int) -> dict:
    return {
        "relation_id": 1000 + index,
        "name": f"Route {index}",
        "route": "hiking",
        "geometry": {
            "type": "LineString",
            "coordinates": [[76.0, 10.0], [76.01, 10.01]],
        },
    }


class PostpassTruncationTests(unittest.TestCase):
    def setUp(self) -> None:
        postpass._CACHE.clear()

    def _relations(self, rows: int, limit: int) -> BboxRows:
        with patch.object(
            postpass,
            "_execute_sql",
            AsyncMock(return_value=[_relation_row(i) for i in range(rows)]),
        ):
            return asyncio.run(
                postpass.discover_relations_in_bbox(
                    (76.0, 10.0, 76.5, 10.5), limit=limit
                )
            )

    def test_a_query_that_hits_its_limit_is_marked_truncated(self) -> None:
        result = self._relations(rows=5, limit=5)
        self.assertEqual(len(result), 5)
        self.assertTrue(result.truncated)

    def test_a_query_under_its_limit_is_not_truncated(self) -> None:
        result = self._relations(rows=3, limit=5)
        self.assertFalse(result.truncated)


@dataclass(frozen=True)
class _Row:
    relation_id: int = 0
    way_id: int = 0


class _World:
    """A fake provider: points on a plane, capped queries, call log."""

    def __init__(self, relation_points: list[tuple[float, float]]) -> None:
        self.relation_points = relation_points
        self.way_points: list[tuple[float, float]] = []
        self.relation_calls: list[Tile] = []
        self.way_calls: list[Tile] = []

    @staticmethod
    def _inside(point: tuple[float, float], tile: Tile) -> bool:
        west, south, east, north = tile
        return west <= point[0] < east and south <= point[1] < north

    def _query(
        self,
        points: list[tuple[float, float]],
        tile: Tile,
        limit: int,
        make,
    ) -> BboxRows:
        hits = [i for i, p in enumerate(points) if self._inside(p, tile)]
        rows = BboxRows(make(i) for i in hits[:limit])
        rows.truncated = len(hits) >= limit
        return rows

    async def relations(self, tile: Tile, *, limit: int) -> BboxRows:
        self.relation_calls.append(tile)
        return self._query(
            self.relation_points,
            tile,
            limit,
            lambda i: _Row(relation_id=i + 1),
        )

    async def ways(self, tile: Tile, *, limit: int) -> BboxRows:
        self.way_calls.append(tile)
        return self._query(
            self.way_points, tile, limit, lambda i: _Row(way_id=i + 1)
        )


def _grid(count: int) -> list[tuple[float, float]]:
    side = int(count**0.5) + 1
    return [
        (0.01 + 0.98 * (i % side) / side, 0.01 + 0.98 * (i // side) / side)
        for i in range(count)
    ]


class ExpandTruncatedTilesTests(unittest.TestCase):
    TILE: Tile = (0.0, 0.0, 1.0, 1.0)

    def _expand(
        self,
        world: _World,
        *,
        budget: int,
        relation_limit: int = 100,
        way_limit: int = 100,
    ) -> discovery._TileHarvest:
        first_relations = asyncio.run(
            world.relations(self.TILE, limit=relation_limit)
        )
        first_ways = asyncio.run(world.ways(self.TILE, limit=way_limit))
        world.relation_calls.clear()
        world.way_calls.clear()
        with patch.object(
            discovery, "discover_relations_in_bbox", new=world.relations
        ), patch.object(
            discovery, "discover_named_trail_ways_in_bbox", new=world.ways
        ):
            return asyncio.run(
                discovery._expand_truncated(
                    self.TILE,
                    relation_rows=first_relations,
                    way_rows=first_ways,
                    relation_limit=relation_limit,
                    way_limit=way_limit,
                    budget=discovery._QueryBudget(budget),
                    semaphore=asyncio.Semaphore(3),
                )
            )

    def test_a_capped_tile_is_requeried_until_nothing_is_cut(self) -> None:
        world = _World(_grid(300))
        harvest = self._expand(world, budget=30)
        ids = {row.relation_id for row in harvest.relations}
        self.assertEqual(harvest.relations_truncated, 0)
        # The first pass already returned 100; the expansion must supply the
        # rest, so together they cover all 300.
        first = {
            row.relation_id
            for row in asyncio.run(world.relations(self.TILE, limit=100))
        }
        self.assertEqual(len(ids | first), 300)

    def test_only_the_capped_kind_is_requeried(self) -> None:
        world = _World(_grid(300))
        world.way_points = _grid(10)
        self._expand(world, budget=30)
        self.assertGreater(len(world.relation_calls), 0)
        self.assertEqual(world.way_calls, [])

    def test_the_tile_budget_is_never_exceeded(self) -> None:
        # Every point sits on one spot, so no split can ever separate them.
        world = _World([(0.5, 0.5)] * 500)
        harvest = self._expand(world, budget=9)
        self.assertLessEqual(len(world.relation_calls), 9)
        self.assertGreaterEqual(harvest.relations_truncated, 1)

    def test_no_budget_means_no_requeries_and_a_reported_cut(self) -> None:
        world = _World(_grid(300))
        harvest = self._expand(world, budget=3)
        self.assertEqual(world.relation_calls, [])
        self.assertEqual(harvest.relations_truncated, 1)

    def test_an_uncapped_tile_costs_no_extra_queries(self) -> None:
        world = _World(_grid(20))
        harvest = self._expand(world, budget=30)
        self.assertEqual(world.relation_calls, [])
        self.assertEqual(harvest.relations_truncated, 0)


class CoverageReportsTruncationTests(unittest.TestCase):
    def test_an_unresolvable_cap_is_reported_and_bounded(self) -> None:
        calls: list[Tile] = []

        async def capped(tile: Tile, *, limit: int) -> BboxRows:
            calls.append(tile)
            rows = BboxRows()
            rows.truncated = True
            return rows

        async def no_ways(tile: Tile, *, limit: int) -> BboxRows:
            return BboxRows()

        async def semantic(*args: object, **kwargs: object):
            return TrailDiscoveryResult(
                place="Test Place",
                trails=[],
                agent_available=False,
                provider="searxng",
                provider_status="unavailable",
            )

        with patch.object(
            discovery, "discover_relations_in_bbox", new=capped
        ), patch.object(
            discovery, "discover_named_trail_ways_in_bbox", new=no_ways
        ), patch.object(
            discovery, "discover_trail_candidates", new=semantic
        ), patch.object(
            discovery, "find_relations_by_names", new=AsyncMock(return_value={})
        ):
            payload = asyncio.run(
                discovery.discover_trails(
                    latitude=10.0,
                    longitude=76.9,
                    search_query="Test Place",
                    location_name="Test Place",
                    scope="local",
                    bbox=None,
                )
            )

        coverage = payload["coverage"]
        self.assertGreaterEqual(coverage["rows_truncated"]["relations"], 1)
        self.assertEqual(coverage["rows_truncated"]["ways"], 0)
        # One query for the tile, then at most the split budget.
        self.assertLessEqual(len(calls), 1 + discovery.MAX_SPLIT_QUERIES)

    def _coverage(self, tile_plan: dict) -> dict:
        relation = postpass._relation_from_row(_relation_row(1))
        with patch.object(
            discovery, "find_relations_by_names", new=AsyncMock(return_value={})
        ):
            return asyncio.run(
                discovery._assemble_discovery_result(
                    place="Munnar",
                    scope="area",
                    latitude=0.0,
                    longitude=0.0,
                    search_bbox=(-0.5, -0.5, 0.5, 0.5),
                    bbox_source="radius",
                    semantic_result=TrailDiscoveryResult(
                        place="Munnar",
                        trails=[],
                        agent_available=False,
                        provider="searxng",
                        provider_status="unavailable",
                    ),
                    relation_result=[relation],
                    way_result=[],
                    include_semantic=False,
                    agent_result=None,
                    tile_plan=tile_plan,
                    place_kind="area",
                    page=1,
                    page_size=discovery.MAX_MAP_READY_RESULTS,
                )
            )["coverage"]

    def test_a_cut_makes_coverage_incomplete(self) -> None:
        complete = self._coverage({"tiles_total": 1, "tiles_queried": 1})
        self.assertTrue(complete["coverage_complete"])
        self.assertEqual(
            complete["rows_truncated"], {"relations": 0, "ways": 0}
        )

        cut = self._coverage(
            {
                "tiles_total": 1,
                "tiles_queried": 1,
                "rows_truncated": {"relations": 2, "ways": 0},
                "tiles_split": 3,
            }
        )
        self.assertFalse(cut["coverage_complete"])
        self.assertEqual(cut["rows_truncated"]["relations"], 2)
        self.assertEqual(cut["tiles_split"], 3)


class DeadlineWhileQueuedTests(unittest.TestCase):
    """
    The deadline was checked before splitting but not when a queued query
    finally got a slot. With splits plentiful, ~200 quadrant queries were
    queued early and ran long after the 60 s budget: a live Switzerland search
    took 233 s.
    """

    def test_queued_quadrants_do_not_run_after_the_deadline(self) -> None:
        import time

        calls: list[Tile] = []

        async def relations(tile: Tile, *, limit: int) -> BboxRows:
            calls.append(tile)
            return BboxRows()

        async def scenario() -> discovery._TileHarvest:
            semaphore = asyncio.Semaphore(1)
            await semaphore.acquire()  # every slot is busy
            truncated = BboxRows()
            truncated.truncated = True
            task = asyncio.create_task(
                discovery._expand_truncated(
                    (0.0, 0.0, 1.0, 1.0),
                    relation_rows=truncated,
                    way_rows=None,
                    relation_limit=100,
                    way_limit=100,
                    budget=discovery._QueryBudget(50),
                    semaphore=semaphore,
                    deadline=time.monotonic() + 0.1,
                )
            )
            await asyncio.sleep(0.4)  # the deadline passes while they wait
            semaphore.release()
            return await task

        with patch.object(
            discovery, "discover_relations_in_bbox", new=relations
        ):
            harvest = asyncio.run(scenario())

        self.assertEqual(calls, [])
        self.assertGreaterEqual(harvest.relations_truncated, 1)

    def test_before_the_deadline_queued_quadrants_still_run(self) -> None:
        import time

        calls: list[Tile] = []

        async def relations(tile: Tile, *, limit: int) -> BboxRows:
            calls.append(tile)
            return BboxRows()

        async def scenario() -> discovery._TileHarvest:
            truncated = BboxRows()
            truncated.truncated = True
            return await discovery._expand_truncated(
                (0.0, 0.0, 1.0, 1.0),
                relation_rows=truncated,
                way_rows=None,
                relation_limit=100,
                way_limit=100,
                budget=discovery._QueryBudget(50),
                semaphore=asyncio.Semaphore(1),
                deadline=time.monotonic() + 60,
            )

        with patch.object(
            discovery, "discover_relations_in_bbox", new=relations
        ):
            harvest = asyncio.run(scenario())
        self.assertEqual(len(calls), 4)
        self.assertEqual(harvest.relations_truncated, 0)


class BroadAreaSplitBudgetTests(unittest.TestCase):
    """
    A region wide enough to fill the tile grid used to leave splits with
    whatever MAX_TILES had left (4 queries, one split), so a live Switzerland
    search stopped splitting after 4 splits and left 59 areas cut. Splits have
    their own budget; the time budget bounds them.
    """

    CAPPED = {(0.0, 0.0), (2.0, 3.0), (4.0, 5.0)}

    def _run(self):
        calls: list[Tile] = []

        async def relations(tile: Tile, *, limit: int) -> BboxRows:
            calls.append(tile)
            rows = BboxRows()
            span = tile[2] - tile[0]
            # Only three full-size tiles are dense; their quadrants are not.
            rows.truncated = span > 0.99 and (tile[0], tile[1]) in self.CAPPED
            return rows

        async def no_ways(tile: Tile, *, limit: int) -> BboxRows:
            return BboxRows()

        async def semantic(*args: object, **kwargs: object):
            return TrailDiscoveryResult(
                place="Region",
                trails=[],
                agent_available=False,
                provider="searxng",
                provider_status="unavailable",
            )

        async def run():
            return await discovery._run_discovery(
                latitude=3.0,
                longitude=3.0,
                place="Region",
                scope="area",
                search_bbox=(0.0, 0.0, 6.0, 6.0),
                bbox_source="requested_bbox",
                include_semantic=False,
            )

        with patch.object(
            discovery, "discover_relations_in_bbox", new=relations
        ), patch.object(
            discovery, "discover_named_trail_ways_in_bbox", new=no_ways
        ), patch.object(
            discovery, "discover_trail_candidates", new=semantic
        ), patch.object(
            discovery, "find_relations_by_names", new=AsyncMock(return_value={})
        ):
            payload = asyncio.run(run())
        return payload["coverage"], calls

    def test_the_grid_fills_the_tile_limit(self) -> None:
        tiles, _ = discovery._tile_plan((0.0, 0.0, 6.0, 6.0))
        self.assertGreaterEqual(len(tiles), discovery.MAX_TILES - 4)

    def test_every_dense_tile_of_a_broad_area_is_split(self) -> None:
        coverage, calls = self._run()
        self.assertEqual(coverage["tiles_split"], len(self.CAPPED))
        self.assertEqual(coverage["rows_truncated"]["relations"], 0)
        quadrant_queries = [t for t in calls if t[2] - t[0] < 0.6]
        self.assertEqual(len(quadrant_queries), 4 * len(self.CAPPED))

    def test_splits_have_a_bound_of_their_own(self) -> None:
        self.assertGreater(discovery.MAX_SPLIT_QUERIES, 4)


if __name__ == "__main__":
    unittest.main()
