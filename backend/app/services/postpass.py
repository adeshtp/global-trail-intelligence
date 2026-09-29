from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from unidecode import unidecode

from app.core.config import settings
from app.services import overpass as overpass_fallback


logger = logging.getLogger(__name__)


POSTPASS_URL = (
    os.getenv("POSTPASS_URL")
    or getattr(
        settings,
        "POSTPASS_URL",
        "https://postpass.geofabrik.de/api/interpreter",
    )
).strip()

POSTPASS_TIMEOUT_SECONDS = float(
    os.getenv(
        "POSTPASS_TIMEOUT_SECONDS",
        getattr(settings, "POSTPASS_TIMEOUT_SECONDS", 30.0),
    )
)

POSTPASS_CACHE_TTL_SECONDS = float(
    os.getenv(
        "POSTPASS_CACHE_TTL_SECONDS",
        getattr(settings, "POSTPASS_CACHE_TTL_SECONDS", 300.0),
    )
)
POSTPASS_CACHE_MAX_ENTRIES = max(
    32,
    min(int(os.getenv("POSTPASS_CACHE_MAX_ENTRIES", "256")), 2048),
)

# Small, bounded retry count for transport-level failures only. A
# persistent outage is never masked: the request still raises and the
# caller still reports the provider as unavailable.
POSTPASS_RETRIES = max(
    0,
    min(int(os.getenv("POSTPASS_RETRIES", "1")), 2),
)

# Bounded retry for TRANSIENT SERVER-SIDE failures only (HTTP 5xx). Postpass is
# a free public mirror and has been observed returning 503 for short stretches
# under load; that says nothing about the query, so it is worth one short
# backoff before declaring the area unsearchable. Client errors are never
# retried, and a persistent outage still surfaces as a provider failure.
POSTPASS_SERVER_RETRIES = max(
    0,
    min(
        int(
            getattr(
                settings,
                "POSTPASS_SERVER_RETRIES",
                os.getenv("POSTPASS_SERVER_RETRIES", "2"),
            )
            or 0
        ),
        3,
    ),
)

POSTPASS_SERVER_BACKOFF_SECONDS = max(
    0.5,
    min(
        float(
            getattr(
                settings,
                "POSTPASS_SERVER_BACKOFF_SECONDS",
                os.getenv("POSTPASS_SERVER_BACKOFF_SECONDS", "1.5"),
            )
            or 1.5
        ),
        10.0,
    ),
)


# Hiking is the primary route type.
# Foot is accepted as a secondary walking-route representation.
HIKING_ROUTE_TYPES = {
    "hiking",
    "foot",
    "walking",
}


# Generic words are ignored when generating semantic search variants, so that a
# name and a shorter form of the same name match. For example "<Place> Peak Trek"
# also matches "<Place> Trail" and "<Place> Peak". Only the words are generic:
# the place itself is never discarded, and nothing here is specific to any one
# location.
GENERIC_TRAIL_WORDS = {
    "trail",
    "trails",
    "trek",
    "trekking",
    "hike",
    "hiking",
    "route",
    "path",
    "footpath",
    "walk",
    "walking",
    "loop",
    "peak",
    "mountain",
    "hill",
    "summit",
    "ridge",
    "falls",
    "waterfall",
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
}


# Bounded in-process cache for repeated Postpass queries.
_CACHE: OrderedDict[
    tuple[Any, ...],
    tuple[float, Any],
] = OrderedDict()
_SQL_INFLIGHT: dict[str, asyncio.Task[list[dict[str, Any]]]] = {}


@dataclass
class PostpassWay:
    way_id: int
    name: str
    route: str | None
    highway: str | None
    sac_scale: str | None
    trail_visibility: str | None
    surface: str | None
    smoothness: str | None
    tracktype: str | None
    access: str | None
    incline: str | None
    incline_direction: str | None
    width: str | None
    assisted_trail: str | None
    aliases: list[str]
    geometry_type: str | None
    point_count: int
    length_km: float
    geometry: dict[str, Any] | None
    # Additional evidence channels. Optional so existing constructions of
    # this dataclass (tests, fixtures) remain valid.
    foot: str | None = None
    footway_use: str | None = None
    trailblazed: str | None = None
    designation: str | None = None
    hiking: str | None = None
    sport: str | None = None
    source: str = "OpenStreetMap via Postpass"


@dataclass(frozen=True)
class PostpassRelationMember:
    member_type: str
    ref: int
    role: str


@dataclass
class PostpassRelation:
    relation_id: int
    name: str
    route: str
    network: str | None
    ref: str | None
    description: str | None
    sac_scale: str | None
    surface: str | None
    trail_visibility: str | None
    members: list[PostpassRelationMember]
    aliases: list[str]
    geometry_type: str | None
    point_count: int
    length_km: float
    geometry: dict[str, Any] | None
    source: str = "OpenStreetMap via Postpass"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# BASIC HELPERS
# ============================================================


def _norm(value: Any) -> str:
    text = unidecode(
        unicodedata.normalize(
            "NFKC",
            str(value or ""),
        )
    ).strip().casefold()
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text)


def _norm_native(value: Any) -> str:
    """
    Normalize text WITHOUT transliteration.

    `_norm` folds to Latin for Python-side comparison. That is wrong for
    building Postpass SQL patterns, because the patterns are matched with
    ILIKE against the raw OSM tag values, which keep their original
    script. Transliterating here would make a non-Latin name produce a
    Latin pattern that can never match the stored tag.
    """
    text = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    ).strip().casefold()
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", text)


def _sql_string(value: Any) -> str:
    """
    Quote a text value as a PostgreSQL string literal.

    Postpass exposes a SQL endpoint rather than parameter binding,
    so user-derived text must be escaped before interpolation.
    """
    text = str(value or "")
    return "'" + text.replace("'", "''") + "'"


