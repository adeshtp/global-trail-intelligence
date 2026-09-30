from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.rate_limit import (
    CircuitBreaker,
    is_provider_outage,
    search_limiter,
    suggest_limiter,
)


router = APIRouter(
    prefix="/api/search",
    tags=["Search"],
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_LOOKUP_URL = "https://nominatim.openstreetmap.org/lookup"
# Photon is built for search-as-you-type; the public Nominatim service forbids
# it. Suggestions come from here, the search itself still comes from Nominatim.
PHOTON_URL = "https://photon.komoot.io/api/"
SUGGESTION_LIMIT = 6
SEARCH_CACHE_TTL_SECONDS = 300.0
SEARCH_CACHE_MAX_ENTRIES = 128

HEADERS = {
    "User-Agent": "GoBeyond/1.0 (outdoor trail intelligence)",
}

# Repeated outages stop each keystroke-driven search waiting out a timeout.
nominatim_breaker = CircuitBreaker("nominatim")
photon_breaker = CircuitBreaker("photon")

_SEARCH_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_SEARCH_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}
_SUGGEST_CACHE: OrderedDict[str, tuple[float, list[Suggestion]]] = OrderedDict()
_SUGGEST_INFLIGHT: dict[str, asyncio.Task[list[Suggestion]]] = {}


def _cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _cache_get(key: str, cache: OrderedDict[str, Any] | None = None) -> Any:
    cache = _SEARCH_CACHE if cache is None else cache
    cached = cache.get(key)
    if cached is None:
        return None
    created_at, value = cached
    if time.monotonic() - created_at > SEARCH_CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    cache.move_to_end(key)
    return value


def _cache_set(
    key: str, value: Any, cache: OrderedDict[str, Any] | None = None
) -> None:
    cache = _SEARCH_CACHE if cache is None else cache
    cache[key] = (time.monotonic(), value)
    cache.move_to_end(key)
    while len(cache) > SEARCH_CACHE_MAX_ENTRIES:
        cache.popitem(last=False)


def _parse_boundingbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        south, north, west, east = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= south < north <= 90.0):
        return None
    if not (-180.0 <= west < east <= 180.0):
        return None
    return [west, south, east, north]


async def _nominatim_get(url: str, params: dict[str, Any]) -> Any:
    if not nominatim_breaker.allow():
        raise RuntimeError(
            "Nominatim search is temporarily skipped after repeated failures"
        )

    # One retry for a transient failure only (a dropped connection or a server
    # error), so a single blip does not fail the search box. A timeout is not
    # retried: a hung host does not answer on an immediate second try, and
    # retrying doubled the wait (10 s became 21 s). Nominatim's usage policy is
    # strict, so a refusal such as 429 is never retried and there is never more
    # than the one extra request.
    for attempt in (0, 1):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                headers=HEADERS,
                follow_redirects=True,
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            transient = (
                isinstance(exc, httpx.TransportError)
                and not isinstance(exc, httpx.TimeoutException)
            ) or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code >= 500
            )
            if attempt == 0 and transient:
                await asyncio.sleep(1.0)
                continue
            if is_provider_outage(exc):
                nominatim_breaker.record_failure()
            else:
                # The server answered, even if not usefully.
                nominatim_breaker.record_success()
            raise RuntimeError("Nominatim search failed") from exc
    nominatim_breaker.record_success()
    return payload


def _normalise_results(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise RuntimeError("Nominatim returned an invalid result")

    results: list[dict[str, Any]] = []
    for result in payload:
        if not isinstance(result, dict):
            continue
        try:
            latitude = float(result["lat"])
            longitude = float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            continue
        display_name = str(result.get("display_name") or "").strip()
        if not display_name:
            continue
        results.append(
            {
                "name": str(result.get("name") or display_name.split(",", 1)[0]).strip(),
                "display_name": display_name,
                "latitude": latitude,
                "longitude": longitude,
                "osm_type": result.get("osm_type"),
                "osm_id": result.get("osm_id"),
                "class": result.get("category") or result.get("class"),
                "type": result.get("type"),
                "addresstype": result.get("addresstype"),
                "boundingbox": _parse_boundingbox(result.get("boundingbox")),
                "address": result.get("address") or {},
                "importance": result.get("importance"),
            }
        )

    return results


async def _search_uncached(query: str) -> dict[str, Any]:
    payload = await _nominatim_get(
        NOMINATIM_URL,
        {
            "q": query,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
            "namedetails": 1,
        },
    )
    return {"query": query, "results": _normalise_results(payload)}


_OSM_TYPES = {"N": "node", "W": "way", "R": "relation"}


class Suggestion(BaseModel):
    """One type-ahead row: enough to show and to look the place up by id."""

    label: str
    detail: str
    osm_type: Literal["node", "way", "relation"]
    osm_id: int
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)


