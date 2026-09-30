"""
Overpass API fallback for real OSM identity and geometry.

Postpass is the primary source: it is fast, rendered, and purpose-built.
This module exists for one demonstrated reason — Postpass answering HTTP 503
"No server is available to handle this request" for extended periods, during
which the product otherwise maps nothing at all.

It is a FALLBACK, not a second geometry architecture:

* It runs only when the Postpass query raises. A successful Postpass answer,
  including a successful empty answer, is never second-guessed: re-querying
  elsewhere would blur NO_PROVIDER_DATA and double provider load.
* It returns the same dataclasses (`PostpassWay`, `PostpassRelation`) with
  the same validation gates, so every downstream rule — evidence scoring,
  verification, provenance, MAP_READY honesty — applies unchanged.
* Identity comes from OSM itself: real ids, real tags, real member refs and
  roles, real node geometry assembled without invention. Relation geometry is
  one MultiLineString component per member way, in member order; components
  are never stitched, ordered, or connected artificially.
* Anything it cannot establish stays missing: a relation whose members have
  no usable geometry is dropped by the same `_geometry_is_usable` gate the
  primary path uses, which surfaces downstream as UNMAPPED, never as a
  fabricated route.

Overpass etiquette matters because this hits a shared public resource:
one in-flight request at a time, bbox-bounded queries, capped output,
a identifying User-Agent, and a bounded in-process cache so a repeated
selection does not re-query.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import OrderedDict
from typing import Any

import httpx

from app.core.config import settings
from app.services.rate_limit import CircuitBreaker, ProviderOutage


logger = logging.getLogger(__name__)


OVERPASS_URL = (
    os.getenv("OVERPASS_URL")
    or settings.OVERPASS_URL
    or "https://overpass-api.de/api/interpreter"
).strip()

OVERPASS_TIMEOUT_SECONDS = max(
    30.0,
    min(
        float(
            os.getenv(
                "OVERPASS_TIMEOUT_SECONDS",
                settings.OVERPASS_TIMEOUT_SECONDS,
            )
        ),
        400.0,
    ),
)

OVERPASS_SERVER_TIMEOUT_SECONDS = 150

OVERPASS_CACHE_TTL_SECONDS = max(
    60.0,
    min(
        float(
            os.getenv(
                "OVERPASS_CACHE_TTL_SECONDS",
                settings.OVERPASS_CACHE_TTL_SECONDS,
            )
        ),
        3600.0,
    ),
)

OVERPASS_CACHE_MAX_ENTRIES = max(
    16,
    min(
        int(
            os.getenv(
                "OVERPASS_CACHE_MAX_ENTRIES",
                settings.OVERPASS_CACHE_MAX_ENTRIES,
            )
        ),
        1024,
    ),
)

# Backoff before the single retry of a rate-limited (429) request.
OVERPASS_429_BACKOFF_SECONDS = max(
    1.0,
    min(
        float(
            os.getenv(
                "OVERPASS_429_BACKOFF_SECONDS",
                settings.OVERPASS_429_BACKOFF_SECONDS,
            )
        ),
        60.0,
    ),
)

OVERPASS_USER_AGENT = (
    "GoBeyond/1.0 (OSM fallback for trail verification; "
    "educational outdoor intelligence project)"
)

# One in-flight Overpass request at a time. This is both politeness toward a
# shared public endpoint and backpressure against stampedes when the primary
# is down and every tile wants the fallback at once.
_OVERPASS_SEMAPHORE = asyncio.Semaphore(1)

# Opens after consecutive outage failures. Overpass allows one request at a
# time with a long timeout, so an outage would otherwise serialise the full
# failure cost across every query in a search.
overpass_breaker = CircuitBreaker("overpass")

_CACHE: OrderedDict[
    tuple[Any, ...],
    tuple[float, Any],
] = OrderedDict()


def _cache_has(key: tuple[Any, ...]) -> bool:
    cached = _CACHE.get(key)
    if cached is None:
        return False
    created_at, _value = cached
    if time.monotonic() - created_at > OVERPASS_CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return False
    _CACHE.move_to_end(key)
    return True


def _cache_get(key: tuple[Any, ...]) -> Any | None:
    if not _cache_has(key):
        return None
    return _CACHE[key][1]


def _cache_set(key: tuple[Any, ...], value: Any) -> None:
    _CACHE[key] = (time.monotonic(), value)
    _CACHE.move_to_end(key)
    while len(_CACHE) > OVERPASS_CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


# Cap on member ways fetched for one relation's geometry. Beyond this the
# response grows unbounded (continental routes have thousands of members),
# so geometry assembly stops and the relation is dropped by the shared
# usability gate instead of being presented with partial geometry.
MAX_RELATION_GEOMETRY_MEMBERS = 300


def _haversine_km(
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
) -> float:
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1)
        * math.cos(phi2)
        * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * radius * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _line_length_km(coordinates: list[list[float]]) -> float:
    total = 0.0
    for first, second in zip(coordinates, coordinates[1:]):
        try:
            total += _haversine_km(
                float(first[0]),
                float(first[1]),
                float(second[0]),
                float(second[1]),
            )
        except (TypeError, ValueError, IndexError):
            continue
    return total


def _ql_string(value: str) -> str:
    """Quote a literal for Overpass QL double-quoted strings."""
    return '"{}"'.format(
        str(value).replace("\\", "\\\\").replace('"', '\\"')
    )


def _ql_regex(value: str) -> str:
    """Escape a literal substring for an Overpass QL regex match."""
    return re.sub(r"([].^$*+?{}()|\\])", r"\\\1", value)


async def _overpass_post(query: str) -> dict[str, Any]:
    if not overpass_breaker.allow():
        raise ProviderOutage(
            "Overpass is temporarily skipped after repeated failures; "
            "it will be tried again shortly"
        )
    try:
        payload = await _overpass_request(query)
    except ProviderOutage:
        overpass_breaker.record_failure()
        raise
    except Exception:
        # The server answered, even if it refused this query.
        overpass_breaker.record_success()
        raise
    overpass_breaker.record_success()
    return payload


async def _overpass_request(query: str) -> dict[str, Any]:
    """
    Run one Overpass QL query and return the parsed JSON document.

    Raises RuntimeError on any transport, status, parse or Overpass-error
    outcome, so the caller treats every failure as provider failure and the
    existing UNAVAILABLE semantics apply unchanged.

    One bounded exception: HTTP 429 (rate limiting) is retried exactly once
    after a short backoff. Throttling is the endpoint asking for a slower
    pace, not a refusal, and a single polite retry rescues transient bursts
    without hammering. Anything else fails immediately.
    """
    response: Any = None
    last_error: str | None = None
    for attempt in (0, 1):
        if attempt > 0:
            await asyncio.sleep(OVERPASS_429_BACKOFF_SECONDS)
        try:
            async with _OVERPASS_SEMAPHORE:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(OVERPASS_TIMEOUT_SECONDS),
                    headers={
                        "User-Agent": OVERPASS_USER_AGENT,
                        "Accept": "application/json",
                    },
                ) as client:
                    response = await client.post(
                        OVERPASS_URL,
                        data={"data": query},
                    )
        except Exception as exc:
            raise ProviderOutage(
                f"Overpass request failed: {exc}"
            ) from exc

        if response.status_code == 200:
            break
        last_error = (
            "Overpass returned HTTP "
            f"{response.status_code}: "
            f"{response.text.strip()[:300]}"
        )
        if response.status_code != 429 or attempt > 0:
            # A server error, or throttling that persisted after the retry,
            # says the provider is not serving us. Anything else is a refusal
            # of this one query.
            raise (
                ProviderOutage(last_error)
                if response.status_code >= 500 or response.status_code == 429
                else RuntimeError(last_error)
            )
        logger.info(
            "Overpass rate-limited the request; retrying once after %.0fs",
            OVERPASS_429_BACKOFF_SECONDS,
        )
    else:
        raise RuntimeError(last_error or "Overpass request failed")

    assert response is not None

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Overpass returned invalid JSON"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Overpass returned an invalid response object"
        )

    remark = payload.get("remark")
    if isinstance(remark, str) and "error" in remark.lower():
        raise RuntimeError(
            f"Overpass query error: {remark[:300]}"
        )

    return payload


def _elements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return []
    return [item for item in elements if isinstance(item, dict)]


def _way_coordinates(element: dict[str, Any]) -> list[list[float]]:
    """Node geometry of one Overpass way element, as [lon, lat] pairs."""
    coordinates: list[list[float]] = []
    geometry = element.get("geometry")
    if not isinstance(geometry, list):
        return coordinates
    for point in geometry:
        if not isinstance(point, dict):
            continue
        try:
            lon = float(point["lon"])
            lat = float(point["lat"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (
            math.isfinite(lon)
            and math.isfinite(lat)
            and -180.0 <= lon <= 180.0
            and -90.0 <= lat <= 90.0
        ):
            continue
        if coordinates and coordinates[-1] == [lon, lat]:
            continue
        coordinates.append([lon, lat])
    return coordinates


def _tags(element: dict[str, Any]) -> dict[str, str]:
    tags = element.get("tags")
    if not isinstance(tags, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in tags.items()
    }


# Deferred postpass imports live here so this module never creates an import
# cycle: postpass.py imports this module at top level for its fallback hooks,
# and this module reaches back only inside functions, after both are loaded.
def _postpass() -> Any:
    from app.services import postpass as postpass_module

    return postpass_module


def _norm(value: Any) -> str:
    return _postpass()._norm(value)


def _way_from_element(element: dict[str, Any]) -> Any | None:
    """One Overpass way element into the shared way dataclass."""
    postpass = _postpass()
    try:
        way_id = int(element.get("id"))
    except (TypeError, ValueError):
        return None
    if way_id <= 0 or element.get("type") != "way":
        return None

    tags = _tags(element)
    name = (tags.get("name") or "").strip()
    if not name:
        return None

    coordinates = _way_coordinates(element)
    if len(coordinates) < 2:
        return None
    geometry = {"type": "LineString", "coordinates": coordinates}
    if not postpass._geometry_is_usable(geometry):
        return None

    def tag(key: str) -> str | None:
        value = (tags.get(key) or "").strip()
        return value or None

    aliases: list[str] = []
    for key in (
        "name:en",
        "int_name",
        "alt_name",
        "official_name",
        "loc_name",
        "short_name",
    ):
        value = (tags.get(key) or "").strip()
        if value and _norm(value) != _norm(name):
            aliases.append(value)

    return postpass.PostpassWay(
        way_id=way_id,
        name=name,
        route=_norm(tags.get("route")) or None,
        highway=_norm(tags.get("highway")) or None,
        sac_scale=_norm(tags.get("sac_scale")) or None,
        trail_visibility=_norm(tags.get("trail_visibility")) or None,
        surface=_norm(tags.get("surface")) or None,
        smoothness=_norm(tags.get("smoothness")) or None,
        tracktype=_norm(tags.get("tracktype")) or None,
        access=_norm(tags.get("access")) or None,
        incline=tag("incline"),
        incline_direction=_norm(tags.get("incline_direction")) or None,
        width=tag("width"),
        assisted_trail=_norm(tags.get("assisted_trail")) or None,
        foot=_norm(tags.get("foot")) or None,
        footway_use=_norm(tags.get("footway")) or None,
        trailblazed=_norm(tags.get("trailblazed")) or None,
        designation=_norm(tags.get("designation")) or None,
        hiking=_norm(tags.get("hiking")) or None,
        sport=_norm(tags.get("sport")) or None,
        aliases=list(dict.fromkeys(aliases)),
        geometry_type="LineString",
        point_count=len(coordinates),
        length_km=round(_line_length_km(coordinates), 3),
        geometry=geometry,
        source="OpenStreetMap via Overpass",
    )


_OVERPASS_MEMBER_TYPES = {
    "way": "W",
    "node": "N",
    "relation": "R",
    "w": "W",
    "n": "N",
    "r": "R",
}


def _relation_members(
    element: dict[str, Any],
) -> list[Any]:
    postpass = _postpass()
    members: list[Any] = []
    raw_members = element.get("members")
    if not isinstance(raw_members, list):
        return members
    for raw_member in raw_members:
        if not isinstance(raw_member, dict):
            continue
        try:
            # Overpass spells member types in full ("way"); the shared
            # dataclass uses single letters ("W"). Normalise at this
            # boundary so every downstream `member_type == "W"` check,
            # member-id list and role filter keeps working unchanged.
            member_type = _OVERPASS_MEMBER_TYPES.get(
                _norm(raw_member.get("type")), ""
            )
            ref = int(raw_member.get("ref"))
        except (TypeError, ValueError):
            continue
        if not member_type or ref <= 0:
            continue
        members.append(
            postpass.PostpassRelationMember(
                member_type=member_type,
                ref=ref,
                role=_norm(raw_member.get("role")),
            )
        )
    return members


def _relation_name(tags: dict[str, str]) -> str:
    for key in (
        "name",
        "name:en",
        "int_name",
        "official_name",
        "alt_name",
        "loc_name",
        "short_name",
    ):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


def _relation_from_element(
    element: dict[str, Any],
    member_geometries: dict[int, list[list[float]]],
) -> Any | None:
    """One Overpass relation element into the shared relation dataclass.

    Geometry is assembled as one MultiLineString component per member way
    with real node geometry, in member order. Members without geometry are
    skipped for the shape but kept in the member list, so identity stays
    complete while the shape only ever contains verified coordinates.
    Nothing is stitched, ordered artificially, or invented.
    """
    postpass = _postpass()
    try:
        relation_id = int(element.get("id"))
    except (TypeError, ValueError):
        return None
    if relation_id <= 0 or element.get("type") != "relation":
        return None

    tags = _tags(element)
    name = _relation_name(tags)
    route = _norm(tags.get("route"))
    if not name or route not in postpass.HIKING_ROUTE_TYPES:
        return None

    members = _relation_members(element)
    components: list[list[list[float]]] = []
    for member in members:
        if member.member_type != "W":
            continue
        coordinates = member_geometries.get(member.ref)
        if coordinates and len(coordinates) >= 2:
            components.append(coordinates)

    geometry: dict[str, Any] | None = None
    if components:
        geometry = {"type": "MultiLineString", "coordinates": components}
        if not postpass._geometry_is_usable(geometry):
            geometry = None
    if geometry is None:
        return None

    def tag(key: str) -> str | None:
        value = (tags.get(key) or "").strip()
        return value or None

    aliases: list[str] = []
    for key in (
        "name:en",
        "int_name",
        "alt_name",
        "official_name",
        "loc_name",
        "short_name",
    ):
        value = (tags.get(key) or "").strip()
        if value:
            aliases.append(value)

    point_count = sum(len(part) for part in components)
    length_km = round(
        sum(_line_length_km(part) for part in components), 2
    )

    return postpass.PostpassRelation(
        relation_id=relation_id,
        name=name,
        route=route,
        network=tag("network"),
        ref=tag("ref"),
        description=tag("description"),
        sac_scale=_norm(tags.get("sac_scale")) or None,
        surface=_norm(tags.get("surface")) or None,
        trail_visibility=_norm(tags.get("trail_visibility")) or None,
        members=members,
        aliases=list(dict.fromkeys(aliases)),
        geometry_type="MultiLineString",
        point_count=point_count,
        length_km=length_km,
        geometry=geometry,
        source="OpenStreetMap via Overpass",
    )


def _member_geometries(
    payload: dict[str, Any],
) -> dict[int, list[list[float]]]:
    """Way-id to coordinates for every way element in a response."""
    geometries: dict[int, list[list[float]]] = {}
    for element in _elements(payload):
        if element.get("type") != "way":
            continue
        try:
            way_id = int(element.get("id"))
        except (TypeError, ValueError):
            continue
        coordinates = _way_coordinates(element)
        if way_id > 0 and len(coordinates) >= 2:
            geometries[way_id] = coordinates
    return geometries


def _bbox_filter(bbox: tuple[float, float, float, float]) -> str:
    """Overpass bbox filter; Overpass order is south,west,north,east."""
    west, south, east, north = bbox
    return f"({south},{west},{north},{east})"


async def overpass_relations_in_bbox(
    bbox: tuple[float, float, float, float],
    *,
    limit: int = 100,
) -> list[Any]:
    """
    Hiking route relations in a bbox, with member-assembled geometry.

    Two bounded queries: relation identities and tags first, then member
    way geometries for the selected relations only. Relations whose members
    yield no usable geometry are dropped by the shared usability gate,
    exactly as the primary path drops unrendered relations.
    """
    postpass = _postpass()
    bbox = postpass._clean_bbox(bbox)
    safe_limit = max(1, min(int(limit), 1000))
    cache_key = (
        "overpass:bbox",
        *(round(value, 5) for value in bbox),
        safe_limit,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    bbox_q = _bbox_filter(bbox)
    identity_query = (
        "[out:json]"
        f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
        "("
        f'relation["type"="route"]["route"~"^(hiking|foot|walking)$"]'
        f'["name"]{bbox_q};'
        ");"
        "out tags;"
    )
    payload = await _overpass_post(identity_query)
    candidates = [
        element
        for element in _elements(payload)
        if element.get("type") == "relation"
    ]

    def _rank_key(element: dict[str, Any]) -> tuple[int, int, str, int]:
        tags = _tags(element)
        route = _norm(tags.get("route"))
        name = _relation_name(tags).lower()
        trail_words = (
            "trail", "trek", "trekking", "hike", "hiking", "route",
            "walk", "walking", "loop", "peak", "summit", "ridge",
            "waterfall", "falls",
        )
        has_trail_word = any(word in name for word in trail_words)
        try:
            relation_id = int(element.get("id"))
        except (TypeError, ValueError):
            relation_id = 0
        return (
            0 if route == "hiking" else 1,
            0 if has_trail_word else 1,
            name,
            relation_id,
        )

    candidates.sort(key=_rank_key)
    candidates = candidates[:safe_limit]
    if not candidates:
        _cache_set(cache_key, [])
        return []

    ids = ",".join(
        str(element["id"]) for element in candidates if element.get("id")
    )
    geometry_query = (
        "[out:json]"
        f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
        f"(relation(id:{ids});way(r););"
        "out geom;"
    )
    geometry_payload = await _overpass_post(geometry_query)
    member_geometries = _member_geometries(geometry_payload)

    relations: list[Any] = []
    for element in candidates:
        relation = _relation_from_element(element, member_geometries)
        if relation is not None:
            relations.append(relation)

    _cache_set(cache_key, relations)
    return relations


async def overpass_named_ways_in_bbox(
    bbox: tuple[float, float, float, float],
    *,
    limit: int = 2000,
) -> list[Any]:
    """Named trail-capable ways in a bbox, with real node geometry."""
    postpass = _postpass()
    bbox = postpass._clean_bbox(bbox)
    safe_limit = max(1, min(int(limit), 5000))
    cache_key = (
        "overpass:named_ways_bbox",
        *(round(value, 5) for value in bbox),
        safe_limit,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    bbox_q = _bbox_filter(bbox)
    query = (
        "[out:json]"
        f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
        "("
        f'way["name"]["route"~"^(hiking|foot|walking)$"]{bbox_q};'
        f'way["name"]["sac_scale"]{bbox_q};'
        f'way["name"]["trail_visibility"]{bbox_q};'
        f'way["name"]["trailblazed"]{bbox_q};'
        f'way["name"]["designation"]{bbox_q};'
        f'way["name"]["hiking"]{bbox_q};'
        f'way["name"]["sport"~"^(hiking|walking|trail_running)$"]{bbox_q};'
        f'way["name"]["highway"~"^(path|track|footway|steps|bridleway|pedestrian)$"]{bbox_q};'
        ");"
        f"out geom {safe_limit};"
    )
    payload = await _overpass_post(query)

    ways: list[Any] = []
    for element in _elements(payload):
        way = _way_from_element(element)
        if way is not None:
            ways.append(way)

    def _rank_key(way: Any) -> tuple[int, str, int]:
        route = _norm(way.route)
        if route == "hiking":
            rank = 0
        elif route in ("foot", "walking"):
            rank = 1
        elif way.sac_scale:
            rank = 2
        elif way.trail_visibility:
            rank = 3
        elif way.highway in ("path", "bridleway", "steps"):
            rank = 4
        elif way.highway in ("footway", "track"):
            rank = 5
        else:
            rank = 6
        return (rank, _norm(way.name), way.way_id)

    ways.sort(key=_rank_key)
    ways = ways[:safe_limit]
    _cache_set(cache_key, ways)
    return ways


async def overpass_relations_by_names(
    names: list[str],
    *,
    bbox: tuple[float, float, float, float] | None = None,
    limit_per_name: int = 10,
) -> dict[str, list[Any]]:
    """
    Hiking route relations whose names match the requested names.

    Overpass finds textual candidates with case-insensitive substring
    matches; the deterministic Python match score then decides, exactly as
    the primary path does, so the semantic layer is never trusted directly.
    """
    postpass = _postpass()
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

    safe_limit = max(1, min(int(limit_per_name), 20))
    bbox_value = postpass._clean_bbox(bbox) if bbox is not None else None
    cache_key = (
        "overpass:names",
        tuple(_norm(name) for name in cleaned),
        bbox_value,
        safe_limit,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    bbox_q = _bbox_filter(bbox_value) if bbox_value is not None else ""
    name_keys = (
        "name", "alt_name", "official_name", "loc_name", "short_name",
        "name:en", "int_name",
    )

    result: dict[str, list[Any]] = {name: [] for name in cleaned}
    for requested_name in cleaned:
        variants = postpass._name_variants(requested_name)
        if not variants:
            continue
        # Longest variants first, mirroring the primary's term ordering.
        variants = sorted(set(variants), key=lambda v: (-len(v), v))
        branches: list[str] = []
        for variant in variants[:12]:
            pattern = _ql_regex(variant)
            for key in name_keys:
                branches.append(
                    f'relation["type"="route"]'
                    f'["route"~"^(hiking|foot|walking)$"]'
                    f'["{key}"~"{pattern}",i]{bbox_q};'
                )
        query = (
            "[out:json]"
            f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
            "(" + "".join(branches) + ");"
            "out tags;"
        )
        try:
            payload = await _overpass_post(query)
        except Exception as exc:
            logger.warning(
                "Overpass name lookup failed for %r: %s",
                requested_name,
                exc,
            )
            continue

        candidates = [
            element
            for element in _elements(payload)
            if element.get("type") == "relation"
        ]
        ids = sorted(
            {
                int(element["id"])
                for element in candidates
                if isinstance(element.get("id"), int)
                or str(element.get("id", "")).isdigit()
            }
        )[:50]
        member_geometries: dict[int, list[list[float]]] = {}
        if ids:
            geometry_query = (
                "[out:json]"
                f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
                f"(relation(id:{','.join(str(i) for i in ids)});way(r););"
                "out geom;"
            )
            try:
                geometry_payload = await _overpass_post(geometry_query)
                member_geometries = _member_geometries(geometry_payload)
            except Exception as exc:
                logger.warning(
                    "Overpass member geometry failed for %r: %s",
                    requested_name,
                    exc,
                )

        by_id = {
            int(element["id"]): element
            for element in candidates
            if str(element.get("id", "")).isdigit()
        }
        ranked: list[tuple[float, Any]] = []
        for element in by_id.values():
            relation = _relation_from_element(element, member_geometries)
            if relation is None:
                continue
            score = postpass._match_score(requested_name, relation)
            if score < 55.0:
                continue
            ranked.append((score, relation))
        ranked.sort(
            key=lambda item: (
                -item[0],
                _norm(item[1].name),
                item[1].relation_id,
            )
        )
        result[requested_name] = [
            relation for _score, relation in ranked[:safe_limit]
        ]

    _cache_set(cache_key, result)
    return result


async def overpass_get_relation(relation_id: int) -> Any | None:
    """One hiking route relation with member-assembled geometry."""
    try:
        relation_id = int(relation_id)
    except (TypeError, ValueError):
        return None
    if relation_id <= 0:
        return None

    cache_key = ("overpass:relation", relation_id)
    if _cache_has(cache_key):
        return _cache_get(cache_key)

    query = (
        "[out:json]"
        f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
        f"(relation({relation_id});way(r););"
        "out geom;"
    )
    # Transport failures propagate as provider failure. Returning None here
    # would misreport an outage as "relation not found", which downstream
    # reads as a coverage statement rather than UNAVAILABLE.
    payload = await _overpass_post(query)

    relation_element: dict[str, Any] | None = None
    for element in _elements(payload):
        if (
            element.get("type") == "relation"
            and str(element.get("id")) == str(relation_id)
        ):
            relation_element = element
            break
    if relation_element is None:
        _cache_set(cache_key, None)
        return None

    tags = _tags(relation_element)
    if tags.get("type") != "route" or _norm(
        tags.get("route")
    ) not in ("hiking", "foot", "walking"):
        _cache_set(cache_key, None)
        return None
    if not _relation_name(tags):
        _cache_set(cache_key, None)
        return None

    member_geometries = _member_geometries(payload)
    relation = _relation_from_element(relation_element, member_geometries)
    _cache_set(cache_key, relation)
    return relation


async def overpass_get_way(way_id: int) -> Any | None:
    """One named OSM way with real node geometry."""
    try:
        way_id = int(way_id)
    except (TypeError, ValueError):
        return None
    if way_id <= 0:
        return None

    cache_key = ("overpass:way", way_id)
    if _cache_has(cache_key):
        return _cache_get(cache_key)

    query = (
        "[out:json]"
        f"[timeout:{OVERPASS_SERVER_TIMEOUT_SECONDS}];"
        f"way({way_id});"
        "out geom;"
    )
    # See overpass_get_relation: transport failure must propagate, never
    # masquerade as "way not found".
    payload = await _overpass_post(query)

    for element in _elements(payload):
        if (
            element.get("type") == "way"
            and str(element.get("id")) == str(way_id)
        ):
            way = _way_from_element(element)
            _cache_set(cache_key, way)
            return way
    _cache_set(cache_key, None)
    return None


__all__ = [
    "OVERPASS_URL",
    "overpass_get_relation",
    "overpass_get_way",
    "overpass_named_ways_in_bbox",
    "overpass_relations_by_names",
    "overpass_relations_in_bbox",
]
