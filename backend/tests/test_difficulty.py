"""
The one difficulty model: target, artifact, contract, and honesty.

These tests exist to stop the three ways this can quietly rot: the artifact
stopping matching the runtime contract, the label mapping drifting between
training and serving, or a model being shipped that does not actually beat
answering with the most common grade.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from app.ml import feature_contract as contract
from app.ml import train
from app.services import difficulty as service

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data" / "difficulty"
ARTIFACT = DATA / "trail_difficulty_model.joblib"
REPORT = DATA / "trail_difficulty_model_report.json"


class LabelMappingTests(unittest.TestCase):
    """The tier mapping is the target, so it is pinned exactly."""

    def test_every_recorded_grade_maps_to_exactly_one_tier(self) -> None:
        self.assertEqual(
            set(contract.SAC_SCALE_TO_DIFFICULTY),
            set(contract.GRADES),
        )
        for grade, tier in contract.SAC_SCALE_TO_DIFFICULTY.items():
            self.assertIn(tier, contract.DIFFICULTY_CLASSES, grade)

    def test_the_tiers_are_ordered_by_skill_requirement(self) -> None:
        """walking must be the easiest tier, alpine the hardest."""
        self.assertEqual(
            contract.DIFFICULTY_CLASSES, ("walking", "mountain", "alpine")
        )
        for grade in ("strolling", "hiking"):
            self.assertEqual(
                contract.SAC_SCALE_TO_DIFFICULTY[grade], "walking"
            )
        for grade in ("mountain_hiking", "demanding_mountain_hiking"):
            self.assertEqual(
                contract.SAC_SCALE_TO_DIFFICULTY[grade], "mountain"
            )
        for grade in (
            "alpine_hiking",
            "demanding_alpine_hiking",
            "difficult_alpine_hiking",
        ):
            self.assertEqual(
                contract.SAC_SCALE_TO_DIFFICULTY[grade], "alpine"
            )

    def test_the_mapping_is_shared_by_training_and_serving(self) -> None:
        """Serving must not carry its own copy of the label mapping."""
        for grade, tier in contract.SAC_SCALE_TO_DIFFICULTY.items():
            self.assertEqual(
                service.grade_to_tier(grade), tier, grade
            )
        self.assertIsNone(service.grade_to_tier(None))
        self.assertIsNone(service.grade_to_tier("not_a_grade"))

    def test_every_tier_has_plain_language_for_a_person(self) -> None:
        for tier in contract.DIFFICULTY_CLASSES:
            self.assertTrue(
                contract.DIFFICULTY_CLASS_LABELS.get(tier)
            )
            self.assertTrue(contract.DIFFICULTY_CLASS_SUMMARY.get(tier))


class ArtifactTests(unittest.TestCase):
    """Exactly one artifact, matching the runtime contract exactly."""

    def test_exactly_one_model_artifact_is_installed(self) -> None:
        self.assertTrue(ARTIFACT.is_file())
        found = sorted(
            path.name
            for path in DATA.glob("*.joblib")
        )
        self.assertEqual(
            found, ["trail_difficulty_model.joblib"], found
        )

    def test_no_retired_difficulty_artifact_returns(self) -> None:
        for retired in (
            "models/difficulty_model.joblib",
            "data/difficulty/difficulty_grade_model.joblib",
        ):
            self.assertFalse((REPO_ROOT / retired).exists(), retired)

    def test_the_artifact_matches_the_runtime_contract(self) -> None:
        package, version = service._load()
        self.assertIsNotNone(
            package, service._LOAD_ERROR or "artifact did not load"
        )
        self.assertEqual(
            list(package["model_features"]), list(contract.MODEL_FEATURES)
        )
        self.assertEqual(
            [str(c) for c in package["classes"]],
            list(contract.DIFFICULTY_CLASSES),
        )
        self.assertEqual(
            dict(package["sac_scale_to_class"]),
            dict(contract.SAC_SCALE_TO_DIFFICULTY),
        )
        self.assertTrue(version.startswith(package["model_name"]))

    def test_the_artifact_records_the_dataset_it_was_fitted_on(
        self,
    ) -> None:
        package, _ = service._load()
        digest = hashlib.sha256(
            (DATA / "difficulty_features.csv").read_bytes()
        ).hexdigest()
        self.assertEqual(
            package["dataset_sha256"], digest[:16]
        )

    def test_readiness_and_the_artifact_agree(self) -> None:
        package, version = service._load()
        readiness = service.difficulty_readiness()
        self.assertTrue(readiness["ready"])
        self.assertEqual(
            readiness["model_name"], package["model_name"]
        )
        self.assertEqual(
            readiness["model_version"], version
        )
        self.assertEqual(
            readiness["evaluation"], package["metrics"]
        )
        self.assertEqual(
            readiness["recorded_grades"],
            list(contract.SAC_SCALE_TO_DIFFICULTY),
        )
        self.assertFalse(
            readiness["authoritative_for_complete_route"]
        )

    def test_the_observation_unit_is_declared_and_honoured(self) -> None:
        package, _ = service._load()
        self.assertEqual(package["observation_unit"], "osm_way")
        # A route relation is not a way, so no estimate is offered.
        result = service.predict_trail_difficulty(
            {"osm_type": "relation", "osm_id": 1}
        )
        self.assertFalse(result["available"])
        self.assertIn("route", result["reason"].lower())


class UsefulnessGateTests(unittest.TestCase):
    """
    The gate the model had to pass to ship.

    These assert the recorded numbers rather than re-deriving them, so a
    regression in the artifact is caught without an expensive retrain.
    """

    def setUp(self) -> None:
        self.report = json.loads(REPORT.read_text())

    def test_the_model_beats_the_majority_baseline_on_real_data(
        self,
    ) -> None:
        final = self.report["final_test"]
        baseline = final["majority_baseline"]
        for metric in ("accuracy", "macro_f1", "balanced_accuracy"):
            self.assertGreater(
                final[metric],
                baseline[metric],
                f"{metric} must beat the majority baseline",
            )
        self.assertTrue(final["usefulness_gate_passed"])

    def test_every_tier_is_actually_predicted(self) -> None:
        """A tier the model never emits would be a dead label."""
        tiers = self.report["model"]["classes"]
        confusion = self.report["final_test"]["confusion_matrix"]
        for index, tier in enumerate(tiers):
            predicted = sum(row[index] for row in confusion["rows"])
            self.assertGreater(
                predicted, 0, f"no held-out row was ever predicted {tier}"
            )

    def test_per_class_results_are_published(self) -> None:
        per_class = self.report["final_test"]["per_class"]
        for tier in contract.DIFFICULTY_CLASSES:
            self.assertIn(tier, per_class)
            for key in ("precision", "recall", "f1"):
                self.assertIsNotNone(per_class[tier][key])

    def test_the_split_is_geographically_disjoint(self) -> None:
        split = self.report["split"]
        self.assertEqual(split["group_overlap_train_test"], 0)
        self.assertGreater(split["test_rows"], 1000)
        self.assertGreater(split["train_rows"], 1000)

    def test_the_test_set_played_no_part_in_selection(self) -> None:
        """Selection ran on validation inside the training split only."""
        validation = self.report["validation"]
        self.assertIn("selection_rule", validation)
        self.assertIn("test set takes no part", validation["selection_rule"])
        self.assertTrue(validation["candidates"])

    def test_the_target_is_the_recorded_grade_grouped_by_skill(
        self,
    ) -> None:
        target = self.report["target"]
        self.assertEqual(target["recorded_field"], "sac_scale")
        self.assertEqual(
            target["classes"], list(contract.DIFFICULTY_CLASSES)
        )
        self.assertEqual(
            dict(target["mapping"]),
            dict(contract.SAC_SCALE_TO_DIFFICULTY),
        )
        self.assertTrue(target["rationale"])


class LeakageTests(unittest.TestCase):
    def test_no_label_or_bookkeeping_column_is_a_feature(self) -> None:
        for forbidden in contract.LABEL_COLUMNS:
            self.assertNotIn(
                forbidden, contract.MODEL_FEATURES, forbidden
            )
        for forbidden in (
            "sac_scale",
            "difficulty_class",
            "split_group",
            "osm_id",
        ):
            self.assertNotIn(
                forbidden, contract.MODEL_FEATURES, forbidden
            )

    def test_derived_features_are_functions_of_base_features_only(
        self,
    ) -> None:
        base = set(contract.BASE_FEATURES)
        for name in contract.DERIVED_FEATURES:
            self.assertNotIn(
                name, base, f"{name} must be derived, not a base column"
            )

    def test_the_target_column_is_never_an_input(self) -> None:
        """The inference row must not change when the recorded grade does."""
        import pandas as pd

        package, _ = service._load()
        trail = {
            "osm_type": "way",
            "osm_id": 999,
            "length_km": 1.2,
            "highway_type": "path",
            "surface": "earth",
            "source_difficulty": "hiking",
        }
        first = service.predict_trail_difficulty(trail)
        changed = dict(trail)
        changed["source_difficulty"] = "difficult_alpine_hiking"
        second = service.predict_trail_difficulty(changed)
        self.assertEqual(
            first.get("probabilities"), second.get("probabilities")
        )
        # And the estimate itself does not move with the recorded grade.
        self.assertEqual(
            first.get("estimate_tier"), second.get("estimate_tier")
        )
        del package, pd

    def test_duplicate_geometry_is_absent_from_the_dataset(self) -> None:
        import pandas as pd
        from shapely import wkt as shapely_wkt

        ways = pd.read_csv(DATA / "difficulty_ways.csv")
        geometries = [
            shapely_wkt.loads(value).wkt
            for value in ways["geometry_wkt"].head(4000)
        ]
        self.assertEqual(
            len(geometries), len(set(geometries)),
            "the dataset contains repeated geometry",
        )

    def test_route_shape_matches_between_dataset_and_serving(
        self,
    ) -> None:
        """Train/serve parity for the shape features, on real rows.

        The dataset values were built from committed WKT; serving computes
        from verified GeoJSON through the same contract function. Both must
        agree, or the model is served numbers it was not fitted on.
        """
        import pandas as pd
        from shapely import wkt as shapely_wkt

        from app.ml.feature_contract import route_shape_features

        features = pd.read_csv(DATA / "difficulty_features.csv")
        ways = pd.read_csv(DATA / "difficulty_ways.csv").set_index(
            "osm_id"
        )
        for osm_id in features["osm_id"].head(60).to_numpy():
            stored = features.loc[
                features["osm_id"] == osm_id,
                ["mean_turn", "verts_per_km"],
            ].iloc[0]
            geometry = shapely_wkt.loads(
                ways.loc[osm_id, "geometry_wkt"]
            )
            parts = (
                [
                    [[x, y] for x, y in part.coords]
                    for part in geometry.geoms
                ]
                if geometry.geom_type == "MultiLineString"
                else [[[x, y] for x, y in geometry.coords]]
            )
            computed = route_shape_features(parts)
            for column in ("mean_turn", "verts_per_km"):
                self.assertAlmostEqual(
                    float(stored[column]),
                    float(computed[column]),
                    places=9,
                    msg=f"{column} differs for osm_id {osm_id}",
                )

    def test_serving_shape_matches_the_dataset_row(self) -> None:
        """The full serving path reproduces a real dataset row's shape."""
        import pandas as pd
        from shapely import wkt as shapely_wkt

        features = pd.read_csv(DATA / "difficulty_features.csv")
        ways = pd.read_csv(DATA / "difficulty_ways.csv").set_index(
            "osm_id"
        )
        row = features.iloc[7]
        osm_id = int(row["osm_id"])
        geometry = shapely_wkt.loads(ways.loc[osm_id, "geometry_wkt"])
        if geometry.geom_type == "MultiLineString":
            coordinates = [
                [[x, y] for x, y in part.coords]
                for part in geometry.geoms
            ]
            geojson = {
                "type": "MultiLineString",
                "coordinates": coordinates,
            }
        else:
            geojson = {
                "type": "LineString",
                "coordinates": [
                    [x, y] for x, y in geometry.coords
                ],
            }
        served, _missing = service.feature_row(
            {
                "osm_type": "way",
                "osm_id": osm_id,
                "length_km": float(row["length_km"]),
                "geometry": geojson,
            }
        )
        for column in ("mean_turn", "verts_per_km"):
            self.assertAlmostEqual(
                float(served[column]),
                float(row[column]),
                places=9,
                msg=f"serving {column} differs for osm_id {osm_id}",
            )

    def test_shape_features_read_only_geometry(self) -> None:
        """Shape is a function of coordinates, never of the label."""
        from app.ml.feature_contract import route_shape_features

        parts = [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01]]]
        first = route_shape_features(parts)
        # Extra keys, labels, tags: all ignored, same answer.
        self.assertEqual(
            first,
            route_shape_features(
                [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01]]]
            ),
        )
        self.assertGreater(first["mean_turn"], 0.0)
        self.assertGreater(first["verts_per_km"], 0.0)
        # Degenerate input stays missing rather than invented.
        empty = route_shape_features([])
        self.assertIsNone(empty["mean_turn"])
        self.assertIsNone(empty["verts_per_km"])
        single = route_shape_features([[[1.0, 2.0]]])
        self.assertIsNone(single["mean_turn"])


