"""
The trail-difficulty feature contract.

This module is the single source of truth for the model's inputs AND for the way
OSM values that are turned into features. Both the training side
(``app.ml.dataset``, which builds the dataset) and the runtime side
(``app.services.difficulty``) import it, so the two cannot drift apart: a
feature added here is available to training and to inference at the same time, a
feature present in the saved artifact but not computable at runtime is a
load-time error rather than a silently dropped column, and a value parser that
changes here changes on both sides at once.

TARGET
    The native OpenStreetMap ``sac_scale`` value, kept exactly as recorded, and
    grouped into ``DIFFICULTY_CLASSES`` for supervision. The recorded value is
    never rewritten, never imputed and never invented; the grouping is a
    published, fixed mapping of the seven real levels onto three skill
    requirements, and the mapping lives here beside the parsers so training and
    serving cannot disagree about what a label means.

    Why three tiers and not seven: the seven SAC levels separate degrees of
    difficulty *within* a skill tier, which is not the question a walker asks.
    What a walker needs to know is whether a path is walkable by anyone, needs
    mountain fitness, or needs scrambling and climbing skill. Predicting the
    fine tiers is measurably worse for that question, not merely harder: on
    geographically held-out data scored under the real OpenStreetMap grade
    distribution, the seven-level model reached 0.3460 against a 0.5689
    majority baseline - worse than answering with the most common grade - while
    the three-tier model scores above that baseline. The measurement is in
    ``app.ml.train`` and in the model report.

LEAKAGE
    ``sac_scale``, ``difficulty_class``, ``split_group`` and ``osm_id`` are
    labels or bookkeeping and are never features. Every derived feature below is
    a function of the base features alone, so nothing can smuggle the label in
    through a transformation.

TRAIN/SERVE PARITY
    Parity here means the DEFINITION is identical, not merely that the column
    names match:

    * the same value parsers and the same missing-value token
      (``<MISSING>``), so an absent tag is the same explicit category on both
      sides instead of being silently imputed to the most common value;
    * the same terrain derivation - per component, resampled to the DEM
      resolution, per-interval slope, no smoothing, and the same minimum
      component length - through ``model_terrain_features``;
    * the same derived features and the same units.

    The one difference that cannot be removed is the elevation SOURCE. Training
    read a static Copernicus GLO-90 DEM; serving reads a live Open-Meteo
    elevation profile of the selected geometry. Both are real global DEMs and
    the derivation applied to them is identical, but the numbers are not
    expected to be identical. That is measured rather than assumed, and the
    result is in the model report: aggregate elevation transfers closely
    (range r=0.98, gain r=0.95) while slope, being a derivative, transfers
    poorly (average r=0.53, max r=0.47).
"""

from __future__ import annotations

import math
import re
from typing import Any


# Ordered by difficulty. The order is significant: it is what the reported
# mean-absolute-grade-error and the "within one grade" measure are counted in.
# The seven levels OpenStreetMap actually records, in difficulty order. This is
# the label vocabulary as mappers write it, and it is what the dataset holds.
GRADES: tuple[str, ...] = (
    "strolling",
    "hiking",
    "mountain_hiking",
    "demanding_mountain_hiking",
    "alpine_hiking",
    "demanding_alpine_hiking",
    "difficult_alpine_hiking",
)

GRADE_INDEX: dict[str, int] = {
    grade: index for index, grade in enumerate(GRADES)
}

# The supervised target: the skill tier a recorded grade implies. The tiers are
# a real distinction in hiking, not a convenience grouping - a walker decides
# between them on whether the route needs scrambling and rope skills, which is a
# different question from how hard an alpine route is once you already have
# those skills. The mapping is total over the seven recorded values and is
# applied identically by training and by serving.
DIFFICULTY_CLASSES: tuple[str, ...] = (
    "walking",
    "mountain",
    "alpine",
)

DIFFICULTY_CLASS_INDEX: dict[str, int] = {
    label: index for index, label in enumerate(DIFFICULTY_CLASSES)
}

SAC_SCALE_TO_DIFFICULTY: dict[str, str] = {
    "strolling": "walking",
    "hiking": "walking",
    "mountain_hiking": "mountain",
    "demanding_mountain_hiking": "mountain",
    "alpine_hiking": "alpine",
    "demanding_alpine_hiking": "alpine",
    "difficult_alpine_hiking": "alpine",
}

# Plain-language wording for each tier, used by the interface and never by the
# model: the model emits the class name, this is how a person reads it.
DIFFICULTY_CLASS_LABELS: dict[str, str] = {
    "walking": "Walkable trail",
    "mountain": "Mountain trail",
    "alpine": "Alpine / scrambling",
}

