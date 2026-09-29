from __future__ import annotations

import asyncio
import math
import os
import time
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import HTTPException

from app.services.rate_limit import is_provider_outage, open_meteo_breaker


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_CACHE_TTL_SECONDS = max(
    60.0,
    min(float(os.getenv("WEATHER_CACHE_TTL_SECONDS", "600")), 3600.0),
)
WEATHER_CACHE_MAX_ENTRIES = max(
    32,
    min(int(os.getenv("WEATHER_CACHE_MAX_ENTRIES", "256")), 2048),
)

WEATHER_HEADERS = {
    "User-Agent": "GoBeyond/1.0 (outdoor trail intelligence)",
}

# Keyed by rounded coordinates: one (lat, lon) pair for a single point, one
# (lat, lon, elevation) triple per point for a route.
_WEATHER_CACHE: OrderedDict[
    tuple[Any, ...],
    tuple[float, dict[str, Any]],
] = OrderedDict()
_WEATHER_INFLIGHT: dict[
    tuple[Any, ...],
    asyncio.Task[dict[str, Any]],
] = {}


def weather_code_description(
    weather_code: int | None,
) -> str:
    descriptions = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snowfall",
        73: "Moderate snowfall",
        75: "Heavy snowfall",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    if weather_code is None:
        return "Unknown"
    return descriptions.get(weather_code, "Unknown")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sum_window(
    values: list[Any],
    start: int,
    end: int,
) -> float | None:
    numbers = [
        number
        for value in values[start:end]
        if (number := _number(value)) is not None
    ]
    return round(sum(numbers), 2) if numbers else None


# Naismith's rule: 5 km/h on the flat plus one hour for every 600 m of ascent.
# It is a planning convention, not a prediction of anyone's pace.
NAISMITH_KM_PER_HOUR = 5.0
NAISMITH_ASCENT_M_PER_HOUR = 600.0
# The forecast is read two days ahead, so a window cannot usefully be longer
# than a day; a multi-day route is judged on its first day.
MIN_WINDOW_HOURS = 1.0
MAX_WINDOW_HOURS = 24.0


# Hourly series read only when a route window is asked for.
WINDOW_HOURLY_VARIABLES = (
    "temperature_2m",
    "wind_speed_10m",
    "wind_gusts_10m",
    "snowfall",
    "snow_depth",
    "freezing_level_height",
)

# Inferring snow underfoot. Thresholds are stated here so they can be argued
# with: snow already lying, snow expected during the walk, or recent snow with
# the freezing level below the top of the route.
SNOW_LYING_LIKELY_M = 0.05
SNOW_IN_WINDOW_LIKELY_CM = 0.5
RECENT_SNOW_LIKELY_CM = 2.0


def estimate_walking_hours(
    distance_km: float | None,
    ascent_m: float | None,
) -> float | None:
    """
    Time a route takes to walk, by Naismith's rule, bounded to 1-24 h.

    With no ascent known only the distance counts. With no distance there is
    no estimate, and no window.
    """
    distance = _number(distance_km)
    if distance is None or distance <= 0.0:
        return None
    ascent = _number(ascent_m) or 0.0
    hours = distance / NAISMITH_KM_PER_HOUR + max(ascent, 0.0) / (
        NAISMITH_ASCENT_M_PER_HOUR
    )
    return max(MIN_WINDOW_HOURS, min(MAX_WINDOW_HOURS, hours))


def _hourly_slice(
    hourly: dict[str, Any],
    key: str,
    start: int,
    end: int,
) -> list[float]:
    series = hourly.get(key) or []
    return [
        number
        for value in series[max(0, start):end]
        if (number := _number(value)) is not None
    ]


