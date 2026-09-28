from __future__ import annotations

import asyncio
import math
import os
import time
from collections import OrderedDict
from typing import Any

import httpx
from fastapi import HTTPException


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

_WEATHER_CACHE: OrderedDict[
    tuple[float, float],
    tuple[float, dict[str, Any]],
] = OrderedDict()
_WEATHER_INFLIGHT: dict[
    tuple[float, float],
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


def normalize_weather_response(
    data: dict[str, Any],
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

    return {
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


def _cache_key(
    latitude: float,
    longitude: float,
) -> tuple[float, float]:
    return round(latitude, 4), round(longitude, 4)


def _cache_get(
    key: tuple[float, float],
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
    key: tuple[float, float],
    value: dict[str, Any],
) -> None:
    _WEATHER_CACHE[key] = (time.monotonic(), value)
    _WEATHER_CACHE.move_to_end(key)
    while len(_WEATHER_CACHE) > WEATHER_CACHE_MAX_ENTRIES:
        _WEATHER_CACHE.popitem(last=False)


async def _fetch_weather_uncached(
    latitude: float,
    longitude: float,
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
            ]
        ),
        "past_days": 3,
        "forecast_days": 1,
        "timezone": "auto",
    }
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
        return normalize_weather_response(data)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Weather service is temporarily unavailable",
        ) from exc


async def get_weather(
    latitude: float,
    longitude: float,
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
    cached = _cache_get(key)
    if cached is not None:
        return cached

    task = _WEATHER_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(
            _fetch_weather_uncached(latitude, longitude)
        )
        _WEATHER_INFLIGHT[key] = task
    try:
        result = await asyncio.shield(task)
    finally:
        if task.done() and _WEATHER_INFLIGHT.get(key) is task:
            _WEATHER_INFLIGHT.pop(key, None)

    _cache_set(key, result)
    return result
