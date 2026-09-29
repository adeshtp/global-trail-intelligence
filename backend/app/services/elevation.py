from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import HTTPException


OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
MAX_PROFILE_POINTS = max(
    20,
    min(int(os.getenv("ELEVATION_MAX_PROFILE_POINTS", "100")), 100),
)
ELEVATION_CACHE_TTL_SECONDS = max(
    300.0,
    min(float(os.getenv("ELEVATION_CACHE_TTL_SECONDS", "21600")), 86400.0),
)
ELEVATION_CACHE_MAX_ENTRIES = max(
    16,
    min(int(os.getenv("ELEVATION_CACHE_MAX_ENTRIES", "128")), 512),
)
SLOPE_WINDOW_M = 150.0

# Minimum endpoint elevation difference that justifies a low-to-high
# presentation of the profile. A single DEM sample carries metre-scale
# noise, so differences below this margin cannot support a claim about
# which end of the route is lower. Below the margin the mapped geometry
# order is preserved and reported as such.
ORIENTATION_MIN_ENDPOINT_DIFF_M = 10.0

ELEVATION_HEADERS = {
    "User-Agent": "GoBeyond/1.0 (outdoor trail intelligence)",
}

_ELEVATION_CACHE: OrderedDict[
    str,
    tuple[float, dict[str, Any]],
] = OrderedDict()
_ELEVATION_INFLIGHT: dict[
    str,
    asyncio.Task[dict[str, Any]],
] = {}


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


def _dedupe(coordinates: list[list[float]]) -> list[list[float]]:
    result: list[list[float]] = []
    for coordinate in coordinates:
        if result and coordinate == result[-1]:
            continue
        result.append(coordinate)
    return result


def _geometry_segments(
    geometry: dict[str, Any],
) -> list[list[list[float]]]:
    if not isinstance(geometry, dict):
        return []
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "LineString":
        raw_segments = [coordinates]
    elif geometry_type == "MultiLineString":
        raw_segments = coordinates if isinstance(coordinates, list) else []
    else:
        return []

    segments: list[list[list[float]]] = []
    for raw_segment in raw_segments:
        cleaned = _dedupe(
            [
                coordinate
                for raw_coordinate in (
                    raw_segment if isinstance(raw_segment, list) else []
                )
                if (coordinate := _clean_coordinate(raw_coordinate))
                is not None
            ]
        )
        if cleaned:
            segments.append(cleaned)
    return segments


def _haversine_km(
    first: list[float],
    second: list[float],
) -> float:
    longitude_1, latitude_1 = first
    longitude_2, latitude_2 = second
    radius_km = 6371.0
    phi_1 = math.radians(latitude_1)
    phi_2 = math.radians(latitude_2)
    delta_phi = math.radians(latitude_2 - latitude_1)
    delta_lambda = math.radians(longitude_2 - longitude_1)
    value = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi_1)
        * math.cos(phi_2)
        * math.sin(delta_lambda / 2.0) ** 2
    )
    value = max(0.0, min(1.0, value))
    return radius_km * 2.0 * math.atan2(
        math.sqrt(value),
        math.sqrt(1.0 - value),
    )


def _line_length_km(coordinates: list[list[float]]) -> float:
    return sum(
        _haversine_km(coordinates[index], coordinates[index + 1])
        for index in range(len(coordinates) - 1)
    )


def _cumulative_distances(
    coordinates: list[list[float]],
) -> list[float]:
    distances = [0.0]
    for index in range(1, len(coordinates)):
        distances.append(
            distances[-1]
            + _haversine_km(
                coordinates[index - 1],
                coordinates[index],
            )
        )
    return distances


def _interpolate(
    first: list[float],
    second: list[float],
    ratio: float,
) -> list[float]:
    ratio = max(0.0, min(1.0, ratio))
    return [
        first[0] + (second[0] - first[0]) * ratio,
        first[1] + (second[1] - first[1]) * ratio,
    ]


