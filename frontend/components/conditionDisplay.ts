/**
 * Presentation helpers for the trail-conditions section.
 *
 * The backend condition engine stays the source of truth: status,
 * likelihood, summary, factors and missing evidence all come from the
 * intelligence response untouched. Nothing here re-infers conditions,
 * fills in missing weather as zero, or calls any provider.
 *
 * Two display decisions live here, both presentation-only:
 *
 *   - the human headline for a backend status. It never exposes the raw
 *     `favorable` / `caution` / `adverse` / `unknown` tokens, and the
 *     caution variant is picked from real wet evidence, not hardcoded;
 *   - which observed weather values to show. Only non-null provider
 *     values are listed; a missing value is omitted, never rendered as 0.
 */
export type ConditionStatusInput =
  | "favorable"
  | "caution"
  | "adverse"
  | "unknown"
  | string;

export type ConditionWeatherInput = {
  current?: {
    precipitation?: number | null;
    rain?: number | null;
    temperature?: number | null;
    wind_speed?: number | null;
    snowfall?: number | null;
  } | null;
  recent_rain?: Record<string, number | null> | null;
  recent_precipitation?: Record<string, number | null> | null;
  forecast?: {
    precipitation_mm?: number | null;
    rain_mm?: number | null;
    precipitation_probability_max?: number | null;
  } | null;
} | null;

export type ConditionTone = "good" | "warn" | "bad" | "neutral";

export type ObservedEvidence = {
  label: string;
  value: string;
};

/** A forecast chance worth calling "likely" in headline wording only. */
const LIKELY_PROBABILITY_PCT = 40;

/** Below this the wind is ordinary walking weather, not evidence. */
const NOTABLE_WIND_KMH = 20;

function num(
  value: number | null | undefined,
): number | null {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : null;
}

function rain24h(weather: ConditionWeatherInput): number | null {
  return (
    num(weather?.recent_rain?.["24h_mm"]) ??
    num(weather?.recent_precipitation?.["24h_mm"])
  );
}

/**
 * Whether any real wet signal exists in the observed evidence. Used only
 * to pick the more specific caution headline; the underlying status still
 * comes from the backend engine.
 */
export function hasWetSignal(
  weather: ConditionWeatherInput,
): boolean {
  if (!weather) {
    return false;
  }
  const recent = rain24h(weather) ?? 0;
  const falling =
    num(weather.current?.precipitation) ??
    num(weather.current?.rain) ??
    0;
  const expected =
    num(weather.forecast?.rain_mm) ??
    num(weather.forecast?.precipitation_mm) ??
    0;
  const probability =
    num(weather.forecast?.precipitation_probability_max) ?? 0;
  return (
    recent > 0 ||
    falling > 0 ||
    expected > 0 ||
    probability >= LIKELY_PROBABILITY_PCT
  );
}

export function conditionHeadline(
  status: ConditionStatusInput,
  weather: ConditionWeatherInput,
): { headline: string; tone: ConditionTone } {
  if (status === "unknown" || !weather) {
    return {
      headline: "Conditions unavailable",
      tone: "neutral",
    };
  }
  if (status === "favorable") {
    return {
      headline: "Mostly favorable conditions",
      tone: "good",
    };
  }
  if (status === "adverse") {
    return {
      headline: "Poor conditions likely",
      tone: "bad",
    };
  }
  return hasWetSignal(weather)
    ? {
        headline: "Wetter conditions likely",
        tone: "warn",
      }
    : {
        headline: "Some caution advised",
        tone: "warn",
      };
}

/**
 * The most useful observed values, most decision-relevant first. Every
 * entry carries a real provider number; missing values are omitted, so a
 * gap in the list means "not reported", never "zero".
 */
export function observedEvidence(
  weather: ConditionWeatherInput,
): ObservedEvidence[] {
  if (!weather) {
    return [];
  }
  const rows: ObservedEvidence[] = [];
  const recent = rain24h(weather);
  if (recent !== null) {
    rows.push({
      label: "Rain · last 24 h",
      value: `${recent.toFixed(1)} mm`,
    });
  }
  const probability = num(
    weather.forecast?.precipitation_probability_max,
  );
  if (probability !== null) {
    rows.push({
      label: "Chance of rain · next 24 h",
      value: `${Math.round(probability)}%`,
    });
  }
  const expected =
    num(weather.forecast?.rain_mm) ??
    num(weather.forecast?.precipitation_mm);
  if (expected !== null) {
    rows.push({
      label: "Rain expected · next 24 h",
      value: `${expected.toFixed(1)} mm`,
    });
  }
  const falling =
    num(weather.current?.precipitation) ??
    num(weather.current?.rain);
  if (falling !== null) {
    rows.push({
      label: "Falling now",
      value: `${falling.toFixed(1)} mm`,
    });
  }
  const temperature = num(weather.current?.temperature);
  if (temperature !== null) {
    rows.push({
      label: "Temperature now",
      value: `${Math.round(temperature)}°C`,
    });
  }
  const snowfall = num(weather.current?.snowfall);
  if (snowfall !== null && snowfall > 0) {
    rows.push({
      label: "Snowfall now",
      value: `${snowfall.toFixed(1)} mm`,
    });
  }
  const wind = num(weather.current?.wind_speed);
  if (wind !== null && wind >= NOTABLE_WIND_KMH) {
    rows.push({
      label: "Wind now",
      value: `${Math.round(wind)} km/h`,
    });
  }
  return rows;
}
