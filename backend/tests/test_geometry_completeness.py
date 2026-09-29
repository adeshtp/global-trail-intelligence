"""
A fragmented route must say how fragmented it is.

Live data: 15% of Chamonix hiking relations and 22% of Edinburgh's are still
in several pieces after exact-endpoint stitching. A count of pieces does not
separate a route split by unshared nodes 7 m apart from a route missing 2 km
of trail, or from a network of unrelated local paths such as "Core Paths"
(140 pieces, the largest 5% of the total). Measuring the gaps does. It never
changes a coordinate and never draws a segment across a gap.
"""

from __future__ import annotations

import unittest

from app.routes import trails
from app.services.postpass import measure_geometry_completeness

LON = 6.87
LAT = 45.9
# 0.0001 degrees of latitude is about 11.1 m.
M = 0.0001 / 11.1


def _line(lat_from_m: float, lat_to_m: float) -> list[list[float]]:
    """A north-south line between two offsets, in metres from LAT."""
    steps = 4
    return [
        [LON, LAT + (lat_from_m + (lat_to_m - lat_from_m) * i / steps) * M]
        for i in range(steps + 1)
    ]


class MeasureGapsTests(unittest.TestCase):
    def test_a_single_part_is_connected(self) -> None:
        result = measure_geometry_completeness([_line(0, 1000)])
        self.assertEqual(result.status, "connected")
        self.assertEqual(result.chain_count, 1)
        self.assertEqual(result.total_gap_km, 0.0)
        self.assertIsNone(result.note)

    def test_ends_within_tolerance_count_as_connected(self) -> None:
        # Two ways whose ends were never given a shared node, 10 m apart.
        result = measure_geometry_completeness(
            [_line(0, 1000), _line(1010, 2000)]
        )
        self.assertEqual(result.part_count, 2)
        self.assertEqual(result.chain_count, 1)
        self.assertEqual(result.status, "connected")

    def test_a_real_gap_is_measured(self) -> None:
        result = measure_geometry_completeness(
            [_line(0, 2000), _line(2500, 3000)]
        )
        self.assertEqual(result.chain_count, 2)
        self.assertEqual(result.status, "gaps")
        self.assertAlmostEqual(result.largest_gap_km, 0.5, delta=0.02)
        self.assertAlmostEqual(result.total_gap_km, 0.5, delta=0.02)
        self.assertGreaterEqual(result.main_chain_share, 0.5)
        self.assertIn("gap", result.note.lower())

    def test_total_gap_is_what_is_missing_to_join_everything(self) -> None:
        # A and B are 300 m apart, C is a further 400 m beyond B.
        result = measure_geometry_completeness(
            [_line(0, 3000), _line(3300, 5000), _line(5400, 8000)]
        )
        self.assertEqual(result.chain_count, 3)
        self.assertAlmostEqual(result.total_gap_km, 0.7, delta=0.03)
        self.assertAlmostEqual(result.largest_gap_km, 0.4, delta=0.02)

    def test_a_far_away_piece_is_a_separate_piece(self) -> None:
        result = measure_geometry_completeness(
            [_line(0, 3000), _line(6000, 6500)]
        )
        self.assertEqual(result.status, "separate_pieces")
        self.assertGreater(result.largest_gap_km, 2.0)

    def test_no_dominant_piece_is_a_collection_not_a_route(self) -> None:
        # Four equal pieces 300 m apart: the largest is only a quarter.
        parts = [_line(i * 1300, i * 1300 + 1000) for i in range(4)]
        result = measure_geometry_completeness(parts)
        self.assertEqual(result.chain_count, 4)
        self.assertLess(result.main_chain_share, 0.5)
        self.assertEqual(result.status, "separate_pieces")
        self.assertIn("separate", result.note.lower())


class AnalysisReportsCompletenessTests(unittest.TestCase):
    def _geometry(self, *parts: list[list[float]]) -> dict:
        return {"type": "MultiLineString", "coordinates": list(parts)}

    def test_analysis_carries_completeness_and_keeps_every_coordinate(
        self,
    ) -> None:
        first, second = _line(0, 2000), _line(2500, 3000)
        analysis = trails.normalize_selected_geometry(
            self._geometry(first, second)
        )
        completeness = analysis["completeness"]
        self.assertEqual(completeness["status"], "gaps")
        self.assertAlmostEqual(completeness["largest_gap_km"], 0.5, delta=0.02)
        # Nothing is joined or invented across the gap.
        self.assertEqual(analysis["geometry"]["type"], "MultiLineString")
        self.assertEqual(
            analysis["coordinate_count"], len(first) + len(second)
        )

    def test_a_connected_route_reports_connected(self) -> None:
        analysis = trails.normalize_selected_geometry(
            {"type": "LineString", "coordinates": _line(0, 2000)}
        )
        self.assertEqual(analysis["completeness"]["status"], "connected")


class DiscoveryCardsTests(unittest.TestCase):
    def _cards(self, geometry: dict) -> list[dict]:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from app.routes import discovery
        from app.services import postpass
        from app.services.trail_discovery import TrailDiscoveryResult

        relation = postpass._relation_from_row(
            {
                "relation_id": 1,
                "name": "Tour du Test",
                "route": "hiking",
                "geometry": geometry,
            }
        )
        with patch.object(
            discovery, "find_relations_by_names", new=AsyncMock(return_value={})
        ):
            payload = asyncio.run(
                discovery._assemble_discovery_result(
                    place="Test",
                    scope="area",
                    latitude=LAT,
                    longitude=LON,
                    search_bbox=(LON - 1, LAT - 1, LON + 1, LAT + 1),
                    bbox_source="radius",
                    semantic_result=TrailDiscoveryResult(
                        place="Test",
                        trails=[],
                        agent_available=False,
                        provider="searxng",
                        provider_status="unavailable",
                    ),
                    relation_result=[relation],
                    way_result=[],
                    include_semantic=False,
                    agent_result=None,
                    tile_plan={"tiles_total": 1, "tiles_queried": 1},
                    place_kind="area",
                    page=1,
                    page_size=discovery.MAX_MAP_READY_RESULTS,
                )
            )
        return payload["trails"]

    def test_a_multi_piece_card_reports_its_gaps(self) -> None:
        trails_ = self._cards(
            {
                "type": "MultiLineString",
                "coordinates": [_line(0, 2000), _line(2500, 3000)],
            }
        )
        self.assertEqual(len(trails_), 1)
        self.assertEqual(trails_[0]["geometry_completeness"]["status"], "gaps")

    def test_a_single_line_card_has_nothing_to_report(self) -> None:
        trails_ = self._cards(
            {"type": "LineString", "coordinates": _line(0, 2000)}
        )
        self.assertEqual(len(trails_), 1)
        self.assertNotIn("geometry_completeness", trails_[0])


if __name__ == "__main__":
    unittest.main()