def _clean_bbox(
    bbox: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    west, south, east, north = (
        float(bbox[0]),
        float(bbox[1]),
        float(bbox[2]),
        float(bbox[3]),
    )

    if not all(
        math.isfinite(value)
        for value in (west, south, east, north)
    ):
        raise ValueError(
            "Bounding box contains non-finite values"
        )

    if not (
        -180.0 <= west < east <= 180.0
    ):
        raise ValueError(
            "Invalid longitude bounds"
        )

    if not (
        -90.0 <= south < north <= 90.0
    ):
        raise ValueError(
            "Invalid latitude bounds"
        )

    return (
        west,
        south,
        east,
        north,
    )


def _cache_has(
    key: tuple[Any, ...],
) -> bool:
    cached = _CACHE.get(key)

    if cached is None:
        return False

    created_at, _value = cached
    if (
        time.monotonic() - created_at
        > POSTPASS_CACHE_TTL_SECONDS
    ):
        _CACHE.pop(key, None)
        return False

    _CACHE.move_to_end(key)
    return True


def _cache_get(
    key: tuple[Any, ...],
) -> Any | None:
    if not _cache_has(key):
        return None

    return _CACHE[key][1]


def _cache_set(
    key: tuple[Any, ...],
    value: Any,
) -> None:
    _CACHE[key] = (
        time.monotonic(),
        value,
    )
    _CACHE.move_to_end(key)
    while len(_CACHE) > POSTPASS_CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


# ============================================================
# POSTPASS HTTP
# ============================================================


async def _execute_sql(
    sql: str,
) -> list[dict[str, Any]]:
    task = _SQL_INFLIGHT.get(sql)
    if task is None:
        task = asyncio.create_task(_execute_sql_uncached(sql))
        _SQL_INFLIGHT[sql] = task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _SQL_INFLIGHT.get(sql) is task:
            _SQL_INFLIGHT.pop(sql, None)


async def _execute_sql_uncached(
    sql: str,
) -> list[dict[str, Any]]:
    """
    Execute read-only SQL through Postpass.

    IMPORTANT:
    Postpass defaults to GeoJSON output. Our SQL deliberately returns
    ST_AsGeoJSON(...) as a normal JSON value instead of a raw geometry
    column, so the interpreter must be told:

        options[geojson] = false
    """
    # Postpass is a free public mirror with no availability guarantee, and it
    # has been observed answering HTTP 503 "No server is available to handle
    # this request" for seconds at a time under load. That is a transient
    # server-side condition, not a property of the query, so it is retried
    # with a short backoff. This is the least-complex resilience available: no
    # second provider, no cached geometry, no change to what is verified.
    #
    # A 4xx is never retried. A bad query will fail the same way every time,
    # and retrying it would only spend quota to reach the same error.
    last_error: str | None = None
    for attempt in range(POSTPASS_SERVER_RETRIES + 1):
        try:
            # One bounded transport retry per attempt, for transient
            # DNS/connection failures.
            transport = httpx.AsyncHTTPTransport(
                retries=POSTPASS_RETRIES
            )
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(POSTPASS_TIMEOUT_SECONDS),
                transport=transport,
                headers={
                    "User-Agent": (
                        "GoBeyond/0.1 "
                        "(educational outdoor intelligence project)"
                    ),
                    "Accept": "application/json",
                },
            ) as client:
                response = await client.post(
                    POSTPASS_URL,
                    data={
                        "options[geojson]": "false",
                        "data": sql,
                    },
                )
        except Exception as exc:
            last_error = f"Postpass request failed: {exc}"
            if attempt < POSTPASS_SERVER_RETRIES:
                await asyncio.sleep(
                    POSTPASS_SERVER_BACKOFF_SECONDS * (attempt + 1)
                )
                continue
            raise RuntimeError(last_error) from exc

        if response.status_code == 200:
            break
        if (
            500 <= response.status_code < 600
            and attempt < POSTPASS_SERVER_RETRIES
        ):
            last_error = (
                "Postpass returned HTTP "
                f"{response.status_code}: "
                f"{response.text.strip()[:200]}"
            )
            await asyncio.sleep(
                POSTPASS_SERVER_BACKOFF_SECONDS * (attempt + 1)
            )
            continue
        raise RuntimeError(
            "Postpass returned HTTP "
            f"{response.status_code}: "
            f"{response.text.strip()[:500]}"
        )
    else:
        raise RuntimeError(
            last_error or "Postpass is unavailable"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Postpass returned invalid JSON"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            "Postpass returned an invalid response object"
        )

    if payload.get("error"):
        raise RuntimeError(
            "Postpass query error: "
            f"{payload['error']}"
        )

    result = payload.get(
        "result"
    )

    if result is None:
        return []

    if not isinstance(
        result,
        list,
    ):
        raise RuntimeError(
            "Postpass returned an invalid result array"
        )

    return [
        row
        for row in result
        if isinstance(
            row,
            dict,
        )
    ]


# ============================================================
# GEOMETRY
# ============================================================


def _parse_geometry(
    value: Any,
) -> dict[str, Any] | None:
    if isinstance(
        value,
        dict,
    ):
        return value

    if isinstance(
        value,
        str,
    ):
        try:
            parsed = json.loads(
                value
            )
        except json.JSONDecodeError:
            return None

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    return None


def _geometry_point_count(
    geometry: dict[str, Any] | None,
) -> int:
    if not isinstance(
        geometry,
        dict,
    ):
        return 0

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if not isinstance(
        coordinates,
        list,
    ):
        return 0

    if geometry_type == "LineString":
        return sum(
            1
            for point in coordinates
            if isinstance(
                point,
                (list, tuple),
            )
            and len(point) >= 2
        )

    if geometry_type == "MultiLineString":
        total = 0

        for segment in coordinates:
            if not isinstance(
                segment,
                list,
            ):
                continue

            total += sum(
                1
                for point in segment
                if isinstance(
                    point,
                    (list, tuple),
                )
                and len(point) >= 2
            )

        return total

    return 0


