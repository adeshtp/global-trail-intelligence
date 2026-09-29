"""
Route complexity analysis - an interpretable, geometry-based route profile.

The way-level difficulty classifier in `app.services.difficulty` is honest but
weak: on the geographically disjoint holdout it reaches roughly 0.33 macro-F1
over the seven native grades, with the middle grades close to chance. Rather
than present that as a difficulty judgement, this module derives an
interpretable profile from the VERIFIED route itself, using only measured
distance, sampled ascent, sampled slope, relief and component structure.

This is NOT a trained model, it is NOT a difficulty grade, and it is never
combined with the learned estimate into a single score. The two answer
different questions and are reported separately.

No provider calls. Runs purely on measured geometry and terrain.
"""
from __future__ import annotations

import math
from typing import Any


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _log_saturate(value: float, half: float) -> float:
    """Monotonic 0..1 saturation so one huge value cannot dominate."""
    if value <= 0.0:
        return 0.0
    return value / (value + half)


def route_complexity(
    analysis: dict[str, Any],
    terrain: dict[str, Any] | None,
    trail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build an interpretable complexity profile from verified measurements.

    Every component is a measured quantity, so the score can be explained
    line by line. Nothing here is a personal or medical judgement.
    """
    trail = trail or {}
    metrics = (terrain or {}).get("metrics") or {}

    distance_km = _num(analysis.get("distance_km"))
    gain_m = _num(metrics.get("elevation_gain_m"))
    loss_m = _num(metrics.get("elevation_loss_m"))
    range_m = _num(metrics.get("elevation_range_m"))
    avg_slope = _num(metrics.get("average_slope_percent"))
    max_slope = _num(metrics.get("max_slope_percent"))
    components = int(analysis.get("component_count") or 0)

    components_list: list[dict[str, Any]] = []
    missing: list[str] = []
    total = 0.0
    weight_total = 0.0

    def add(
        label: str,
        raw: float | None,
        weight: float,
        explain: str,
    ) -> None:
        nonlocal total, weight_total
        if raw is None:
            missing.append(label)
            return
        total += weight * raw
        weight_total += weight
        components_list.append(
            {
                "component": label,
                "normalised": round(raw, 4),
                "weight": weight,
                "measured": explain,
            }
        )

    if distance_km is None:
        missing.append("route_distance")
    else:
        # 20 km is treated as "as complex as distance alone gets".
        add(
            "distance",
            _log_saturate(distance_km, 8.0),
            0.30,
            f"{distance_km:.2f} km of verified route",
        )

    climb = gain_m if gain_m is not None else None
    if climb is not None and climb > 0.0:
        add(
            "ascent",
            _log_saturate(climb, 500.0),
            0.28,
            f"{climb:.0f} m of sampled ascent",
        )
    elif climb is None:
        missing.append("elevation_gain")

    if max_slope is not None:
        add(
            "steepness",
            min(max_slope / 60.0, 1.0),
            0.24,
            f"{max_slope:.1f}% maximum sampled slope",
        )
    else:
        missing.append("max_slope")

    if range_m is not None and range_m > 0.0:
        add(
            "relief",
            min(range_m / 1200.0, 1.0),
            0.18,
            f"{range_m:.0f} m of elevation range",
        )

    if components > 1:
        add(
            "structure",
            min((components - 1) / 6.0, 1.0),
            0.10,
            f"{components} disconnected route components",
        )

    score = round(100.0 * total / weight_total, 1) if weight_total > 0 else None

    if score is None:
        label = "unavailable"
    elif score < 22.0:
        label = "low"
    elif score < 45.0:
        label = "moderate"
    elif score < 70.0:
        label = "substantial"
    else:
        label = "demanding"

    surface = str(trail.get("surface") or "").strip() or None
    if surface:
        components_list.append(
            {
                "component": "surface",
                "normalised": None,
                "weight": 0.0,
                "measured": f"Recorded trail surface is {surface}",
            }
        )

    return {
        "available": score is not None,
        "score": score,
        "label": label,
        "observation_unit": "verified_route_geometry",
        "components": components_list,
        "missing_evidence": missing,
        "method": (
            "Weighted, monotonic saturation of measured distance, ascent, "
            "maximum slope, relief and component count. Deterministic and "
            "fully explainable."
        ),
    }
