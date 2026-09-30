from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


# Surfaces that hold water and become difficult when wet.
VULNERABLE_SURFACES = {
    "earth",
    "soil",
    "mud",
    "grass",
    "sand",
    "dirt",
    "ground",
    "gravel",
    "rock",
    "wood",
}
# Surfaces that drain quickly and stay comparatively firm in rain.
DRAINING_SURFACES = {
    "asphalt",
    "paved",
    "concrete",
    "cement",
    "paving_stones",
}

# Open-Meteo WMO weather interpretation codes grouped by trail impact.
WMO_RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
WMO_SNOW_CODES = {71, 73, 75, 77, 85, 86}
WMO_STORM_CODES = {95, 96, 99}
WMO_FOG_CODES = {45, 48}


# How much a rain reading counts. Each fact is scored once, by how much there
# is, so a drizzle is not an adverse walk. The cut-offs for "adverse" (55) and
# "caution" (25) are below in condition_likelihood; these set how much each
# kind of rain contributes.
#
# Rain rates follow the usual meteorological bands: light under 2.5 mm/h,
# moderate to 7.6 mm/h, heavy above.
RAIN_RATE_MODERATE_MM_H = 2.5
RAIN_RATE_HEAVY_MM_H = 7.6
RAIN_NOW_POINTS = {"light": 4, "moderate": 10, "heavy": 24}
# What the WMO weather code says about intensity. Freezing rain and drizzle
# (56, 57, 66, 67) make ground slippery whatever the amount.
RAIN_CODE_INTENSITY = {
    51: "light", 53: "light", 55: "light",
    61: "light", 80: "light",
    56: "moderate", 57: "moderate", 66: "moderate", 67: "moderate",
    63: "moderate", 81: "moderate",
    65: "heavy", 82: "heavy",
}
# Rain in the last 24 h: under 1 mm is a trace, not wet ground.
RECENT_RAIN_TRACE_MM = 1.0
# Rain expected in the next 24 h counts from 1 mm; a chance of rain only adds
# to a real amount (a 100% chance of a trace is not a hazard).
FORECAST_RAIN_MIN_MM = 1.0
FORECAST_RAIN_HEAVY_MM = 5.0
FORECAST_CHANCE_PERCENT = 70.0
# A natural surface is penalised only when it is actually wet.
SURFACE_WET_MM = 5.0


def _rain_level(rate_mm_h: float | None) -> str | None:
    if rate_mm_h is None or rate_mm_h <= 0.0:
        return None
    if rate_mm_h >= RAIN_RATE_HEAVY_MM_H:
        return "heavy"
    if rate_mm_h >= RAIN_RATE_MODERATE_MM_H:
        return "moderate"
    return "light"


def _worse_level(first: str | None, second: str | None) -> str | None:
    order = {None: 0, "light": 1, "moderate": 2, "heavy": 3}
    return first if order[first] >= order[second] else second


