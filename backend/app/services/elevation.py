import math
from typing import Any

import httpx
from fastapi import HTTPException


OPEN_METEO_ELEVATION_URL = (
    "https://api.open-meteo.com/v1/elevation"
)

ELEVATION_HEADERS = {
    "User-Agent": (
        "TerraPath/0.1 "
        "(educational outdoor intelligence project)"
    ),
}


# ============================================================
# CONFIGURATION
# ============================================================

MAX_PROFILE_POINTS = 100


# ============================================================
# COORDINATE HELPERS
# ============================================================

def clean_coordinate(
    coordinate: list[float],
) -> list[float] | None:

    if len(coordinate) < 2:
        return None

    try:
        longitude = float(coordinate[0])
        latitude = float(coordinate[1])
    except (
        TypeError,
        ValueError,
    ):
        return None

    if not (
        math.isfinite(longitude)
        and math.isfinite(latitude)
    ):
        return None

    return [
        longitude,
        latitude,
    ]


# ============================================================
# GEOGRAPHIC DISTANCE
# ============================================================

def haversine_distance_km(
    first: list[float],
    second: list[float],
) -> float:

    longitude_1, latitude_1 = first
    longitude_2, latitude_2 = second

    earth_radius_km = 6371.0

    phi_1 = math.radians(
        latitude_1
    )

    phi_2 = math.radians(
        latitude_2
    )

    delta_phi = math.radians(
        latitude_2 - latitude_1
    )

    delta_lambda = math.radians(
        longitude_2 - longitude_1
    )

    a = (
        math.sin(
            delta_phi / 2
        ) ** 2
        +
        math.cos(phi_1)
        *
        math.cos(phi_2)
        *
        math.sin(
            delta_lambda / 2
        ) ** 2
    )

    return (
        earth_radius_km
        *
        2
        *
        math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a),
        )
    )


# ============================================================
# GEOMETRY FLATTENING
# ============================================================

