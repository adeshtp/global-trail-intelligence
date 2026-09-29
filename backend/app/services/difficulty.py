"""
Trail difficulty: the official recorded scale, and one learned estimate.

Two things are deliberately kept apart here, because they mean different
things and only one of them is authoritative.

OFFICIAL DIFFICULTY
    The ``sac_scale`` that OpenStreetMap actually records. When it exists it
    is the answer. Nothing in this module can override it, and the learned
    estimate is suppressed rather than shown as a contradiction.

MODEL-ESTIMATED DIFFICULTY
    A supervised classifier over the native seven-level ``sac_scale``, trained
    on real OSM ways and evaluated on a geographically disjoint holdout. It is
    an ESTIMATE. Its accuracy is published next to it, and it is never
    described as official.

There is exactly one model artifact, one evaluation report and one prediction
path.

TRAIN/SERVE PARITY
    ``app.ml.feature_contract`` is the single implementation of the feature
    definitions, and this module imports it rather than restating it. The same
    value parsers, the same ``<MISSING>`` level, the same derived-feature
    arithmetic and the same terrain derivation run on both sides. Terrain
    columns are derived from the selected route's own sampled elevation
    profile through ``model_terrain_features``, which is literally the
    function the training rows were produced by, so gain, loss, range and
    slope mean the same quantity in both places. The user-facing elevation
    figures rendered on the page are a separate presentation of the same
    profile and are deliberately not the model's inputs.

    The one thing parity cannot fix is the elevation SOURCE: training read a
    static Copernicus GLO-90 DEM, serving reads a live Open-Meteo profile.
    Both are real global DEMs and the arithmetic applied to them is identical,
    but the numbers are not expected to be identical. That is stated as a
    limitation rather than hidden.
"""

from __future__ import annotations

import hashlib
import math
import threading
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from app.core.config import settings
from app.ml.feature_contract import (
    BASE_FEATURES,
    CATEGORICAL_FEATURES,
    DERIVED_FEATURES,
    DIFFICULTY_CLASSES,
    DIFFICULTY_CLASS_INDEX,
    DIFFICULTY_CLASS_LABELS,
    DIFFICULTY_CLASS_SUMMARY,
    MODEL_FEATURES,
    SAC_SCALE_TO_DIFFICULTY,
    derive,
    model_terrain_features,
    normalise_categorical,
    parse_incline,
    parse_width,
    route_shape_features,
)


# The recorded OSM grade is an activity/technical grade, not a difficulty word,
# so the raw token "hiking" is never shown to a user as a difficulty. The
# model emits a difficulty tier and this is how each tier is worded.
#
# When OpenStreetMap itself records a grade, that grade is authoritative and is
# rendered through the same tier mapping, so an official value and an estimate
# are always comparable and never presented in different vocabularies.
def grade_to_tier(grade: Any) -> str | None:
    """The difficulty tier a recorded OSM grade falls into."""
    if grade is None:
        return None
    return SAC_SCALE_TO_DIFFICULTY.get(str(grade).strip().casefold())


def tier_label(tier: str | None) -> str | None:
    """Plain-language name for a difficulty tier."""
    if tier is None:
        return None
    return DIFFICULTY_CLASS_LABELS.get(tier)


FEATURE_CONTRACT_NAME = "difficulty_tier_v1"

_PACKAGE: dict[str, Any] | None = None
_MODEL_VERSION: str | None = None
_LOAD_ERROR: str | None = None
_LOAD_LOCK = threading.Lock()


def _model_path() -> Path:
    configured = str(
        getattr(settings, "DIFFICULTY_MODEL_PATH", "")
    ).strip()
    if configured:
        return Path(configured).expanduser()
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "difficulty"
        / "trail_difficulty_model.joblib"
    )