def condition_likelihood(
    trail: dict[str, Any],
    elevation: dict[str, Any] | None,
    weather: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Evidence-based assessment of route conditions for the walk.

    When the weather carries an estimated walking window, cold, wind and snow
    are judged on the worst of that window and every factor names its source;
    otherwise this is the current reading, as before.

    This is an INFERENCE from observed and forecast weather combined with
    the route's own recorded attributes. It is never a physical observation
    of the trail surface, and it never states that a trail is safe or
    dangerous - only what the available evidence does and does not support.
    """
    if weather is None:
        return {
            "available": False,
            "status": "unknown",
            "likelihood": "unknown",
            "score": None,
            "observation_type": "inference",
            "summary": (
                "Current route conditions cannot be assessed because live "
                "weather is unavailable. This is not a statement that "
                "conditions are good."
            ),
            "factors": [],
            "evidence": [],
            "missing_evidence": ["live_weather"],
            "source": None,
            "observed_at": None,
            "assessed_over": "now",
        }

    recent_rain = weather.get("recent_rain") or {}
    recent_precipitation = weather.get("recent_precipitation") or {}
    current = weather.get("current") or {}
    forecast = weather.get("forecast") or {}
    metrics = (elevation or {}).get("metrics") or {}

    rain_24h = _number(recent_rain.get("24h_mm"))
    precip_24h = _number(recent_precipitation.get("24h_mm"))
    rain_72h = _number(recent_rain.get("72h_mm"))
    precip_72h = _number(recent_precipitation.get("72h_mm"))
    current_precip = _number(current.get("precipitation"))
    snowfall = _number(current.get("snowfall"))
    temperature = _number(current.get("temperature"))
    wind = _number(current.get("wind_speed"))
    forecast_rain = _number(forecast.get("rain_mm"))
    forecast_prob = _number(
        forecast.get("precipitation_probability_max")
    )
    weather_code = _number(current.get("weather_code"))
    max_slope = _number(metrics.get("max_slope_percent"))
    elevation_gain = _number(metrics.get("elevation_gain_m"))
    surface = _text(trail.get("surface"))

    # The walk takes time: cold and wind are judged on the worst of the
    # estimated walking window when there is one, and say so. Heat and rain
    # stay as they were. With no window the labels below are the old wording.
    window = weather.get("window") or {}
    inference = weather.get("inference") or {}
    window_hours = _number(window.get("hours"))
    over = f" over the next {window_hours:.0f} h" if window_hours else ""
    cold_temperature = temperature
    cold_label = "Current temperature"
    window_cold = _number(window.get("min_temperature"))
    if window_cold is not None and (
        cold_temperature is None or window_cold < cold_temperature
    ):
        cold_temperature = window_cold
        cold_label = f"Coldest forecast temperature{over}"
    wind_label = "Current wind speed"
    window_wind = _number(window.get("max_wind_speed"))
    if window_wind is not None and (wind is None or window_wind > wind):
        wind = window_wind
        wind_label = f"Strongest forecast wind{over}"

    score = 0
    evidence: list[str] = []
    missing: list[str] = []
    factors: list[dict[str, Any]] = []

    def add_factor(
        name: str,
        state: str,
        detail: str,
        weight: int,
    ) -> None:
        factors.append(
            {
                "factor": name,
                "state": state,
                "detail": detail,
                "weight": weight,
            }
        )

    # ---------------- WETNESS ----------------
    recent_value = rain_24h if rain_24h is not None else precip_24h
    if recent_value is None:
        missing.append("recent_24h_rainfall")
    elif recent_value >= 20.0:
        score += 34
        add_factor(
            "wetness",
            "saturated",
            f"{recent_value:.1f} mm of rain fell in the last 24 h",
            34,
        )
        evidence.append(f"{recent_value:.1f} mm rain/precipitation in 24 h")
    elif recent_value >= 5.0:
        score += 22
        add_factor(
            "wetness",
            "wet",
            f"{recent_value:.1f} mm of rain fell in the last 24 h",
            22,
        )
        evidence.append(f"{recent_value:.1f} mm rain/precipitation in 24 h")
    elif recent_value >= RECENT_RAIN_TRACE_MM:
        score += 10
        add_factor(
            "wetness",
            "damp",
            f"{recent_value:.1f} mm of rain fell in the last 24 h",
            10,
        )
    elif recent_value > 0.0:
        score += 3
        add_factor(
            "wetness",
            "damp",
            f"{recent_value:.1f} mm of rain fell in the last 24 h (a trace)",
            3,
        )
    else:
        add_factor(
            "wetness",
            "dry",
            "No rain was reported in the last 24 h",
            0,
        )

    value_72h = rain_72h if rain_72h is not None else precip_72h
    if value_72h is None:
        missing.append("recent_72h_rainfall")
    elif value_72h >= 50.0:
        score += 14
    elif value_72h >= 20.0:
        score += 8

    if current_precip is None:
        missing.append("current_precipitation")

    # Raining now is one fact, scored once by intensity: the worse of what is
    # measured (the current 15-minute step read as a rate, and the heaviest
    # hour of the walk) and what the weather code reports.
    interval_s = _number(current.get("precipitation_interval_s")) or 3600.0
    measured_rate = (
        current_precip * 3600.0 / interval_s
        if current_precip is not None
        else None
    )
    window_peak = _number(window.get("max_precipitation_mm"))
    if window_peak is not None and (
        measured_rate is None or window_peak > measured_rate
    ):
        measured_rate = window_peak
    rain_level = _worse_level(
        _rain_level(measured_rate),
        RAIN_CODE_INTENSITY.get(int(weather_code))
        if weather_code is not None
        else None,
    )
    if rain_level is not None:
        points = RAIN_NOW_POINTS[rain_level]
        score += points
        rate_text = (
            f" ({measured_rate:.1f} mm/h)"
            if measured_rate is not None and measured_rate > 0.0
            else ""
        )
        add_factor(
            "precipitation",
            "active",
            f"{rain_level.capitalize()} rain{rate_text} at the weather point"
            + (" during the walk" if window_peak else " now"),
            points,
        )

    if weather_code is not None and weather_code in WMO_FOG_CODES:
        score += 6
        add_factor(
            "visibility",
            "fog",
            f"Weather code {int(weather_code)} reports fog",
            6,
        )

    # Rain expected is scored by amount. The chance of rain only adds to a
    # real amount; a high chance of a trace is not a hazard.
    if forecast_rain is None:
        missing.append("forecast_rain")
    elif forecast_rain >= FORECAST_RAIN_MIN_MM:
        heavy = forecast_rain >= FORECAST_RAIN_HEAVY_MM
        points = 14 if heavy else 6
        likely = (
            forecast_prob is not None
            and forecast_prob >= FORECAST_CHANCE_PERCENT
        )
        if likely:
            points += 4 if heavy else 3
        score += points
        add_factor(
            "forecast",
            "likely_rain" if likely else "rain",
            f"{forecast_rain:.1f} mm of rain forecast in the next 24 h"
            + (f" ({forecast_prob:.0f}% chance)" if likely else ""),
            points,
        )

    # ---------------- SNOW, STORM AND COLD ----------------
    snow_now = snowfall if snowfall is not None else 0.0
    if snow_now > 0.0 or (
        weather_code is not None and weather_code in WMO_SNOW_CODES
    ):
        score += 30
        add_factor(
            "snow",
            "snow",
            (
                f"{snow_now:.1f} cm of snow is falling now"
                if snow_now > 0.0
                else f"Weather code {int(weather_code)} reports snow"
            ),
            30,
        )
    if weather_code is not None and weather_code in WMO_STORM_CODES:
        # A thunderstorm is adverse on its own (the adverse cut-off is 55). It
        # used to get there only with the surface's fixed +8.
        score += 55
        add_factor(
            "storm",
            "thunderstorm",
            f"Weather code {int(weather_code)} reports a thunderstorm",
            55,
        )

    if cold_temperature is None:
        missing.append("temperature")
    elif cold_temperature <= 0.0:
        score += 26
        add_factor(
            "cold",
            "freezing",
            f"{cold_label} is {cold_temperature:.1f} °C",
            26,
        )
    elif cold_temperature <= 5.0:
        score += 16
        add_factor(
            "cold",
            "very_cold",
            f"{cold_label} is {cold_temperature:.1f} °C",
            16,
        )
    elif cold_temperature <= 10.0:
        score += 6
        add_factor(
            "cold",
            "cold",
            f"{cold_label} is {cold_temperature:.1f} °C",
            6,
        )

    # ---------------- WIND ----------------
    if wind is None:
        missing.append("wind_speed")
    elif wind >= 60.0:
        score += 28
        add_factor(
            "wind",
            "storm_force",
            f"{wind_label} is {wind:.1f} km/h",
            28,
        )
    elif wind >= 40.0:
        # A strong wind is a caution on its own. It used to reach caution only
        # because every natural surface carried a fixed +8; with that gone the
        # wind has to carry the weight itself.
        score += 25
        add_factor(
            "wind",
            "strong",
            f"{wind_label} is {wind:.1f} km/h",
            25,
        )
    elif wind >= 25.0:
        score += 9
        add_factor(
            "wind",
            "breezy",
            f"{wind_label} is {wind:.1f} km/h",
            9,
        )

    # ---------------- ROUTE EXPOSURE ----------------
    if not surface:
        missing.append("surface")
    elif surface in VULNERABLE_SURFACES:
        wet_surface = (
            (recent_value is not None and recent_value >= SURFACE_WET_MM)
            or (
                forecast_rain is not None
                and forecast_rain >= SURFACE_WET_MM
            )
            or rain_level in {"moderate", "heavy"}
        )
        if wet_surface:
            score += 8
            add_factor(
                "surface",
                "water_retaining",
                f"Recorded trail surface is {surface}, which holds water",
                8,
            )
        else:
            add_factor(
                "surface",
                "natural",
                f"Recorded trail surface is {surface}; it holds water when "
                f"wet, and it is not wet now",
                0,
            )
    elif surface in DRAINING_SURFACES:
        add_factor(
            "surface",
            "draining",
            f"Recorded trail surface is {surface}",
            0,
        )
        evidence.append(f"Surface is recorded as {surface}")
    else:
        add_factor(
            "surface",
            "natural",
            f"Recorded trail surface is {surface}",
            0,
        )

    if max_slope is None:
        missing.append("terrain_slope")
    elif max_slope >= 35.0:
        score += 8
        add_factor(
            "terrain",
            "steep",
            f"Sampled sections reach {max_slope:.1f}% slope",
            8,
        )
    elif max_slope >= 25.0:
        score += 4
        add_factor(
            "terrain",
            "moderately_steep",
            f"Sampled sections reach {max_slope:.1f}% slope",
            4,
        )

    if (
        elevation_gain is not None
        and elevation_gain >= 600.0
        and cold_temperature is not None
        and cold_temperature <= 8.0
    ):
        score += 10
        add_factor(
            "exposure",
            "high_cold_exposed",
            (
                f"Route climbs {elevation_gain:.0f} m while the "
                f"{cold_label[0].lower()}{cold_label[1:]} is "
                f"{cold_temperature:.1f} °C"
            ),
            10,
        )

    # Snow expected on the route, inferred from the forecast. It is a factor of
    # its own, not "snow": snow falling now is reported above and counted once.
    snow_falling_now = (snowfall is not None and snowfall > 0.0) or any(
        f["factor"] == "snow" for f in factors
    )
    if inference.get("snow_on_route_likely") and not snow_falling_now:
        reasons = "; ".join(str(r) for r in inference.get("snow_reasons") or [])
        score += 22
        add_factor(
            "snow_forecast",
            "snow",
            "Snow is likely on the upper route, inferred from the forecast"
            + (f": {reasons}" if reasons else ""),
            22,
        )

    score = max(0, min(score, 100))

    if len(missing) > 3:
        status = "unknown"
    elif score >= 55:
        status = "adverse"
    elif score >= 25:
        status = "caution"
    else:
        status = "favorable"

    walk = bool(window_hours)
    summary_by_status = {
        "adverse": (
            "Observed and forecast weather indicates this route may be "
            + (
                "unsuitable during the estimated walk."
                if walk
                else "unsuitable under current conditions."
            )
        ),
        "caution": (
            "Some forecast weather conditions during the walk may make parts "
            "of this route difficult."
            if walk
            else "Some current weather conditions may make parts of this "
            "route difficult."
        ),
        "favorable": (
            "No adverse weather signal is present in the available data for "
            "this route over the estimated walk."
            if walk
            else "No adverse weather signal is present in the available data "
            "for this route right now."
        ),
        "unknown": (
            "Not enough verified weather evidence is available to assess this "
            "route's current conditions."
        ),
    }

    return {
        "available": True,
        "status": status,
        "likelihood": status,
        "score": score,
        "observation_type": "inference",
        "summary": summary_by_status[status],
        "factors": factors,
        "evidence": evidence,
        "missing_evidence": missing,
        "source": weather.get("source") or "Open-Meteo",
        "observed_at": (weather.get("current") or {}).get("time"),
        # What the assessment covers, so wording downstream can follow it.
        "assessed_over": "walk" if walk else "now",
    }


def route_suitability_context(
    trail: dict[str, Any],
    analysis: dict[str, Any],
    elevation: dict[str, Any] | None,
    condition: dict[str, Any],
) -> dict[str, Any]:
    distance = (
        _number(analysis.get("distance_km"))
        or _number(trail.get("length_km"))
        or 0.0
    )
    metrics = (elevation or {}).get("metrics") or {}
    gain = _number(metrics.get("elevation_gain_m"))
    max_slope = _number(metrics.get("max_slope_percent"))
    source_difficulty = _text(trail.get("source_difficulty"))
    ml_difficulty = _text(
        (trail.get("difficulty_estimate") or {}).get("estimate")
    )
    difficulty = source_difficulty or ml_difficulty

    complexity_score = 0
    factors: list[dict[str, Any]] = []
    if distance >= 12.0:
        complexity_score += 2
        factors.append({
            "factor": "distance",
            "level": "high",
            "evidence": f"{distance:.1f} km route length",
        })
    elif distance >= 6.0:
        complexity_score += 1
        factors.append({
            "factor": "distance",
            "level": "moderate",
            "evidence": f"{distance:.1f} km route length",
        })
    else:
        factors.append({
            "factor": "distance",
            "level": "low",
            "evidence": f"{distance:.1f} km route length",
        })

    if gain is None:
        factors.append({
            "factor": "elevation_gain",
            "level": "unknown",
            "evidence": "Elevation gain is unavailable",
        })
    elif gain >= 800.0:
        complexity_score += 2
        factors.append({
            "factor": "elevation_gain",
            "level": "high",
            "evidence": f"{gain:.0f} m sampled gain",
        })
    elif gain >= 350.0:
        complexity_score += 1
        factors.append({
            "factor": "elevation_gain",
            "level": "moderate",
            "evidence": f"{gain:.0f} m sampled gain",
        })
    else:
        factors.append({
            "factor": "elevation_gain",
            "level": "low",
            "evidence": f"{gain:.0f} m sampled gain",
        })

    if max_slope is not None and max_slope >= 35.0:
        complexity_score += 2
        factors.append({
            "factor": "slope",
            "level": "high",
            "category": "route",
            "evidence": f"{max_slope:.1f}% maximum sampled slope",
        })
    elif max_slope is not None and max_slope >= 20.0:
        complexity_score += 1
        factors.append({
            "factor": "slope",
            "level": "moderate",
            "category": "route",
            "evidence": f"{max_slope:.1f}% maximum sampled slope",
        })
    elif max_slope is not None:
        factors.append({
            "factor": "slope",
            "level": "low",
            "category": "route",
            "evidence": f"{max_slope:.1f}% maximum sampled slope",
        })

    if difficulty:
        factors.append({
            "factor": "difficulty",
            "level": "source_or_model_signal",
            "category": "route",
            "evidence": difficulty,
        })

    condition_status = _text(condition.get("status") or condition.get("likelihood"))
    walk = condition.get("assessed_over") == "walk"
    for condition_factor in condition.get("factors", []) or []:
        if not isinstance(condition_factor, dict):
            continue
        weight = condition_factor.get("weight")
        if not isinstance(weight, (int, float)) or weight <= 0:
            continue
        complexity_score += 1 if weight >= 10 else 0
        factors.append({
            "factor": f"condition_{condition_factor.get('factor')}",
            "level": condition_factor.get("state"),
            "category": "current_condition",
            "evidence": condition_factor.get("detail"),
        })

    if condition_status in {"adverse", "caution"}:
        factors.append({
            "factor": "current_condition",
            "level": condition_status,
            "category": "current_condition",
            "evidence": condition.get("summary"),
        })

    if condition_status in {"unknown", ""}:
        level = "insufficient_data"
        headline = (
            "Suitability under current conditions cannot be assessed because "
            "there is not enough verified weather evidence."
        )
    elif condition_status == "adverse":
        level = "currently_unfavorable"
        headline = (
            "Observed and forecast weather indicates this route may be "
            "unsuitable during the estimated walk."
            if walk
            else "Current observed and forecast weather indicates this route "
            "may be unsuitable right now."
        )
    elif condition_status == "caution" or complexity_score >= 4:
        level = "caution"
        headline = (
            "Forecast conditions during the estimated walk and route demands "
            "may make parts of this route difficult."
            if walk
            else "Current conditions and route demands may make parts of this "
            "route difficult."
        )
    elif complexity_score >= 2:
        level = "demanding"
        headline = (
            "No adverse weather signal is present, but this route has "
            "demanding terrain or length."
        )
    else:
        level = "suitable_now"
        headline = (
            "No adverse weather signal is present in the available data and "
            "this route has relatively modest demands."
        )

    return {
        "level": level,
        "headline": headline,
        "condition_status": condition_status or "unknown",
        "route_complexity_score": complexity_score,
        "assessed_over": "walk" if walk else "now",
        "assessment_scope": (
            (
                "Route demands and the forecast over the estimated walk only."
                if walk
                else "Route demands and observed current conditions only."
            )
            + " No user age, fitness, health, experience, or medical "
            "assumptions were used, and nothing here is a guarantee about "
            "the route."
        ),
        "factors": factors,
    }


# ============================================================
# PREPARATION (GEAR)
# ============================================================

# Preparation needs, not product names. Two items serving the same need are
# collapsed so the list stays a decision aid rather than a shopping dump.
NEED_FOOTWEAR = "footwear"
NEED_WET_FOOTWEAR = "wet_footwear"
NEED_THERMAL_LAYER = "thermal_layer"
NEED_INSULATION = "insulation"
NEED_RAIN_SHELL = "rain_shell"
NEED_WIND_LAYER = "wind_layer"
NEED_SUN = "sun_protection"
NEED_HYDRATION = "hydration"
NEED_POLES = "poles"
NEED_SOCKS = "socks"
NEED_NAVIGATION = "navigation"
NEED_FIRST_AID = "first_aid"
NEED_LIGHT = "light"
NEED_TRAIL_ACTIVITY = "trail_footwear"
NEED_WINTER = "winter_equipment"

TIER_ORDER = {"essential": 0, "recommended": 1, "conditional": 2}

# Needs that only some kinds of trip create.
NEED_OVERNIGHT = "overnight"
NEED_RESUPPLY = "resupply"
NEED_ACCLIMATISATION = "acclimatisation"
NEED_HELMET = "helmet"
NEED_HARNESS = "harness"
NEED_EXPERIENCE = "experience"


# ============================================================
# ACTIVITY
# ============================================================

# A route longer than this is more than a day at a walking pace.
MULTI_DAY_KM = 25.0
# Above this, altitude itself is a preparation need.
HIGH_ALTITUDE_M = 3000.0
VERY_HIGH_ALTITUDE_M = 4000.0
# SAC grades that mean glacier, rock or climbing terrain. T4 (alpine hiking)
# is exposed walking and is deliberately not in this set.
TECHNICAL_GRADES = {"demanding_alpine_hiking", "difficult_alpine_hiking"}


class ActivityType(str, Enum):
    DAY_HIKE = "day_hike"
    MULTI_DAY_TREK = "multi_day_trek"
    HIGH_ALTITUDE_TREK = "high_altitude_trek"
    TECHNICAL_ALPINE = "technical_alpine"


_ACTIVITY_LABELS = {
    ActivityType.DAY_HIKE: ("Day hike", "hiking"),
    ActivityType.MULTI_DAY_TREK: ("Multi-day trek", "trekking"),
    ActivityType.HIGH_ALTITUDE_TREK: (
        "High-altitude trek",
        "high altitude trekking",
    ),
    ActivityType.TECHNICAL_ALPINE: (
        "Technical alpine route",
        "mountaineering",
    ),
}
_ACTIVITY_RANK = {
    ActivityType.DAY_HIKE: 0,
    ActivityType.MULTI_DAY_TREK: 1,
    ActivityType.HIGH_ALTITUDE_TREK: 2,
    ActivityType.TECHNICAL_ALPINE: 3,
}


@dataclass(frozen=True)
class ActivityProfile:
    """
    What kind of trip a route is, with the signals that say so.

    ``query_term`` is the word product search uses in place of "hiking".
    """

    type: ActivityType
    label: str
    query_term: str
    reasons: tuple[str, ...]
    max_elevation_m: float | None = None
    distance_km: float | None = None
    assisted: bool = False

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        data["reasons"] = list(self.reasons)
        return data


def continuous_distance_km(
    trail: dict[str, Any],
    analysis: dict[str, Any],
) -> tuple[float | None, bool]:
    """
    The distance that says how long a trip is, and whether it was reduced.

    Normally the whole mapped length. When the geometry is a scattered network
    of separate pieces (a "Core Paths" of 236 km in 140 pieces, the largest
    holding 5%) that total is not a trip anyone walks, so the largest piece is
    used instead. The second value is True when that reduction was applied.
    """
    distance = _number(analysis.get("distance_km")) or _number(
        trail.get("length_km")
    )
    completeness = analysis.get("completeness") or {}
    share = _number(completeness.get("main_chain_share"))
    if (
        distance is not None
        and completeness.get("status") == "separate_pieces"
        and share is not None
    ):
        return distance * share, True
    return distance, False


def classify_activity(
    trail: dict[str, Any],
    analysis: dict[str, Any],
) -> ActivityProfile:
    """
    Decide what kind of trip this route is from recorded and measured signals.

    Each rule reads one signal and names it in ``reasons``; the most demanding
    activity any signal supports wins. Nothing here reads the user or the
    weather. Ways of a route are read as well as the route itself, because a
    relation usually carries no grade of its own while its ways do.
    """
    members = [
        member
        for member in (trail.get("member_trails") or [])
        if isinstance(member, dict)
    ]
    grades = {
        grade
        for grade in [
            _text(trail.get("source_difficulty")),
            *(_text(member.get("sac_scale")) for member in members),
        ]
        if grade
    }
    distance, largest_piece_only = continuous_distance_km(trail, analysis)
    max_elevation = _number(
        ((trail.get("terrain") or {}).get("metrics") or {}).get(
            "max_elevation_m"
        )
    )
    assisted = any(
        _text(value) in {"yes", "via_ferrata"}
        for value in [
            trail.get("assisted_trail"),
            *(member.get("assisted_trail") for member in members),
        ]
    ) or any(
        _text(value) == "via_ferrata"
        for value in [
            trail.get("highway_type"),
            *(member.get("highway_type") for member in members),
        ]
    )

    found: list[tuple[ActivityType, str]] = []
    technical_grade = sorted(grades & TECHNICAL_GRADES)
    if technical_grade:
        found.append(
            (
                ActivityType.TECHNICAL_ALPINE,
                f"recorded grade {technical_grade[-1]} (glacier, rock or "
                f"climbing terrain)",
            )
        )
    if assisted:
        found.append(
            (
                ActivityType.TECHNICAL_ALPINE,
                "fixed aids recorded (via ferrata or assisted trail)",
            )
        )
    if max_elevation is not None and max_elevation >= HIGH_ALTITUDE_M:
        found.append(
            (
                ActivityType.HIGH_ALTITUDE_TREK,
                f"highest sampled point {max_elevation:.0f} m",
            )
        )
    if distance is not None and distance >= MULTI_DAY_KM:
        found.append(
            (
                ActivityType.MULTI_DAY_TREK,
                (
                    f"{distance:.0f} km in its largest continuous piece, more "
                    f"than a day at a walking pace"
                    if largest_piece_only
                    else f"{distance:.0f} km of route, more than a day at a "
                    f"walking pace"
                ),
            )
        )

    activity = max(
        (kind for kind, _ in found),
        key=_ACTIVITY_RANK.__getitem__,
        default=ActivityType.DAY_HIKE,
    )
    reasons = tuple(reason for _, reason in found) or (
        "no signal beyond an ordinary walked route",
    )
    label, query_term = _ACTIVITY_LABELS[activity]
    return ActivityProfile(
        type=activity,
        label=label,
        query_term=query_term,
        reasons=reasons,
        max_elevation_m=max_elevation,
        distance_km=distance,
        assisted=assisted,
    )


def gear_recommendations(
    trail: dict[str, Any],
    analysis: dict[str, Any],
    weather: dict[str, Any] | None,
    condition: dict[str, Any],
) -> dict[str, Any]:
    """
    Prioritised, evidence-grounded preparation for the selected route.

    Rules that this function deliberately enforces:

    * Every item is justified by a measured quantity (route length, sampled
      ascent, sampled slope, recorded surface) or by an actual weather
      observation. Nothing is included just because it is common hiking kit.
    * Weather-specific items require the corresponding weather evidence.
      Without live weather, no rain, cold, snow or wind item is produced.
    * Items are tiered. A universal hiking checklist is not returned, and the
      number of items scales with what the route and conditions actually
      require.
    * Two items serving the same preparation need are merged, not stacked.
    """
    distance = (
        _number(analysis.get("distance_km"))
        or _number(trail.get("length_km"))
        or 0.0
    )
    elevation = (trail.get("terrain") or {}).get("metrics") or {}
    gain = _number(elevation.get("elevation_gain_m"))
    loss = _number(elevation.get("elevation_loss_m"))
    max_slope = _number(elevation.get("max_slope_percent"))
    elevation_range = _number(elevation.get("elevation_range_m"))
    surface = _text(trail.get("surface"))
    current = (weather or {}).get("current") or {}
    recent_rain = (weather or {}).get("recent_rain") or {}
    recent_precip = (weather or {}).get("recent_precipitation") or {}
    forecast = (weather or {}).get("forecast") or {}
    temperature = _number(current.get("temperature"))
    wind = _number(current.get("wind_speed"))

    # The walk takes time. Cold, wind and snow are judged on the worst of the
    # estimated walking window when one is present, and each reason says which
    # it used. Heat stays on the current reading. With no window, this is all
    # exactly as it was: the sources below stay "current ...".
    window = (weather or {}).get("window") or {}
    inference = (weather or {}).get("inference") or {}
    window_hours = _number(window.get("hours"))
    over = (
        f" over the next {window_hours:.0f} h"
        if window_hours
        else ""
    )
    cold_temperature = temperature
    cold_source = "current temperature"
    window_cold = _number(window.get("min_temperature"))
    if window_cold is not None and (
        cold_temperature is None or window_cold < cold_temperature
    ):
        cold_temperature = window_cold
        cold_source = f"coldest forecast temperature{over}"
    gear_wind = wind
    wind_source = "current wind"
    window_wind = _number(window.get("max_wind_speed"))
    if window_wind is not None and (gear_wind is None or window_wind > gear_wind):
        gear_wind = window_wind
        wind_source = f"strongest forecast wind{over}"
    snowfall = _number(current.get("snowfall"))
    condition_status = _text(
        condition.get("status") or condition.get("likelihood")
    )
    condition_available = bool(condition.get("available"))

    # ------------------------------------------------------------------
    # PRECIPITATION / SNOW EVIDENCE
    #
    # Wetness and snow are read from the actual measurements, not from the
    # condition engine's overall verdict. A `caution` or `adverse` status is
    # just as often produced by cold, wind or steep exposed ground, and
    # treating any caution as "wet" previously produced a waterproof shell
    # on a freezing route with a reason claiming the conditions were wet.
    # The condition engine's own wetness/precipitation factors are consulted
    # as a second source, so snow reported by weather code is not lost.
    # ------------------------------------------------------------------
    rain_24h = _number(recent_rain.get("24h_mm"))
    if rain_24h is None:
        rain_24h = _number(recent_precip.get("24h_mm"))
    current_precip = _number(current.get("precipitation"))
    forecast_rain = _number(forecast.get("rain_mm"))
    if forecast_rain is None:
        forecast_rain = _number(forecast.get("precipitation_mm"))
    forecast_prob = _number(
        forecast.get("precipitation_probability_max")
    )

    condition_factors = {
        _text(factor.get("factor")): factor
        for factor in (condition.get("factors") or [])
        if isinstance(factor, dict)
    }

    wet_factor = condition_factors.get("wetness")
    precip_factor = condition_factors.get("precipitation")
    snow_factor = condition_factors.get("snow")

    # A factor is only evidence for its own state. The condition engine
    # reports `wetness: dry` as well as `wetness: damp`, so the presence of
    # a factor proves nothing on its own.
    WET_STATES = {"damp", "wet", "saturated"}
    ACTIVE_PRECIP_STATES = {"active", "likely_rain", "rain"}
    wet_state = (
        _text(wet_factor.get("state")) if wet_factor else ""
    )
    precip_state = (
        _text(precip_factor.get("state")) if precip_factor else ""
    )

    precipitation_mm = 0.0
    precipitation_seen = False
    for value in (rain_24h, current_precip, forecast_rain):
        if value is not None:
            precipitation_seen = True
            precipitation_mm = max(precipitation_mm, value)

    # Rain protection needs rain evidence, not merely a temperature reading.
    # A payload carrying rainfall but no temperature still means a wet route.
    precipitation_evidence = (
        precipitation_mm > 0.0
        or (forecast_prob is not None and forecast_prob >= 50.0)
        or wet_state in WET_STATES
        or precip_state in ACTIVE_PRECIP_STATES
    )
    wet_condition = precipitation_evidence and (
        wet_state in WET_STATES
        or precip_state in ACTIVE_PRECIP_STATES
        or precipitation_mm >= 1.0
    )

    # Snow may be reported as a measured depth, as a factor the condition
    # engine derived from the weather code, or as neither.
    snow_now = (
        snowfall is not None and snowfall > 0.0
    ) or snow_factor is not None
    snow_inferred = bool(inference.get("snow_on_route_likely"))
    snow_reported = snow_now or snow_inferred
    snow_depth_cm = snowfall if snowfall is not None else 0.0
    # What the snow reasoning cites, and how it words the claim. Current snow
    # and the forecast inference are both cited when both apply.
    snow_evidence: list[str] = []
    if snowfall is not None and snowfall > 0.0:
        snow_evidence.append(f"current snowfall: {snow_depth_cm:.1f} cm")
    elif snow_now:
        snow_evidence.append("weather reports snow on this route")
    if snow_inferred:
        snow_evidence.extend(str(r) for r in inference.get("snow_reasons") or [])
    snow_phrase = (
        "Snow is reported on this route"
        if snow_now
        else "Snow is likely on the upper part of this route, inferred from "
        "the forecast"
    )

    weather_known = temperature is not None or wind is not None
    # Any measured weather field at all counts as weather evidence.
    any_weather_known = weather_known or precipitation_seen or snow_reported

    # A steep, sustained climb is the strongest single demand signal and is
    # measured directly, so it is stated as a number rather than a platitude.
    climb_evidence: list[str] = []
    if gain is not None:
        climb_evidence.append(f"{gain:.0f} m sampled ascent")
    if max_slope is not None:
        climb_evidence.append(f"{max_slope:.0f}% steepest sampled section")
    climb_summary = " and ".join(climb_evidence)

    steep = max_slope is not None and max_slope >= 30.0
    sustained_climb = gain is not None and gain >= 400.0
    long_route = distance >= 8.0
    exposed = steep and (elevation_range or 0.0) >= 300.0

    items: dict[str, dict[str, Any]] = {}

    def offer(
        need: str,
        *,
        tier: str,
        item: str,
        category: str,
        reason: str,
        evidence: list[str],
        specific: bool = False,
    ) -> None:
        """
        Add an item unless the same preparation need is already met.

        ``specific=True`` means this variant is more precisely justified than
        whatever already covers the need, so it replaces the existing wording
        instead of adding a second line for the same thing.
        """
        if need in items:
            existing = items[need]
            # Keep the strongest tier and merge the supporting evidence.
            if TIER_ORDER[tier] < TIER_ORDER[existing["priority"]]:
                existing["priority"] = tier
            for value in evidence:
                if value not in existing["evidence"]:
                    existing["evidence"].append(value)
            if specific:
                existing["item"] = item
                existing["reason"] = reason
            return
        items[need] = {
            "need": need,
            "priority": tier,
            "item": item,
            "category": category,
            "reason": reason,
            "evidence": list(evidence),
        }

    # ---------------- BASELINE, JUSTIFIED BY THE ACTIVITY ----------------
    # These two follow from selecting a mapped walking route at all. They are
    # labelled as baseline rather than dressed up as trail-specific findings.
    offer(
        NEED_TRAIL_ACTIVITY,
        tier="essential",
        item="Broken-in trail shoes",
        category="footwear",
        reason=(
            "The selected feature is a walked route, so footwear is the "
            "baseline requirement. It is a general preparation item, not a "
            "finding about this trail."
        ),
        evidence=["route is a walked OSM trail"],
    )
    offer(
        NEED_NAVIGATION,
        tier="essential",
        item="Offline access to this route",
        category="navigation",
        reason=(
            f"This is one real OSM route ("
            f"{trail.get('osm_type') or 'feature'} "
            f"{trail.get('osm_id')}); keeping it available covers the part "
            f"of the route with no reception."
        ),
        evidence=["OSM identity is verified and stable"],
    )

    # ---------------- ACTIVITY-DRIVEN REQUIREMENTS ----------------
    # The kind of trip changes what preparation is worth stating. Each item
    # is justified by the recorded or measured signal that created it, and
    # signals are read individually rather than from the winning label, so a
    # long route that also climbs high gets both sets.
    profile = classify_activity(trail, analysis)
    max_elevation = _number(elevation.get("max_elevation_m"))
    # A scattered network is judged by its largest piece, not its total.
    trip_km, _ = continuous_distance_km(trail, analysis)

    if trip_km is not None and trip_km >= MULTI_DAY_KM:
        multi_day_evidence = [f"{trip_km:.0f} km of continuous route"]
        offer(
            NEED_OVERNIGHT,
            tier="recommended",
            item="Overnight plan: huts, lodging or shelter and a sleep system",
            category="shelter",
            reason=(
                f"The route is {trip_km:.0f} km, which is more than a day at "
                f"a walking pace, so where to sleep has to be settled before "
                f"starting."
            ),
            evidence=multi_day_evidence,
        )
        offer(
            NEED_RESUPPLY,
            tier="recommended",
            item="Food and water resupply plan for each day",
            category="food",
            reason=(
                f"A {trip_km:.0f} km route cannot be carried on one day's "
                f"supplies, so resupply points need to be known in advance."
            ),
            evidence=multi_day_evidence,
        )
        offer(
            NEED_LIGHT,
            tier="recommended",
            item="Headlamp",
            category="lighting",
            reason=(
                f"On a {trip_km:.0f} km route some walking is unlikely to "
                f"finish in daylight."
            ),
            evidence=multi_day_evidence,
        )

    if max_elevation is not None and max_elevation >= HIGH_ALTITUDE_M:
        altitude_evidence = [f"highest sampled point {max_elevation:.0f} m"]
        offer(
            NEED_ACCLIMATISATION,
            tier=(
                "essential"
                if max_elevation >= VERY_HIGH_ALTITUDE_M
                else "recommended"
            ),
            item="Acclimatisation plan and the signs of altitude sickness",
            category="acclimatisation",
            reason=(
                f"The route reaches {max_elevation:.0f} m, where altitude "
                f"itself affects how the body copes, so the pace of ascent and "
                f"rest days need planning."
            ),
            evidence=altitude_evidence,
        )
        offer(
            NEED_INSULATION,
            tier="recommended",
            item="Insulating layer for the high sections",
            category="clothing",
            reason=(
                "Air temperature falls with height, so the high sections are "
                "colder than a valley reading suggests."
            ),
            evidence=altitude_evidence,
        )
        offer(
            NEED_SUN,
            tier="recommended",
            item="Sunglasses and high-SPF sun protection",
            category="sun protection",
            reason=(
                "Ultraviolet exposure increases with altitude, and more so "
                "over snow."
            ),
            evidence=altitude_evidence,
        )

    if profile.type == ActivityType.TECHNICAL_ALPINE:
        technical_evidence = list(profile.reasons)
        offer(
            NEED_EXPERIENCE,
            tier="essential",
            item="Mountaineering experience, or a qualified guide",
            category="experience",
            reason=(
                "The recorded terrain goes beyond hiking, so it should not be "
                "attempted without the skills for it."
            ),
            evidence=technical_evidence,
        )
        offer(
            NEED_HELMET,
            tier="essential" if profile.assisted else "recommended",
            item="Climbing helmet",
            category="equipment",
            reason=(
                "Rockfall and falls are the main hazards on terrain of this "
                "kind."
            ),
            evidence=technical_evidence,
        )
        if profile.assisted:
            offer(
                NEED_HARNESS,
                tier="essential",
                item="Harness and via ferrata set",
                category="equipment",
                reason=(
                    "Fixed aids are recorded on this route, and they are used "
                    "by clipping in."
                ),
                evidence=technical_evidence,
            )

    # ---------------- TERRAIN-DRIVEN REQUIREMENTS ----------------
    if surface and surface not in {"asphalt", "paved", "concrete", "cement"}:
        offer(
            NEED_TRAIL_ACTIVITY,
            tier="essential",
            item=f"Trail shoes with an outsole suited to {surface}",
            category="footwear",
            reason=(
                f"The recorded surface on this route is {surface}, so grip "
                f"matters more here than on a surfaced path."
            ),
            evidence=[
                "route is a walked OSM trail",
                f"recorded surface: {surface}",
            ],
            specific=True,
        )

    if sustained_climb or steep:
        offer(
            NEED_POLES,
            tier="recommended",
            item="Trekking poles",
            category="equipment",
            reason=(
                f"This route has {climb_summary}, so poles reduce repeated "
                f"loading on the knees."
            ),
            evidence=climb_evidence,
        )
        offer(
            NEED_SOCKS,
            tier="recommended",
            item="Socks, plus a spare pair",
            category="clothing",
            reason=(
                f"With {climb_summary}, feet and knees take repeated load, "
                f"and a dry spare pair is the main defence against blisters."
            ),
            evidence=climb_evidence,
        )

    # Heat raises the water demand on the same route, so it is an
    # independent reason to carry water rather than a distance effect.
    hot = weather_known and temperature is not None and temperature >= 28.0

    if long_route or hot:
        heat_note = (
            f", and the current temperature is {temperature:.1f} °C, "
            f"which raises how much water the same walk costs"
            if hot and temperature is not None
            else ""
        )
        offer(
            NEED_HYDRATION,
            tier="essential",
            item=(
                "Water and electrolytes for the route length"
                + (
                    ", and extra for the heat"
                    if hot and temperature is not None
                    else ""
                )
            ),
            category="hydration",
            reason=(
                f"The verified route is {distance:.1f} km"
                + heat_note
                + ", so carried water is required rather than a single refill."
            ),
            evidence=[
                f"verified route length: {distance:.1f} km",
                *(
                    [f"current temperature: {temperature:.1f} °C"]
                    if hot and temperature is not None
                    else []
                ),
            ],
        )
    elif distance >= 3.0:
        offer(
            NEED_HYDRATION,
            tier="recommended",
            item="Water for the route length",
            category="hydration",
            reason=(
                f"The verified route is {distance:.1f} km of walking."
            ),
            evidence=[f"verified route length: {distance:.1f} km"],
        )

    if long_route or sustained_climb:
        offer(
            NEED_FIRST_AID,
            tier="recommended",
            item="Small first-aid kit",
            category="first_aid",
            reason=(
                f"The route is {distance:.1f} km"
                + (
                    f" with {gain:.0f} m of ascent"
                    if gain is not None
                    else ""
                )
                + ", so a walk-back is a realistic part of this outing."
            ),
            evidence=[
                f"verified route length: {distance:.1f} km",
                *([f"sampled ascent: {gain:.0f} m"] if gain is not None else []),
            ],
        )

    # ---------------- WEATHER-DRIVEN REQUIREMENTS ----------------
    # Each of these is gated on the corresponding observation existing. With
    # no live weather, none of them are produced.
    if any_weather_known and wet_condition:
        wet_reasons: list[str] = []
        wet_ev: list[str] = []
        if rain_24h is not None and rain_24h > 0.0:
            wet_reasons.append(
                f"{rain_24h:.1f} mm of rain fell in the last 24 h"
            )
            wet_ev.append(f"24 h rainfall: {rain_24h:.1f} mm")
        if current_precip is not None and current_precip > 0.0:
            wet_reasons.append("precipitation is falling right now")
            wet_ev.append("current precipitation: above zero")
        if forecast_prob is not None and forecast_prob >= 50.0:
            wet_reasons.append(
                f"{forecast_prob:.0f}% chance of rain in the next 24 h"
            )
            wet_ev.append(f"forecast rain chance: {forecast_prob:.0f}%")
        if surface:
            wet_reasons.append(f"the recorded surface is {surface}")
            wet_ev.append(f"recorded surface: {surface}")
        offer(
            NEED_RAIN_SHELL,
            tier="essential",
            item="Waterproof shell",
            category="clothing",
            reason=(
                "There is rain on this route"
                + (
                    ": " + ", ".join(wet_reasons) + "."
                    if wet_reasons
                    else "."
                )
            ),
            evidence=wet_ev,
        )
        if current_precip is not None and current_precip > 0.0 and surface:
            # Only when it is actually raining on a trail with a recorded
            # natural surface. Never a default.
            offer(
                NEED_WET_FOOTWEAR,
                tier="conditional",
                item="Waterproof over-trousers",
                category="clothing",
                reason=(
                    f"Rain is falling now and the recorded surface is "
                    f"{surface}, so lower-body waterproofing is worth "
                    f"carrying."
                ),
                evidence=[
                    "current precipitation: above zero",
                    f"recorded surface: {surface}",
                ],
            )

    if cold_temperature is not None and cold_temperature <= 12.0:
        offer(
            NEED_THERMAL_LAYER,
            tier="essential",
            item="Fleece or synthetic mid layer",
            category="clothing",
            reason=(
                f"The {cold_source} on this route is "
                f"{cold_temperature:.1f} °C."
            ),
            evidence=[f"{cold_source}: {cold_temperature:.1f} °C"],
        )
    if cold_temperature is not None and cold_temperature <= 4.0:
        offer(
            NEED_INSULATION,
            tier="essential",
            item="Insulated jacket, hat and gloves",
            category="clothing",
            reason=(
                f"The {cold_source} is {cold_temperature:.1f} °C, and heat "
                f"is lost quickly on a stopped"
                + (
                    f" {climb_summary.replace(' and ', '-')} route"
                    if climb_summary
                    else " route"
                )
                + "."
            ),
            evidence=[
                f"{cold_source}: {cold_temperature:.1f} °C",
                *climb_evidence,
            ],
        )
    if (
        gear_wind is not None
        and gear_wind >= 30.0
    ):
        offer(
            NEED_WIND_LAYER,
            tier="essential",
            item="Windproof outer layer",
            category="clothing",
            reason=(
                (
                    "Current wind"
                    if wind_source == "current wind"
                    else f"The {wind_source}"
                )
                + f" on this route is {gear_wind:.1f} km/h"
                + (
                    ", and the route reaches "
                    f"{max_slope:.0f}% slope over "
                    f"{elevation_range:.0f} m of relief."
                    if max_slope is not None and elevation_range is not None
                    else "."
                )
            ),
            evidence=[
                f"{wind_source}: {gear_wind:.1f} km/h",
                *(
                    [f"steepest sampled section: {max_slope:.0f}%"]
                    if max_slope is not None
                    else []
                ),
            ],
        )
    if (
        weather_known
        and temperature is not None
        and temperature >= 27.0
        and not precipitation_evidence
    ):
        # Sun protection is for dry, bright conditions. Offering it while
        # rain is falling or forecast is contradictory advice.
        offer(
            NEED_SUN,
            tier="recommended",
            item="Sun hat, sunglasses and sunscreen",
            category="clothing",
            reason=(
                f"The current temperature is {temperature:.1f} °C"
                + (
                    f", and the route climbs {gain:.0f} m."
                    if gain is not None
                    else "."
                )
                + " with no rain reported."
            ),
            evidence=[f"current temperature: {temperature:.1f} °C"],
        )
    if cold_temperature is not None and cold_temperature <= -10.0:
        # Well below freezing the insulated layer above is not sufficient
        # on its own, whatever the route looks like.
        offer(
            NEED_INSULATION,
            tier="essential",
            item="Expedition insulation: mitts, balaclava and insulated boots",
            category="clothing",
            reason=(
                f"The {cold_source} is {cold_temperature:.1f} °C, well "
                f"below freezing, so exposed skin and extremities are at "
                f"risk of cold injury rather than merely being cold."
            ),
            evidence=[
                f"{cold_source}: {cold_temperature:.1f} °C",
                *(
                    [f"{wind_source}: {gear_wind:.1f} km/h"]
                    if gear_wind is not None and gear_wind >= 25.0
                    else []
                ),
            ],
            specific=True,
        )
    if snow_reported:
        # Traction gear is only justified where there is slope to descend.
        # Snow on a flat path needs warm layers and water-resistant boots,
        # not an ice axe.
        if steep or (sustained_climb and gain is not None and gain >= 300.0):
            offer(
                NEED_WINTER,
                tier="essential",
                item="Ice axe or crampons, matched to the route",
                category="equipment",
                reason=(
                    snow_phrase
                    + (
                        f" and it climbs {gain:.0f} m"
                        if gain is not None
                        else ""
                    )
                    + (
                        f" with a {max_slope:.0f}% steepest section"
                        if max_slope is not None
                        else ""
                    )
                    + ", so traction is a route requirement here rather "
                    "than a contingency."
                ),
                evidence=[
                    *snow_evidence,
                    *climb_evidence,
                ],
            )
        else:
            offer(
                NEED_WINTER,
                tier="recommended",
                item="Waterproof boots and warm layers for snow underfoot",
                category="equipment",
                reason=(
                    f"{snow_phrase}, but the measured route "
                    "is not steep enough to need traction gear."
                ),
                evidence=[
                    *snow_evidence,
                ],
            )

    # ---------------- CONDITIONAL ITEMS ----------------
    # These are never essential. They appear only with a stated trigger, and
    # the trigger is shown to the user.
    if long_route or sustained_climb:
        offer(
            NEED_LIGHT,
            tier="conditional",
            item="Headlamp, if any of the route is walked after dark",
            category="lighting",
            reason=(
                f"The route is {distance:.1f} km"
                + (
                    f" with {gain:.0f} m of ascent"
                    if gain is not None
                    else ""
                )
                + ", so a late start or a slow section can run into "
                "darkness. This is a contingency, not a requirement."
            ),
            evidence=[
                f"verified route length: {distance:.1f} km",
                *(
                    [f"sampled ascent: {gain:.0f} m"]
                    if gain is not None
                    else []
                ),
            ],
        )
    if exposed and wet_condition:
        offer(
            NEED_POLES,
            tier="recommended",
            item="Trekking poles, and extra care on the steepest section",
            category="equipment",
            reason=(
                f"The route combines {max_slope:.0f}% slope with "
                f"{elevation_range:.0f} m of relief while current conditions "
                f"are {condition_status}."
            ),
            evidence=[
                f"steepest sampled section: {max_slope:.0f}%",
                f"elevation range: {elevation_range:.0f} m",
                f"current condition: {condition_status}",
            ],
        )

    ordered = sorted(
        items.values(),
        key=lambda entry: (
            TIER_ORDER[entry["priority"]],
            entry["item"],
        ),
    )

    basis: list[str] = []
    if distance > 0.0:
        basis.append("verified route length")
    if surface:
        basis.append("recorded OSM surface")
    if gain is not None or max_slope is not None:
        basis.append("sampled elevation profile")
    if weather_known:
        basis.append("current weather at the selected route")
    if condition_available:
        basis.append("condition inference")
    if not basis:
        basis.append(
            "route identity only; live weather and terrain were unavailable"
        )

    missing: list[str] = []
    if gain is None:
        missing.append("elevation profile")
    if not weather_known:
        missing.append("current weather")
    if not condition_available:
        missing.append("condition likelihood")
    if not surface:
        missing.append("recorded surface")

    # User-facing grouping. "navigation" is a preparation habit rather than a
    # physical item, and presenting it beside a rain shell made the list read
    # like a shopping checklist of equipment.
    group_of = {
        "navigation": "preparation",
        "first_aid": "preparation",
        "acclimatisation": "preparation",
        "experience": "preparation",
        "hydration": "supplies",
        "food": "supplies",
    }
    for entry in ordered:
        entry["group"] = group_of.get(
            entry["category"], "equipment"
        )

    return {
        "items": ordered,
        "groups": {
            "equipment": sum(
                1
                for entry in ordered
                if entry["group"] == "equipment"
            ),
            "supplies": sum(
                1
                for entry in ordered
                if entry["group"] == "supplies"
            ),
            "preparation": sum(
                1
                for entry in ordered
                if entry["group"] == "preparation"
            ),
        },
        "essential_count": sum(
            1
            for entry in ordered
            if entry["priority"] == "essential"
        ),
        "recommended_count": sum(
            1
            for entry in ordered
            if entry["priority"] == "recommended"
        ),
        "conditional_count": sum(
            1
            for entry in ordered
            if entry["priority"] == "conditional"
        ),
        "basis": basis,
        "missing_evidence": missing,
        "activity": profile.as_dict(),
    }
