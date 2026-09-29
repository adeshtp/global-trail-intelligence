"""
A way is a duplicate of a route only if it lies along it.

The collapse step dropped any way that shared a name with a discovered relation
whose bounding box overlapped the way's. A bounding box is a loose test: a
335 km route's box covers a whole region. The benchmark found strong-evidence
ways dropped this way that were not along the route at all (Sentiero Natura is
a 1.8 km way; its same-name relation is 1.3 km). A way is now merged into a
route only when at least 90% of its length lies within about 5 m of the
route's line.
"""

from __future__ import annotations

import unittest

from app.routes import discovery

ROUTE = {
    "type": "MultiLineString",
    "coordinates": [[[6.00, 45.0], [6.05, 45.0], [6.10, 45.0]]],
}


def _relation(name: str = "Sentier des Gardes", **fields) -> dict:
    candidate = {
        "osm_type": "relation",
        "osm_id": 100,
        "trail_id": "relation:100",
        "name": name,
        "aliases": [],
        "geometry": ROUTE,
        "member_way_ids": [],
    }
    candidate.update(fields)
    return candidate


def _way(coordinates, name: str = "Sentier des Gardes", osm_id: int = 7) -> dict:
    return {
        "osm_type": "way",
        "osm_id": osm_id,
        "trail_id": f"way:{osm_id}",
        "name": name,
        "aliases": [],
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }


def _kept_way_ids(*candidates: dict) -> set[int]:
    return {
        int(c["osm_id"])
        for c in discovery._collapse_connected_named_ways(list(candidates))
        if c["osm_type"] == "way"
    }


class WayAgainstRouteTests(unittest.TestCase):
    def test_a_way_lying_along_the_route_is_merged_into_it(self) -> None:
        along = _way([[6.02, 45.0], [6.04, 45.0]])
        self.assertEqual(_kept_way_ids(_relation(), along), set())

    def test_a_tiny_piece_exactly_on_the_route_is_merged(self) -> None:
        tiny = _way([[6.0500, 45.0], [6.0502, 45.0]])
        self.assertEqual(_kept_way_ids(_relation(), tiny), set())

    def test_a_way_that_only_crosses_the_route_is_kept(self) -> None:
        crossing = _way([[6.05, 44.99], [6.05, 45.01]])
        self.assertEqual(_kept_way_ids(_relation(), crossing), {7})

    def test_a_way_that_only_touches_the_route_end_is_kept(self) -> None:
        touching = _way([[6.10, 45.0], [6.10, 45.02]])
        self.assertEqual(_kept_way_ids(_relation(), touching), {7})

    def test_a_parallel_way_a_hundred_metres_off_is_kept(self) -> None:
        parallel = _way([[6.02, 45.0009], [6.04, 45.0009]])
        self.assertEqual(_kept_way_ids(_relation(), parallel), {7})

    def test_a_longer_way_that_only_partly_follows_the_route_is_kept(self) -> None:
        # Mostly beyond the end of the route, as Sentiero Natura is longer
        # than the relation carrying its name.
        longer = _way([[6.09, 45.0], [6.10, 45.0], [6.20, 45.0]])
        self.assertEqual(_kept_way_ids(_relation(), longer), {7})

    def test_a_way_with_another_name_is_untouched(self) -> None:
        other = _way([[6.02, 45.0], [6.04, 45.0]], name="Chemin du Lac")
        self.assertEqual(_kept_way_ids(_relation(), other), {7})

    def test_a_listed_member_is_still_merged_by_identity(self) -> None:
        # Members are decided by OSM id, never by geometry: unchanged.
        far_member = _way([[7.0, 46.0], [7.1, 46.0]], name="Anything", osm_id=9)
        self.assertEqual(
            _kept_way_ids(_relation(member_way_ids=[9]), far_member), set()
        )

    def test_an_alias_of_the_route_counts_as_its_name(self) -> None:
        along = _way([[6.02, 45.0], [6.04, 45.0]], name="Gardes Path")
        relation = _relation(aliases=["Gardes Path"])
        self.assertEqual(_kept_way_ids(relation, along), set())

    def test_two_ways_are_still_deduplicated_by_id(self) -> None:
        a = _way([[6.02, 44.9], [6.04, 44.9]], name="Solo", osm_id=5)
        b = _way([[6.02, 44.9], [6.04, 44.9]], name="Solo", osm_id=5)
        self.assertEqual(_kept_way_ids(a, b), {5})


class ScaleTests(unittest.TestCase):
    """
    Each way was checked against every relation's name in turn, so a country
    of 20,000 ways and 2,000 relations cost tens of millions of comparisons
    (about 14 s of a profiled 18 s assembly). Relations are indexed by name.
    """

    def test_thousands_of_ways_against_thousands_of_relations_is_fast(self) -> None:
        import time

        candidates = []
        for index in range(2000):
            candidates.append(
                _relation(
                    name=f"Route {index}",
                    osm_id=index,
                    trail_id=f"relation:{index}",
                    geometry={
                        "type": "MultiLineString",
                        "coordinates": [[[6.0 + index * 1e-3, 45.0], [6.0005 + index * 1e-3, 45.0]]],
                    },
                )
            )
        for index in range(20000):
            candidates.append(
                _way(
                    [[8.0 + index * 1e-4, 46.0], [8.00005 + index * 1e-4, 46.0]],
                    name=f"Way {index}",
                    osm_id=100000 + index,
                )
            )
        started = time.perf_counter()
        kept = discovery._collapse_connected_named_ways(candidates)
        elapsed = time.perf_counter() - started
        self.assertEqual(len([c for c in kept if c["osm_type"] == "way"]), 20000)
        self.assertLess(elapsed, 1.0)

    def test_the_index_still_matches_on_name_alone_for_the_check(self) -> None:
        # Same name, along the route: merged. Different name, same place: kept.
        along = _way([[6.02, 45.0], [6.04, 45.0]], name="Sentier des Gardes", osm_id=1)
        other = _way([[6.02, 45.0], [6.04, 45.0]], name="Another Path", osm_id=2)
        self.assertEqual(_kept_way_ids(_relation(), along, other), {2})


if __name__ == "__main__":
    unittest.main()