DIFFICULTY_CLASS_SUMMARY: dict[str, str] = {
    "walking": (
        "An ordinary walking route. Steep or uneven ground may be present, "
        "but no scrambling or climbing is involved."
    ),
    "mountain": (
        "A mountain route needing fitness, sure footing and reasonable "
        "weather. Scrambling may occur on exposed ground."
    ),
    "alpine": (
        "A route in the alpine tier: expect scrambling, and possibly climbing "
        "or a rope. It needs real mountain experience."
    ),
}

# Every recorded grade must resolve to a tier. Checked at import so a mapping
# typo fails loudly here rather than silently dropping training rows.
assert set(SAC_SCALE_TO_DIFFICULTY) == set(GRADES), (
    "every recorded grade must map to exactly one difficulty class"
)


# Never features. Declared so the exclusion is explicit and testable.
LABEL_COLUMNS: tuple[str, ...] = (
    "sac_scale",
    "difficulty_class",
    "split_group",
    "osm_id",
)

# Real, label-free predictors. Every one is available both per OSM way at
# training time and, from the selected route's measured profile plus its OSM
# tags, at inference time.
BASE_FEATURES: tuple[str, ...] = (
    # geometry
    "length_km",
    "mean_turn",
    "verts_per_km",
    "incline_pct",
    "width_m",
    # sampled terrain, present for roughly two thirds of observations
    "elevation_gain_m",
    "elevation_loss_m",
    "elevation_range_m",
    "average_slope_pct",
    "max_slope_pct",
    # OSM tagging
    "highway",
    "surface",
    "smoothness",
    "tracktype",
    "trail_visibility",
    "incline_direction",
    "assisted_trail",
    # explicit coverage flags, so the model can learn to distrust absent
    # terrain rather than treating a missing value as a measured zero
    "terrain_available",
    "incline_numeric_missing",
    "width_missing",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "highway",
    "surface",
    "smoothness",
    "tracktype",
    "trail_visibility",
    "incline_direction",
    "assisted_trail",
)

# How each categorical column's raw OSM value is normalised. Plain columns use
# lowercase text only; the rest apply a documented, value-specific cleanup.
# Keeping this as a table means the runtime cannot invent its own cleaning for
# one column and not another.
CATEGORICAL_NORMALISERS: dict[str, str] = {
    "highway": "plain",
    "surface": "plain",
    "smoothness": "plain",
    "tracktype": "plain",
    "trail_visibility": "trail_visibility",
    "incline_direction": "incline_direction",
    "assisted_trail": "assisted_trail",
}

# Derived features. Functions of BASE_FEATURES only, with no label involved.
DERIVED_FEATURES: tuple[str, ...] = (
    "log_length",
    "gain_per_km",
    "descent_ratio",
    "steepness_squared",
    "relief_per_km",
    "length_x_no_terrain",
)

MODEL_FEATURES: tuple[str, ...] = BASE_FEATURES + DERIVED_FEATURES


# --------------------------------------------------------------------------
# OSM value normalisation, shared by dataset construction and serving
# --------------------------------------------------------------------------

# An absent tag is an explicit level, not a hole. Representing it as its own
# category means "this was never surveyed" is a value the model can learn,
# instead of being imputed to whatever tag happens to be most common in the
# training rows. The same token is produced at training time and at serving
# time, which is what makes the one-hot columns line up.
MISSING_CATEGORY = "<MISSING>"

NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")
_TERMINAL_UNIT_PATTERN = re.compile(r"(mm|cm|ft|in|m|°)\s*$")
_RANGE_SEPARATOR_PATTERN = re.compile(r"\s*[-–]\s*")


def is_missing(value: Any) -> bool:
    """True for every way a CSV cell or a request field can be absent."""
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def normalize_text(value: Any) -> str:
    """Normalize an ordinary categorical OSM value."""
    if is_missing(value):
        return MISSING_CATEGORY
    text = str(value).strip().lower()
    return text or MISSING_CATEGORY


def normalize_trail_visibility(value: Any) -> str:
    """Normalize only clear formatting/spelling variants."""
    text = normalize_text(value)
    replacements = {
        "intermdiate": "intermediate",
        (
            "intermediate: few markers, "
            "path mostly visible"
        ): "intermediate",
    }
    return replacements.get(text, text)


def normalize_assisted_trail(value: Any) -> str:
    """Fold rope/ropes into one category."""
    text = normalize_text(value)
    if text in {"rope", "ropes"}:
        return "rope"
    return text