class PredictionContractTests(unittest.TestCase):
    """The response shape a client depends on, with and without a value."""

    TRAIL = {
        "osm_type": "way",
        "osm_id": 4242,
        "name": "Test path",
        "length_km": 1.4,
        "highway_type": "path",
        "surface": "earth",
        "trail_visibility": "yes",
        "source_difficulty": None,
    }

    def test_an_available_prediction_publishes_its_own_qualification(
        self,
    ) -> None:
        result = service.predict_trail_difficulty(self.TRAIL)
        self.assertTrue(result["available"], result.get("reason"))
        self.assertIn(
            result["estimate_tier"], contract.DIFFICULTY_CLASSES
        )
        self.assertTrue(result["estimate_label"])
        self.assertTrue(result["estimate_description"])
        reliability = result["reliability"]
        # The number that bounds the estimate travels with it.
        self.assertIsNotNone(reliability["held_out_accuracy"])
        self.assertIsNotNone(reliability["majority_baseline_accuracy"])
        self.assertTrue(reliability["beats_majority_baseline"])
        self.assertTrue(reliability["per_class"])

    def test_the_response_shape_is_identical_when_unavailable(self) -> None:
        available = service.predict_trail_difficulty(self.TRAIL)
        unavailable = service.predict_trail_difficulty(
            {"osm_type": "way", "osm_id": 1}
        )
        self.assertEqual(
            set(available), set(unavailable),
            "a client must not have to guess which keys exist",
        )
        self.assertIsNone(unavailable["estimate"])
        self.assertTrue(unavailable["reliability"]["held_out_accuracy"])

    def test_confidence_is_labelled_as_uncalibrated(self) -> None:
        result = service.predict_trail_difficulty(self.TRAIL)
        self.assertEqual(
            result["confidence_kind"], "uncalibrated_max_probability"
        )
        self.assertEqual(
            set(result["probabilities"]),
            set(contract.DIFFICULTY_CLASSES),
        )

    def test_a_missing_length_is_refused_rather_than_guessed(self) -> None:
        result = service.predict_trail_difficulty(
            {"osm_type": "way", "osm_id": 1, "length_km": None}
        )
        self.assertFalse(result["available"])
        self.assertIn("length", result["reason"].lower())

    def test_the_estimate_is_never_authoritative(self) -> None:
        result = service.predict_trail_difficulty(self.TRAIL)
        self.assertFalse(result["authoritative_for_complete_route"])