def _sample_segment(
    coordinates: list[list[float]],
    target_points: int,
) -> list[list[float]]:
    if len(coordinates) <= target_points or target_points <= 1:
        return coordinates
    cumulative = _cumulative_distances(coordinates)
    total = cumulative[-1]
    if total <= 0.0:
        return [coordinates[0]]

    sampled: list[list[float]] = []
    segment_index = 0
    for sample_index in range(target_points):
        target_distance = total * sample_index / (target_points - 1)
        while (
            segment_index < len(cumulative) - 2
            and cumulative[segment_index + 1] < target_distance
        ):
            segment_index += 1
        segment_distance = (
            cumulative[segment_index + 1]
            - cumulative[segment_index]
        )
        if segment_distance <= 0.0:
            sampled.append(coordinates[segment_index])
            continue
        ratio = (
            target_distance - cumulative[segment_index]
        ) / segment_distance
        sampled.append(
            _interpolate(
                coordinates[segment_index],
                coordinates[segment_index + 1],
                ratio,
            )
        )
    return _dedupe(sampled)


def _allocate_samples(
    segments: list[list[list[float]]],
) -> tuple[list[list[list[float]]], bool]:
    if not segments:
        return [], False
    lengths = [_line_length_km(segment) for segment in segments]
    total_length = sum(lengths)
    complete = len(segments) <= MAX_PROFILE_POINTS

    if complete:
        selected_segments = segments
        selected_lengths = lengths
    else:
        ranked_indices = sorted(
            range(len(segments)),
            key=lambda index: lengths[index],
            reverse=True,
        )[:MAX_PROFILE_POINTS]
        selected_segments = [
            segments[index] for index in ranked_indices
        ]
        selected_lengths = [
            lengths[index] for index in ranked_indices
        ]
    selected_total = sum(selected_lengths)
    allocations = [1] * len(selected_segments)
    remaining = max(0, MAX_PROFILE_POINTS - len(selected_segments))
    if selected_total > 0.0 and remaining:
        for index, length in enumerate(selected_lengths):
            allocation = int(round(remaining * length / selected_total))
            allocations[index] += max(0, allocation)

    while sum(allocations) > MAX_PROFILE_POINTS:
        index = max(
            range(len(allocations)),
            key=lambda item: allocations[item],
        )
        if allocations[index] <= 1:
            break
        allocations[index] -= 1

    return (
        [
            _sample_segment(segment, max(1, allocation))
            for segment, allocation in zip(
                selected_segments,
                allocations,
            )
        ],
        complete,
    )


def _smooth(
    values: list[float],
    window_size: int = 5,
) -> list[float]:
    if len(values) < 3:
        return values
    half = window_size // 2
    return [
        sum(
            values[max(0, index - half):min(len(values), index + half + 1)]
        )
        / len(
            values[max(0, index - half):min(len(values), index + half + 1)]
        )
        for index in range(len(values))
    ]


