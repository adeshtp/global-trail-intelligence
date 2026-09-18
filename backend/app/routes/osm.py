import asyncio
import gzip
import hashlib
import heapq
import json
import math
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/osm", tags=["OpenStreetMap"])

# ============================================================
# CONFIGURATION
# ============================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSM_API_BASE = "https://api.openstreetmap.org/api/0.6"

HEADERS = {
    "User-Agent": "TerraPath/0.2 (educational outdoor intelligence project)",
    "Accept": "application/json",
}

DEFAULT_RADIUS_M = 5000
POINT_SEARCH_RADIUS_DEGREES = 0.05
COMPACT_AREA_HALF_SIZE_DEGREES = 0.025
AREA_CENTER_PROXIMITY_MAX_BONUS = 8.0

COMPACT_AREA_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "municipality",
    "suburb",
    "neighbourhood",
    "quarter",
    "locality",
}

BROAD_AREA_TYPES = {
    "district",
    "state_district",
    "county",
    "state",
    "region",
    "province",
    "country",
    "island",
}

INITIAL_TILE_DEGREES = 0.10
MIN_ADAPTIVE_TILE_DEGREES = 0.005
MAX_TILES = 1600
TILE_OVERLAP_DEGREES = 0.00025

OSM_REQUEST_TIMEOUT = 20.0
FULL_OBJECT_TIMEOUT = 20.0
NOMINATIM_TIMEOUT = 10.0
RELATION_HYDRATION_CONCURRENCY = 3
COMPONENT_WAY_FETCH_CONCURRENCY = 4

OBJECT_CACHE_TTL_SECONDS = 600.0
GEOCODE_CACHE_TTL_SECONDS = 900.0

# Maximum number of hiking relations to fully hydrate per discover_trails call.
# Prevents unbounded OSM API calls during broad-area searches.
RELATION_PROMOTION_BUDGET = 5

OSM_DATASET_CACHE_TTL_SECONDS = 900.0
OSM_DATASET_CACHE_MAX_ENTRIES = 32
OSM_DISK_CACHE_DIR = (
    Path(__file__).resolve().parents[2]
    / ".cache"
    / "osm"
)

_FULL_OBJECT_CACHE: dict[
    tuple[str, int],
    tuple[float, dict[str, Any]],
] = {}

_GEOCODE_CACHE: dict[
    str,
    tuple[float, list[dict[str, Any]]],
] = {}

_OSM_DATASET_CACHE: dict[
    str,
    tuple[
        float,
        tuple[
            dict[str, Any],
            dict[str, Any],
        ],
    ],
] = {}

_OSM_DATASET_INFLIGHT: dict[
    str,
    asyncio.Task[
        tuple[
            dict[str, Any],
            dict[str, Any],
        ]
    ],
] = {}

TRAIL_HIGHWAYS = {
    "path",
    "bridleway",
    "steps",
}

POSSIBLE_TRAIL_HIGHWAYS = {
    "footway",
    "track",
}

EXCLUDED_FOOTWAY_USES = {
    "sidewalk",
    "crossing",
    "alley",
    "driveway",
    "parking_aisle",
    "corridor",
}

HIKING_ROUTE_TYPES = {
    "hiking",
    "foot",
    "walking",
}

PRIMARY_MEMBER_ROLES = {
    "",
    "main",
    "forward",
    "backward",
}

SECONDARY_BRANCH_ROLES = {
    "approach",
    "excursion",
    "alternative",
    "connection",
}

UNPAVED_SURFACES = {
    "earth",
    "ground",
    "dirt",
    "mud",
    "sand",
    "gravel",
    "fine_gravel",
    "pebblestone",
    "unpaved",
    "grass",
    "wood",
    "woodchips",
    "compacted",
    "rock",
    "natural",
}

PAVED_SURFACES = {
    "asphalt",
    "concrete",
    "paved",
    "cement",
    "paving_stones",
    "sett",
}

GENERIC_NAMES = {
    "path",
    "trail",
    "way",
    "route",
    "footway",
    "track",
    "footpath",
    "walkway",
    "outdoor path",
    "outdoor track",
    "hiking path",
    "unnamed trail",
    "unnamed hiking trail",
    "hiking trail",
    "outdoor trail",
    "walking route",
    "hiking route",
    "foot route",
    "dead end path",
    "unnamed path",
    "unnamed track",
}

ROAD_LIKE_PATTERN = re.compile(
    r"\b(?:road|rd|street|st|lane|ln|avenue|ave|drive|dr|"
    r"highway|hwy|bypass|junction|jct|motorway|expressway|"
    r"school|college|hostel|campus|bus stand|market|parking|"
    r"estate road|service road|main road|entrance|gate|bridge|"
    r"hospital|station|temple|church|office)\b",
    re.I,
)

TRAIL_NAME_PATTERN = re.compile(
    r"\b(?:trail|trek|hike|hiking|nature|forest|mountain|peak|"
    r"summit|waterfall|falls|viewpoint|ridge|pass|gorge|wildlife|"
    r"sanctuary|reserve|plantation|walk|loop|lake|zero point|"
    r"top station|mala|shola|forest walk)\b",
    re.I,
)

SEARCH_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "near",
    "at",
    "to",
    "for",
    "and",
    "or",
    "on",
    "trail",
    "trek",
    "hike",
    "hiking",
    "route",
    "path",
    "mountain",
    "peak",
    "hill",
    "location",
    "place",
}

FEATURE_TYPES = {
    "peak",
    "mountain",
    "summit",
    "ridge",
    "hill",
    "waterfall",
    "viewpoint",
    "cliff",
    "gorge",
    "valley",
    "saddle",
    "lake",
    "pass",
}


# ============================================================
# BASIC HELPERS
# ============================================================


def norm(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").strip().lower(),
    )


def meaningful_name(
    value: Any,
) -> bool:
    name = norm(value)

    return bool(
        name
        and name not in GENERIC_NAMES
    )


def trailish_name(
    value: Any,
) -> bool:
    return bool(
        TRAIL_NAME_PATTERN.search(
            str(value or "")
        )
    )


def road_like_name(
    value: Any,
) -> bool:
    return bool(
        ROAD_LIKE_PATTERN.search(
            str(value or "")
        )
    )


def text_tokens(
    value: Any,
) -> list[str]:
    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        norm(value),
    )

    return [
        token
        for token in text.split()
        if len(token) >= 2
        and token not in SEARCH_STOPWORDS
    ]


def search_tokens(
    value: Any,
) -> list[str]:
    """Tokenize names/queries for geocoding without removing place-type words."""
    return [
        token
        for token in re.findall(r"[a-z0-9]+", norm(value))
        if len(token) >= 2
    ]


def haversine(
    coord1: list[float],
    coord2: list[float],
) -> float:
    lon1, lat1 = coord1
    lon2, lat2 = coord2

    radius = 6371.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dphi = math.radians(
        lat2 - lat1
    )

    dlambda = math.radians(
        lon2 - lon1
    )

    a = (
        math.sin(
            dphi / 2.0
        ) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(
            dlambda / 2.0
        ) ** 2
    )

    a = max(
        0.0,
        min(
            1.0,
            a,
        ),
    )

    return (
        radius
        * 2.0
        * math.atan2(
            math.sqrt(a),
            math.sqrt(
                1.0 - a
            ),
        )
    )


def line_length(
    coords: list[list[float]],
) -> float:
    if len(coords) < 2:
        return 0.0

    return sum(
        haversine(
            coords[index],
            coords[index + 1],
        )
        for index in range(
            len(coords) - 1
        )
    )


def dedupe_coordinates(
    coords: list[list[float]],
) -> list[list[float]]:
    cleaned: list[list[float]] = []

    previous: list[float] | None = None

    for point in coords:
        if (
            not isinstance(
                point,
                list,
            )
            or len(point) < 2
        ):
            continue

        try:
            current = [
                float(point[0]),
                float(point[1]),
            ]
        except (
            TypeError,
            ValueError,
        ):
            continue

        if not (
            -180
            <= current[0]
            <= 180
            and -90
            <= current[1]
            <= 90
        ):
            continue

        if (
            previous is not None
            and current == previous
        ):
            continue

        cleaned.append(
            current
        )

        previous = current

    return cleaned


def geometry_object(
    segments: list[
        list[
            list[float]
        ]
    ],
) -> dict[str, Any] | None:
    usable = [
        dedupe_coordinates(
            segment
        )
        for segment in segments
    ]

    usable = [
        segment
        for segment in usable
        if len(segment) >= 2
    ]

    if not usable:
        return None

    if len(usable) == 1:
        return {
            "type": "LineString",
            "coordinates": usable[0],
        }

    return {
        "type": "MultiLineString",
        "coordinates": usable,
    }


def route_shape(
    segments: list[
        list[
            list[float]
        ]
    ],
) -> str:
    usable = [
        dedupe_coordinates(
            segment
        )
        for segment in segments
    ]

    usable = [
        segment
        for segment in usable
        if len(segment) >= 2
    ]

    if not usable:
        return "unknown"

    if len(usable) > 1:
        return "multiline"

    if (
        len(usable[0]) >= 3
        and haversine(
            usable[0][0],
            usable[0][-1],
        )
        <= 0.06
    ):
        return "loop"

    return "open_or_out_and_back"


def route_endpoints(
    segments: list[
        list[
            list[float]
        ]
    ],
) -> tuple[
    list[float] | None,
    list[float] | None,
]:
    usable = [
        dedupe_coordinates(
            segment
        )
        for segment in segments
    ]

    usable = [
        segment
        for segment in usable
        if len(segment) >= 2
    ]

    if len(usable) == 1:
        return (
            usable[0][0],
            usable[0][-1],
        )

    return (
        None,
        None,
    )


def min_geometry_distance(
    coords: list[list[float]],
    latitude: float,
    longitude: float,
) -> float:
    if not coords:
        return float("inf")

    target = [
        longitude,
        latitude,
    ]

    return min(
        haversine(
            point,
            target,
        )
        for point in coords
    )


def center_from_coords(
    coords: list[list[float]],
) -> tuple[
    float | None,
    float | None,
]:
    if not coords:
        return (
            None,
            None,
        )

    return (
        sum(
            point[1]
            for point in coords
        )
        / len(coords),
        sum(
            point[0]
            for point in coords
        )
        / len(coords),
    )


def parse_bbox(
    value: Any,
) -> tuple[
    float,
    float,
    float,
    float,
] | None:
    if value is None:
        return None

    try:
        if (
            isinstance(
                value,
                (
                    list,
                    tuple,
                ),
            )
            and len(value) == 4
        ):
            first, second, third, fourth = map(
                float,
                value,
            )

        else:
            parts = [
                float(
                    part.strip()
                )
                for part in str(
                    value
                ).split(",")
            ]

            if len(parts) != 4:
                return None

            first, second, third, fourth = parts

        if (
            -90 <= first <= 90
            and -90 <= second <= 90
            and -180 <= third <= 180
            and -180 <= fourth <= 180
        ):
            south = first
            north = second
            west = third
            east = fourth

            if (
                south <= north
                and west <= east
            ):
                return (
                    west,
                    south,
                    east,
                    north,
                )

        if (
            -180 <= first <= 180
            and -90 <= second <= 90
            and -180 <= third <= 180
            and -90 <= fourth <= 90
        ):
            west = first
            south = second
            east = third
            north = fourth

            if (
                west <= east
                and south <= north
            ):
                return (
                    west,
                    south,
                    east,
                    north,
                )

        return None

    except (
        TypeError,
        ValueError,
    ):
        return None