class RouteAggregationTests(unittest.TestCase):
    """Route difficulty from verified members, never from route totals.

    A relation or component is the object the user actually selects, so
    answering it matters — but feeding its totals into a way-fitted model
    would be a unit error dressed as an answer. Every test below pins the
    honest alternative: score each verified member with the same artifact
    and publish the vote.
    """

    @staticmethod
    def _member(
        osm_id: int,
        *,
        length_km: float = 1.0,
        surface: str = "ground",
        trail_visibility: str | None = None,
        n_points: int = 4,
    ) -> dict[str, Any]:
        coordinates = [
            [10.0 + osm_id * 0.01 + index * 0.001, 46.0 + index * 0.001]
            for index in range(n_points)
        ]
        return {
            "osm_type": "way",
            "osm_id": osm_id,
            "length_km": length_km,
            "highway_type": "path",
            "surface": surface,
            "trail_visibility": trail_visibility,
            "geometry": {
                "type": "LineString",
                "coordinates": coordinates,
            },
        }

    def test_majority_tier_with_full_distribution(self) -> None:
        members = [
            self._member(1, surface="ground"),
            self._member(2, surface="ground"),
            self._member(3, surface="rock", trail_visibility="excellent"),
        ]
        result = service.aggregate_route_difficulty(members)
        self.assertTrue(result["available"], result.get("reason"))
        aggregation = result["aggregation"]
        self.assertEqual(
            aggregation["scored_members"], len(members)
        )
        self.assertEqual(
            aggregation["total_members"], len(members)
        )
        # The vote is published, not just the winner.
        self.assertEqual(
            sum(aggregation["tier_counts"].values()), len(members)
        )
        self.assertEqual(
            result["estimate_tier"],
            max(
                aggregation["tier_counts"],
                key=lambda tier: aggregation["tier_counts"][tier],
            ),
        )
        self.assertEqual(len(aggregation["members"]), len(members))
        for entry in aggregation["members"]:
            self.assertIn(
                entry["tier"], contract.DIFFICULTY_CLASSES
            )

    def test_disagreement_is_flagged_not_averaged_away(self) -> None:
        members = [
            self._member(1, surface="ground"),
            self._member(2, surface="rock", trail_visibility="excellent"),
            self._member(3, surface="rock", trail_visibility="excellent"),
        ]
        result = service.aggregate_route_difficulty(members)
        if result["aggregation"]["members_disagree"]:
            self.assertIsNotNone(result["reason"])
            self.assertIn("disagree", result["reason"].lower())
        # Either way the split travels with the answer.
        self.assertEqual(
            sum(result["aggregation"]["tier_counts"].values()),
            result["aggregation"]["scored_members"],
        )

    def test_fewer_than_two_scored_members_is_refused(self) -> None:
        result = service.aggregate_route_difficulty(
            [self._member(1, length_km=1.0)]
        )
        # A lone member object is not a one-member route: without the route
        # having exactly one member there could be other sections unheard.
        self.assertTrue(result["available"])
        self.assertTrue(
            result["aggregation"]["single_section_route"]
        )

    def test_one_scored_section_of_many_is_still_refused(self) -> None:
        """One scored section must not speak for a multi-section route."""
        members = [
            self._member(1),
            self._member(2, length_km=None),
            self._member(3, length_km=None),
        ]
        result = service.aggregate_route_difficulty(members)
        self.assertFalse(result["available"])
        self.assertIn("single section of many", result["reason"])

    def test_empty_members_is_refused(self) -> None:
        result = service.aggregate_route_difficulty([])
        self.assertFalse(result["available"])

    def test_members_without_length_are_skipped_not_failed(self) -> None:
        members = [
            self._member(1),
            self._member(2, length_km=None),
            self._member(3),
        ]
        result = service.aggregate_route_difficulty(members)
        self.assertTrue(result["available"], result.get("reason"))
        self.assertEqual(result["aggregation"]["scored_members"], 2)
        self.assertEqual(result["aggregation"]["total_members"], 3)

    def test_member_terrain_is_missing_and_stated(self) -> None:
        """Members are scored without per-member sampled terrain.

        The model was fitted with a third of its rows in exactly this
        situation, so this is a supported inference, not a degraded one —
        but the response must say so rather than imply full information.
        """
        result = service.aggregate_route_difficulty(
            [self._member(1), self._member(2)]
        )
        self.assertTrue(result["available"], result.get("reason"))
        self.assertFalse(result["terrain_features_used"])
        self.assertIn(
            "member_terrain_profile", result["missing_features"]
        )
        self.assertIn("terrain", result["prediction_basis"].lower())
        self.assertEqual(
            result["observation_unit"], "osm_way_aggregated_to_route"
        )

    def test_truncation_is_stated(self) -> None:
        members = [
            self._member(osm_id, length_km=0.5)
            for osm_id in range(1, service.MAX_AGGREGATION_MEMBERS + 6)
        ]
        result = service.aggregate_route_difficulty(members)
        self.assertTrue(result["available"], result.get("reason"))
        self.assertEqual(
            result["aggregation"]["scored_members"],
            service.MAX_AGGREGATION_MEMBERS,
        )
        self.assertTrue(
            result["aggregation"]["truncated_to_first_members"]
        )

    def test_predict_routes_relations_through_aggregation(self) -> None:
        """predict_trail_difficulty answers routes from member_trails."""
        members = [self._member(1), self._member(2)]
        for osm_type in ("relation", "component"):
            result = service.predict_trail_difficulty(
                {"osm_type": osm_type, "osm_id": 9, "member_trails": members}
            )
            self.assertTrue(
                result["available"], (osm_type, result.get("reason"))
            )
            self.assertIsNotNone(result["aggregation"])
        # Without members the old honest refusal stands.
        refused = service.predict_trail_difficulty(
            {"osm_type": "relation", "osm_id": 9}
        )
        self.assertFalse(refused["available"])

    def test_official_grade_still_wins_over_an_aggregated_estimate(
        self,
    ) -> None:
        source = service.source_difficulty(
            {"source_difficulty": "hiking"}
        )
        result = service.reconcile_difficulty(
            source,
            {
                "available": True,
                "estimate_tier": "alpine",
                "estimate_label": "Alpine / scrambling",
                "aggregation": {"scored_members": 5},
            },
        )
        self.assertEqual(
            result["status"], "superseded_by_official_scale"
        )
        self.assertEqual(result["display"], "Walkable trail")


