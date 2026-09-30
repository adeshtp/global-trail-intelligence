"""
A route's member ways must be fetched in bulk, not one round trip each.

Selecting the Tour du Mont Blanc (740 member ways) timed out after 300 s:
each member was re-verified with its own Postpass query, one after another.
"""

from __future__ import annotations

import asyncio
import re
import unittest
from unittest.mock import AsyncMock, patch

from app.routes import trails
from app.services import postpass


def _row(way_id: int) -> dict:
    return {
        "way_id": way_id,
        "name": f"Way {way_id}",
        "highway": "path",
        "sac_scale": "hiking",
        "length_km": 0.5,
        "geometry_type": "LineString",
        "point_count": 2,
        "geometry": {
            "type": "LineString",
            "coordinates": [[6.0, 45.0], [6.01, 45.01]],
        },
    }


class _Provider:
    """Answers bulk way queries; ids above 9000 do not exist."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def __call__(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        ids = [int(n) for n in re.findall(r"\b\d+\b", sql.split("IN (")[1].split(")")[0])]
        # Rows come back in no particular order, as SQL gives them.
        return [_row(i) for i in sorted(ids, reverse=True) if i <= 9000]


class GetWaysTests(unittest.TestCase):
    def setUp(self) -> None:
        postpass._CACHE.clear()

    def test_many_ways_cost_a_few_queries_not_one_each(self) -> None:
        provider = _Provider()
        with patch.object(postpass, "_execute_sql", new=provider):
            ways = asyncio.run(postpass.get_ways(list(range(1, 401))))
        self.assertEqual(len(ways), 400)
        self.assertEqual(len(provider.queries), 2)
        self.assertEqual(ways[7].name, "Way 7")

    def test_missing_ways_are_absent_not_errors(self) -> None:
        provider = _Provider()
        with patch.object(postpass, "_execute_sql", new=provider):
            ways = asyncio.run(postpass.get_ways([5, 9001, 6]))
        self.assertEqual(sorted(ways), [5, 6])

    def test_ways_already_seen_are_not_fetched_again(self) -> None:
        provider = _Provider()
        with patch.object(postpass, "_execute_sql", new=provider):
            asyncio.run(postpass.get_ways([1, 2, 3]))
            asyncio.run(postpass.get_ways([1, 2, 3, 4]))
        self.assertEqual(len(provider.queries), 2)
        self.assertIn("4", provider.queries[1].split("IN (")[1].split(")")[0])
        self.assertNotIn("1,", provider.queries[1].split("IN (")[1].split(")")[0])

    def test_duplicates_and_bad_ids_are_ignored(self) -> None:
        provider = _Provider()
        with patch.object(postpass, "_execute_sql", new=provider):
            ways = asyncio.run(postpass.get_ways([3, 3, "x", -1, None, 4]))
        self.assertEqual(sorted(ways), [3, 4])

    def test_a_failed_chunk_falls_back_to_single_lookups(self) -> None:
        calls: list[int] = []

        async def failing(sql: str) -> list[dict]:
            raise RuntimeError("Postpass down")

        async def single(way_id: int):
            calls.append(way_id)
            return None

        with patch.object(postpass, "_execute_sql", new=failing), patch.object(
            postpass.overpass_fallback, "overpass_get_way", new=single
        ):
            ways = asyncio.run(postpass.get_ways([1, 2]))
        self.assertEqual(ways, {})
        self.assertEqual(sorted(calls), [1, 2])


class MemberTrailsTests(unittest.TestCase):
    def test_members_come_back_in_route_order_and_skip_missing(self) -> None:
        async def get_ways(ids):
            return {i: postpass._way_from_row(_row(i)) for i in ids if i <= 9000}

        with patch.object(trails, "get_ways", new=AsyncMock(side_effect=get_ways)) as bulk:
            members = asyncio.run(
                trails._verified_member_trails(
                    {"member_way_ids": [30, 9001, 10, 20]}
                )
            )
        self.assertEqual([m["osm_id"] for m in members], [30, 10, 20])
        bulk.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
