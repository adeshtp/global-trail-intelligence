import type {
  ElevationProfilePoint,
  TrailIntelligenceResponse,
} from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/*
 * Bound for one discovery fetch. Fallback-served queries can legitimately
 * take minutes, so this is generous; without it a silently hung connection
 * leaves the loading state on forever with no error shown.
 */
export const DISCOVERY_TIMEOUT_MS = 300000;
export const INTELLIGENCE_TIMEOUT_MS = 240000;
export const PRODUCTS_TIMEOUT_MS = 180000;
export const ASSISTANT_TIMEOUT_MS = 120000;

/*
 * Bound a fetch controller. Returns whether this bound fired and a cleanup.
 * Callers keep treating supersede-aborts as silent (a newer request won)
 * and report only their own timeout while still current, so a hung provider
 * surfaces as an error instead of an eternal spinner.
 */
export function abortAfter(
  controller: AbortController,
  ms: number
): { timedOut: () => boolean; clear: () => void } {
  let fired = false;
  const id = setTimeout(() => {
    fired = true;
    controller.abort();
  }, ms);
  return {
    timedOut: () => fired,
    clear: () => clearTimeout(id),
  };
}


/**
 * Render a missing-evidence key as a sentence a person can read.
 *
 * The backend reports machine keys such as "recorded surface" or
 * "condition likelihood". Showing them raw produces developer wording in the
 * middle of the user-facing page, so they are translated here rather than
 * leaked.
 */
export const MISSING_EVIDENCE_LABELS: Record<
  string,
  string
> = {
  live_weather: "live weather for this route",
  recorded_surface: "the trail surface",
  surface: "the trail surface",
  condition_likelihood: "the current-condition assessment",
  "elevation profile": "the elevation profile",
  elevation_profile: "the elevation profile",
  current_weather: "the current weather",
  route_distance: "the route length",
  elevation_gain: "the ascent",
  max_slope: "the steepest section",
  terrain_slope: "the steepest section",
  temperature: "the temperature",
  wind_speed: "the wind speed",
  recent_24h_rainfall: "the last 24 hours of rainfall",
  recent_72h_rainfall: "the last 72 hours of rainfall",
  current_precipitation: "whether rain is falling now",
  forecast_rain: "the rain forecast",
  weather: "the weather at this route",
};


export function missingEvidenceText(
  items: string[]
): string {
  const labels = items.map(
    (item) =>
      MISSING_EVIDENCE_LABELS[item] ??
      item.replaceAll("_", " ")
  );
  if (labels.length === 0) {
    return "";
  }
  if (labels.length === 1) {
    return labels[0];
  }
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}


/**
 * Split a sampled elevation profile into its separate route components.
 *
 * The backend samples each disconnected component of the geometry and tags
 * every point with the component it came from, so the split is read from the
 * data rather than inferred from a distance jump. Components with fewer than
 * two usable samples cannot be drawn and are dropped.
 */
export function groupProfileComponents<
  T extends ElevationProfilePoint
>(
  points: T[]
): T[][] {
  const components: T[][] = [];
  let current: T[] | null = null;
  let currentIndex: number | null = null;

  for (const point of points) {
    if (current === null || point.component_index !== currentIndex) {
      if (current && current.length >= 2) {
        components.push(current);
      }
      current = [point];
      currentIndex = point.component_index;
      continue;
    }
    current.push(point);
  }
  if (current && current.length >= 2) {
    components.push(current);
  }
  return components;
}


/**
 * What the distance figure is actually a measurement of.
 *
 * It is always the length of the geometry that is verified and drawn, and it
 * is labelled that way unconditionally. "Route length" was previously used
 * whenever OpenStreetMap mapped the trail as a multi-member route relation,
 * which is exactly the reasoning this must not rely on: a relation holding
 * forty member ways still records only what mappers have drawn, and a long
 * mapped section is not thereby the whole trek. Object type is not evidence
 * of completeness, so the wording does not vary with it.
 *
 * The application never extends, stitches or infers the unmapped remainder,
 * so it has no basis for a total. `route_completeness` may only ever refine
 * the explanation shown alongside the figure, never the figure's meaning.
 */