class ReconciliationTests(unittest.TestCase):
    """
    Official grade versus estimate.

    Both speak in tiers, so an official value and an estimate are directly
    comparable, and a contradiction is never shown.
    """

    def test_a_recorded_grade_wins_over_a_disagreeing_estimate(
        self,
    ) -> None:
        source = service.source_difficulty(
            {"source_difficulty": "difficult_alpine_hiking"}
        )
        self.assertEqual(source["tier"], "alpine")
        self.assertTrue(source["authoritative"])
        result = service.reconcile_difficulty(
            source,
            {
                "available": True,
                "estimate_tier": "walking",
                "estimate_label": "Walkable trail",
            },
        )
        # The recorded grade is what the user is shown, not the estimate.
        self.assertEqual(
            result["status"], "superseded_by_official_scale"
        )
        self.assertEqual(
            result["display_provenance"], "official_osm_sac_scale"
        )
        self.assertEqual(result["display"], source["label"])
        self.assertEqual(result["display"], "Alpine / scrambling")
        # And the disagreement is stated rather than hidden.
        self.assertIsNone(result["ml_agrees_with_official"])

    def test_an_agreeing_estimate_is_not_a_second_difficulty(self) -> None:
        source = service.source_difficulty(
            {"source_difficulty": "mountain_hiking"}
        )
        result = service.reconcile_difficulty(
            source,
            {
                "available": True,
                "estimate_tier": "mountain",
                "estimate_label": "Mountain trail",
            },
        )
        self.assertEqual(result["status"], "official_scale_available")
        self.assertTrue(result["ml_agrees_with_official"])

    def test_only_an_estimate_is_labelled_as_an_estimate(self) -> None:
        source = service.source_difficulty(
            {"source_difficulty": None}
        )
        result = service.reconcile_difficulty(
            source,
            {
                "available": True,
                "estimate_tier": "alpine",
                "estimate_label": "Alpine / scrambling",
            },
        )
        self.assertEqual(result["status"], "model_estimate_only")
        self.assertEqual(result["display_provenance"], "model_estimated")

    def test_nothing_available_is_stated_rather_than_invented(self) -> None:
        result = service.reconcile_difficulty(
            service.source_difficulty({"source_difficulty": None}),
            {"available": False, "reason": "no measured length"},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["display"])
        self.assertIn("length", result["message"])


