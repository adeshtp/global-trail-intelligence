from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from app.services.rate_limit import search_limiter


router = APIRouter(
    prefix="/api/search",
    tags=["Search"],
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SEARCH_CACHE_TTL_SECONDS = 300.0
SEARCH_CACHE_MAX_ENTRIES = 128

HEADERS = {
    "User-Agent": "GoBeyond/1.0 (outdoor trail intelligence)",
}

_SEARCH_CACHE: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
_SEARCH_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}


def _cache_key(query: str) -> str:
    return " ".join(query.casefold().split())


def _cache_get(key: str) -> dict[str, Any] | None:
    cached = _SEARCH_CACHE.get(key)
    if cached is None:
        return None
    created_at, value = cached
    if time.monotonic() - created_at > SEARCH_CACHE_TTL_SECONDS:
        _SEARCH_CACHE.pop(key, None)
        return None
    _SEARCH_CACHE.move_to_end(key)
    return value


def _cache_set(key: str, value: dict[str, Any]) -> None:
    _SEARCH_CACHE[key] = (time.monotonic(), value)
    _SEARCH_CACHE.move_to_end(key)
    while len(_SEARCH_CACHE) > SEARCH_CACHE_MAX_ENTRIES:
        _SEARCH_CACHE.popitem(last=False)


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


async def _search_uncached(query: str) -> dict[str, Any]:
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
        "namedetails": 1,
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            response = await client.get(
                NOMINATIM_URL,
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Nominatim search failed") from exc

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

    return {
        "query": query,
        "results": results,
    }


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
