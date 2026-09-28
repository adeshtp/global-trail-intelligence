"""
Phase 5 — Real OSM difficulty dataset builder.

Pipeline:

    Postpass / OpenStreetMap
        ↓
    OSM ways only
        ↓
    canonical sac_scale
        ↓
    real OSM attributes
        ↓
    real OSM geometry
        ↓
    deterministic geographic sampling
        ↓
    real CSV dataset

Important:
    - No synthetic data.
    - No manually entered trails.
    - sac_scale is the TARGET.
    - sac_scale is never an input feature.
    - Only osm_type='W' objects are used.
    - Relations are excluded.
    - Geometry is preserved as WKT.
    - Geographic fields are retained for later spatial validation.
    - Current weather is NOT used for static difficulty modelling.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pandas as pd


# ============================================================
# PROJECT PATHS
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parents[2]

PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = (
    PROJECT_ROOT
    / "data"
    / "difficulty"
)

AUDIT_PATH = (
    DATA_DIR
    / "difficulty_audit.json"
)

DATASET_PATH = (
    DATA_DIR
    / "difficulty_ways.csv"
)


# ============================================================
# POSTPASS
# ============================================================

POSTPASS_URL = (
    "https://postpass.geofabrik.de/api/interpreter"
)

REQUEST_TIMEOUT_SECONDS = 240.0


# ============================================================
# TARGET
# ============================================================

SAC_SCALE_CLASSES = [
    "strolling",
    "hiking",
    "mountain_hiking",
    "demanding_mountain_hiking",
    "alpine_hiking",
    "demanding_alpine_hiking",
    "difficult_alpine_hiking",
]


# ============================================================
# INPUT FEATURES
# ============================================================

# sac_scale is intentionally NOT included.
#
# It is the supervised target.
RAW_FEATURE_COLUMNS = [
    "highway",
    "surface",
    "trail_visibility",
    "incline",
    "smoothness",
    "tracktype",
    "width",
    "assisted_trail",
]


# ============================================================
# DATASET COLUMNS
# ============================================================

OUTPUT_COLUMNS = [
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

    "length_m",
    "length_km",

    "latitude",
    "longitude",

    "grid_x",
    "grid_y",

    "geometry_wkt",
]


# ============================================================
# SQL HELPERS
# ============================================================

def sql_class_list() -> str:
    """
    Convert the canonical SAC classes into a PostgreSQL
    IN-list.
    """

    escaped = [
        "'"
        + value.replace("'", "''")
        + "'"
        for value in SAC_SCALE_CLASSES
    ]

    return ", ".join(escaped)


# ============================================================
# AUDIT SQL
# ============================================================

def build_audit_sql() -> str:
    """
    Audit geometry-backed OSM ways.

    postpass_line contains both:
        W = way
        R = relation

    We explicitly restrict the audit to W.
    """

    classes_sql = sql_class_list()

    return f"""
SELECT
    tags->>'sac_scale' AS sac_scale,

    COUNT(*) AS total_ways,

    COUNT(*) FILTER (
        WHERE tags ? 'highway'
    ) AS highway_count,

    COUNT(*) FILTER (
        WHERE tags ? 'surface'
    ) AS surface_count,

    COUNT(*) FILTER (
        WHERE tags ? 'trail_visibility'
    ) AS trail_visibility_count,

    COUNT(*) FILTER (
        WHERE tags ? 'incline'
    ) AS incline_count,

    COUNT(*) FILTER (
        WHERE tags ? 'smoothness'
    ) AS smoothness_count,

    COUNT(*) FILTER (
        WHERE tags ? 'tracktype'
    ) AS tracktype_count,

    COUNT(*) FILTER (
        WHERE tags ? 'width'
    ) AS width_count,

    COUNT(*) FILTER (
        WHERE tags ? 'assisted_trail'
    ) AS assisted_trail_count,

    COUNT(*) FILTER (
        WHERE geom IS NOT NULL
    ) AS geometry_count

FROM postpass_line

WHERE osm_type = 'W'

  AND tags->>'sac_scale' IN (
      {classes_sql}
  )

GROUP BY
    tags->>'sac_scale'