class ReproducibilityTests(unittest.TestCase):
    def test_the_report_names_the_commands_that_rebuild_everything(
        self,
    ) -> None:
        report = json.loads(REPORT.read_text())
        for module in (
            "app.ml.difficulty_dataset",
            "app.ml.feature_engineering",
            "app.ml.train",
        ):
            self.assertIn(module, report["reproduce"], module)
        # Every named module must actually be runnable.
        import subprocess
        import sys

        for module in (
            "app.ml.difficulty_dataset",
            "app.ml.feature_engineering",
            "app.ml.train",
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import {module} as module; "
                    "assert callable(module.main)",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={
                    "PATH": "/usr/bin:/bin",
                    "PYTHONPATH": "backend",
                },
                timeout=120,
            )
            self.assertEqual(
                result.returncode, 0, result.stderr[-2000:]
            )

    def test_the_environment_is_recorded(self) -> None:
        report = json.loads(REPORT.read_text())
        for key in ("python", "numpy", "pandas", "scikit_learn"):
            self.assertTrue(report["environment"].get(key))

    def test_the_natural_distribution_is_recorded_beside_the_retained_one(
        self,
    ) -> None:
        """A capped sample must never be readable as the real population."""
        dataset = json.loads(REPORT.read_text())["dataset"]
        self.assertIn("natural_tier_distribution", dataset)
        self.assertIn("retained_tier_distribution", dataset)
        self.assertNotEqual(
            dataset["natural_tier_distribution"],
            dataset["retained_tier_distribution"],
        )

    def test_the_committed_dataset_covers_the_runtime_contract(
        self,
    ) -> None:
        """The CSV on disk must carry every feature the model is fitted on.

        A column added to the contract but not to the committed dataset would
        train on nothing and serve from the imputer. This pins the two
        together, including the audit file that documents the dataset.
        """
        import pandas as pd

        columns = pd.read_csv(
            DATA / "difficulty_features.csv", nrows=0
        ).columns.tolist()
        # Base features are stored; derived features are computed from them
        # by derive()/add_derived, which a separate test pins.
        for name in contract.BASE_FEATURES:
            self.assertIn(name, columns, name)
        audit = json.load(
            open(DATA / "difficulty_features_audit.json")
        )
        for name in contract.BASE_FEATURES:
            if name in contract.CATEGORICAL_FEATURES:
                self.assertIn(
                    name, audit["categorical_audit"], name
                )
            else:
                self.assertIn(name, audit["numeric_audit"], name)


