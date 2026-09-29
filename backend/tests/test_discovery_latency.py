"""
Discovery must not wait longer than it has to, or forever.

A live Chamonix search took 30 s once capped tiles started splitting, and 115 s
on a cold provider. Tiles were awaited in batches of six, so every batch waited
for its slowest tile; nothing bounded a whole search; and the semantic Gemini
call had no timeout at all.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routes import discovery
from app.services import trail_discovery
from app.services.postpass import BboxRows
from app.services.trail_discovery import TrailDiscoveryResult

# Eight tiles: a 4 x 2 grid at the default 0.75 degree tile size.
EIGHT_TILES = (0.0, 0.0, 3.0, 1.5)


def _semantic(*args: object, **kwargs: object):
    return TrailDiscoveryResult(
        place="Test",
        trails=[],
        agent_available=False,
        provider="searxng",
        provider_status="unavailable",
    )


def _run(bbox=EIGHT_TILES, **providers):
    async def semantic(*args: object, **kwargs: object):
        return _semantic()

    async def run():
        return await discovery._run_discovery(
            latitude=0.75,
            longitude=1.5,
            place="Test",
            scope="area",
            search_bbox=bbox,
            bbox_source="requested_bbox",
            include_semantic=False,
        )

    with patch.object(
        discovery,
        "discover_relations_in_bbox",
        new=providers["relations"],
    ), patch.object(
        discovery,
        "discover_named_trail_ways_in_bbox",
        new=providers["ways"],
    ), patch.object(
        discovery, "discover_trail_candidates", new=semantic
    ), patch.object(
        discovery, "find_relations_by_names", new=AsyncMock(return_value={})
    ):
        return asyncio.run(run())


class TileSchedulingTests(unittest.TestCase):
    def test_one_slow_tile_does_not_hold_back_the_rest(self) -> None:
        started = time.monotonic()
        finished: dict[tuple, float] = {}
        first_tile: list[tuple] = []

        async def relations(tile, *, limit):
            if not first_tile:
                first_tile.append(tile)
            # The first tile is slow, every other one is instant.
            await asyncio.sleep(0.6 if tile == first_tile[0] else 0.0)
            finished[tile] = time.monotonic() - started
            return BboxRows()

        async def ways(tile, *, limit):
            return BboxRows()

        _run(relations=relations, ways=ways)
        slow = finished[first_tile[0]]
        others = [t for k, t in finished.items() if k != first_tile[0]]
        self.assertEqual(len(others), 7)
        # With batches of six, tiles 7 and 8 could not start until the slow
        # tile finished. Now every other tile is done long before it.
        self.assertLess(max(others), slow - 0.3)


class TimeBudgetTests(unittest.TestCase):
    def test_a_spent_budget_skips_tiles_and_says_so(self) -> None:
        async def relations(tile, *, limit):
            await asyncio.sleep(0.15)
            return BboxRows()

        async def ways(tile, *, limit):
            await asyncio.sleep(0.15)
            return BboxRows()

        with patch.object(discovery, "DISCOVERY_TIME_BUDGET_SECONDS", 0.2):
            started = time.monotonic()
            payload = _run(relations=relations, ways=ways)
            elapsed = time.monotonic() - started

        coverage = payload["coverage"]
        # Three tiles at a time, 0.15 s each: only the first wave fits.
        self.assertGreater(coverage["tiles_skipped"], 0)
        self.assertFalse(coverage["coverage_complete"])
        self.assertLess(elapsed, 1.0)

    def test_a_spent_budget_stops_splitting_and_reports_the_cut(self) -> None:
        async def relations(tile, *, limit):
            rows = BboxRows()
            rows.truncated = True
            return rows

        async def ways(tile, *, limit):
            return BboxRows()

        with patch.object(discovery, "DISCOVERY_TIME_BUDGET_SECONDS", 0.0):
            payload = _run(
                bbox=(0.0, 0.0, 0.5, 0.5), relations=relations, ways=ways
            )

        coverage = payload["coverage"]
        self.assertEqual(coverage["tiles_split"], 0)
        self.assertGreaterEqual(coverage["rows_truncated"]["relations"], 1)


class TimingTests(unittest.TestCase):
    def test_response_reports_where_the_time_went(self) -> None:
        async def relations(tile, *, limit):
            return BboxRows()

        async def ways(tile, *, limit):
            return BboxRows()

        payload = _run(
            bbox=(0.0, 0.0, 0.5, 0.5), relations=relations, ways=ways
        )
        timings = payload["diagnostics"]["timings_ms"]
        self.assertGreaterEqual(timings["providers"], 0)
        self.assertGreaterEqual(timings["assemble"], 0)


class GeminiTimeoutTests(unittest.TestCase):
    def test_a_hung_gemini_call_times_out(self) -> None:
        class _Models:
            async def generate_content(self, **kwargs):
                await asyncio.sleep(30)

        fake_client = SimpleNamespace(
            aio=SimpleNamespace(models=_Models()), close=lambda: None
        )
        results = [{"url": "https://example.org/a", "title": "t", "content": "c"}]
        with patch.object(
            trail_discovery, "GEMINI_API_KEY", "test-key"
        ), patch.object(
            trail_discovery, "GEMINI_TIMEOUT_SECONDS", 0.1
        ), patch(
            "google.genai.Client", return_value=fake_client
        ):
            started = time.monotonic()
            with self.assertRaises(asyncio.TimeoutError):
                asyncio.run(trail_discovery._run_gemini("Test", results))
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