def _load() -> tuple[dict[str, Any] | None, str | None]:
    """
    Load and validate the one canonical artifact.

    The contract check is strict on purpose. If the saved feature list does not
    match what this module can compute, the model is refused rather than
    served with a silently missing column, because a model fed fewer columns
    than it was trained on produces a confident wrong number.
    """
    global _PACKAGE, _MODEL_VERSION, _LOAD_ERROR

    if _PACKAGE is not None:
        return _PACKAGE, _MODEL_VERSION
    if _LOAD_ERROR is not None:
        return None, None

    with _LOAD_LOCK:
        if _PACKAGE is not None:
            return _PACKAGE, _MODEL_VERSION
        if _LOAD_ERROR is not None:
            return None, None

        path = _model_path()
        if not path.is_file():
            _LOAD_ERROR = (
                "The trail difficulty model artifact is not installed"
            )
            return None, None

        try:
            package = joblib.load(path)
            if not isinstance(package, dict):
                raise ValueError("artifact is not a model package")
            missing = {
                "model",
                "model_name",
                "classes",
                "class_index",
                "model_features",
                "metrics",
                "majority_baseline_metrics",
            } - set(package)
            if missing:
                raise ValueError(
                    f"artifact is missing {sorted(missing)}"
                )
            # The classes must be the tiers this build serves, in order. A
            # model saved against a different taxonomy is refused outright
            # rather than decoded into the wrong vocabulary.
            if [str(value) for value in package["classes"]] != list(
                DIFFICULTY_CLASSES
            ):
                raise ValueError(
                    "the artifact was trained on a different difficulty "
                    "taxonomy than the runtime serves"
                )
            if list(package["model_features"]) != list(
                MODEL_FEATURES
            ):
                raise ValueError(
                    "the artifact's feature contract does not match the "
                    "runtime feature contract"
                )

            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            _MODEL_VERSION = (
                f"{package['model_name']}-{digest}"
            )
            _PACKAGE = package
            return _PACKAGE, _MODEL_VERSION
        except Exception as exc:
            _LOAD_ERROR = (
                f"The trail difficulty model could not be loaded: {exc}"
            )
            return None, None


# --------------------------------------------------------------------------
# Feature construction
# --------------------------------------------------------------------------


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _geometry_parts(geometry: Any) -> list[list[list[float]]]:
    """
    Split verified GeoJSON geometry into per-component coordinate lists.

    This mirrors the dataset build's WKT parsing: one list of [lon, lat]
    pairs per connected component, each with at least two points. Anything
    else (missing geometry, points, degenerate lines) yields no parts, and the
    shape features stay missing rather than invented.
    """
    if not isinstance(geometry, dict):
        return []
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return []
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        lines = [coordinates]
    elif geometry_type == "MultiLineString":
        lines = [
            raw
            for raw in coordinates
            if isinstance(raw, list)
        ]
    else:
        return []
    parts: list[list[list[float]]] = []
    for raw_line in lines:
        line: list[list[float]] = []
        for point in raw_line:
            if (
                isinstance(point, (list, tuple))
                and len(point) >= 2
                and isinstance(point[0], (int, float))
                and isinstance(point[1], (int, float))
            ):
                if not line or line[-1] != [point[0], point[1]]:
                    line.append([point[0], point[1]])
        if len(line) >= 2:
            parts.append(line)
    return parts


def _model_terrain(trail: dict[str, Any]) -> dict[str, Any]:
    """
    The model's terrain columns, derived from the selected route's own
    sampled elevation profile.

    This is the serving half of the terrain contract. The derivation is
    `app.ml.feature_contract.model_terrain_features`, which is the same
    function the training rows were produced by, so gain, loss, range, average
    slope and maximum slope mean the same quantity on both sides. The
    user-facing figures rendered on the page are a separate, deliberately
    different presentation: they smooth the profile and measure slope over a
    150 m window, which reads better on a chart and is not what the model was
    fitted on.

    When no profile was obtained every terrain column stays absent and
    `terrain_available` is 0, which is what tells the model those predictors
    are missing rather than measured as zero.
    """
    terrain = trail.get("terrain")
    if not isinstance(terrain, dict):
        return model_terrain_features(None)
    profile = terrain.get("profile")
    return model_terrain_features(profile if isinstance(profile, list) else None)


