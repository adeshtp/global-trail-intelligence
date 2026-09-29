from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.services.difficulty import (
    difficulty_readiness,
    predict_trail_difficulty,
    reconcile_difficulty,
    source_difficulty,
)
from app.services.elevation import get_elevation_profile
from app.services.postpass import (
    get_relation,
    get_way,
    get_ways,
    measure_geometry_completeness,
)
from app.services.intelligence import (
    condition_likelihood,
    gear_recommendations,
    route_suitability_context,
)
from app.services.products import discover_products
from app.services.route_complexity import route_complexity
from app.services.rate_limit import enrichment_limiter, intelligence_limiter
from app.services.assistant import answer_trail_question
from app.services.weather import (
    get_route_weather,
    get_weather,
    select_route_points,
)


router = APIRouter(
    tags=["Selected Trail"],
)


def _enrichment_client_key(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def _check_enrichment_limit(
    limiter: Any,
    request: Request | None,
    *,
    limit: int,
) -> None:
    key = _enrichment_client_key(request)
    if key and not limiter.allow(
        key,
        limit=limit,
        window_seconds=60.0,
    ):
        raise HTTPException(
            status_code=429,
            detail="Trail intelligence request limit exceeded; retry shortly",
        )


class TrailAnalysisRequest(BaseModel):
    geometry: dict[str, Any] | None = None
    trail: dict[str, Any] | None = None
    feature_coordinate: list[float] | None = None

    @field_validator("feature_coordinate")
    @classmethod
    def validate_feature_coordinate(
        cls,
        value: list[float] | None,
    ) -> list[float] | None:
        if value is None:
            return None
        if len(value) != 2:
            raise ValueError(
                "feature_coordinate must be [longitude, latitude]"
            )
        longitude, latitude = value
        if not (
            math.isfinite(longitude)
            and math.isfinite(latitude)
            and -180.0 <= longitude <= 180.0
            and -90.0 <= latitude <= 90.0
        ):
            raise ValueError("feature_coordinate is outside valid bounds")
        return [longitude, latitude]


class TrailIntelligenceRequest(BaseModel):
    trail: dict[str, Any]


class ProductDiscoveryRequest(BaseModel):
    intelligence: dict[str, Any]


class TrailAssistantRequest(BaseModel):
    question: str
    trail: dict[str, Any]
    intelligence: dict[str, Any]


def _clean_coordinate(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        longitude = float(value[0])
        latitude = float(value[1])
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and -180.0 <= longitude <= 180.0
        and -90.0 <= latitude <= 90.0
    ):
        return None
    return [longitude, latitude]


def _dedupe_coordinates(
    coordinates: Any,
) -> list[list[float]]:
    result: list[list[float]] = []
    for raw_coordinate in coordinates if isinstance(coordinates, list) else []:
        coordinate = _clean_coordinate(raw_coordinate)
        if coordinate is None:
            continue
        if result and coordinate == result[-1]:
            continue
        result.append(coordinate)
    return result


def _geometry_parts(
    geometry: dict[str, Any],
) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return []
    if geometry_type == "LineString":
        line = _dedupe_coordinates(coordinates)
        return [line] if len(line) >= 2 else []
    if geometry_type == "MultiLineString":
        return [
            line
            for raw_line in coordinates
            if len(line := _dedupe_coordinates(raw_line)) >= 2
        ]
    return []


def _points_equal(
    first: list[float],
    second: list[float],
) -> bool:
    return first == second


def _stitch_connected_parts(
    parts: list[list[list[float]]],
) -> list[list[list[float]]]:
    remaining = [list(part) for part in parts if len(part) >= 2]
    connected: list[list[list[float]]] = []

    while remaining:
        current = remaining.pop(0)
        changed = True
        while changed and remaining:
            changed = False
            for index, candidate in enumerate(remaining):
                if _points_equal(current[-1], candidate[0]):
                    current.extend(candidate[1:])
                elif _points_equal(current[-1], candidate[-1]):
                    current.extend(reversed(candidate[:-1]))
                elif _points_equal(current[0], candidate[-1]):
                    current = candidate[:-1] + current
                elif _points_equal(current[0], candidate[0]):
                    current = list(reversed(candidate[1:])) + current
                else:
                    continue
                remaining.pop(index)
                changed = True
                break
        current = _dedupe_coordinates(current)
        if len(current) >= 2:
            connected.append(current)

    return connected


def _haversine_km(
    first: list[float],
    second: list[float],
) -> float:
    longitude1, latitude1 = first
    longitude2, latitude2 = second
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


def _line_length_km(
    coordinates: list[list[float]],
) -> float:
    return sum(
        _haversine_km(coordinates[index], coordinates[index + 1])
        for index in range(len(coordinates) - 1)
    )


def _geometry_object(
    parts: list[list[list[float]]],
) -> dict[str, Any] | None:
    parts = [part for part in parts if len(part) >= 2]
    if not parts:
        return None
    if len(parts) == 1:
        return {
            "type": "LineString",
            "coordinates": parts[0],
        }
    return {
        "type": "MultiLineString",
        "coordinates": parts,
    }


def _orient_to_feature(
    coordinates: list[list[float]],
    feature_coordinate: list[float] | None,
) -> tuple[list[list[float]], float | None]:
    if not coordinates or feature_coordinate is None:
        return coordinates, None
    start_distance = _haversine_km(
        coordinates[0],
        feature_coordinate,
    )
    end_distance = _haversine_km(
        coordinates[-1],
        feature_coordinate,
    )
    if end_distance < start_distance:
        return list(reversed(coordinates)), end_distance
    return coordinates, start_distance


def _midpoint(
    coordinates: list[list[float]],
) -> list[float] | None:
    if not coordinates:
        return None
    if len(coordinates) == 1:
        return coordinates[0]
    lengths = [
        _haversine_km(coordinates[index], coordinates[index + 1])
        for index in range(len(coordinates) - 1)
    ]
    total = sum(lengths)
    if total <= 0.0:
        return coordinates[len(coordinates) // 2]
    target = total / 2.0
    walked = 0.0
    for index, length in enumerate(lengths):
        if walked + length >= target:
            ratio = (target - walked) / max(length, 1e-12)
            first = coordinates[index]
            second = coordinates[index + 1]
            return [
                first[0] + (second[0] - first[0]) * ratio,
                first[1] + (second[1] - first[1]) * ratio,
            ]
        walked += length
    return coordinates[-1]


def _bbox(
    parts: list[list[list[float]]],
) -> list[float] | None:
    points = [point for part in parts for point in part]
    if not points:
        return None
    return [
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    ]


def _geometry_hash(geometry: dict[str, Any]) -> str:
    canonical = json.dumps(
        geometry,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalise_route(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _geometry_fingerprint(
    geometry: dict[str, Any],
) -> tuple[tuple[tuple[float, float], ...], ...]:
    parts = _geometry_parts(geometry)
    fingerprints: list[tuple[tuple[float, float], ...]] = []
    for part in parts:
        forward = tuple(
            (round(point[0], 7), round(point[1], 7))
            for point in part
        )
        reverse = tuple(reversed(forward))
        fingerprints.append(min(forward, reverse))
    return tuple(sorted(fingerprints))


def _geometry_equivalent(
    first: dict[str, Any],
    second: dict[str, Any],
) -> bool:
    return _geometry_fingerprint(first) == _geometry_fingerprint(second)


async def verify_selected_trail(
    trail: dict[str, Any],
) -> dict[str, Any]:
    osm_type = trail.get("osm_type")
    if osm_type == "way":
        try:
            osm_id = int(trail.get("osm_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Selected way identity is invalid",
            ) from exc
        try:
            way = await get_way(osm_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="OSM way verification is temporarily unavailable",
            ) from exc
        if way is None or not way.geometry:
            raise HTTPException(
                status_code=409,
                detail="Selected way is no longer available with verified geometry",
            )
        server_geometry = way.geometry
        verified = {
            **trail,
            "name": way.name,
            "route_type": _normalise_route(way.route),
            "highway_type": way.highway,
            "surface": way.surface,
            "trail_visibility": way.trail_visibility,
            "source_difficulty": way.sac_scale,
            "assisted_trail": way.assisted_trail,
            "geometry": server_geometry,
            "geometry_hash": _geometry_hash(server_geometry),
            "member_way_ids": [way.way_id],
            "ordered_way_ids": [way.way_id],
        }
    elif osm_type == "relation":
        try:
            osm_id = int(trail.get("osm_id"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Selected relation identity is invalid",
            ) from exc
        try:
            relation = await get_relation(osm_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="OSM relation verification is temporarily unavailable",
            ) from exc
        if relation is None or not relation.geometry:
            raise HTTPException(
                status_code=409,
                detail="Selected relation is no longer available with verified geometry",
            )
        server_geometry = relation.geometry
        verified = {
            **trail,
            "name": relation.name,
            "route_type": _normalise_route(relation.route),
            "surface": relation.surface,
            "trail_visibility": relation.trail_visibility,
            "source_difficulty": relation.sac_scale,
            "geometry": server_geometry,
            "geometry_hash": _geometry_hash(server_geometry),
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
        }
    elif osm_type == "component":
        member_ids = trail.get("member_way_ids") or []
        if not isinstance(member_ids, list) or not member_ids:
            raise HTTPException(
                status_code=422,
                detail="Selected component has no member way identity",
            )
        try:
            member_ids = [int(member_id) for member_id in member_ids]
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Selected component member identity is invalid",
            ) from exc
        member_ways = await asyncio.gather(
            *(get_way(member_id) for member_id in member_ids),
            return_exceptions=True,
        )
        if any(
            isinstance(member_way, Exception)
            for member_way in member_ways
        ):
            raise HTTPException(
                status_code=503,
                detail="OSM component verification is temporarily unavailable",
            )
        if any(
            member_way is None or not member_way.geometry
            for member_way in member_ways
        ):
            raise HTTPException(
                status_code=409,
                detail="Selected component members are no longer available with verified geometry",
            )
        segments: list[list[list[float]]] = []
        for member_way in member_ways:
            if isinstance(member_way, Exception) or member_way is None:
                continue
            segments.extend(_geometry_parts(member_way.geometry))
        if not segments:
            raise HTTPException(
                status_code=409,
                detail="Selected component has no verified geometry",
            )
        server_geometry = {
            "type": "MultiLineString",
            "coordinates": segments,
        }
        verified = {
            **trail,
            "geometry": server_geometry,
            "geometry_hash": _geometry_hash(server_geometry),
            "member_way_ids": member_ids,
            "ordered_way_ids": member_ids,
        }
    else:
        raise HTTPException(
            status_code=422,
            detail="A verified OSM trail identity is required",
        )

    client_geometry = trail.get("geometry")
    client_hash = trail.get("geometry_hash")
    if (
        isinstance(client_geometry, dict)
        and client_hash
        and not _geometry_equivalent(client_geometry, verified["geometry"])
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected geometry no longer matches the verified OSM identity",
        )
    return verified


async def _verified_member_trails(
    trail: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Re-verify each member way of a route selection for difficulty scoring.

    Returns one serving-shape trail dict per verified member: its own OSM
    tags, its own measured length, its own geometry. A member that cannot be
    verified is skipped, because one stale section must not veto the route
    answer — the aggregation reports how many members were actually scored.
    No terrain profile is attached: member terrain is not sampled per member,
    and the model reads those predictors as missing by design.
    """
    member_ids = trail.get("member_way_ids") or []
    try:
        ordered_ids = [
            int(member_id)
            for member_id in member_ids
            if member_id is not None
        ]
    except (TypeError, ValueError):
        return []
    # One bulk lookup for every member, not one query per way: a long route has
    # hundreds of members and fetching them one by one took minutes.
    try:
        ways = await get_ways(ordered_ids)
    except Exception:
        return []
    member_trails: list[dict[str, Any]] = []
    for member_id in ordered_ids:
        way = ways.get(member_id)
        if way is None or not way.geometry:
            continue
        member_trails.append(
            {
                "osm_type": "way",
                "osm_id": way.way_id,
                "name": way.name,
                "route_type": _normalise_route(way.route),
                "highway_type": way.highway,
                "surface": way.surface,
                "smoothness": way.smoothness,
                "tracktype": way.tracktype,
                "trail_visibility": way.trail_visibility,
                "incline": way.incline,
                "incline_direction": way.incline_direction,
                "width": way.width,
                "assisted_trail": way.assisted_trail,
                # Read by the activity classifier only. The difficulty
                # model's features do not include it.
                "sac_scale": way.sac_scale,
                "length_km": way.length_km,
                "geometry": way.geometry,
                "source": way.source,
            }
        )
    return member_trails


def normalize_selected_geometry(
    geometry: dict[str, Any],
    feature_coordinate: list[float] | None = None,
) -> dict[str, Any]:
    parts = _geometry_parts(geometry)
    if not parts:
        raise HTTPException(
            status_code=422,
            detail="No usable LineString or MultiLineString geometry was supplied",
        )

    connected = _stitch_connected_parts(parts)
    if not connected:
        raise HTTPException(
            status_code=422,
            detail="Geometry contains no usable connected segments",
        )

    primary_index = max(
        range(len(connected)),
        key=lambda index: _line_length_km(connected[index]),
    )
    primary, feature_distance = _orient_to_feature(
        connected[primary_index],
        feature_coordinate,
    )
    normalized_parts = [
        primary,
        *[
            part
            for index, part in enumerate(connected)
            if index != primary_index
        ],
    ]
    normalized_geometry = _geometry_object(normalized_parts)
    if normalized_geometry is None:
        raise HTTPException(
            status_code=422,
            detail="Geometry could not be normalized",
        )

    return {
        "geometry": normalized_geometry,
        "geometry_type": normalized_geometry["type"],
        "normalized_geometry_hash": _geometry_hash(normalized_geometry),
        "geometry_status": (
            "connected"
            if len(normalized_parts) == 1
            else "fragmented"
        ),
        "component_count": len(normalized_parts),
        # Gap measurement over the same parts. `geometry_status` and
        # `component_count` above count pieces joined only at identical
        # points; this also treats ends within a few metres as touching and
        # says how large the remaining gaps are.
        "completeness": asdict(measure_geometry_completeness(normalized_parts)),
        "coordinate_count": sum(
            len(part) for part in normalized_parts
        ),
        "distance_km": round(
            sum(_line_length_km(part) for part in normalized_parts),
            3,
        ),
        "analysis_distance_km": round(
            _line_length_km(primary),
            3,
        ),
        "start_coordinate": primary[0],
        "end_coordinate": primary[-1],
        "midpoint_coordinate": _midpoint(primary),
        "bbox": _bbox(normalized_parts),
        "feature_distance_km": (
            round(feature_distance, 3)
            if feature_distance is not None
            else None
        ),
        "feature_oriented": feature_coordinate is not None,
    }


@router.post("/api/osm/trails/analysis")
async def analyze_selected_trail(
    payload: TrailAnalysisRequest,
    request: Request = None,
) -> dict[str, Any]:
    _check_enrichment_limit(
        intelligence_limiter,
        request,
        limit=60,
    )
    geometry = payload.geometry
    if geometry is None and payload.trail:
        geometry = payload.trail.get("geometry")
    if not geometry:
        raise HTTPException(
            status_code=422,
            detail="Supply geometry or trail.geometry",
        )

    normalized = normalize_selected_geometry(
        geometry,
        payload.feature_coordinate,
    )
    result: dict[str, Any] = {
        "source": "OpenStreetMap",
        "analysis_version": "selected_geometry_v1",
        **normalized,
    }
    if payload.trail:
        for key in (
            "trail_id",
            "osm_type",
            "osm_id",
            "name",
            "route_type",
            "member_way_ids",
            "ordered_way_ids",
            "geometry_hash",
            "geometry_provenance",
        ):
            if key in payload.trail:
                result[key] = payload.trail[key]
    return result


@router.get("/api/trails/difficulty/readiness")
async def get_difficulty_readiness() -> dict[str, Any]:
    return difficulty_readiness()


@router.post("/api/trails/intelligence")
async def get_selected_trail_intelligence(
    payload: TrailIntelligenceRequest,
    request: Request = None,
) -> dict[str, Any]:
    _check_enrichment_limit(
        intelligence_limiter,
        request,
        limit=30,
    )
    trail = payload.trail
    geometry = trail.get("geometry")
    if not trail.get("map_ready") or not isinstance(geometry, dict):
        raise HTTPException(
            status_code=422,
            detail="A MAP_READY trail with real geometry is required",
        )

    trail = await verify_selected_trail(trail)
    geometry = trail["geometry"]
    analysis = normalize_selected_geometry(geometry)
    analysis["selected_geometry_hash"] = trail.get("geometry_hash")
    analysis["trail_id"] = trail.get("trail_id")
    analysis["geometry_provenance"] = trail.get("geometry_provenance")
    midpoint = analysis.get("midpoint_coordinate")
    if not isinstance(midpoint, list) or len(midpoint) != 2:
        raise HTTPException(
            status_code=422,
            detail="Selected geometry has no representative midpoint",
        )

    # Elevation says where along the route the weather should be read: start,
    # highest, lowest, end and middle, each at its own height. A route with only
    # one distinct place on it, or with no profile, is weathered at its
    # midpoint as before.
    #
    # The midpoint reading is started alongside elevation rather than after it,
    # so a hung provider is waited on once and not twice (measured 50 s
    # against 30 s). It is a cheap, cached call, and it is dropped when the
    # route has real points to read.
    midpoint_weather = asyncio.create_task(
        get_weather(
            latitude=midpoint[1],
            longitude=midpoint[0],
        )
    )
    # A dropped or failed fallback must not log "exception never retrieved".
    midpoint_weather.add_done_callback(
        lambda task: None if task.cancelled() else task.exception()
    )
    (elevation_result,) = await asyncio.gather(
        get_elevation_profile(analysis["geometry"]),
        return_exceptions=True,
    )
    route_points = (
        []
        if isinstance(elevation_result, Exception)
        else select_route_points(elevation_result.get("profile") or [])
    )
    try:
        if len(route_points) > 1:
            midpoint_weather.cancel()
            weather_result = await get_route_weather(route_points)
        else:
            weather_result = await midpoint_weather
    except Exception as exc:
        weather_result = exc

    provider_status: dict[str, str] = {}
    if isinstance(weather_result, Exception):
        weather = None
        provider_status["weather"] = "unavailable"
    else:
        weather = weather_result
        provider_status["weather"] = "ok"

    if isinstance(elevation_result, Exception):
        terrain = None
        provider_status["elevation"] = "unavailable"
    else:
        terrain = elevation_result
        provider_status["elevation"] = "ok"

    enriched_trail = {
        **trail,
        "terrain": terrain,
    }
    source = source_difficulty(trail)
    if trail.get("osm_type") in ("relation", "component"):
        # Route-level difficulty is answered from verified member ways, never
        # from route totals. Members are re-verified individually (cached), a
        # failed member is skipped rather than failing the route, and the
        # aggregation reports exactly what was scored.
        enriched_trail["member_trails"] = await _verified_member_trails(
            trail
        )
    ml = predict_trail_difficulty(enriched_trail)
    enriched_trail["difficulty_estimate"] = ml
    enriched_trail["difficulty"] = source
    condition = condition_likelihood(
        enriched_trail,
        terrain,
        weather,
    )
    suitability = route_suitability_context(
        enriched_trail,
        analysis,
        terrain,
        condition,
    )
    gear = gear_recommendations(
        enriched_trail,
        analysis,
        weather,
        condition,
    )
    complexity = route_complexity(analysis, terrain, trail)

    # One difficulty model, one decision. The recorded OpenStreetMap scale is
    # authoritative when it exists; the learned estimate is a clearly labelled
    # fallback, never a second competing answer.
    difficulty_reconciliation = reconcile_difficulty(source, ml)

    analysis_response = {
        key: value
        for key, value in analysis.items()
        if key != "geometry"
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trail": {
            key: trail.get(key)
            for key in (
                "trail_id",
                "osm_type",
                "osm_id",
                "name",
                "route_type",
                "highway_type",
                "source",
                "geometry_hash",
                "geometry_provenance",
                "member_way_ids",
                "ordered_way_ids",
                "relation_members",
            )
        },
        "weather_coordinate": {
            "latitude": midpoint[1],
            "longitude": midpoint[0],
            "basis": (
                "Representative midpoint of the selected route geometry, "
                "not the originally searched place."
                + (
                    f" Conditions are the worst case across "
                    f"{weather['sample_count']} points sampled along the "
                    f"route, each at its own elevation."
                    if weather and weather.get("aggregation") == "worst_case"
                    else ""
                )
            ),
        },
        "weather_samples": (weather or {}).get("samples") or [],
        "analysis": analysis_response,
        "terrain": terrain,
        "weather": weather,
        "difficulty": {
            "source": source,
            "ml": ml,
            "reconciliation": difficulty_reconciliation,
            "display": difficulty_reconciliation["display"],
            "display_provenance": difficulty_reconciliation[
                "display_provenance"
            ],
            "model_readiness": difficulty_readiness(),
        },
        "condition": condition,
        "suitability": suitability,
        "gear": gear,
        "activity": gear["activity"],
        "route_complexity": complexity,
        "providers": provider_status,
    }


@router.post("/api/trails/products")
async def get_trail_products(
    payload: ProductDiscoveryRequest,
    request: Request = None,
) -> dict[str, Any]:
    _check_enrichment_limit(
        enrichment_limiter,
        request,
        limit=10,
    )
    return await discover_products(payload.intelligence)


@router.post("/api/trails/assistant")
async def ask_trail_assistant(
    payload: TrailAssistantRequest,
    request: Request = None,
) -> dict[str, Any]:
    _check_enrichment_limit(
        enrichment_limiter,
        request,
        limit=20,
    )
    return await answer_trail_question(
        payload.question,
        payload.trail,
        payload.intelligence,
    )
