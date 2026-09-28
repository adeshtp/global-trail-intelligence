"""
Natural-prevalence holdout collection.

WHY THIS EXISTS

The retained training dataset is deliberately class-capped: at most 2,500 ways
per grade and at most 20 per grade per one-degree cell. That is the right
instrument for comparing models, because every grade is learnable. It is not
the real world. In the real OpenStreetMap population roughly 57% of graded
ways are ``hiking`` and 1% are ``difficult_alpine_hiking``, and a model scored
on a balanced sample is not being scored on anything that resembles a user's
experience.

So this module draws a SECOND sample from the same geography, with no per-class
control at all: inside every held-out grid cell it takes a uniform random draw
of labelled ways. The class mix that comes out is whatever the real
distribution in that area happens to be. Those rows are scored once, after
model selection, and nothing is ever fitted on them.

The sample is drawn from the cells that are already held out of training, so
it cannot contain a training row's geography. It is deliberately capped per
cell so the query stays bounded and the draw remains a uniform sample of each
cell rather than a ranking.

WHAT IT IS NOT

* It is not a new label source. The grades are the same OpenStreetMap
  ``sac_scale`` values, read from the same Postpass endpoint the dataset
  builder used.
* It is not a second dataset. It is a test set, stored separately, and nothing
  in the training path reads it.
* It does not touch the runtime. The application loads exactly one artifact.

PROVENANCE

OpenStreetMap data, © OpenStreetMap contributors, available under the
Open Database License, read through the public Postpass SQL endpoint.

Run:

    PYTHONPATH=backend backend/.venv/bin/python -m app.ml.natural_holdout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data" / "difficulty"
WAYS_CSV = DATA_DIR / "difficulty_ways.csv"
FEATURE_CSV = DATA_DIR / "difficulty_features.csv"
OUTPUT_CSV = DATA_DIR / "difficulty_natural_holdout.csv"
AUDIT_JSON = DATA_DIR / "difficulty_natural_holdout_audit.json"

POSTPASS_URL = "https://postpass.geofabrik.de/api/interpreter"
# The scan behind this query is a full pass over the public way table, so the
# budget is generous. It is a single request; how many requests are made is
# what matters for courtesy, not the duration of the one request.
REQUEST_TIMEOUT_SECONDS = 900.0

DEFAULT_ROWS_PER_CELL = 20
MAX_REGIONS = 6

GRADES = (
    "strolling",
    "hiking",
    "mountain_hiking",
    "demanding_mountain_hiking",
    "alpine_hiking",
    "demanding_alpine_hiking",
    "difficult_alpine_hiking",
)

OUTPUT_COLUMNS = [
    "osm_id",
    "sac_scale",
    "difficulty_class",
    "split_group",
    "length_km",
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
    "highway",
    "surface",
    "trail_visibility",
    "incline_direction",
    "smoothness",
    "tracktype",
    "assisted_trail",
    "elevation_min_m",
    "elevation_max_m",
    "elevation_sample_count",
    "latitude",
    "longitude",
    "grid_x",
    "grid_y",
    "geometry_wkt",
]


def _sql_string(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_sql(
    regions: list[dict[str, Any]],
    rows_per_cell: int,
) -> str:
    """
    A single bounded read-only query.

    The envelope test is an index-friendly pre-filter; the exact cell list is
    applied afterwards so nothing outside the held-out cells can be selected.
    Per cell, ways are ordered by a hash of the OSM id and the first N are
    kept. Hash ordering is deterministic and unrelated to the grade, so the
    result is a uniform random sample of that cell rather than the cell's
    easiest or most heavily tagged ways.
    """
    envelopes = " OR ".join(
        "geom && ST_MakeEnvelope("
        f"{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}, 4326)"
        for bbox in (region["bbox"] for region in regions)
    )
    cell_list = ", ".join(
        f"({int(cell[0])}, {int(cell[1])})"
        for region in regions
        for cell in region["cells"]
    )
    grades = ", ".join(_sql_string(grade) for grade in GRADES)
    # A line has no single X/Y of its own in PostGIS, so the cell is taken
    # from the line's centroid, which is the same definition the dataset
    # builder used for its grid. The centroid is materialised once in a CTE
    # rather than recomputed per row, which is what keeps the query inside
    # the endpoint's time budget.
    return f"""