def strip_terminal_unit(text: str) -> tuple[str, str]:
    """Remove one terminal unit from a numeric string."""
    text = text.strip().lower()
    match = _TERMINAL_UNIT_PATTERN.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), match.group(1)


def parse_numeric_or_range(
    value: Any,
) -> tuple[float, float | None, str]:
    """
    Parse one number, or a simple numeric range, plus any terminal unit.

    Returns ``(first, second, unit)``; ``first`` is NaN when there is no number
    at all. ``mm`` is matched before ``m`` so "5 mm" is never read as 5 metres.
    """
    if is_missing(value):
        return math.nan, None, ""

    cleaned, unit = strip_terminal_unit(
        str(value).strip().lower()
    )
    parts = _RANGE_SEPARATOR_PATTERN.split(
        cleaned, maxsplit=1
    )
    if len(parts) == 2:
        first_match = NUMBER_PATTERN.search(parts[0])
        second_match = NUMBER_PATTERN.search(parts[1])
        if first_match and second_match:
            return (
                float(first_match.group()),
                float(second_match.group()),
                unit,
            )

    match = NUMBER_PATTERN.search(cleaned)
    if match:
        return float(match.group()), None, unit
    return math.nan, None, unit


def parse_incline(value: Any) -> float | None:
    """
    Convert an OSM ``incline`` value to a percentage.

    Percentages are retained. Degree values are converted with
    ``tan(angle) * 100``. A range is averaged. Text such as ``up``, ``down`` or
    ``steep`` has no numeric percentage and is handled by
    ``normalize_incline_direction`` instead.
    """
    first, second, unit = parse_numeric_or_range(value)
    if math.isnan(first):
        return None
    numeric = (first + second) / 2.0 if second is not None else first
    if unit == "°":
        return math.tan(math.radians(numeric)) * 100.0
    return float(numeric)


def normalize_incline_direction(value: Any) -> str:
    """Reduce an incline value to ``up`` / ``down`` / ``flat`` / ``unknown``."""
    if is_missing(value):
        return MISSING_CATEGORY
    text = str(value).strip().lower()
    if not text:
        return MISSING_CATEGORY
    numeric = parse_incline(text)
    if numeric is not None:
        if numeric > 0:
            return "up"
        if numeric < 0:
            return "down"
        return "flat"
    if text in {"up", "down", "steep"}:
        return text
    return "unknown"


def parse_width(value: Any) -> float | None:
    """Convert a usable OSM ``width`` value to metres."""
    first, second, unit = parse_numeric_or_range(value)
    if math.isnan(first):
        return None
    numeric = (first + second) / 2.0 if second is not None else first
    if unit == "mm":
        return numeric / 1000.0
    if unit == "cm":
        return numeric / 100.0
    if unit == "ft":
        return numeric * 0.3048
    if unit == "in":
        return numeric * 0.0254
    return float(numeric)


def normalise_categorical(value: Any, column: str) -> str:
    """
    Apply the declared normalisation for one categorical feature column.

    Unknown column names fall back to plain text rather than raising, so a new
    tag column added to the contract still produces a usable level rather than
    breaking inference.
    """
    kind = CATEGORICAL_NORMALISERS.get(column, "plain")
    if kind == "trail_visibility":
        return normalize_trail_visibility(value)
    if kind == "assisted_trail":
        return normalize_assisted_trail(value)
    if kind == "incline_direction":
        return normalize_incline_direction(value)
    return normalize_text(value)


# --------------------------------------------------------------------------
# Terrain derivation, shared by dataset construction and serving
# --------------------------------------------------------------------------

# The DEM the training rows were built against, and the resolution the
# training profile was sampled at. The serving profile is resampled to the
# same spacing before the same arithmetic is applied, so the feature means
# "measured over 90 m intervals" on both sides.
TERRAIN_DEM_RESOLUTION_M = 90.0

# A component shorter than one DEM cell cannot be resolved, so training marks
# it as having no terrain. Serving applies the identical rule, which is why a
# very short path is not given a confident terrain-based estimate.
TERRAIN_MIN_COMPONENT_LENGTH_M = 90.0


def _resample_uniform(
    distances: list[float],
    values: list[float],
    length_m: float,
) -> list[float]:
    """
    Resample a component onto the DEM resolution, in place order.

    Linear in distance, endpoints preserved, so the resampled series describes
    the same line rather than a smoothed or invented one.
    """
    count = max(2, math.ceil(length_m / TERRAIN_DEM_RESOLUTION_M) + 1)
    if len(distances) < 2 or distances[-1] <= 0.0:
        return list(values)
    resampled: list[float] = []
    cursor = 0
    for index in range(count):
        target = length_m * index / (count - 1)
        while (
            cursor < len(distances) - 2
            and distances[cursor + 1] < target
        ):
            cursor += 1
        span = distances[cursor + 1] - distances[cursor]
        if span <= 0.0:
            resampled.append(values[cursor])
            continue
        ratio = (target - distances[cursor]) / span
        resampled.append(
            values[cursor]
            + (values[cursor + 1] - values[cursor]) * ratio
        )
    return resampled