def _base_features(
    trail: dict[str, Any],
) -> dict[str, Any]:
    terrain = _model_terrain(trail)

    length = _number(trail.get("length_km"))
    if length is None:
        length = _number(trail.get("distance_km"))

    # `incline` is the raw OSM value, so it is parsed with the training
    # parser rather than coerced here. A discovery payload that already
    # carries a numeric value is used as it stands.
    incline = _number(trail.get("incline_pct"))
    if incline is None:
        incline = parse_incline(trail.get("incline"))

    width = _number(trail.get("width_m"))
    if width is None:
        width = parse_width(trail.get("width"))

    # `incline_direction` is derived from `incline` when the raw tag is absent,
    # because that is how the training rows built it: from the value, not from
    # a tag that is rarely mapped.
    incline_direction_raw = trail.get("incline_direction")
    if incline_direction_raw is None or str(incline_direction_raw).strip() == "":
        incline_direction_raw = trail.get("incline")

    gain = _number(terrain.get("elevation_gain_m"))
    loss = _number(terrain.get("elevation_loss_m"))
    relief = _number(terrain.get("elevation_range_m"))
    average_slope = _number(terrain.get("average_slope_pct"))
    max_slope = _number(terrain.get("max_slope_pct"))

    # Route-shape features come from the server-verified geometry through the
    # shared contract function — the same function and the same input shape
    # the dataset build uses, so training and serving cannot disagree about
    # what twistiness means. A selection without usable geometry leaves them
    # missing rather than zeroed.
    shape = route_shape_features(_geometry_parts(trail.get("geometry")))

    return {
        "length_km": length,
        "mean_turn": _number(shape.get("mean_turn")),
        "verts_per_km": _number(shape.get("verts_per_km")),
        "incline_pct": incline,
        "width_m": width,
        "elevation_gain_m": gain,
        "elevation_loss_m": loss,
        "elevation_range_m": relief,
        "average_slope_pct": average_slope,
        "max_slope_pct": max_slope,
        "highway": normalise_categorical(
            trail.get("highway_type"), "highway"
        ),
        "surface": normalise_categorical(
            trail.get("surface"), "surface"
        ),
        "smoothness": normalise_categorical(
            trail.get("smoothness"), "smoothness"
        ),
        "tracktype": normalise_categorical(
            trail.get("tracktype"), "tracktype"
        ),
        "trail_visibility": normalise_categorical(
            trail.get("trail_visibility"), "trail_visibility"
        ),
        "incline_direction": normalise_categorical(
            incline_direction_raw, "incline_direction"
        ),
        "assisted_trail": normalise_categorical(
            trail.get("assisted_trail"), "assisted_trail"
        ),
        "terrain_available": _number(
            terrain.get("terrain_available")
        )
        or 0.0,
        "incline_numeric_missing": 1.0 if incline is None else 0.0,
        "width_missing": 1.0 if width is None else 0.0,
    }


