import httpx
from fastapi import HTTPException


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_HEADERS = {
    "User-Agent": (
        "TerraPath/0.1 "
        "(educational outdoor intelligence project)"
    ),
}


# ============================================================
# WEATHER CODE
# ============================================================

def weather_code_description(
    weather_code: int | None,
) -> str:

    if weather_code is None:
        return "Unknown"

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

    return descriptions.get(
        weather_code,
        "Unknown",
    )


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_weather_response(
    data: dict,
) -> dict:

    current = data.get("current") or {}
    hourly = data.get("hourly") or {}

    weather_code = current.get(
        "weather_code"
    )

    current_time = current.get("time")

    # --------------------------------------------------------
    # Current conditions
    # --------------------------------------------------------

    temperature = current.get(
        "temperature_2m"
    )

    humidity = current.get(
        "relative_humidity_2m"
    )

    precipitation = current.get(
        "precipitation"
    )

    wind_speed = current.get(
        "wind_speed_10m"
    )

    rain = current.get(
        "rain"
    )

    showers = current.get(
        "showers"
    )

    snowfall = current.get(
        "snowfall"
    )

    # --------------------------------------------------------
    # Current-hour precipitation probability
    # --------------------------------------------------------

    precipitation_probability = None

    hourly_times = hourly.get("time") or []

    probability_values = (
        hourly.get(
            "precipitation_probability"
        )
        or []
    )

    if hourly_times and probability_values:
        if current_time in hourly_times:
            index = hourly_times.index(current_time)

        else:
            # Fall back to the first available hour
            # when the provider's current timestamp
            # does not exactly match the hourly timestamp.
            index = 0

        if index < len(probability_values):
            precipitation_probability = (
                probability_values[index]
            )

    return {
        "source": "Open-Meteo",

        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),

        "timezone": data.get("timezone"),

        "current": {
            "time": current_time,

            "temperature": temperature,

            "humidity": humidity,

            "precipitation": precipitation,

            "rain": rain,

            "showers": showers,

            "snowfall": snowfall,

            "precipitation_probability": (
                precipitation_probability
            ),

            "wind_speed": wind_speed,

            "weather_code": weather_code,

            "weather_condition": (
                weather_code_description(
                    weather_code
                )
            ),
        },
    }


# ============================================================
# FETCH WEATHER
# ============================================================

async def get_weather(
    latitude: float,
    longitude: float,
) -> dict:

    params = {
        "latitude": latitude,
        "longitude": longitude,

        # Current conditions
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

        # Needed for rain probability
        "hourly": "precipitation_probability",

        # We want local time for the selected location.
        "timezone": "auto",

        # Small MVP forecast window.
        "forecast_days": 1,
    }

    try:

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=20.0,
                write=10.0,
                pool=10.0,
            ),
            headers=WEATHER_HEADERS,
        ) as client:

            response = await client.get(
                OPEN_METEO_URL,
                params=params,
            )

            response.raise_for_status()

            data = response.json()

    except httpx.HTTPError as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Weather service request failed: "
                f"{exc}"
            ),
        ) from exc

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Weather service failed: "
                f"{exc}"
            ),
        ) from exc

    return normalize_weather_response(
        data
    )