def _terrain_metrics(
    profile: list[dict[str, Any]],
) -> dict[str, float | None]:
    usable = [
        point
        for point in profile
        if point.get("elevation_m") is not None
    ]
    if len(usable) < 2:
        return {
            "min_elevation_m": None,
            "max_elevation_m": None,
            "elevation_range_m": None,
            "elevation_gain_m": None,
            "elevation_loss_m": None,
            "average_slope_percent": None,
            "max_slope_percent": None,
            "terrain_available": False,
        }

    elevations = [float(point["elevation_m"]) for point in usable]
    gain = 0.0
    loss = 0.0
    for previous, current in zip(usable, usable[1:]):
        if previous["component_index"] != current["component_index"]:
            continue
        change = float(current["elevation_m"]) - float(previous["elevation_m"])
        if change > 0:
            gain += change
        elif change < 0:
            loss += -change

    slope_values: list[tuple[float, float]] = []
    for component_index in sorted(
        {int(point["component_index"]) for point in usable}
    ):
        component_points = [
            point
            for point in usable
            if point["component_index"] == component_index
        ]
        if len(component_points) < 2:
            continue
        elevations_component = _smooth(
            [float(point["elevation_m"]) for point in component_points]
        )
        start_index = 0
        while start_index < len(component_points) - 1:
            start_distance = float(
                component_points[start_index]["component_distance_km"]
            )
            target = start_distance + SLOPE_WINDOW_M / 1000.0
            end_index = start_index + 1
            while (
                end_index < len(component_points)
                and float(
                    component_points[end_index]["component_distance_km"]
                ) < target
            ):
                end_index += 1
            if end_index >= len(component_points):
                break
            horizontal_km = (
                float(component_points[end_index]["component_distance_km"])
                - start_distance
            )
            if horizontal_km <= 0.0:
                start_index += 1
                continue
            slope = abs(
                elevations_component[end_index]
                - elevations_component[start_index]
            ) / (horizontal_km * 1000.0) * 100.0
            slope_values.append((slope, horizontal_km))
            start_index += 1

    average_slope = None
    max_slope = None
    if slope_values:
        total_slope_distance = sum(distance for _, distance in slope_values)
        if total_slope_distance > 0.0:
            average_slope = sum(
                slope * distance for slope, distance in slope_values
            ) / total_slope_distance
        max_slope = max(slope for slope, _ in slope_values)

    minimum = min(elevations)
    maximum = max(elevations)
    return {
        "min_elevation_m": round(minimum, 1),
        "max_elevation_m": round(maximum, 1),
        "elevation_range_m": round(maximum - minimum, 1),
        "elevation_gain_m": round(gain, 1),
        "elevation_loss_m": round(loss, 1),
        "average_slope_percent": (
            round(average_slope, 2)
            if average_slope is not None
            else None
        ),
        "max_slope_percent": (
            round(max_slope, 2)
            if max_slope is not None
            else None
        ),
        "terrain_available": True,
    }