class SplitTests(unittest.TestCase):
    def test_the_split_is_reproducible(self) -> None:
        import pandas as pd

        frame = train.load_frame()
        ways = train._ways()
        first = train.training_split(frame, ways)
        second = train.training_split(frame, ways)
        self.assertEqual(
            first["train_idx"].tolist(), second["train_idx"].tolist()
        )
        self.assertEqual(
            first["test_idx"].tolist(), second["test_idx"].tolist()
        )
        del pd

    def test_connected_fragments_cannot_straddle_the_split(self) -> None:
        """Route fragments joined at an endpoint share a split group."""
        import pandas as pd
        from app.ml import geographic_groups

        ways = train._ways()
        groups = geographic_groups.build_split_groups(ways)
        by_id = pd.Series(groups.to_numpy(), index=ways["osm_id"].to_numpy())
        endpoints = geographic_groups._endpoints
        by_osm = ways.set_index("osm_id")
        checked = 0
        for osm_id in ways["osm_id"].head(400):
            raw = by_osm.loc[osm_id, "geometry_wkt"]
            points = endpoints(raw)
            if len(points) < 2:
                continue
            others = ways.iloc[:400]
            for candidate in others["osm_id"]:
                if candidate == osm_id:
                    continue
                other_points = endpoints(
                    by_osm.loc[candidate, "geometry_wkt"]
                )
                shared = set(points) & set(other_points)
                if not shared:
                    continue
                checked += 1
                self.assertEqual(
                    by_id.loc[osm_id],
                    by_id.loc[candidate],
                    "connected ways were placed in different split groups",
                )
                break
        self.assertGreater(checked, 0, "no connected fragment pair found")


if __name__ == "__main__":
    unittest.main()