def _geometry_is_usable(
    geometry: dict[str, Any] | None,
) -> bool:
    if not isinstance(
        geometry,
        dict,
    ):
        return False

    geometry_type = geometry.get(
        "type"
    )

    if geometry_type not in {
        "LineString",
        "MultiLineString",
    }:
        return False

    coordinates = geometry.get(
        "coordinates"
    )

    if not isinstance(
        coordinates,
        list,
    ):
        return False

    if _geometry_point_count(
        geometry
    ) < 2:
        return False

    def valid_point(
        point: Any,
    ) -> bool:
        if not isinstance(
            point,
            (list, tuple),
        ):
            return False

        if len(point) < 2:
            return False

        try:
            longitude = float(
                point[0]
            )
            latitude = float(
                point[1]
            )
        except (
            TypeError,
            ValueError,
        ):
            return False

        return (
            math.isfinite(
                longitude
            )
            and math.isfinite(
                latitude
            )
            and -180.0
            <= longitude
            <= 180.0
            and -90.0
            <= latitude
            <= 90.0
        )

    if geometry_type == "LineString":
        return all(
            valid_point(point)
            for point in coordinates
        )

    for segment in coordinates:
        if not isinstance(
            segment,
            list,
        ):
            return False

        if len(segment) < 2:
            return False

        if not all(
            valid_point(point)
            for point in segment
        ):
            return False

    return True


def _relation_members(
    value: Any,
) -> list[PostpassRelationMember]:
    if not isinstance(value, list):
        return []
    members: list[PostpassRelationMember] = []
    for raw_member in value:
        if not isinstance(raw_member, dict):
            continue
        try:
            member_type = _norm(raw_member.get("type")).upper()
            ref = int(raw_member.get("ref"))
        except (TypeError, ValueError):
            continue
        if not member_type or ref <= 0:
            continue
        members.append(
            PostpassRelationMember(
                member_type=member_type,
                ref=ref,
                role=_norm(raw_member.get("role")),
            )
        )
    return members


# ============================================================
# RELATION NORMALISATION
# ============================================================