def _window_summary(
    hourly: dict[str, Any],
    current_index: int,
    hours: float,
) -> dict[str, Any]:
    """
    The harshest hourly values from now until the walk is estimated to end.

    Both ends are included: the walk starts in the current hour and finishes
    ``hours`` later. Missing series stay None; they are never read as zero.
    """
    end = current_index + int(math.ceil(hours)) + 1

    def worst(key: str, pick: Any) -> float | None:
        values = _hourly_slice(hourly, key, current_index, end)
        return pick(values) if values else None

    snowfall = _hourly_slice(hourly, "snowfall", current_index, end)
    recent = _hourly_slice(hourly, "snowfall", current_index - 72, current_index)
    return {
        "hours": hours,
        "min_temperature": worst("temperature_2m", min),
        "max_wind_speed": worst("wind_speed_10m", max),
        "max_wind_gust": worst("wind_gusts_10m", max),
        "max_precipitation_mm": worst("precipitation", max),
        "snowfall_cm": round(sum(snowfall), 2) if snowfall else None,
        "max_snow_depth_m": worst("snow_depth", max),
        "min_freezing_level_m": worst("freezing_level_height", min),
        "recent_snowfall_cm_72h": round(sum(recent), 2) if recent else None,
    }


def normalize_weather_response(
    data: dict[str, Any],
    window_hours: float | None = None,
) -> dict[str, Any]:
    current = data.get("current") or {}
    hourly = data.get("hourly") or {}
    if not isinstance(current, dict) or not isinstance(hourly, dict):
        raise ValueError("Open-Meteo returned an invalid weather payload")

    hourly_times = hourly.get("time") or []
    precipitation = hourly.get("precipitation") or []
    rain = hourly.get("rain") or []
    probabilities = hourly.get("precipitation_probability") or []
    current_time = current.get("time")

    current_index: int | None = None
    if isinstance(hourly_times, list) and current_time:
        current_hour = str(current_time)[:13]
        current_index = next(
            (
                index
                for index, hourly_time in enumerate(hourly_times)
                if str(hourly_time)[:13] == current_hour
            ),
            None,
        )
    if current_index is None and isinstance(hourly_times, list) and hourly_times:
        current_index = 0

    recent_precipitation = {
        "24h_mm": None,
        "48h_mm": None,
        "72h_mm": None,
    }
    recent_rain = {
        "24h_mm": None,
        "48h_mm": None,
        "72h_mm": None,
    }
    next_24h = {
        "precipitation_mm": None,
        "rain_mm": None,
        "precipitation_probability_max": None,
    }
    current_probability = None

    if current_index is not None:
        for hours in (24, 48, 72):
            start = max(0, current_index - hours + 1)
            recent_precipitation[f"{hours}h_mm"] = _sum_window(
                precipitation,
                start,
                current_index + 1,
            )
            recent_rain[f"{hours}h_mm"] = _sum_window(
                rain,
                start,
                current_index + 1,
            )

        next_24h["precipitation_mm"] = _sum_window(
            precipitation,
            current_index + 1,
            current_index + 25,
        )
        next_24h["rain_mm"] = _sum_window(
            rain,
            current_index + 1,
            current_index + 25,
        )
        next_probabilities = [
            number
            for value in probabilities[current_index + 1:current_index + 25]
            if (number := _number(value)) is not None
        ]
        if next_probabilities:
            next_24h["precipitation_probability_max"] = max(
                next_probabilities
            )
        if current_index < len(probabilities):
            current_probability = _number(probabilities[current_index])

    weather_code = _number(current.get("weather_code"))
    weather_code_value = (
        int(weather_code)
        if weather_code is not None
        else None
    )

    window = (
        _window_summary(hourly, current_index, window_hours)
        if window_hours is not None and current_index is not None
        else None
    )

    result = {
        "source": "Open-Meteo",
        "latitude": _number(data.get("latitude")),
        "longitude": _number(data.get("longitude")),
        "timezone": data.get("timezone"),
        "current": {
            "time": current_time,
            "temperature": _number(current.get("temperature_2m")),
            "humidity": _number(current.get("relative_humidity_2m")),
            "precipitation": _number(current.get("precipitation")),
            "rain": _number(current.get("rain")),
            "showers": _number(current.get("showers")),
            "snowfall": _number(current.get("snowfall")),
            "precipitation_probability": current_probability,
            "wind_speed": _number(current.get("wind_speed_10m")),
            "weather_code": weather_code_value,
            "weather_condition": weather_code_description(
                weather_code_value
            ),
        },
        "recent_precipitation": recent_precipitation,
        "recent_rain": recent_rain,
        "forecast": next_24h,
    }
    if window is not None:
        result["window"] = window
    return result


