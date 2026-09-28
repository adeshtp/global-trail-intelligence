/**
 * Presentation-only elevation orientation.
 *
 * The backend always stores the sampled profile in mapped geometry order
 * (route difficulty features are derived from that order), and the route
 * geometry itself is never reordered. When the backend reports
 * `profile_orientation: "low_to_high"` with `reversal_needed: true` for a
 * single-component route, the chart may run from the lower end to the
 * higher end instead.
 *
 * Reversal here produces display copies only: cumulative distances are
 * mirrored so the horizontal axis still reads 0 → route length, and the
 * displayed ascent/descent are swapped to match what the reversed chart
 * actually climbs. Min, max and range are unaffected by order. Multi-piece
 * routes are never reversed — component order is preserved exactly as
 * mapped.
 */
export type OrientedProfilePoint = {
  component_index: number;
  component_distance_km: number;
  distance_km: number;
  elevation_m: number | null;
};

export type ProfileOrientationInfo = {
  profile_orientation?: string;
  reversal_needed?: boolean;
} | null | undefined;

/**
 * Nearest-sample hover lookup for the elevation chart.
 *
 * The readout always snaps to a real sampled profile point — never to an
 * interpolated value between components, and never across a disconnected
 * gap. Because every displayed point keeps its own component index and
 * cumulative distance, the tooltip position and both readout values always
 * describe one actual sample.
 *
 * Returns the point index, or null when there is nothing to snap to.
 * Ties resolve to the earlier (lower-distance) point deterministically.
 */
export function nearestDisplayPointIndex<
  T extends OrientedProfilePoint,
>(points: T[], targetDistanceKm: number): number | null {
  if (points.length === 0 || !Number.isFinite(targetDistanceKm)) {
    return null;
  }
  let best = 0;
  let bestGap = Math.abs(points[0].distance_km - targetDistanceKm);
  for (let index = 1; index < points.length; index += 1) {
    const gap = Math.abs(points[index].distance_km - targetDistanceKm);
    if (gap < bestGap) {
      best = index;
      bestGap = gap;
    }
  }
  return best;
}

/**
 * Which side of the crosshair the tooltip box should sit on so it stays
 * inside the plot area instead of clipping at the chart edge.
 */
export function tooltipSide(
  pointX: number,
  plotRight: number,
  boxWidth: number,
): "left" | "right" {
  return pointX + 8 + boxWidth > plotRight ? "left" : "right";
}

export function orientDisplayProfile<
  T extends OrientedProfilePoint,
>(
  points: T[],
  maxDistance: number,
  gain_m: number | null | undefined,
  loss_m: number | null | undefined,
  orientation: ProfileOrientationInfo,
  singleComponent: boolean,
): {
  displayPoints: T[];
  displayGain: number | null | undefined;
  displayLoss: number | null | undefined;
  directionNote: string;
} {
  const oriented =
    orientation?.profile_orientation === "low_to_high";
  if (
    oriented &&
    orientation?.reversal_needed === true &&
    singleComponent &&
    points.length >= 2
  ) {
    const maxComponentDistance = Math.max(
      ...points.map((point) => point.component_distance_km)
    );
    const displayPoints = [...points]
      .reverse()
      .map((point) => ({
        ...point,
        distance_km:
          Math.round((maxDistance - point.distance_km) * 1000) /
          1000,
        component_distance_km:
          Math.round(
            (maxComponentDistance - point.component_distance_km) *
              1000
          ) / 1000,
      }));
    return {
      displayPoints,
      displayGain: loss_m,
      displayLoss: gain_m,
      directionNote:
        "Oriented from the lower end to the higher end. The mapped route order runs the opposite way.",
    };
  }

  return {
    displayPoints: points,
    displayGain: gain_m,
    displayLoss: loss_m,
    directionNote: oriented
      ? "Runs from the lower end to the higher end, following the mapped route order."
      : "Direction follows the mapped route order.",
  };
}