def _profile_orientation(
    profile: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Decide whether the sampled profile may be presented low-to-high.

    The stored profile order is ALWAYS the mapped geometry order: route
    difficulty features (gain/loss) are derived from it, so reordering it
    here would silently change the model's inputs. This decision only
    describes presentation, and the geometry itself is never touched.

    A low-to-high presentation is allowed only when all of these hold:

    * the profile covers exactly one route component (a MultiLineString
      keeps its mapped component order; continuity between components is
      never created to serve a chart),
    * the first and last sampled points in mapped order both carry usable
      elevations,
    * their difference reaches ORIENTATION_MIN_ENDPOINT_DIFF_M, so the
      direction claim survives metre-scale DEM noise.

    Otherwise the mapped order is preserved and said to be preserved.
    """
    basis: dict[str, float] | None = None
    orientation = "as_mapped"
    reversal_needed = False
    components = {
        point.get("component_index") for point in profile
    }
    if len(components) == 1 and len(profile) >= 2:
        start_elevation = profile[0].get("elevation_m")
        end_elevation = profile[-1].get("elevation_m")
        if isinstance(start_elevation, (int, float)) and isinstance(
            end_elevation, (int, float)
        ):
            start_value = float(start_elevation)
            end_value = float(end_elevation)
            difference = end_value - start_value
            basis = {
                "start_elevation_m": round(start_value, 1),
                "end_elevation_m": round(end_value, 1),
                "endpoint_difference_m": round(difference, 1),
            }
            if abs(difference) >= ORIENTATION_MIN_ENDPOINT_DIFF_M:
                orientation = "low_to_high"
                reversal_needed = start_value > end_value
    if orientation == "low_to_high":
        note = (
            "Profile may be presented from the lower end to the "
            "higher end; the stored point order follows the mapped "
            "geometry."
        )
    else:
        note = "Profile follows the mapped route order."
    return {
        "profile_orientation": orientation,
        "reversal_needed": reversal_needed,
        "orientation_basis": basis,
        "direction_note": note,
    }


def _geometry_hash(geometry: dict[str, Any]) -> str:
    canonical = json.dumps(
        geometry,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _cache_get(key: str) -> dict[str, Any] | None:
    cached = _ELEVATION_CACHE.get(key)
    if cached is None:
        return None
    created_at, value = cached
    if time.monotonic() - created_at > ELEVATION_CACHE_TTL_SECONDS:
        _ELEVATION_CACHE.pop(key, None)
        return None
    _ELEVATION_CACHE.move_to_end(key)
    return value


def _cache_set(key: str, value: dict[str, Any]) -> None:
    _ELEVATION_CACHE[key] = (time.monotonic(), value)
    _ELEVATION_CACHE.move_to_end(key)
    while len(_ELEVATION_CACHE) > ELEVATION_CACHE_MAX_ENTRIES:
        _ELEVATION_CACHE.popitem(last=False)


async def _fetch_elevation_uncached(
    geometry: dict[str, Any],
) -> dict[str, Any]:
    segments = _geometry_segments(geometry)
    if not segments:
        raise HTTPException(
            status_code=422,
            detail="No usable trail geometry was supplied",
        )

    sampled_segments, all_components_covered = _allocate_samples(segments)
    sampled_coordinates = [
        coordinate
        for segment in sampled_segments
        for coordinate in segment
    ]
    if not sampled_coordinates:
        raise HTTPException(
            status_code=422,
            detail="Trail geometry has no sampleable distance",
        )

    params = {
        "latitude": ",".join(
            str(coordinate[1]) for coordinate in sampled_coordinates
        ),
        "longitude": ",".join(
            str(coordinate[0]) for coordinate in sampled_coordinates
        ),
    }
    try:
        transport = httpx.AsyncHTTPTransport(retries=1)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=30.0,
                write=10.0,
                pool=10.0,
            ),
            headers=ELEVATION_HEADERS,
            transport=transport,
        ) as client:
            response = await client.get(
                OPEN_METEO_ELEVATION_URL,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Elevation service is temporarily unavailable",
        ) from exc

    provider_elevations = data.get("elevation") if isinstance(data, dict) else None
    if not isinstance(provider_elevations, list):
        raise HTTPException(
            status_code=502,
            detail="Elevation service returned an invalid response",
        )
    elevations: list[float | None] = []
    for value in provider_elevations:
        try:
            number = float(value)
        except (TypeError, ValueError):
            elevations.append(None)
        else:
            elevations.append(
                number if math.isfinite(number) else None
            )
    if not any(value is not None for value in elevations):
        raise HTTPException(
            status_code=502,
            detail="Elevation service returned no usable values",
        )

    profile: list[dict[str, Any]] = []
    coordinate_index = 0
    cumulative_distance = 0.0
    route_distance = sum(
        _line_length_km(segment) for segment in segments
    )
    for component_index, segment in enumerate(sampled_segments):
        cumulative = _cumulative_distances(segment)
        for point_index, coordinate in enumerate(segment):
            elevation = (
                elevations[coordinate_index]
                if coordinate_index < len(elevations)
                else None
            )
            component_distance = cumulative[point_index]
            if point_index > 0:
                cumulative_distance += (
                    component_distance
                    - cumulative[point_index - 1]
                )
            profile.append(
                {
                    "component_index": component_index,
                    "component_distance_km": round(
                        component_distance,
                        3,
                    ),
                    "distance_km": round(cumulative_distance, 3),
                    "longitude": coordinate[0],
                    "latitude": coordinate[1],
                    "elevation_m": (
                        round(elevation, 1)
                        if elevation is not None
                        else None
                    ),
                }
            )
            coordinate_index += 1

    return {
        "source": "Open-Meteo",
        "sampled_points": len(profile),
        "component_count": len(segments),
        "sampled_component_count": len(sampled_segments),
        "all_components_covered": all_components_covered,
        "route_distance_km": round(route_distance, 3),
        "profile": profile,
        "metrics": _terrain_metrics(profile),
        # Presentation-only direction decision. The profile above always
        # stays in mapped geometry order (difficulty features read it), so
        # a low-to-high chart is produced by the client from these fields,
        # never by reordering stored data.
        **_profile_orientation(profile),
    }


async def get_elevation_profile(
    geometry: dict[str, Any],
) -> dict[str, Any]:
    key = _geometry_hash(geometry)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _ELEVATION_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _fetch_elevation_uncached(geometry)
        )
        _ELEVATION_INFLIGHT[key] = task
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _ELEVATION_INFLIGHT.get(key) is task:
            _ELEVATION_INFLIGHT.pop(key, None)

    _cache_set(key, result)
    return result
