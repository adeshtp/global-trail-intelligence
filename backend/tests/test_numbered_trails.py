"""
A numbered marked path is a trail.

Recall against Postpass for Chamonix found four real, marked hiking paths named
only "16", "19" and "23" (sac_scale=mountain_hiking, up to 1.5 km) that were
rejected as "non-descriptive name". A bare number is a survey marker when
nothing else says hiking, but hiking metadata is exactly what says so.
"""

from __future__ import annotations

import unittest

from app.routes import discovery
from app.services.postpass import PostpassWay


def _way(name: str, **fields) -> PostpassWay:
    values = dict(
        way_id=1,
        name=name,
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
        point_count=10,
        length_km=1.0,
        geometry=None,
    )
    values.update(fields)
    return PostpassWay(**values)


def _accepted(way: PostpassWay) -> bool:
    return discovery._named_way_evidence(way, place="Chamonix")[0]


class NumberedTrailTests(unittest.TestCase):
    def test_a_numbered_path_with_a_recorded_grade_is_kept(self) -> None:
        for name in ("23", "16", "19", "7a"):
            with self.subTest(name=name):
                self.assertTrue(
                    _accepted(_way(name, sac_scale="mountain_hiking"))
                )

    def test_a_numbered_path_with_recorded_visibility_is_kept(self) -> None:
        self.assertTrue(_accepted(_way("23", trail_visibility="good")))

    def test_a_short_numbered_piece_of_a_marked_path_is_kept(self) -> None:
        self.assertTrue(
            _accepted(
                _way("16", sac_scale="mountain_hiking", length_km=0.041)
            )
        )

    def test_a_bare_number_with_no_hiking_evidence_is_still_rejected(self) -> None:
        for fields in ({}, {"surface": "gravel"}, {"highway": "footway"}):
            with self.subTest(fields=fields):
                self.assertFalse(_accepted(_way("23", **fields)))

    def test_a_single_character_or_symbol_is_still_rejected(self) -> None:
        for name in ("a", "-", "#", "?", ""):
            with self.subTest(name=name):
                self.assertFalse(
                    _accepted(_way(name, sac_scale="mountain_hiking"))
                )

    def test_a_long_number_is_not_a_trail_number(self) -> None:
        # A phone number or id, not a path number.
        self.assertFalse(
            _accepted(_way("2547109", sac_scale="mountain_hiking"))
        )

    def test_the_reported_evidence_says_why(self) -> None:
        _, _, reasons, evidence_class = discovery._named_way_evidence(
            _way("23", sac_scale="mountain_hiking"), place="Chamonix"
        )
        self.assertEqual(evidence_class, "strong")
        self.assertTrue(any("sac_scale" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