def _suggestion_from_feature(feature: Any) -> Suggestion | None:
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    osm_type = _OSM_TYPES.get(str(properties.get("osm_type") or ""))
    label = str(properties.get("name") or "").strip()
    if (
        not label
        or osm_type is None
        or not isinstance(coordinates, list)
        or len(coordinates) < 2
    ):
        return None
    detail: list[str] = []
    for key in ("city", "county", "state", "country"):
        part = str(properties.get(key) or "").strip()
        if part and part != label and part not in detail:
            detail.append(part)
    try:
        return Suggestion(
            label=label,
            detail=", ".join(detail),
            osm_type=osm_type,
            osm_id=int(properties["osm_id"]),
            latitude=float(coordinates[1]),
            longitude=float(coordinates[0]),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _suggest_uncached(query: str) -> list[Suggestion]:
    if not photon_breaker.allow():
        raise RuntimeError(
            "Location suggestions are temporarily skipped after repeated failures"
        )
    try:
        # No retry: a suggestion is worth nothing a second later, and a
        # refusal must never be answered with more requests.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                PHOTON_URL,
                params={"q": query, "limit": SUGGESTION_LIMIT, "lang": "en"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if is_provider_outage(exc):
            photon_breaker.record_failure()
        else:
            photon_breaker.record_success()
        raise RuntimeError("Location suggestions failed") from exc
    photon_breaker.record_success()

    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise RuntimeError("Photon returned an invalid result")
    suggestions: dict[tuple[str, int], Suggestion] = {}
    for feature in features:
        suggestion = _suggestion_from_feature(feature)
        if suggestion is not None:
            # Photon lists some places twice.
            suggestions.setdefault(
                (suggestion.osm_type, suggestion.osm_id), suggestion
            )
    return list(suggestions.values())[:SUGGESTION_LIMIT]


def _client_key(request: Request | None) -> str | None:
    if request is not None and request.client is not None:
        return request.client.host
    return None


@router.get("/suggest")
async def suggest_locations(
    q: str = Query(..., min_length=3, max_length=100),
    request: Request = None,
) -> list[Suggestion]:
    query = " ".join(q.strip().split())
    if len(query) < 3:
        raise HTTPException(status_code=400, detail="Search query is too short")
    client_key = _client_key(request)
    if client_key and not suggest_limiter.allow(
        client_key, limit=120, window_seconds=60.0
    ):
        raise HTTPException(
            status_code=429,
            detail="Suggestion limit exceeded; retry shortly",
        )

    key = _cache_key(query)
    cached = _cache_get(key, _SUGGEST_CACHE)
    if cached is not None:
        return cached
    task = _SUGGEST_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_suggest_uncached(query))
        _SUGGEST_INFLIGHT[key] = task
    try:
        result = await asyncio.shield(task)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Location suggestions are temporarily unavailable",
        ) from exc
    finally:
        if task.done() and _SUGGEST_INFLIGHT.get(key) is task:
            _SUGGEST_INFLIGHT.pop(key, None)
    _cache_set(key, result, _SUGGEST_CACHE)
    return result


@router.get("/lookup")
async def lookup_location(
    osm_type: Literal["node", "way", "relation"],
    osm_id: int = Query(..., ge=1),
    request: Request = None,
):
    """
    The place the user picked, in the shape a typed search returns.

    Suggestions carry only an OSM id. Looking it up on Nominatim gives the
    classification and bounds the rest of the app reads, and means the place
    searched is the place clicked, not whatever a text search ranks first.
    """
    client_key = _client_key(request)
    if client_key and not search_limiter.allow(
        client_key, limit=60, window_seconds=60.0
    ):
        raise HTTPException(
            status_code=429,
            detail="Location search limit exceeded; retry shortly",
        )
    reference = f"{osm_type[0].upper()}{osm_id}"
    key = f"lookup:{reference}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        payload = await _nominatim_get(
            NOMINATIM_LOOKUP_URL,
            {
                "osm_ids": reference,
                "format": "jsonv2",
                "addressdetails": 1,
                "namedetails": 1,
            },
        )
        results = _normalise_results(payload)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail="Location search is temporarily unavailable",
        ) from exc
    if not results:
        raise HTTPException(status_code=404, detail="Location not found")
    result = {"query": reference, "results": results}
    _cache_set(key, result)
    return result


@router.get("")
async def search_location(
    q: str = Query(..., min_length=2, max_length=200),
    request: Request = None,
):
    query = " ".join(q.strip().split())
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Search query is too short")

    client_key = (
        request.client.host
        if request is not None and request.client is not None
        else None
    )
    if client_key and not search_limiter.allow(
        client_key,
        limit=60,
        window_seconds=60.0,
    ):
        raise HTTPException(
            status_code=429,
            detail="Location search limit exceeded; retry shortly",
        )

    key = _cache_key(query)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _SEARCH_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_search_uncached(query))
        _SEARCH_INFLIGHT[key] = task

    try:
        result = await asyncio.shield(task)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail="Location search is temporarily unavailable",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Location search is temporarily unavailable",
        ) from exc
    finally:
        if task.done() and _SEARCH_INFLIGHT.get(key) is task:
            _SEARCH_INFLIGHT.pop(key, None)

    _cache_set(key, result)
    return result