def _cache_key(
    latitude: float,
    longitude: float,
) -> tuple[float, float]:
    return round(latitude, 4), round(longitude, 4)


def _cache_get(
    key: tuple[Any, ...],
) -> dict[str, Any] | None:
    cached = _WEATHER_CACHE.get(key)
    if cached is None:
        return None
    created_at, value = cached
    if time.monotonic() - created_at > WEATHER_CACHE_TTL_SECONDS:
        _WEATHER_CACHE.pop(key, None)
        return None
    _WEATHER_CACHE.move_to_end(key)
    return value


def _cache_set(
    key: tuple[Any, ...],
    value: dict[str, Any],
) -> None:
    _WEATHER_CACHE[key] = (time.monotonic(), value)
    _WEATHER_CACHE.move_to_end(key)
    while len(_WEATHER_CACHE) > WEATHER_CACHE_MAX_ENTRIES:
        _WEATHER_CACHE.popitem(last=False)


async def _fetch_weather_uncached(
    latitude: float,
    longitude: float,
    window_hours: float | None = None,
) -> dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "rain",
                "showers",
                "snowfall",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "hourly": ",".join(
            [
                "precipitation_probability",
                "precipitation",
                "rain",
                "showers",
                *(WINDOW_HOURLY_VARIABLES if window_hours else []),
            ]
        ),
        "past_days": 3,
        # A window can run into tomorrow, so the forecast reaches two days.
        "forecast_days": 2 if window_hours else 1,
        "timezone": "auto",
    }
    # One breaker for the whole Open-Meteo host, shared with elevation.
    if not open_meteo_breaker.allow():
        raise HTTPException(
            status_code=502,
            detail="Weather service is temporarily unavailable",
        )
    try:
        transport = httpx.AsyncHTTPTransport(retries=1)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=20.0,
                write=10.0,
                pool=10.0,
            ),
            headers=WEATHER_HEADERS,
            transport=transport,
        ) as client:
            response = await client.get(
                OPEN_METEO_URL,
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Open-Meteo returned an invalid response")
        result = normalize_weather_response(data, window_hours=window_hours)
        open_meteo_breaker.record_success()
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        if is_provider_outage(exc):
            open_meteo_breaker.record_failure()
        else:
            # The host answered, even if not usefully.
            open_meteo_breaker.record_success()
        raise HTTPException(
            status_code=502,
            detail="Weather service is temporarily unavailable",
        ) from exc


# ============================================================
# WEATHER ALONG A ROUTE
# ============================================================

# Two sample points closer than this, and within ROUTE_POINT_MIN_ELEVATION_GAP_M
# of each other in height, are the same weather and cost one location.
ROUTE_POINT_MIN_SEPARATION_KM = 0.3
ROUTE_POINT_MIN_ELEVATION_GAP_M = 150.0
_KM_PER_DEGREE = 111.195


def _km_between(first: dict[str, Any], second: dict[str, Any]) -> float:
    mean_latitude = math.radians(
        (first["latitude"] + second["latitude"]) / 2.0
    )
    return math.hypot(
        (second["longitude"] - first["longitude"])
        * math.cos(mean_latitude)
        * _KM_PER_DEGREE,
        (second["latitude"] - first["latitude"]) * _KM_PER_DEGREE,
    )


def select_route_points(
    profile: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Choose where along a route to read the weather, from its elevation profile.

    The start, the highest point, the lowest point, the end and the middle of
    the route, each carrying the elevation it is read at. Air temperature
    falls with height, so a route that climbs is not one weather. Points that
    are effectively the same place collapse into one sample that keeps all
    their labels. Returns an empty list when the profile has no usable points.
    """
    valid = [
        point
        for point in profile
        if isinstance(point, dict)
        and _number(point.get("latitude")) is not None
        and _number(point.get("longitude")) is not None
        and _number(point.get("elevation_m")) is not None
    ]
    if not valid:
        return []

    total = max(_number(point.get("distance_km")) or 0.0 for point in valid)
    picks = [
        ("start", valid[0]),
        ("highest", max(valid, key=lambda p: p["elevation_m"])),
        ("lowest", min(valid, key=lambda p: p["elevation_m"])),
        ("end", valid[-1]),
        (
            "midpoint",
            min(
                valid,
                key=lambda p: abs(
                    (_number(p.get("distance_km")) or 0.0) - total / 2.0
                ),
            ),
        ),
    ]

    samples: list[dict[str, Any]] = []
    for label, point in picks:
        candidate = {
            "labels": [label],
            "latitude": float(point["latitude"]),
            "longitude": float(point["longitude"]),
            "elevation_m": float(point["elevation_m"]),
        }
        twin = next(
            (
                sample
                for sample in samples
                if _km_between(sample, candidate) < ROUTE_POINT_MIN_SEPARATION_KM
                and abs(sample["elevation_m"] - candidate["elevation_m"])
                < ROUTE_POINT_MIN_ELEVATION_GAP_M
            ),
            None,
        )
        if twin is None:
            samples.append(candidate)
        else:
            twin["labels"].append(label)
    return samples


# WMO weather codes are not ordered by how bad the weather is (80, slight
# showers, is numerically above 73, moderate snow), so severity is ranked
# explicitly, harshest last. A code missing from this table ranks as clear.
_CODE_SEVERITY_TIERS: tuple[tuple[int, ...], ...] = (
    (0,),
    (1, 2),
    (3,),
    (45, 48),
    (51, 53, 55),
    (61, 80),
    (63, 81, 56, 57),
    (65, 82, 66, 67),
    (71, 77, 85),
    (73,),
    (75, 86),
    (95, 96, 99),
)
_CODE_SEVERITY = {
    code: rank
    for rank, codes in enumerate(_CODE_SEVERITY_TIERS)
    for code in codes
}


def _most_severe_code(codes: list[Any]) -> int | None:
    numbers = [n for value in codes if (n := _number(value)) is not None]
    if not numbers:
        return None
    return int(max(numbers, key=lambda n: _CODE_SEVERITY.get(int(n), 0)))


def _worst(values: list[Any], pick: Any) -> float | None:
    numbers = [n for value in values if (n := _number(value)) is not None]
    return pick(numbers) if numbers else None


def _route_inference(
    windows: list[dict[str, Any]],
    window_hours: float,
    highest_point_m: float | None,
) -> dict[str, Any]:
    """
    What the forecast implies for the route, and how far to trust it.

    Compares the freezing level with the highest point and judges whether snow
    is likely underfoot. Everything here is read from a forecast at a handful
    of points; nothing is observed on the trail, and the basis says so.
    """

    def across(key: str, pick: Any) -> float | None:
        values = [w[key] for w in windows if w.get(key) is not None]
        return pick(values) if values else None

    freezing = across("min_freezing_level_m", min)
    depth = across("max_snow_depth_m", max)
    snowfall = across("snowfall_cm", max)
    recent = across("recent_snowfall_cm_72h", max)
    above = (
        highest_point_m is not None
        and freezing is not None
        and highest_point_m > freezing
    )

    reasons: list[str] = []
    if depth is not None and depth >= SNOW_LYING_LIKELY_M:
        reasons.append(f"about {depth * 100:.0f} cm of snow lying at the high points")
    if snowfall is not None and snowfall >= SNOW_IN_WINDOW_LIKELY_CM:
        reasons.append(f"{snowfall:.1f} cm of snow forecast during the walk")
    if above and recent is not None and recent >= RECENT_SNOW_LIKELY_CM:
        reasons.append(
            f"{recent:.1f} cm of snow in the last 3 days, and the freezing "
            f"level ({freezing:.0f} m) is below the highest point "
            f"({highest_point_m:.0f} m)"
        )

    return {
        "basis": (
            "Forecast read at points along the route. This is inferred from "
            "the forecast; conditions on the trail itself are not observed."
        ),
        "window_hours": window_hours,
        "window_basis": (
            "Estimated walking time (Naismith's rule: 5 km/h plus one hour "
            "per 600 m of ascent)"
        ),
        "highest_point_m": highest_point_m,
        "freezing_level_m": freezing,
        "upper_route_above_freezing_level": above,
        "margin_m": (
            round(highest_point_m - freezing, 0)
            if above
            else None
        ),
        "snow_on_route_likely": bool(reasons),
        "snow_reasons": reasons,
    }


def aggregate_route_weather(
    readings: list[tuple[dict[str, Any], dict[str, Any]]],
    window_hours: float | None = None,
    highest_point_m: float | None = None,
) -> dict[str, Any]:
    """
    Combine readings from several points into the worst case for the route.

    ``readings`` pairs each sample point with its normalised weather. The
    result has the same shape as single-point weather, so conditions and gear
    read it unchanged, but every value is the harshest across the points: the
    coldest temperature, the strongest wind, the most rain and snow, the most
    severe weather code. The individual readings are kept in ``samples``.
    """
    weathers = [weather for _, weather in readings]

    def current(key: str, pick: Any) -> float | None:
        return _worst([w["current"].get(key) for w in weathers], pick)

    def window(group: str, key: str) -> float | None:
        return _worst([(w.get(group) or {}).get(key) for w in weathers], max)

    code_value = _most_severe_code(
        [w["current"].get("weather_code") for w in weathers]
    )
    first = weathers[0]
    windows = [w["window"] for w in weathers if w.get("window")]
    if highest_point_m is None:
        highest_point_m = max(
            (point["elevation_m"] for point, _ in readings), default=None
        )

    def window_worst(key: str, pick: Any) -> float | None:
        values = [w[key] for w in windows if w.get(key) is not None]
        return pick(values) if values else None

    merged_window = (
        {
            "hours": window_hours,
            "min_temperature": window_worst("min_temperature", min),
            "max_wind_speed": window_worst("max_wind_speed", max),
            "max_wind_gust": window_worst("max_wind_gust", max),
            "max_precipitation_mm": window_worst("max_precipitation_mm", max),
            "snowfall_cm": window_worst("snowfall_cm", max),
            "max_snow_depth_m": window_worst("max_snow_depth_m", max),
            "min_freezing_level_m": window_worst("min_freezing_level_m", min),
            "recent_snowfall_cm_72h": window_worst("recent_snowfall_cm_72h", max),
        }
        if window_hours is not None and windows
        else None
    )
    result = {
        "source": "Open-Meteo",
        "latitude": first.get("latitude"),
        "longitude": first.get("longitude"),
        "timezone": first.get("timezone"),
        "current": {
            "time": first["current"].get("time"),
            "temperature": current("temperature", min),
            "humidity": current("humidity", max),
            "precipitation": current("precipitation", max),
            "rain": current("rain", max),
            "showers": current("showers", max),
            "snowfall": current("snowfall", max),
            "precipitation_probability": current(
                "precipitation_probability", max
            ),
            "wind_speed": current("wind_speed", max),
            "weather_code": code_value,
            "weather_condition": weather_code_description(code_value),
        },
        "recent_precipitation": {
            key: window("recent_precipitation", key)
            for key in ("24h_mm", "48h_mm", "72h_mm")
        },
        "recent_rain": {
            key: window("recent_rain", key)
            for key in ("24h_mm", "48h_mm", "72h_mm")
        },
        "forecast": {
            key: window("forecast", key)
            for key in (
                "precipitation_mm",
                "rain_mm",
                "precipitation_probability_max",
            )
        },
        "aggregation": "worst_case",
        "sample_count": len(readings),
        "samples": [
            {
                "labels": point["labels"],
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "elevation_m": point["elevation_m"],
                "temperature": reading["current"].get("temperature"),
                "wind_speed": reading["current"].get("wind_speed"),
                "snowfall": reading["current"].get("snowfall"),
                "precipitation": reading["current"].get("precipitation"),
                "weather_condition": reading["current"].get(
                    "weather_condition"
                ),
                "window_min_temperature": (reading.get("window") or {}).get(
                    "min_temperature"
                ),
                "freezing_level_m": (reading.get("window") or {}).get(
                    "min_freezing_level_m"
                ),
                "snow_depth_m": (reading.get("window") or {}).get(
                    "max_snow_depth_m"
                ),
            }
            for point, reading in readings
        ],
    }
    if merged_window is not None:
        result["window"] = merged_window
        result["inference"] = _route_inference(
            windows, window_hours, highest_point_m
        )
    return result


def _coordinate(value: float) -> str:
    """Five decimals (about a metre), never an exponent, no trailing zeros."""
    return f"{value:.5f}".rstrip("0").rstrip(".") or "0"


async def _fetch_route_weather_uncached(
    points: list[dict[str, Any]],
    window_hours: float | None = None,
) -> dict[str, Any]:
    params = {
        "latitude": ",".join(_coordinate(p["latitude"]) for p in points),
        "longitude": ",".join(_coordinate(p["longitude"]) for p in points),
        # Each location is read at its own height. Without this the provider
        # uses the height of its own terrain grid, which is wrong on a slope.
        "elevation": ",".join(f"{p['elevation_m']:.0f}" for p in points),
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "rain",
                "showers",
                "snowfall",
                "weather_code",
                "wind_speed_10m",
            ]
        ),
        "hourly": ",".join(
            [
                "precipitation_probability",
                "precipitation",
                "rain",
                "showers",
                *(WINDOW_HOURLY_VARIABLES if window_hours else []),
            ]
        ),
        "past_days": 3,
        # A window can run into tomorrow, so the forecast reaches two days.
        "forecast_days": 2 if window_hours else 1,
        "timezone": "auto",
    }
    # One breaker for the whole Open-Meteo host, shared with elevation.
    if not open_meteo_breaker.allow():
        raise HTTPException(
            status_code=502,
            detail="Weather service is temporarily unavailable",
        )
    try:
        transport = httpx.AsyncHTTPTransport(retries=1)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0, read=20.0, write=10.0, pool=10.0
            ),
            headers=WEATHER_HEADERS,
            transport=transport,
        ) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
            data = response.json()
        # One location comes back as an object, several as a list.
        items = data if isinstance(data, list) else [data]
        readings = [
            (point, normalize_weather_response(item, window_hours=window_hours))
            for point, item in zip(points, items)
            if isinstance(item, dict)
        ]
        if not readings:
            raise ValueError("Open-Meteo returned no usable locations")
        result = aggregate_route_weather(
            readings,
            window_hours=window_hours,
            highest_point_m=max(p["elevation_m"] for p in points),
        )
        open_meteo_breaker.record_success()
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        if is_provider_outage(exc):
            open_meteo_breaker.record_failure()
        else:
            # The host answered, even if not usefully.
            open_meteo_breaker.record_success()
        raise HTTPException(
            status_code=502,
            detail="Weather service is temporarily unavailable",
        ) from exc


async def get_route_weather(
    points: list[dict[str, Any]],
    window_hours: float | None = None,
) -> dict[str, Any]:
    """
    Weather for a route: one provider call, worst case across ``points``.

    Cached and de-duplicated like single-point weather, keyed by every point
    and its elevation.
    """
    key = tuple(
        (round(p["latitude"], 4), round(p["longitude"], 4), round(p["elevation_m"]))
        for p in points
    ) + ((round(window_hours, 1),) if window_hours is not None else ())
    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _WEATHER_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(_fetch_route_weather_uncached(points, window_hours))
        _WEATHER_INFLIGHT[key] = task
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _WEATHER_INFLIGHT.get(key) is task:
            _WEATHER_INFLIGHT.pop(key, None)

    _cache_set(key, result)
    return result


async def get_weather(
    latitude: float,
    longitude: float,
    window_hours: float | None = None,
) -> dict[str, Any]:
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid weather coordinates",
        )

    key = _cache_key(latitude, longitude)
    if window_hours is not None:
        # A different window reads different hours, so it is a different entry.
        key = (*key, round(window_hours, 1))
    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _WEATHER_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _fetch_weather_uncached(latitude, longitude, window_hours)
        )
        _WEATHER_INFLIGHT[key] = task
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _WEATHER_INFLIGHT.get(key) is task:
            _WEATHER_INFLIGHT.pop(key, None)

    _cache_set(key, result)
    return result
