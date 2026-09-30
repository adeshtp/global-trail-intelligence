/**
 * Presentation mapping from the difficulty system's own results to the
 * product's user-facing difficulty vocabulary: Easy / Moderate / Hard /
 * Very Hard.
 *
 * This is display only. The model, its three native tiers and every
 * recorded OSM grade are untouched; nothing here retrains, re-scores or
 * invents a difficulty class. The native tier and the recorded grade stay
 * available as provenance.
 *
 * Two sources feed the same product vocabulary:
 *
 *   1. A recorded OpenStreetMap `sac_scale` grade. Each of the seven real
 *      grades already has a product label, used verbatim.
 *   2. The learned estimate, whose native tier is one of
 *      `walking` / `mountain` / `alpine`. A tier is a band of recorded
 *      grades, so each tier maps to the HIGHEST product difficulty any
 *      grade in that band carries. That rule is deterministic, and it
 *      deliberately never understates: an estimate shown as easier than
 *      the hardest grade it can represent is the one error a hiker cannot
 *      recover from. The covered band is published alongside the label so
 *      the conservative choice is visible rather than implied.
 */
export type ProductDifficulty =
  | "Easy"
  | "Moderate"
  | "Hard"
  | "Very Hard";

export type NativeTier = "walking" | "mountain" | "alpine";

/** Recorded OSM grades to the product vocabulary. */
const GRADE_TO_PRODUCT: Record<string, ProductDifficulty> = {
  strolling: "Easy",
  hiking: "Easy",
  mountain_hiking: "Moderate",
  demanding_mountain_hiking: "Hard",
  alpine_hiking: "Very Hard",
  demanding_alpine_hiking: "Very Hard",
  difficult_alpine_hiking: "Very Hard",
};

/** Recorded grades each native tier is trained to represent. */
const TIER_GRADES: Record<NativeTier, string[]> = {
  walking: ["strolling", "hiking"],
  mountain: ["mountain_hiking", "demanding_mountain_hiking"],
  alpine: [
    "alpine_hiking",
    "demanding_alpine_hiking",
    "difficult_alpine_hiking",
  ],
};

const ORDER: ProductDifficulty[] = [
  "Easy",
  "Moderate",
  "Hard",
  "Very Hard",
];

function rank(value: ProductDifficulty): number {
  return ORDER.indexOf(value);
}

/** The recorded grades that make up one product label, for filtering. */
export function gradesForProduct(label: ProductDifficulty): string[] {
  return Object.entries(GRADE_TO_PRODUCT)
    .filter(([, product]) => product === label)
    .map(([grade]) => grade);
}

/** Product label for a recorded OSM grade, or null when unrecognised. */
export function productDifficultyForGrade(
  grade: string | null | undefined,
): ProductDifficulty | null {
  if (!grade) {
    return null;
  }
  const key = grade.toLowerCase().replaceAll(" ", "_");
  return GRADE_TO_PRODUCT[key] ?? null;
}

/**
 * Product label for a native model tier: the highest product difficulty
 * among the recorded grades that tier represents.
 */
export function productDifficultyForTier(
  tier: string | null | undefined,
): ProductDifficulty | null {
  if (!tier) {
    return null;
  }
  const grades = TIER_GRADES[tier as NativeTier];
  if (!grades) {
    return null;
  }
  return grades.reduce<ProductDifficulty>((highest, grade) => {
    const label = GRADE_TO_PRODUCT[grade];
    return rank(label) > rank(highest) ? label : highest;
  }, "Easy");
}

export type DifficultyPresentation = {
  /** The product vocabulary label shown as the headline. */
  label: ProductDifficulty | null;
  /** True when the label comes from a recorded grade, not the model. */
  isOfficial: boolean;
  /** Native model tier, kept for provenance. Null for an official grade. */
  nativeTier: string | null;
  /** Recorded grade this label came from, verbatim. */
  recordedGrade: string | null;
  /** The product range the native tier represents, when estimated. */
  estimatedRange: string | null;
  /** Short line stating where the number came from. */
  provenance: string;
  /** Short line stating what the value is not. */
  qualification: string;
};

/**
 * Build the difficulty presentation for one selected trail.
 *
 * A recorded grade always wins and is never blended with the estimate,
 * so the interface never shows two competing difficulty labels. When no
 * grade is recorded the estimate is used, still labeled as an estimate.
 */
export function difficultyPresentation(input: {
  recordedGrade: string | null;
  officialTier: string | null;
  estimateTier: string | null;
  estimateAvailable: boolean;
}): DifficultyPresentation {
  const official = productDifficultyForGrade(input.recordedGrade);
  const estimate = input.estimateAvailable
    ? productDifficultyForTier(input.estimateTier)
    : null;

  if (official) {
    return {
      label: official,
      isOfficial: true,
      nativeTier: input.officialTier,
      recordedGrade: input.recordedGrade,
      estimatedRange: null,
      provenance: "Official difficulty, recorded in OpenStreetMap.",
      qualification: "",
    };
  }

  if (estimate) {
    const grades = TIER_GRADES[input.estimateTier as NativeTier] ?? [];
    const covered = grades
      .map((grade) => GRADE_TO_PRODUCT[grade])
      .filter(
        (value, index, all) =>
          all.indexOf(value) === index,
      )
      .join("–");
    return {
      label: estimate,
      isOfficial: false,
      nativeTier: input.estimateTier,
      recordedGrade: null,
      estimatedRange:
        covered.split("–").length > 1 ? covered : null,
      provenance: "Model-estimated difficulty.",
      qualification: "Not an official trail rating.",
    };
  }

  return {
    label: null,
    isOfficial: false,
    nativeTier: input.estimateTier,
    recordedGrade: input.recordedGrade,
    estimatedRange: null,
    provenance: "Difficulty",
    qualification:
      "No recorded grade and not enough evidence to estimate one.",
  };
}