WITH located AS (
    SELECT
        osm_id,
        tags,
        geom,
        length_m,
        tags->>'sac_scale' AS sac_scale,
        ST_Centroid(geom) AS centroid
    FROM postpass_line
    WHERE osm_type = 'W'
      AND tags->>'sac_scale' IN ({grades})
      AND geom IS NOT NULL
      AND length_m IS NOT NULL
      AND length_m > 0
      AND ({envelopes})
)
SELECT
    osm_id,
    sac_scale,
    tags->>'highway' AS highway,
    tags->>'surface' AS surface,
    tags->>'trail_visibility' AS trail_visibility,
    tags->>'incline' AS incline,
    tags->>'smoothness' AS smoothness,
    tags->>'tracktype' AS tracktype,
    tags->>'width' AS width,
    tags->>'assisted_trail' AS assisted_trail,
    (length_m / 1000.0)::double precision AS length_km,
    ST_Y(centroid)::double precision AS latitude,
    ST_X(centroid)::double precision AS longitude,
    grid_x,
    grid_y,
    geometry_wkt
FROM (
    SELECT
        osm_id,
        tags,
        sac_scale,
        length_m,
        centroid,
        geom,
        FLOOR(ST_X(centroid) / 1.0)::BIGINT AS grid_x,
        FLOOR(ST_Y(centroid) / 1.0)::BIGINT AS grid_y,
        ST_AsText(geom) AS geometry_wkt,
        ROW_NUMBER() OVER (
            PARTITION BY
                FLOOR(ST_X(centroid) / 1.0)::BIGINT,
                FLOOR(ST_Y(centroid) / 1.0)::BIGINT
            ORDER BY md5(osm_id::TEXT)
        ) AS cell_rank
    FROM located
    WHERE (FLOOR(ST_X(centroid) / 1.0), FLOOR(ST_Y(centroid) / 1.0))
          IN ({cell_list})
) ranked
WHERE cell_rank <= {int(rows_per_cell)}
ORDER BY grid_x, grid_y, cell_rank
"""


def postpass_query(sql: str) -> list[dict[str, Any]]:
    """Execute one read-only SQL request against Postpass."""
    import httpx

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
        detail = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = f" | {response.text[:600]}"
            except Exception:  # noqa: BLE001
                detail = ""
        raise RuntimeError(
            f"Postpass request failed: {exc}{detail}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Postpass returned a non-JSON response."
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Postpass response format.")
    if "error" in payload:
        raise RuntimeError(
            f"Postpass SQL error:\n{payload['error']}"
        )
    result = payload.get("result")
    if not isinstance(result, list):
        raise RuntimeError(
            "Postpass response does not contain a result list."
        )
    return result


def cluster_cells(
    cells: list[tuple[int, int]],
    max_regions: int = 6,
) -> list[dict[str, Any]]:
    """
    Group the held-out cells into a few tight regional envelopes.

    The public endpoint times out on a query that has to touch every graded
    way on the planet. Adding a bounding-box pre-filter lets it use a spatial
    index instead, which is the difference between a sub-second response and a
    gateway timeout. Cells are clustered on four-neighbour adjacency so each
    envelope is small and the regions chosen are genuinely contiguous, not a
    worldwide bounding box that would select everything anyway.

    Only whole cells are kept, so the result is exactly the held-out
    geography and never reaches into training.
    """
    remaining = set(cells)
    clusters: list[list[tuple[int, int]]] = []
    while remaining:
        seed = min(remaining)
        remaining.discard(seed)
        stack = [seed]
        component = [seed]
        while stack:
            current = stack.pop()
            for delta in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                neighbour = (
                    current[0] + delta[0],
                    current[1] + delta[1],
                )
                if neighbour in remaining:
                    remaining.discard(neighbour)
                    component.append(neighbour)
                    stack.append(neighbour)
        clusters.append(sorted(component))

    clusters.sort(key=len, reverse=True)
    chosen = clusters[:max_regions]
    regions: list[dict[str, Any]] = []
    for component in chosen:
        longitudes = [cell[0] for cell in component]
        latitudes = [cell[1] for cell in component]
        regions.append(
            {
                "cells": component,
                "bbox": [
                    float(min(longitudes)),
                    float(min(latitudes)),
                    float(max(longitudes) + 1),
                    float(max(latitudes) + 1),
                ],
            }
        )
    return regions


def build_frame(
    rows: list[dict[str, Any]],
) -> pd.DataFrame:
    """
    Turn the raw rows into the same feature columns the dataset builder emits.

    The terrain columns are deliberately left absent. Sampling a DEM for this
    test set is a second, much larger collection step, and a partially
    populated test set would be worse than an honestly terrain-free one: the
    report states the coverage and compares this set against the
    terrain-free subset of the main holdout, so the two numbers are read
    together rather than conflated.
    """
    from app.ml.feature_contract import (
        normalise_categorical,
        parse_incline,
        parse_width,
    )
    from app.ml.feature_engineering import DIFFICULTY_MAP

    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            osm_id = int(row["osm_id"])
            length_km = float(row["length_km"])
        except (KeyError, TypeError, ValueError):
            continue
        grade = str(row.get("sac_scale") or "").strip()
        if grade not in GRADES or length_km <= 0:
            continue

        incline = parse_incline(row.get("incline"))
        width = parse_width(row.get("width"))
        grid_x = int(row.get("grid_x") or 0)
        grid_y = int(row.get("grid_y") or 0)
        record: dict[str, Any] = {
            "osm_id": osm_id,
            "sac_scale": grade,
            "difficulty_class": DIFFICULTY_MAP[grade],
            "split_group": f"{grid_x}:{grid_y}",
            "length_km": length_km,
            "incline_pct": incline,
            "width_m": width,
            "incline_numeric_missing": 1.0 if incline is None else 0.0,
            "width_missing": 1.0 if width is None else 0.0,
            "terrain_available": 0.0,
            "elevation_gain_m": None,
            "elevation_loss_m": None,
            "elevation_range_m": None,
            "average_slope_pct": None,
            "max_slope_pct": None,
            "elevation_min_m": None,
            "elevation_max_m": None,
            "elevation_sample_count": None,
            "highway": normalise_categorical(
                row.get("highway"), "highway"
            ),
            "surface": normalise_categorical(
                row.get("surface"), "surface"
            ),
            "smoothness": normalise_categorical(
                row.get("smoothness"), "smoothness"
            ),
            "tracktype": normalise_categorical(
                row.get("tracktype"), "tracktype"
            ),
            "trail_visibility": normalise_categorical(
                row.get("trail_visibility"), "trail_visibility"
            ),
            "incline_direction": normalise_categorical(
                row.get("incline"), "incline_direction"
            ),
            "assisted_trail": normalise_categorical(
                row.get("assisted_trail"), "assisted_trail"
            ),
            "latitude": _number(row.get("latitude")),
            "longitude": _number(row.get("longitude")),
            "grid_x": grid_x,
            "grid_y": grid_y,
            "geometry_wkt": str(row.get("geometry_wkt") or ""),
        }
        records.append(record)

    frame = pd.DataFrame(records)
    if frame.empty:
        raise RuntimeError("the holdout query returned no usable rows")
    return frame.reindex(columns=OUTPUT_COLUMNS)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def holdout_cells() -> list[tuple[int, int]]:
    """
    The grid cells the trained model never sees.

    Derived from the same deterministic split the trainer uses, so this can
    never accidentally reach into training geography.
    """
    from app.ml import geographic_groups, train

    ways = pd.read_csv(WAYS_CSV)
    features = pd.read_csv(FEATURE_CSV)
    merged = features.merge(
        ways[["osm_id"]], on="osm_id", how="inner"
    )
    split = train.training_split(merged, ways)
    groups = geographic_groups.build_split_groups(ways)
    wanted = set(split["groups"][split["test_idx"]].tolist())
    return sorted(
        {
            (int(row.grid_x), int(row.grid_y))
            for row, group in zip(
                ways[["grid_x", "grid_y"]].itertuples(index=False),
                groups.tolist(),
            )
            if group in wanted
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Draw a natural-prevalence test sample from the already "
            "held-out geography."
        )
    )
    parser.add_argument(
        "--rows-per-cell",
        type=int,
        default=DEFAULT_ROWS_PER_CELL,
    )
    parser.add_argument(
        "--max-regions",
        type=int,
        default=MAX_REGIONS,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and the SQL without contacting Postpass.",
    )
    args = parser.parse_args()
    if args.rows_per_cell <= 0:
        raise ValueError("--rows-per-cell must be greater than zero")
    if args.max_regions <= 0:
        raise ValueError("--max-regions must be greater than zero")

    all_cells = holdout_cells()
    regions = cluster_cells(all_cells, max_regions=args.max_regions)
    region_cells = [
        cell for region in regions for cell in region["cells"]
    ]
    sql = build_sql(regions, args.rows_per_cell)
    print(f"held-out cells total:  {len(all_cells)}")
    print(f"regions sampled:       {len(regions)}")
    print(f"cells sampled:         {len(region_cells)}")
    print(f"rows per cell:         {args.rows_per_cell}")
    print(f"maximum rows:          {len(region_cells) * args.rows_per_cell}")
    if args.dry_run:
        print()
        print(sql)
        return 0

    rows = postpass_query(sql)
    print(f"rows returned:         {len(rows)}")
    frame = build_frame(rows)
    frame.to_csv(OUTPUT_CSV, index=False)

    counts = frame["sac_scale"].value_counts().to_dict()
    audit = {
        "source": POSTPASS_URL,
        "osm_object_type": "W",
        "target": "sac_scale",
        "purpose": (
            "Natural-prevalence final test. Drawn from geography already held "
            "out of training, with no per-class control, so the class mix is "
            "the real distribution of that area. Scored once, after model "
            "selection, and never fitted on."
        ),
        "sampling": {
            "method": (
                "Uniform random draw by md5(osm_id) ordering within each "
                "held-out one-degree cell, capped per cell."
            ),
            "rows_per_cell": args.rows_per_cell,
            "held_out_cells_total": len(all_cells),
            "regions": [
                {
                    "bbox": region["bbox"],
                    "cells": len(region["cells"]),
                }
                for region in regions
            ],
            "region_note": (
                "The public endpoint times out on a query that must touch "
                "every graded way worldwide, so the draw is restricted to the "
                "largest contiguous clusters of held-out cells and a "
                "bounding-box pre-filter lets the service use a spatial "
                "index. The result is a natural-prevalence sample of those "
                "regions, not a global one, and is reported as such."
            ),
        },
        "rows": int(len(frame)),
        "unique_osm_ways": int(frame["osm_id"].nunique()),
        "class_counts": {str(k): int(v) for k, v in counts.items()},
        "terrain_coverage": 0.0,
        "terrain_note": (
            "No DEM was sampled for this test set. It is reported together "
            "with the terrain-free subset of the main holdout so the two are "
            "compared on like terrain coverage."
        ),
        "attribution": (
            "(c) OpenStreetMap contributors, ODbL, via the Postpass public "
            "SQL endpoint."
        ),
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "sql_sha256_prefix": hashlib.sha256(
            sql.encode("utf-8")
        ).hexdigest()[:16],
    }
    AUDIT_JSON.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )

    print()
    print("class distribution (natural, not controlled):")
    for grade in GRADES:
        print(f"  {grade:28s} {counts.get(grade, 0)}")
    print()
    print(f"wrote {OUTPUT_CSV.name} and {AUDIT_JSON.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled.")
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