def flatten_geometry(
    geometry: dict[str, Any],
) -> list[list[float]]:

    geometry_type = geometry.get(
        "type"
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if not coordinates:
        return []

    if geometry_type == "LineString":

        result: list[list[float]] = []

        for coordinate in coordinates:

            cleaned = clean_coordinate(
                coordinate
            )

            if cleaned is not None:
                result.append(
                    cleaned
                )

        return result


    if geometry_type == "MultiLineString":

        result = []

        for segment in coordinates:

            for coordinate in segment:

                cleaned = clean_coordinate(
                    coordinate
                )

                if cleaned is not None:
                    result.append(
                        cleaned
                    )

        return result


    return []


# ============================================================
# REMOVE CONSECUTIVE DUPLICATES
# ============================================================

def remove_consecutive_duplicates(
    coordinates: list[list[float]],
) -> list[list[float]]:

    if not coordinates:
        return []

    result = [
        coordinates[0]
    ]

    for coordinate in coordinates[1:]:

        if coordinate != result[-1]:
            result.append(
                coordinate
            )

    return result


# ============================================================
# BUILD CUMULATIVE DISTANCE
# ============================================================

def build_cumulative_distances(
    coordinates: list[list[float]],
) -> list[float]:

    if not coordinates:
        return []

    distances = [
        0.0
    ]

    cumulative_distance = 0.0

    for index in range(
        1,
        len(coordinates),
    ):

        segment_distance_km = (
            haversine_distance_km(
                coordinates[index - 1],
                coordinates[index],
            )
        )

        cumulative_distance += (
            segment_distance_km
        )

        distances.append(
            cumulative_distance
        )

    return distances


# ============================================================
# INTERPOLATE COORDINATE
# ============================================================

def interpolate_coordinate(
    first: list[float],
    second: list[float],
    ratio: float,
) -> list[float]:

    ratio = max(
        0.0,
        min(
            1.0,
            ratio,
        ),
    )

    longitude = (
        first[0]
        +
        (
            second[0]
            - first[0]
        )
        * ratio
    )

    latitude = (
        first[1]
        +
        (
            second[1]
            - first[1]
        )
        * ratio
    )

    return [
        longitude,
        latitude,
    ]


# ============================================================
# DISTANCE-BASED SAMPLING
# ============================================================

def sample_coordinates(
    coordinates: list[list[float]],
    max_points: int = MAX_PROFILE_POINTS,
) -> list[list[float]]:

    coordinates = remove_consecutive_duplicates(
        coordinates
    )

    if not coordinates:
        return []

    if len(coordinates) == 1:
        return [
            coordinates[0]
        ]

    cumulative_distances = (
        build_cumulative_distances(
            coordinates
        )
    )

    total_distance_km = (
        cumulative_distances[-1]
    )

    if (
        total_distance_km <= 0
    ):
        return [
            coordinates[0]
        ]

    target_points = min(
        max_points,
        len(coordinates),
    )

    if target_points < 2:
        return [
            coordinates[0]
        ]

    # Evenly distribute samples by route distance.
    target_distances = [
        (
            index
            / (target_points - 1)
        )
        *
        total_distance_km
        for index in range(
            target_points
        )
    ]

    sampled: list[list[float]] = []

    segment_index = 0

    for target_distance in target_distances:

        while (
            segment_index
            <
            len(cumulative_distances) - 2
            and
            cumulative_distances[
                segment_index + 1
            ]
            <
            target_distance
        ):

            segment_index += 1

        segment_start_distance = (
            cumulative_distances[
                segment_index
            ]
        )

        segment_end_distance = (
            cumulative_distances[
                segment_index + 1
            ]
        )

        segment_start_coordinate = (
            coordinates[
                segment_index
            ]
        )

        segment_end_coordinate = (
            coordinates[
                segment_index + 1
            ]
        )

        segment_length = (
            segment_end_distance
            -
            segment_start_distance
        )

        if segment_length <= 0:

            sampled.append(
                segment_start_coordinate
            )

            continue

        ratio = (
            target_distance
            -
            segment_start_distance
        ) / segment_length

        sampled.append(
            interpolate_coordinate(
                segment_start_coordinate,
                segment_end_coordinate,
                ratio,
            )
        )

    return remove_consecutive_duplicates(
        sampled
    )


# ============================================================
# ELEVATION PROFILE
# ============================================================

def build_elevation_profile(
    coordinates: list[list[float]],
    elevations: list[float | None],
) -> list[dict[str, float]]:

    if not coordinates:
        return []

    profile: list[dict[str, float]] = []

    cumulative_distance_km = 0.0

    for index, coordinate in enumerate(
        coordinates
    ):

        elevation = (
            elevations[index]
            if index < len(elevations)
            else None
        )

        if elevation is None:
            continue

        if index > 0:

            previous_coordinate = (
                coordinates[index - 1]
            )

            cumulative_distance_km += (
                haversine_distance_km(
                    previous_coordinate,
                    coordinate,
                )
            )

        profile.append(
            {
                "distance_km": round(
                    cumulative_distance_km,
                    3,
                ),
                "elevation_m": round(
                    float(elevation),
                    1,
                ),
            }
        )

    return profile


# ============================================================
# TERRAIN METRICS
# ============================================================

def smooth_elevations(
    elevations: list[float],
    window_size: int = 5,
) -> list[float]:

    if len(elevations) < 3:
        return elevations

    half_window = window_size // 2

    smoothed: list[float] = []

    for index in range(
        len(elevations)
    ):

        start = max(
            0,
            index - half_window,
        )

        end = min(
            len(elevations),
            index + half_window + 1,
        )

        window = elevations[
            start:end
        ]

        smoothed.append(
            sum(window) / len(window)
        )

    return smoothed


def calculate_terrain_metrics(
    profile: list[dict[str, float]],
) -> dict[str, float | None]:

    if not profile:
        return {
            "min_elevation_m": None,
            "max_elevation_m": None,
            "elevation_gain_m": None,
            "elevation_loss_m": None,
            "average_slope_percent": None,
            "maximum_slope_percent": None,
        }


    elevations = [
        point["elevation_m"]
        for point in profile
    ]


    minimum_elevation = min(
        elevations
    )

    maximum_elevation = max(
        elevations
    )


    # --------------------------------------------------------
    # Gain / loss
    #
    # Keep these based on the actual returned elevation
    # profile.
    # --------------------------------------------------------

    elevation_gain = 0.0

    elevation_loss = 0.0


    for index in range(
        1,
        len(elevations),
    ):

        change = (
            elevations[index]
            -
            elevations[index - 1]
        )

        if change > 0:

            elevation_gain += change

        elif change < 0:

            elevation_loss += abs(
                change
            )


    # --------------------------------------------------------
    # Smooth only for slope calculation.
    #
    # DEM elevation can contain local fluctuations.
    # We don't change the reported min/max/gain/loss.
    # --------------------------------------------------------

    smoothed_elevations = (
        smooth_elevations(
            elevations,
            window_size=5,
        )
    )


    # --------------------------------------------------------
    # Calculate slope over a larger distance window.
    #
    # 150 m prevents extremely short segments from producing
    # unrealistic instantaneous grades.
    # --------------------------------------------------------

    slope_window_m = 150.0

    slopes: list[float] = []

    slope_distances_m: list[float] = []


    for start_index in range(
        len(profile) - 1
    ):

        start_distance_km = (
            profile[start_index][
                "distance_km"
            ]
        )


        target_distance_km = (
            start_distance_km
            +
            slope_window_m / 1000.0
        )


        end_index = (
            start_index + 1
        )


        while (
            end_index < len(profile)
            and
            profile[end_index][
                "distance_km"
            ]
            <
            target_distance_km
        ):

            end_index += 1


        if (
            end_index >= len(profile)
        ):

            break


        horizontal_distance_km = (
            profile[end_index][
                "distance_km"
            ]
            -
            profile[start_index][
                "distance_km"
            ]
        )


        if horizontal_distance_km <= 0:
            continue


        horizontal_distance_m = (
            horizontal_distance_km
            * 1000.0
        )


        elevation_change = (
            smoothed_elevations[end_index]
            -
            smoothed_elevations[start_index]
        )


        slope_percent = (
            abs(elevation_change)
            /
            horizontal_distance_m
            *
            100.0
        )


        slopes.append(
            slope_percent
        )

        slope_distances_m.append(
            horizontal_distance_m
        )


    if slopes:

        total_slope_distance = (
            sum(
                slope_distances_m
            )
        )


        if total_slope_distance > 0:

            average_slope = (
                sum(
                    slope * distance
                    for slope, distance
                    in zip(
                        slopes,
                        slope_distances_m,
                    )
                )
                /
                total_slope_distance
            )

        else:

            average_slope = None


        maximum_slope = max(
            slopes
        )

    else:

        average_slope = None

        maximum_slope = None


    return {
        "min_elevation_m": round(
            minimum_elevation,
            1,
        ),

        "max_elevation_m": round(
            maximum_elevation,
            1,
        ),

        "elevation_gain_m": round(
            elevation_gain,
            1,
        ),

        "elevation_loss_m": round(
            elevation_loss,
            1,
        ),

        "average_slope_percent": (
            round(
                average_slope,
                2,
            )
            if average_slope is not None
            else None
        ),

        "maximum_slope_percent": (
            round(
                maximum_slope,
                2,
            )
            if maximum_slope is not None
            else None
        ),
    }


# ============================================================
# OPEN-METEO ELEVATION REQUEST
# ============================================================

async def get_elevation_profile(
    coordinates: list[list[float]],
) -> dict[str, Any]:

    cleaned_coordinates = [
        coordinate
        for coordinate in (
            clean_coordinate(
                value
            )
            for value in coordinates
        )
        if coordinate is not None
    ]


    cleaned_coordinates = (
        remove_consecutive_duplicates(
            cleaned_coordinates
        )
    )


    if not cleaned_coordinates:

        raise HTTPException(
            status_code=400,
            detail=(
                "No valid trail coordinates "
                "were provided."
            ),
        )


    if len(cleaned_coordinates) < 2:

        raise HTTPException(
            status_code=400,
            detail=(
                "At least two valid trail "
                "coordinates are required."
            ),
        )


    # --------------------------------------------------------
    # Distance-based sampling
    # --------------------------------------------------------

    sampled_coordinates = (
        sample_coordinates(
            cleaned_coordinates,
            MAX_PROFILE_POINTS,
        )
    )


    if len(sampled_coordinates) < 2:

        raise HTTPException(
            status_code=400,
            detail=(
                "Trail geometry does not contain "
                "enough usable distance."
            ),
        )


    # --------------------------------------------------------
    # Provider coordinates
    #
    # Open-Meteo expects:
    #
    # latitude
    # longitude
    # --------------------------------------------------------

    latitudes = ",".join(
        str(
            coordinate[1]
        )
        for coordinate in sampled_coordinates
    )


    longitudes = ",".join(
        str(
            coordinate[0]
        )
        for coordinate in sampled_coordinates
    )


    params = {
        "latitude": latitudes,
        "longitude": longitudes,
    }


    # --------------------------------------------------------
    # External request
    # --------------------------------------------------------

    try:

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=30.0,
                write=10.0,
                pool=10.0,
            ),
            headers=ELEVATION_HEADERS,
        ) as client:

            response = await client.get(
                OPEN_METEO_ELEVATION_URL,
                params=params,
            )

            response.raise_for_status()

            data = response.json()

    except httpx.HTTPError as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Elevation service request failed: "
                f"{exc}"
            ),
        ) from exc

    except Exception as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Elevation service failed: "
                f"{exc}"
            ),
        ) from exc


    # --------------------------------------------------------
    # Provider response
    # --------------------------------------------------------

    provider_elevations = (
        data.get("elevation") or []
    )


    if not provider_elevations:

        raise HTTPException(
            status_code=502,
            detail=(
                "Elevation service returned "
                "no elevation values."
            ),
        )


    elevation_values: list[
        float | None
    ] = []


    for value in provider_elevations:

        try:

            elevation_values.append(
                float(value)
            )

        except (
            TypeError,
            ValueError,
        ):

            elevation_values.append(
                None
            )


    usable_count = min(
        len(sampled_coordinates),
        len(elevation_values),
    )


    profile_coordinates = (
        sampled_coordinates[
            :usable_count
        ]
    )


    profile_elevations = (
        elevation_values[
            :usable_count
        ]
    )


    # --------------------------------------------------------
    # Build profile
    # --------------------------------------------------------

    profile = build_elevation_profile(
        profile_coordinates,
        profile_elevations,
    )


    # --------------------------------------------------------
    # Calculate terrain features
    # --------------------------------------------------------

    metrics = calculate_terrain_metrics(
        profile
    )


    # --------------------------------------------------------
    # Normalized project response
    # --------------------------------------------------------

    return {
        "source": "Open-Meteo",

        "sampled_points": len(
            profile
        ),

        "profile": profile,

        "metrics": metrics,
    }