def parse_nominatim_bbox(
    value: Any,
) -> tuple[
    float,
    float,
    float,
    float,
] | None:
    if value is None:
        return None

    try:
        parts = (
            list(value)
            if isinstance(
                value,
                (
                    list,
                    tuple,
                ),
            )
            else [
                part.strip()
                for part in str(
                    value
                ).split(",")
            ]
        )

        if len(parts) != 4:
            return None

        south = float(parts[0])
        north = float(parts[1])
        west = float(parts[2])
        east = float(parts[3])

        if not (
            -90 <= south <= 90
            and -90 <= north <= 90
            and -180 <= west <= 180
            and -180 <= east <= 180
        ):
            return None

        if (
            south >= north
            or west >= east
        ):
            return None

        return (
            west,
            south,
            east,
            north,
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def bbox_from_radius(
    latitude: float,
    longitude: float,
    radius_m: int,
) -> tuple[
    float,
    float,
    float,
    float,
]:
    earth_radius_m = 6_371_000.0

    latitude_delta = (
        radius_m
        / earth_radius_m
        * (
            180.0
            / math.pi
        )
    )

    longitude_scale = max(
        abs(
            math.cos(
                math.radians(
                    latitude
                )
            )
        ),
        1e-9,
    )

    longitude_delta = (
        radius_m
        / (
            earth_radius_m
            * longitude_scale
        )
        * (
            180.0
            / math.pi
        )
    )

    return (
        max(
            -180.0,
            longitude - longitude_delta,
        ),
        max(
            -90.0,
            latitude - latitude_delta,
        ),
        min(
            180.0,
            longitude + longitude_delta,
        ),
        min(
            90.0,
            latitude + latitude_delta,
        ),
    )


def bbox_area_km2(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> float:
    west, south, east, north = bbox

    return max(
        0.0,
        haversine(
            [west, south],
            [east, south],
        )
        * haversine(
            [west, south],
            [west, north],
        ),
    )


def bbox_string(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> str:
    west, south, east, north = bbox

    return (
        f"{west:.7f},"
        f"{south:.7f},"
        f"{east:.7f},"
        f"{north:.7f}"
    )


def make_tiles(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
    tile_size: float,
) -> list[
    tuple[
        float,
        float,
        float,
        float,
    ]
]:
    west, south, east, north = bbox

    width = east - west
    height = north - south

    cols = max(
        1,
        math.ceil(
            width
            / tile_size
        ),
    )

    rows = max(
        1,
        math.ceil(
            height
            / tile_size
        ),
    )

    tiles: list[
        tuple[
            float,
            float,
            float,
            float,
        ]
    ] = []

    for row in range(rows):
        tile_south = (
            south
            + height
            * row
            / rows
        )

        tile_north = (
            south
            + height
            * (row + 1)
            / rows
        )

        for column in range(cols):
            tile_west = (
                west
                + width
                * column
                / cols
            )

            tile_east = (
                west
                + width
                * (column + 1)
                / cols
            )

            tiles.append(
                (
                    max(
                        -180.0,
                        tile_west
                        - TILE_OVERLAP_DEGREES,
                    ),
                    max(
                        -90.0,
                        tile_south
                        - TILE_OVERLAP_DEGREES,
                    ),
                    min(
                        180.0,
                        tile_east
                        + TILE_OVERLAP_DEGREES,
                    ),
                    min(
                        90.0,
                        tile_north
                        + TILE_OVERLAP_DEGREES,
                    ),
                )
            )

    return tiles


def split_bbox(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> list[
    tuple[
        float,
        float,
        float,
        float,
    ]
]:
    west, south, east, north = bbox

    if (
        max(
            east - west,
            north - south,
        )
        <= MIN_ADAPTIVE_TILE_DEGREES
    ):
        return []

    mid_lon = (
        west
        + east
    ) / 2.0

    mid_lat = (
        south
        + north
    ) / 2.0

    return [
        (
            west,
            south,
            mid_lon,
            mid_lat,
        ),
        (
            mid_lon,
            south,
            east,
            mid_lat,
        ),
        (
            west,
            mid_lat,
            mid_lon,
            north,
        ),
        (
            mid_lon,
            mid_lat,
            east,
            north,
        ),
    ]


# ============================================================
# OSM TILE PROVIDER
# ============================================================


def is_too_dense(
    response: httpx.Response,
) -> bool:
    if response.status_code != 400:
        return False

    message = norm(
        response.text
    )

    return (
        "too many nodes"
        in message
        or (
            "bounding box"
            in message
            and "large"
            in message
        )
    )


async def fetch_map_tile(
    client: httpx.AsyncClient,
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> tuple[
    dict[str, Any] | None,
    bool,
    str | None,
]:
    try:
        response = await client.get(
            f"{OSM_API_BASE}/map",
            params={
                "bbox": bbox_string(
                    bbox
                )
            },
        )

        if response.status_code == 200:
            try:
                data = response.json()

            except ValueError as exc:
                return (
                    None,
                    False,
                    (
                        "OSM returned HTTP 200 "
                        "but invalid JSON: "
                        f"{exc}"
                    ),
                )

            if not isinstance(
                data,
                dict,
            ):
                return (
                    None,
                    False,
                    "OSM returned a non-object JSON response",
                )

            return (
                data,
                False,
                None,
            )

        if is_too_dense(
            response
        ):
            return (
                None,
                True,
                None,
            )

        return (
            None,
            False,
            (
                f"HTTP {response.status_code}: "
                f"{response.text[:180]}"
            ),
        )

    except Exception as exc:
        return (
            None,
            False,
            str(exc),
        )


async def _fetch_osm_dataset_uncached(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    elements_by_key: dict[
        tuple[str, int],
        dict[str, Any],
    ] = {}

    initial_tiles = make_tiles(
        bbox,
        INITIAL_TILE_DEGREES,
    )

    if len(initial_tiles) > MAX_TILES:
        scale = math.sqrt(
            len(initial_tiles)
            / MAX_TILES
        )

        initial_tiles = make_tiles(
            bbox,
            INITIAL_TILE_DEGREES
            * scale,
        )

    initial_tiles = initial_tiles[
        :MAX_TILES
    ]

    queue: list[
        tuple[
            tuple[
                float,
                float,
                float,
                float,
            ],
            int,
        ]
    ] = [
        (
            tile,
            0,
        )
        for tile in initial_tiles
    ]

    adaptive_splits = 0
    successful_tiles = 0
    failed_tiles = 0

    provider_errors: list[str] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            OSM_REQUEST_TIMEOUT
        ),
        headers=HEADERS,
    ) as client:
        while queue:
            current_batch = queue[
                :8
            ]

            queue = queue[
                8:
            ]

            results = await asyncio.gather(
                *(
                    fetch_map_tile(
                        client,
                        tile,
                    )
                    for tile, _depth
                    in current_batch
                ),
                return_exceptions=True,
            )

            for (
                (
                    tile,
                    depth,
                ),
                result,
            ) in zip(
                current_batch,
                results,
            ):
                if isinstance(
                    result,
                    Exception,
                ):
                    failed_tiles += 1

                    provider_errors.append(
                        str(result)
                    )

                    continue

                data, too_dense, error = result

                if data is not None:
                    successful_tiles += 1

                    for element in (
                        data.get(
                            "elements"
                        )
                        or []
                    ):
                        try:
                            element_type = str(
                                element.get(
                                    "type"
                                )
                                or ""
                            )

                            element_id = int(
                                element.get(
                                    "id"
                                )
                                or 0
                            )

                        except (
                            TypeError,
                            ValueError,
                        ):
                            continue

                        if element_id:
                            elements_by_key[
                                (
                                    element_type,
                                    element_id,
                                )
                            ] = element

                    continue

                if too_dense:
                    children = split_bbox(
                        tile
                    )

                    if children:
                        adaptive_splits += 1

                        queue.extend(
                            (
                                child,
                                depth + 1,
                            )
                            for child in children
                        )

                    else:
                        failed_tiles += 1

                        provider_errors.append(
                            (
                                "OSM tile remained too dense "
                                "at minimum size "
                                f"{MIN_ADAPTIVE_TILE_DEGREES}: "
                                f"{bbox_string(tile)}"
                            )
                        )

                else:
                    failed_tiles += 1

                    if error:
                        provider_errors.append(
                            error
                        )

    return (
        {
            "version": 0.6,
            "generator": "OpenStreetMap",
            "elements": list(
                elements_by_key.values()
            ),
        },
        {
            "initial_tiles_requested": len(
                initial_tiles
            ),
            "successful_tiles": successful_tiles,
            "failed_tiles": failed_tiles,
            "adaptive_splits": adaptive_splits,
            "provider_errors": provider_errors[
                :20
            ],
        },
    )


# ============================================================
# OSM DATASET CACHE
# ============================================================


def _dataset_disk_path(
    cache_key: str,
) -> Path:
    digest = hashlib.sha256(
        cache_key.encode(
            "utf-8"
        )
    ).hexdigest()

    return (
        OSM_DISK_CACHE_DIR
        / f"{digest}.json.gz"
    )


def _load_dataset_disk_cache(
    cache_key: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
] | None:
    path = _dataset_disk_path(
        cache_key
    )

    try:
        if not path.exists():
            return None

        if (
            time.time()
            - path.stat().st_mtime
            > OSM_DATASET_CACHE_TTL_SECONDS
        ):
            return None

        with gzip.open(
            path,
            "rt",
            encoding="utf-8",
        ) as handle:
            payload = json.load(
                handle
            )

        if not isinstance(
            payload,
            dict,
        ):
            return None

        data = payload.get(
            "data"
        )

        acquisition = payload.get(
            "acquisition"
        )

        if not isinstance(
            data,
            dict,
        ) or not isinstance(
            acquisition,
            dict,
        ):
            return None

        return (
            data,
            acquisition,
        )

    except Exception:
        return None


def _save_dataset_disk_cache(
    cache_key: str,
    data: dict[str, Any],
    acquisition: dict[str, Any],
) -> None:
    path = _dataset_disk_path(
        cache_key
    )

    temporary = path.with_suffix(
        ".tmp"
    )

    try:
        OSM_DISK_CACHE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        with gzip.open(
            temporary,
            "wt",
            encoding="utf-8",
        ) as handle:
            json.dump(
                {
                    "data": data,
                    "acquisition": acquisition,
                },
                handle,
                separators=(
                    ",",
                    ":",
                ),
            )

        temporary.replace(
            path
        )

    except Exception:
        try:
            temporary.unlink(
                missing_ok=True
            )
        except Exception:
            pass


async def fetch_osm_dataset(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    cache_key = bbox_string(
        bbox
    )

    now = time.monotonic()

    cached = _OSM_DATASET_CACHE.get(
        cache_key
    )

    if (
        cached
        and (
            now - cached[0]
            <= OSM_DATASET_CACHE_TTL_SECONDS
        )
    ):
        data, acquisition = cached[
            1
        ]

        return (
            data,
            {
                **acquisition,
                "dataset_cache_hit": True,
                "dataset_disk_cache_hit": False,
            },
        )

    disk_cached = await asyncio.to_thread(
        _load_dataset_disk_cache,
        cache_key,
    )

    if disk_cached is not None:
        data, acquisition = disk_cached

        _OSM_DATASET_CACHE[
            cache_key
        ] = (
            now,
            (
                data,
                acquisition,
            ),
        )

        return (
            data,
            {
                **acquisition,
                "dataset_cache_hit": True,
                "dataset_disk_cache_hit": True,
            },
        )

    stale_keys = [
        key
        for key, (
            created,
            _value,
        )
        in _OSM_DATASET_CACHE.items()
        if (
            now - created
            > OSM_DATASET_CACHE_TTL_SECONDS
        )
    ]

    for key in stale_keys:
        _OSM_DATASET_CACHE.pop(
            key,
            None,
        )

    while (
        len(_OSM_DATASET_CACHE)
        >= OSM_DATASET_CACHE_MAX_ENTRIES
    ):
        oldest_key = min(
            _OSM_DATASET_CACHE.items(),
            key=lambda item: item[1][0],
        )[0]

        _OSM_DATASET_CACHE.pop(
            oldest_key,
            None,
        )

    inflight = _OSM_DATASET_INFLIGHT.get(
        cache_key
    )

    if inflight is not None:
        data, acquisition = await inflight

        return (
            data,
            {
                **acquisition,
                "dataset_cache_hit": True,
                "dataset_inflight_hit": True,
                "dataset_disk_cache_hit": False,
            },
        )

    task = asyncio.create_task(
        _fetch_osm_dataset_uncached(
            bbox
        )
    )

    _OSM_DATASET_INFLIGHT[
        cache_key
    ] = task

    try:
        data, acquisition = await task

        _OSM_DATASET_CACHE[
            cache_key
        ] = (
            time.monotonic(),
            (
                data,
                acquisition,
            ),
        )

        await asyncio.to_thread(
            _save_dataset_disk_cache,
            cache_key,
            data,
            acquisition,
        )

        return (
            data,
            {
                **acquisition,
                "dataset_cache_hit": False,
                "dataset_disk_cache_hit": False,
            },
        )

    finally:
        if (
            _OSM_DATASET_INFLIGHT.get(
                cache_key
            )
            is task
        ):
            _OSM_DATASET_INFLIGHT.pop(
                cache_key,
                None,
            )


# ============================================================
# RELATION / WAY PARSING
# ============================================================


def build_relation_evidence(
    elements: list[
        dict[str, Any]
    ],
) -> dict[
    int,
    dict[str, Any],
]:
    evidence: dict[
        int,
        dict[str, Any],
    ] = {}

    for relation in elements:
        if relation.get(
            "type"
        ) != "relation":
            continue

        tags = relation.get(
            "tags"
        ) or {}

        route = norm(
            tags.get(
                "route"
            )
        )

        if route not in HIKING_ROUTE_TYPES:
            continue

        for member in (
            relation.get(
                "members"
            )
            or []
        ):
            if member.get(
                "type"
            ) != "way":
                continue

            try:
                way_id = int(
                    member.get(
                        "ref"
                    )
                )

                relation_id = int(
                    relation.get(
                        "id"
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            current = evidence.setdefault(
                way_id,
                {
                    "route_types": set(),
                    "relation_ids": set(),
                    "networks": set(),
                },
            )

            current[
                "route_types"
            ].add(
                route
            )

            current[
                "relation_ids"
            ].add(
                relation_id
            )

            network = norm(
                tags.get(
                    "network"
                )
            )

            if network:
                current[
                    "networks"
                ].add(
                    network
                )

    return evidence


def extract_way_geometry(
    way: dict[str, Any],
    nodes: dict[
        int,
        list[float],
    ],
) -> list[
    list[float]
]:
    coords: list[
        list[float]
    ] = []

    geometry = way.get(
        "geometry"
    )

    if isinstance(
        geometry,
        list,
    ):
        for point in geometry:
            if not isinstance(
                point,
                dict,
            ):
                continue

            try:
                coords.append(
                    [
                        float(
                            point.get(
                                "lon"
                            )
                        ),
                        float(
                            point.get(
                                "lat"
                            )
                        ),
                    ]
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

        if coords:
            return dedupe_coordinates(
                coords
            )

    for raw_node_id in (
        way.get(
            "nodes"
        )
        or []
    ):
        try:
            node_id = int(
                raw_node_id
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        point = nodes.get(
            node_id
        )

        if point:
            coords.append(
                [
                    float(
                        point[0]
                    ),
                    float(
                        point[1]
                    ),
                ]
            )

    return dedupe_coordinates(
        coords
    )


def _parse_full_relation_payload(
    data: dict[str, Any] | list[dict[str, Any]],
    relation_id: int | None = None,
) -> dict[str, Any] | None:
    """
    Extract and structure the relation object, authoritative tags,
    ordered member ways, roles as metadata, and coordinate maps
    from an OSM 0.6 full-object response (GET /api/0.6/relation/{id}/full.json)
    or a collection of raw OSM elements.

    Preserves all way members by default. Roles are retained as metadata.
    Unknown roles are never discarded; they remain primary by default.
    """
    if isinstance(data, dict):
        elements = data.get("elements") or []
    elif isinstance(data, list):
        elements = data
    else:
        return None

    if not isinstance(elements, list) or not elements:
        return None

    target_rel_id = None
    if relation_id is not None:
        try:
            target_rel_id = int(relation_id)
        except (TypeError, ValueError):
            return None

    relation_obj: dict[str, Any] | None = None

    for el in elements:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "relation":
            try:
                current_id = int(el.get("id"))
            except (TypeError, ValueError):
                continue

            if target_rel_id is None or current_id == target_rel_id:
                relation_obj = el
                target_rel_id = current_id
                break

    if relation_obj is None:
        return None

    raw_tags = relation_obj.get("tags")
    tags = raw_tags if isinstance(raw_tags, dict) else {}

    rel_name = str(tags.get("name") or "").strip() or None
    route_type = norm(tags.get("route")) or None
    network = str(tags.get("network") or "").strip() or None
    symbol = (
        str(tags.get("symbol") or tags.get("osmc:symbol") or "").strip()
        or None
    )
    ref = str(tags.get("ref") or "").strip() or None

    nodes: dict[int, list[float]] = {}
    ways_by_id: dict[int, dict[str, Any]] = {}

    for el in elements:
        if not isinstance(el, dict):
            continue
        el_type = el.get("type")
        if el_type == "node":
            try:
                nid = int(el.get("id"))
                lon = float(el.get("lon"))
                lat = float(el.get("lat"))
                nodes[nid] = [lon, lat]
            except (TypeError, ValueError):
                continue
        elif el_type == "way":
            try:
                wid = int(el.get("id"))
                ways_by_id[wid] = el
            except (TypeError, ValueError):
                continue

    raw_members = relation_obj.get("members")
    if not isinstance(raw_members, list):
        raw_members = []

    member_ways: list[dict[str, Any]] = []

    for index, raw_member in enumerate(raw_members):
        if not isinstance(raw_member, dict):
            continue
        if raw_member.get("type") != "way":
            continue

        try:
            way_id = int(raw_member.get("ref"))
        except (TypeError, ValueError):
            continue

        raw_role = str(raw_member.get("role") or "").strip()
        norm_role = norm(raw_role)

        is_secondary = norm_role in SECONDARY_BRANCH_ROLES
        is_primary = (
            norm_role in PRIMARY_MEMBER_ROLES
            or not is_secondary
        )

        way_elem = ways_by_id.get(way_id)
        way_coords: list[list[float]] = []
        if way_elem:
            way_coords = extract_way_geometry(way_elem, nodes)

        member_ways.append(
            {
                "member_index": index,
                "way_id": way_id,
                "role": raw_role,
                "normalized_role": norm_role,
                "is_primary": is_primary,
                "is_secondary": is_secondary,
                "way": way_elem,
                "geometry": way_coords,
                "has_geometry": len(way_coords) >= 2,
            }
        )

    return {
        "relation_id": target_rel_id,
        "name": rel_name,
        "tags": tags,
        "route": route_type,
        "network": network,
        "symbol": symbol,
        "ref": ref,
        "member_ways": member_ways,
        "primary_members": [
            m for m in member_ways if m["is_primary"]
        ],
        "secondary_members": [
            m for m in member_ways if m["is_secondary"]
        ],
        "nodes": nodes,
        "ways_by_id": ways_by_id,
        "all_members_count": len(raw_members),
        "way_members_count": len(member_ways),
        "way_members_with_geometry": sum(
            1 for m in member_ways if m["has_geometry"]
        ),
    }


def _points_connect(
    p1: list[float] | None,
    p2: list[float] | None,
    tolerance_km: float = 0.005,
) -> bool:
    if not p1 or not p2:
        return False
    if p1[0] == p2[0] and p1[1] == p2[1]:
        return True
    return haversine(p1, p2) <= tolerance_km


def _stitch_member_sequence(
    members: list[dict[str, Any]],
) -> list[list[list[float]]]:
    """
    Stitch an ordered sequence of member ways into continuous chains.
    Preserves member order and only reverses individual ways when
    necessary to connect to the preceding route segment.
    Never bridges physical gaps artificially.
    """
    valid_geometries: list[list[list[float]]] = []
    for m in members:
        geom = m.get("geometry")
        if isinstance(geom, list) and len(geom) >= 2:
            cleaned = dedupe_coordinates(geom)
            if len(cleaned) >= 2:
                valid_geometries.append(cleaned)

    if not valid_geometries:
        return []

    chains: list[list[list[float]]] = []
    current_chain: list[list[float]] = list(valid_geometries[0])

    for next_geom in valid_geometries[1:]:
        # Try to connect next_geom to current_chain
        # 1. next_geom start matches current_chain end (forward)
        if _points_connect(current_chain[-1], next_geom[0]):
            current_chain.extend(next_geom[1:])
        # 2. next_geom end matches current_chain end (reversed way)
        elif _points_connect(current_chain[-1], next_geom[-1]):
            current_chain.extend(reversed(next_geom[:-1]))
        # 3. Special case: first way in chain was mapped backwards relative to way 1
        elif (
            len(chains) == 0
            and len(current_chain) == len(valid_geometries[0])
            and (
                _points_connect(current_chain[0], next_geom[0])
                or _points_connect(current_chain[0], next_geom[-1])
            )
        ):
            current_chain = list(reversed(current_chain))
            if _points_connect(current_chain[-1], next_geom[0]):
                current_chain.extend(next_geom[1:])
            else:
                current_chain.extend(reversed(next_geom[:-1]))
        # 4. next_geom end matches current_chain start (prepend forward)
        elif _points_connect(current_chain[0], next_geom[-1]):
            current_chain = next_geom[:-1] + current_chain
        # 5. next_geom start matches current_chain start (prepend reversed)
        elif _points_connect(current_chain[0], next_geom[0]):
            current_chain = list(reversed(next_geom[1:])) + current_chain
        else:
            # Physical gap: next_geom does not connect to current_chain.
            # Do NOT draw an artificial line across the gap.
            chains.append(dedupe_coordinates(current_chain))
            current_chain = list(next_geom)

    if current_chain:
        chains.append(dedupe_coordinates(current_chain))

    return [c for c in chains if len(c) >= 2]


def _assemble_relation_geometry(
    parsed_relation: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Step 2 Sequence-Preserving & Role-Aware Geometry Assembler.

    Requirements:
    - Preserves OSM relation member order (never uses sorted way IDs).
    - Reverses individual ways only when necessary to connect with preceding route.
    - Never bridges physical gaps artificially.
    - Preserves secondary branches (approach, excursion, alternative) separately.
    - Preserves unknown roles (treated as primary by default, never discarded).
    - Detects closed loops: sets is_loop=True and endpoint_available=False.
    - Do not infer or force summit/trailhead direction.
    - Deduplicates shared junction coordinates.
    - Calculates total distance without double-counting shared endpoints.
    - Reuses existing geometry helpers (geometry_object, route_shape, route_endpoints).
    """
    if not parsed_relation or not isinstance(parsed_relation, dict):
        return None

    member_ways = parsed_relation.get("member_ways") or []
    if not member_ways:
        return None

    primary_members = parsed_relation.get("primary_members") or []
    secondary_members = parsed_relation.get("secondary_members") or []

    # If all members are flagged secondary (rare), treat them all as primary
    if not primary_members and secondary_members:
        primary_members = secondary_members
        secondary_members = []

    # Stitch primary members preserving relation member order
    primary_chains = _stitch_member_sequence(primary_members)

    # Stitch secondary branch members preserving roles
    secondary_chains = _stitch_member_sequence(secondary_members)

    all_segments = primary_chains + secondary_chains
    if not all_segments:
        return None

    # Construct GeoJSON geometry object using existing helper
    geometry = geometry_object(all_segments)
    if not geometry:
        return None

    # Determine loop semantics on the main primary chain
    is_loop = False
    endpoint_available = True

    if primary_chains:
        main_chain = primary_chains[0]
        if len(main_chain) >= 3 and _points_connect(
            main_chain[0], main_chain[-1], tolerance_km=0.06
        ):
            is_loop = True
            endpoint_available = False

    # Route shape: use existing route_shape helper or "loop" if closed loop detected
    if is_loop:
        route_shape_val = "loop"
    else:
        route_shape_val = route_shape(all_segments)

    # Start/end endpoints (no artificial summit-based reversal)
    start_coordinate: list[float] | None = None
    end_coordinate: list[float] | None = None

    if is_loop:
        if primary_chains:
            start_coordinate = primary_chains[0][0]
            end_coordinate = primary_chains[0][-1]
    else:
        if primary_chains:
            start_coordinate = primary_chains[0][0]
            end_coordinate = primary_chains[0][-1]
        elif all_segments:
            start_coordinate = all_segments[0][0]
            end_coordinate = all_segments[0][-1]

    # Calculate total distance without double counting shared endpoints
    total_distance_km = round(
        sum(line_length(seg) for seg in all_segments),
        3,
    )

    ordered_way_ids = [
        int(m["way_id"])
        for m in member_ways
        if m.get("way_id") is not None
    ]

    return {
        "geometry": geometry,
        "route_shape": route_shape_val,
        "is_loop": is_loop,
        "distance_km": total_distance_km,
        "start_coordinate": start_coordinate,
        "end_coordinate": end_coordinate,
        "endpoint_available": endpoint_available,
        "primary_segment_count": len(primary_chains),
        "secondary_segment_count": len(secondary_chains),
        "total_segment_count": len(all_segments),
        "ordered_way_ids": ordered_way_ids,
    }


def way_is_candidate(
    way: dict[str, Any],
) -> bool:
    tags = way.get(
        "tags"
    ) or {}

    highway = norm(
        tags.get(
            "highway"
        )
    )

    if highway in TRAIL_HIGHWAYS:
        return True

    if highway in POSSIBLE_TRAIL_HIGHWAYS:
        if (
            highway == "footway"
            and norm(
                tags.get(
                    "footway"
                )
            )
            in EXCLUDED_FOOTWAY_USES
        ):
            return False

        return True

    return False


# ============================================================
# TRAIL EVIDENCE / SCORING
# ============================================================


def hiking_evidence(
    tags: dict[str, Any],
    relation: dict[str, Any] | None,
) -> tuple[
    int,
    list[str],
]:
    score = 0

    reasons: list[str] = []

    route = norm(
        tags.get(
            "route"
        )
    )

    if route in HIKING_ROUTE_TYPES:
        score += 100

        reasons.append(
            f"route={route}"
        )

    sac = norm(
        tags.get(
            "sac_scale"
        )
    )

    if sac:
        score += 45

        reasons.append(
            f"sac_scale={sac}"
        )

    visibility = norm(
        tags.get(
            "trail_visibility"
        )
    )

    if visibility:
        score += 15

        reasons.append(
            f"trail_visibility={visibility}"
        )

    if norm(
        tags.get(
            "trailblazed"
        )
    ):
        score += 10

        reasons.append(
            "trailblazed metadata"
        )

    if norm(
        tags.get(
            "designation"
        )
    ):
        score += 8

        reasons.append(
            "designation metadata"
        )

    if norm(
        tags.get(
            "hiking"
        )
    ):
        score += 8

        reasons.append(
            "hiking metadata"
        )

    surface = norm(
        tags.get(
            "surface"
        )
    )

    if surface in UNPAVED_SURFACES:
        score += 15

        reasons.append(
            f"unpaved surface={surface}"
        )

    elif surface in PAVED_SURFACES:
        score -= 8

        reasons.append(
            f"paved surface={surface}"
        )

    highway = norm(
        tags.get(
            "highway"
        )
    )

    score += {
        "path": 25,
        "bridleway": 20,
        "steps": 10,
        "footway": 3,
        "track": -4,
    }.get(
        highway,
        0,
    )

    if highway:
        reasons.append(
            f"highway={highway}"
        )

    if relation:
        score += 90

        reasons.append(
            "OSM hiking route relation"
        )

    return (
        score,
        reasons,
    )


def access_state(
    tags: dict[str, Any],
) -> tuple[
    str,
    int,
    list[str],
]:
    reasons: list[str] = []

    access = norm(
        tags.get(
            "access"
        )
    )

    foot = norm(
        tags.get(
            "foot"
        )
    )

    if access in {
        "no",
        "private",
        "military",
    }:
        return (
            "restricted",
            -65,
            [
                f"restricted access={access}"
            ],
        )

    if foot in {
        "no",
        "private",
    }:
        return (
            "restricted",
            -55,
            [
                f"restricted foot access={foot}"
            ],
        )

    if foot == "designated":
        reasons.append(
            "foot=designated"
        )

        return (
            "normal_or_unspecified",
            5,
            reasons,
        )

    if foot == "yes":
        reasons.append(
            "foot=yes"
        )

        return (
            "normal_or_unspecified",
            3,
            reasons,
        )

    return (
        "normal_or_unspecified",
        0,
        reasons,
    )


def name_signal(
    name: Any,
) -> tuple[
    int,
    list[str],
]:
    value = norm(
        name
    )

    if (
        not value
        or value in GENERIC_NAMES
    ):
        return (
            0,
            [
                "generic_or_unnamed OSM feature"
            ],
        )

    score = 12

    reasons = [
        "meaningful OSM name"
    ]

    if trailish_name(
        value
    ):
        score += 18

        reasons.append(
            "trail-like name"
        )

    if road_like_name(
        value
    ):
        score -= 24

        reasons.append(
            "infrastructure-like name"
        )

    return (
        score,
        reasons,
    )


def query_name_match(
    name: Any,
    search_query: str,
) -> tuple[
    int,
    list[str],
]:
    name_tokens = set(
        text_tokens(
            name
        )
    )

    query_tokens = set(search_tokens(search_query))

    if (
        not name_tokens
        or not query_tokens
    ):
        return (
            0,
            [],
        )

    overlap = (
        name_tokens
        & query_tokens
    )

    if not overlap:
        return (
            0,
            [],
        )

    if query_tokens.issubset(
        name_tokens
    ):
        return (
            55,
            [
                "search-name match"
            ],
        )

    return (
        min(
            35,
            12 * len(overlap),
        ),
        [
            "partial search-name match"
        ],
    )


def geometry_quality(
    coords: list[
        list[float]
    ],
) -> tuple[
    int,
    list[str],
    float,
    str,
]:
    if len(coords) < 2:
        return (
            -100,
            [
                "invalid geometry"
            ],
            0.0,
            "invalid",
        )

    length = line_length(
        coords
    )

    score = 0
    reasons: list[str] = []

    if length >= 5:
        score += 25

        reasons.append(
            "substantial mapped length"
        )

    elif length >= 1:
        score += 18

        reasons.append(
            "mapped length >= 1 km"
        )

    elif length >= 0.25:
        score += 8

        reasons.append(
            "usable short mapped length"
        )

    elif length < 0.10:
        score -= 28

        reasons.append(
            "tiny ordinary infrastructure segment"
        )

    else:
        score -= 10

        reasons.append(
            "short trail segment"
        )

    if len(coords) >= 8:
        score += 4

        reasons.append(
            "detailed geometry"
        )

    elif len(coords) <= 2:
        score -= 3

        reasons.append(
            "sparse geometry"
        )

    shape = (
        "loop"
        if (
            len(coords) >= 3
            and haversine(
                coords[0],
                coords[-1],
            )
            <= 0.06
        )
        else "line"
    )

    return (
        score,
        reasons,
        round(
            length,
            2,
        ),
        shape,
    )


def candidate_class(
    tags: dict[str, Any],
    relation: dict[str, Any] | None,
) -> str:
    highway = norm(
        tags.get(
            "highway"
        )
    )

    name = norm(
        tags.get(
            "name"
        )
    )

    route = norm(
        tags.get(
            "route"
        )
    )

    sac = norm(
        tags.get(
            "sac_scale"
        )
    )

    visibility = norm(
        tags.get(
            "trail_visibility"
        )
    )

    if (
        relation
        or route in HIKING_ROUTE_TYPES
        or sac
        or visibility
    ):
        return "hiking_tagged_way"

    if (
        highway == "path"
        and meaningful_name(
            name
        )
        and (
            trailish_name(
                name
            )
            or norm(
                tags.get(
                    "designation"
                )
            )
        )
    ):
        return "named_hiking_way"

    if highway == "path":
        return (
            "unnamed_path"
            if not meaningful_name(
                name
            )
            else "named_path"
        )

    if highway == "bridleway":
        return "bridleway"

    if highway == "steps":
        return "steps"

    if highway == "footway":
        return (
            "named_footway"
            if meaningful_name(
                name
            )
            else "unnamed_footway"
        )

    if highway == "track":
        return (
            "named_track"
            if meaningful_name(
                name
            )
            else "unnamed_track"
        )

    return "other"


def relevance_score_for_way(
    way: dict[str, Any],
    coords: list[
        list[float]
    ],
    latitude: float,
    longitude: float,
    search_query: str,
    relation: dict[str, Any] | None,
    area_search: bool = False,
) -> dict[str, Any]:
    tags = way.get(
        "tags"
    ) or {}

    highway = norm(
        tags.get(
            "highway"
        )
    )

    name = norm(
        tags.get(
            "name"
        )
    )

    candidate = candidate_class(
        tags,
        relation,
    )

    score = 80.0

    reasons: list[str] = []

    evidence, evidence_reasons = hiking_evidence(
        tags,
        relation,
    )

    score += evidence
    reasons.extend(
        evidence_reasons
    )

    score += {
        "path": 18,
        "bridleway": 14,
        "steps": 6,
        "footway": -8,
        "track": -12,
    }.get(
        highway,
        0,
    )

    if (
        highway == "footway"
        and norm(
            tags.get(
                "footway"
            )
        )
        in EXCLUDED_FOOTWAY_USES
    ):
        score -= 25

        reasons.append(
            "ordinary footway use"
        )

    name_score, name_reasons = name_signal(
        name
    )

    score += name_score

    reasons.extend(
        name_reasons
    )

    query_score, query_reasons = query_name_match(
        name,
        search_query,
    )

    score += query_score

    reasons.extend(
        query_reasons
    )

    access_status, access_penalty, access_reasons = access_state(
        tags
    )

    score += access_penalty

    reasons.extend(
        access_reasons
    )

    geometry_score, geometry_reasons, distance_km, shape = geometry_quality(
        coords
    )

    score += geometry_score
    reasons.extend(
        geometry_reasons
    )

    proximity_km = min_geometry_distance(
        coords,
        latitude,
        longitude,
    )

    if math.isfinite(
        proximity_km
    ):
        if area_search:
            score += (
                AREA_CENTER_PROXIMITY_MAX_BONUS
                * math.exp(
                    -proximity_km
                    / 8.0
                )
            )

        else:
            score += (
                34.0
                * math.exp(
                    -proximity_km
                    / 3.5
                )
            )

        if proximity_km <= 1.0:
            reasons.append(
                "very near searched place"
            )

        elif proximity_km <= 3.0:
            reasons.append(
                "near searched place"
            )

        elif (
            not area_search
            and proximity_km > 10.0
        ):
            score -= 8

            reasons.append(
                "far from searched place"
            )

    route = norm(
        tags.get(
            "route"
        )
    )

    sac = norm(
        tags.get(
            "sac_scale"
        )
    )

    visibility = norm(
        tags.get(
            "trail_visibility"
        )
    )

    if route in HIKING_ROUTE_TYPES:
        score += 35

    if sac:
        score += 20

    if visibility:
        score += 8

    surface = norm(
        tags.get(
            "surface"
        )
    )

    if surface in UNPAVED_SURFACES:
        score += 7

    elif surface in PAVED_SURFACES:
        score -= 4

    if area_search:
        if (
            highway == "footway"
            and not route
            and not sac
            and not visibility
        ):
            score -= 20

        if (
            highway == "track"
            and not route
            and not sac
            and not visibility
            and not trailish_name(
                name
            )
        ):
            score -= 18

        if (
            meaningful_name(
                name
            )
            and road_like_name(
                name
            )
            and not route
            and not sac
            and not visibility
        ):
            score -= 28

        if (
            meaningful_name(
                name
            )
            and distance_km < 0.15
            and not route
            and not sac
            and not visibility
            and not trailish_name(
                name
            )
        ):
            score -= 20

    if (
        meaningful_name(
            name
        )
        and road_like_name(
            name
        )
        and relation is None
        and highway == "footway"
    ):
        score -= 25

    return {
        "score": round(
            score,
            2,
        ),
        "relevance_band": (
            "high"
            if score >= 200
            else (
                "medium"
                if score >= 120
                else "low"
            )
        ),
        "relevance_reasons": reasons,
        "candidate_class": candidate,
        "access_status": access_status,
        "geometry_quality": (
            "good"
            if geometry_score >= 10
            else (
                "fair"
                if geometry_score > -10
                else "weak"
            )
        ),
        "distance_km": distance_km,
        "route_shape": (
            "loop"
            if shape == "loop"
            else "open_or_out_and_back"
        ),
    }


def make_way_candidate(
    way: dict[str, Any],
    nodes: dict[
        int,
        list[float],
    ],
    relation_evidence: dict[
        int,
        dict[str, Any],
    ],
    latitude: float,
    longitude: float,
    search_query: str,
    feature_center: tuple[
        float,
        float,
    ] | None = None,
    area_search: bool = False,
) -> dict[str, Any] | None:
    if not way_is_candidate(
        way
    ):
        return None

    coords = extract_way_geometry(
        way,
        nodes,
    )

    if len(coords) < 2:
        return None

    try:
        way_id = int(
            way.get(
                "id"
            )
            or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    relation = relation_evidence.get(
        way_id
    )

    relation_view = None

    if relation:
        relation_view = {
            "route_types": sorted(
                relation[
                    "route_types"
                ]
            ),
            "relation_ids": sorted(
                relation[
                    "relation_ids"
                ]
            ),
            "networks": sorted(
                relation[
                    "networks"
                ]
            ),
        }

    ranking = relevance_score_for_way(
        way,
        coords,
        latitude,
        longitude,
        search_query,
        relation_view,
        area_search=area_search,
    )

    if feature_center:
        feature_lat, feature_lon = feature_center

        feature_distance = min_geometry_distance(
            coords,
            feature_lat,
            feature_lon,
        )

        if math.isfinite(
            feature_distance
        ):
            ranking[
                "score"
            ] = round(
                ranking[
                    "score"
                ]
                + (
                    40.0
                    * math.exp(
                        -feature_distance
                        / 2.5
                    )
                ),
                2,
            )

            ranking[
                "feature_distance_km"
            ] = round(
                feature_distance,
                2,
            )

            if feature_distance <= 1.0:
                ranking[
                    "relevance_reasons"
                ].append(
                    "very near searched feature"
                )

            elif feature_distance <= 3.0:
                ranking[
                    "relevance_reasons"
                ].append(
                    "near searched feature"
                )

    tags = way.get(
        "tags"
    ) or {}

    center_lat, center_lon = center_from_coords(
        coords
    )

    start, end = route_endpoints(
        [
            coords
        ]
    )

    return {
        "osm_type": "way",
        "osm_id": way_id,
        "name": (
            tags.get(
                "name"
            )
            or "Unnamed hiking trail"
        ),
        "route_type": (
            relation_view[
                "route_types"
            ][0]
            if (
                relation_view
                and relation_view[
                    "route_types"
                ]
            )
            else (
                norm(
                    tags.get(
                        "route"
                    )
                )
                or None
            )
        ),
        "description": tags.get(
            "description"
        ),
        "difficulty": (
            tags.get(
                "sac_scale"
            )
            or tags.get(
                "difficulty"
            )
        ),
        "distance_km": ranking[
            "distance_km"
        ],
        "geometry": {
            "type": "LineString",
            "coordinates": coords,
        },
        "route_shape": ranking[
            "route_shape"
        ],
        "source": "OpenStreetMap",
        **ranking,
        "tags": tags,
        "highway": (
            norm(
                tags.get(
                    "highway"
                )
            )
            or None
        ),
        "center": {
            "latitude": center_lat,
            "longitude": center_lon,
        },
        "start_coordinate": start,
        "end_coordinate": end,
        "relation_evidence": relation_view,
        "_node_ids": [
            int(
                node_id
            )
            for node_id in (
                way.get(
                    "nodes"
                )
                or []
            )
            if str(
                node_id
            ).strip()
        ],
    }


# ============================================================
# CONNECTED SAME-NAME SEGMENT GROUPING
# ============================================================


_COMPONENT_ID_REGISTRY: dict[int, tuple[int, ...]] = {}


def generate_component_id(
    member_way_ids: Any,
) -> int:
    """
    Generate a deterministic, process-stable, collision-resistant integer ID
    from a canonical set of member way IDs.

    - Canonicalized by sorting unique integer IDs.
    - Derived via SHA-256 hash so it is completely deterministic across
      requests, processes, and restarts (no Python hash() or random values).
    - Fits within JavaScript Number.MAX_SAFE_INTEGER (52-bit positive int)
      so frontend JSON parsing never loses precision.
    - Includes a lightweight runtime registry safeguard to prevent two distinct
      canonical sets from silently colliding within the same process.
    """
    try:
        canonical = tuple(
            sorted(
                dict.fromkeys(
                    int(wid)
                    for wid in member_way_ids
                    if wid is not None
                )
            )
        )
    except (TypeError, ValueError):
        return 0

    if not canonical:
        return 0

    raw_key = "component:" + ",".join(str(wid) for wid in canonical)
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    # Use 52 bits (13 hex characters) to guarantee it fits safely within
    # JavaScript Number.MAX_SAFE_INTEGER (2^53 - 1 = 9007199254740991)
    base_id = int(digest[:13], 16)

    existing = _COMPONENT_ID_REGISTRY.get(base_id)
    if existing is not None:
        if existing == canonical:
            return base_id
        raise ValueError("Deterministic component ID collision detected")

    _COMPONENT_ID_REGISTRY[base_id] = canonical
    return base_id


def group_connected_named_candidates(
    candidates: list[
        dict[str, Any]
    ],
) -> list[
    dict[str, Any]
]:
    grouped: dict[
        tuple[
            str,
            str,
        ],
        list[
            dict[str, Any]
        ],
    ] = {}

    passthrough: list[
        dict[str, Any]
    ] = []

    for candidate in candidates:
        name = norm(
            candidate.get(
                "name"
            )
        )

        highway = norm(
            candidate.get(
                "highway"
            )
        )

        node_ids = (
            candidate.get(
                "_node_ids"
            )
            or []
        )

        if (
            meaningful_name(
                name
            )
            and highway
            and node_ids
        ):
            grouped.setdefault(
                (
                    name,
                    highway,
                ),
                [],
            ).append(
                candidate
            )

        else:
            passthrough.append(
                candidate
            )

    result: list[
        dict[str, Any]
    ] = []

    for candidate in passthrough:
        cleaned = dict(
            candidate
        )

        cleaned.pop(
            "_node_ids",
            None,
        )

        result.append(
            cleaned
        )

    for (
        group_key,
        members,
    ) in grouped.items():
        del group_key

        if len(members) == 1:
            cleaned = dict(
                members[0]
            )

            cleaned.pop(
                "_node_ids",
                None,
            )

            result.append(
                cleaned
            )

            continue

        parent = list(
            range(
                len(members)
            )
        )

        def find(
            index: int,
        ) -> int:
            while (
                parent[index]
                != index
            ):
                parent[index] = parent[
                    parent[index]
                ]

                index = parent[
                    index
                ]

            return index

        def union(
            first: int,
            second: int,
        ) -> None:
            first_root = find(
                first
            )

            second_root = find(
                second
            )

            if first_root != second_root:
                parent[
                    second_root
                ] = first_root

        node_map: dict[
            int,
            list[int],
        ] = {}

        for index, member in enumerate(
            members
        ):
            for raw_node_id in (
                member.get(
                    "_node_ids"
                )
                or []
            ):
                try:
                    node_id = int(
                        raw_node_id
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    continue

                node_map.setdefault(
                    node_id,
                    [],
                ).append(
                    index
                )

        for indices in node_map.values():
            for index in indices[
                1:
            ]:
                union(
                    indices[0],
                    index,
                )

        components: dict[
            int,
            list[int],
        ] = {}

        for index in range(
            len(members)
        ):
            components.setdefault(
                find(index),
                [],
            ).append(
                index
            )

        for component in (
            components.values()
        ):
            if len(component) == 1:
                cleaned = dict(
                    members[
                        component[0]
                    ]
                )

                cleaned.pop(
                    "_node_ids",
                    None,
                )

                result.append(
                    cleaned
                )

                continue

            selected = [
                members[index]
                for index in component
            ]

            representative = max(
                selected,
                key=lambda item: (
                    float(
                        item.get(
                            "score"
                        )
                        or -9999
                    ),
                    float(
                        item.get(
                            "distance_km"
                        )
                        or 0
                    ),
                ),
            )

            merged = dict(
                representative
            )

            geometries: list[
                list[
                    list[float]
                ]
            ] = []

            for member in selected:
                geometry = (
                    member.get(
                        "geometry"
                    )
                    or {}
                )

                coords = geometry.get(
                    "coordinates"
                )

                if (
                    isinstance(
                        coords,
                        list,
                    )
                    and len(coords) >= 2
                ):
                    cleaned_coords = dedupe_coordinates(
                        coords
                    )

                    if len(
                        cleaned_coords
                    ) >= 2:
                        geometries.append(
                            cleaned_coords
                        )

            if geometries:
                merged[
                    "geometry"
                ] = geometry_object(
                    geometries
                )

                if len(geometries) > 1:
                    merged[
                        "route_shape"
                    ] = "multiline"

                merged[
                    "distance_km"
                ] = round(
                    sum(
                        line_length(
                            geometry
                        )
                        for geometry in geometries
                    ),
                    2,
                )

                if len(geometries) == 1:
                    merged[
                        "start_coordinate"
                    ] = geometries[0][0]

                    merged[
                        "end_coordinate"
                    ] = geometries[0][-1]

            member_way_ids = sorted(
                int(
                    member.get(
                        "osm_id"
                    )
                    or 0
                )
                for member in selected
            )

            merged[
                "member_way_ids"
            ] = member_way_ids

            merged[
                "osm_type"
            ] = "component"

            merged[
                "osm_id"
            ] = generate_component_id(
                member_way_ids
            )

            merged[
                "segment_count"
            ] = len(
                selected
            )

            reasons = list(
                merged.get(
                    "relevance_reasons"
                )
                or []
            )

            reasons.append(
                (
                    f"combined {len(selected)} "
                    "connected OSM way segments"
                )
            )

            merged[
                "relevance_reasons"
            ] = reasons

            endpoint_values = [
                float(
                    member[
                        "feature_endpoint_distance_km"
                    ]
                )
                for member in selected
                if member.get(
                    "feature_endpoint_distance_km"
                )
                is not None
            ]

            if endpoint_values:
                merged[
                    "feature_endpoint_distance_km"
                ] = round(
                    min(
                        endpoint_values
                    ),
                    2,
                )

            merged.pop(
                "_node_ids",
                None,
            )

            result.append(
                merged
            )

    return result


def dedupe_candidates(
    candidates: list[
        dict[str, Any]
    ],
) -> list[
    dict[str, Any]
]:
    seen: set[
        tuple[
            str,
            int,
        ]
    ] = set()

    result: list[
        dict[str, Any]
    ] = []

    for candidate in candidates:
        try:
            osm_id = int(
                candidate.get(
                    "osm_id"
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        key = (
            str(
                candidate.get(
                    "osm_type"
                )
                or "way"
            ),
            osm_id,
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        result.append(
            candidate
        )

    return result


# ============================================================
# FEATURE GEOCODING
# ============================================================


async def geocode_feature(
    query: str,
) -> list[
    dict[str, Any]
]:
    key = norm(
        query
    )

    cached = _GEOCODE_CACHE.get(
        key
    )

    if (
        cached
        and (
            time.monotonic()
            - cached[0]
            <= GEOCODE_CACHE_TTL_SECONDS
        )
    ):
        return cached[1]

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                NOMINATIM_TIMEOUT
            ),
            headers=HEADERS,
        ) as client:
            response = await client.get(
                NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 10,
                    "addressdetails": 1,
                    "extratags": 1,
                    "namedetails": 1,
                },
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(
                data,
                list,
            ):
                return []

            _GEOCODE_CACHE[
                key
            ] = (
                time.monotonic(),
                data,
            )

            return data

    except Exception:
        return []


def looks_like_feature(
    result: dict[str, Any],
) -> bool:
    result_type = norm(result.get("type"))
    address_type = norm(result.get("addresstype"))
    result_class = norm(result.get("class"))
    natural = norm(
        (result.get("extratags") or {}).get("natural")
    )

    display_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", norm(result.get("display_name")))
    }

    return (
        result_type in FEATURE_TYPES
        or address_type in FEATURE_TYPES
        or natural in FEATURE_TYPES
        or (
            result_class in {"natural", "tourism"}
            and bool(display_tokens & FEATURE_TYPES)
        )
    )




def feature_matches_query(
    result: dict[str, Any],
    search_query: str,
) -> bool:
    query_tokens = set(search_tokens(search_query))

    result_tokens = set(
        search_tokens(
            (
                str(result.get("name") or "")
                + " "
                + str(result.get("display_name") or "")
            )
        )
    )

    return bool(
        query_tokens
        and result_tokens
        and query_tokens
        & result_tokens
    )


def select_feature_result(
    results: list[
        dict[str, Any]
    ],
    search_query: str,
) -> dict[str, Any] | None:
    matches = [
        result
        for result in results
        if looks_like_feature(
            result
        )
        and feature_matches_query(
            result,
            search_query,
        )
    ]

    if not matches:
        return None

    query_tokens = set(
        text_tokens(
            search_query
        )
    )

    def score(
        result: dict[str, Any],
    ) -> tuple[
        int,
        int,
        int,
        float,
    ]:
        name_tokens = set(search_tokens(result.get("name") or ""))
        display_tokens = set(search_tokens(result.get("display_name") or ""))

        exact = int(
            bool(
                query_tokens
            )
            and query_tokens.issubset(
                name_tokens
            )
        )

        name_overlap = len(
            query_tokens
            & name_tokens
        )

        display_overlap = len(
            query_tokens
            & display_tokens
        )

        try:
            importance = float(
                result.get(
                    "importance"
                )
                or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            importance = 0.0

        return (
            exact,
            name_overlap,
            display_overlap,
            importance,
        )

    return max(
        matches,
        key=score,
    )
def _geocoder_match_score(
    result: dict[str, Any],
    search_query: str,
) -> tuple[int, int, int, float]:
    query_tokens = set(search_tokens(search_query))
    name_tokens = set(search_tokens(result.get("name") or ""))
    display_tokens = set(search_tokens(result.get("display_name") or ""))

    exact = int(bool(query_tokens) and query_tokens.issubset(name_tokens))
    name_overlap = len(query_tokens & name_tokens)
    display_overlap = len(query_tokens & display_tokens)

    try:
        importance = float(result.get("importance") or 0)
    except (TypeError, ValueError):
        importance = 0.0

    return exact, name_overlap, display_overlap, importance


def classify_search_result(
    results: list[dict[str, Any]],
    search_query: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    feature = select_feature_result(results, search_query)
    if feature is not None:
        return "feature", feature, feature

    compact_candidates: list[dict[str, Any]] = []
    broad_candidates: list[dict[str, Any]] = []

    for result in results:
        result_type = norm(result.get("type"))
        address_type = norm(result.get("addresstype"))

        if result_type in BROAD_AREA_TYPES or address_type in BROAD_AREA_TYPES:
            broad_candidates.append(result)
        elif result_type in COMPACT_AREA_TYPES or address_type in COMPACT_AREA_TYPES:
            compact_candidates.append(result)

    if compact_candidates:
        best = max(
            compact_candidates,
            key=lambda result: _geocoder_match_score(result, search_query),
        )
        return "compact_area", best, None

    if broad_candidates:
        best = max(
            broad_candidates,
            key=lambda result: _geocoder_match_score(result, search_query),
        )
        return "broad_area", best, None

    if results:
        best = max(
            results,
            key=lambda result: _geocoder_match_score(result, search_query),
        )
        best_bbox = parse_nominatim_bbox(best.get("boundingbox"))
        if best_bbox is not None:
            area_km2 = bbox_area_km2(best_bbox)
            return (
                "compact_area" if area_km2 <= 400.0 else "broad_area",
                best,
                None,
            )

    return "radius", None, None




# ============================================================
# FEATURE-SPECIFIC CONNECTED NETWORK
# ============================================================


def build_feature_access_route(
    feature_name: str,
    feature_type: str,
    feature_lat: float,
    feature_lon: float,
    ways: list[dict[str, Any]],
    nodes: dict[int, list[float]],
) -> dict[str, Any] | None:
    """Build a derived connected hiking route only as a feature-search fallback."""
    feature_point = [feature_lon, feature_lat]

    eligible: dict[int, tuple[dict[str, Any], list[int]]] = {}
    node_to_way: dict[int, set[int]] = {}

    for way in ways:
        if not way_is_candidate(way):
            continue

        tags = way.get("tags") or {}
        highway = norm(tags.get("highway"))
        if (
            highway == "footway"
            and norm(tags.get("footway")) in EXCLUDED_FOOTWAY_USES
        ):
            continue

        try:
            way_id = int(way.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not way_id:
            continue

        ids: list[int] = []
        for raw_node_id in way.get("nodes") or []:
            try:
                node_id = int(raw_node_id)
            except (TypeError, ValueError):
                continue
            if node_id in nodes:
                ids.append(node_id)

        if len(ids) < 2:
            continue

        eligible[way_id] = (way, ids)
        for node_id in ids:
            node_to_way.setdefault(node_id, set()).add(way_id)

    if not eligible:
        return None

    seed_node: int | None = None
    seed_distance = float("inf")
    for node_id in nodes:
        if not node_to_way.get(node_id):
            continue
        distance = haversine(nodes[node_id], feature_point)
        if distance < seed_distance and distance <= 0.35:
            seed_node = node_id
            seed_distance = distance

    if seed_node is None:
        return None

    adjacency: dict[int, list[tuple[int, float, int]]] = {}
    component_ways: set[int] = set()
    queue = [seed_node]
    seen_nodes: set[int] = set()

    while queue and len(component_ways) < 250:
        node_id = queue.pop()
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)

        for way_id in node_to_way.get(node_id, set()):
            if way_id not in eligible:
                continue
            component_ways.add(way_id)
            _way, ids = eligible[way_id]

            for index in range(len(ids) - 1):
                a = ids[index]
                b = ids[index + 1]
                weight = haversine(nodes[a], nodes[b])

                adjacency.setdefault(a, []).append((b, weight, way_id))
                adjacency.setdefault(b, []).append((a, weight, way_id))

                if b not in seen_nodes:
                    queue.append(b)

            if len(component_ways) >= 250:
                break

    if not adjacency:
        return None

    distances: dict[int, float] = {seed_node: 0.0}
    previous: dict[int, tuple[int, int]] = {}
    heap: list[tuple[float, int]] = [(0.0, seed_node)]
    visited_count = 0

    while heap and visited_count < 5000:
        current_distance, node_id = heapq.heappop(heap)
        if current_distance != distances.get(node_id):
            continue
        visited_count += 1

        for next_node, weight, way_id in adjacency.get(node_id, []):
            new_distance = current_distance + weight
            if new_distance > 12.0:
                continue
            if new_distance < distances.get(next_node, float("inf")):
                distances[next_node] = new_distance
                previous[next_node] = (node_id, way_id)
                heapq.heappush(heap, (new_distance, next_node))

    endpoints = [
        node_id
        for node_id in distances
        if sum(
            1
            for neighbor, _weight, _way_id in adjacency.get(node_id, [])
            if neighbor in distances
        ) == 1
    ] or list(distances)

    if not endpoints:
        return None

    end_node = max(
        endpoints,
        key=lambda node_id: distances.get(node_id, -1.0),
    )
    route_distance = distances.get(end_node, 0.0)
    if route_distance < 0.25:
        return None

    node_path: list[int] = []
    way_path: list[int] = []
    current = end_node

    while current != seed_node:
        if current not in previous:
            return None
        previous_node, way_id = previous[current]
        node_path.append(current)
        way_path.append(way_id)
        current = previous_node

    node_path.append(seed_node)
    node_path.reverse()
    way_path.reverse()

    coords = [nodes[node_id] for node_id in node_path if node_id in nodes]
    if len(coords) < 2:
        return None

    member_way_ids = list(dict.fromkeys(way_path))
    canonical_member_ids = sorted(set(member_way_ids))

    names: list[str] = []
    sac_scales: list[str] = []
    for way_id in member_way_ids:
        way = eligible[way_id][0]
        tags = way.get("tags") or {}

        name = str(tags.get("name") or "").strip()
        if meaningful_name(name) and not road_like_name(name):
            names.append(name)

        sac = norm(tags.get("sac_scale"))
        if sac:
            sac_scales.append(sac)

    route_name = names[0] if names else f"Hiking route near {feature_name}"

    endpoint_distance = haversine(coords[0], feature_point)
    if endpoint_distance <= 0.40:
        association = "direct"
    elif endpoint_distance <= 0.80:
        association = "strong"
    elif endpoint_distance <= 1.50:
        association = "good"
    else:
        association = "supporting"

    association_score = {
        "direct": 420,
        "strong": 250,
        "good": 160,
        "supporting": 80,
    }[association]

    score = (
        220.0
        + association_score
        + min(85.0, route_distance * 6.0)
        + (75.0 if sac_scales else 0.0)
        + (18.0 if meaningful_name(route_name) else 0.0)
    )

    center_lat, center_lon = center_from_coords(coords)
    component_id = generate_component_id(canonical_member_ids)

    return {
        "osm_type": "component",
        "osm_id": component_id,
        "canonical_component_id": component_id,
        "name": route_name,
        "name_source": "derived_from_connected_osm_members",
        "route_type": "hiking",
        "description": None,
        "difficulty": sac_scales[0] if sac_scales else "hiking",
        "distance_km": round(line_length(coords), 2),
        "geometry": {
            "type": "LineString",
            "coordinates": coords,
        },
        "route_shape": "open_or_out_and_back",
        "source": "OpenStreetMap",
        "score": round(score, 2),
        "relevance_band": "high",
        "relevance_reasons": [
            "derived fallback from connected OSM hiking-capable ways",
            "feature-focused trail network",
        ],
        "access_status": "normal_or_unspecified",
        "candidate_class": "feature_access_hiking_route",
        "geometry_quality": "good",
        "feature_distance_km": round(endpoint_distance, 2),
        "feature_endpoint_distance_km": round(endpoint_distance, 2),
        "feature_association_level": association,
        "feature_association_score": association_score,
        "association_reasons": [
            (
                "trail network reaches searched feature vicinity"
                if association == "direct"
                else "trail network approaches searched feature"
            )
        ],
        "feature_name": feature_name,
        "feature_type": feature_type,
        "member_way_ids": canonical_member_ids,
        "ordered_way_ids": member_way_ids,
        "segment_count": len(member_way_ids),
        "tags": {
            "route": "hiking",
            "source": "connected_osm_way_network",
        },
        "highway": "route",
        "center": {
            "latitude": center_lat,
            "longitude": center_lon,
        },
        "start_coordinate": coords[0],
        "end_coordinate": coords[-1],
        "relation_evidence": None,
    }




# ============================================================
# RESPONSE / RANKING
# ============================================================


def is_relevant_area_candidate(
    candidate: dict[str, Any],
) -> bool:
    """Keep meaningful trail candidates for named-area searches."""
    candidate_type = norm(candidate.get("osm_type"))
    candidate_class_name = norm(candidate.get("candidate_class"))
    route_type = norm(candidate.get("route_type"))
    name = candidate.get("name")
    highway = norm(candidate.get("highway"))

    if candidate_type == "relation":
        return bool(candidate.get("geometry"))

    if route_type in HIKING_ROUTE_TYPES:
        return True

    if candidate_class_name in {
        "hiking_tagged_way",
        "hiking_route",
    }:
        return True

    if candidate.get("relation_evidence"):
        return True

    if not meaningful_name(name) or road_like_name(name):
        return (
            highway in {"path", "bridleway", "steps"}
            and float(candidate.get("score") or 0) >= 120.0
        )

    if highway in TRAIL_HIGHWAYS:
        return True

    if highway in POSSIBLE_TRAIL_HIGHWAYS:
        if trailish_name(name):
            return True
        return candidate_class_name in {
            "named_footway",
            "named_track",
        } and float(candidate.get("score") or 0) >= 120.0

    return False


def presentation_tier(
    candidate: dict[str, Any],
) -> int:
    """
    Rank area-search candidates by trail quality for presentation.

    This function only controls ordering of already-discovered candidates.
    It does not change OSM coverage, candidate discovery, candidate scores,
    feature-network logic, or the requested search bbox.

    Tier meaning:
        0 = named trail-like candidate
        1 = explicit hiking-tagged candidate
        2 = named hiking/path candidate without enough evidence for tier 0
        3 = ordinary or infrastructure-like candidate
        4 = restricted candidate
    """
    name = norm(
        candidate.get(
            "name"
        )
    )

    highway = norm(
        candidate.get(
            "highway"
        )
    )

    candidate_class_name = norm(
        candidate.get(
            "candidate_class"
        )
    )

    length = float(
        candidate.get(
            "distance_km"
        )
        or 0
    )

    if (
        candidate.get(
            "access_status"
        )
        == "restricted"
    ):
        return 4

    # Infrastructure/road-like names are kept below genuine trail
    # candidates. This is presentation ordering only.
    if road_like_name(
        name
    ):
        return 3

    # A meaningful trail-like name on a trail-capable highway is the
    # strongest general area-search signal. Keep the existing length
    # safeguard so tiny incidental segments do not dominate results.
    if (
        meaningful_name(
            name
        )
        and trailish_name(
            name
        )
        and highway in TRAIL_HIGHWAYS
        and (
            length >= 0.5
            or candidate_class_name
            == "hiking_tagged_way"
        )
    ):
        return 0

    # Explicit OSM hiking evidence includes route membership,
    # sac_scale, and trail_visibility through candidate classification.
    # This deliberately outranks ordinary named paths, including urban
    # shortcuts that merely happen to be mapped as highway=path.
    if candidate_class_name in {
        "hiking_tagged_way",
        "hiking_route",
    }:
        return 1

    # Shorter named trail-like ways remain useful even when they did not
    # meet the tier-0 length safeguard above.
    if candidate_class_name == "named_hiking_way":
        return 2

    return 3


def sort_candidates(
    candidates: list[
        dict[str, Any]
    ],
    area_search: bool = False,
) -> list[
    dict[str, Any]
]:
    if area_search:
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                presentation_tier(
                    candidate
                ),
                -float(
                    candidate.get(
                        "score"
                    )
                    or -9999
                ),
                float(
                    candidate.get(
                        "distance_km"
                    )
                    or 0
                ),
                int(
                    candidate.get(
                        "osm_id"
                    )
                    or 0
                ),
            ),
        )

    else:
        association_order = {
            "direct": 0,
            "strong": 1,
            "good": 2,
            "supporting": 3,
        }

        ordered = sorted(
            candidates,
            key=lambda candidate: (
                -float(
                    candidate.get(
                        "score"
                    )
                    or -9999
                ),
                association_order.get(
                    candidate.get(
                        "feature_association_level"
                    ),
                    4,
                ),
                float(
                    candidate.get(
                        "feature_endpoint_distance_km"
                    )
                    or 999999
                ),
                int(
                    candidate.get(
                        "osm_id"
                    )
                    or 0
                ),
            ),
        )

    for index, candidate in enumerate(
        ordered,
        start=1,
    ):
        candidate[
            "rank"
        ] = index

    return ordered


def map_trail(
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "osm_type": candidate.get(
            "osm_type"
        ),
        "osm_id": candidate.get(
            "osm_id"
        ),
        "name": candidate.get(
            "name"
        ),
        "geometry": candidate.get(
            "geometry"
        ),
        "route_shape": candidate.get(
            "route_shape"
        ),
        "distance_km": candidate.get(
            "distance_km"
        ),
        "score": candidate.get(
            "score"
        ),
        "relevance_band": candidate.get(
            "relevance_band"
        ),
    }


# ============================================================
# STEP 5 – RELATION PROMOTION
# ============================================================


def _safe_int(value: Any) -> int | None:
    """Return int(value) or None on failure — helper for relation promotion."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _promote_hiking_relations(
    elements: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]],
    latitude: float,
    longitude: float,
    search_query: str,
    area_search: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Promote a bounded number of real OSM hiking relations to candidates."""
    relation_elements = [
        element
        for element in elements
        if element.get("type") == "relation"
        and norm((element.get("tags") or {}).get("route")) in HIKING_ROUTE_TYPES
    ]

    total_discovered = len(relation_elements)
    if not total_discovered:
        return [], {
            "hiking_relations_discovered": 0,
            "relations_needing_hydration": 0,
            "relations_hydrated": 0,
            "hydration_errors": [],
            "relation_way_candidates_checked": 0,
        }

    flat_way_ids = {
        wid
        for element in elements
        if element.get("type") == "way"
        for wid in [_safe_int(element.get("id"))]
        if wid is not None
    }

    already_promoted = {
        rid
        for candidate in existing_candidates
        if candidate.get("osm_type") == "relation"
        for rid in [_safe_int(candidate.get("osm_id"))]
        if rid is not None
    }

    scored: list[tuple[int, int, int, int, dict[str, Any]]] = []
    for relation in relation_elements:
        relation_id = _safe_int(relation.get("id"))
        if relation_id is None or relation_id in already_promoted:
            continue

        tags = relation.get("tags") or {}
        name = tags.get("name")
        name_score, _ = query_name_match(name, search_query)
        covered = sum(
            1
            for member in relation.get("members") or []
            if member.get("type") == "way"
            and _safe_int(member.get("ref")) in flat_way_ids
        )
        named = int(meaningful_name(name))
        scored.append(
            (-name_score, -named, -covered, relation_id, relation)
        )

    heapq.heapify(scored)
    relations_needing_hydration = len(scored)
    budget = min(relations_needing_hydration, RELATION_PROMOTION_BUDGET)
    selected = [heapq.heappop(scored) for _ in range(budget)]

    semaphore = asyncio.Semaphore(RELATION_HYDRATION_CONCURRENCY)

    async def hydrate(
        relation_id: int,
    ) -> tuple[int, dict[str, Any] | None, dict[str, Any] | None, str | None]:
        async with semaphore:
            try:
                full_data = await fetch_osm_full("relation", relation_id)
            except Exception as exc:
                return relation_id, None, None, f"relation/{relation_id}: fetch failed - {exc}"

            try:
                parsed = _parse_full_relation_payload(
                    full_data,
                    relation_id=relation_id,
                )
            except Exception as exc:
                return relation_id, None, None, f"relation/{relation_id}: parse failed - {exc}"

            if not parsed:
                return relation_id, None, None, f"relation/{relation_id}: no parseable content"

            try:
                assembled = _assemble_relation_geometry(parsed)
            except Exception as exc:
                return relation_id, None, None, f"relation/{relation_id}: geometry assembly failed - {exc}"

            if not assembled:
                return relation_id, None, None, f"relation/{relation_id}: geometry assembly returned None"

            return relation_id, parsed, assembled, None

    hydration_results = await asyncio.gather(
        *(hydrate(item[3]) for item in selected)
    )

    promoted: list[dict[str, Any]] = []
    hydration_errors: list[str] = []
    relations_hydrated = 0
    way_candidates_checked = 0

    for relation_id, parsed, assembled, error in hydration_results:
        if error is not None:
            hydration_errors.append(error)
            continue

        if parsed is None or assembled is None:
            continue

        relations_hydrated += 1

        tags = parsed.get("tags") or {}
        rel_name = parsed.get("name")
        raw_members = parsed.get("member_ways") or []
        member_way_ids = sorted(
            {
                int(member["way_id"])
                for member in raw_members
                if member.get("way_id") is not None
            }
        )
        way_candidates_checked += len(member_way_ids)

        geometry = assembled.get("geometry")
        segments: list[list[list[float]]] = []
        if isinstance(geometry, dict):
            geometry_type = geometry.get("type")
            coordinates = geometry.get("coordinates") or []
            if geometry_type == "LineString" and coordinates:
                segments = [coordinates]
            elif geometry_type == "MultiLineString":
                segments = [segment for segment in coordinates if segment]

        primary_segment = max(
            segments,
            key=line_length,
            default=[],
        )
        proximity_points = [
            point
            for segment in segments
            for point in segment
        ]

        geo_score, geo_reasons, _segment_distance, _shape = geometry_quality(
            primary_segment
        )

        score = 80.0 + geo_score
        reasons = list(geo_reasons)
        reasons.append("promoted hiking relation")

        name_score, name_reasons = name_signal(rel_name)
        score += name_score
        reasons.extend(name_reasons)

        query_score, query_reasons = query_name_match(rel_name, search_query)
        score += query_score
        reasons.extend(query_reasons)

        access_status, access_penalty, access_reasons = access_state(tags)
        score += access_penalty
        reasons.extend(access_reasons)

        route = norm(tags.get("route"))
        sac = norm(tags.get("sac_scale"))
        visibility = norm(tags.get("trail_visibility"))
        surface = norm(tags.get("surface"))

        if route in HIKING_ROUTE_TYPES:
            score += 35.0
        if sac:
            score += 20.0
            reasons.append(f"sac_scale={sac}")
        if visibility:
            score += 8.0
            reasons.append(f"trail_visibility={visibility}")
        if surface in UNPAVED_SURFACES:
            score += 7.0
            reasons.append(f"unpaved surface={surface}")
        elif surface in PAVED_SURFACES:
            score -= 4.0
            reasons.append(f"paved surface={surface}")

        proximity_km = min_geometry_distance(
            proximity_points,
            latitude,
            longitude,
        )
        if math.isfinite(proximity_km):
            if area_search:
                score += AREA_CENTER_PROXIMITY_MAX_BONUS * math.exp(
                    -proximity_km / 8.0
                )
            else:
                score += 34.0 * math.exp(
                    -proximity_km / 3.5
                )

            if proximity_km <= 1.0:
                reasons.append("very near searched place")
            elif proximity_km <= 3.0:
                reasons.append("near searched place")
            elif not area_search and proximity_km > 10.0:
                score -= 8.0
                reasons.append("far from searched place")

        route_shape_value = assembled.get("route_shape") or "unknown"
        center_lat, center_lon = center_from_coords(proximity_points)

        promoted.append(
            {
                "osm_type": "relation",
                "osm_id": relation_id,
                "name": rel_name,
                "route_type": route or "hiking",
                "network": str(tags.get("network") or "").strip() or None,
                "symbol": str(
                    tags.get("symbol")
                    or tags.get("osmc:symbol")
                    or ""
                ).strip() or None,
                "ref": str(tags.get("ref") or "").strip() or None,
                "description": tags.get("description"),
                "difficulty": tags.get("sac_scale") or tags.get("difficulty"),
                "tags": tags,
                "member_way_ids": member_way_ids,
                "ordered_way_ids": assembled.get("ordered_way_ids") or [],
                "geometry": geometry,
                "route_shape": route_shape_value,
                "distance_km": assembled.get("distance_km"),
                "start_coordinate": assembled.get("start_coordinate"),
                "end_coordinate": assembled.get("end_coordinate"),
                "is_loop": assembled.get("is_loop"),
                "endpoint_available": assembled.get("endpoint_available"),
                "center": {
                    "latitude": center_lat,
                    "longitude": center_lon,
                },
                "source": "OpenStreetMap",
                "score": round(score, 2),
                "relevance_band": (
                    "high"
                    if score >= 200
                    else "medium"
                    if score >= 120
                    else "low"
                ),
                "relevance_reasons": reasons,
                "candidate_class": "hiking_route",
                "access_status": access_status,
                "geometry_quality": (
                    "good"
                    if geo_score >= 10
                    else "fair"
                    if geo_score > -10
                    else "weak"
                ),
                "relation_evidence": None,
                "highway": None,
            }
        )

    return promoted, {
        "hiking_relations_discovered": total_discovered,
        "relations_needing_hydration": relations_needing_hydration,
        "relations_hydrated": relations_hydrated,
        "hydration_errors": hydration_errors[:20],
        "relation_way_candidates_checked": way_candidates_checked,
    }




def finalize_response(
    query: str,
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
    candidates: list[
        dict[str, Any]
    ],
    acquisition: dict[str, Any],
    raw_element_count: int,
    point_like_search: bool,
    extra_metadata: dict[str, Any],
    area_search: bool = False,
) -> dict[str, Any]:
    unique_candidates = dedupe_candidates(candidates)

    if area_search:
        relevant_candidates = [
            candidate
            for candidate in unique_candidates
            if is_relevant_area_candidate(candidate)
        ]
        if relevant_candidates:
            unique_candidates = relevant_candidates

    ordered = sort_candidates(
        unique_candidates,
        area_search=area_search,
    )

    for candidate in ordered:
        candidate.pop(
            "_node_ids",
            None,
        )

    failed_tiles = int(
        acquisition.get(
            "failed_tiles",
            0,
        )
        or 0
    )

    provider_errors = (
        acquisition.get(
            "provider_errors"
        )
        or []
    )

    if (
        provider_errors
        and not ordered
    ):
        coverage_status = "failed"
        status = "error"

    elif failed_tiles:
        coverage_status = "partial"
        status = (
            "success"
            if ordered
            else "partial"
        )

    else:
        coverage_status = "complete"
        status = "success"

    return {
        "source": "OpenStreetMap",
        "query": query,
        "bbox": list(
            bbox
        ),
        "count": len(
            ordered
        ),
        "trails": ordered,
        "map_trails": [
            map_trail(
                candidate
            )
            for candidate in ordered
        ],
        "metadata": {
            **acquisition,
            "raw_osm_elements": raw_element_count,
            "trail_candidates": len(
                ordered
            ),
            "bbox": list(
                bbox
            ),
            "point_like_search": point_like_search,
            "area_search": area_search,
            **extra_metadata,
        },
        "coverage_status": coverage_status,
        "status": status,
    }


# ============================================================
# MAIN TRAIL DISCOVERY ENDPOINT
# ============================================================


@router.get(
    "/trails"
)
async def discover_trails(
    latitude: float = Query(...),
    longitude: float = Query(...),
    radius_m: int = Query(
        DEFAULT_RADIUS_M,
        ge=1000,
        le=30000,
    ),
    query: str = Query(
        "",
        max_length=200,
    ),
    search_query: str = Query(
        "",
        max_length=200,
    ),
    location_name: str = Query(
        "",
        max_length=300,
    ),
    scope: str = Query(
        "local",
        pattern="^(local|area)$",
    ),
    bbox: str | None = Query(
        default=None
    ),
):
    del scope

    requested_query = (
        query.strip()
        or search_query.strip()
        or location_name.strip()
    )

    explicit_bbox = parse_bbox(
        bbox
    )

    resolved_place: dict[
        str,
        Any,
    ] | None = None

    search_kind = "radius"
    selected_search_result: dict[str, Any] | None = None
    selected_feature_result: dict[str, Any] | None = None

    search_bbox = explicit_bbox

    geocoded = (
        await geocode_feature(requested_query)
        if requested_query
        else []
    )

    if geocoded:
        (
            search_kind,
            selected_search_result,
            selected_feature_result,
        ) = classify_search_result(
            geocoded,
            requested_query,
        )

    point_like_search = search_kind == "feature"

    if search_bbox is None and selected_search_result is not None:
        best = selected_search_result

        try:
            best_lat = float(best.get("lat"))
            best_lon = float(best.get("lon"))
        except (TypeError, ValueError):
            best_lat = latitude
            best_lon = longitude

        parsed = parse_nominatim_bbox(best.get("boundingbox"))

        if parsed is not None:
            search_bbox = parsed
            latitude = best_lat
            longitude = best_lon

            resolved_place = {
                "display_name": best.get("display_name"),
                "osm_type": best.get("osm_type"),
                "osm_id": best.get("osm_id"),
                "lat": best_lat,
                "lon": best_lon,
                "search_kind": search_kind,
            }

            west, south, east, north = search_bbox
            width = east - west
            height = north - south

            if search_kind == "feature" and width < 0.015 and height < 0.015:
                search_bbox = (
                    max(-180.0, best_lon - POINT_SEARCH_RADIUS_DEGREES),
                    max(-90.0, best_lat - POINT_SEARCH_RADIUS_DEGREES),
                    min(180.0, best_lon + POINT_SEARCH_RADIUS_DEGREES),
                    min(90.0, best_lat + POINT_SEARCH_RADIUS_DEGREES),
                )
            elif search_kind == "compact_area" and (
                width < COMPACT_AREA_HALF_SIZE_DEGREES * 2
                or height < COMPACT_AREA_HALF_SIZE_DEGREES * 2
            ):
                search_bbox = (
                    max(-180.0, best_lon - COMPACT_AREA_HALF_SIZE_DEGREES),
                    max(-90.0, best_lat - COMPACT_AREA_HALF_SIZE_DEGREES),
                    min(180.0, best_lon + COMPACT_AREA_HALF_SIZE_DEGREES),
                    min(90.0, best_lat + COMPACT_AREA_HALF_SIZE_DEGREES),
                )

    if search_bbox is None:
        search_bbox = bbox_from_radius(
            latitude,
            longitude,
            radius_m,
        )

    area_search = search_kind in {"compact_area", "broad_area"}

    data, acquisition = await fetch_osm_dataset(
        search_bbox
    )

    elements = (
        data.get(
            "elements"
        )
        or []
    )

    nodes: dict[
        int,
        list[float],
    ] = {}

    ways: list[
        dict[str, Any]
    ] = []

    for element in elements:
        element_type = element.get(
            "type"
        )

        if element_type == "node":
            try:
                node_id = int(
                    element.get(
                        "id"
                    )
                )

                lon = float(
                    element.get(
                        "lon"
                    )
                )

                lat = float(
                    element.get(
                        "lat"
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

            nodes[
                node_id
            ] = [
                lon,
                lat,
            ]

        elif element_type == "way":
            ways.append(
                element
            )

    relation_evidence = build_relation_evidence(
        elements
    )

    feature_center: tuple[
        float,
        float,
    ] | None = None

    feature_used = False

    feature_metadata: dict[
        str,
        Any,
    ] = {}

    feature_result = selected_feature_result

    if feature_result:
        try:
            feature_lat = float(
                feature_result.get(
                    "lat"
                )
            )

            feature_lon = float(
                feature_result.get(
                    "lon"
                )
            )

            feature_center = (
                feature_lat,
                feature_lon,
            )

            feature_used = True

            feature_metadata = {
                "feature": {
                    "name": (
                        feature_result.get(
                            "name"
                        )
                        or requested_query
                    ),
                    "display_name": feature_result.get(
                        "display_name"
                    ),
                    "type": feature_result.get(
                        "type"
                    ),
                    "latitude": feature_lat,
                    "longitude": feature_lon,
                    "osm_type": feature_result.get(
                        "osm_type"
                    ),
                    "osm_id": feature_result.get(
                        "osm_id"
                    ),
                }
            }

        except (
            TypeError,
            ValueError,
        ):
            feature_center = None

    candidates: list[
        dict[str, Any]
    ] = []

    for way in ways:
        candidate = make_way_candidate(
            way,
            nodes,
            relation_evidence,
            latitude,
            longitude,
            requested_query,
            feature_center
            if feature_used
            else None,
            area_search=area_search,
        )

        if candidate is not None:
            candidates.append(
                candidate
            )

    candidates = group_connected_named_candidates(
        candidates
    )

    # ── Step 5: promote hiking relations into first-class candidates ──────
    # Runs after way-candidate grouping; standalone way candidates are never
    # removed.  Bounded by RELATION_PROMOTION_BUDGET per request.
    promoted_relations, promotion_stats = await _promote_hiking_relations(
        elements=elements,
        existing_candidates=candidates,
        latitude=latitude,
        longitude=longitude,
        search_query=requested_query,
        area_search=area_search,
    )
    candidates.extend(promoted_relations)

    feature_network_route_found = False
    feature_network_error = None

    if (
        feature_used
        and feature_center
        and point_like_search
    ):
        try:
            feature_route = build_feature_access_route(
                feature_name=str(
                    (
                        feature_metadata.get(
                            "feature"
                        )
                        or {}
                    ).get(
                        "name"
                    )
                    or requested_query
                ).strip(),
                feature_type=str(
                    (
                        feature_metadata.get(
                            "feature"
                        )
                        or {}
                    ).get(
                        "type"
                    )
                    or "feature"
                ),
                feature_lat=feature_center[0],
                feature_lon=feature_center[1],
                ways=ways,
                nodes=nodes,
            )

        except Exception as exc:
            feature_route = None
            feature_network_error = str(
                exc
            )

        if feature_route:
            feature_network_route_found = True

            member_ids = set(
                feature_route.get(
                    "member_way_ids"
                )
                or []
            )

            candidates = [
                candidate
                for candidate in candidates
                if not (
                    candidate.get(
                        "osm_type"
                    )
                    == "way"
                    and int(
                        candidate.get(
                            "osm_id"
                        )
                        or 0
                    )
                    in member_ids
                )
            ]

            candidates.append(
                feature_route
            )

    metadata_extra = {
        "resolved_place": resolved_place,
        "search_kind": search_kind,
        **promotion_stats,
        "feature_node_way_candidates": 0,
        "feature_network_route_found": feature_network_route_found,
        "feature_network_error": feature_network_error,
        "feature_search_used": feature_used,
        "feature_distance_available": (
            feature_center
            is not None
        ),
        **feature_metadata,
    }

    return finalize_response(
        query=requested_query,
        bbox=search_bbox,
        candidates=candidates,
        acquisition=acquisition,
        raw_element_count=len(
            elements
        ),
        point_like_search=point_like_search,
        extra_metadata=metadata_extra,
        area_search=area_search,
    )


# ============================================================
# SELECTED TRAIL GEOMETRY ANALYSIS
# ============================================================


def _geometry_parts(
    geometry: Any,
) -> list[
    list[
        list[float]
    ]
]:
    if not isinstance(
        geometry,
        dict,
    ):
        return []

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if geometry_type == "LineString":
        part = dedupe_coordinates(
            coordinates
            or []
        )

        return (
            [
                part
            ]
            if len(part) >= 2
            else []
        )

    if (
        geometry_type
        == "MultiLineString"
    ):
        parts: list[
            list[
                list[float]
            ]
        ] = []

        for part in (
            coordinates
            or []
        ):
            cleaned = dedupe_coordinates(
                part
                or []
            )

            if len(cleaned) >= 2:
                parts.append(
                    cleaned
                )

        return parts

    return []


def _stitch_geometry_parts(
    parts: list[
        list[
            list[float]
        ]
    ],
) -> list[
    list[
        list[float]
    ]
]:
    remaining = [
        list(part)
        for part in parts
        if len(part) >= 2
    ]

    stitched: list[
        list[
            list[float]
        ]
    ] = []

    while remaining:
        current = remaining.pop(
            0
        )

        changed = True

        while (
            changed
            and remaining
        ):
            changed = False

            for index, candidate in enumerate(
                remaining
            ):
                if (
                    current[-1]
                    == candidate[0]
                ):
                    current.extend(
                        candidate[1:]
                    )

                elif (
                    current[-1]
                    == candidate[-1]
                ):
                    current.extend(
                        reversed(
                            candidate[:-1]
                        )
                    )

                elif (
                    current[0]
                    == candidate[-1]
                ):
                    current = (
                        candidate[:-1]
                        + current
                    )

                elif (
                    current[0]
                    == candidate[0]
                ):
                    current = (
                        list(
                            reversed(
                                candidate[1:]
                            )
                        )
                        + current
                    )

                else:
                    continue

                remaining.pop(
                    index
                )

                changed = True

                break

        cleaned = dedupe_coordinates(
            current
        )

        if len(cleaned) >= 2:
            stitched.append(
                cleaned
            )

    return stitched


def _orient_geometry_to_feature(
    coords: list[
        list[float]
    ],
    feature_coordinate: list[
        float
    ] | None,
) -> tuple[
    list[
        list[float]
    ],
    float | None,
]:
    if (
        len(coords) < 2
        or not feature_coordinate
    ):
        return (
            coords,
            None,
        )

    start_distance = haversine(
        coords[0],
        feature_coordinate,
    )

    end_distance = haversine(
        coords[-1],
        feature_coordinate,
    )

    if (
        end_distance
        < start_distance
    ):
        return (
            list(
                reversed(
                    coords
                )
            ),
            end_distance,
        )

    return (
        coords,
        start_distance,
    )


def _geometry_midpoint(
    coords: list[
        list[float]
    ],
) -> list[
    float
] | None:
    if not coords:
        return None

    if len(coords) == 1:
        return coords[0]

    segment_lengths = [
        haversine(
            coords[index],
            coords[index + 1],
        )
        for index in range(
            len(coords) - 1
        )
    ]

    total = sum(
        segment_lengths
    )

    if total <= 0:
        return coords[
            len(coords) // 2
        ]

    target = total / 2.0
    walked = 0.0

    for index, segment_length in enumerate(
        segment_lengths
    ):
        if (
            walked
            + segment_length
            >= target
        ):
            ratio = (
                target
                - walked
            ) / max(
                segment_length,
                1e-12,
            )

            a = coords[
                index
            ]

            b = coords[
                index + 1
            ]

            return [
                a[0]
                + (
                    b[0]
                    - a[0]
                )
                * ratio,
                a[1]
                + (
                    b[1]
                    - a[1]
                )
                * ratio,
            ]

        walked += segment_length

    return coords[
        -1
    ]


def _geometry_bbox(
    parts: list[
        list[
            list[float]
        ]
    ],
) -> list[
    float
] | None:
    points = [
        point
        for part in parts
        for point in part
    ]

    if not points:
        return None

    return [
        min(
            point[0]
            for point in points
        ),
        min(
            point[1]
            for point in points
        ),
        max(
            point[0]
            for point in points
        ),
        max(
            point[1]
            for point in points
        ),
    ]


def normalize_selected_geometry(
    geometry: dict[str, Any],
    feature_coordinate: list[
        float
    ] | None = None,
) -> dict[str, Any]:
    parts = _geometry_parts(
        geometry
    )

    if not parts:
        raise HTTPException(
            status_code=422,
            detail=(
                "No usable LineString or "
                "MultiLineString geometry was supplied"
            ),
        )

    stitched = _stitch_geometry_parts(
        parts
    )

    if not stitched:
        raise HTTPException(
            status_code=422,
            detail=(
                "Geometry contains no usable "
                "connected segments"
            ),
        )

    longest_index = max(
        range(
            len(stitched)
        ),
        key=lambda index: line_length(
            stitched[index]
        ),
    )

    primary, feature_distance = _orient_geometry_to_feature(
        stitched[
            longest_index
        ],
        feature_coordinate,
    )

    normalized_parts = [
        primary
    ]

    for index, part in enumerate(
        stitched
    ):
        if index == longest_index:
            continue

        normalized_parts.append(
            part
        )

    normalized_geometry = geometry_object(
        normalized_parts
    )

    return {
        "geometry": normalized_geometry,
        "geometry_type": (
            normalized_geometry[
                "type"
            ]
            if normalized_geometry
            else None
        ),
        "geometry_status": (
            "connected"
            if len(
                normalized_parts
            ) == 1
            else "fragmented"
        ),
        "component_count": len(
            normalized_parts
        ),
        "coordinate_count": sum(
            len(part)
            for part in normalized_parts
        ),
        "distance_km": round(
            sum(
                line_length(
                    part
                )
                for part in normalized_parts
            ),
            3,
        ),
        "analysis_distance_km": round(
            line_length(
                primary
            ),
            3,
        ),
        "start_coordinate": primary[
            0
        ],
        "end_coordinate": primary[
            -1
        ],
        "midpoint_coordinate": _geometry_midpoint(
            primary
        ),
        "bbox": _geometry_bbox(
            normalized_parts
        ),
        "feature_distance_km": (
            round(
                feature_distance,
                3,
            )
            if feature_distance
            is not None
            else None
        ),
        "feature_oriented": (
            feature_coordinate
            is not None
        ),
    }


class TrailAnalysisRequest(
    BaseModel
):
    geometry: dict[str, Any] | None = None
    trail: dict[str, Any] | None = None
    feature: dict[str, Any] | None = None
    feature_coordinate: list[
        float
    ] | None = None


@router.post(
    "/trails/analysis"
)
async def analyze_selected_trail(
    payload: TrailAnalysisRequest,
) -> dict[str, Any]:
    geometry = payload.geometry

    if (
        geometry is None
        and payload.trail
    ):
        geometry = payload.trail.get(
            "geometry"
        )

    if not geometry:
        raise HTTPException(
            status_code=422,
            detail=(
                "Supply geometry or trail.geometry"
            ),
        )

    feature_coordinate = payload.feature_coordinate

    if (
        feature_coordinate is None
        and payload.feature
    ):
        try:
            feature_coordinate = [
                float(
                    payload.feature[
                        "longitude"
                    ]
                ),
                float(
                    payload.feature[
                        "latitude"
                    ]
                ),
            ]

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            feature_coordinate = None

    if (
        feature_coordinate is not None
        and len(feature_coordinate) < 2
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "feature_coordinate must be "
                "[longitude, latitude]"
            ),
        )

    normalized = normalize_selected_geometry(
        geometry,
        feature_coordinate,
    )

    result: dict[str, Any] = {
        "source": "OpenStreetMap",
        "analysis_version": (
            "phase4_geometry_v1"
        ),
        **normalized,
    }

    if payload.trail:
        for key in (
            "osm_type",
            "osm_id",
            "name",
            "route_type",
            "candidate_class",
            "feature_association_level",
            "member_way_ids",
            "route_shape",
        ):
            if key in payload.trail:
                result[
                    key
                ] = payload.trail[
                    key
                ]

    if payload.feature:
        result[
            "feature"
        ] = payload.feature

    return result


# ============================================================
# FULL OSM OBJECTS / COMPATIBILITY ENDPOINTS
# ============================================================


async def fetch_osm_full(
    osm_type: str,
    osm_id: int,
) -> dict[str, Any]:
    key = (
        norm(
            osm_type
        ),
        int(
            osm_id
        ),
    )

    cached = _FULL_OBJECT_CACHE.get(
        key
    )

    if (
        cached
        and (
            time.monotonic()
            - cached[0]
            <= OBJECT_CACHE_TTL_SECONDS
        )
    ):
        return cached[1]

    if key[0] not in {
        "node",
        "way",
        "relation",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported OSM object type"
            ),
        )

    url = (
        f"{OSM_API_BASE}/"
        f"{key[0]}/"
        f"{key[1]}/"
        "full.json"
    )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                FULL_OBJECT_TIMEOUT
            ),
            headers=HEADERS,
        ) as client:
            response = await client.get(
                url
            )

            if response.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "OSM object not found"
                    ),
                )

            response.raise_for_status()

            try:
                data = response.json()

            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "OpenStreetMap returned "
                        f"invalid JSON: {exc}"
                    ),
                ) from exc

            if not isinstance(
                data,
                dict,
            ):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "OpenStreetMap returned an "
                        "invalid full-object payload"
                    ),
                )

            _FULL_OBJECT_CACHE[
                key
            ] = (
                time.monotonic(),
                data,
            )

            return data

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "OpenStreetMap request failed: "
                f"{exc}"
            ),
        ) from exc


@router.get(
    "/full/{osm_type}/{osm_id}"
)
async def get_full_osm_object(
    osm_type: str,
    osm_id: int,
):
    return await fetch_osm_full(
        osm_type,
        osm_id,
    )
@router.get(
    "/trails/component/{component_id}"
)
async def get_component_geometry(
    component_id: int,
    member_way_ids: str = Query(...),
    trail_name: str | None = Query(default=None),
    ordered_way_ids: str | None = Query(default=None),
):
    """Rebuild a connected component from its OSM member ways."""
    del trail_name

    try:
        member_ids = list(
            dict.fromkeys(
                int(item.strip())
                for item in member_way_ids.split(",")
                if item.strip()
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="member_way_ids must be comma-separated integers",
        ) from exc

    if not member_ids:
        raise HTTPException(
            status_code=400,
            detail="At least one member way is required",
        )

    requested_order: list[int] = []
    if not isinstance(ordered_way_ids, str):
        ordered_way_ids = None

    if ordered_way_ids:
        try:
            requested_order = list(
                dict.fromkeys(
                    int(item.strip())
                    for item in ordered_way_ids.split(",")
                    if item.strip()
                )
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="ordered_way_ids must be comma-separated integers",
            ) from exc

        member_set = set(member_ids)
        requested_order = [
            way_id
            for way_id in requested_order
            if way_id in member_set
        ]
        requested_order.extend(
            way_id
            for way_id in member_ids
            if way_id not in requested_order
        )
    else:
        requested_order = member_ids

    semaphore = asyncio.Semaphore(COMPONENT_WAY_FETCH_CONCURRENCY)

    async def fetch_member(
        way_id: int,
    ) -> tuple[int, dict[str, Any] | None]:
        async with semaphore:
            try:
                return way_id, await fetch_osm_full("way", way_id)
            except HTTPException:
                return way_id, None
            except Exception:
                return way_id, None

    fetched = await asyncio.gather(
        *(fetch_member(way_id) for way_id in requested_order)
    )
    fetched_by_id = {
        way_id: data
        for way_id, data in fetched
        if data is not None
    }

    member_records: list[dict[str, Any]] = []

    for way_id in requested_order:
        data = fetched_by_id.get(way_id)
        if not data:
            continue

        elements = data.get("elements") or []
        nodes: dict[int, list[float]] = {}
        way: dict[str, Any] | None = None

        for element in elements:
            element_type = element.get("type")

            if element_type == "node":
                try:
                    node_id = int(element.get("id"))
                    nodes[node_id] = [
                        float(element.get("lon")),
                        float(element.get("lat")),
                    ]
                except (TypeError, ValueError):
                    continue

            elif element_type == "way":
                try:
                    element_way_id = int(element.get("id"))
                except (TypeError, ValueError):
                    continue
                if element_way_id == way_id:
                    way = element

        if way is None:
            continue

        coords = extract_way_geometry(way, nodes)
        if len(coords) < 2:
            continue

        member_records.append(
            {
                "member_index": len(member_records),
                "way_id": way_id,
                "role": "",
                "normalized_role": "",
                "is_primary": True,
                "is_secondary": False,
                "way": way,
                "geometry": coords,
                "has_geometry": True,
            }
        )

    if not member_records:
        raise HTTPException(
            status_code=404,
            detail="No usable OSM way geometry found",
        )

    chains = _stitch_member_sequence(member_records)
    if not chains:
        raise HTTPException(
            status_code=404,
            detail="Component geometry could not be assembled",
        )

    geometry = geometry_object(chains)
    if not geometry:
        raise HTTPException(
            status_code=404,
            detail="Component geometry could not be assembled",
        )

    is_loop = (
        len(chains) == 1
        and len(chains[0]) >= 3
        and _points_connect(
            chains[0][0],
            chains[0][-1],
            tolerance_km=0.06,
        )
    )

    route_shape_value = "loop" if is_loop else route_shape(chains)
    start_coordinate = chains[0][0] if len(chains) == 1 else None
    end_coordinate = chains[0][-1] if len(chains) == 1 else None
    endpoint_available = len(chains) == 1 and not is_loop

    actual_order = [
        int(member["way_id"])
        for member in member_records
        if member.get("way_id") is not None
    ]

    distance_km = round(
        sum(line_length(segment) for segment in chains),
        2,
    )

    canonical_member_ids = sorted(set(actual_order))

    return {
        "source": "OpenStreetMap",
        "osm_type": "component",
        "osm_id": int(component_id),
        "canonical_component_id": generate_component_id(canonical_member_ids),
        "name": None,
        "tags": {},
        "geometry": geometry,
        "route_shape": route_shape_value,
        "is_loop": is_loop,
        "start_coordinate": start_coordinate,
        "end_coordinate": end_coordinate,
        "endpoint_available": endpoint_available,
        "distance_km": distance_km,
        "member_way_ids": canonical_member_ids,
        "ordered_way_ids": actual_order,
    }



@router.get(
    "/trails/{osm_type}/{osm_id}"
)
async def get_trail_object(
    osm_type: str,
    osm_id: int,
):
    osm_type_norm = norm(osm_type)

    if osm_type_norm == "relation":
        data = await fetch_osm_full(
            "relation",
            osm_id,
        )

        parsed = _parse_full_relation_payload(
            data,
            relation_id=osm_id,
        )

        if parsed is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Relation not found or "
                    "contains no parseable data"
                ),
            )

        assembled = _assemble_relation_geometry(
            parsed
        )

        if assembled is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Relation geometry could "
                    "not be assembled"
                ),
            )

        member_way_ids = [
            int(member["way_id"])
            for member in (
                parsed.get("member_ways") or []
            )
            if member.get("way_id") is not None
        ]

        return {
            "source": "OpenStreetMap",
            "osm_type": "relation",
            "osm_id": int(osm_id),
            "name": parsed.get("name"),
            "tags": parsed.get("tags") or {},
            "route_type": parsed.get("route"),
            "network": parsed.get("network"),
            "symbol": parsed.get("symbol"),
            "ref": parsed.get("ref"),
            "geometry": assembled["geometry"],
            "route_shape": assembled["route_shape"],
            "distance_km": assembled["distance_km"],
            "is_loop": assembled["is_loop"],
            "start_coordinate": assembled["start_coordinate"],
            "end_coordinate": assembled["end_coordinate"],
            "endpoint_available": assembled["endpoint_available"],
            "member_way_ids": sorted(
                set(member_way_ids)
            ),
            "ordered_way_ids": assembled[
                "ordered_way_ids"
            ],
            "primary_segment_count": assembled[
                "primary_segment_count"
            ],
            "secondary_segment_count": assembled[
                "secondary_segment_count"
            ],
            "total_segment_count": assembled[
                "total_segment_count"
            ],
        }

    data = await fetch_osm_full(
        osm_type,
        osm_id,
    )

    elements = (
        data.get(
            "elements"
        )
        or []
    )

    nodes: dict[
        int,
        list[float],
    ] = {}

    for element in elements:
        if element.get(
            "type"
        ) != "node":
            continue

        try:
            nodes[
                int(
                    element.get(
                        "id"
                    )
                )
            ] = [
                float(
                    element.get(
                        "lon"
                    )
                ),
                float(
                    element.get(
                        "lat"
                    )
                ),
            ]

        except (
            TypeError,
            ValueError,
        ):
            continue

    ways = [
        element
        for element in elements
        if element.get(
            "type"
        )
        == "way"
    ]

    if not ways:
        return data

    target_way = ways[0]

    geometry = extract_way_geometry(
        target_way,
        nodes,
    )

    if len(geometry) < 2:
        return data

    relation_data = build_relation_evidence(
        elements
    )

    relation = relation_data.get(
        int(
            target_way.get(
                "id"
            )
            or 0
        )
    )

    relation_view = None

    if relation:
        relation_view = {
            "route_types": sorted(
                relation[
                    "route_types"
                ]
            ),
            "relation_ids": sorted(
                relation[
                    "relation_ids"
                ]
            ),
            "networks": sorted(
                relation[
                    "networks"
                ]
            ),
        }

    ranking = relevance_score_for_way(
        target_way,
        geometry,
        sum(
            point[1]
            for point in geometry
        )
        / len(geometry),
        sum(
            point[0]
            for point in geometry
        )
        / len(geometry),
        norm(
            (
                target_way.get(
                    "tags"
                )
                or {}
            ).get(
                "name"
            )
        ),
        relation_view,
        area_search=False,
    )

    tags = target_way.get(
        "tags"
    ) or {}

    return {
        "source": "OpenStreetMap",
        "osm_type": "way",
        "osm_id": int(
            target_way.get(
                "id"
            )
            or 0
        ),
        "name": (
            tags.get(
                "name"
            )
            or "Unnamed hiking trail"
        ),
        "tags": tags,
        "geometry": {
            "type": "LineString",
            "coordinates": geometry,
        },
        "route_type": (
            relation_view[
                "route_types"
            ][0]
            if (
                relation_view
                and relation_view[
                    "route_types"
                ]
            )
            else (
                norm(
                    tags.get(
                        "route"
                    )
                )
                or None
            )
        ),
        "relation_evidence": relation_view,
        **ranking,
        "raw": data,
    }