ORDER BY
    CASE tags->>'sac_scale'
        WHEN 'strolling' THEN 1
        WHEN 'hiking' THEN 2
        WHEN 'mountain_hiking' THEN 3
        WHEN 'demanding_mountain_hiking' THEN 4
        WHEN 'alpine_hiking' THEN 5
        WHEN 'demanding_alpine_hiking' THEN 6
        WHEN 'difficult_alpine_hiking' THEN 7
        ELSE 99
    END
"""


# ============================================================
# SAMPLE SQL
# ============================================================

def build_sample_sql(
    per_class_limit: int,
    grid_size_degrees: float,
    max_rows_per_class_cell: int,
) -> str:
    """
    Build a deterministic geographically balanced sample.

    Steps:

        1. Keep OSM ways only.
        2. Keep canonical sac_scale values.
        3. Require real geometry.
        4. Calculate a geographic centroid.
        5. Assign a geographic grid cell.
        6. Limit observations inside each
           class/grid-cell combination.
        7. Use deterministic hashing for selection.
        8. Apply the final per-class limit.
        9. Preserve geometry as WKT.

    Default:

        2,500 rows / class
        1 degree grid
        20 rows / class / grid cell

    Target size:

        7 × 2,500 = 17,500 rows
    """

    classes_sql = sql_class_list()

    return f"""
WITH eligible AS (
    SELECT
        osm_id,
        tags,
        geom,
        length_m,

        tags->>'sac_scale'
            AS sac_scale,

        ST_Centroid(geom)
            AS centroid

    FROM postpass_line

    WHERE osm_type = 'W'

      AND tags->>'sac_scale' IN (
          {classes_sql}
      )

      AND geom IS NOT NULL

      AND length_m IS NOT NULL

      AND length_m > 0
),

located AS (
    SELECT
        osm_id,
        tags,
        geom,
        length_m,
        sac_scale,
        centroid,

        FLOOR(
            ST_X(centroid)
            / {grid_size_degrees}
        )::BIGINT AS grid_x,

        FLOOR(
            ST_Y(centroid)
            / {grid_size_degrees}
        )::BIGINT AS grid_y

    FROM eligible
),

cell_ranked AS (
    SELECT
        osm_id,
        tags,
        geom,
        length_m,
        sac_scale,
        centroid,
        grid_x,
        grid_y,

        ROW_NUMBER() OVER (
            PARTITION BY
                sac_scale,
                grid_x,
                grid_y

            ORDER BY
                md5(
                    osm_id::TEXT
                )
        ) AS cell_rank

    FROM located
),

geographically_balanced AS (
    SELECT
        osm_id,
        tags,
        geom,
        length_m,
        sac_scale,
        centroid,
        grid_x,
        grid_y

    FROM cell_ranked

    WHERE cell_rank <= {
        max_rows_per_class_cell
    }
),

class_ranked AS (
    SELECT
        osm_id,
        tags,
        geom,
        length_m,
        sac_scale,
        centroid,
        grid_x,
        grid_y,

        ROW_NUMBER() OVER (
            PARTITION BY sac_scale

            ORDER BY
                md5(
                    osm_id::TEXT
                )
        ) AS class_rank

    FROM geographically_balanced
)

SELECT
    osm_id,

    sac_scale,

    tags->>'highway'
        AS highway,

    tags->>'surface'
        AS surface,

    tags->>'trail_visibility'
        AS trail_visibility,

    tags->>'incline'
        AS incline,

    tags->>'smoothness'
        AS smoothness,

    tags->>'tracktype'
        AS tracktype,

    tags->>'width'
        AS width,

    tags->>'assisted_trail'
        AS assisted_trail,

    length_m,

    (
        length_m / 1000.0
    )::double precision
        AS length_km,

    ST_Y(centroid)::double precision
        AS latitude,

    ST_X(centroid)::double precision
        AS longitude,

    grid_x,

    grid_y,

    ST_AsText(geom)
        AS geometry_wkt

FROM class_ranked

WHERE class_rank <= {
    per_class_limit
}