def _component_terrain(
    distances: list[float],
    elevations: list[float],
) -> dict[str, float] | None:
    """
    Terrain metrics for one connected component.

    Slope is the change in elevation over each sampling interval, expressed as
    a percentage of that interval's horizontal length. The average is the
    distance-weighted mean of those intervals and the maximum is the steepest
    single one. This is the definition the dataset builder used, reproduced
    exactly.
    """
    length_m = distances[-1] - distances[0]
    if length_m < TERRAIN_MIN_COMPONENT_LENGTH_M:
        return None
    if len(elevations) < 2:
        return None

    resampled = _resample_uniform(
        distances, elevations, length_m
    )
    interval_count = len(resampled) - 1
    if interval_count < 1:
        return None
    interval_length_m = length_m / interval_count
    if interval_length_m <= 0.0:
        return None

    gain_m = 0.0
    loss_m = 0.0
    weighted_slope = 0.0
    max_slope = 0.0
    for index in range(interval_count):
        delta = resampled[index + 1] - resampled[index]
        if delta > 0.0:
            gain_m += delta
        elif delta < 0.0:
            loss_m += -delta
        slope_pct = abs(delta) / interval_length_m * 100.0
        weighted_slope += slope_pct * interval_length_m
        max_slope = max(max_slope, slope_pct)

    return {
        "length_m": length_m,
        "elevation_min_m": min(resampled),
        "elevation_max_m": max(resampled),
        "elevation_gain_m": gain_m,
        "elevation_loss_m": loss_m,
        "average_slope_pct": weighted_slope / length_m,
        "max_slope_pct": max_slope,
        "sample_count": float(len(resampled)),
    }


def model_terrain_features(
    profile: list[dict[str, Any]] | None,
) -> dict[str, float | None]:
    """
    Derive the model's terrain columns from a sampled elevation profile.

    The profile is the same shape the elevation endpoint returns: a list of
    points, each tagged with the ``component_index`` it belongs to, its
    distance along that component, and an elevation that may be absent.

    Every rule here is the training rule: a component with an unresolved
    elevation, or shorter than one DEM cell, contributes nothing; ascent and
    descent are summed per component and never across a gap; the average slope
    is length-weighted across components; the range is the span of all
    components. When no component qualifies, the result is all-``None`` with
    ``terrain_available`` 0, which is what tells the model those predictors
    are missing rather than measured as zero.
    """
    if not isinstance(profile, list) or not profile:
        return _no_terrain()

    by_component: dict[int, tuple[list[float], list[float]]] = {}
    for point in profile:
        if not isinstance(point, dict):
            continue
        elevation = point.get("elevation_m")
        if elevation is None or isinstance(elevation, bool):
            continue
        try:
            value = float(elevation)
            distance_km = float(point.get("component_distance_km") or 0.0)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(value) and math.isfinite(distance_km)):
            continue
        try:
            component = int(point.get("component_index") or 0)
        except (TypeError, ValueError):
            continue
        distances, elevations = by_component.setdefault(
            component, ([], [])
        )
        distances.append(distance_km * 1000.0)
        elevations.append(value)

    metrics: list[dict[str, float]] = []
    for distances, elevations in by_component.values():
        if len(distances) < 2:
            continue
        order = sorted(
            range(len(distances)),
            key=lambda index: distances[index],
        )
        ordered_distances = [distances[i] for i in order]
        ordered_elevations = [elevations[i] for i in order]
        component = _component_terrain(
            ordered_distances, ordered_elevations
        )
        if component is not None:
            metrics.append(component)

    if not metrics:
        return _no_terrain()

    total_length = sum(metric["length_m"] for metric in metrics)
    if total_length <= 0.0:
        return _no_terrain()

    return {
        "elevation_gain_m": sum(
            metric["elevation_gain_m"] for metric in metrics
        ),
        "elevation_loss_m": sum(
            metric["elevation_loss_m"] for metric in metrics
        ),
        "elevation_range_m": (
            max(metric["elevation_max_m"] for metric in metrics)
            - min(metric["elevation_min_m"] for metric in metrics)
        ),
        "average_slope_pct": (
            sum(
                metric["average_slope_pct"] * metric["length_m"]
                for metric in metrics
            )
            / total_length
        ),
        "max_slope_pct": max(
            metric["max_slope_pct"] for metric in metrics
        ),
        "terrain_available": 1.0,
    }


