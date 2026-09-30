"""
A pedestrian facility is not a hiking trail.

A live Edinburgh search returned Brunswick Street, Redbraes Place and
Featherhall Crescent as trails. Their only evidence was ``foot=designated``,
which mappers put on pedestrian streets and paved cycle/foot paths, so it
cannot count as hiking evidence on a street or a built-up surface. On a
natural surface it still does, so real countryside paths are kept.
"""

from __future__ import annotations

import unittest

from app.routes import discovery
from app.services.postpass import PostpassWay


def _way(
    name: str,
    *,
    highway: str,
    surface: str | None,
    length_km: float = 0.6,
    foot: str | None = "designated",
    **extra,
) -> PostpassWay:
    return PostpassWay(
        way_id=1,
        name=name,
        route=None,
        highway=highway,
        sac_scale=extra.pop("sac_scale", None),
        trail_visibility=extra.pop("trail_visibility", None),
        surface=surface,
        smoothness=None,
        tracktype=None,
        access=None,
        incline=None,
        incline_direction=None,
        width=None,
        assisted_trail=None,
        aliases=[],
        geometry_type="LineString",
        point_count=10,
        length_km=length_km,
        geometry=None,
        foot=foot,
        **extra,
    )


class FootDesignatedTests(unittest.TestCase):
    def _accepted(self, way: PostpassWay, place: str = "Edinburgh") -> bool:
        return discovery._named_way_evidence(way, place=place)[0]

    def test_designated_pedestrian_streets_are_not_trails(self) -> None:
        for name, highway, surface in (
            ("Brunswick Street", "pedestrian", "paving_stones"),
            ("Melvin Walk", "pedestrian", "paving_stones"),
            ("Figgate Lane", "pedestrian", "concrete"),
            ("Redbraes Place", "footway", "asphalt"),
            ("Mausley Park", "path", "asphalt"),
            ("Featherhall Crescent", "pedestrian", "asphalt"),
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    self._accepted(
                        _way(name, highway=highway, surface=surface)
                    )
                )

    def test_designated_paths_on_natural_ground_are_kept(self) -> None:
        for name, highway, surface in (
            ("Nell Burn Path", "footway", "gravel"),
            ("Pentland School Lane", "footway", "dirt"),
            ("Chemin de la Rosaire", "path", None),
            ("Powies Path", "track", None),
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    self._accepted(
                        _way(name, highway=highway, surface=surface)
                    )
                )

    def test_real_hiking_tags_still_win_on_a_paved_way(self) -> None:
        # Hut approaches and short summit paths are frequently paved.
        for extra in ({"sac_scale": "hiking"}, {"trail_visibility": "good"}):
            with self.subTest(extra=extra):
                self.assertTrue(
                    self._accepted(
                        _way(
                            "Sentier du Refuge",
                            highway="path",
                            surface="asphalt",
                            **extra,
                        )
                    )
                )


class PedestrianWayTests(unittest.TestCase):
    """
    highway=pedestrian is a street with the cars taken off it, not a trail.
    Without hiking evidence it is not accepted whatever else is tagged, so a
    missing surface tag cannot let one through.
    """

    def _accepted(self, way: PostpassWay) -> bool:
        return discovery._named_way_evidence(way, place="Edinburgh")[0]

    def test_an_untagged_pedestrian_way_is_not_a_trail(self) -> None:
        for foot in (None, "designated", "yes"):
            with self.subTest(foot=foot):
                self.assertFalse(
                    self._accepted(
                        _way(
                            "Featherhall Crescent",
                            highway="pedestrian",
                            surface=None,
                            length_km=0.6,
                            foot=foot,
                        )
                    )
                )

    def test_a_long_untagged_pedestrian_way_is_not_a_trail(self) -> None:
        self.assertFalse(
            self._accepted(
                _way(
                    "Old Town Passage",
                    highway="pedestrian",
                    surface="gravel",
                    length_km=2.0,
                    foot=None,
                )
            )
        )

    def test_a_pedestrian_way_with_real_hiking_evidence_is_kept(self) -> None:
        for extra in ({"sac_scale": "hiking"}, {"trail_visibility": "good"}):
            with self.subTest(extra=extra):
                self.assertTrue(
                    self._accepted(
                        _way(
                            "Sentier du Lac",
                            highway="pedestrian",
                            surface="gravel",
                            foot=None,
                            **extra,
                        )
                    )
                )

    def test_a_pedestrian_way_that_says_it_is_a_trail_is_kept(self) -> None:
        self.assertTrue(
            self._accepted(
                _way(
                    "Woodland Nature Trail",
                    highway="pedestrian",
                    surface="gravel",
                    foot=None,
                )
            )
        )

    def test_paths_footways_and_tracks_are_unchanged(self) -> None:
        for highway in ("path", "footway", "track"):
            with self.subTest(highway=highway):
                self.assertTrue(
                    self._accepted(
                        _way(
                            "Nell Burn Path",
                            highway=highway,
                            surface="gravel",
                            length_km=0.8,
                            foot="designated",
                        )
                    )
                )


if __name__ == "__main__":
    unittest.main()