ORDER BY
    CASE sac_scale
        WHEN 'strolling' THEN 1
        WHEN 'hiking' THEN 2
        WHEN 'mountain_hiking' THEN 3
        WHEN 'demanding_mountain_hiking' THEN 4
        WHEN 'alpine_hiking' THEN 5
        WHEN 'demanding_alpine_hiking' THEN 6
        WHEN 'difficult_alpine_hiking' THEN 7
        ELSE 99
    END,

    class_rank
"""


# ============================================================
# POSTPASS REQUEST
# ============================================================

def postpass_query(
    sql: str,
) -> list[dict[str, Any]]:
    """
    Execute one read-only SQL request.
    """

    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:

            response = client.post(
                POSTPASS_URL,
                data={
                    "options[geojson]": "false",
                    "data": sql,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                },
            )

            response.raise_for_status()

    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Postpass request failed: {exc}"
        ) from exc

    try:
        payload = response.json()

    except ValueError as exc:
        raise RuntimeError(
            "Postpass returned a non-JSON response."
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "Unexpected Postpass response format."
        )

    if "error" in payload:
        raise RuntimeError(
            "Postpass SQL error:\n"
            + str(payload["error"])
        )

    result = payload.get(
        "result"
    )

    if result is None:
        raise RuntimeError(
            "Postpass response does not contain 'result'."
        )

    if not isinstance(
        result,
        list,
    ):
        raise RuntimeError(
            "Postpass 'result' is not a list."
        )

    return result


# ============================================================
# AUDIT
# ============================================================

def run_audit() -> pd.DataFrame:
    """
    Run the live audit.
    """

    print()
    print("=" * 72)
    print(
        "PHASE 5 — LIVE OSM DIFFICULTY DATA AUDIT"
    )
    print("=" * 72)
    print()

    print("Source:")
    print(POSTPASS_URL)
    print()

    print(
        "Geometry table: postpass_line"
    )

    print(
        "Object type: OSM ways only (osm_type='W')"
    )

    print()

    rows = postpass_query(
        build_audit_sql()
    )

    if not rows:
        raise RuntimeError(
            "The audit returned zero rows."
        )

    audit = pd.DataFrame(
        rows
    )

    numeric_columns = [
        column
        for column in audit.columns
        if column != "sac_scale"
    ]

    for column in numeric_columns:
        audit[column] = pd.to_numeric(
            audit[column],
            errors="coerce",
        )

    for feature in RAW_FEATURE_COLUMNS:

        count_column = (
            feature
            + "_count"
        )

        coverage_column = (
            feature
            + "_coverage_pct"
        )

        if (
            count_column
            in audit.columns
        ):

            audit[
                coverage_column
            ] = (
                audit[count_column]
                / audit["total_ways"]
                * 100.0
            )

    expected = set(
        SAC_SCALE_CLASSES
    )

    observed = set(
        audit["sac_scale"]
        .dropna()
        .astype(str)
        .tolist()
    )

    missing = (
        expected
        - observed
    )

    total_labelled = int(
        audit["total_ways"].sum()
    )

    total_geometry = int(
        audit["geometry_count"].sum()
    )

    print(
        audit[
            [
                "sac_scale",
                "total_ways",
                "highway_count",
                "surface_count",
                "trail_visibility_count",
                "incline_count",
                "smoothness_count",
                "tracktype_count",
                "width_count",
                "assisted_trail_count",
                "geometry_count",
            ]
        ].to_string(
            index=False
        )
    )

    print()

    print(
        "Canonical classes expected:",
        len(SAC_SCALE_CLASSES),
    )

    print(
        "Canonical classes observed:",
        len(observed),
    )

    print(
        "Missing classes:",
        sorted(missing),
    )

    print()

    print(
        "Total canonical labelled OSM ways:",
        f"{total_labelled:,}",
    )

    print(
        "Ways with geometry:",
        f"{total_geometry:,}",
    )

    if missing:
        raise RuntimeError(
            "One or more canonical sac_scale "
            "classes are missing."
        )

    if total_labelled == 0:
        raise RuntimeError(
            "No canonical labelled ways found."
        )

    if total_geometry == 0:
        raise RuntimeError(
            "No geometry-backed labelled ways found."
        )

    return audit


# ============================================================
# DATASET VALIDATION
# ============================================================

def validate_dataset(
    dataframe: pd.DataFrame,
) -> None:
    """
    Validate the sampled real dataset.
    """

    missing_columns = (
        set(OUTPUT_COLUMNS)
        - set(dataframe.columns)
    )

    if missing_columns:
        raise RuntimeError(
            "Dataset is missing required columns: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    if dataframe.empty:
        raise RuntimeError(
            "The sampled dataset is empty."
        )

    invalid_labels = (
        set(
            dataframe["sac_scale"]
            .dropna()
            .astype(str)
        )
        - set(SAC_SCALE_CLASSES)
    )

    if invalid_labels:
        raise RuntimeError(
            "Unexpected difficulty labels: "
            + ", ".join(
                sorted(invalid_labels)
            )
        )

    duplicate_count = int(
        dataframe["osm_id"]
        .duplicated()
        .sum()
    )

    if duplicate_count:
        raise RuntimeError(
            f"Dataset contains {duplicate_count} "
            "duplicate OSM ways."
        )

    required_numeric = [
        "osm_id",
        "length_m",
        "length_km",
        "latitude",
        "longitude",
        "grid_x",
        "grid_y",
    ]

    for column in required_numeric:

        if dataframe[column].isna().any():

            raise RuntimeError(
                f"Dataset contains missing values "
                f"in {column}."
            )

    if (
        dataframe["length_m"]
        <= 0
    ).any():

        raise RuntimeError(
            "Dataset contains zero/negative "
            "length values."
        )

    if (
        dataframe["length_km"]
        <= 0
    ).any():

        raise RuntimeError(
            "Dataset contains zero/negative "
            "length_km values."
        )

    if (
        dataframe["geometry_wkt"]
        .isna()
        .any()
    ):

        raise RuntimeError(
            "Dataset contains missing geometry."
        )

    empty_geometry = (
        dataframe["geometry_wkt"]
        .astype(str)
        .str.strip()
        .eq("")
    )

    if empty_geometry.any():

        raise RuntimeError(
            "Dataset contains empty geometry."
        )


# ============================================================
# BUILD DATASET
# ============================================================

def build_dataset(
    per_class_limit: int,
    grid_size_degrees: float,
    max_rows_per_class_cell: int,
) -> pd.DataFrame:
    """
    Build the real OSM sample.
    """

    print()
    print("=" * 72)
    print(
        "BUILDING REAL OSM DIFFICULTY DATASET"
    )
    print("=" * 72)
    print()

    print(
        "Maximum rows per class:",
        f"{per_class_limit:,}",
    )

    print(
        "Geographic grid:",
        f"{grid_size_degrees} degrees",
    )

    print(
        "Maximum rows per class/grid cell:",
        f"{max_rows_per_class_cell:,}",
    )

    print()

    rows = postpass_query(
        build_sample_sql(
            per_class_limit=(
                per_class_limit
            ),
            grid_size_degrees=(
                grid_size_degrees
            ),
            max_rows_per_class_cell=(
                max_rows_per_class_cell
            ),
        )
    )

    if not rows:
        raise RuntimeError(
            "Sampling query returned zero rows."
        )

    dataframe = pd.DataFrame(
        rows
    )

    dataframe = dataframe.reindex(
        columns=OUTPUT_COLUMNS
    )

    # --------------------------------------------------------
    # Numeric fields
    # --------------------------------------------------------

    numeric_columns = [
        "osm_id",
        "length_m",
        "length_km",
        "latitude",
        "longitude",
        "grid_x",
        "grid_y",
    ]

    for column in numeric_columns:

        dataframe[column] = pd.to_numeric(
            dataframe[column],
            errors="coerce",
        )

    # --------------------------------------------------------
    # Integer-like fields
    # --------------------------------------------------------

    dataframe["osm_id"] = (
        dataframe["osm_id"]
        .astype("Int64")
    )

    dataframe["grid_x"] = (
        dataframe["grid_x"]
        .astype("Int64")
    )

    dataframe["grid_y"] = (
        dataframe["grid_y"]
        .astype("Int64")
    )

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    validate_dataset(
        dataframe
    )

    return dataframe


# ============================================================
# SAVE AUDIT
# ============================================================

def save_audit(
    audit: pd.DataFrame,
    dataframe: pd.DataFrame | None,
    parameters: dict[str, Any],
) -> None:
    """
    Save audit and dataset metadata.
    """

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    audit_records = (
        audit
        .where(
            pd.notna(audit),
            None,
        )
        .to_dict(
            orient="records"
        )
    )

    payload: dict[str, Any] = {

        "source": POSTPASS_URL,

        "geometry_table": (
            "postpass_line"
        ),

        "osm_object_type": "W",

        "target": "sac_scale",

        "input_features": (
            RAW_FEATURE_COLUMNS
        ),

        "geometry_preserved_as": (
            "WKT"
        ),

        "canonical_classes": (
            SAC_SCALE_CLASSES
        ),

        "sampling_parameters": (
            parameters
        ),

        "audit": audit_records,
    }

    if dataframe is not None:

        class_counts = (
            dataframe["sac_scale"]
            .value_counts()
            .reindex(
                SAC_SCALE_CLASSES,
                fill_value=0,
            )
            .astype(int)
            .to_dict()
        )

        unique_cells = int(
            dataframe[
                [
                    "grid_x",
                    "grid_y",
                ]
            ]
            .drop_duplicates()
            .shape[0]
        )

        payload[
            "sampled_dataset"
        ] = {

            "rows": int(
                len(dataframe)
            ),

            "unique_osm_ways": int(
                dataframe["osm_id"]
                .nunique()
            ),

            "unique_spatial_cells": (
                unique_cells
            ),

            "class_counts": (
                class_counts
            ),
        }

    with AUDIT_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
            default=str,
        )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Audit and build a real OSM "
            "sac_scale difficulty dataset."
        )
    )

    parser.add_argument(
        "--audit-only",
        action="store_true",
        help=(
            "Run only the live audit."
        ),
    )

    parser.add_argument(
        "--sample-only",
        action="store_true",
        help=(
            "Build the dataset without "
            "running the audit again."
        ),
    )

    parser.add_argument(
        "--per-class-limit",
        type=int,
        default=2500,
        help=(
            "Maximum sampled ways per "
            "difficulty class."
        ),
    )

    parser.add_argument(
        "--grid-size-degrees",
        type=float,
        default=1.0,
        help=(
            "Geographic grid size in degrees."
        ),
    )

    parser.add_argument(
        "--max-rows-per-class-cell",
        type=int,
        default=20,
        help=(
            "Maximum rows retained per "
            "class/grid cell."
        ),
    )

    args = parser.parse_args()

    if args.audit_only and args.sample_only:

        raise ValueError(
            "--audit-only and --sample-only "
            "cannot be used together."
        )

    if (
        args.per_class_limit
        <= 0
    ):

        raise ValueError(
            "--per-class-limit must be "
            "greater than zero."
        )

    if (
        args.grid_size_degrees
        <= 0
    ):

        raise ValueError(
            "--grid-size-degrees must be "
            "greater than zero."
        )

    if (
        args.max_rows_per_class_cell
        <= 0
    ):

        raise ValueError(
            "--max-rows-per-class-cell "
            "must be greater than zero."
        )

    parameters = {

        "per_class_limit": (
            args.per_class_limit
        ),

        "grid_size_degrees": (
            args.grid_size_degrees
        ),

        "max_rows_per_class_cell": (
            args.max_rows_per_class_cell
        ),
    }

    # --------------------------------------------------------
    # Audit mode
    # --------------------------------------------------------

    if args.audit_only:

        audit = run_audit()

        save_audit(
            audit=audit,
            dataframe=None,
            parameters=parameters,
        )

        print()
        print(
            "Audit saved to:"
        )
        print(
            AUDIT_PATH
        )

        print()
        print(
            "AUDIT COMPLETE."
        )

        return 0

    # --------------------------------------------------------
    # Sample-only mode
    # --------------------------------------------------------

    if args.sample_only:

        dataframe = build_dataset(
            per_class_limit=(
                args.per_class_limit
            ),

            grid_size_degrees=(
                args.grid_size_degrees
            ),

            max_rows_per_class_cell=(
                args.max_rows_per_class_cell
            ),
        )

        dataframe.to_csv(
            DATASET_PATH,
            index=False,
        )

        # Existing audit is retained.
        # Update it with the newly created
        # dataset summary if it exists.
        if AUDIT_PATH.exists():

            try:

                with AUDIT_PATH.open(
                    "r",
                    encoding="utf-8",
                ) as file:

                    existing = json.load(
                        file
                    )

            except (
                OSError,
                json.JSONDecodeError,
            ):

                existing = {
                    "source": POSTPASS_URL,
                    "target": "sac_scale",
                }

        else:

            existing = {
                "source": POSTPASS_URL,
                "target": "sac_scale",
            }

        class_counts = (
            dataframe["sac_scale"]
            .value_counts()
            .reindex(
                SAC_SCALE_CLASSES,
                fill_value=0,
            )
            .astype(int)
            .to_dict()
        )

        existing[
            "sampled_dataset"
        ] = {

            "rows": int(
                len(dataframe)
            ),

            "unique_osm_ways": int(
                dataframe["osm_id"]
                .nunique()
            ),

            "unique_spatial_cells": int(
                dataframe[
                    [
                        "grid_x",
                        "grid_y",
                    ]
                ]
                .drop_duplicates()
                .shape[0]
            ),

            "class_counts": (
                class_counts
            ),

            "dataset_path": str(
                DATASET_PATH
            ),

            "geometry_column": (
                "geometry_wkt"
            ),
        }

        with AUDIT_PATH.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                existing,
                file,
                indent=2,
                ensure_ascii=False,
                default=str,
            )

        print()
        print("=" * 72)
        print(
            "DATASET CREATED"
        )
        print("=" * 72)
        print()

        print(
            "Rows:",
            f"{len(dataframe):,}",
        )

        print(
            "Unique OSM ways:",
            f"{dataframe['osm_id'].nunique():,}",
        )

        print(
            "Unique geographic cells:",
            f"{dataframe[['grid_x', 'grid_y']].drop_duplicates().shape[0]:,}",
        )

        print()
        print(
            "Class distribution:"
        )

        print(
            dataframe["sac_scale"]
            .value_counts()
            .reindex(
                SAC_SCALE_CLASSES,
                fill_value=0,
            )
            .to_string()
        )

        print()
        print(
            "Dataset saved to:"
        )

        print(
            DATASET_PATH
        )

        print()
        print(
            "Audit/metadata saved to:"
        )

        print(
            AUDIT_PATH
        )

        print()
        print(
            "DATASET BUILD COMPLETE."
        )

        return 0

    # --------------------------------------------------------
    # Default mode:
    # audit + dataset
    # --------------------------------------------------------

    audit = run_audit()

    dataframe = build_dataset(
        per_class_limit=(
            args.per_class_limit
        ),

        grid_size_degrees=(
            args.grid_size_degrees
        ),

        max_rows_per_class_cell=(
            args.max_rows_per_class_cell
        ),
    )

    dataframe.to_csv(
        DATASET_PATH,
        index=False,
    )

    save_audit(
        audit=audit,
        dataframe=dataframe,
        parameters=parameters,
    )

    print()
    print("=" * 72)
    print(
        "DATASET CREATED"
    )
    print("=" * 72)
    print()

    print(
        "Rows:",
        f"{len(dataframe):,}",
    )

    print(
        "Unique OSM ways:",
        f"{dataframe['osm_id'].nunique():,}",
    )

    print()
    print(
        "Class distribution:"
    )

    print(
        dataframe["sac_scale"]
        .value_counts()
        .reindex(
            SAC_SCALE_CLASSES,
            fill_value=0,
        )
        .to_string()
    )

    print()
    print(
        "Dataset saved to:"
    )

    print(
        DATASET_PATH
    )

    print()
    print(
        "Audit saved to:"
    )

    print(
        AUDIT_PATH
    )

    print()
    print(
        "DATASET BUILD COMPLETE."
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

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