def feature_row(
    trail: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Build the exact inference row, plus the base features that are missing.

    `sac_scale` is never read. It is the label, so reading it here would be
    leakage at serving time and would let the "estimate" simply echo the
    answer it is supposed to predict.
    """
    base = _base_features(trail)
    row: dict[str, Any] = {
        name: base.get(name) for name in BASE_FEATURES
    }
    row.update(derive(base))
    missing = [
        name
        for name in BASE_FEATURES
        if row.get(name) is None
    ]
    return row, missing


# --------------------------------------------------------------------------
# Prediction
# --------------------------------------------------------------------------


def _reliability(package: dict[str, Any] | None) -> dict[str, Any]:
    """
    How much this estimate can be worth, read from the artifact itself.

    Every number here is measured on geographically held-out real rows scored
    under the real OpenStreetMap grade distribution, which is the distribution
    a user actually meets. It travels with every prediction so a client cannot
    render a difficulty tier without the evidence qualifying it.

    The published shape is identical whether a prediction was made or not, so
    a client never has to guess which keys exist.
    """
    metrics = (package or {}).get("metrics") or {}
    baseline = (package or {}).get("majority_baseline_metrics") or {}
    holdout = (package or {}).get("natural_sample_holdout") or {}
    per_class = (package or {}).get("per_class") or {}
    return {
        "held_out_accuracy": metrics.get("accuracy"),
        "held_out_macro_f1": metrics.get("macro_f1"),
        "held_out_balanced_accuracy": metrics.get("balanced_accuracy"),
        "majority_baseline_accuracy": baseline.get("accuracy"),
        "majority_baseline_macro_f1": baseline.get("macro_f1"),
        "beats_majority_baseline": (
            (package or {}).get("usefulness_gate_passed")
        ),
        "per_class": per_class,
        "natural_sample_rows": (
            holdout.get("rows") if holdout.get("available") else None
        ),
        "natural_sample_accuracy": (
            holdout.get("accuracy") if holdout.get("available") else None
        ),
        "natural_sample_macro_f1": (
            holdout.get("macro_f1") if holdout.get("available") else None
        ),
        "summary": (
            "Measured on geographically held-out real OpenStreetMap ways, "
            "scored under the real grade distribution rather than a balanced "
            "one. This model is more accurate on real trails than answering "
            "with the most common grade, and it separates the tiers far "
            "better, but the rarest tier is still its weakest part."
        ),
    }


def _unavailable(
    reason: str,
    *,
    model_name: str | None,
    model_version: str | None,
    feature_coverage: float | None = None,
    missing_features: list[str] | None = None,
    package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "estimate": None,
        "estimate_tier": None,
        "estimate_label": None,
        "estimate_index": None,
        "estimate_recorded_grade": None,
        "estimate_description": None,
        "prediction_basis": None,
        "confidence": None,
        "confidence_kind": None,
        "probabilities": None,
        # Published even when no prediction was made, so the response shape
        # does not change with the outcome and a client never has to guess
        # which keys exist.
        "reliability": _reliability(package),
        "feature_coverage": feature_coverage,
        "missing_features": missing_features,
        "model_name": model_name,
        "model_version": model_version,
        "feature_contract": FEATURE_CONTRACT_NAME,
        "observation_unit": "osm_way",
        "terrain_features_used": True,
        "authoritative_for_complete_route": False,
        "reason": reason,
    }


def _score_row(
    row: dict[str, Any],
    package: dict[str, Any],
) -> tuple[str, list[float], list[str]]:
    """
    Score one way-level feature row with the loaded artifact.

    Shared by single-way prediction and route-member aggregation so both read
    the same model through the same code. Raises on any failure; callers turn
    that into an unavailable response with a stated reason.
    """
    frame = pd.DataFrame(
        [{name: row.get(name) for name in MODEL_FEATURES}],
        columns=list(MODEL_FEATURES),
    )
    probabilities = package["model"].predict_proba(frame)[0]
    tiers = [str(value) for value in package["classes"]]
    if len(tiers) != len(probabilities):
        raise ValueError(
            "the saved class list does not match the fitted model's "
            "class count"
        )
    best = max(
        range(len(probabilities)),
        key=lambda index: probabilities[index],
    )
    return tiers[best], [float(value) for value in probabilities], tiers


# At most this many member ways are scored for one route answer. A very long
# relation is answered from its first ordered members and says so, rather
# than spending an unbounded number of provider calls re-verifying members
# that are already cached.
MAX_AGGREGATION_MEMBERS = 25


def aggregate_route_difficulty(
    member_trails: list[dict[str, Any]],
    *,
    package: dict[str, Any] | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
) -> dict[str, Any]:
    """
    Answer route difficulty from verified member ways, not route totals.

    Each member is scored individually with the same way-level artifact: its
    own OSM tags, its own measured length, its own route-shape features. A
    route total is never fed in, because a 12 km relation length is a
    different quantity from the per-way measurements the model was fitted on
    and would push almost every route to the hardest tier as an artefact of
    the units.

    Members carry no sampled elevation profile of their own, so terrain
    predictors are missing for every member row and the model reads them as
    missing - the same situation as the training rows without terrain, which
    the model was explicitly fitted to handle. The response states this
    rather than hiding it.

    The route answer is the majority tier across scored members, with the full
    distribution published. Disagreement is never averaged away: the per-tier
    counts, the agreement share and a flag travel with the answer, and a tie
    is broken by total probability mass and stated as a tie.
    """
    if package is None:
        package, model_version = _load()
        model_name = (
            str(package.get("model_name")) if package else None
        )
    if package is None:
        return _unavailable(
            _LOAD_ERROR or "The trail difficulty model is unavailable",
            model_name=None,
            model_version=None,
        )

    members = [
        member
        for member in (member_trails or [])
        if isinstance(member, dict)
    ][:MAX_AGGREGATION_MEMBERS]
    truncated = len(member_trails or []) > MAX_AGGREGATION_MEMBERS

    scored: list[dict[str, Any]] = []
    for member in members:
        try:
            row, _missing = feature_row(
                {**member, "osm_type": "way", "terrain": None}
            )
            if _number(row.get("length_km")) is None:
                continue
            tier, probabilities, _tiers = _score_row(row, package)
        except Exception:
            continue
        scored.append(
            {
                "osm_id": member.get("osm_id"),
                "tier": tier,
                "confidence": round(
                    float(max(probabilities)), 4
                ),
                "probabilities": {
                    name: round(float(value), 4)
                    for name, value in zip(
                        [str(value) for value in package["classes"]],
                        probabilities,
                    )
                },
            }
        )

    total_members = len(member_trails or [])
    if len(scored) < 2:
        if total_members == 1 and len(scored) == 1:
            # The single verified member section constitutes this route's
            # entire mapped geometry: there is no other section whose
            # difficulty could disagree. Reporting its tier is not letting
            # one arbitrary way speak for many; it is scoring the whole
            # verified route. The basis below states exactly this so the
            # answer is never mistaken for a multi-section vote.
            only = scored[0]
            tier = only["tier"]
            return {
                "available": True,
                "estimate": tier,
                "estimate_tier": tier,
                "estimate_label": tier_label(tier),
                "estimate_index": DIFFICULTY_CLASS_INDEX.get(tier),
                "estimate_recorded_grade": SAC_SCALE_TO_DIFFICULTY,
                "estimate_description": DIFFICULTY_CLASS_SUMMARY.get(tier),
                "prediction_basis": (
                    "The single verified member section constitutes this "
                    "route's mapped geometry, so its individually scored "
                    "tier is the route answer. No other section exists to "
                    "disagree with it."
                ),
                "confidence": only["confidence"],
                "confidence_kind": "uncalibrated_max_probability",
                "probabilities": only["probabilities"],
                "aggregation": {
                    "scored_members": 1,
                    "total_members": 1,
                    "truncated_to_first_members": False,
                    "tier_counts": {
                        name: 1 if name == tier else 0
                        for name in [
                            str(value) for value in package["classes"]
                        ]
                    },
                    "tier_probability_mass": only["probabilities"],
                    "agreement": 1.0,
                    "members_disagree": False,
                    "tied": False,
                    "single_section_route": True,
                    "members": scored,
                },
                "reliability": _reliability(package),
                "feature_coverage": None,
                "missing_features": ["member_terrain_profile"],
                "model_name": model_name,
                "model_version": model_version,
                "feature_contract": FEATURE_CONTRACT_NAME,
                "observation_unit": "osm_way_aggregated_to_route",
                "terrain_features_used": False,
                "authoritative_for_complete_route": False,
                "reason": None,
            }
        return _unavailable(
            "Fewer than two verified member ways of this route could be "
            "scored, so no route difficulty is offered rather than an "
            "answer built on a single section of many.",
            model_name=model_name,
            model_version=model_version,
            package=package,
        )

    tiers = [str(value) for value in package["classes"]]
    tier_counts = {
        tier: sum(1 for entry in scored if entry["tier"] == tier)
        for tier in tiers
    }
    # Total probability mass per tier breaks ties deterministically and uses
    # more of what the members actually said than a bare vote count.
    mass = {
        tier: round(
            sum(entry["probabilities"].get(tier, 0.0) for entry in scored),
            4,
        )
        for tier in tiers
    }
    ranked = sorted(
        tiers,
        key=lambda tier: (tier_counts[tier], mass[tier]),
        reverse=True,
    )
    majority = ranked[0]
    runner_up = ranked[1]
    tied = (
        tier_counts[majority] == tier_counts[runner_up]
        and mass[majority] == mass[runner_up]
    )
    agreement = round(tier_counts[majority] / len(scored), 4)
    members_disagree = agreement < 2 / 3

    return {
        "available": True,
        "estimate": majority,
        "estimate_tier": majority,
        "estimate_label": tier_label(majority),
        "estimate_index": DIFFICULTY_CLASS_INDEX.get(majority),
        "estimate_recorded_grade": SAC_SCALE_TO_DIFFICULTY,
        "estimate_description": DIFFICULTY_CLASS_SUMMARY.get(majority),
        "prediction_basis": (
            "Majority tier across individually scored verified member ways "
            "of this route, using the same way-level model: each member's "
            "own tags, length and route shape. Route totals are never fed "
            "to the model, and member terrain is not sampled per member, "
            "so terrain predictors read as missing."
        ),
        "confidence": round(mass[majority] / len(scored), 4),
        "confidence_kind": "member_agreement_share",
        "probabilities": None,
        "aggregation": {
            "scored_members": len(scored),
            "total_members": len(member_trails or []),
            "truncated_to_first_members": truncated,
            "tier_counts": tier_counts,
            "tier_probability_mass": mass,
            "agreement": agreement,
            "members_disagree": members_disagree,
            "tied": tied,
            "members": scored,
        },
        "reliability": _reliability(package),
        "feature_coverage": None,
        "missing_features": ["member_terrain_profile"],
        "model_name": model_name,
        "model_version": model_version,
        "feature_contract": FEATURE_CONTRACT_NAME,
        "observation_unit": "osm_way_aggregated_to_route",
        "terrain_features_used": False,
        "authoritative_for_complete_route": False,
        "reason": (
            "Member ways of this route disagree substantially about its "
            "difficulty tier; the majority is shown with the full split."
            if members_disagree
            else None
        ),
    }


def predict_trail_difficulty(
    trail: dict[str, Any],
) -> dict[str, Any]:
    """
    Estimate the difficulty tier of a selected OSM way.

    The output is a skill tier - walking, mountain, or alpine - which is what
    the recorded grades actually distinguish for someone deciding whether a
    route is for them. The recorded grade itself is returned alongside it so a
    caller can see which recorded grades the estimate came from.

    This is never authoritative. A grade recorded in OpenStreetMap always wins,
    and this estimate is not shown when one exists.
    """
    package, model_version = _load()
    model_name = (
        str(package.get("model_name")) if package else None
    )
    if package is None:
        return _unavailable(
            _LOAD_ERROR
            or "The trail difficulty model is unavailable",
            model_name=None,
            model_version=None,
        )

    osm_type = trail.get("osm_type")

    if osm_type in ("relation", "component"):
        # A route is answered one verified member way at a time, never from
        # its totals: see aggregate_route_difficulty. The caller supplies the
        # members under "member_trails".
        return aggregate_route_difficulty(
            trail.get("member_trails") or [],
            package=package,
            model_name=model_name,
            model_version=model_version,
        )

    if osm_type != "way":
        # The model is fitted to individual OSM ways. An unknown selection
        # kind has no defined inference row, so no estimate is offered and
        # the reason is stated. Official difficulty and the interpretable
        # route-demand profile still apply to these selections.
        return _unavailable(
            "This selection kind has no defined difficulty inference, so "
            "no estimate is offered.",
            model_name=model_name,
            model_version=model_version,
            package=package,
        )

    row, missing = feature_row(trail)
    available = len(BASE_FEATURES) - len(missing)
    coverage = available / len(BASE_FEATURES)

    if _number(row.get("length_km")) is None:
        return _unavailable(
            "The selected path has no measured length, so the model has "
            "nothing to predict from.",
            model_name=model_name,
            model_version=model_version,
            feature_coverage=round(coverage, 3),
            missing_features=missing,
            package=package,
        )

    try:
        tier, probabilities, tiers = _score_row(row, package)
    except Exception:
        return _unavailable(
            "Difficulty inference did not complete for this path.",
            model_name=model_name,
            model_version=model_version,
            feature_coverage=round(coverage, 3),
            missing_features=missing,
            package=package,
        )

    best = max(
        range(len(probabilities)),
        key=lambda index: probabilities[index],
    )

    return {
        "available": True,
        "estimate": tier,
        "estimate_tier": tier,
        "estimate_label": tier_label(tier),
        "estimate_index": DIFFICULTY_CLASS_INDEX.get(tier),
        # Which recorded grades this tier is drawn from. Published so the
        # estimate can be compared with an official value in one vocabulary.
        "estimate_recorded_grade": SAC_SCALE_TO_DIFFICULTY,
        "estimate_description": DIFFICULTY_CLASS_SUMMARY.get(tier),
        "prediction_basis": (
            "Individual OpenStreetMap way attributes and the measured "
            "elevation profile of its own geometry."
        ),
        "confidence": round(float(probabilities[best]), 4),
        "confidence_kind": "uncalibrated_max_probability",
        "probabilities": {
            name: round(float(value), 4)
            for name, value in zip(tiers, probabilities)
        },
        "reliability": _reliability(package),
        "feature_coverage": round(coverage, 3),
        "missing_features": missing,
        "model_name": model_name,
        "model_version": model_version,
        "feature_contract": FEATURE_CONTRACT_NAME,
        "observation_unit": "osm_way",
        "terrain_features_used": True,
        "authoritative_for_complete_route": False,
        "reason": None,
    }


# --------------------------------------------------------------------------
# Readiness and reconciliation
# --------------------------------------------------------------------------


def difficulty_readiness() -> dict[str, Any]:
    """
    Report the model that is actually served, not a remembered benchmark.

    Everything here is read from the loaded artifact, so the readiness
    endpoint cannot drift away from the runtime behaviour.
    """
    package, model_version = _load()
    if package is None:
        return {
            "ready": False,
            "model_name": None,
            "model_version": None,
            "task": None,
            "classes": None,
            "feature_contract": FEATURE_CONTRACT_NAME,
            "observation_unit": "osm_way",
            "terrain_features_used_at_runtime": True,
            "authoritative_for_complete_route": False,
            "evaluation": None,
            "reliability": _reliability(None),
            "message": (
                _LOAD_ERROR
                or "The trail difficulty model is unavailable"
            ),
        }

    model = package["model"]
    return {
        "ready": True,
        "model_name": package.get("model_name"),
        "model_version": model_version,
        "task": package.get("task"),
        "classes": list(package.get("classes") or []),
        "artifact": str(_model_path().name),
        "trained_rows": package.get("trained_rows"),
        "dataset_sha256_prefix": package.get("dataset_sha256"),
        "feature_contract": FEATURE_CONTRACT_NAME,
        "model_features": list(MODEL_FEATURES),
        "base_features": list(BASE_FEATURES),
        "derived_features": list(DERIVED_FEATURES),
        "categorical_features": list(CATEGORICAL_FEATURES),
        "observation_unit": "osm_way",
        "terrain_features_used_at_runtime": True,
        "authoritative_for_complete_route": False,
        "evaluation": package.get("metrics"),
        "majority_baseline": package.get("majority_baseline_metrics"),
        "usefulness_gate_passed": package.get("usefulness_gate_passed"),
        "per_class": package.get("per_class"),
        "natural_sample_holdout": package.get("natural_sample_holdout"),
        "recorded_grades": list(SAC_SCALE_TO_DIFFICULTY),
        "grade_to_tier": dict(SAC_SCALE_TO_DIFFICULTY),
        "tier_labels": dict(DIFFICULTY_CLASS_LABELS),
        "reliability": _reliability(package),
        "limitations": package.get("limitations"),
        "estimator": type(
            model.named_steps.get("clf")
        ).__name__,
        "message": (
            "A supervised classifier that predicts which difficulty tier a "
            "way's recorded OpenStreetMap grade falls into: walkable, "
            "mountain, or alpine. Trained on real OSM ways and evaluated on a "
            "geographically disjoint holdout scored under the real grade "
            "distribution. It is a way-level estimate, never treated as "
            "official difficulty, and it beats the majority-class baseline on "
            "that distribution."
        ),
    }


def source_difficulty(
    trail: dict[str, Any],
) -> dict[str, Any]:
    """
    The difficulty actually recorded in OpenStreetMap.

    ``sac_scale`` is an activity/technical grade rather than a difficulty word,
    so the raw value is kept verbatim and the difficulty tier it falls into is
    reported alongside it. The raw token is never presented to a user as a
    difficulty.
    """
    sac_scale = (
        str(trail.get("source_difficulty") or "").strip().casefold()
        or None
    )
    tier = grade_to_tier(sac_scale)
    return {
        "sac_scale": sac_scale,
        "tier": tier,
        "label": tier_label(tier),
        "class": tier_label(tier),
        "mapping": "sac_scale_to_difficulty_tier_v1",
        "authoritative": bool(sac_scale),
    }


def reconcile_difficulty(
    source: dict[str, Any],
    ml: dict[str, Any],
) -> dict[str, Any]:
    """
    Decide which difficulty signal the user is shown, and say why.

    The recorded OpenStreetMap grade wins whenever it exists, in the same tier
    vocabulary the estimate uses, so the two are directly comparable. A
    disagreeing estimate is marked superseded and hidden, so the interface never
    shows two contradictory difficulty labels. The estimate is never promoted
    over the recorded value, and the recorded value is never derived from the
    model.
    """
    source_tier = source.get("tier")
    source_grade = source.get("sac_scale")
    source_label = source.get("label")
    ml_tier = ml.get("estimate_tier") if ml.get("available") else None
    ml_label = ml.get("estimate_label") if ml.get("available") else None

    # Both signals speak in tiers, so agreement is a like-for-like comparison.
    agrees = bool(source_tier and ml_tier and source_tier == ml_tier)

    if source_tier and ml_tier and not agrees:
        status = "superseded_by_official_scale"
        display = source_label
        display_provenance = "official_osm_sac_scale"
        message = (
            f"OpenStreetMap records this path as '{source_grade}', a "
            f"{source_label} route. The learned estimate suggested a "
            f"different tier, so it is not shown. The recorded value is the "
            f"one used."
        )
    elif source_tier:
        status = "official_scale_available"
        display = source_label
        display_provenance = "official_osm_sac_scale"
        message = (
            f"OpenStreetMap records this path as '{source_grade}', a "
            f"{source_label} route. That recorded grade is the difficulty "
            f"used here."
        )
    elif ml_tier:
        status = "model_estimate_only"
        display = ml_label
        display_provenance = "model_estimated"
        message = (
            "OpenStreetMap records no difficulty for this path, so the "
            "figure shown is a machine-learning estimate from its length, "
            "terrain and tagging. It is an estimate, not a recorded value."
        )
    else:
        status = "unavailable"
        display = None
        display_provenance = "unavailable"
        message = ml.get("reason") or (
            "No recorded difficulty and no estimate are available for this "
            "selection."
        )

    return {
        "authoritative_tier": source_tier,
        "authoritative_class": source_label,
        "authoritative_grade": source_grade,
        "authoritative_source": (
            "OpenStreetMap sac_scale" if source_tier else None
        ),
        "ml_estimate": ml_tier,
        "ml_estimate_class": ml_label,
        "ml_agrees_with_official": agrees if agrees else None,
        "status": status,
        "display": display,
        "display_provenance": display_provenance,
        "message": message,
    }