def _relation_from_row(
    row: dict[str, Any],
) -> PostpassRelation | None:
    try:
        relation_id = int(
            row.get(
                "relation_id"
            )
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    name = ""

    for name_key in (
        "name",
        "name_en",
        "int_name",
        "official_name",
        "alt_name",
        "loc_name",
        "short_name",
    ):
        value = str(
            row.get(name_key)
            or ""
        ).strip()

        if value:
            name = value
            break

    route = _norm(
        row.get(
            "route"
        )
    )

    if relation_id <= 0:
        return None

    if not name:
        return None

    if route not in HIKING_ROUTE_TYPES:
        return None

    geometry = _parse_geometry(
        row.get(
            "geometry"
        )
    )

    try:
        point_count = int(
            row.get(
                "point_count"
            )
            or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        point_count = 0

    try:
        length_km = float(
            row.get(
                "length_km"
            )
            or 0.0
        )
    except (
        TypeError,
        ValueError,
    ):
        length_km = 0.0

    aliases: list[str] = []

    for key in (
        "name_en",
        "int_name",
        "alt_name",
        "official_name",
        "loc_name",
        "short_name",
        # A former name is real OpenStreetMap evidence about this exact
        # feature, so a trail renamed upstream is still findable by the name
        # people actually know. It is deliberately never a fallback for the
        # primary `name`: that would relabel a route with a name it no
        # longer has.
        "old_name",
        # `alt_name` carries every semicolon-separated variant on a single
        # tag, so it arrives as one string. Preserved as-is below rather
        # than truncated, because a multi-name tag is still real evidence.
    ):
        value = str(
            row.get(
                key
            )
            or ""
        ).strip()

        if value:
            aliases.append(
                value
            )

    return PostpassRelation(
        relation_id=relation_id,
        name=name,
        route=route,
        network=(
            str(
                row.get(
                    "network"
                )
            ).strip()
            if row.get(
                "network"
            )
            else None
        ),
        ref=(
            str(
                row.get(
                    "ref"
                )
            ).strip()
            if row.get(
                "ref"
            )
            else None
        ),
        description=(
            str(
                row.get(
                    "description"
                )
            ).strip()
            if row.get(
                "description"
            )
            else None
        ),
        sac_scale=_norm(
            row.get("sac_scale")
        ) or None,
        surface=_norm(
            row.get("surface")
        ) or None,
        trail_visibility=_norm(
            row.get("trail_visibility")
        ) or None,
        members=_relation_members(
            row.get("members")
        ),
        aliases=list(
            dict.fromkeys(
                aliases
            )
        ),
        geometry_type=(
            str(
                row.get(
                    "geometry_type"
                )
            )
            if row.get(
                "geometry_type"
            )
            else None
        ),
        point_count=point_count,
        length_km=length_km,
        geometry=geometry,
    )


# ============================================================
# SEMANTIC NAME MATCHING
# ============================================================


def _name_variants(
    value: str,
) -> list[str]:
    """
    Build deterministic search variants.

    A name that carries generic route words is also searched for without them,
    in decreasing order of specificity, so a fuller name matches a shorter
    spelling of the same place. For "<Place> Peak Trek" the variants are the
    full name, then "<place> peak", then "<place>". The place name itself is
    always retained; only the route-type words are dropped.
    """
    words = re.findall(
        r"[a-z0-9]+",
        _norm_native(value),
    )

    if not words:
        return []

    variants: list[str] = []

    full = " ".join(
        words
    )

    if full:
        variants.append(
            full
        )

    significant = [
        word
        for word in words
        if word not in GENERIC_TRAIL_WORDS
        and word not in SEARCH_STOPWORDS
        and len(word) >= 3
    ]

    if significant:
        variants.append(
            " ".join(
                significant
            )
        )

        variants.append(
            significant[0]
        )

        if len(significant) >= 2:
            variants.append(
                " ".join(
                    significant[:2]
                )
            )

    return list(
        dict.fromkeys(
            variants
        )
    )


def _match_score(
    requested: str,
    relation: PostpassRelation,
) -> float:
    requested_norm = _norm(
        requested
    )

    requested_tokens = {
        token
        for token in re.findall(
            r"[a-z0-9]+",
            requested_norm,
        )
        if token not in GENERIC_TRAIL_WORDS
        and token not in SEARCH_STOPWORDS
    }

    if not requested_tokens:
        requested_tokens = set(
            re.findall(
                r"[a-z0-9]+",
                requested_norm,
            )
        )

    best_score = 0.0

    for candidate_name in (
        relation.name,
        *relation.aliases,
    ):
        candidate_norm = _norm(
            candidate_name
        )

        if not candidate_norm:
            continue

        if (
            candidate_norm
            == requested_norm
        ):
            best_score = max(
                best_score,
                100.0,
            )
            continue

        if (
            requested_norm
            and requested_norm in candidate_norm
        ):
            best_score = max(
                best_score,
                90.0,
            )

        if (
            candidate_norm in requested_norm
        ):
            best_score = max(
                best_score,
                82.0,
            )

        candidate_tokens = set(
            re.findall(
                r"[a-z0-9]+",
                candidate_norm,
            )
        )

        if requested_tokens:
            overlap = len(
                requested_tokens
                & candidate_tokens
            )

            score = (
                60.0
                * overlap
                / len(requested_tokens)
            )

            best_score = max(
                best_score,
                score,
            )

    return best_score


# ============================================================
# DISCOVERY
# ============================================================


class BboxRows(list):
    """
    Rows returned for one bounding box, plus whether the query was capped.

    ``truncated`` is True when the query returned as many rows as its
    ``LIMIT``, which means rows may exist that were never seen. It is a plain
    list otherwise, so callers that only iterate are unaffected.
    """

    truncated: bool = False


async def discover_relations_in_bbox(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
    *,
    limit: int = 100,
) -> BboxRows:
    """
    Discover named hiking/foot route relations with rendered geometry
    overlapping the requested geographic area.

    This is an OSM map-coverage query.

    It must NOT be interpreted as an exhaustive catalogue of every
    hiking experience known by people or tourism sources.
    """
    bbox = _clean_bbox(
        bbox
    )

    safe_limit = max(
        1,
        min(
            int(limit),
            1000,
        ),
    )

    cache_key = (
        "bbox",
        *(
            round(
                value,
                5,
            )
            for value in bbox
        ),
        safe_limit,
    )

    cached = _cache_get(
        cache_key
    )

    if cached is not None:
        return cached

    west, south, east, north = bbox

    sql = f"""
SELECT
    r.id AS relation_id,
    r.members AS members,
    COALESCE(
        NULLIF(r.tags->>'name', ''),
        NULLIF(r.tags->>'name:en', ''),
        NULLIF(r.tags->>'int_name', ''),
        NULLIF(r.tags->>'official_name', ''),
        NULLIF(r.tags->>'alt_name', ''),
        NULLIF(r.tags->>'loc_name', ''),
        NULLIF(r.tags->>'short_name', '')
    ) AS name,
    r.tags->>'route' AS route,
    r.tags->>'network' AS network,
    r.tags->>'ref' AS ref,
    r.tags->>'description' AS description,
    r.tags->>'sac_scale' AS sac_scale,
    r.tags->>'surface' AS surface,
    r.tags->>'trail_visibility' AS trail_visibility,
    r.tags->>'alt_name' AS alt_name,
    r.tags->>'official_name' AS official_name,
    r.tags->>'loc_name' AS loc_name,
    r.tags->>'short_name' AS short_name,
    r.tags->>'old_name' AS old_name,
    r.tags->>'name:en' AS name_en,
    r.tags->>'int_name' AS int_name,
    ST_GeometryType(l.geom) AS geometry_type,
    ST_NPoints(l.geom) AS point_count,
    ROUND(
        (ST_Length(l.geom::geography) / 1000.0)::numeric,
        2
    ) AS length_km,
    ST_AsGeoJSON(l.geom)::json AS geometry
FROM planet_osm_rels AS r
JOIN postpass_line AS l
  ON l.osm_type = 'R'
 AND l.osm_id = r.id
WHERE r.tags->>'type' = 'route'
  AND r.tags->>'route' IN ('hiking', 'foot', 'walking')
  AND (
      r.tags ? 'name'
      OR r.tags ? 'name:en'
      OR r.tags ? 'int_name'
      OR r.tags ? 'official_name'
      OR r.tags ? 'alt_name'
      OR r.tags ? 'loc_name'
      OR r.tags ? 'short_name'
  )
  AND (
      r.tags->>'route' = 'hiking'
      OR lower(COALESCE(r.tags->>'name', r.tags->>'name:en', r.tags->>'int_name', r.tags->>'official_name', r.tags->>'alt_name', r.tags->>'loc_name', r.tags->>'short_name')) !~*
         '(^| )(residential road|service road|access road|main road|road)( |$)'
  )
  AND l.geom && ST_MakeEnvelope(
      {west},
      {south},
      {east},
      {north},
      4326
  )
ORDER BY
    CASE
        WHEN r.tags->>'route' = 'hiking' THEN 0
        ELSE 1
    END,
    CASE
        WHEN lower(COALESCE(r.tags->>'name', r.tags->>'name:en', r.tags->>'int_name', r.tags->>'official_name', r.tags->>'alt_name', r.tags->>'loc_name', r.tags->>'short_name')) ~*
             '(^| )(trail|trails|trek|trekking|hike|hiking|route|walk|walking|loop|peak|summit|ridge|waterfall|falls)( |$)'
        THEN 0
        ELSE 1
    END,
    l.length_m DESC NULLS LAST,
    lower(COALESCE(r.tags->>'name', r.tags->>'name:en', r.tags->>'int_name', r.tags->>'official_name', r.tags->>'alt_name', r.tags->>'loc_name', r.tags->>'short_name')),
    r.id
LIMIT {safe_limit}
"""

    try:
        rows = await _execute_sql(
            sql
        )
    except Exception as exc:
        # The primary source failed. Fall back to Overpass rather than
        # reporting zero mapped trails for a live area: the failure is the
        # provider's, not the area's. A successful Postpass answer never
        # reaches this branch, so empty results are never second-guessed.
        logger.warning(
            "Postpass bbox relation discovery failed; "
            "using Overpass fallback: %s",
            exc,
        )
        return await overpass_fallback.overpass_relations_in_bbox(
            bbox,
            limit=safe_limit,
        )

    relations = BboxRows()

    for row in rows:
        relation = _relation_from_row(
            row
        )

        if relation is None:
            continue

        if not _geometry_is_usable(
            relation.geometry
        ):
            continue

        relations.append(
            relation
        )

    # Judged on the rows the query returned, not the ones that survived the
    # checks above: a capped query is capped even if some rows were unusable.
    relations.truncated = len(rows) >= safe_limit

    _cache_set(
        cache_key,
        relations,
    )

    return relations



# ============================================================
# NAMED OSM WAY DISCOVERY
# ============================================================


def _way_from_row(
    row: dict[str, Any],
) -> PostpassWay | None:
    try:
        way_id = int(row.get("way_id"))
    except (TypeError, ValueError):
        return None

    name = str(row.get("name") or "").strip()

    if way_id <= 0 or not name:
        return None

    geometry = _parse_geometry(row.get("geometry"))

    if not _geometry_is_usable(geometry):
        return None

    try:
        point_count = int(row.get("point_count") or 0)
    except (TypeError, ValueError):
        point_count = 0

    try:
        length_km = float(row.get("length_km") or 0.0)
    except (TypeError, ValueError):
        length_km = 0.0

    aliases: list[str] = []

    for key in (
        "name_en",
        "int_name",
        "alt_name",
        "official_name",
        "loc_name",
        "short_name",
        "old_name",
    ):
        value = str(row.get(key) or "").strip()

        if value and _norm(value) != _norm(name):
            aliases.append(value)

    return PostpassWay(
        way_id=way_id,
        name=name,
        route=_norm(row.get("route")) or None,
        highway=_norm(row.get("highway")) or None,
        sac_scale=_norm(row.get("sac_scale")) or None,
        trail_visibility=_norm(row.get("trail_visibility")) or None,
        surface=_norm(row.get("surface")) or None,
        smoothness=_norm(row.get("smoothness")) or None,
        tracktype=_norm(row.get("tracktype")) or None,
        access=_norm(row.get("access")) or None,
        incline=(
            str(row.get("incline")).strip()
            if row.get("incline")
            else None
        ),
        incline_direction=_norm(row.get("incline_direction")) or None,
        width=(
            str(row.get("width")).strip()
            if row.get("width")
            else None
        ),
        assisted_trail=_norm(row.get("assisted_trail")) or None,
        foot=_norm(row.get("foot")) or None,
        footway_use=_norm(row.get("footway")) or None,
        trailblazed=_norm(row.get("trailblazed")) or None,
        designation=_norm(row.get("designation")) or None,
        hiking=_norm(row.get("hiking")) or None,
        sport=_norm(row.get("sport")) or None,
        aliases=list(dict.fromkeys(aliases)),
        geometry_type=(
            str(row.get("geometry_type"))
            if row.get("geometry_type")
            else None
        ),
        point_count=point_count,
        length_km=length_km,
        geometry=geometry,
    )


async def discover_named_trail_ways_in_bbox(
    bbox: tuple[
        float,
        float,
        float,
        float,
    ],
    *,
    limit: int = 2000,
) -> BboxRows:
    """
    Discover named, trail-capable OSM ways with real Postpass geometry.

    This is deliberately separate from route-relation discovery because
    many real trails in OSM are mapped as standalone named ways rather
    than as route relations.

    No geometry is created here.
    """
    bbox = _clean_bbox(bbox)

    safe_limit = max(
        1,
        min(
            int(limit),
            5000,
        ),
    )

    cache_key = (
        "named_ways_bbox",
        *(
            round(
                value,
                5,
            )
            for value in bbox
        ),
        safe_limit,
    )

    cached = _cache_get(cache_key)

    if cached is not None:
        return cached

    west, south, east, north = bbox

    sql = f"""
SELECT
    l.osm_id AS way_id,
    l.tags->>'name' AS name,
    l.tags->>'name:en' AS name_en,
    l.tags->>'int_name' AS int_name,
    l.tags->>'alt_name' AS alt_name,
    l.tags->>'official_name' AS official_name,
    l.tags->>'loc_name' AS loc_name,
    l.tags->>'short_name' AS short_name,
    l.tags->>'old_name' AS old_name,
    l.tags->>'route' AS route,
    l.tags->>'highway' AS highway,
    l.tags->>'sac_scale' AS sac_scale,
    l.tags->>'trail_visibility' AS trail_visibility,
    l.tags->>'surface' AS surface,
    l.tags->>'smoothness' AS smoothness,
    l.tags->>'tracktype' AS tracktype,
    l.tags->>'access' AS access,
    l.tags->>'incline' AS incline,
    l.tags->>'incline_direction' AS incline_direction,
    l.tags->>'width' AS width,
    l.tags->>'assisted_trail' AS assisted_trail,
    l.tags->>'foot' AS foot,
    l.tags->>'footway' AS footway,
    l.tags->>'trailblazed' AS trailblazed,
    l.tags->>'designation' AS designation,
    l.tags->>'hiking' AS hiking,
    l.tags->>'sport' AS sport,
    ST_GeometryType(l.geom) AS geometry_type,
    ST_NPoints(l.geom) AS point_count,
    ROUND(
        (l.length_m / 1000.0)::numeric,
        3
    ) AS length_km,
    ST_AsGeoJSON(l.geom)::json AS geometry
FROM postpass_line AS l
WHERE l.osm_type = 'W'
  AND l.geom && ST_MakeEnvelope(
      {west},
      {south},
      {east},
      {north},
      4326
  )
  AND l.tags ? 'name'
  AND (
      l.tags->>'route' IN ('hiking', 'foot', 'walking')
      OR l.tags->>'sac_scale' IS NOT NULL
      OR l.tags->>'trail_visibility' IS NOT NULL
      OR l.tags->>'trailblazed' IS NOT NULL
      OR l.tags->>'designation' IS NOT NULL
      OR l.tags->>'hiking' IS NOT NULL
      OR l.tags->>'sport' IN ('hiking', 'walking', 'trail_running')
      OR l.tags->>'highway' IN (
          'path',
          'track',
          'footway',
          'steps',
          'bridleway',
          'pedestrian'
      )
  )
ORDER BY
    CASE
        WHEN l.tags->>'route' = 'hiking' THEN 0
        WHEN l.tags->>'route' IN ('foot', 'walking') THEN 1
        WHEN l.tags->>'sac_scale' IS NOT NULL THEN 2
        WHEN l.tags->>'trail_visibility' IS NOT NULL THEN 3
        WHEN l.tags->>'highway' IN ('path', 'bridleway', 'steps') THEN 4
        WHEN l.tags->>'highway' IN ('footway', 'track') THEN 5
        ELSE 6
    END,
    l.length_m DESC NULLS LAST,
    lower(l.tags->>'name'),
    l.osm_id
LIMIT {safe_limit}
"""

    try:
        rows = await _execute_sql(sql)
    except Exception as exc:
        logger.warning(
            "Postpass named-way discovery failed; "
            "using Overpass fallback: %s",
            exc,
        )
        return await overpass_fallback.overpass_named_ways_in_bbox(
            bbox,
            limit=safe_limit,
        )

    ways = BboxRows()

    for row in rows:
        way = _way_from_row(row)

        if way is None:
            continue

        ways.append(way)

    ways.truncated = len(rows) >= safe_limit

    _cache_set(
        cache_key,
        ways,
    )

    return ways


# ============================================================
# NAME-BASED RELATION RESOLUTION
# ============================================================


async def _find_relations_by_names_batch(
    names: list[str],
    *,
    bbox: tuple[
        float,
        float,
        float,
        float,
    ] | None = None,
    limit_per_name: int = 10,
) -> dict[
    str,
    list[PostpassRelation],
]:
    """
    Find actual OSM hiking/foot route relations corresponding to
    semantic trail names.

    Name matching is intentionally two-stage:

        1. SQL finds plausible textual candidates.
        2. Python applies deterministic matching scores.

    This prevents the semantic agent from being trusted directly.
    """
    cleaned_names: list[str] = []

    seen_names: set[str] = set()

    for value in names:
        cleaned = " ".join(
            str(value or "")
            .strip()
            .split()
        )

        key = _norm(
            cleaned
        )

        if (
            cleaned
            and key not in seen_names
        ):
            seen_names.add(
                key
            )
            cleaned_names.append(
                cleaned
            )

    if not cleaned_names:
        return {}

    safe_limit = max(
        1,
        min(
            int(limit_per_name),
            20,
        ),
    )

    bbox_value = (
        _clean_bbox(bbox)
        if bbox is not None
        else None
    )

    cache_key = (
        "names",
        tuple(
            _norm(name)
            for name in cleaned_names
        ),
        bbox_value,
        safe_limit,
    )

    cached = _cache_get(
        cache_key
    )

    if cached is not None:
        return cached

    # Build a small shared set of SQL search terms from all semantic names.
    search_terms: set[str] = set()

    for name in cleaned_names:
        search_terms.update(
            _name_variants(
                name
            )
        )

    if not search_terms:
        return {
            name: []
            for name in cleaned_names
        }

    conditions: list[str] = []

    for term in sorted(
        search_terms,
        key=lambda value: (
            -len(value),
            value,
        ),
    ):
        literal = _sql_string(
            f"%{term}%"
        )

        conditions.append(
            "("
            "r.tags->>'name' ILIKE "
            f"{literal} "
            "OR r.tags->>'alt_name' ILIKE "
            f"{literal} "
            "OR r.tags->>'official_name' ILIKE "
            f"{literal} "
            "OR r.tags->>'loc_name' ILIKE "
            f"{literal} "
            "OR r.tags->>'short_name' ILIKE "
            f"{literal} "
            "OR r.tags->>'name:en' ILIKE "
            f"{literal} "
            "OR r.tags->>'int_name' ILIKE "
            f"{literal}"
            ")"
        )

    bbox_sql = ""

    if bbox_value is not None:
        west, south, east, north = bbox_value

        bbox_sql = f"""
  AND l.geom && ST_MakeEnvelope(
      {west},
      {south},
      {east},
      {north},
      4326
  )
"""

    sql = f"""
SELECT
    r.id AS relation_id,
    r.members AS members,
    COALESCE(
        NULLIF(r.tags->>'name', ''),
        NULLIF(r.tags->>'name:en', ''),
        NULLIF(r.tags->>'int_name', ''),
        NULLIF(r.tags->>'official_name', ''),
        NULLIF(r.tags->>'alt_name', ''),
        NULLIF(r.tags->>'loc_name', ''),
        NULLIF(r.tags->>'short_name', '')
    ) AS name,
    r.tags->>'route' AS route,
    r.tags->>'network' AS network,
    r.tags->>'ref' AS ref,
    r.tags->>'description' AS description,
    r.tags->>'sac_scale' AS sac_scale,
    r.tags->>'surface' AS surface,
    r.tags->>'trail_visibility' AS trail_visibility,
    r.tags->>'alt_name' AS alt_name,
    r.tags->>'official_name' AS official_name,
    r.tags->>'loc_name' AS loc_name,
    r.tags->>'short_name' AS short_name,
    r.tags->>'old_name' AS old_name,
    r.tags->>'name:en' AS name_en,
    r.tags->>'int_name' AS int_name,
    ST_GeometryType(l.geom) AS geometry_type,
    ST_NPoints(l.geom) AS point_count,
    ROUND(
        (ST_Length(l.geom::geography) / 1000.0)::numeric,
        2
    ) AS length_km,
    ST_AsGeoJSON(l.geom)::json AS geometry
FROM planet_osm_rels AS r
JOIN postpass_line AS l
  ON l.osm_type = 'R'
 AND l.osm_id = r.id
WHERE r.tags->>'type' = 'route'
  AND r.tags->>'route' IN ('hiking', 'foot', 'walking')
  AND (
      r.tags ? 'name'
      OR r.tags ? 'name:en'
      OR r.tags ? 'int_name'
      OR r.tags ? 'official_name'
      OR r.tags ? 'alt_name'
      OR r.tags ? 'loc_name'
      OR r.tags ? 'short_name'
  )
  AND (
      {" OR ".join(conditions)}
  )
  {bbox_sql}
ORDER BY
    lower(COALESCE(r.tags->>'name', r.tags->>'name:en', r.tags->>'int_name', r.tags->>'official_name', r.tags->>'alt_name', r.tags->>'loc_name', r.tags->>'short_name')),
    r.id
LIMIT {min(250, max(50, safe_limit * max(5, len(cleaned_names) * 4)))}
"""

    rows = await _execute_sql(
        sql
    )

    relations: list[
        PostpassRelation
    ] = []

    for row in rows:
        relation = _relation_from_row(
            row
        )

        if relation is None:
            continue

        if not _geometry_is_usable(
            relation.geometry
        ):
            continue

        relations.append(
            relation
        )

    result: dict[
        str,
        list[PostpassRelation],
    ] = {
        name: []
        for name in cleaned_names
    }

    for requested_name in cleaned_names:
        ranked: list[
            tuple[
                float,
                PostpassRelation,
            ]
        ] = []

        for relation in relations:
            score = _match_score(
                requested_name,
                relation,
            )

            if score < 55.0:
                continue

            ranked.append(
                (
                    score,
                    relation,
                )
            )

        ranked.sort(
            key=lambda item: (
                -item[0],
                _norm(
                    item[1].name
                ),
                item[1].relation_id,
            )
        )

        result[requested_name] = [
            relation
            for _score, relation in ranked[
                :safe_limit
            ]
        ]

    _cache_set(
        cache_key,
        result,
    )

    return result


# ============================================================
# DIRECT RELATION LOOKUP
# ============================================================




async def find_relations_by_names(
    names: list[str],
    *,
    bbox: tuple[
        float,
        float,
        float,
        float,
    ] | None = None,
    limit_per_name: int = 10,
) -> dict[
    str,
    list[PostpassRelation],
]:
    """Resolve multiple semantic names without creating an oversized SQL query.

    Postpass can time out when dozens of names are combined into one large
    ILIKE/OR expression. Keep each request compact, then merge the deterministic
    results locally.
    """
    cleaned: list[str] = []
    seen: set[str] = set()

    for value in names:
        name = " ".join(str(value or "").strip().split())
        key = _norm(name)
        if name and key not in seen:
            seen.add(key)
            cleaned.append(name)

    if not cleaned:
        return {}

    merged: dict[str, list[PostpassRelation]] = {name: [] for name in cleaned}

    chunk_size = max(3, min(int(os.getenv("POSTPASS_NAME_BATCH_SIZE", "5")), 8))
    concurrency = max(
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
    chunks = [
        cleaned[index:index + chunk_size]
        for index in range(0, len(cleaned), chunk_size)
    ]
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_chunk(
        chunk: list[str],
    ) -> tuple[list[str], dict[str, list[PostpassRelation]] | None, Exception | None]:
        async with semaphore:
            try:
                return (
                    chunk,
                    await _find_relations_by_names_batch(
                        chunk,
                        bbox=bbox,
                        limit_per_name=limit_per_name,
                    ),
                    None,
                )
            except Exception as exc:  # noqa: BLE001 - reported below
                return chunk, None, exc

    # Bounded concurrency, but results are applied back in the original
    # chunk order so merging and failure accounting stay deterministic.
    chunk_results = await asyncio.gather(
        *(_run_chunk(chunk) for chunk in chunks),
    )

    failed_chunks = 0
    for chunk, batch, error in chunk_results:
        if error is not None or batch is None:
            failed_chunks += 1
            logger.warning(
                "Postpass name batch failed (%d-%d): %s",
                cleaned.index(chunk) + 1,
                cleaned.index(chunk) + len(chunk),
                error,
            )
            continue

        for name in chunk:
            merged[name].extend(batch.get(name, []))

    if failed_chunks == len(chunks):
        # Every primary batch failed. Resolve through Overpass rather than
        # dropping all semantic candidates for a provider outage.
        logger.warning(
            "All Postpass name-resolution batches failed; "
            "using Overpass fallback for %d names",
            len(cleaned),
        )
        return await overpass_fallback.overpass_relations_by_names(
            cleaned,
            bbox=bbox,
            limit_per_name=limit_per_name,
        )

    for name in merged:
        deduped: dict[int, PostpassRelation] = {}
        for relation in merged[name]:
            deduped[relation.relation_id] = relation
        merged[name] = list(deduped.values())

    return merged



# ============================================================
# DIRECT RELATION LOOKUP
# ============================================================


async def get_relation(
    relation_id: int,
) -> PostpassRelation | None:
    """
    Fetch one actual OSM hiking/foot route relation and its
    rendered Postpass geometry.
    """
    try:
        relation_id = int(relation_id)
    except (TypeError, ValueError):
        return None

    if relation_id <= 0:
        return None

    cache_key = (
        "relation",
        relation_id,
    )

    if _cache_has(cache_key):
        return _cache_get(cache_key)

    sql = f"""
SELECT
    r.id AS relation_id,
    r.members AS members,
    COALESCE(
        NULLIF(r.tags->>'name', ''),
        NULLIF(r.tags->>'name:en', ''),
        NULLIF(r.tags->>'int_name', ''),
        NULLIF(r.tags->>'official_name', ''),
        NULLIF(r.tags->>'alt_name', ''),
        NULLIF(r.tags->>'loc_name', ''),
        NULLIF(r.tags->>'short_name', '')
    ) AS name,
    r.tags->>'name:en' AS name_en,
    r.tags->>'int_name' AS int_name,
    r.tags->>'route' AS route,
    r.tags->>'network' AS network,
    r.tags->>'ref' AS ref,
    r.tags->>'description' AS description,
    r.tags->>'sac_scale' AS sac_scale,
    r.tags->>'surface' AS surface,
    r.tags->>'trail_visibility' AS trail_visibility,
    r.tags->>'alt_name' AS alt_name,
    r.tags->>'official_name' AS official_name,
    r.tags->>'loc_name' AS loc_name,
    r.tags->>'short_name' AS short_name,
    r.tags->>'old_name' AS old_name,
    ST_GeometryType(l.geom) AS geometry_type,
    ST_NPoints(l.geom) AS point_count,
    ROUND(
        (ST_Length(l.geom::geography) / 1000.0)::numeric,
        2
    ) AS length_km,
    ST_AsGeoJSON(l.geom)::json AS geometry
FROM planet_osm_rels AS r
JOIN postpass_line AS l
  ON l.osm_type = 'R'
 AND l.osm_id = r.id
WHERE r.id = {relation_id}
  AND r.tags->>'type' = 'route'
  AND r.tags->>'route' IN ('hiking', 'foot', 'walking')
  AND (
      r.tags ? 'name'
      OR r.tags ? 'name:en'
      OR r.tags ? 'int_name'
      OR r.tags ? 'official_name'
      OR r.tags ? 'alt_name'
      OR r.tags ? 'loc_name'
      OR r.tags ? 'short_name'
  )
LIMIT 1
"""

    try:
        rows = await _execute_sql(sql)
    except Exception as exc:
        logger.warning(
            "Postpass relation lookup failed for %s; "
            "using Overpass fallback: %s",
            relation_id,
            exc,
        )
        return await overpass_fallback.overpass_get_relation(
            relation_id
        )

    if not rows:
        _cache_set(
            cache_key,
            None,
        )
        return None

    relation = _relation_from_row(rows[0])

    if relation is None:
        _cache_set(
            cache_key,
            None,
        )
        return None

    if not _geometry_is_usable(
        relation.geometry
    ):
        logger.warning(
            "Postpass relation %s has unusable geometry",
            relation_id,
        )

        _cache_set(
            cache_key,
            None,
        )
        return None

    _cache_set(
        cache_key,
        relation,
    )

    return relation


async def get_way(
    way_id: int,
) -> PostpassWay | None:
    """Fetch one named OSM way with its real Postpass geometry."""
    try:
        way_id = int(way_id)
    except (TypeError, ValueError):
        return None
    if way_id <= 0:
        return None

    cache_key = ("way", way_id)
    if _cache_has(cache_key):
        return _cache_get(cache_key)

    sql = f"""
SELECT
    l.osm_id AS way_id,
    l.tags->>'name' AS name,
    l.tags->>'name:en' AS name_en,
    l.tags->>'int_name' AS int_name,
    l.tags->>'alt_name' AS alt_name,
    l.tags->>'official_name' AS official_name,
    l.tags->>'loc_name' AS loc_name,
    l.tags->>'short_name' AS short_name,
    l.tags->>'old_name' AS old_name,
    l.tags->>'route' AS route,
    l.tags->>'highway' AS highway,
    l.tags->>'sac_scale' AS sac_scale,
    l.tags->>'trail_visibility' AS trail_visibility,
    l.tags->>'surface' AS surface,
    l.tags->>'smoothness' AS smoothness,
    l.tags->>'tracktype' AS tracktype,
    l.tags->>'access' AS access,
    l.tags->>'incline' AS incline,
    l.tags->>'incline_direction' AS incline_direction,
    l.tags->>'width' AS width,
    l.tags->>'assisted_trail' AS assisted_trail,
    ST_GeometryType(l.geom) AS geometry_type,
    ST_NPoints(l.geom) AS point_count,
    ROUND((l.length_m / 1000.0)::numeric, 3) AS length_km,
    ST_AsGeoJSON(l.geom)::json AS geometry
FROM postpass_line AS l
WHERE l.osm_type = 'W'
  AND l.osm_id = {way_id}
  AND l.tags ? 'name'
LIMIT 1
"""

    try:
        rows = await _execute_sql(sql)
    except Exception as exc:
        logger.warning(
            "Postpass way lookup failed for %s; "
            "using Overpass fallback: %s",
            way_id,
            exc,
        )
        return await overpass_fallback.overpass_get_way(way_id)
    way = _way_from_row(rows[0]) if rows else None
    _cache_set(cache_key, way)
    return way