export function mappedLengthLabel(): string {
  return "Length of the mapped section";
}


/**
 * Suggested questions for the selected trail, chosen from what the trail
 * actually has rather than a fixed list dumped on every trail.
 *
 * A fixed list inevitably shows prompts the trail cannot answer ("Why is
 * this caution?" on a favorable trail) alongside engineering-flavored ones
 * ("Where did this route come from?") that no walker would ask. Each
 * suggestion below is gated on the evidence that would answer it: condition
 * questions only when the condition is actually caution or adverse, gear
 * questions only when gear exists, climbing questions only when measured
 * ascent exists. At most four are shown, in priority order.
 */
export function suggestedQuestions(
  intelligence: TrailIntelligenceResponse | null | undefined,
): string[] {
  if (!intelligence) {
    return [];
  }
  const questions: string[] = [];
  const status = intelligence.condition?.status;
  if (status === "caution" || status === "adverse") {
    questions.push(`Why is this ${status}?`);
  }
  const gearItems = intelligence.gear?.items ?? [];
  if (
    gearItems.some((item) => item.priority === "essential") ||
    gearItems.length > 0
  ) {
    questions.push("Which gear is essential?");
  }
  if (
    intelligence.difficulty?.source?.sac_scale ||
    intelligence.difficulty?.ml?.available
  ) {
    questions.push("How difficult is this trail?");
  }
  const gain = intelligence.terrain?.metrics?.elevation_gain_m;
  if (typeof gain === "number" && Number.isFinite(gain) && gain > 0) {
    questions.push("How much climbing is involved?");
  }
  if (questions.length === 0 && intelligence.weather) {
    questions.push("What are the current conditions?");
  }
  if (questions.length === 0) {
    questions.push("What should I prepare for?");
  }
  return questions.slice(0, 4);
}


/**
 * Render the point the weather was actually measured at.
 *
 * The backend returns the real coordinate it used, which is a representative
 * point on the verified geometry rather than the place the user searched.
 * Printing it makes the weather figures locatable instead of merely asserted.
 */
export function weatherPointLabel(
  coordinate:
    | { latitude: number; longitude: number }
    | undefined,
): string {
  if (
    !coordinate ||
    !Number.isFinite(coordinate.latitude) ||
    !Number.isFinite(coordinate.longitude)
  ) {
    return "a point on the selected route";
  }
  const ns = coordinate.latitude >= 0 ? "N" : "S";
  const ew = coordinate.longitude >= 0 ? "E" : "W";
  return `${Math.abs(coordinate.latitude).toFixed(3)}°${ns} ${Math.abs(
    coordinate.longitude,
  ).toFixed(3)}°${ew}`;
}


/**
 * Human phrasing for a condition factor state.
 *
 * The backend uses precise machine words such as "saturated" or
 * "thunderstorm". Those are correct but read like an internal status, so
 * they are phrased for a person without changing the underlying meaning.
 */
export const CONDITION_STATE_LABELS: Record<
  string,
  string
> = {
  saturated: "very wet",
  wet: "wet",
  damp: "damp",
  dry: "dry",
  active: "happening now",
  rain: "rain",
  likely_rain: "rain likely",
  fog: "low visibility",
  snow: "snow",
  thunderstorm: "thunderstorms",
  freezing: "freezing",
  very_cold: "very cold",
  cold: "cold",
  hot: "hot",
  storm_force: "storm force",
  strong: "strong wind",
  breezy: "breezy",
  water_retaining: "holds water",
  draining: "drains well",
  natural: "natural surface",
  steep: "steep",
  moderately_steep: "moderately steep",
  high_cold_exposed: "high and cold",
};
