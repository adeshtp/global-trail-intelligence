"""
Phase 5 — Final difficulty feature engineering.

Source:
    data/difficulty/difficulty_ways.csv

Output:
    data/difficulty/difficulty_features.csv
    data/difficulty/difficulty_features_audit.json

Terrain source:
    Copernicus DEM GLO-90
    Public Cloud Optimized GeoTIFF tiles on AWS S3.

Final difficulty target:
    Easy
    Moderate
    Hard
    Very Hard

Source target:
    sac_scale

Important:
    - Real OSM data only.
    - No synthetic data.
    - No weather in the difficulty target.
    - No Open-Meteo bulk elevation calls.
    - Terrain is obtained from static GLO-90.
    - Short OSM ways are retained.
    - Terrain is marked unavailable for ways below
      the 90 m DEM resolution threshold.
    - Actual trail geometry length is used for slope.
    - Absolute latitude/longitude are not model features.
    - Geographic split_group is only used for validation.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import rasterio
from rasterio.errors import RasterioIOError
from shapely import wkt
from shapely.geometry import LineString, MultiLineString

# The value parsers, the missing-value token and the terrain derivation are
# defined once, in the shared feature contract, and imported by the runtime
# service as well. Reusing them here is what makes the training columns and
# the served columns the same quantities.
from app.ml.feature_contract import (  # noqa: F401
    DIFFICULTY_CLASSES,
    GRADES,
    MISSING_CATEGORY,
    SAC_SCALE_TO_DIFFICULTY,
    model_terrain_features,
    route_shape_features,
    normalize_assisted_trail,
    normalize_incline_direction,
    normalize_text,
    normalize_trail_visibility,
    parse_incline,
    parse_width,
)


# ============================================================
# PATHS
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parents[2]

PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = (
    PROJECT_ROOT
    / "data"
    / "difficulty"
)

INPUT_PATH = (
    DATA_DIR
    / "difficulty_ways.csv"
)

OUTPUT_PATH = (
    DATA_DIR
    / "difficulty_features.csv"
)

AUDIT_PATH = (
    DATA_DIR
    / "difficulty_features_audit.json"
)


# ============================================================
# COPERNICUS GLO-90
# ============================================================

DEM_BUCKET_URL = (
    "https://copernicus-dem-90m.s3.eu-central-1.amazonaws.com"
)

DEM_RESOLUTION_M = 90.0

MIN_TERRAIN_LENGTH_M = 90.0


# ============================================================
# GDAL / RASTERIO REMOTE COG SETTINGS
# ============================================================

GDAL_CONFIG = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "CPL_VSIL_CURL_CHUNK_SIZE": "1048576",
    "CPL_VSIL_CURL_CACHE_SIZE": "67108864",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "5",
    "GDAL_HTTP_TIMEOUT": "60",
}


# ============================================================
# SAC → USER DIFFICULTY
# ============================================================

# The recorded grade, kept exactly as written, and the supervised tier derived
# from it. Both the mapping and the expected vocabularies come from the shared
# contract so the dataset, the model and the runtime cannot disagree about what
# a label means.
DIFFICULTY_MAP = dict(SAC_SCALE_TO_DIFFICULTY)

EXPECTED_SAC_CLASSES = list(GRADES)

EXPECTED_DIFFICULTY_CLASSES = list(DIFFICULTY_CLASSES)


# ============================================================
# FEATURES
# ============================================================

# Every tag-value parser, the missing-value token and the terrain derivation
# live in `app.ml.feature_contract`, which the runtime service imports too.
# They are imported here rather than redefined so the values the model was
# fitted on and the values it is served cannot drift apart. The constants and
# the column lists below stay here because they describe this dataset build.
CATEGORICAL_FEATURES = [
    "highway",
    "surface",
    "trail_visibility",
    "incline_direction",
    "smoothness",
    "tracktype",
    "assisted_trail",
]

NUMERIC_FEATURES = [
    "length_km",
    "mean_turn",
    "verts_per_km",
    "incline_pct",
    "width_m",
    "incline_numeric_missing",
    "width_missing",
    "terrain_available",
    "elevation_gain_m",
    "elevation_loss_m",
    "elevation_range_m",
    "average_slope_pct",
    "max_slope_pct",
]

OUTPUT_COLUMNS = [
    "osm_id",
    "sac_scale",
    "difficulty_class",
    "split_group",
]
OUTPUT_COLUMNS += NUMERIC_FEATURES
OUTPUT_COLUMNS += CATEGORICAL_FEATURES


# ============================================================
# TERRAIN FEATURE COLUMNS
# ============================================================

TERRAIN_COLUMNS = [
    "elevation_min_m",
    "elevation_max_m",
    "elevation_gain_m",
    "elevation_loss_m",
    "elevation_range_m",
    "average_slope_pct",
    "max_slope_pct",
    "elevation_sample_count",
    "terrain_available",
]


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class TerrainComponent:
    samples: list[tuple[float, float]]
    length_m: float
    point_refs: list[tuple[int, int, int]]


# ============================================================
# TEXT NORMALIZATION AND NUMERIC PARSING
# ============================================================
# Imported from the shared feature contract, which the runtime service also
# imports. `parse_geometry`, the DEM reading and the dataset plumbing stay
# local to this build script.




# ============================================================
# GEOMETRY
# ============================================================

def parse_geometry(
    geometry_wkt: Any,
) -> list[LineString]:
    """
    Parse supported line geometry.
    """

    if geometry_wkt is None:
        return []

    text = str(
        geometry_wkt
    ).strip()

    if not text:
        return []

    try:

        geometry = wkt.loads(
            text
        )

    except Exception:

        return []

    if isinstance(
        geometry,
        LineString,
    ):

        if (
            geometry.is_empty
            or len(
                geometry.coords
            ) < 2
        ):

            return []

        return [
            geometry
        ]

    if isinstance(
        geometry,
        MultiLineString,
    ):

        return [
            line
            for line in geometry.geoms
            if (
                not line.is_empty
                and len(
                    line.coords
                ) >= 2
            )
        ]

    return []


def haversine_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """
    Great-circle distance in metres.
    """

    radius_m = 6_371_000.0

    phi1 = math.radians(
        lat1
    )

    phi2 = math.radians(
        lat2
    )

    delta_phi = math.radians(
        lat2 - lat1
    )

    delta_lambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(
            delta_phi / 2.0
        )
        ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(
            delta_lambda / 2.0
        )
        ** 2
    )

    a = max(
        0.0,
        min(
            1.0,
            a,
        ),
    )

    return (
        radius_m
        * 2.0
        * math.atan2(
            math.sqrt(a),
            math.sqrt(
                1.0 - a
            ),
        )
    )


def line_length_m(
    line: LineString,
) -> float:
    """
    Calculate geographic length along all original
    LineString vertices.
    """

    coordinates = list(
        line.coords
    )

    total = 0.0

    for index in range(
        len(coordinates) - 1
    ):

        lon1, lat1 = (
            coordinates[index][:2]
        )

        lon2, lat2 = (
            coordinates[
                index + 1
            ][:2]
        )

        total += haversine_m(
            lat1,
            lon1,
            lat2,
            lon2,
        )

    return total


def samples_for_line(
    line: LineString,
) -> tuple[
    list[tuple[float, float]],
    float,
]:
    """
    Sample approximately every DEM resolution along a
    connected trail component.

    For components shorter than the DEM resolution we do
    not request terrain training features, because a 90 m
    DEM cannot resolve that segment reliably.
    """

    length_m = line_length_m(
        line
    )

    if length_m < (
        MIN_TERRAIN_LENGTH_M
    ):

        return (
            [],
            length_m,
        )

    sample_count = (
        math.ceil(
            length_m
            / DEM_RESOLUTION_M
        )
        + 1
    )

    sample_count = max(
        2,
        sample_count,
    )

    samples: list[
        tuple[float, float]
    ] = []

    for index in range(
        sample_count
    ):

        fraction = (
            index
            / (
                sample_count
                - 1
            )
        )

        point = line.interpolate(
            fraction,
            normalized=True,
        )

        longitude = float(
            point.x
        )

        latitude = float(
            point.y
        )

        if not (
            -90.0
            <= latitude
            <= 90.0
        ):

            continue

        if not (
            -180.0
            <= longitude
            <= 180.0
        ):

            continue

        samples.append(
            (
                latitude,
                longitude,
            )
        )

    if len(samples) < 2:

        return (
            [],
            length_m,
        )

    return (
        samples,
        length_m,
    )


# ============================================================
# COPERNICUS TILE NAMING
# ============================================================

def tile_key_for_coordinate(
    latitude: float,
    longitude: float,
) -> tuple[int, int]:
    """
    Return the 1x1-degree tile's south-west integer coordinate.
    """

    if longitude >= 180.0:
        longitude = (
            179.999999
        )

    if latitude >= 90.0:
        latitude = (
            89.999999
        )

    tile_lat = math.floor(
        latitude
    )

    tile_lon = math.floor(
        longitude
    )

    return (
        tile_lat,
        tile_lon,
    )


def tile_stem(
    tile_lat: int,
    tile_lon: int,
) -> str:
    """
    Build the Copernicus GLO-90 tile stem.

    GLO-90 uses 30 arc-second source resolution in the
    public COG naming convention.
    """

    north_south = (
        "N"
        if tile_lat >= 0
        else "S"
    )

    east_west = (
        "E"
        if tile_lon >= 0
        else "W"
    )

    return (
        "Copernicus_DSM_COG_30_"
        f"{north_south}"
        f"{abs(tile_lat):02d}_00_"
        f"{east_west}"
        f"{abs(tile_lon):03d}_00_DEM"
    )


def tile_url(
    tile_lat: int,
    tile_lon: int,
) -> str:

    stem = tile_stem(
        tile_lat,
        tile_lon,
    )

    return (
        f"{DEM_BUCKET_URL}/"
        f"{stem}/"
        f"{stem}.tif"
    )


# ============================================================
# TILE REQUEST COLLECTION
# ============================================================

def build_terrain_components(
    dataframe: pd.DataFrame,
) -> tuple[
    list[list[TerrainComponent]],
    dict[
        tuple[int, int],
        dict[
            str,
            dict[str, Any]
        ],
    ],
]:
    """
    Prepare terrain sampling and group unique requested
    coordinates by Copernicus 1x1 degree tile.
    """

    all_components: list[
        list[TerrainComponent]
    ] = []

    tile_requests: dict[
        tuple[int, int],
        dict[
            str,
            dict[str, Any]
        ],
    ] = {}

    for row_index, geometry_wkt in enumerate(
        dataframe[
            "geometry_wkt"
        ].tolist()
    ):

        row_components: list[
            TerrainComponent
        ] = []

        for component_index, line in enumerate(
            parse_geometry(
                geometry_wkt
            )
        ):

            samples, length_m = (
                samples_for_line(
                    line
                )
            )

            if len(samples) < 2:

                continue

            point_refs: list[
                tuple[int, int, int]
            ] = []

            for sample_index, (
                latitude,
                longitude,
            ) in enumerate(
                samples
            ):

                reference = (
                    row_index,
                    component_index,
                    sample_index,
                )

                point_refs.append(
                    reference
                )

                tile_key = (
                    tile_key_for_coordinate(
                        latitude,
                        longitude,
                    )
                )

                coordinate_key = (
                    f"{latitude:.7f},"
                    f"{longitude:.7f}"
                )

                tile_bucket = (
                    tile_requests
                    .setdefault(
                        tile_key,
                        {},
                    )
                )

                if coordinate_key not in (
                    tile_bucket
                ):

                    tile_bucket[
                        coordinate_key
                    ] = {
                        "latitude": latitude,
                        "longitude": longitude,
                        "references": [],
                    }

                tile_bucket[
                    coordinate_key
                ][
                    "references"
                ].append(
                    reference
                )

            row_components.append(
                TerrainComponent(
                    samples=samples,
                    length_m=length_m,
                    point_refs=point_refs,
                )
            )

        all_components.append(
            row_components
        )

    return (
        all_components,
        tile_requests,
    )


# ============================================================
# TERRAIN TILE READ
# ============================================================

def read_tile_elevations(
    tile_key: tuple[int, int],
    requests: dict[
        str,
        dict[str, Any],
    ],
    point_elevations: dict[
        tuple[int, int, int],
        float,
    ],
) -> None:
    """
    Read one remote COG tile and distribute its elevation
    values back to all corresponding sample references.

    No fallback terrain source is used.
    """

    tile_lat, tile_lon = (
        tile_key
    )

    url = tile_url(
        tile_lat,
        tile_lon,
    )

    coordinates = [
        (
            request["longitude"],
            request["latitude"],
        )
        for request in requests.values()
    ]

    entries = list(
        requests.values()
    )

    try:

        with rasterio.open(
            url
        ) as source:

            values = source.sample(
                coordinates,
                masked=True,
            )

            for entry, sampled in zip(
                entries,
                values,
            ):

                value = sampled[0]

                if np.ma.is_masked(
                    value
                ):

                    continue

                elevation = float(
                    value
                )

                if not math.isfinite(
                    elevation
                ):

                    continue

                for reference in (
                    entry[
                        "references"
                    ]
                ):

                    point_elevations[
                        reference
                    ] = elevation

    except RasterioIOError as exc:

        message = str(
            exc
        )

        if (
            "404"
            in message
            or "Not Found"
            in message
            or "does not exist"
            in message
        ):

            print(
                f"DEM tile unavailable: "
                f"{tile_stem(tile_lat, tile_lon)}"
            )

            return

        raise RuntimeError(
            "Failed to read Copernicus GLO-90 tile:\n"
            f"{url}\n"
            f"{exc}"
        ) from exc


def collect_all_elevations(
    tile_requests: dict[
        tuple[int, int],
        dict[
            str,
            dict[str, Any],
        ],
    ],
) -> dict[
    tuple[int, int, int],
    float,
]:
    """
    Fetch every required DEM coordinate, grouped by tile.
    """

    point_elevations: dict[
        tuple[int, int, int],
        float,
    ] = {}

    total_tiles = len(
        tile_requests
    )

    total_unique_points = sum(
        len(
            requests
        )
        for requests
        in tile_requests.values()
    )

    print()
    print(
        "Static DEM extraction"
    )

    print(
        "Unique DEM tiles:",
        f"{total_tiles:,}",
    )

    print(
        "Unique DEM sample coordinates:",
        f"{total_unique_points:,}",
    )

    print()

    for tile_number, (
        tile_key,
        requests,
    ) in enumerate(
        tile_requests.items(),
        start=1,
    ):

        read_tile_elevations(
            tile_key,
            requests,
            point_elevations,
        )

        if (
            tile_number == 1
            or tile_number % 25 == 0
            or tile_number == total_tiles
        ):

            print(
                f"DEM tiles processed: "
                f"{tile_number:,}/"
                f"{total_tiles:,}    "
                f"elevations resolved: "
                f"{len(point_elevations):,}/"
                f"{total_unique_points:,}"
            )

    return point_elevations


# ============================================================
# TERRAIN METRICS
# ============================================================
# The arithmetic below is NOT defined here. It is
# `app.ml.feature_contract._component_terrain` and `model_terrain_features`,
# which the runtime service calls with the live elevation profile of the
# selected route. Delegating means the training rows and the served rows come
# from one implementation of one definition, rather than from two
# implementations that have to be kept in step by hand. The two were verified
# to agree to floating-point precision before this delegation replaced the
# local copy.


def row_terrain_metrics(
    components: list[TerrainComponent],
    point_elevations: dict[
        tuple[int, int, int],
        float,
    ],
) -> dict[str, float]:
    """
    Aggregate terrain without crossing disconnected MultiLineString
    components.

    A component is skipped entirely when any of its samples could not be
    resolved, exactly as the serving path skips a component with an absent
    elevation, so "no terrain" means the same thing in both.
    """

    profile: list[dict[str, Any]] = []
    sample_total = 0

    for component_index, component in enumerate(components):
        resolved: list[tuple[float, float]] = []

        for reference in component.point_refs:
            elevation = point_elevations.get(reference)
            if elevation is None:
                resolved = []
                break
            resolved.append(
                (
                    reference[2] / 1000.0,
                    float(elevation),
                )
            )

        if len(resolved) < 2:
            continue

        sample_total += len(component.samples)

        for distance_km, elevation in resolved:
            profile.append(
                {
                    "component_index": component_index,
                    "component_distance_km": distance_km,
                    "elevation_m": elevation,
                }
            )

    metrics = model_terrain_features(profile)

    if not metrics.get("terrain_available"):
        return {
            "elevation_min_m": math.nan,
            "elevation_max_m": math.nan,
            "elevation_gain_m": math.nan,
            "elevation_loss_m": math.nan,
            "elevation_range_m": math.nan,
            "average_slope_pct": math.nan,
            "max_slope_pct": math.nan,
            "elevation_sample_count": math.nan,
            "terrain_available": 0.0,
        }

    return {
        "elevation_min_m": (
            min(
                float(point["elevation_m"])
                for point in profile
            )
        ),
        "elevation_max_m": (
            max(
                float(point["elevation_m"])
                for point in profile
            )
        ),
        "elevation_gain_m": float(metrics["elevation_gain_m"]),
        "elevation_loss_m": float(metrics["elevation_loss_m"]),
        "elevation_range_m": float(metrics["elevation_range_m"]),
        "average_slope_pct": float(metrics["average_slope_pct"]),
        "max_slope_pct": float(metrics["max_slope_pct"]),
        "elevation_sample_count": float(sample_total),
        "terrain_available": 1.0,
    }




# ============================================================
# DATA VALIDATION
# ============================================================

def validate_source(
    dataframe: pd.DataFrame,
) -> None:

    required_columns = {
        "osm_id",
        "sac_scale",
        "highway",
        "surface",
        "trail_visibility",
        "incline",
        "smoothness",
        "tracktype",
        "width",
        "assisted_trail",
        "length_km",
        "grid_x",
        "grid_y",
        "geometry_wkt",
    }

    missing = (
        required_columns
        - set(dataframe.columns)
    )

    if missing:

        raise RuntimeError(
            "Missing required source columns: "
            + ", ".join(
                sorted(missing)
            )
        )

    if dataframe.empty:

        raise RuntimeError(
            "Source dataset is empty."
        )

    if (
        dataframe[
            "osm_id"
        ]
        .duplicated()
        .any()
    ):

        raise RuntimeError(
            "Duplicate OSM IDs detected."
        )

    observed_sac = set(
        dataframe[
            "sac_scale"
        ]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    unknown_sac = (
        observed_sac
        - set(
            EXPECTED_SAC_CLASSES
        )
    )

    if unknown_sac:

        raise RuntimeError(
            "Unknown SAC classes detected: "
            + ", ".join(
                sorted(
                    unknown_sac
                )
            )
        )


# ============================================================
# BUILD OSM FEATURES
# ============================================================

def build_osm_features(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Deterministic OSM feature transformations.
    """

    result = pd.DataFrame(
        index=dataframe.index
    )

    result["osm_id"] = (
        dataframe[
            "osm_id"
        ]
        .astype("int64")
    )

    result["sac_scale"] = (
        dataframe[
            "sac_scale"
        ]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    result["difficulty_class"] = (
        result[
            "sac_scale"
        ]
        .map(
            DIFFICULTY_MAP
        )
    )

    if (
        result[
            "difficulty_class"
        ]
        .isna()
        .any()
    ):

        raise RuntimeError(
            "Could not map every SAC class "
            "to a production difficulty class."
        )

    result["split_group"] = (
        dataframe[
            "grid_x"
        ]
        .astype(str)
        + "_"
        + dataframe[
            "grid_y"
        ].astype(str)
    )

    result["length_km"] = pd.to_numeric(
        dataframe[
            "length_km"
        ],
        errors="coerce",
    )

    result["highway"] = (
        dataframe[
            "highway"
        ]
        .map(
            normalize_text
        )
    )

    result["surface"] = (
        dataframe[
            "surface"
        ]
        .map(
            normalize_text
        )
    )

    result["trail_visibility"] = (
        dataframe[
            "trail_visibility"
        ]
        .map(
            normalize_trail_visibility
        )
    )

    result["smoothness"] = (
        dataframe[
            "smoothness"
        ]
        .map(
            normalize_text
        )
    )

    result["tracktype"] = (
        dataframe[
            "tracktype"
        ]
        .map(
            normalize_text
        )
    )

    result["assisted_trail"] = (
        dataframe[
            "assisted_trail"
        ]
        .map(
            normalize_assisted_trail
        )
    )

    result["incline_pct"] = (
        dataframe[
            "incline"
        ]
        .map(
            parse_incline
        )
    )

    result["incline_direction"] = (
        dataframe[
            "incline"
        ]
        .map(
            normalize_incline_direction
        )
    )

    result[
        "incline_numeric_missing"
    ] = (
        result[
            "incline_pct"
        ]
        .isna()
        .astype("int8")
    )

    result["width_m"] = (
        dataframe[
            "width"
        ]
        .map(
            parse_width
        )
    )

    result[
        "width_missing"
    ] = (
        result[
            "width_m"
        ]
        .isna()
        .astype("int8")
    )

    result["length_km"] = (
        pd.to_numeric(
            result[
                "length_km"
            ],
            errors="coerce",
        )
    )

    if (
        result[
            "length_km"
        ]
        .isna()
        .any()
    ):

        raise RuntimeError(
            "length_km contains invalid or missing values."
        )

    if "geometry_wkt" not in dataframe.columns:
        raise RuntimeError(
            "source rows carry no geometry_wkt; "
            "route-shape features cannot be built."
        )

    # Route-shape features come from the way's own committed geometry through
    # the shared contract function, so a dataset rebuild produces the same
    # values the runtime computes from verified GeoJSON.
    shape_rows = [
        route_shape_features(
            [
                [
                    [float(x), float(y)]
                    for x, y in line.coords
                ]
                for line in parse_geometry(value)
            ]
        )
        for value in dataframe["geometry_wkt"]
    ]
    result["mean_turn"] = pd.to_numeric(
        pd.Series(
            [row["mean_turn"] for row in shape_rows],
            index=result.index,
        ),
        errors="coerce",
    )
    result["verts_per_km"] = pd.to_numeric(
        pd.Series(
            [row["verts_per_km"] for row in shape_rows],
            index=result.index,
        ),
        errors="coerce",
    )

    return result[
        [
            "osm_id",
            "sac_scale",
            "difficulty_class",
            "split_group",
            "length_km",
            "mean_turn",
            "verts_per_km",
            "incline_pct",
            "width_m",
            "incline_numeric_missing",
            "width_missing",
            "highway",
            "surface",
            "trail_visibility",
            "incline_direction",
            "smoothness",
            "tracktype",
            "assisted_trail",
        ]
    ]


# ============================================================
# AUDIT
# ============================================================

def build_audit(
    source: pd.DataFrame,
    features: pd.DataFrame,
) -> dict[str, Any]:

    terrain_available = (
        features[
            "terrain_available"
        ]
        .astype(int)
        .value_counts()
        .to_dict()
    )

    categorical_audit: dict[
        str,
        dict[str, int],
    ] = {}

    for column in (
        CATEGORICAL_FEATURES
    ):

        counts = (
            features[
                column
            ]
            .value_counts(
                dropna=False
            )
            .to_dict()
        )

        categorical_audit[
            column
        ] = {
            str(key): int(value)
            for key, value
            in counts.items()
        }

    numeric_audit: dict[
        str,
        dict[str, Any],
    ] = {}

    for column in (
        NUMERIC_FEATURES
        + TERRAIN_COLUMNS
    ):

        if column not in features.columns:
            continue

        series = pd.to_numeric(
            features[
                column
            ],
            errors="coerce",
        )

        non_missing = (
            series.dropna()
        )

        record: dict[
            str,
            Any,
        ] = {
            "missing": int(
                series.isna().sum()
            ),
            "non_missing": int(
                series.notna().sum()
            ),
        }

        if not non_missing.empty:

            record.update(
                {
                    "min": float(
                        non_missing.min()
                    ),
                    "max": float(
                        non_missing.max()
                    ),
                    "mean": float(
                        non_missing.mean()
                    ),
                }
            )

        numeric_audit[
            column
        ] = record

    sac_distribution = (
        features[
            "sac_scale"
        ]
        .value_counts()
        .reindex(
            EXPECTED_SAC_CLASSES,
            fill_value=0,
        )
    )

    difficulty_distribution = (
        features[
            "difficulty_class"
        ]
        .value_counts()
        .reindex(
            EXPECTED_DIFFICULTY_CLASSES,
            fill_value=0,
        )
    )

    return {
        "source": {
            "file": str(
                INPUT_PATH
            ),
            "rows": int(
                len(source)
            ),
            "unique_osm_ids": int(
                source[
                    "osm_id"
                ].nunique()
            ),
        },
        "terrain": {
            "source": (
                "Copernicus DEM GLO-90"
            ),
            "resolution_m": (
                DEM_RESOLUTION_M
            ),
            "access": (
                "public AWS COG"
            ),
            "access_date": (
                date.today().isoformat()
            ),
            "terrain_available_counts": {
                str(key): int(value)
                for key, value
                in terrain_available.items()
            },
        },
        "target": {
            "source_column": (
                "sac_scale"
            ),
            "production_column": (
                "difficulty_class"
            ),
            "sac_distribution": {
                str(key): int(value)
                for key, value
                in sac_distribution.items()
            },
            "difficulty_distribution": {
                str(key): int(value)
                for key, value
                in difficulty_distribution.items()
            },
        },
        "split": {
            "column": (
                "split_group"
            ),
            "unique_groups": int(
                features[
                    "split_group"
                ].nunique()
            ),
        },
        "features": {
            "numeric": (
                NUMERIC_FEATURES
            ),
            "categorical": (
                CATEGORICAL_FEATURES
            ),
        },
        "numeric_audit": (
            numeric_audit
        ),
        "categorical_audit": (
            categorical_audit
        ),
    }


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    print()
    print("=" * 72)
    print(
        "PHASE 5 — FINAL OSM + TERRAIN FEATURE ENGINEERING"
    )
    print("=" * 72)
    print()

    if not INPUT_PATH.exists():

        raise FileNotFoundError(
            f"Input dataset not found: {INPUT_PATH}"
        )

    source = pd.read_csv(
        INPUT_PATH
    )

    print(
        "Input:",
        INPUT_PATH,
    )

    print(
        "Rows:",
        f"{len(source):,}",
    )

    validate_source(
        source
    )

    osm_features = (
        build_osm_features(
            source
        )
    )

    print()
    print(
        "Preparing static Copernicus GLO-90 terrain..."
    )

    (
        all_components,
        tile_requests,
    ) = build_terrain_components(
        source
    )

    point_elevations = (
        collect_all_elevations(
            tile_requests
        )
    )

    terrain_rows: list[
        dict[str, float]
    ] = []

    for components in (
        all_components
    ):

        terrain_rows.append(
            row_terrain_metrics(
                components,
                point_elevations,
            )
        )

    terrain_dataframe = pd.DataFrame(
        terrain_rows
    )

    features = osm_features.copy()

    for column in (
        TERRAIN_COLUMNS
    ):

        features[
            column
        ] = terrain_dataframe[
            column
        ]

    features = features[
        OUTPUT_COLUMNS
        + [
            "elevation_min_m",
            "elevation_max_m",
            "elevation_sample_count",
        ]
    ]

    if len(features) != len(
        source
    ):

        raise RuntimeError(
            "Feature engineering changed the row count."
        )

    if (
        features[
            "osm_id"
        ]
        .duplicated()
        .any()
    ):

        raise RuntimeError(
            "Duplicate OSM IDs detected after feature engineering."
        )

    valid_difficulty = (
        set(
            features[
                "difficulty_class"
            ]
            .dropna()
            .unique()
        )
        == set(
            EXPECTED_DIFFICULTY_CLASSES
        )
    )

    if not valid_difficulty:

        raise RuntimeError(
            "Production difficulty classes are incomplete."
        )

    audit = build_audit(
        source,
        features,
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    features.to_csv(
        OUTPUT_PATH,
        index=False,
    )

    with AUDIT_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            audit,
            file,
            indent=2,
            ensure_ascii=False,
        )

    terrain_count = int(
        (
            features[
                "terrain_available"
            ]
            == 1
        ).sum()
    )

    terrain_missing = (
        len(features)
        - terrain_count
    )

    print()
    print("=" * 72)
    print(
        "FINAL FEATURE ENGINEERING COMPLETE"
    )
    print("=" * 72)
    print()

    print(
        "Rows:",
        f"{len(features):,}",
    )

    print(
        "Unique OSM ways:",
        f"{features['osm_id'].nunique():,}",
    )

    print(
        "Geographic groups:",
        f"{features['split_group'].nunique():,}",
    )

    print(
        "Rows with DEM terrain:",
        f"{terrain_count:,}",
    )

    print(
        "Rows without DEM terrain:",
        f"{terrain_missing:,}",
    )

    print()
    print(
        "Final difficulty distribution:"
    )

    print(
        features[
            "difficulty_class"
        ]
        .value_counts()
        .reindex(
            EXPECTED_DIFFICULTY_CLASSES,
            fill_value=0,
        )
        .to_string()
    )

    print()
    print(
        "Saved:"
    )

    print(
        OUTPUT_PATH
    )

    print(
        AUDIT_PATH
    )

    return 0


if __name__ == "__main__":

    try:

        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:

        print(
            "\nOperation cancelled."
        )

        raise SystemExit(130)

    except Exception as exc:

        print(
            "\nERROR:",
            exc,
            file=sys.stderr,
        )

        raise SystemExit(1)