"""
Sorting and filtering the trail list.

Discovery pages a ranked set of up to thousands of trails, so a sort or filter
applied to the loaded page would be wrong whenever more pages exist ("nearest"
would mean "nearest of the ones loaded"). It is applied to the whole ranked set
before the page is cut, so page 2 continues the same order and every count
follows the filter.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.routes import discovery
from test_harvest_cache import SMALL, _patched, _Providers, _run


def _trail(
    name: str,
    *,
    distance: float | None = 1.0,
    length: float | None = 5.0,
    grade: str | None = None,
    grades: list[str] | None = None,
    mapped: bool = True,
) -> dict:
    return {
        "trail_id": name,
        "name": name,
        "map_ready": mapped,
        "distance_from_search_km": distance,
        "length_km": length,
        "source_difficulty": grade,
        "source_difficulty_values": grades or ([grade] if grade else []),
        "priority_tier": 1,
        "relevance_score": 50.0,
    }


TRAILS = [
    _trail("a-near-long-hard", distance=0.5, length=20.0, grade="demanding_mountain_hiking"),
    _trail("b-far-short-easy", distance=9.0, length=1.5, grade="hiking"),
    _trail("c-mid-mid-moderate", distance=3.0, length=8.0, grade="mountain_hiking"),
    _trail("d-mid-mid-nograde", distance=4.0, length=8.0),
    _trail("e-far-long-alpine", distance=12.0, length=30.0, grade="alpine_hiking"),
    _trail("f-unmapped", mapped=False, length=None, distance=None),
]


def _view(**kwargs) -> discovery.ResultView:
    grades = kwargs.pop("grades", ())
    return discovery.ResultView(grades=frozenset(grades), **kwargs)


def _names(items: list[dict]) -> list[str]:
    return [item["name"] for item in items]


def _apply(view: discovery.ResultView):
    # As in discovery: the mapped set is already ranked when the view arrives.
    mapped = sorted(
        (t for t in TRAILS if t["map_ready"]),
        key=discovery._candidate_sort_key,
    )
    unmapped = [t for t in TRAILS if not t["map_ready"]]
    return discovery._apply_view(mapped, unmapped, view)


class SortTests(unittest.TestCase):
    def test_nearest_first(self) -> None:
        mapped, _ = _apply(_view(sort=discovery.SortOrder.nearest))
        self.assertEqual(
            _names(mapped)[:3],
            ["a-near-long-hard", "c-mid-mid-moderate", "d-mid-mid-nograde"],
        )

    def test_longest_and_shortest(self) -> None:
        longest, _ = _apply(_view(sort=discovery.SortOrder.longest))
        shortest, _ = _apply(_view(sort=discovery.SortOrder.shortest))
        self.assertEqual(_names(longest)[0], "e-far-long-alpine")
        self.assertEqual(_names(shortest)[0], "b-far-short-easy")

    def test_easiest_puts_ungraded_trails_last(self) -> None:
        mapped, _ = _apply(_view(sort=discovery.SortOrder.easiest))
        self.assertEqual(
            _names(mapped),
            [
                "b-far-short-easy",
                "c-mid-mid-moderate",
                "a-near-long-hard",
                "e-far-long-alpine",
                "d-mid-mid-nograde",
            ],
        )

    def test_best_match_is_the_existing_ranking(self) -> None:
        mapped, _ = _apply(_view())
        expected = sorted(
            [t for t in TRAILS if t["map_ready"]],
            key=discovery._candidate_sort_key,
        )
        self.assertEqual(_names(mapped), _names(expected))

    def test_a_tie_keeps_a_stable_order_across_pages(self) -> None:
        twins = [_trail(f"t{i}", length=5.0) for i in range(6)]
        first, _ = discovery._apply_view(
            list(twins), [], _view(sort=discovery.SortOrder.longest)
        )
        again, _ = discovery._apply_view(
            list(reversed(twins)), [], _view(sort=discovery.SortOrder.longest)
        )
        self.assertEqual(_names(first), _names(again))


class FilterTests(unittest.TestCase):
    def test_grade_filter_uses_the_hardest_grade(self) -> None:
        mixed = _trail("mixed", grade=None, grades=["hiking", "demanding_mountain_hiking"])
        mapped, _ = discovery._apply_view(
            [mixed, *[t for t in TRAILS if t["map_ready"]]],
            [],
            _view(grades=("hiking", "strolling")),
        )
        self.assertEqual(_names(mapped), ["b-far-short-easy"])

    def test_grade_filter_drops_ungraded_trails(self) -> None:
        mapped, _ = _apply(_view(grades=("mountain_hiking",)))
        self.assertEqual(_names(mapped), ["c-mid-mid-moderate"])

    def test_length_range(self) -> None:
        mapped, _ = _apply(_view(min_length_km=5.0, max_length_km=15.0))
        self.assertEqual(
            sorted(_names(mapped)), ["c-mid-mid-moderate", "d-mid-mid-nograde"]
        )

    def test_unmapped_trails_are_hidden_when_a_filter_needs_their_data(self) -> None:
        _, unmapped = _apply(_view(min_length_km=1.0))
        self.assertEqual(unmapped, [])
        _, unmapped = _apply(_view(sort=discovery.SortOrder.nearest))
        self.assertEqual(_names(unmapped), ["f-unmapped"])

    def test_no_view_changes_nothing(self) -> None:
        mapped, unmapped = _apply(_view())
        self.assertEqual(len(mapped), 5)
        self.assertEqual(len(unmapped), 1)


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def _get(self, extra: str):
        return self.client.get(
            "/api/osm/trails/discover?latitude=1&longitude=1&search_query=x&" + extra
        )

    def test_an_unknown_sort_is_rejected(self) -> None:
        self.assertEqual(self._get("sort=sideways").status_code, 422)

    def test_an_unknown_grade_is_rejected(self) -> None:
        self.assertEqual(self._get("grade=easy").status_code, 422)

    def test_a_negative_length_is_rejected(self) -> None:
        self.assertEqual(self._get("min_length_km=-1").status_code, 422)

    def test_both_endpoints_take_the_view(self) -> None:
        seen: list[discovery.ResultView] = []

        async def fake(**kwargs):
            seen.append(kwargs["view"])
            return {"trails": []}

        with patch.object(discovery, "_run_discovery", new=fake):
            self.client.get(
                "/api/osm/trails/discover?latitude=1&longitude=1&search_query=x"
                "&sort=nearest&grade=hiking&grade=strolling&max_length_km=9"
            )
            self.client.get(
                "/api/osm/trails/enrichment?latitude=1&longitude=1&search_query=x"
                "&sort=longest"
            )
        self.assertEqual(seen[0].sort, discovery.SortOrder.nearest)
        self.assertEqual(seen[0].grades, frozenset({"hiking", "strolling"}))
        self.assertEqual(seen[0].max_length_km, 9.0)
        self.assertEqual(seen[1].sort, discovery.SortOrder.longest)

    def test_the_grade_list_matches_the_model_contract(self) -> None:
        from app.ml.feature_contract import GRADES

        self.assertEqual(set(discovery.GRADE_NAMES), set(GRADES))


class SortMakesNoProviderCallsTests(unittest.TestCase):
    def setUp(self) -> None:
        discovery._HARVEST_CACHE.clear()
        discovery._HARVEST_INFLIGHT.clear()

    def test_changing_the_sort_reads_the_same_harvest(self) -> None:
        providers = _Providers()
        with _patched(providers):
            asyncio.run(_run(bbox=SMALL))
            after_first = providers.total
            asyncio.run(
                discovery._run_discovery(
                    latitude=0.25,
                    longitude=0.25,
                    place="Test",
                    scope="area",
                    search_bbox=SMALL,
                    bbox_source="requested_bbox",
                    include_semantic=False,
                    view=_view(sort=discovery.SortOrder.nearest),
                )
            )
        self.assertEqual(providers.total, after_first)


class ResponseFollowsTheFilterTests(unittest.TestCase):
    def test_counts_and_status_follow_the_filtered_list(self) -> None:
        mapped, unmapped = _apply(_view(grades=("hiking",)))
        summary = discovery._view_summary(
            _view(grades=("hiking",)),
            matched=len(mapped) + len(unmapped),
            before=len(TRAILS),
        )
        self.assertTrue(summary["filtered"])
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["before_filters"], 6)


class StatusTests(unittest.TestCase):
    """A filter that matches nothing is never an outage or an empty area."""

    def _status(self, **kwargs) -> str:
        args = dict(
            provider_failed=False,
            no_provider_data=False,
            has_results=False,
            filtered_to_nothing=False,
        )
        args.update(kwargs)
        return discovery._discovery_status(**args)

    def test_existing_statuses_are_unchanged(self) -> None:
        self.assertEqual(self._status(provider_failed=True, has_results=True), "partial")
        self.assertEqual(self._status(provider_failed=True), "unavailable")
        self.assertEqual(self._status(no_provider_data=True), "no_provider_data")
        self.assertEqual(self._status(has_results=True), "success")
        self.assertEqual(self._status(), "empty")

    def test_a_filter_matching_nothing_is_not_unavailable(self) -> None:
        # Trails were found, then filtered away, while a provider also failed.
        self.assertEqual(
            self._status(provider_failed=True, filtered_to_nothing=True),
            "partial",
        )

    def test_a_filter_matching_nothing_is_a_success_when_providers_are_fine(self) -> None:
        self.assertEqual(self._status(filtered_to_nothing=True), "success")


if __name__ == "__main__":
    unittest.main()
