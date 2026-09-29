/**
 * Presentation helpers for the suitability card.
 *
 * The backend suitability engine stays the authority: level, headline,
 * factors and assessment scope all come from the intelligence response
 * untouched. Nothing here recomputes conditions, difficulty, complexity
 * or gear, and no new ranking or scoring lives in the frontend.
 *
 * Two display decisions live here, both presentation-only:
 *
 *   - the human headline for a backend level. Raw tokens such as
 *     `currently_unfavorable` or `insufficient_data` are never shown;
 *     `insufficient_data` is worded so missing evidence is never read
 *     as "unsafe".
 *   - which factors are decisive: the backend marks strong route demand
 *     (`high` / `very_high`) and current-condition factors, and at most
 *     three are shown, in backend order.
 */
export type SuitabilityLevelInput =
  | "suitable_now"
  | "demanding"
  | "caution"
  | "currently_unfavorable"
  | "insufficient_data"
  | string;

export type SuitabilityTone = "good" | "warn" | "bad" | "neutral";

export type SuitabilityFactorInput = {
  factor: string;
  level: string;
  category?: string;
  evidence: string;
};

export function suitabilityHeadline(
  level: SuitabilityLevelInput,
  // "walk" when the backend assessed the forecast over the estimated walking
  // time; anything else is the current reading. The wording follows the data,
  // it is never guessed.
  assessedOver?: string,
): { headline: string; tone: SuitabilityTone } {
  const walk = assessedOver === "walk";
  switch (level) {
    case "suitable_now":
      return {
        headline: walk ? "Suitable for this walk" : "Suitable right now",
        tone: "good",
      };
    case "demanding":
      return {
        headline: "Demanding route",
        tone: "neutral",
      };
    case "caution":
      return {
        headline: walk ? "Use caution on this walk" : "Use caution right now",
        tone: "warn",
      };
    case "currently_unfavorable":
      return {
        headline: walk
          ? "Not suitable for this walk"
          : "Not suitable right now",
        tone: "bad",
      };
    case "insufficient_data":
      return {
        headline: "Not enough current evidence",
        tone: "neutral",
      };
    default:
      return {
        headline: "Suitability unclear",
        tone: "neutral",
      };
  }
}

/** At most three decisive factors, in backend order. */
export function selectDecisiveFactors(
  factors: SuitabilityFactorInput[] | null | undefined,
): SuitabilityFactorInput[] {
  if (!Array.isArray(factors)) {
    return [];
  }
  return factors
    .filter(
      (factor) =>
        factor.level === "high" ||
        factor.level === "very_high" ||
        factor.category === "current_condition",
    )
    .slice(0, 3);
}