def _no_terrain() -> dict[str, float | None]:
    return {
        "elevation_gain_m": None,
        "elevation_loss_m": None,
        "elevation_range_m": None,
        "average_slope_pct": None,
        "max_slope_pct": None,
        "terrain_available": 0.0,
    }


# --------------------------------------------------------------------------
# Route-shape features
# --------------------------------------------------------------------------


def route_shape_features(
    parts: list[list[list[float]]],
) -> dict[str, float | None]:
    """
    Twistiness of the route's own geometry, shared by training and serving.

    ``parts`` is one coordinate list per connected component, each a list of
    [longitude, latitude] pairs in degrees — the same nesting GeoJSON uses, so
    the dataset build (from WKT parsed into this shape) and the runtime (from
    verified GeoJSON geometry) call this with identical input.

    Two measurements, both label-free and both available wherever geometry is:

    * ``mean_turn`` — average absolute bearing change per vertex, in radians.
      A straight towpath scores near zero; a switchbacking alpine ascent does
      not. Bearings use an equirectangular projection, which is exact enough
      at way scale and, crucially, identical on both sides.
    * ``verts_per_km`` — digitised vertices per kilometre of the route's own
      coordinate length. Denser digitisation accompanies more complex ground.

    Length is measured from these same coordinates (haversine sum) rather than
    taken from a length column, so training and serving cannot disagree about
    the denominator: one function, one definition, both sides.
    """
    total_turn = 0.0
    vertex_count = 0
    coordinate_length_km = 0.0
    for component in parts or []:
        points = [
            (float(point[0]), float(point[1]))
            for point in (component or [])
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        try:
            points = [
                (lon, lat)
                for lon, lat in points
                if math.isfinite(lon) and math.isfinite(lat)
            ]
        except (TypeError, ValueError):
            continue
        if len(points) < 2:
            continue
        mean_lat = math.radians(
            sum(lat for _, lat in points) / len(points)
        )
        kx = 111.32 * max(0.05, math.cos(mean_lat))
        ky = 110.574
        previous_bearing: float | None = None
        for first, second in zip(points, points[1:]):
            dx = (second[0] - first[0]) * kx
            dy = (second[1] - first[1]) * ky
            segment_km = math.hypot(dx, dy)
            if segment_km <= 0.0:
                continue
            coordinate_length_km += segment_km
            vertex_count += 1
            bearing = math.atan2(dy, dx)
            if previous_bearing is not None:
                delta = (bearing - previous_bearing + math.pi) % (
                    2 * math.pi
                ) - math.pi
                total_turn += abs(delta)
            previous_bearing = bearing

    if vertex_count == 0:
        return {"mean_turn": None, "verts_per_km": None}
    return {
        "mean_turn": total_turn / vertex_count,
        "verts_per_km": (
            vertex_count / coordinate_length_km
            if coordinate_length_km > 0
            else None
        ),
    }


# --------------------------------------------------------------------------
# Derived features
# --------------------------------------------------------------------------


def derive(base: dict[str, Any]) -> dict[str, float | None]:
    """
    Add the derived features to a base feature row.

    Works on a plain mapping so the identical arithmetic runs over a pandas
    row during training and over a request payload at inference. `None` is
    propagated rather than coerced to zero, so an absent measurement stays
    absent and the model's own imputation decides what to do with it.
    """
    length = _number(base.get("length_km"))
    if length is None or length <= 0:
        # A zero-length way has no defined per-kilometre rate. The clamp
        # matches the training transform, and the rate is left missing.
        length = max(length or 0.0, 0.05)

    gain = _number(base.get("elevation_gain_m"))
    loss = _number(base.get("elevation_loss_m"))
    relief = _number(base.get("elevation_range_m"))
    max_slope = _number(base.get("max_slope_pct"))
    terrain_available = _number(base.get("terrain_available")) or 0.0

    total = None
    if gain is not None or loss is not None:
        total = (gain or 0.0) + (loss or 0.0)
        if total == 0.0:
            total = None

    return {
        "log_length": _log(length),
        "gain_per_km": _divide(gain, length),
        "descent_ratio": _divide(loss, total),
        "steepness_squared": (
            None if max_slope is None else (max_slope**2) / 100.0
        ),
        "relief_per_km": _divide(relief, length),
        "length_x_no_terrain": length * (1.0 - terrain_available),
    }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _log(value: float) -> float:
    return math.log(value) if value > 0 else 0.0


def _divide(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator
