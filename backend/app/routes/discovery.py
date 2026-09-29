from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import unicodedata
from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from difflib import SequenceMatcher
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from shapely.geometry import box, shape
from unidecode import unidecode

from app.core.config import settings
from app.services.postpass import (
    PostpassRelation,
    PostpassWay,
    discover_named_trail_ways_in_bbox,
    discover_relations_in_bbox,
    find_relations_by_names,
    get_relation,
    get_way,
    measure_geometry_completeness,
)
from app.services.rate_limit import discovery_limiter
from app.services.trail_discovery import (
    PLACE_ASSOCIATION_RADIUS_M,
    DiscoveredTrail,
    TrailDiscoveryResult,
    discover_trail_candidates,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/osm",
    tags=["Trail Discovery"],
)

MAX_MAP_READY_RESULTS = max(
    20,
    min(int(os.getenv("MAX_MAP_READY_RESULTS", "100")), 250),
)


def _positive_int(value: Any, default: int) -> int:
    """
    Coerce a FastAPI parameter to a positive int.

    FastAPI supplies a ``Query`` marker object as the Python default, which
    breaks direct in-process calls (tests and the audit harness). Coercing
    here keeps one implementation for both call paths.
    """
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number >= 1 else default
MAX_NAMES_FOR_OSM_MATCHING = max(
    10,
    min(int(os.getenv("MAX_NAMES_FOR_OSM_MATCHING", "60")), 120),
)
AREA_RELATION_ROW_LIMIT = max(
    200,
    min(
        int(
            os.getenv(
                "AREA_RELATION_ROW_LIMIT",
                getattr(settings, "AREA_RELATION_ROW_LIMIT", 500),
            )
        ),
        1000,
    ),
)
AREA_WAY_ROW_LIMIT = max(
    2000,
    min(
        int(
            os.getenv(
                "AREA_WAY_ROW_LIMIT",
                getattr(settings, "AREA_WAY_ROW_LIMIT", 5000),
            )
        ),
        5000,
    ),
)

# ---------------------------------------------------------------
# LARGE AREA TILING
# ---------------------------------------------------------------
# A single bbox query over a country or large region is both slow and
# silently truncating, so any area wider than MAX_TILE_SPAN_DEG is split
# into a uniform, deterministic grid. The grid is uniform (not density
# based) on purpose: a density-based split biases results toward the busy
# corner of the region, which would misreport coverage.
MAX_TILE_SPAN_DEG = max(
    0.25,
    min(float(os.getenv("MAX_TILE_SPAN_DEG", "0.75")), 5.0),
)
MAX_TILES = max(
    1,
    min(int(os.getenv("MAX_DISCOVERY_TILES", "40")), 64),
)
# Queries a search may spend re-querying capped tiles as quadrants, four per
# split. Deliberately separate from the tile grid: a region wide enough to fill
# MAX_TILES used to leave only what the grid left over (4 queries, one split),
# so a live Switzerland search stopped after 4 splits with 59 areas still cut.
# This bounds a pathological search; the time budget below is what governs a
# normal one.
MAX_SPLIT_QUERIES = max(
    4,
    min(int(os.getenv("MAX_DISCOVERY_SPLIT_QUERIES", "200")), 1000),
)

# The longest a search may keep starting new provider queries. Past it, no new
# tile is queried and no capped tile is split further; what was skipped or cut
# is reported in `coverage`, and the results found so far are returned. A query
# already in flight is bounded by its own provider timeout.
DISCOVERY_TIME_BUDGET_SECONDS = max(
    0.0,
    float(os.getenv("DISCOVERY_TIME_BUDGET_SECONDS", "60")),
)
# Bounded concurrency for tiled Postpass queries, kept well inside the public
# service's tolerance.
TILE_CONCURRENCY = max(
    1,
    min(int(os.getenv("TILE_CONCURRENCY", "3")), 8),
)

# Bounded concurrency for Postpass lookups so many semantic candidates or
# name-resolution batches do not serialise into one round-trip each, while
# still staying well within the public Postpass service's tolerance.
AGENT_RESOLUTION_CONCURRENCY = max(
    1,
    min(
        int(
            os.getenv(
                "AGENT_RESOLUTION_CONCURRENCY",
                getattr(settings, "AGENT_RESOLUTION_CONCURRENCY", 4),
            )
        ),
        8,
    ),
)
NAME_RESOLUTION_CONCURRENCY = max(
    1,
    min(
        int(
            os.getenv(
                "NAME_RESOLUTION_CONCURRENCY",
                getattr(settings, "NAME_RESOLUTION_CONCURRENCY", 3),
            )
        ),
        6,
    ),
)

MIN_SEARCH_BBOX_KM = max(
    2.0,
    min(float(os.getenv("MIN_SEARCH_BBOX_KM", "8.0")), 50.0),
)
# Search radii, by what kind of question was asked. These were two inline
# literals (25 km local, 60 km area) applied to every non-peak query, so a
# hill-station or district search was confined to a town-sized circle and
# lost the destinations the place is actually known for.
LOCAL_SEARCH_RADIUS_M = max(
    5000.0,
    min(float(os.getenv("LOCAL_SEARCH_RADIUS_M", "25000.0")), 80000.0),
)
AREA_SEARCH_RADIUS_M = max(
    LOCAL_SEARCH_RADIUS_M,
    min(float(os.getenv("AREA_SEARCH_RADIUS_M", "60000.0")), 200000.0),
)
# A summit is a point feature. Expanding it to a full locality box buries the
# summit in regional results, so peaks get their own tighter radius. The
# surrounding region is still reachable by searching the place name.
PEAK_SEARCH_BBOX_KM = max(
    1.0,
    min(float(os.getenv("PEAK_SEARCH_BBOX_KM", "6.0")), 50.0),
)

HIKING_ROUTE_TYPES = {"hiking", "foot", "walking"}
PATH_HIGHWAYS = {"path", "footway", "bridleway", "steps"}
# `pedestrian` is deliberately absent: it is a street with the cars taken off
# it. It is in URBAN_HIGHWAYS, so it is accepted only with real hiking evidence.
NON_TRAIL_TRACK_HIGHWAYS = {"track"}
# Highways that are not inherently trail-shaped but may still carry a real
# named path in regions with coarse tagging. These are only ever considered
# in the WEAK accept branch, never on their own.
NEUTRAL_HIGHWAYS = {
    "unclassified",
    "service",
    "living_street",
    "residential",
}
EXCLUDED_FOOTWAY_USES = {
    "sidewalk",
    "crossing",
    "alley",
    "driveway",
    "parking_aisle",
    "corridor",
}
RESTRICTED_ACCESS = {"no", "private", "military"}

# A generically-named way ("Trail", "Foot Path") is only usable as a weak
# candidate when it is substantial. Short generic stubs are noise.
GENERIC_NAME_LENGTH_KM = 0.8

# Weak-evidence acceptance thresholds for locally-named paths.
MIN_LOCAL_PATH_KM = max(
    0.05,
    min(float(os.getenv("MIN_LOCAL_PATH_KM", "0.30")), 5.0),
)
MIN_LOCAL_PATH_STRONG_KM = max(
    MIN_LOCAL_PATH_KM,
    min(float(os.getenv("MIN_LOCAL_PATH_STRONG_KM", "1.50")), 25.0),
)
WEAK_EVIDENCE_SCORE = 55.0

GENERIC_TRAIL_WORDS = {
    "trail",
    "trails",
    "trek",
    "treks",
    "trekking",
    "hike",
    "hikes",
    "hiking",
    "route",
    "routes",
    "path",
    "paths",
    "footpath",
    "walk",
    "walking",
    "loop",
    "peak",
    "peaks",
    "mountain",
    "mountains",
    "hill",
    "hills",
    "summit",
    "ridge",
    "falls",
    "waterfall",
    "waterfalls",
    "mala",
    "shola",
    "pass",
    "valley",
    "gorge",
    "canyon",
    "circuit",
    "nature",
    "reserve",
    "sanctuary",
    "forest",
}

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
    "via",
    "from",
    "to",
}

OUTDOOR_NAME_WORDS = {
    "trail",
    "trek",
    "trekking",
    "hike",
    "hiking",
    "peak",
    "peaks",
    "summit",
    "ridge",
    "waterfall",
    "waterfalls",
    "falls",
    "mountain",
    "mountains",
    "hill",
    "hills",
    "mala",
    "shola",
    "pass",
    "valley",
    "gorge",
    "canyon",
    "circuit",
    "loop",
    "reserve",
    "reserves",
    "sanctuary",
    "sanctuaries",
    "forest",
    "forests",
}

DESTINATION_NAME_WORDS = {
    "peak",
    "peaks",
    "summit",
    "ridge",
    "waterfall",
    "waterfalls",
    "falls",
    "mountain",
    "mountains",
    "hill",
    "hills",
    "mala",
    "shola",
    "pass",
    "valley",
    "gorge",
    "canyon",
    "cave",
    "lake",
    "reserve",
    "sanctuary",
    "forest",
}

INFRASTRUCTURE_NAME_PATTERN = re.compile(
    r"\b("
    r"college|university|school|hospital|health\s*centre|health\s*center|"
    r"nursing|hostel|hotel|resort|campus|parking|entrance|entry|exit|"
    r"emergency|gate|bridge|overbridge|bus\s*stop|bus\s*stand|market|"
    r"residential|apartment|apartments|society|ward|station|shortcut|"
    r"short\s*cut|water\s*tank|watchtower|toilet|washroom|bypass|"
    r"road|street|lane|drive|highway|service\s*road|private\s*road"
    r")\b",
    re.IGNORECASE,
)

RELIGIOUS_ACCESS_NAME_PATTERN = re.compile(
    r"\b(temple|church|mosque|shrine)\b",
    re.IGNORECASE,
)

# Names that state the way leads nowhere. These are access stubs rather than
# routes, and are rejected regardless of length or surface.
DEAD_END_NAME_PATTERN = re.compile(
    r"\b(dead[\s_-]?end|no\s+through\s+route|no\s+exit|cul[\s_-]?de[\s_-]?sac"
    r"|blind\s+lane|turnback)\b",
    re.IGNORECASE,
)

# Structure-access vocabulary. This is deliberately NOT a flat reject list:
# real trails are frequently named after a hut or refuge they serve. A
# structure token is only treated as an access stub when it is the FINAL
# significant word of the name and the name carries no destination word.
STRUCTURE_ACCESS_TOKENS = {
    "shed",
    "hut",
    "hütte",
    "huette",
    "cabin",
    "barracks",
    "barrack",
    "reservoir",
    "tank",
    "tower",
    "gatehouse",
    "toilet",
    "washroom",
    "parking",
    "garage",
    "workshop",
    "quarry",
    "mine",
    "dam",
    "mill",
    "office",
    "ward",
    "boundary",
    "garden",
    "park",
    "canal",
    "playground",
    "stadium",
    "railway",
    "promenade",
    "plaza",
    "square",
    "compound",
    # Crossing structures. A named footway is the normal way OSM records a
    # bridge, so without these a purely structural way ("Overbridge
    # Footpath") is indistinguishable from a short local path and slips into
    # the weak-evidence tier. These are structure words rather than place
    # names, so a real route named after one keeps working: a name carrying
    # outdoor or destination evidence ("Rainbow Bridge Trail") returns
    # before this check is reached.
    "bridge",
    "footbridge",
    "overbridge",
    "underbridge",
    "viaduct",
    "underpass",
    "overpass",
    "subway",
    # Emergency and evacuation circulation. A way people are directed to
    # use in an emergency is not a recreational trail.
    "emergency",
    "evacuation",
}

# Genuinely built-up walking surfaces. These never disqualify a way carrying
# real hiking metadata (hut approaches and summit paths are often paved), but
# they do disqualify the weak-evidence tier.
PAVED_SURFACES = {
    "asphalt",
    "concrete",
    "paved",
    "cement",
    "paving_stones",
    "sett",
    "bitumen",
}

# Highways that are streets or street furniture rather than trails.
URBAN_HIGHWAYS = {
    "pedestrian",
    "residential",
    "unclassified",
    "living_street",
    "tertiary",
    "secondary",
    "primary",
    "service",
}


def _is_structure_access(
    all_names: list[str],
    outdoor_words: set[str],
    destination_words: set[str],
) -> bool:
    """
    True when the name reads as a walk *to a man-made feature* rather than a
    route. Real trails are frequently named after the hut or refuge they
    serve, so the caller only applies this when no strong hiking metadata
    is present.
    """
    if outdoor_words or destination_words:
        return False
    for value in all_names:
        if not value:
            continue
        if set(_normalise_name(value).split()) & STRUCTURE_ACCESS_TOKENS:
            return True
    return False


GENERIC_WAY_NAMES = {
    "path",
    "footpath",
    "footway",
    "walkway",
    "walk way",
    "trail",
    "track",
    "road",
    "route",
    "shortcut",
    "short cut",
    "entrance",
    "exit",
    "gate",
}

# Words that name a building, a facility or a function rather than a place.
# A name built only from these identifies where a path goes, not what it is,
# so it cannot corroborate that the path is a trail. This is what separates a
# local toponym ("pilakkavu") from "electricity office" without needing a
# gazetteer, and it is why admission can rest on several signals rather than
# on one name-shaped guess.
_FACILITY_AND_FUNCTION_WORDS = frozenset(
    {
        "access",
        "academy",
        "agricultural",
        "area",
        "block",
        "local",
        "mainroad",
        "shed",
        "airport",
        "ambulance",
        "approach",
        "bank",
        "barn",
        "boundary",
        "bridge",
        # Crossing structures and emergency circulation are function words,
        # not toponyms. Naming them here is what stops `_name_names_a_place`
        # from reading "Overbridge Footpath" as a name identifying somewhere:
        # without it the only non-generic word is a structure, and the
        # structure-access reject is bypassed by `names_a_place`.
        "overbridge",
        "footbridge",
        "underbridge",
        "viaduct",
        "overpass",
        "subway",
        "emergency",
        "evacuation",
        "building",
        "bus",
        "camp",
        "car",
        "carpark",
        "cattle",
        "centre",
        "center",
        "church",
        "clinic",
        "college",
        "community",
        "cottage",
        "court",
        "creek",
        "dam",
        "depot",
        "drive",
        "electricity",
        "entrance",
        "estate",
        "exit",
        "factory",
        "farm",
        "field",
        "fire",
        "footway",
        "forest",
        "gate",
        "golf",
        "grave",
        "green",
        "guest",
        "hall",
        "hospital",
        "hotel",
        "house",
        "industrial",
        "junction",
        "lane",
        "laundry",
        "level",
        "link",
        "mall",
        "mandir",
        "market",
        "mosque",
        "office",
        "officie",
        "outreach",
        "parking",
        "path",
        "pier",
        "plant",
        "playground",
        "post",
        "power",
        "pumping",
        "quad",
        "quarters",
        "railway",
        "reservoir",
        "residence",
        "restaurant",
        "road",
        "route",
        "school",
        "shelter",
        "shop",
        "signal",
        "spring",
        "stadium",
        "station",
        "store",
        "street",
        "substation",
        "temple",
        "toilets",
        "tower",
        "track",
        "trail",
        "tunnel",
        "underpass",
        "utility",
        "walkway",
        "warehouse",
        "water",
        "watermill",
        "way",
        "well",
        "yard",
    }
)

# A misspelt road name ("harbour roadd") reads as a place name to any
# token-based test, and the existing road-name hard reject matches whole words
# so it does not catch it either. These are checked as prefixes, which is what
# actually distinguishes a misspelt road from a local place name.
_ROAD_WORD_PREFIXES = (
    "roa",
    "stree",
    "lan",
    "driv",
    "highwa",
    "avenu",
    "boul",
    "sq",
)

# Words that say in so many words that the feature is a route. Shared between
# the hard rejects and the corroborated-accept test so the two cannot disagree
# about what counts as an explicit route word.
_EXPLICIT_ROUTE_WORDS = frozenset(
    {
        "bridleway",
        "footpath",
        "hike",
        "hiking",
        "path",
        "trail",
        "trek",
        "trekking",
    }
)


# ============================================================
# AREA AND GEOMETRY
# ============================================================


def _bbox_from_radius(
    latitude: float,
    longitude: float,
    radius_m: float,
) -> tuple[float, float, float, float]:
    radius_km = max(1.0, float(radius_m) / 1000.0)
    lat_delta = radius_km / 111.32
    lon_delta = radius_km / (
        111.32 * max(0.15, abs(math.cos(math.radians(latitude))))
    )
    return (
        max(-180.0, longitude - lon_delta),
        max(-90.0, latitude - lat_delta),
        min(180.0, longitude + lon_delta),
        min(90.0, latitude + lat_delta),
    )


def _parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if not value or not value.strip():
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must contain west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox values must be numbers") from exc
    if not all(
        math.isfinite(number)
        for number in (west, south, east, north)
    ):
        raise ValueError("bbox values must be finite")
    if not (-180.0 <= west < east <= 180.0):
        raise ValueError("invalid bbox longitude range")
    if not (-90.0 <= south < north <= 90.0):
        raise ValueError("invalid bbox latitude range")
    return west, south, east, north


def _enforce_minimum_bbox(
    bbox: tuple[float, float, float, float],
    *,
    minimum_km: float = MIN_SEARCH_BBOX_KM,
) -> tuple[tuple[float, float, float, float], bool]:
    """
    Expand an unusably small requested area around its own centre.

    Geocoders frequently return a bounding box for a peak, viewpoint or
    settlement that is only a few metres across. Using it verbatim would
    return zero OSM rows and look like a successful "no trails here"
    answer. Expanding keeps the requested centre honest instead of
    silently reporting a false empty result.
    """
    west, south, east, north = bbox
    center_lat = (south + north) / 2.0
    center_lon = (west + east) / 2.0

    height_km = (north - south) * 111.32
    width_km = (east - west) * 111.32 * max(
        0.15,
        abs(math.cos(math.radians(center_lat))),
    )
    if height_km >= minimum_km and width_km >= minimum_km:
        return bbox, False

    expanded = _bbox_from_radius(
        center_lat,
        center_lon,
        minimum_km * 1000.0,
    )
    return (
        (
            min(west, expanded[0]),
            min(south, expanded[1]),
            max(east, expanded[2]),
            max(north, expanded[3]),
        ),
        True,
    )


def _tile_plan(
    bbox: tuple[float, float, float, float],
) -> tuple[list[tuple[float, float, float, float]], dict[str, Any]]:
    """
    Split a search area into a uniform, deterministic grid of sub-boxes.

    The grid covers the requested area COMPLETELY: the returned tiles are a
    partition, not a sample, so a count of returned results is a count over
    searched geography. When the grid would need more tiles than the hard
    ``MAX_TILES`` bound, the tile size is increased so the partition is still
    complete, and the fact is reported rather than hidden.

    Returns ``(tiles, plan)`` where ``plan`` is the geographic accounting the
    API and UI surface.
    """
    west, south, east, north = bbox
    span_lat = max(north - south, 1e-6)
    span_lon = max(east - west, 1e-6)

    rows = max(1, math.ceil(span_lat / MAX_TILE_SPAN_DEG))
    cols = max(1, math.ceil(span_lon / MAX_TILE_SPAN_DEG))

    if rows * cols > MAX_TILES:
        # Grow the tile step so the partition stays complete and bounded.
        step_lat = span_lat / math.ceil(math.sqrt(MAX_TILES))
        step_lon = span_lon / math.ceil(math.sqrt(MAX_TILES))
        rows = max(1, math.ceil(span_lat / step_lat))
        cols = max(1, math.ceil(span_lon / step_lon))
        while rows * cols > MAX_TILES:
            step_lat *= 1.05
            step_lon *= 1.05
            rows = max(1, math.ceil(span_lat / step_lat))
            cols = max(1, math.ceil(span_lon / step_lon))

    tiles: list[tuple[float, float, float, float]] = []
    for row in range(rows):
        tile_south = south + (span_lat * row / rows)
        tile_north = south + (span_lat * (row + 1) / rows)
        for col in range(cols):
            tile_west = west + (span_lon * col / cols)
            tile_east = west + (span_lon * (col + 1) / cols)
            tiles.append((tile_west, tile_south, tile_east, tile_north))

    plan = {
        "tiled": len(tiles) > 1,
        "tiles_total": len(tiles),
        "tile_grid": f"{rows}x{cols}",
        "tile_span_deg": round(max(span_lat / rows, span_lon / cols), 4),
        "tiles_queried": 0,
        "tiles_failed": 0,
        "tiles_skipped": 0,
    }
    return tiles, plan


# A capped tile is split no finer than this, so rows that all share one
# location cannot be halved forever.
MIN_SPLIT_SPAN_DEG = 0.02


@dataclass
class _QueryBudget:
    """Tile queries still allowed for this search, shared by every tile."""

    remaining: int

    def take(self, count: int) -> bool:
        if count > self.remaining:
            return False
        self.remaining -= count
        return True


@dataclass
class _TileHarvest:
    """Rows found by re-querying capped tiles, and how much is still cut."""

    ways: list[Any] = field(default_factory=list)
    relations: list[Any] = field(default_factory=list)
    ways_truncated: int = 0
    relations_truncated: int = 0
    splits: int = 0

    def merge(self, other: "_TileHarvest") -> None:
        self.ways.extend(other.ways)
        self.relations.extend(other.relations)
        self.ways_truncated += other.ways_truncated
        self.relations_truncated += other.relations_truncated
        self.splits += other.splits


def _quadrants(
    tile: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    west, south, east, north = tile
    mid_lon = (west + east) / 2
    mid_lat = (south + north) / 2
    return [
        (west, south, mid_lon, mid_lat),
        (mid_lon, south, east, mid_lat),
        (west, mid_lat, mid_lon, north),
        (mid_lon, mid_lat, east, north),
    ]


async def _expand_truncated(
    tile: tuple[float, float, float, float],
    *,
    relation_rows: Any,
    way_rows: Any,
    relation_limit: int,
    way_limit: int,
    budget: _QueryBudget,
    semaphore: asyncio.Semaphore,
    deadline: float | None = None,
) -> _TileHarvest:
    """
    Re-query a capped tile as four quadrants, recursively, within the budget.

    A query that returns as many rows as its LIMIT has cut rows the search
    never saw. Splitting the tile gives each part its own LIMIT, so dense
    areas are read in full instead of being truncated by name order. Only the
    kind that was capped is re-queried. The rows passed in are not returned
    again; only what the quadrants add is.

    Whatever cannot be resolved, because the shared budget is spent or the
    tile is already at the minimum size or the time ``deadline`` has passed, is
    counted in ``*_truncated`` and reported, never hidden. A failed re-query counts the same way: its rows
    are unknown.
    """
    need_relations = bool(getattr(relation_rows, "truncated", False))
    need_ways = bool(getattr(way_rows, "truncated", False))
    harvest = _TileHarvest()
    if not (need_relations or need_ways):
        return harvest

    west, south, east, north = tile
    half_span = max(north - south, east - west) / 2
    out_of_time = deadline is not None and time.monotonic() >= deadline
    if half_span < MIN_SPLIT_SPAN_DEG or out_of_time or not budget.take(4):
        harvest.relations_truncated += int(need_relations)
        harvest.ways_truncated += int(need_ways)
        return harvest
    harvest.splits += 1

    async def _quadrant(
        quadrant: tuple[float, float, float, float],
    ) -> _TileHarvest:
        async def _skip() -> None:
            return None

        async with semaphore:
            # A query that waited for a slot may have waited past the deadline
            # (hundreds can queue at once); it is then not run, and the area
            # is reported as still cut.
            if deadline is not None and time.monotonic() >= deadline:
                skipped = _TileHarvest()
                skipped.relations_truncated = int(need_relations)
                skipped.ways_truncated = int(need_ways)
                return skipped
            relations, ways = await asyncio.gather(
                discover_relations_in_bbox(quadrant, limit=relation_limit)
                if need_relations
                else _skip(),
                discover_named_trail_ways_in_bbox(quadrant, limit=way_limit)
                if need_ways
                else _skip(),
                return_exceptions=True,
            )
        found = _TileHarvest()
        for rows, kind in ((relations, "relations"), (ways, "ways")):
            if isinstance(rows, BaseException):
                logger.warning(
                    "Re-query of a capped %s tile failed: %s", kind, rows
                )
                setattr(found, f"{kind}_truncated", 1)
            elif rows is not None:
                getattr(found, kind).extend(rows)
        child = await _expand_truncated(
            quadrant,
            relation_rows=None if isinstance(relations, BaseException) else relations,
            way_rows=None if isinstance(ways, BaseException) else ways,
            relation_limit=relation_limit,
            way_limit=way_limit,
            budget=budget,
            semaphore=semaphore,
            deadline=deadline,
        )
        found.merge(child)
        return found

    for part in await asyncio.gather(
        *(_quadrant(quadrant) for quadrant in _quadrants(tile))
    ):
        harvest.merge(part)
    return harvest


def _dedupe_rows(rows: list[Any], attribute: str) -> list[Any]:
    """
    Keep the first row per OSM id.

    A way or relation that crosses a tile boundary appears in both tiles.
    OSM identity, not position, decides identity, so the first row wins.
    Nothing is merged or synthesised.
    """
    unique: dict[int, Any] = {}
    for row in rows:
        row_id = getattr(row, attribute, None)
        if isinstance(row_id, int):
            unique.setdefault(row_id, row)
    return list(unique.values())


def _geometry_segments(
    geometry: dict[str, Any] | None,
) -> list[list[list[float]]]:
    if not isinstance(geometry, dict):
        return []
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return []

    if geometry_type == "LineString":
        line = _clean_line(coordinates)
        return [line] if len(line) >= 2 else []

    if geometry_type == "MultiLineString":
        return [
            line
            for raw_line in coordinates
            if len(line := _clean_line(raw_line)) >= 2
        ]
    return []


def _clean_line(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    line: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            longitude = float(point[0])
            latitude = float(point[1])
        except (TypeError, ValueError):
            continue
        if not (
            math.isfinite(longitude)
            and math.isfinite(latitude)
            and -180.0 <= longitude <= 180.0
            and -90.0 <= latitude <= 90.0
        ):
            continue
        line.append([longitude, latitude])
    return line


def _geometry_points(
    geometry: dict[str, Any] | None,
) -> list[list[float]]:
    return [
        point
        for segment in _geometry_segments(geometry)
        for point in segment
    ]


def _geometry_intersects_bbox(
    geometry: dict[str, Any] | None,
    bbox: tuple[float, float, float, float],
) -> bool:
    segments = _geometry_segments(geometry)
    if not segments:
        return False
    try:
        route = shape(
            {
                "type": "MultiLineString",
                "coordinates": segments,
            }
        )
        search_area = box(*bbox)
        return bool(not route.is_empty and route.intersects(search_area))
    except Exception:
        # A conservative fallback for any geometry object the provider may add.
        west, south, east, north = bbox
        return any(
            west <= point[0] <= east and south <= point[1] <= north
            for point in (
                coordinate
                for segment in segments
                for coordinate in segment
            )
        )


def _geometry_bounds(
    geometry: dict[str, Any] | None,
) -> tuple[float, float, float, float] | None:
    points = _geometry_points(geometry)
    if not points:
        return None
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return (
        min(longitudes),
        min(latitudes),
        max(longitudes),
        max(latitudes),
    )


def _bounds_overlap(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> bool:
    if first is None or second is None:
        return False
    first_west, first_south, first_east, first_north = first
    second_west, second_south, second_east, second_north = second
    return not (
        first_east < second_west
        or second_east < first_west
        or first_north < second_south
        or second_north < first_south
    )


def _geometry_center(
    geometry: dict[str, Any] | None,
) -> tuple[float, float] | None:
    points = _geometry_points(geometry)
    if not points:
        return None
    return (
        sum(point[1] for point in points) / len(points),
        sum(point[0] for point in points) / len(points),
    )


def _haversine_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    radius_km = 6371.0
    phi1 = math.radians(latitude1)
    phi2 = math.radians(latitude2)
    delta_phi = math.radians(latitude2 - latitude1)
    delta_lambda = math.radians(longitude2 - longitude1)
    value = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2.0) ** 2
    )
    value = max(0.0, min(1.0, value))
    return radius_km * 2.0 * math.atan2(
        math.sqrt(value),
        math.sqrt(1.0 - value),
    )


def _distance_from_search(
    geometry: dict[str, Any] | None,
    latitude: float,
    longitude: float,
) -> float | None:
    points = _geometry_points(geometry)
    if not points:
        return None
    # Only the nearest point matters, so it is found with a cheap planar
    # comparison and measured once, exactly. A haversine per point of every
    # candidate dominated ranking on large areas. Longitude is wrapped so a
    # search beside the antimeridian still finds the nearer side.
    cos_latitude = math.cos(math.radians(latitude))

    def planar_squared(point: list[float]) -> float:
        d_lon = (point[0] - longitude + 180.0) % 360.0 - 180.0
        return (d_lon * cos_latitude) ** 2 + (point[1] - latitude) ** 2

    nearest = min(points, key=planar_squared)
    return _haversine_km(latitude, longitude, nearest[1], nearest[0])


def _geometry_hash(geometry: dict[str, Any] | None) -> str | None:
    if not geometry:
        return None
    canonical = json.dumps(
        geometry,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ============================================================
# PEAK ASSOCIATION
# ============================================================

# How close a route must come to a summit to count as reaching it. A mapped
# trail rarely has a vertex exactly on the surveyed summit point, so the test
# is a proximity band, not an equality.
PEAK_SUMMIT_BAND_M = 150.0
PEAK_APPROACH_BAND_M = 1500.0


def _is_peak_kind(place_kind: Any) -> bool:
    """
    True when the searched place is a summit rather than an area or town.

    Derived from the geocoder's own classification, so it works for any
    language and any region's tagging, rather than a name list.
    """
    text = _normalise_name(place_kind)
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "peak",
            "summit",
            "mountain",
            "hill",
            "volcano",
            "crag",
            "ridge",
            "spur",
            "pass",
            "col",
            "saddle",
            "cirque",
            "massif",
            "fell",
            "ben",
            "berg",
            "horn",
            "kopf",
            "pico",
            "monte",
            "cerro",
        )
    )


def _peak_association(
    geometry: dict[str, Any] | None,
    peak_latitude: float,
    peak_longitude: float,
) -> dict[str, Any] | None:
    """
    Measure a real geographic relationship between a route and a summit.

    Returns ``None`` when the route has no geometry to measure. Otherwise it
    reports the closest approach in metres and one of:

    ``summit_route``
        the route passes within the summit band of the peak point
    ``peak_approach``
        the route passes close enough to be a genuine approach
    ``nearby_route``
        the route is in the area but does not come near the summit point

    No route is ever synthesised or extended to reach a peak, and this uses
    only geometry that was already fetched, so it costs no extra query.
    """
    points = _geometry_points(geometry)
    if not points:
        return None

    closest_m = min(
        _haversine_km(peak_latitude, peak_longitude, point[1], point[0])
        * 1000.0
        for point in points
    )
    if closest_m <= PEAK_SUMMIT_BAND_M:
        association = "summit_route"
    elif closest_m <= PEAK_APPROACH_BAND_M:
        association = "peak_approach"
    else:
        association = "nearby_route"
    return {
        "association": association,
        "closest_approach_m": round(closest_m, 1),
    }


def _summit_coverage_note(
    associations: list[dict[str, Any]],
    peak_name: str,
) -> str:
    """
    State the truth about summit coverage without overclaiming.

    These two statements are different and must not be conflated:

    * no mapped route touches the summit point, but associated routes exist;
    * no relevant associated route was found at all.
    """
    if not associations:
        return (
            f"No relevant hiking trail associated with {peak_name} was found "
            f"in the searched area."
        )
    summaries = [str(entry.get("association")) for entry in associations]
    if "summit_route" in summaries:
        count = summaries.count("summit_route")
        return (
            f"{count} mapped route{'s' if count > 1 else ''} pass within "
            f"{int(PEAK_SUMMIT_BAND_M)} m of the summit point."
        )
    if "peak_approach" in summaries:
        return (
            f"No mapped route touches the summit point itself. "
            f"{summaries.count('peak_approach')} associated route"
            f"{'s' if summaries.count('peak_approach') > 1 else ''} approach "
            f"within {int(PEAK_APPROACH_BAND_M)} m of it."
        )
    return (
        f"No mapped route reaches the summit point or approaches it closely. "
        f"{len(associations)} real trail{'s' if len(associations) > 1 else ''} "
        f"were found in the surrounding area."
    )


def _stable_id(prefix: str, values: list[Any]) -> str:
    canonical = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(canonical).hexdigest()[:20]}"


# ============================================================
# NAME MATCHING
# ============================================================


def _normalise_name(value: Any) -> str:
    return _normalise_text(str(value or ""))


@lru_cache(maxsize=131072)
def _normalise_text(value: str) -> str:
    """Pure in the string, and called hundreds of thousands of times."""
    text = unidecode(
        unicodedata.normalize(
            "NFKC",
            value,
        )
    ).strip().casefold()
    text = text.replace("&", " and ")
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def _phonetic_name(value: Any) -> str:
    text = _normalise_name(value).replace(" ", "")
    text = re.sub(r"([a-z])\1+", r"\1", text)
    for source, target in (
        ("ee", "i"),
        ("ie", "i"),
        ("aa", "a"),
        ("oo", "u"),
        ("ch", "c"),
    ):
        text = text.replace(source, target)
    return text


def _name_tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalise_name(value).split()
        if token not in GENERIC_TRAIL_WORDS
        and token not in SEARCH_STOPWORDS
        and len(token) >= 2
    }


def _all_name_tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normalise_name(value).split()
        if len(token) >= 2
    }


def _name_match_score(
    requested_names: list[str],
    candidate_names: list[str],
) -> float:
    requested_clean = [
        value
        for value in (
            _normalise_name(name)
            for name in requested_names
        )
        if value
    ]
    candidate_clean = [
        value
        for value in (
            _normalise_name(name)
            for name in candidate_names
        )
        if value
    ]
    if not requested_clean or not candidate_clean:
        return 0.0

    best = 0.0
    for requested in requested_clean:
        requested_tokens = _name_tokens(requested)
        for candidate in candidate_clean:
            candidate_tokens = _name_tokens(candidate)
            if requested == candidate:
                best = max(best, 100.0)
                continue
            if requested.replace(" ", "") == candidate.replace(" ", ""):
                best = max(best, 98.0)
                continue
            if requested in candidate or candidate in requested:
                best = max(best, 92.0)
                continue

            if requested_tokens and candidate_tokens:
                overlap = requested_tokens & candidate_tokens
                if overlap:
                    coverage = len(overlap) / min(
                        len(requested_tokens),
                        len(candidate_tokens),
                    )
                    jaccard = len(overlap) / len(
                        requested_tokens | candidate_tokens
                    )
                    score = max(
                        82.0 * coverage,
                        78.0 * jaccard,
                    )
                    if (
                        len(requested_tokens) > 1
                        and len(candidate_tokens) > 1
                        and coverage < 0.75
                    ):
                        score = min(score, 68.0)
                    best = max(best, score)

            sequence_score = (
                SequenceMatcher(
                    None,
                    requested.replace(" ", ""),
                    candidate.replace(" ", ""),
                ).ratio()
                * 84.0
            )
            best = max(best, sequence_score)

            phonetic_requested = _phonetic_name(requested)
            phonetic_candidate = _phonetic_name(candidate)
            if phonetic_requested == phonetic_candidate:
                best = max(best, 90.0)
            elif (
                phonetic_requested
                and phonetic_candidate
                and (
                    phonetic_requested in phonetic_candidate
                    or phonetic_candidate in phonetic_requested
                )
            ):
                best = max(best, 84.0)
            else:
                best = max(
                    best,
                    SequenceMatcher(
                        None,
                        phonetic_requested,
                        phonetic_candidate,
                    ).ratio()
                    * 86.0,
                )

    return round(best, 2)


def _first_number(value: Any) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_incline_percent(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    numbers = [
        float(match.group(0))
        for match in re.finditer(r"[-+]?\d+(?:\.\d+)?", text)
    ]
    if not numbers:
        return None
    numeric = sum(numbers[:2]) / min(len(numbers), 2)
    if text.endswith("°") or text.endswith("deg"):
        return round(math.tan(math.radians(numeric)) * 100.0, 3)
    return round(numeric, 3)


def _parse_width_m(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    number = _first_number(text)
    if number is None:
        return None
    if "cm" in text:
        return round(number / 100.0, 3)
    if "mm" in text:
        return round(number / 1000.0, 3)
    if "ft" in text or "'" in text:
        return round(number * 0.3048, 3)
    if "in" in text or '"' in text:
        return round(number * 0.0254, 3)
    return round(number, 3)


# ============================================================
# HIKING EVIDENCE
# ============================================================


def _named_way_evidence(
    way: PostpassWay,
    *,
    place: str,
    semantic_score: float = 0.0,
) -> tuple[bool, float, list[str], str]:
    """
    Evidence-based relevance for one named OSM way.

    Returns ``(accepted, relevance_score, reasons, evidence_class)`` where
    ``evidence_class`` is ``"strong"``, ``"weak"`` or ``"none"``.

    Six independent evidence channels are evaluated before any decision is
    taken, so a weak channel can never delete a candidate before the other
    channels have been read. Hard rejects are limited to the false-positive
    classes that manual triage confirmed (roads, ward boundaries, school and
    hospital access, generic urban footways, structure-access stubs).
    """
    name = _normalise_name(way.name)
    aliases = [_normalise_name(alias) for alias in way.aliases]
    all_names = [name, *aliases]
    tokens = set().union(*(_all_name_tokens(value) for value in all_names))
    outdoor_words = tokens & OUTDOOR_NAME_WORDS
    destination_words = tokens & DESTINATION_NAME_WORDS
    reasons: list[str] = []
    rejected: list[str] = []
    score = 0.0

    route = _normalise_name(way.route)
    sac = _normalise_name(way.sac_scale)
    visibility = _normalise_name(way.trail_visibility)
    foot = _normalise_name(way.foot)
    footway_use = _normalise_name(way.footway_use)
    highway = _normalise_name(way.highway)
    surface = _normalise_name(way.surface)
    tracktype = _normalise_name(way.tracktype)
    incline = _normalise_name(way.incline)
    width = _normalise_name(way.width)
    sport = _normalise_name(way.sport)

    # foot=designated is how mappers mark a pedestrian street or a paved
    # cycle/foot path. On a street or a built-up surface it says nothing about
    # hiking, and it was the only evidence behind Brunswick Street and
    # Redbraes Place appearing as Edinburgh trails. On natural ground it still
    # counts, so countryside paths keep their evidence.
    foot_designated = (
        foot == "designated"
        and surface not in PAVED_SURFACES
        and highway not in URBAN_HIGHWAYS
    )

    # ---------------------------------------------------------------
    # CHANNEL 1 - STRUCTURAL EVIDENCE
    # ---------------------------------------------------------------
    if route in HIKING_ROUTE_TYPES:
        score += 100.0
        reasons.append(f"route={route}")
    if sac:
        score += 92.0
        reasons.append(f"sac_scale={way.sac_scale}")
    if visibility:
        score += 88.0
        reasons.append(f"trail_visibility={visibility}")
    if foot_designated:
        score += 74.0
        reasons.append("foot=designated")
    if way.trailblazed:
        score += 18.0
        reasons.append("trailblazed metadata")
    if way.designation:
        score += 14.0
        reasons.append("designation metadata")
    if way.hiking:
        score += 16.0
        reasons.append("hiking metadata")
    if sport in {"hiking", "walking", "trail_running"}:
        score += 60.0
        reasons.append(f"sport={sport}")

    # ---------------------------------------------------------------
    # CHANNEL 2 - NAME EVIDENCE
    # ---------------------------------------------------------------
    # A name that is ONLY a generic route word is not a trail identity. "Trail"
    # on its own describes the kind of thing, not which one, and treating it as
    # strong name evidence is what let a bare "Trail" through as a confirmed
    # route. An outdoor word only counts when the name says something more.
    name_is_only_generic = bool(
        tokens and tokens <= (GENERIC_TRAIL_WORDS | GENERIC_WAY_NAMES)
    )
    # Computed here rather than at the accept decision, because the hard
    # rejects below need it: a name that identifies a real place must not be
    # discarded just because one of its words is a facility term.
    names_a_place = _name_names_a_place(name, tokens)
    if outdoor_words and not name_is_only_generic:
        score += 82.0
        reasons.append("hiking destination/name evidence")

    place_key = _normalise_name(place)
    if place_key and any(
        value == place_key
        or value.startswith(place_key + " ")
        or place_key in value
        for value in all_names
        if value
    ):
        score += 78.0
        reasons.append("name matches searched place")

    # ---------------------------------------------------------------
    # CHANNEL 3 - SURFACE / PHYSICAL METADATA
    # ---------------------------------------------------------------
    if surface:
        score += 5.0
    if way.smoothness:
        score += 5.0
    if tracktype:
        score += 8.0
        reasons.append("tracktype metadata")
    if incline:
        score += 4.0
    if width:
        score += 3.0

    # ---------------------------------------------------------------
    # CHANNEL 6 - SEMANTIC EVIDENCE
    # ---------------------------------------------------------------
    if semantic_score >= 90.0:
        score += 86.0
        reasons.append("strong semantic-to-OSM name match")

    # ---------------------------------------------------------------
    # HIGHWAY GATE
    # A non-trail highway is only rejected when the way carries no trail
    # metadata at all. Real Alpine trails are commonly mapped on road ways
    # carrying a SAC scale, so metadata must always win here.
    # ---------------------------------------------------------------
    trail_capable_highway = (
        highway in PATH_HIGHWAYS or highway in NON_TRAIL_TRACK_HIGHWAYS
    )
    strong_metadata = bool(
        route in HIKING_ROUTE_TYPES
        or sac
        or visibility
        or foot_designated
        or way.trailblazed
        or way.designation
        or way.hiking
        or sport in {"hiking", "walking", "trail_running"}
    )
    if not trail_capable_highway and not strong_metadata and not outdoor_words:
        return False, 0.0, ["highway type is not trail-capable"], "none"

    if trail_capable_highway:
        score += 48.0
    elif highway in NEUTRAL_HIGHWAYS:
        score += 20.0

    # Evidence a mapper would only record on a genuine recreational route.
    # A bare route=hiking tag is deliberately excluded here, because
    # mis-tagged school and hospital entrance paths carry it too.
    durable_evidence = bool(
        sac
        or visibility
        or foot_designated
        or semantic_score >= 90.0
        or destination_words
    )

    # ---------------------------------------------------------------
    # HARD REJECTS
    # ---------------------------------------------------------------
    if not name:
        return False, 0.0, ["unnamed feature"], "none"
    # A bare number or single character is a mapper artifact or a survey
    # marker, never a trail identity, unless a recorded grade or visibility
    # says it is a marked path: the numbered paths of the Vallorcine valley
    # ("16", "23") carry sac_scale=mountain_hiking and are real trails. The
    # name is kept exactly as mapped; nothing is invented for it. Only a short
    # trail number qualifies, so an id or a phone number never does.
    numbered_trail = bool(
        re.fullmatch(r"\d{1,4}[a-z]?", name) and (sac or visibility)
    )
    if not numbered_trail and (
        len(name) < 3 or not any(ch.isalpha() for ch in name)
    ):
        return False, 0.0, ["non-descriptive name"], "none"
    if way.length_km <= 0.0:
        return False, 0.0, ["zero-length geometry"], "none"
    # A path too short to walk is not a route in its own right, whatever it is
    # called. This floor applies to every accept tier, not only the weak one: a
    # 10 m stub named "pilakkavu footpath" is a fragment of something else, and
    # presenting it as a trail would pad the mapped count with geometry that is
    # not a route. Durable hiking metadata is exempt, because mappers do split
    # genuine trails into short pieces.
    if name in GENERIC_WAY_NAMES and way.length_km < GENERIC_NAME_LENGTH_KM:
        return False, 0.0, ["generic path identity"], "none"

    road_name_match = any(
        re.search(
            r"\b(road|street|lane|drive|highway)\b",
            value,
            flags=re.IGNORECASE,
        )
        for value in all_names
        if value
    )
    explicit_route_words = tokens & _EXPLICIT_ROUTE_WORDS
    # A road name is only rescued by durable trail evidence, by an explicit
    # route word, or by a name that also identifies a real place and physical
    # length. "Anamudi Ghat Road path" is a route; "harbour roadd" is not, and
    # the difference is the place name plus the word "path", not the length
    # alone.
    if (
        road_name_match
        and not explicit_route_words
        and not strong_metadata
        and not outdoor_words
        and not names_a_place
        and semantic_score < 90.0
    ):
        rejected.append("road-oriented name without explicit route evidence")
        return False, 0.0, rejected, "none"

    if (
        _is_structure_access(all_names, outdoor_words, destination_words)
        and not durable_evidence
        and not names_a_place
    ):
        rejected.append("structure access rather than a route")
        return False, 0.0, rejected, "none"

    infrastructure_match = any(
        INFRASTRUCTURE_NAME_PATTERN.search(value)
        for value in all_names
        if value
    )
    # Infrastructure names are only rescued by durable evidence, OR by a name
    # that both identifies a real place AND says in so many words that it is a
    # path. "Anamudi Ghat Road path" is a genuine summit route whose name
    # happens to mention the road that reaches it; a bare "guest quarters road"
    # satisfies neither half of that test.
    if (
        infrastructure_match
        and not durable_evidence
        and not (names_a_place and explicit_route_words)
    ):
        rejected.append("ordinary access/infrastructure name")
        return False, 0.0, rejected, "none"

    religious_access_match = any(
        RELIGIOUS_ACCESS_NAME_PATTERN.search(value)
        for value in all_names
        if value
    )
    if (
        religious_access_match
        and not outdoor_words
        and not strong_metadata
        and semantic_score < 90.0
        and not destination_words
        # A name that identifies a real place is not a place of worship, so
        # "Jain Temple Pathway" stays rejected while a genuine trail that
        # happens to pass a temple is not thrown away.
        and not names_a_place
    ):
        rejected.append("religious site access without route evidence")
        return False, 0.0, rejected, "none"

    if footway_use in EXCLUDED_FOOTWAY_USES and not strong_metadata:
        rejected.append("ordinary urban footway use")
        return False, 0.0, rejected, "none"

    if DEAD_END_NAME_PATTERN.search(name):
        rejected.append("name states the way is a dead end")
        return False, 0.0, rejected, "none"

    if _normalise_name(way.access) in RESTRICTED_ACCESS and not (
        route in HIKING_ROUTE_TYPES or sac
    ):
        rejected.append("restricted access without explicit hiking route")
        return False, 0.0, rejected, "none"

    # ---------------------------------------------------------------
    # STRONG ACCEPT
    # ---------------------------------------------------------------
    # A name that names a real destination, or that contains a local place
    # name, is trail evidence on its own terms. "Anamudi Ghat Road path" is a
    # genuine summit route described partly by the road that reaches it, so the
    # infrastructure-name reject must not fire on the strength of one word
    # when the rest of the name identifies somewhere real.
    # A name that identifies a real place is trail evidence, but NOT on a
    # built-up surface. "North Giri Veethi" on asphalt is a town street that
    # happens to be named after a place, and a paved surface is the signal
    # that overrides the name. Durable hiking metadata still wins, because hut
    # approaches and short summit paths are frequently paved.
    # A pedestrian or residential way is street furniture, not a trail, however
    # well it is named. "North Giri Veethi" is a town street in a town. A real
    # trail is mapped as a path, footway, track or bridleway, and a route
    # relation or a grade tag overrides this too.
    urban_highway = highway in URBAN_HIGHWAYS
    names_a_place_on_trail = bool(
        names_a_place
        and surface not in PAVED_SURFACES
        and not urban_highway
    )
    # STRONG means durable evidence that a mapper recorded this as a route:
    # a hiking route tag, a recorded grade, a trail visibility, a designated
    # foot, or a name that explicitly says it is a trail.
    #
    # A name that identifies a place is deliberately NOT strong on its own. It
    # is real evidence, and it recovers genuine untagged trails, but "Local
    # Path" and "Path to BL shed mainroad" are also names that name places or
    # destinations without being trails. Naming somewhere is not the same
    # claim as being a route, so it corroborates rather than decides.
    strong_signals = (
        route in HIKING_ROUTE_TYPES
        or bool(sac)
        or bool(visibility)
        or foot_designated
        or bool(way.trailblazed)
        or bool(way.designation)
        or bool(way.hiking)
        or sport in {"hiking", "walking", "trail_running"}
        or bool(outdoor_words and not name_is_only_generic)
        or semantic_score >= 90.0
        or bool(
            place_key
            and any(place_key in value for value in all_names if value)
        )
    )
    if strong_signals:
        return True, min(score, 100.0), reasons, "strong"

    # ---------------------------------------------------------------
    # WEAK ACCEPT - a named, locally-named path with physical evidence.
    # Geometry here is real and verified; only the *relevance* evidence is
    # weak, which is why the class is reported separately.
    # ---------------------------------------------------------------
    if way.length_km < MIN_LOCAL_PATH_KM:
        return False, min(score, 100.0), [
            "too short to be a route in its own right"
        ], "none"

    # A built-up surface disqualifies the weak tier only. Durable hiking
    # metadata still wins, because hut approaches and short summit paths
    # are frequently paved.
    if surface in PAVED_SURFACES and not durable_evidence:
        return False, min(score, 100.0), [
            "built-up surface without hiking metadata"
        ], "none"

    # Weak tier, deliberately unchanged.
    #
    # Two variants of this branch were tried while auditing recall. Requiring
    # `names_a_place` broke genuine untagged paths whose name is only its
    # route type, and excluding facility-word names broke them too: both
    # "Local Path" and "Power House Footpath" clear the same length and
    # metadata bar, because nothing lexical separates a generic local name
    # from a utility-path name. Rather than guess a word list that happened to
    # satisfy one example, the branch keeps its existing behaviour.
    #
    # The residual imprecision is real and pre-existing: a long, verified,
    # untagged path named for what it serves ("Power House Footpath") can
    # reach the weak tier. Weak is reported separately from strong precisely
    # so that tier is visible, and its geometry is still verified. Tightening
    # it further belongs with a real corpus of rejected facility names, not
    # with a recall fix.
    physical_metadata = bool(
        surface or tracktype or incline or width or sac or visibility
    )
    if physical_metadata or way.length_km >= MIN_LOCAL_PATH_STRONG_KM:
        reasons.append(
            "named local path; no route or trail-scale tags recorded"
        )
        return True, min(max(score, WEAK_EVIDENCE_SCORE), 100.0), reasons, "weak"

    # ---------------------------------------------------------------
    # CORROBORATED ACCEPT - accumulated independent evidence.
    # ---------------------------------------------------------------
    # A single length threshold is the wrong test. The audit that motivated
    # this branch rejected genuinely named local trails of 0.5-1.1 km whose
    # geometry is verified, purely for falling under MIN_LOCAL_PATH_STRONG_KM,
    # while admitting the filter must not turn "electricity officie way" into
    # a trail.
    #
    # The earlier attempt relaxed the threshold and immediately admitted that
    # noise, because nothing lexical separates a local toponym from a facility
    # descriptor: both are a non-generic word next to "way". So the length bar
    # is kept, and admission instead requires SEVERAL independent signals that
    # a facility access path does not produce. Each signal below is checked
    # only after every hard reject above has already run, so none of them can
    # rescue a road, a building entrance or a generic "Path".
    if _corroborated_local_trail(
        way, name=name, all_names=all_names, tokens=tokens
    ):
        reasons.append(
            "named local trail corroborated by independent evidence; no "
            "route or trail-scale tags recorded"
        )
        return (
            True,
            min(max(score, WEAK_EVIDENCE_SCORE), 100.0),
            reasons,
            "weak",
        )

    # Nothing qualifies. The reason distinguishes a stub from an untagged
    # path, because "too short to walk" and "no hiking evidence" are
    # different gaps and a caller triaging rejections needs to tell them.
    if way.length_km < MIN_LOCAL_PATH_KM:
        return False, min(score, 100.0), [
            "too short to be a route in its own right"
        ], "none"
    return False, min(score, 100.0), ["insufficient hiking evidence"], "none"


def _name_names_a_place(
    name: str,
    tokens: set[str],
) -> bool:
    """
    True when a name contains a word that identifies a PLACE rather than a
    facility, a function or a generic path word.

    A local toponym ("pilakkavu", "athikkal", "anamudi") is the single most
    reliable signal available for an otherwise untagged trail, and it is also
    exactly what distinguishes it from "electricity office way". The comparison
    is tolerant of a single-character typo, because a misspelt road name
    ("harbour roadd") must not read as a place name.

    A real destination word anywhere in the name also counts, even when the
    rest of the name is a road: "Anamudi Ghat Road path" is a real trail
    described by a road that goes to a summit, and rejecting it would lose a
    genuine route purely because of the word "road".
    """
    generic = _FACILITY_AND_FUNCTION_WORDS | GENERIC_WAY_NAMES

    # A destination word is decisive on its own: a name that says "peak" or
    # "ridge" or "summit" is describing an outdoor feature.
    if tokens & DESTINATION_NAME_WORDS:
        return True

    # A facility or function word means the name says where a path GOES, not
    # what it is. "Al Azhar College Path" is a college entrance whatever its
    # other words look like, so this is checked before the place-word test
    # below and cannot be talked out of it by a proper noun.
    if tokens & _FACILITY_AND_FUNCTION_WORDS - GENERIC_WAY_NAMES:
        return False

    # A real place word alongside an explicit route word settles it.
    # "Anamudi Ghat Road path" is a genuine summit route whose name mentions
    # the road that reaches it; the place word is "anamudi", not "path" or
    # "road", and the road prefix check below would otherwise reject it.
    if tokens & _EXPLICIT_ROUTE_WORDS and any(
        len(token) >= 4
        and token not in _FACILITY_AND_FUNCTION_WORDS
        and token not in GENERIC_WAY_NAMES
        and not token.startswith(_ROAD_WORD_PREFIXES)
        for token in tokens
    ):
        return True

    # Otherwise a misspelt road word disqualifies the name, rather than being
    # treated as an unfamiliar place name.
    if any(
        token.startswith(prefix)
        for token in tokens
        for prefix in _ROAD_WORD_PREFIXES
    ):
        return False

    for token in tokens:
        if len(token) < 3:
            continue
        if token in generic:
            continue
        # A typo of a generic word is still a generic word. Checked against a
        # fixed vocabulary rather than with edit distance, so this stays cheap
        # and predictable.
        if any(
            len(token) == len(word) + 1
            and (
                token.startswith(word)
                or token.endswith(word)
                or (len(word) > 2 and word in token)
            )
            for word in generic
        ):
            continue
        return True
    return False


def _corroborated_local_trail(
    way: PostpassWay,
    *,
    name: str,
    all_names: list[str],
    tokens: set[str],
) -> bool:
    """
    True when a name that names a real place is backed by physical evidence.

    Legitimate trails do not all carry the same OSM tagging, so requiring a
    specific tag set loses real ones. What separates a genuine local trail from
    facility access is not any single field but the agreement of two
    independent ones: a name that identifies somewhere, and geometry that
    looks walked rather than walked-past.

    Every signal here is something a building entrance does not have, and each
    is checked only once the hard rejects have already run, so none of them can
    rescue a road, a building entrance or a generic "Path".
    """
    if not name or way.length_km <= 0.0:
        return False

    # The name has to identify a place. Without this, "electricity office way"
    # is indistinguishable from "athikkal kaithodu" by any other means.
    if not _name_names_a_place(name, tokens):
        return False

    # Length is a precondition, not one of the corroborating signals. A 10 m
    # stub is a doorway whatever it is called, and "pilakkavu footpath" at that
    # length is a fragment of something, not a trail in its own right.
    if way.length_km < MIN_LOCAL_PATH_KM:
        return False

    # Physical evidence that this is a walked route rather than an access
    # stub: a surface or tracktype, a real gradient, a real width, or geometry
    # with enough shape to be a path.
    signals = 0
    surface = _normalise_name(way.surface)
    tracktype = _normalise_name(way.tracktype)
    incline = _normalise_name(way.incline)
    width = _normalise_name(way.width)

    if surface and surface not in PAVED_SURFACES:
        signals += 1
    if tracktype:
        signals += 1
    if incline and incline not in ("0", "0%", "flat", "uphill", "downhill"):
        signals += 1
    if width and width not in ("0", "0m"):
        signals += 1
    if way.length_km >= 0.4:
        signals += 1
    if way.point_count >= 8:
        signals += 1
    if tokens & OUTDOOR_NAME_WORDS:
        signals += 1
    if tokens & DESTINATION_NAME_WORDS:
        signals += 1

    # One physical signal plus a real place name is the floor. A single
    # signal is not enough on its own, which is why a name-only stub with no
    # shape and no metadata stays out.
    return signals >= 1


# ============================================================
# TRAIL BUILDERS
# ============================================================


def _relation_completeness(
    relation: PostpassRelation,
    member_ids: list[int],
) -> dict[str, Any]:
    """
    Report how completely OpenStreetMap maps a named route.

    A named hiking route in OSM is normally a relation containing many member
    ways. A relation that carries a route/network tag but contains only a
    single member way is strong evidence that the real trail is only partially
    mapped. This is computed purely from the OSM attributes returned by
    Postpass - it is an observation about the source data, not a guess about
    the real-world trail.

    The application never extends, stitches or invents the missing parts. It
    reports what is mapped so the number can be defended.
    """
    member_count = len(member_ids)
    network = _normalise_name(relation.network) or None
    is_route_relation = _normalise_name(relation.route) in HIKING_ROUTE_TYPES

    if not is_route_relation:
        state = "not_a_route_relation"
        note = None
    elif member_count <= 1 and network:
        state = "single_member_within_a_named_network"
        note = (
            f"OpenStreetMap maps this named trail as one member way inside "
            f"the '{relation.network}' route network. A trail of this name is "
            f"normally split into many member ways, so the mapped geometry "
            f"here is likely only part of the real trail. The length shown is "
            f"the length of what is actually mapped, not an estimate of the "
            f"whole trail."
        )
    elif member_count <= 1:
        state = "single_member_route"
        note = (
            "OpenStreetMap maps this named trail as a single member way, so "
            "the length shown is the length of the mapped geometry only."
        )
    else:
        state = "multi_member"
        # A multi-member relation is NOT evidence that the whole route is
        # mapped. It records that the trail is split into several ways, which
        # a partially drawn trail is equally consistent with. So no note is
        # attached here, and the interface must not read the absence of a note
        # as a claim of completeness either: the distance is always presented
        # as the length of the mapped section.
        note = None

    return {
        "member_count": member_count,
        "network": relation.network,
        "route": relation.route,
        "state": state,
        "note": note,
        "source_length_km": relation.length_km,
        "calculated_length_km": None,
        "establishes_complete_route": False,
        "basis": (
            "Derived from the OpenStreetMap relation membership and tags "
            "returned by Postpass. Membership describes how the mapped "
            "geometry is split, not whether the real trail is fully mapped, "
            "so this never establishes a complete-route length."
        ),
    }


def _discovery_source(base: str, row: Any) -> str:
    """
    Discovery source naming the OSM half truthfully.

    The semantic half is unchanged; the OSM half is whichever source served
    the row. "semantic+postpass" becomes "semantic+overpass" when the row
    came from the fallback, so the label never claims a provider that did
    not serve.
    """
    if "Overpass" in str(getattr(row, "source", "") or ""):
        return base.replace("postpass", "overpass")
    return base


def _osm_source(rows: list[Any]) -> str:
    """
    Which OSM source actually served a set of rows.

    The fallback returns the same dataclasses as the primary, so the source
    field on each row is the only record of which provider answered. Mixed
    sources across tiles are reported as mixed rather than rounded to one.
    """
    sources = {
        str(getattr(row, "source", "") or "") for row in (rows or [])
    }
    sources.discard("")
    if not sources:
        return "none"
    if len(sources) == 1:
        only = next(iter(sources))
        if "Overpass" in only:
            return "overpass"
        return "postpass"
    return "mixed"


def _geometry_provenance(source: Any, kind: str) -> str:
    """
    Provenance names the source that actually served the geometry.

    Postpass serves rendered geometry; the Overpass fallback assembles it
    from member/node geometry. Both are real OSM geometry, but they are
    different derivations, so they must not share a label.
    """
    text = str(source or "")
    if "Overpass" in text:
        return (
            "overpass_relation_members"
            if kind == "relation"
            else "overpass_way"
        )
    return (
        "postpass_relation_rendered"
        if kind == "relation"
        else "postpass_way"
    )


def _relation_to_trail(
    relation: PostpassRelation,
    *,
    latitude: float,
    longitude: float,
    discovery_source: str,
    match_score: float,
    semantic_name: str | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    distance = _distance_from_search(
        relation.geometry,
        latitude,
        longitude,
    )
    route_type = _normalise_name(relation.route)
    candidate_type = (
        "hiking_route"
        if route_type == "hiking"
        else "walking_route"
    )
    merged_aliases = list(
        dict.fromkeys(
            [
                *relation.aliases,
                *(aliases or []),
            ]
        )
    )
    geometry_hash = _geometry_hash(relation.geometry)
    return {
        "trail_id": f"relation:{relation.relation_id}",
        "osm_id": relation.relation_id,
        "osm_type": "relation",
        "name": relation.name,
        "candidate_type": candidate_type,
        "priority_tier": 1,
        "relevance_score": round(max(70.0, match_score), 2),
        "score": round(max(70.0, match_score), 2),
        "route_type": route_type,
        "highway_type": None,
        "description": relation.description,
        "source_difficulty": relation.sac_scale,
        "difficulty": relation.sac_scale,
        "surface": relation.surface,
        "trail_visibility": relation.trail_visibility,
        "operator": None,
        "network": relation.network,
        "length_km": round(relation.length_km, 3),
        "distance_km": round(relation.length_km, 3),
        "distance_from_search_km": (
            round(distance, 2) if distance is not None else 0.0
        ),
        "segment_count": (
            len(relation.members)
            if relation.members
            else None
        ),
        "ordered_segment_count": (
            len(relation.members)
            if relation.members
            else None
        ),
        "member_way_ids": [
            member.ref
            for member in relation.members
            if member.member_type == "W"
        ],
        "ordered_way_ids": [
            member.ref
            for member in relation.members
            if member.member_type == "W"
        ],
        "relation_completeness": _relation_completeness(
            relation,
            [
                member.ref
                for member in relation.members
                if member.member_type == "W"
            ],
        ),
        "relation_members": [
            {
                "type": member.member_type,
                "ref": member.ref,
                "role": member.role or None,
            }
            for member in relation.members
        ],
        "osm_names": [relation.name, *merged_aliases],
        "aliases": merged_aliases,
        "sources": [],
        "discovery_source": discovery_source,
        "discovery_name": semantic_name,
        "discovery_state": "DISCOVERED",
        "match_state": "MATCHED",
        "verification_state": "VERIFIED",
        "map_state": "MAP_READY",
        "state": "MAP_READY",
        "map_ready": True,
        "geometry": relation.geometry,
        "geometry_type": relation.geometry_type,
        "geometry_hash": geometry_hash,
        "geometry_provenance": _geometry_provenance(
            relation.source, "relation"
        ),
        "point_count": relation.point_count,
        "source": relation.source,
        "evidence": [
            f"route={route_type}",
            *( [f"sac_scale={relation.sac_scale}"] if relation.sac_scale else [] ),
            *( ["trail_visibility"] if relation.trail_visibility else [] ),
        ],
    }


def _way_to_trail(
    way: PostpassWay,
    *,
    latitude: float,
    longitude: float,
    place: str,
    discovery_source: str,
    match_score: float,
    semantic_name: str | None = None,
    aliases: list[str] | None = None,
    candidate_type: str | None = None,
) -> dict[str, Any]:
    distance = _distance_from_search(way.geometry, latitude, longitude)
    route_type = _normalise_name(way.route) or None
    if candidate_type is None:
        candidate_type = (
            "hiking_route"
            if route_type == "hiking"
            else "walking_route"
            if route_type in {"foot", "walking"}
            else "named_hiking_way"
        )
    merged_aliases = list(
        dict.fromkeys(
            [
                *way.aliases,
                *(aliases or []),
            ]
        )
    )
    _, evidence_score, evidence_reasons, evidence_class = _named_way_evidence(
        way,
        place=place,
        semantic_score=match_score if semantic_name else 0.0,
    )
    relevance = round(
        max(match_score, evidence_score, 1.0),
        2,
    )
    return {
        "trail_id": f"way:{way.way_id}",
        "osm_id": way.way_id,
        "osm_type": "way",
        "name": way.name,
        "candidate_type": candidate_type,
        "evidence_class": evidence_class,
        "priority_tier": 2 if relevance >= 80.0 else 3,
        "relevance_score": relevance,
        "score": relevance,
        "route_type": route_type,
        "highway_type": way.highway,
        "description": None,
        "source_difficulty": way.sac_scale,
        "difficulty": way.sac_scale,
        "surface": way.surface,
        "smoothness": way.smoothness,
        "tracktype": way.tracktype,
        "incline": way.incline,
        "incline_pct": _parse_incline_percent(way.incline),
        "incline_direction": way.incline_direction,
        "width": way.width,
        "width_m": _parse_width_m(way.width),
        "assisted_trail": way.assisted_trail,
        "trail_visibility": way.trail_visibility,
        "operator": None,
        "network": None,
        "length_km": round(way.length_km, 3),
        "distance_km": round(way.length_km, 3),
        "distance_from_search_km": (
            round(distance, 2) if distance is not None else 0.0
        ),
        "segment_count": 1,
        "ordered_segment_count": 1,
        "member_way_ids": [way.way_id],
        "ordered_way_ids": [way.way_id],
        "osm_names": [way.name, *merged_aliases],
        "aliases": merged_aliases,
        "sources": [],
        "discovery_source": discovery_source,
        "discovery_name": semantic_name,
        "discovery_state": "DISCOVERED",
        "match_state": "MATCHED",
        "verification_state": "VERIFIED",
        "map_state": "MAP_READY",
        "state": "MAP_READY",
        "map_ready": True,
        "geometry": way.geometry,
        "geometry_type": way.geometry_type,
        "geometry_hash": _geometry_hash(way.geometry),
        "geometry_provenance": _geometry_provenance(
            way.source, "way"
        ),
        "point_count": way.point_count,
        "source": way.source,
        "evidence": evidence_reasons,
    }


def _unmapped_trail(
    item: DiscoveredTrail,
    *,
    resolution: dict[str, Any] | None = None,
    sources_tried: list[str] | None = None,
) -> dict[str, Any]:
    stable_id = _stable_id("discovered", [_normalise_name(item.name)])
    return {
        "trail_id": stable_id,
        "osm_id": None,
        "osm_type": None,
        "name": item.name,
        "candidate_type": "discovered_trail",
        "priority_tier": 0,
        "relevance_score": 0.0,
        "route_type": None,
        "highway_type": None,
        "description": None,
        "source_difficulty": None,
        "difficulty": None,
        "surface": None,
        "trail_visibility": None,
        "operator": None,
        "network": None,
        "length_km": None,
        "distance_km": None,
        "distance_from_search_km": None,
        "segment_count": None,
        "ordered_segment_count": None,
        "member_way_ids": [],
        "ordered_way_ids": [],
        "relation_members": [],
        "osm_names": [],
        "aliases": list(dict.fromkeys(item.aliases)),
        # Why this candidate has no geometry, and what was actually tried.
        # The caller reports which OSM sources were consulted, so an
        # outage-driven fallback is recorded rather than assumed.
        "unmapped_kind": "no_verified_geometry",
        "geometry_resolution": {
            "attempted": True,
            "sources_tried": (
                sources_tried
                if sources_tried
                else ["OpenStreetMap via Postpass"]
            ),
            "names_tried": [
                value
                for value in [item.name, *item.aliases]
                if value
            ],
            "matched_geometry": False,
            "reason": (
                "No trustworthy OpenStreetMap geometry could be established "
                "for this candidate. Explicit OSM references, named route "
                "relations (including relations whose member ways are "
                "unnamed) and named ways were all checked inside the searched "
                "area. The candidate is kept visible rather than dropped, and "
                "it is never given fabricated geometry, an invented OSM "
                "identity, or a neighbouring road in place of a route."
            ),
            "resolution_method": [
                "explicit OSM references carried by the source",
                "route relations whose name matches, using real member "
                "geometry through the relation",
                "named ways matching the candidate inside the search area",
            ],
            "detail": resolution,
        },
        "sources": [
            {"title": source.title, "url": source.url}
            for source in item.sources
        ],
        "osm_references": [
            {"type": reference.type, "id": reference.id}
            for reference in item.osm_references
        ],
        "location_context": item.location_context,
        "discovery_source": "semantic",
        "discovery_name": item.name,
        "discovery_state": "DISCOVERED",
        "match_state": "UNMAPPED",
        "verification_state": "UNVERIFIED",
        "map_state": "NOT_MAP_READY",
        "state": "UNMAPPED",
        "map_ready": False,
        "geometry": None,
        "geometry_type": None,
        "geometry_hash": None,
        "geometry_provenance": None,
        "source": "Semantic discovery",
    }


def _component_from_ways(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    member_ids = [
        int(value)
        for item in items
        for value in item.get("member_way_ids", [])
        if value is not None
    ]
    member_ids = list(dict.fromkeys(member_ids))
    ordered_ids = [
        int(value)
        for value in (
            ordered_id
            for item in items
            for ordered_id in item.get("ordered_way_ids", [])
            if ordered_id is not None
        )
    ]
    ordered_ids = list(dict.fromkeys(ordered_ids))
    segments = [
        segment
        for item in items
        for segment in _geometry_segments(item.get("geometry"))
    ]
    geometry = {
        "type": "MultiLineString",
        "coordinates": segments,
    }
    stable_id = _stable_id("component", ordered_ids or member_ids)
    route_types = {
        _normalise_name(item.get("route_type"))
        for item in items
        if item.get("route_type")
    }
    route_type = next(iter(route_types)) if len(route_types) == 1 else None
    source_difficulties = list(
        dict.fromkeys(
            item.get("source_difficulty")
            for item in items
            if item.get("source_difficulty")
        )
    )
    aliases = list(
        dict.fromkeys(
            alias
            for item in items
            for alias in item.get("aliases", [])
            if alias
        )
    )
    total_length = sum(
        float(item.get("length_km") or 0.0)
        for item in items
    )
    return {
        **items[0],
        "trail_id": stable_id,
        "osm_id": None,
        "osm_type": "component",
        "candidate_type": "trail_network",
        "priority_tier": 2,
        "relevance_score": round(
            max(
                float(item.get("relevance_score") or 0.0)
                for item in items
            ),
            2,
        ),
        "score": round(
            max(
                float(item.get("score") or 0.0)
                for item in items
            ),
            2,
        ),
        "route_type": route_type,
        "source_difficulty": (
            source_difficulties[0]
            if len(source_difficulties) == 1
            else None
        ),
        "source_difficulty_values": source_difficulties,
        "difficulty": (
            source_difficulties[0]
            if len(source_difficulties) == 1
            else None
        ),
        "length_km": round(total_length, 3),
        "distance_km": round(total_length, 3),
        "member_way_ids": member_ids,
        "ordered_way_ids": ordered_ids,
        "relation_members": [],
        "segment_count": len(segments),
        "ordered_segment_count": len(segments),
        "geometry": geometry,
        "geometry_type": "MultiLineString",
        "geometry_hash": _geometry_hash(geometry),
        "geometry_provenance": "connected_named_osm_ways",
        "point_count": sum(len(segment) for segment in segments),
        "aliases": aliases,
        "osm_names": list(
            dict.fromkeys(
                name
                for item in items
                for name in item.get("osm_names", [])
                if name
            )
        ),
        "evidence": list(
            dict.fromkeys(
                reason
                for item in items
                for reason in item.get("evidence", [])
                if reason
            )
        ),
    }


# ============================================================
# COLLAPSE AND DEDUPLICATION
# ============================================================


# A way is part of a same-named route only if it lies along it: at least this
# share of its length within about 5 m of the route's line. A bounding box is
# not that test (a long route's box covers a whole region), and a way that only
# crosses or touches a route is another trail that happens to meet it.
ALONG_TOLERANCE_DEGREES = 0.00005
ALONG_FRACTION = 0.9


def _lies_along(
    way_geometry: dict[str, Any] | None,
    route_geometry: dict[str, Any] | None,
    route_buffers: dict[int, Any],
) -> bool:
    way_segments = _geometry_segments(way_geometry)
    route_segments = _geometry_segments(route_geometry)
    if not way_segments or not route_segments:
        return False
    try:
        line = shape({"type": "MultiLineString", "coordinates": way_segments})
        key = id(route_geometry)
        if key not in route_buffers:
            # Built once per route, and only when a same-named way needs it.
            route_buffers[key] = shape(
                {"type": "MultiLineString", "coordinates": route_segments}
            ).buffer(ALONG_TOLERANCE_DEGREES)
        buffered = route_buffers[key]
        if line.length == 0:
            return bool(buffered.contains(line))
        return (
            line.intersection(buffered).length
            >= ALONG_FRACTION * line.length
        )
    except Exception:
        # A geometry object the provider may add: fall back to the earlier,
        # looser behaviour (the caller has already matched name and bounds).
        return True


def _collapse_connected_named_ways(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Relations by name, so each way is compared with the routes that share
    # its name and not with every route (20,000 ways against 2,000 relations
    # was tens of millions of comparisons).
    relations_by_name: dict[
        str,
        list[
            tuple[tuple[float, float, float, float], dict[str, Any] | None]
        ],
    ] = defaultdict(list)
    for candidate in candidates:
        if candidate.get("osm_type") != "relation":
            continue
        bounds = _geometry_bounds(candidate.get("geometry"))
        for name in [
            candidate.get("name"),
            *(candidate.get("aliases") or []),
        ]:
            key = _normalise_name(name)
            if key and bounds is not None:
                relations_by_name[key].append(
                    (bounds, candidate.get("geometry"))
                )
    route_buffers: dict[int, Any] = {}

    # Member ways of an accepted route (relation or connected component) are
    # that route's sections, not independent trails — whatever name they
    # carry. This is decided by OSM identity alone (the route lists the way
    # as a member), never by name agreement or geometry proximity, so it
    # holds regardless of which provider rendered the shapes or how their
    # bounds compare. A differently-named member stays reachable through the
    # route's member_way_ids, which preserve IDs, order and roles; the route
    # card, carrying the provider's own member geometry, represents the
    # trail. This only ever applies when the representing route is itself in
    # the result set: the set below is built from accepted routes only, so a
    # member whose route was not discovered is untouched.
    route_members: set[int] = set()
    for candidate in candidates:
        if candidate.get("osm_type") not in ("relation", "component"):
            continue
        for member_id in candidate.get("member_way_ids") or []:
            try:
                route_members.add(int(member_id))
            except (TypeError, ValueError):
                continue

    filtered: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_way_ids: set[int] = set()

    for candidate in candidates:
        if candidate.get("osm_type") != "way":
            filtered.append(candidate)
            continue
        way_id = int(candidate["osm_id"])
        if way_id in seen_way_ids:
            continue
        seen_way_ids.add(way_id)

        if way_id in route_members:
            # Section of an accepted route: the route card represents this
            # trail, with the member reachable through its member_way_ids.
            continue
        name_key = _normalise_name(candidate.get("name"))
        way_bounds = _geometry_bounds(candidate.get("geometry"))
        duplicate_relation = any(
            _bounds_overlap(way_bounds, relation_bounds)
            and _lies_along(
                candidate.get("geometry"), relation_geometry, route_buffers
            )
            for relation_bounds, relation_geometry in relations_by_name.get(
                name_key, ()
            )
        )
        if duplicate_relation:
            continue
        grouped[name_key].append(candidate)

    for name_key, items in grouped.items():
        del name_key
        if len(items) == 1:
            filtered.append(items[0])
            continue

        parent = list(range(len(items)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(first: int, second: int) -> None:
            first_root = find(first)
            second_root = find(second)
            if first_root != second_root:
                parent[second_root] = first_root

        endpoint_owners: dict[tuple[float, float], list[int]] = defaultdict(list)
        for index, item in enumerate(items):
            segments = _geometry_segments(item.get("geometry"))
            if not segments:
                continue
            first_segment = segments[0]
            last_segment = segments[-1]
            for endpoint in (
                (round(first_segment[0][0], 7), round(first_segment[0][1], 7)),
                (round(last_segment[-1][0], 7), round(last_segment[-1][1], 7)),
            ):
                for owner in endpoint_owners[endpoint]:
                    union(index, owner)
                endpoint_owners[endpoint].append(index)

        components: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for index, item in enumerate(items):
            components[find(index)].append(item)

        for component_items in components.values():
            if len(component_items) == 1:
                filtered.append(component_items[0])
            else:
                filtered.append(_component_from_ways(component_items))

    return filtered


# ============================================================
# SEMANTIC RESOLUTION
# ============================================================


def _relation_score_for_item(
    item: DiscoveredTrail,
    relation: PostpassRelation,
) -> float:
    return _name_match_score(
        [item.name, *item.aliases],
        [relation.name, *relation.aliases],
    )


def _way_score_for_item(
    item: DiscoveredTrail,
    way: PostpassWay,
) -> float:
    return _name_match_score(
        [item.name, *item.aliases],
        [way.name, *way.aliases],
    )


async def _resolve_agent_item(
    item: DiscoveredTrail,
    *,
    latitude: float,
    longitude: float,
    place: str,
    bbox: tuple[float, float, float, float],
    name_mapping: dict[str, list[PostpassRelation]],
    postpass_ways: list[PostpassWay],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Resolve one discovered candidate to real OSM geometry.

    Order of genuine resolution attempts, all against real data:

    1. explicit OSM references carried by the source;
    2. route relations whose name matches the candidate (a named route
       relation may contain unnamed member ways, and those member geometries
       are used through the real relation, never by proximity);
    3. named ways matching the candidate inside the search area.

    ``diagnostics`` is filled with what was actually measured so an unmapped
    result can report real counts instead of asserting a generic failure.
    """
    if diagnostics is not None:
        diagnostics.update(
            {
                "explicit_references_tried": 0,
                "explicit_references_resolved": 0,
                "name_relations_found": 0,
                "name_relations_in_area": 0,
                "named_ways_in_area": 0,
                "recovered_via": None,
            }
        )

    for reference in item.osm_references:
        if diagnostics is not None:
            diagnostics["explicit_references_tried"] += 1
        try:
            if reference.type == "relation":
                relation = await get_relation(reference.id)
                if relation is None:
                    continue
                score = _relation_score_for_item(item, relation)
                if not _geometry_intersects_bbox(relation.geometry, bbox):
                    continue
                if not reference.from_source_url and score < 75.0:
                    continue
                if diagnostics is not None:
                    diagnostics["explicit_references_resolved"] += 1
                    diagnostics["recovered_via"] = (
                        "explicit_osm_reference_relation"
                    )
                return _relation_to_trail(
                    relation,
                    latitude=latitude,
                    longitude=longitude,
                    discovery_source=_discovery_source(
                        "semantic+postpass", relation
                    ),
                    match_score=score,
                    semantic_name=item.name,
                    aliases=item.aliases,
                )

            if reference.type == "way":
                way = await get_way(reference.id)
                if way is None:
                    continue
                score = _way_score_for_item(item, way)
                accepted, _evidence_score, _reasons, _cls = _named_way_evidence(
                    way,
                    place=place,
                    semantic_score=score,
                )
                if not accepted:
                    continue
                if not _geometry_intersects_bbox(way.geometry, bbox):
                    continue
                if not reference.from_source_url and score < 75.0:
                    continue
                return _way_to_trail(
                    way,
                    latitude=latitude,
                    longitude=longitude,
                    place=place,
                    discovery_source=_discovery_source(
                        "semantic+postpass", way
                    ),
                    match_score=score,
                    semantic_name=item.name,
                    aliases=item.aliases,
                )
        except Exception as exc:
            logger.warning(
                "Explicit OSM reference lookup failed (%s/%s): %s",
                reference.type,
                reference.id,
                exc,
            )

    relation_candidates: dict[int, tuple[float, PostpassRelation]] = {}
    for search_name in [item.name, *item.aliases]:
        for relation in name_mapping.get(search_name, []):
            score = _relation_score_for_item(item, relation)
            previous = relation_candidates.get(relation.relation_id)
            if previous is None or score > previous[0]:
                relation_candidates[relation.relation_id] = (score, relation)

    if diagnostics is not None:
        diagnostics["name_relations_found"] = len(relation_candidates)
    ranked_relations = sorted(
        (
            (score, relation)
            for score, relation in relation_candidates.values()
            if score >= 78.0
            and _geometry_intersects_bbox(relation.geometry, bbox)
        ),
        key=lambda value: (
            -value[0],
            _distance_from_search(
                value[1].geometry,
                latitude,
                longitude,
            ) if _distance_from_search(
                value[1].geometry,
                latitude,
                longitude,
            ) is not None else 1e9,
            value[1].relation_id,
        ),
    )
    if diagnostics is not None:
        diagnostics["name_relations_in_area"] = len(ranked_relations)
    if ranked_relations:
        score, relation = ranked_relations[0]
        if diagnostics is not None:
            diagnostics["recovered_via"] = "named_route_relation"
        return _relation_to_trail(
            relation,
            latitude=latitude,
            longitude=longitude,
            discovery_source=_discovery_source(
                "semantic+postpass", relation
            ),
            match_score=score,
            semantic_name=item.name,
            aliases=item.aliases,
        )

    way_matches: list[tuple[float, float, PostpassWay]] = []
    for way in postpass_ways:
        score = _way_score_for_item(item, way)
        if score < 78.0:
            continue
        accepted, _evidence_score, _reasons, _cls = _named_way_evidence(
            way,
            place=place,
            semantic_score=score,
        )
        if not accepted:
            continue
        distance = _distance_from_search(way.geometry, latitude, longitude)
        way_matches.append(
            (
                score,
                distance if distance is not None else 1e9,
                way,
            )
        )

    if diagnostics is not None:
        diagnostics["named_ways_in_area"] = len(way_matches)
    if way_matches:
        score, _distance, way = sorted(
            way_matches,
            key=lambda value: (-value[0], value[1], value[2].way_id),
        )[0]
        if diagnostics is not None:
            diagnostics["recovered_via"] = "named_way"
        return _way_to_trail(
            way,
            latitude=latitude,
            longitude=longitude,
            place=place,
            discovery_source=_discovery_source(
                "semantic+postpass", way
            ),
            match_score=score,
            semantic_name=item.name,
            aliases=item.aliases,
        )

    return None



# ============================================================
# RANKING AND RESPONSE
# ============================================================


def _candidate_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    def number(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # When the user searched for a summit, genuinely associated routes rank
    # ahead of merely nearby ones. The association is a measured geographic
    # relationship, never an assumption.
    association_rank = {
        "summit_route": 0,
        "peak_approach": 1,
        "nearby_route": 2,
    }.get(str(item.get("peak_association") or ""), 3)

    return (
        association_rank,
        number(item.get("priority_tier"), 99),
        -number(item.get("relevance_score"), 0.0),
        number(item.get("distance_from_search_km"), 1e9),
        -number(item.get("length_km"), 0.0),
        _normalise_name(item.get("name")),
        str(item.get("trail_id") or ""),
    )


def _looks_like_exact_trail_query(place: str) -> bool:
    """
    True when the query names a route rather than a place.

    Decided from the words present, never from a list of known trail names.
    It uses the raw token set rather than `_name_tokens`, because that helper
    deliberately strips generic words such as "footpath", "trail" and "loop"
    for similarity scoring — which meant every genuinely trail-shaped query
    ("Kozhiparamba Footpath", "Rhodo Valley Loop") read as a bare place word
    and got a place-sized search area.

    A route word alone is not enough to be certain, so this only tightens the
    radius when the query also carries a word that names something specific.
    A single word is left at the place radius, because "Anamudi" or "Munnar"
    is a place, not a route.
    """
    tokens = {
        token
        for token in _normalise_name(place).split()
        if len(token) >= 2
    }
    if not tokens:
        return False
    if not (tokens & _EXPLICIT_ROUTE_WORDS):
        return False
    # Something in the query that is not itself a route word, so the query
    # names a particular route rather than being a bare route term.
    return bool(tokens - _EXPLICIT_ROUTE_WORDS - GENERIC_TRAIL_WORDS)


def _resolve_discovery_input(
    latitude: float,
    longitude: float,
    search_query: str,
    location_name: str,
    scope: str,
    bbox: str | None,
    place_kind: str = "area",
) -> tuple[str, tuple[float, float, float, float], str]:
    if not (-90.0 <= latitude <= 90.0):
        raise HTTPException(status_code=400, detail="Invalid latitude")
    if not (-180.0 <= longitude <= 180.0):
        raise HTTPException(status_code=400, detail="Invalid longitude")

    place = search_query.strip()
    if not place and location_name.strip():
        place = location_name.split(",", 1)[0].strip()
    if not place:
        raise HTTPException(
            status_code=400,
            detail="search_query or location_name is required",
        )

    try:
        requested_bbox = _parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if requested_bbox is not None:
        search_bbox, bbox_expanded = _enforce_minimum_bbox(
            requested_bbox,
            minimum_km=(
                PEAK_SEARCH_BBOX_KM
                if _is_peak_kind(place_kind)
                else MIN_SEARCH_BBOX_KM
            ),
        )
        bbox_source = (
            "expanded_requested_bbox"
            if bbox_expanded
            else "requested_bbox"
        )
    elif _is_peak_kind(place_kind):
        # A summit is a point feature. The generic local radius would bury it
        # in regional results and make "near this peak" meaningless, so a
        # peak search uses a peak-sized radius regardless of scope.
        search_bbox = _bbox_from_radius(
            latitude,
            longitude,
            PEAK_SEARCH_BBOX_KM * 1000.0,
        )
        bbox_source = "peak_radius"
    elif scope == "area":
        search_bbox = _bbox_from_radius(
            latitude,
            longitude,
            AREA_SEARCH_RADIUS_M,
        )
        bbox_source = "area_radius"
    elif _looks_like_exact_trail_query(place):
        # The user named a route. Nearby is the right answer, so the local
        # radius is kept.
        search_bbox = _bbox_from_radius(
            latitude,
            longitude,
            LOCAL_SEARCH_RADIUS_M,
        )
        bbox_source = "local_radius"
    else:
        # A place search. A hill station or a district is named after
        # destinations that sit well outside a town-sized circle, so a
        # town-radius search silently excluded exactly the trails the place
        # is known for. The wider radius is bounded and is still a circle
        # around the resolved point, so it never becomes a whole-country
        # sweep; a genuine region search passes a real bbox instead.
        search_bbox = _bbox_from_radius(
            latitude,
            longitude,
            PLACE_ASSOCIATION_RADIUS_M,
        )
        bbox_source = "place_association_radius"

    return place, search_bbox, bbox_source


def _discovery_client_key(request: Request | None) -> str | None:
    if request is not None and request.client is not None:
        return request.client.host
    return None


def _enforce_discovery_limit(request: Request | None) -> None:
    client_key = _discovery_client_key(request)
    if client_key and not discovery_limiter.allow(
        client_key,
        limit=30,
        window_seconds=60.0,
    ):
        raise HTTPException(
            status_code=429,
            detail="Discovery request limit exceeded; retry shortly",
        )


async def _timed_assemble(started: float, **kwargs: Any) -> dict[str, Any]:
    """
    Assemble the response and record where the time went.

    ``started`` is when discovery began, so ``providers`` is everything up to
    the point the provider rows were in hand and ``assemble`` is the
    evidence, ranking and response stage after it.
    """
    providers_done = time.monotonic()
    result = await _assemble_discovery_result(**kwargs)
    finished = time.monotonic()
    timings = {
        "providers": round((providers_done - started) * 1000),
        "assemble": round((finished - providers_done) * 1000),
    }
    result.setdefault("diagnostics", {})["timings_ms"] = timings
    logger.info(
        "Discovery for %r: providers %d ms, assemble %d ms",
        kwargs.get("place"),
        timings["providers"],
        timings["assemble"],
    )
    return result


# ------------------------------------------------------------
# One search, one harvest
# ------------------------------------------------------------

# How long the rows read for a search are kept, and how many searches are kept.
# Every page of a search, and its enrichment, ranks the same rows: without this
# each request read the providers again, reached the time budget at a different
# point and ranked a different set (a live Switzerland search returned 13,412,
# 20,292 and 23,639 ranked trails for identical parameters, and each request
# took 84-113 s). Entries can hold tens of thousands of rows, so few are kept.
HARVEST_CACHE_TTL_SECONDS = max(
    0.0,
    float(os.getenv("DISCOVERY_CACHE_TTL_SECONDS", "300")),
)
HARVEST_CACHE_MAX_ENTRIES = max(
    1,
    min(int(os.getenv("DISCOVERY_CACHE_MAX_ENTRIES", "2")), 16),
)


@dataclass
class _Harvest:
    """The provider rows for one search, and how completely they were read."""

    # A list of rows, or the exception the provider raised: a failed read is
    # reported by the assembly stage as a provider failure, never as "empty".
    relations: Any
    ways: Any
    tile_plan: dict[str, Any]

    @property
    def complete(self) -> bool:
        """True only when nothing failed, so it is safe to remember."""
        return not (
            isinstance(self.relations, BaseException)
            or isinstance(self.ways, BaseException)
            or self.tile_plan.get("tiles_failed", 0)
        )


_HARVEST_CACHE: OrderedDict[tuple[Any, ...], tuple[float, _Harvest]] = (
    OrderedDict()
)
_HARVEST_INFLIGHT: dict[tuple[Any, ...], asyncio.Task[_Harvest]] = {}


def _harvest_key(
    search_bbox: tuple[float, float, float, float],
    scope: str,
    center: tuple[float, float] | None = None,
) -> tuple[Any, ...]:
    # Where the search started decides which tiles are read first, so it only
    # matters (and only splits the cache) when the area is tiled and a time
    # limit could leave some unread.
    tiled = len(_tile_plan(search_bbox)[0]) > 1
    return (
        tuple(round(value, 5) for value in search_bbox),
        scope,
        (round(center[0], 2), round(center[1], 2))
        if tiled and center is not None
        else None,
        AREA_RELATION_ROW_LIMIT,
        AREA_WAY_ROW_LIMIT,
        MAX_TILES,
        MAX_SPLIT_QUERIES,
        # The providers themselves, so a substituted provider never shares an
        # entry with the real one.
        discover_relations_in_bbox,
        discover_named_trail_ways_in_bbox,
    )


def _copy_harvest(harvest: _Harvest) -> _Harvest:
    """Same rows, own tile plan: the plan is mutated downstream."""
    return _Harvest(
        harvest.relations, harvest.ways, dict(harvest.tile_plan)
    )


async def _harvest_cached(
    search_bbox: tuple[float, float, float, float],
    scope: str,
    place: str,
    deadline: float,
    center: tuple[float, float] | None = None,
) -> _Harvest:
    key = _harvest_key(search_bbox, scope, center)
    cached = _HARVEST_CACHE.get(key)
    if (
        cached is not None
        and time.monotonic() - cached[0] < HARVEST_CACHE_TTL_SECONDS
    ):
        _HARVEST_CACHE.move_to_end(key)
        return _copy_harvest(cached[1])

    # Identical requests at the same time share one read.
    task = _HARVEST_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _harvest_rows(search_bbox, scope, place, deadline, center)
        )
        _HARVEST_INFLIGHT[key] = task
    try:
        harvest = await asyncio.shield(task)
    finally:
        if task.done() and _HARVEST_INFLIGHT.get(key) is task:
            _HARVEST_INFLIGHT.pop(key, None)

    if harvest.complete:
        _HARVEST_CACHE[key] = (time.monotonic(), harvest)
        _HARVEST_CACHE.move_to_end(key)
        while len(_HARVEST_CACHE) > HARVEST_CACHE_MAX_ENTRIES:
            _HARVEST_CACHE.popitem(last=False)
    return _copy_harvest(harvest)


def _outward_from(
    tiles: list[tuple[float, float, float, float]],
    center: tuple[float, float] | None,
) -> list[tuple[float, float, float, float]]:
    """
    Tiles nearest the searched place first.

    Every tile is queued at once and the queue is first come, first served, so
    this is the order they are read in. When the time budget runs out, the
    tiles left unread are then the ones farthest from where the user searched,
    not whichever lay at the far end of the grid.
    """
    if center is None:
        return tiles
    latitude, longitude = center
    scale = math.cos(math.radians(latitude))

    def distance(tile: tuple[float, float, float, float]) -> float:
        return math.hypot(
            ((tile[0] + tile[2]) / 2 - longitude) * scale,
            (tile[1] + tile[3]) / 2 - latitude,
        )

    return sorted(tiles, key=distance)


async def _harvest_rows(
    search_bbox: tuple[float, float, float, float],
    scope: str,
    place: str,
    deadline: float,
    center: tuple[float, float] | None = None,
) -> _Harvest:
    """Read the relations and named ways for a search area from Postpass."""
    tiles, tile_plan = _tile_plan(search_bbox)
    tiles = _outward_from(tiles, center)
    if len(tiles) == 1:
        # Small area: one query for each kind, no accounting overhead.
        limits = (
            (AREA_RELATION_ROW_LIMIT, AREA_WAY_ROW_LIMIT)
            if scope == "area"
            else (200, 2000)
        )
        relation_task = asyncio.create_task(
            discover_relations_in_bbox(search_bbox, limit=limits[0])
        )
        way_task = asyncio.create_task(
            discover_named_trail_ways_in_bbox(search_bbox, limit=limits[1])
        )
        tile_plan["tiles_queried"] = 1
        relation_result, way_result = await asyncio.gather(
            relation_task, way_task, return_exceptions=True
        )

        # The one tile may itself be capped. Split it so a dense place is read
        # in full, and report whatever is still cut.
        extra = await _expand_truncated(
            search_bbox,
            relation_rows=(
                None
                if isinstance(relation_result, BaseException)
                else relation_result
            ),
            way_rows=(
                None if isinstance(way_result, BaseException) else way_result
            ),
            relation_limit=limits[0],
            way_limit=limits[1],
            budget=_QueryBudget(MAX_SPLIT_QUERIES),
            semaphore=asyncio.Semaphore(TILE_CONCURRENCY),
            deadline=deadline,
        )
        if extra.relations:
            relation_result = _dedupe_rows(
                [*relation_result, *extra.relations], "relation_id"
            )
        if extra.ways:
            way_result = _dedupe_rows([*way_result, *extra.ways], "way_id")
        tile_plan["rows_truncated"] = {
            "relations": extra.relations_truncated,
            "ways": extra.ways_truncated,
        }
        tile_plan["tiles_split"] = extra.splits
        return _Harvest(relation_result, way_result, tile_plan)

    per_tile_ways = max(400, AREA_WAY_ROW_LIMIT // len(tiles))
    per_tile_relations = max(100, AREA_RELATION_ROW_LIMIT // len(tiles))
    way_hits: list[Any] = []
    relation_hits: list[Any] = []
    counters = {
        "queried": 0,
        "failed": 0,
        "relations_truncated": 0,
        "ways_truncated": 0,
        "splits": 0,
    }
    semaphore = asyncio.Semaphore(TILE_CONCURRENCY)
    # Splits have their own budget, not what the grid leaves over.
    budget = _QueryBudget(MAX_SPLIT_QUERIES)

    async def _one(tile: tuple[float, float, float, float]) -> None:
        async with semaphore:
            # A tile that only gets a slot after the budget is spent is not
            # queried; it is reported as skipped.
            if time.monotonic() >= deadline:
                return
            way_rows, relation_rows = await asyncio.gather(
                discover_named_trail_ways_in_bbox(tile, limit=per_tile_ways),
                discover_relations_in_bbox(tile, limit=per_tile_relations),
                return_exceptions=True,
            )
        if isinstance(way_rows, BaseException) and isinstance(
            relation_rows, BaseException
        ):
            counters["failed"] += 1
            return
        counters["queried"] += 1
        if not isinstance(way_rows, BaseException):
            way_hits.extend(way_rows)
        if not isinstance(relation_rows, BaseException):
            relation_hits.extend(relation_rows)
        extra = await _expand_truncated(
            tile,
            relation_rows=(
                None
                if isinstance(relation_rows, BaseException)
                else relation_rows
            ),
            way_rows=(
                None if isinstance(way_rows, BaseException) else way_rows
            ),
            relation_limit=per_tile_relations,
            way_limit=per_tile_ways,
            budget=budget,
            semaphore=semaphore,
            deadline=deadline,
        )
        way_hits.extend(extra.ways)
        relation_hits.extend(extra.relations)
        counters["relations_truncated"] += extra.relations_truncated
        counters["ways_truncated"] += extra.ways_truncated
        counters["splits"] += extra.splits

    # Every tile is scheduled at once; the semaphore alone bounds how many
    # run. Awaiting fixed batches made each batch wait for its slowest tile.
    await asyncio.gather(*(_one(tile) for tile in tiles))

    # A way or relation that crosses a tile boundary appears in two tiles. OSM
    # identity, not position, decides identity, so the first row wins.
    tile_plan["tiles_queried"] = counters["queried"]
    tile_plan["tiles_failed"] = counters["failed"]
    tile_plan["rows_truncated"] = {
        "relations": counters["relations_truncated"],
        "ways": counters["ways_truncated"],
    }
    tile_plan["tiles_split"] = counters["splits"]
    tile_plan["tiles_skipped"] = len(tiles) - counters["queried"]
    if tile_plan["tiles_skipped"] > 0:
        logger.warning(
            "Discovery coverage is incomplete for %r: %d of %d tiles were "
            "not searched.",
            place,
            tile_plan["tiles_skipped"],
            len(tiles),
        )
    return _Harvest(
        _dedupe_rows(relation_hits, "relation_id"),
        _dedupe_rows(way_hits, "way_id"),
        tile_plan,
    )


def _semantic_outcome(place: str, agent_result: Any) -> TrailDiscoveryResult:
    """The semantic layer's result, or an honest stand-in when it has none."""
    if agent_result is None:
        return TrailDiscoveryResult(
            place=place,
            trails=[],
            agent_available=False,
            provider="searxng+gemini",
            provider_status="pending",
            error=None,
        )
    if isinstance(agent_result, Exception):
        logger.warning(
            "Semantic trail discovery failed for %r: %s", place, agent_result
        )
        return TrailDiscoveryResult(
            place=place,
            trails=[],
            agent_available=False,
            provider="searxng+gemini",
            provider_status="unavailable",
            error="Semantic discovery failed",
        )
    return agent_result


async def _run_discovery(
    *,
    latitude: float,
    longitude: float,
    place: str,
    scope: str,
    search_bbox: tuple[float, float, float, float],
    bbox_source: str,
    include_semantic: bool,
    place_kind: str = "area",
    page: int = 1,
    page_size: int = MAX_MAP_READY_RESULTS,
) -> dict[str, Any]:
    """
    Shared discovery implementation.

    ``include_semantic=False`` returns only verified Postpass/OSM results so
    the map and list can render immediately. ``include_semantic=True`` adds
    the supplemental semantic layer (verified against Postpass) and the
    honest UNMAPPED candidates. The full result is a superset of the
    verified-only result, so a client may render the first and then replace
    it with the second.

    The Postpass rows are read once per search (``_harvest_cached``), so every
    page and the enrichment rank the same set.
    """
    started = time.monotonic()
    deadline = started + DISCOVERY_TIME_BUDGET_SECONDS
    agent_task: asyncio.Task | None = None
    if include_semantic:
        agent_task = asyncio.create_task(
            discover_trail_candidates(
                place=place,
                latitude=latitude,
                longitude=longitude,
            )
        )

    harvest = await _harvest_cached(
        search_bbox, scope, place, deadline, (latitude, longitude)
    )

    agent_result: Any = None
    if agent_task is not None:
        (agent_result,) = await asyncio.gather(
            agent_task, return_exceptions=True
        )

    return await _timed_assemble(
        started,
        place=place,
        scope=scope,
        latitude=latitude,
        longitude=longitude,
        search_bbox=search_bbox,
        bbox_source=bbox_source,
        semantic_result=_semantic_outcome(place, agent_result),
        relation_result=harvest.relations,
        way_result=harvest.ways,
        include_semantic=include_semantic,
        agent_result=agent_result,
        tile_plan=harvest.tile_plan,
        place_kind=place_kind,
        page=page,
        page_size=page_size,
    )


async def _assemble_discovery_result(
    *,
    place: str,
    scope: str,
    latitude: float,
    longitude: float,
    search_bbox: tuple[float, float, float, float],
    bbox_source: str,
    semantic_result: TrailDiscoveryResult,
    relation_result: Any,
    way_result: Any,
    include_semantic: bool,
    agent_result: Any,
    tile_plan: dict[str, Any],
    place_kind: str = "area",
    page: int = 1,
    page_size: int = MAX_MAP_READY_RESULTS,
) -> dict[str, Any]:
    """
    Turn raw Postpass rows plus the optional semantic layer into the response.

    Shared by the single-query path and the tiled large-area path so both
    report identical accounting.
    """
    provider_errors: dict[str, str | None] = {
        "semantic": (
            None
            if not isinstance(agent_result, Exception)
            else "Semantic provider failed"
        ),
        "relations": None,
        "ways": None,
    }
    if include_semantic and semantic_result.provider_status != "ok":
        provider_errors["semantic"] = (
            semantic_result.error
            or "Semantic provider is degraded"
        )
    postpass_relations: list[PostpassRelation] = []
    if isinstance(relation_result, Exception):
        provider_errors["relations"] = "Postpass relation discovery failed"
        logger.warning(
            "Postpass relation discovery failed for %r: %s",
            place,
            relation_result,
        )
    else:
        postpass_relations = list(relation_result)

    postpass_ways: list[PostpassWay] = []
    if isinstance(way_result, Exception):
        provider_errors["ways"] = "Postpass named-way discovery failed"
        logger.warning(
            "Postpass named-way discovery failed for %r: %s",
            place,
            way_result,
        )
    else:
        postpass_ways = list(way_result)

    agent_trails = list(semantic_result.trails) if include_semantic else []
    names_for_matching: list[str] = []
    seen_name_keys: set[str] = set()
    for item in agent_trails:
        for name in [item.name, *(item.aliases or [])]:
            key = _normalise_name(name)
            if key and key not in seen_name_keys:
                seen_name_keys.add(key)
                names_for_matching.append(name)
            if len(names_for_matching) >= MAX_NAMES_FOR_OSM_MATCHING:
                break
        if len(names_for_matching) >= MAX_NAMES_FOR_OSM_MATCHING:
            break
    if scope == "local" and place not in names_for_matching:
        names_for_matching.append(place)

    name_mapping: dict[str, Any] = {}
    if include_semantic and names_for_matching:
        try:
            name_mapping = await find_relations_by_names(
                names_for_matching,
                bbox=search_bbox,
                limit_per_name=5,
            )
        except Exception as exc:
            name_mapping = {}
            provider_errors["relations"] = (
                "Postpass semantic-name matching failed"
            )
            logger.warning(
                "Postpass semantic-name resolution failed for %r: %s",
                place,
                exc,
            )

    discovered: list[dict[str, Any]] = []
    seen_trail_ids: set[str] = set()
    seen_relation_ids: set[int] = set()
    seen_way_ids: set[int] = set()
    matched_semantic = 0
    rejected_geographic_ways = 0
    resolve_semaphore = asyncio.Semaphore(AGENT_RESOLUTION_CONCURRENCY)

    async def _resolve_bounded(
        item: DiscoveredTrail,
        diagnostics: dict[str, Any] | None = None,
    ) -> Any:
        async with resolve_semaphore:
            return await _resolve_agent_item(
                item,
                latitude=latitude,
                longitude=longitude,
                place=place,
                bbox=search_bbox,
                name_mapping=name_mapping,
                postpass_ways=postpass_ways,
                diagnostics=diagnostics,
            )

    resolution_diagnostics: list[dict[str, Any]] = [
        {} for _ in agent_trails
    ]
    resolved_candidates = await asyncio.gather(
        *(
            _resolve_bounded(item, diagnostic)
            for item, diagnostic in zip(
                agent_trails, resolution_diagnostics
            )
        ),
        return_exceptions=True,
    )
    for position, (item, candidate) in enumerate(
        zip(agent_trails, resolved_candidates)
    ):
        measured = resolution_diagnostics[position]
        if isinstance(candidate, BaseException):
            logger.warning(
                "Semantic candidate resolution failed for %r: %s",
                item.name,
                candidate,
            )
            candidate = None
        if candidate is not None:
            trail_id = str(candidate["trail_id"])
            if trail_id not in seen_trail_ids:
                seen_trail_ids.add(trail_id)
                if candidate.get("osm_type") == "relation":
                    seen_relation_ids.add(int(candidate["osm_id"]))
                elif candidate.get("osm_type") == "way":
                    seen_way_ids.add(int(candidate["osm_id"]))
                discovered.append(candidate)
                matched_semantic += 1
            continue
        # Every consulted row carries its serving source, so an unmapped
        # candidate reports whether the fallback was actually consulted
        # rather than assuming the primary answered.
        consulted_sources: set[str] = set()
        for relations in name_mapping.values():
            for relation in relations:
                source = str(getattr(relation, "source", "") or "")
                if source:
                    consulted_sources.add(source)
        for way in postpass_ways:
            source = str(getattr(way, "source", "") or "")
            if source:
                consulted_sources.add(source)
        unresolved = _unmapped_trail(
            item,
            resolution=measured,
            sources_tried=sorted(consulted_sources) or None,
        )
        if unresolved["trail_id"] in seen_trail_ids:
            continue
        seen_trail_ids.add(str(unresolved["trail_id"]))
        discovered.append(unresolved)

    for relation in postpass_relations:
        if relation.relation_id in seen_relation_ids:
            continue
        if not _geometry_intersects_bbox(relation.geometry, search_bbox):
            continue
        candidate = _relation_to_trail(
            relation,
            latitude=latitude,
            longitude=longitude,
            discovery_source=_discovery_source("postpass", relation),
            match_score=80.0,
        )
        seen_relation_ids.add(relation.relation_id)
        seen_trail_ids.add(str(candidate["trail_id"]))
        discovered.append(candidate)

    for way in postpass_ways:
        if way.way_id in seen_way_ids:
            continue
        accepted, evidence_score, _reasons, evidence_class = (
            _named_way_evidence(way, place=place)
        )
        if not accepted:
            rejected_geographic_ways += 1
            continue
        candidate = _way_to_trail(
            way,
            latitude=latitude,
            longitude=longitude,
            place=place,
            discovery_source=_discovery_source("postpass", way),
            match_score=evidence_score,
            candidate_type=(
                "named_local_path"
                if evidence_class == "weak"
                else "named_hiking_way"
            ),
        )
        candidate["evidence_class"] = evidence_class
        seen_way_ids.add(way.way_id)
        seen_trail_ids.add(str(candidate["trail_id"]))
        discovered.append(candidate)

    discovered = _collapse_connected_named_ways(discovered)

    # ---------------------------------------------------------------
    # PEAK-AWARE SEARCH
    # A summit search is answered with a measured relationship between each
    # real route and the summit, never with an assumed or synthesised link.
    # ---------------------------------------------------------------
    peak_search = _is_peak_kind(place_kind)
    summit_note: str | None = None
    if peak_search:
        associations: list[dict[str, Any]] = []
        for candidate in discovered:
            association = _peak_association(
                candidate.get("geometry"),
                latitude,
                longitude,
            )
            if association is None:
                continue
            candidate["peak_association"] = association["association"]
            candidate["peak_closest_approach_m"] = association[
                "closest_approach_m"
            ]
            associations.append(association)
        summit_note = _summit_coverage_note(associations, place)
        if associations:
            discovered.sort(
                key=lambda candidate: (
                    {
                        "summit_route": 0,
                        "peak_approach": 1,
                        "nearby_route": 2,
                    }.get(
                        str(candidate.get("peak_association") or ""),
                        3,
                    ),
                    _candidate_sort_key(candidate),
                )
            )

    mapped = sorted(
        [item for item in discovered if item.get("map_ready")],
        key=_candidate_sort_key,
    )
    unmapped = [item for item in discovered if not item.get("map_ready")]
    returned_mapped = mapped[
        (page - 1) * page_size : page * page_size
    ]
    # Measured for the page being returned only: a route in several pieces
    # says how large its gaps are, so a card is never read as one continuous
    # trail when it is not. Single lines have nothing to report.
    for item in returned_mapped:
        geometry = item.get("geometry")
        if (
            isinstance(geometry, dict)
            and geometry.get("type") == "MultiLineString"
        ):
            item["geometry_completeness"] = asdict(
                measure_geometry_completeness(geometry.get("coordinates") or [])
            )
    total_ranked = len(mapped)
    has_more = (page * page_size) < total_ranked
    # Verified trails beyond this page are held back rather than discarded,
    # but they are NOT in this response's `trails`. The count below is what
    # makes that visible: it used to be a hardcoded 0, so a page holding 100
    # of 150 verified trails reported zero truncation and was indistinguishable
    # from a complete result set. Unmapped candidates are never paginated and
    # so never contribute to this number.
    mapped_truncated = max(0, total_ranked - len(returned_mapped))
    ordered = [*returned_mapped, *unmapped]

    # A provider that answers with zero rows for an area it was asked about is
    # not the same thing as an area with no trails in it. The public Postpass
    # mirror is a partial, moving snapshot, and whole regions are simply absent
    # from it at any given moment. Reporting that as a successful empty search
    # would claim the region is empty, which is a claim the system cannot make.
    provider_rows = len(postpass_relations) + len(postpass_ways)
    providers_answered = not (
        provider_errors["relations"] or provider_errors["ways"]
    )
    tiles_total = int(tile_plan.get("tiles_total", 1))
    tiles_queried = int(tile_plan.get("tiles_queried", 1))
    tiles_skipped = int(tile_plan.get("tiles_skipped", 0))
    rows_truncated = {
        "relations": int(
            (tile_plan.get("rows_truncated") or {}).get("relations", 0)
        ),
        "ways": int((tile_plan.get("rows_truncated") or {}).get("ways", 0)),
    }
    no_provider_data = (
        providers_answered
        and provider_rows == 0
        and tiles_queried > 0
    )

    # A provider that FAILED is never reported as an empty area, however few
    # results came back. "No results" and "the source was unavailable" are
    # different facts, and only the first one supports a claim about the area.
    # This previously required `ordered` to be non-empty, so a total provider
    # outage was reported as a successful empty search.
    provider_failed = any(provider_errors.values())

    if provider_failed and ordered:
        status = "partial"
    elif provider_failed:
        status = "unavailable"
    elif no_provider_data:
        status = "no_provider_data"
    elif ordered:
        status = "success"
    else:
        status = "empty"

    center_lat = (search_bbox[1] + search_bbox[3]) / 2.0
    area_km2 = round(
        abs(
            (search_bbox[2] - search_bbox[0])
            * (search_bbox[3] - search_bbox[1])
            * 111.32
            * 111.32
            * max(0.05, math.cos(math.radians(center_lat)))
        ),
        1,
    )

    return {
        "source": "OpenStreetMap via Postpass with supplemental semantic discovery",
        "query": place,
        "scope": scope,
        "status": status,
        "place_kind": place_kind,
        "peak_search": {
            "is_peak_search": peak_search,
            "summit_note": summit_note,
            "summit_routes": sum(
                1
                for candidate in discovered
                if candidate.get("peak_association") == "summit_route"
            ),
            "approach_routes": sum(
                1
                for candidate in discovered
                if candidate.get("peak_association") == "peak_approach"
            ),
            "nearby_routes": sum(
                1
                for candidate in discovered
                if candidate.get("peak_association") == "nearby_route"
            ),
            "method": (
                "Closest approach measured with a local haversine over the "
                "route's own already-fetched geometry. No route is extended "
                "or synthesised to reach a summit."
            ),
        } if peak_search else None,
        "count": len(ordered),
        "mapped_count": len(mapped),
        "returned_mapped_count": len(returned_mapped),
        "mapped_truncated_count": mapped_truncated,
        "unmapped_count": len(unmapped),
        "trails": ordered,
        "result_counts": {
            "definition": (
                "relevance_accepted: distinct relevant trail candidates that "
                "passed the hiking-evidence model, across relations, named "
                "ways and connected components. mapped: those whose identity "
                "and geometry were verified against OpenStreetMap (Postpass "
                "when available, the Overpass fallback otherwise; each "
                "trail's geometry_provenance names which). unmapped: real "
                "candidates kept visible because no "
                "trustworthy geometry could be established. ranked: mapped "
                "results after deterministic ranking. shown: how many are on "
                "this page."
            ),
            "relevance_accepted": len(discovered),
            "mapped": len(mapped),
            "unmapped": len(unmapped),
            "ranked": total_ranked,
            "shown": len(returned_mapped),
            "shown_unmapped": len(unmapped),
            # Verified trails that exist and rank, but are held back on this
            # page. Zero means the page carries every mapped result.
            "mapped_truncated": mapped_truncated,
            # Counted over every mapped result, not only the ones on this
            # page. The interface states this as a subset of the mapped
            # total, so counting a page would understate it and make the
            # sentence "N of the M mapped trails" wrong for page 1.
            "weak_evidence": sum(
                1
                for candidate in mapped
                if candidate.get("evidence_class") == "weak"
            ),
            "weak_evidence_scope": "all_mapped_results",
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_ranked": total_ranked,
            "returned": len(returned_mapped),
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "unmapped_returned": len(unmapped),
            "mapped_truncated": mapped_truncated,
            "note": (
                "All ranked results are computed and ranked; this response "
                "carries one page of them. Request the next page to continue."
            ),
        },
        "coverage": {
            # Geography the user asked about.
            "area_considered": list(search_bbox),
            "area_considered_source": bbox_source,
            "area_km2": area_km2,
            # Geography actually queried. Never conflated with the area
            # considered: tiles that failed or were never reached are stated.
            "area_queried": tiles_queried > 0,
            "tiled": bool(tile_plan.get("tiled", False)),
            "tile_grid": tile_plan.get("tile_grid"),
            "tiles_total": tiles_total,
            "tiles_queried": tiles_queried,
            "tiles_failed": int(tile_plan.get("tiles_failed", 0)),
            "tiles_skipped": tiles_skipped,
            # Areas whose query still returned as many rows as its cap after
            # every allowed split. Rows there may exist that were never seen.
            "rows_truncated": rows_truncated,
            "tiles_split": int(tile_plan.get("tiles_split", 0)),
            # A search that returned no provider rows did not achieve
            # coverage, and neither did one whose provider failed outright,
            # or one that still had rows cut off.
            "coverage_complete": (
                tiles_skipped == 0
                and not no_provider_data
                and not provider_failed
                and not any(rows_truncated.values())
            ),
            "provider_returned_no_rows": no_provider_data,
            # Distinguishes "the source was asked and had nothing" from "the
            # source could not be asked", which are entirely different facts.
            "provider_failed": provider_failed,
            # Each pipeline stage, kept distinct from the others.
            "candidates_found": len(postpass_ways) + len(postpass_relations),
            "candidates_accepted": len(discovered),
            "candidates_verified": total_ranked,
            "candidates_ranked": total_ranked,
            "candidates_returned": len(returned_mapped),
            "note": (
                (
                    "The OpenStreetMap provider could not be reached, so this "
                    "area was not successfully searched. The absence of "
                    "results says nothing about whether trails are here. The "
                    "exact box that was attempted is in area_considered."
                )
                if provider_failed
                else (
                    "The OpenStreetMap provider answered but returned no rows "
                    "at all for this whole searched area. Either the public "
                    "Postpass mirror does not currently hold this area (it is "
                    "a partial, moving snapshot) or the searched coordinates "
                    "did not point where intended. This system cannot tell "
                    "those apart, and neither means the area has no trails. "
                    "The exact box that was queried is in area_considered; "
                    "no coverage claim is made."
                )
                if no_provider_data
                else "Coverage describes the geography actually searched. A "
                "result count is never a claim about the whole region."
            ),
        },
        "providers": {
            "semantic": {
                "status": semantic_result.provider_status,
                "candidates_found": len(agent_trails),
                "matched": matched_semantic,
                "unmapped": max(0, len(agent_trails) - matched_semantic),
                "search_results": semantic_result.search_result_count,
                # The observed path through the semantic layer. This exists
                # so "degraded" is diagnosable instead of a shrug: which
                # search backend actually answered, whether the fallback
                # fired, and why, if the reason is known. No credentials.
                **semantic_result.diagnostics,
                "message": (
                    "Semantic enrichment is delivered by "
                    "/trails/enrichment"
                    if not include_semantic
                    else None
                    if semantic_result.provider_status == "ok"
                    else (
                        "Semantic search answered through the Tavily "
                        "fallback because the local SearXNG instance "
                        "returned nothing"
                        if semantic_result.diagnostics.get(
                            "fallback_used"
                        )
                        else (
                            "Semantic search reached the web but trail "
                            "names were extracted without the language "
                            "model, using search titles only"
                            if semantic_result.provider_status
                            == "degraded"
                            else "Semantic search is temporarily "
                            "unavailable"
                        )
                    )
                ),
            },
            "postpass_relations": {
                "status": "failed" if provider_errors["relations"] else "ok",
                "found": len(postpass_relations),
                "message": provider_errors["relations"],
                # Which source actually served the rows. When the primary
                # failed and the fallback served, the rows say so and the
                # degradation is reported rather than hidden.
                "source": _osm_source(postpass_relations),
            },
            "postpass_ways": {
                "status": "failed" if provider_errors["ways"] else "ok",
                "found": len(postpass_ways),
                "accepted": len(postpass_ways) - rejected_geographic_ways,
                "rejected": rejected_geographic_ways,
                "message": provider_errors["ways"],
                "source": _osm_source(postpass_ways),
            },
        },
        "diagnostics": {
            "search_bbox": list(search_bbox),
            "search_bbox_source": bbox_source,
            "mapped": total_ranked,
            "returned_mapped": len(returned_mapped),
            "mapped_truncated": mapped_truncated,
            "unmapped": len(unmapped),
            "provider_returned_no_rows": no_provider_data,
        },
        "enrichment_pending": not include_semantic,
    }


@router.get("/trails/discover")
async def discover_trails(
    latitude: float = Query(...),
    longitude: float = Query(...),
    search_query: str = Query("", max_length=200),
    location_name: str = Query("", max_length=300),
    scope: str = Query("local", pattern="^(local|area)$"),
    bbox: str | None = Query(default=None, max_length=100),
    place_kind: str = Query("area", max_length=40),
    page: int = Query(1, ge=1),
    page_size: int = Query(
        MAX_MAP_READY_RESULTS, ge=1, le=MAX_MAP_READY_RESULTS
    ),
    request: Request = None,
):
    """
    Stage 1: verified Postpass/OSM results only.

    Returns only candidates whose identity and geometry were verified
    against Postpass, so every MAP_READY entry here is real. The supplemental
    semantic layer and the UNMAPPED candidates are delivered separately by
    ``/trails/enrichment`` so the map and list can render immediately.
    """
    _enforce_discovery_limit(request)
    page = _positive_int(page, 1)
    page_size = min(
        _positive_int(page_size, MAX_MAP_READY_RESULTS),
        MAX_MAP_READY_RESULTS,
    )
    place, search_bbox, bbox_source = _resolve_discovery_input(
        latitude,
        longitude,
        search_query,
        location_name,
        scope,
        bbox,
        place_kind,
    )
    return await _run_discovery(
        latitude=latitude,
        longitude=longitude,
        place=place,
        scope=scope,
        search_bbox=search_bbox,
        bbox_source=bbox_source,
        include_semantic=False,
        place_kind=place_kind,
        page=page,
        page_size=page_size,
    )


@router.get("/trails/enrichment")
async def enrich_trails(
    latitude: float = Query(...),
    longitude: float = Query(...),
    search_query: str = Query("", max_length=200),
    location_name: str = Query("", max_length=300),
    scope: str = Query("local", pattern="^(local|area)$"),
    bbox: str | None = Query(default=None, max_length=100),
    place_kind: str = Query("area", max_length=40),
    page: int = Query(1, ge=1),
    page_size: int = Query(
        MAX_MAP_READY_RESULTS, ge=1, le=MAX_MAP_READY_RESULTS
    ),
    request: Request = None,
):
    """
    Stage 2: full discovery including supplemental semantic discovery.

    Returns the complete authoritative list: verified OSM results, semantic
    candidates that were verified against Postpass, and honest UNMAPPED
    candidates. This is a superset of ``/trails/discover`` and is cached, so
    a client may render stage 1 first and then replace it with this.
    """
    _enforce_discovery_limit(request)
    page = _positive_int(page, 1)
    page_size = min(
        _positive_int(page_size, MAX_MAP_READY_RESULTS),
        MAX_MAP_READY_RESULTS,
    )
    place, search_bbox, bbox_source = _resolve_discovery_input(
        latitude,
        longitude,
        search_query,
        location_name,
        scope,
        bbox,
        place_kind,
    )
    return await _run_discovery(
        latitude=latitude,
        longitude=longitude,
        place=place,
        scope=scope,
        search_bbox=search_bbox,
        bbox_source=bbox_source,
        include_semantic=True,
        place_kind=place_kind,
        page=page,
        page_size=page_size,
    )

