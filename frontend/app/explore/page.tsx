"use client";

import {
  type FormEvent,
  Suspense,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  useSearchParams,
} from "next/navigation";

import ExploreSearch from "@/components/ExploreSearch";

import TrailSidebar, {
  difficultyLabel,
  Trail,
} from "@/components/TrailSidebar";

import CesiumMap from "@/components/CesiumMap";
import {
  nearestDisplayPointIndex,
  orientDisplayProfile,
  tooltipSide,
} from "@/components/elevationDisplay";
import {
  conditionHeadline,
  observedEvidence,
} from "@/components/conditionDisplay";
import { difficultyPresentation } from "@/components/difficultyDisplay";
import {
  selectDecisiveFactors,
  suitabilityHeadline,
} from "@/components/suitabilityDisplay";


const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/*
 * Bound for one discovery fetch. Fallback-served queries can legitimately
 * take minutes, so this is generous; without it a silently hung connection
 * leaves the loading state on forever with no error shown.
 */
const DISCOVERY_TIMEOUT_MS = 300000;
const INTELLIGENCE_TIMEOUT_MS = 240000;
const PRODUCTS_TIMEOUT_MS = 180000;
const ASSISTANT_TIMEOUT_MS = 120000;

/*
 * Bound a fetch controller. Returns whether this bound fired and a cleanup.
 * Callers keep treating supersede-aborts as silent (a newer request won)
 * and report only their own timeout while still current, so a hung provider
 * surfaces as an error instead of an eternal spinner.
 */
function abortAfter(
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


type Location = {
  latitude: number;
  longitude: number;
};


type SelectedTrail = Omit<
  Trail,
  "map_ready" | "geometry" | "distance_km" | "state"
> & {
  map_ready: true;
  state: "MAP_READY";
  distance_km: number;
  geometry: NonNullable<Trail["geometry"]>;
};


type TrailGeometry = SelectedTrail;


type MapTrail = {
  trail_id: string;
  osm_id: number | null;
  osm_type: "way" | "relation" | "component" | null;
  name: string | null;
  geometry: NonNullable<Trail["geometry"]>;
};


/**
 * Render a missing-evidence key as a sentence a person can read.
 *
 * The backend reports machine keys such as "recorded surface" or
 * "condition likelihood". Showing them raw produces developer wording in the
 * middle of the user-facing page, so they are translated here rather than
 * leaked.
 */
const MISSING_EVIDENCE_LABELS: Record<
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


function missingEvidenceText(
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

type TrailDiscoveryResponse = {
  status?:
    | "success"
    | "partial"
    | "empty"
    | "no_provider_data"
    | "unavailable";
  trails?: Trail[];
  mapped_count?: number;
  returned_mapped_count?: number;
  mapped_truncated_count?: number;
  unmapped_count?: number;
  enrichment_pending?: boolean;
  result_counts?: {
    definition: string;
    relevance_accepted: number;
    mapped: number;
    unmapped: number;
    ranked: number;
    shown: number;
    shown_unmapped: number;
    weak_evidence: number;
    mapped_truncated: number;
  };
  peak_search?: {
    is_peak_search: boolean;
    summit_note: string | null;
    summit_routes: number;
    approach_routes: number;
    nearby_routes: number;
    method: string;
  } | null;
  pagination?: {
    page: number;
    page_size: number;
    total_ranked: number;
    returned: number;
    has_more: boolean;
    next_page: number | null;
    unmapped_returned: number;
    note?: string;
  };
  coverage?: {
    area_considered?: number[];
    area_considered_source?: string;
    area_km2?: number;
    area_queried?: boolean;
    tiled?: boolean;
    tile_grid?: string | null;
    tiles_total?: number;
    tiles_queried?: number;
    tiles_failed?: number;
    tiles_skipped?: number;
    coverage_complete?: boolean;
    provider_returned_no_rows?: boolean;
    candidates_found?: number;
    candidates_accepted?: number;
    candidates_verified?: number;
    candidates_ranked?: number;
    candidates_returned?: number;
    results_truncated?: number;
    note?: string;
  };
  providers?: Record<string, {
    status?: string;
    message?: string | null;
  }>;
};


type WeatherCurrent = {
  time: string | null;
  temperature: number | null;
  humidity: number | null;
  precipitation: number | null;
  rain: number | null;
  showers: number | null;
  snowfall: number | null;
  precipitation_probability: number | null;
  wind_speed: number | null;
  weather_code: number | null;
  weather_condition: string;
};


type WeatherResponse = {
  source: string;
  latitude: number;
  longitude: number;
  timezone: string;
  current: WeatherCurrent;
  recent_precipitation?: {
    "24h_mm"?: number | null;
    "48h_mm"?: number | null;
    "72h_mm"?: number | null;
  };
  recent_rain?: {
    "24h_mm"?: number | null;
    "48h_mm"?: number | null;
    "72h_mm"?: number | null;
  };
  forecast?: {
    precipitation_mm?: number | null;
    rain_mm?: number | null;
    precipitation_probability_max?: number | null;
  };
};


type SelectedTrailAnalysis = {
  trail_id?: string;
  geometry?: NonNullable<Trail["geometry"]>;
  geometry_type: "LineString" | "MultiLineString";
  geometry_status: "connected" | "fragmented";
  component_count: number;
  coordinate_count: number;
  distance_km: number;
  analysis_distance_km: number;
  start_coordinate: [number, number];
  end_coordinate: [number, number];
  midpoint_coordinate: [number, number];
  bbox: [number, number, number, number];
  selected_geometry_hash?: string | null;
  normalized_geometry_hash?: string;
  geometry_provenance?: string | null;
  relation_completeness?: {
    member_count: number;
    network: string | null;
    route: string | null;
    state: string;
    note: string | null;
    source_length_km: number | null;
    basis: string;
  };
};


type ElevationProfilePoint = {
  component_index: number;
  component_distance_km: number;
  distance_km: number;
  longitude: number;
  latitude: number;
  elevation_m: number | null;
};


/**
 * Split a sampled elevation profile into its separate route components.
 *
 * The backend samples each disconnected component of the geometry and tags
 * every point with the component it came from, so the split is read from the
 * data rather than inferred from a distance jump. Components with fewer than
 * two usable samples cannot be drawn and are dropped.
 */
function groupProfileComponents<
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


type ElevationMetrics = {
  min_elevation_m: number | null;
  max_elevation_m: number | null;
  elevation_range_m: number | null;
  elevation_gain_m: number | null;
  elevation_loss_m: number | null;
  average_slope_percent: number | null;
  max_slope_percent: number | null;
  terrain_available: boolean;
};


type ElevationResponse = {
  source: string;
  sampled_points: number;
  component_count: number;
  sampled_component_count: number;
  all_components_covered: boolean;
  route_distance_km: number;
  profile: ElevationProfilePoint[];
  metrics: ElevationMetrics;
  /**
   * Presentation-only direction decision from the backend. The profile
   * above always stays in mapped geometry order; a low-to-high chart is
   * produced client-side from these fields, never by reordering data.
   * Absent on older responses, which are always rendered as mapped.
   */
  profile_orientation?: string;
  reversal_needed?: boolean;
  orientation_basis?: {
    start_elevation_m: number;
    end_elevation_m: number;
    endpoint_difference_m: number;
  } | null;
  direction_note?: string;
};

type TrailIntelligenceResponse = {
  generated_at: string;
  trail: Partial<Trail>;
  /**
   * Where the weather was actually measured. The search place is NOT used
   * when a route is selected: this is a representative point on the verified
   * geometry, and the basis string states how it was chosen. Shown in the
   * conditions section so the numbers can be located rather than trusted.
   */
  weather_coordinate?: {
    latitude: number;
    longitude: number;
    basis: string;
  };
  analysis: SelectedTrailAnalysis;
  terrain: ElevationResponse | null;
  weather: WeatherResponse | null;
  difficulty: {
    source: {
      sac_scale: string | null;
      /** The difficulty tier the recorded grade falls into. */
      tier: string | null;
      class: string | null;
      label: string | null;
      mapping: string;
      authoritative: boolean;
    };
    ml: {
      available: boolean;
      /** The estimated difficulty tier, e.g. "mountain". */
      estimate: string | null;
      estimate_tier: string | null;
      /** Plain-language name for a person, e.g. "Mountain trail". */
      estimate_label: string | null;
      estimate_index: number | null;
      /**
       * The recorded OSM grades this tier is drawn from. Published so an
       * estimate and an official value can be compared in one vocabulary.
       */
      estimate_recorded_grade: Record<string, string> | null;
      estimate_description: string | null;
      confidence: number | null;
      confidence_kind: string | null;
      probabilities: Record<string, number> | null;
      feature_coverage: number;
      missing_features: string[];
      model_version: string | null;
      model_name: string | null;
      feature_contract: string;
      observation_unit: string;
      terrain_features_used: boolean;
      authoritative_for_complete_route: boolean;
      /**
       * How much the estimate can be worth, measured on geographically
       * held-out real OpenStreetMap ways scored under the real grade
       * distribution - the mix a user actually meets. Always present, whether
       * or not a prediction was produced, so the page never has to guess
       * which shape it received.
       */
      reliability?: {
        held_out_accuracy: number | null;
        held_out_macro_f1: number | null;
        held_out_balanced_accuracy: number | null;
        majority_baseline_accuracy: number | null;
        majority_baseline_macro_f1: number | null;
        beats_majority_baseline: boolean | null;
        per_class: Record<
          string,
          {
            precision: number;
            recall: number;
            f1: number;
          }
        >;
        natural_sample_rows: number | null;
        natural_sample_accuracy: number | null;
        natural_sample_macro_f1: number | null;
        summary: string | null;
      };
      prediction_basis?: string | null;
      reason: string | null;
      /**
       * Present only for route selections (relations, components): the
       * member-way vote behind the estimate. Each member is scored with
       * the same way-level model; the route answer is the majority tier
       * and the full split is published so disagreement stays visible.
       */
      aggregation?: {
        scored_members: number;
        total_members: number;
        truncated_to_first_members: boolean;
        tier_counts: Record<string, number>;
        agreement: number;
        members_disagree: boolean;
      } | null;
    };
    reconciliation?: {
      authoritative_tier: string | null;
      authoritative_class: string | null;
      authoritative_grade: string | null;
      authoritative_source: string | null;
      ml_estimate: string | null;
      ml_estimate_class: string | null;
      ml_agrees_with_official: boolean | null;
      status:
        | "official_scale_available"
        | "superseded_by_official_scale"
        | "model_estimate_only"
        | "unavailable";
      message: string;
    };
    display: string | null;
    display_provenance: string;
    model_readiness: {
      ready: boolean;
      model_name?: string | null;
      model_version?: string | null;
      task?: string | null;
      classes?: string[] | null;
      observation_unit?: string;
      terrain_features_used_at_runtime?: boolean;
      authoritative_for_complete_route?: boolean;
      evaluation?: Record<string, number> | null;
      limitations?: string | null;
      /**
       * Why the score is what it is. `label_collision` is the measurement
       * behind the statement that the recorded grade is not recoverable from
       * the recorded tags, so the caveat shown to a user rests on a number
       * rather than on an assertion.
       */
      /** Plain-language names for each difficulty tier. */
      tier_labels?: Record<string, string> | null;
      /** The recorded OSM grade each tier is drawn from. */
      grade_to_tier?: Record<string, string> | null;
      message: string | null;
    };
  };
  condition: {
    available: boolean;
    status: "favorable" | "caution" | "adverse" | "unknown";
    likelihood: string;
    score: number | null;
    observation_type: "inference";
    summary: string;
    evidence: string[];
    missing_evidence: string[];
    source?: string | null;
    observed_at?: string | null;
    factors?: Array<{
      factor: string;
      state: string;
      detail: string;
      weight: number;
    }>;
  };
  suitability: {
    level: string;
    headline: string;
    condition_status: string;
    route_complexity_score: number;
    assessment_scope: string;
    factors: Array<{
      factor: string;
      level: string;
      category?: string;
      evidence: string;
    }>;
  };
  route_complexity?: {
    available: boolean;
    score: number | null;
    label: string;
    observation_unit: string;
    method: string;
    components: Array<{
      component: string;
      normalised: number | null;
      weight: number;
      measured: string;
    }>;
    missing_evidence: string[];
  };
  gear: {
    items: Array<{
      need: string;
      category: string;
      group?: "equipment" | "supplies" | "preparation";
      priority: "essential" | "recommended" | "conditional";
      item: string;
      reason: string;
      evidence?: string[];
    }>;
    groups?: {
      equipment: number;
      supplies: number;
      preparation: number;
    };
    essential_count: number;
    recommended_count: number;
    conditional_count: number;
    basis: string[];
    missing_evidence: string[];
  };
  providers: Record<string, "ok" | "unavailable">;
};

type ProductSearchResponse = {
  status: "ok" | "search_link" | "unavailable";
  provider: string;
  groups: Array<{
    category: string;
    item: string;
    priority: "essential" | "recommended" | "conditional";
    reason: string;
    query: string;
    status: "ok" | "search_link" | "unavailable";
    product_results: Array<{
      title: string;
      display_title?: string;
      retailer?: string;
      editorial?: boolean;
      off_topic?: boolean;
      url: string;
      source?: string;
      image?: string;
      snippet?: string;
    }>;
    related_results: Array<{
      title: string;
      display_title?: string;
      retailer?: string;
      url: string;
      source?: string;
      image?: string;
      snippet?: string;
    }>;
    rejected_results?: Array<{
      title: string;
      url: string;
      reason: string;
    }>;
    search_link: {
      title: string;
      url: string;
      source: string;
    };
    result_source?: "live_product_search" | "search_link" | "provider_unavailable";
    image_available?: boolean;
    message?: string | null;
    /**
     * Exactly one shopping card per gear item. Either a real product page
     * with the image the provider attached to that same result, or an
     * honest category card carrying the gear item, a category picture and a
     * real destination. The distinction is internal; the card simply says
     * "View product" or "Shop options".
     */
    card?: {
      mode: "direct_product" | "shopping_fallback";
      gear_item: string;
      name: string;
      image: string | null;
      retailer: string | null;
      url: string;
      cta: string;
      description: string | null;
    };
  }>;
  message?: string | null;
};

/**
 * One shopping card for one gear requirement.
 *
 * The card arrives already decided by the backend and is rendered as
 * given, so the interface never re-classifies anything:
 *
 *   direct_product    name, picture and link all come from one real
 *                     result the provider returned
 *   shopping_fallback a category card: the gear item the route asked for,
 *                     a category picture, and a real destination
 *
 * On a fallback card the picture represents the CATEGORY, which is exactly
 * what the card claims to be, so it is never a product photo standing in
 * for a product. No price, rating or stock is shown: the source does not
 * return them and they are never invented.
 *
 * `referrerPolicy="no-referrer"` matters because retailer CDNs commonly
 * refuse hotlinked images when the referrer is another site.
 */
function ProductCard({
  card,
}: {
  card: NonNullable<
    ProductSearchResponse["groups"][number]["card"]
  >;
}) {
  const [imageBroken, setImageBroken] =
    useState(false);

  const isProduct = card.mode === "direct_product";
  const showImage = Boolean(card.image) && !imageBroken;

  return (
    <a
      href={card.url}
      target="_blank"
      rel="noreferrer"
      className="group flex flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02] transition hover:border-emerald-300/35 hover:bg-white/[0.04]"
    >
      {showImage ? (
        // A fixed aspect-ratio area with `object-contain`, not a fixed
        // height with `object-cover`. Providers return whatever shape the
        // source has - square product shots, wide category banners, tall
        // portraits - and `cover` cropped roughly 40% off a square image at
        // this card width. Containing the image keeps the whole product
        // visible and cannot distort it. The small padding means the
        // leftover space reads as deliberate letterbox rather than a
        // cropping bug.
        <div className="flex aspect-[4/3] w-full items-center justify-center bg-white/[0.03] p-1.5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={card.image ?? undefined}
            alt={
              isProduct
                ? card.name
                : `${card.gear_item} — category image`
            }
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setImageBroken(true)}
            className="max-h-full max-w-full rounded-lg object-contain"
          />
        </div>
      ) : (
        // Same box as the image case so cards with and without a photo keep
        // a consistent height in the grid row.
        <div className="flex aspect-[4/3] w-full items-center justify-center bg-white/[0.03] p-3 text-[10px] text-white/25">
          {card.gear_item}
        </div>
      )}

      <div className="flex flex-1 flex-col p-3">
        <p className="line-clamp-2 text-[12px] font-semibold leading-4 text-white/85">
          {card.name}
        </p>

        {card.retailer ? (
          <p className="mt-1 text-[10px] text-white/35">
            {card.retailer}
          </p>
        ) : null}

        {card.description ? (
          <p className="mt-1.5 line-clamp-2 text-[10px] leading-4 text-white/30">
            {card.description}
          </p>
        ) : null}

        <p className="mt-auto pt-2 text-[10px] font-medium text-emerald-200/70 group-hover:text-emerald-200">
          {card.cta} ↗
        </p>
      </div>
    </a>
  );
}

type AssistantResponse = {
  status:
    | "ok"
    | "grounded_local"
    | "not_in_context"
    | "unavailable"
    | "invalid";
  answer: string;
  grounded: boolean;
  model?: string;
  generated_by?: "local_retrieval" | "language_model";
  provider_note?: string;
  retrieved_sources?: string[];
  corpus_size?: number;
};


/**
 * Trail elevation profile.
 *
 * Horizontal axis is distance along the selected route, vertical axis is
 * elevation in metres, with real axis ticks. Elevation figures deliberately
 * do NOT appear on the distance axis, which previously made the chart read
 * as unrelated bars.
 *
 * Each disconnected component of the route is drawn as its own polyline. A
 * route whose mapped geometry is several separate pieces would otherwise be
 * drawn as one continuous line bridging the gaps, which asserts a climb and a
 * distance that do not exist on the ground. The horizontal axis is the real
 * cumulative distance, so the pieces sit in the right order with the true gap
 * between them, and nothing is joined across it.
 */
function ElevationProfile({
  profile,
  metrics,
  orientation,
}: {
  profile: ElevationProfilePoint[];
  metrics: ElevationMetrics;
  orientation?: {
    profile_orientation?: string;
    reversal_needed?: boolean;
  } | null;
}) {
  const width = 640;
  const height = 190;
  const padLeft = 46;
  const padRight = 10;
  const padTop = 10;
  const padBottom = 22;

  /*
   * Hover readout state: an index into the displayed (possibly
   * low-to-high oriented) points, or null when the pointer is off the
   * chart. Declared before any early return so hook order is stable.
   */
  const [hoverIndex, setHover] = useState<number | null>(null);

  const points = profile
    .filter(
      (point): point is ElevationProfilePoint & {
        elevation_m: number;
      } =>
        typeof point.elevation_m === "number" &&
        point.elevation_m !== null
    )
    .slice(0, 600);

  const components = groupProfileComponents(points);

  if (points.length < 2 || components.length === 0) {
    return (
      <p className="mt-5 text-[11px] text-white/30">
        Not enough elevation samples to draw a profile.
      </p>
    );
  }

  /*
   * Presentation-only orientation. The points above stay in mapped order;
   * when the backend reports strong low-to-high endpoint evidence for a
   * single-piece route, display copies run from the lower end instead.
   * The route geometry and the stored profile are never reordered.
   */
  const oriented = orientDisplayProfile(
    points,
    points[points.length - 1].distance_km || 1,
    metrics.elevation_gain_m,
    metrics.elevation_loss_m,
    orientation ?? null,
    components.length === 1
  );
  const displayPoints = oriented.displayPoints;
  const drawComponents = groupProfileComponents(displayPoints);

  const maxDistance =
    displayPoints[displayPoints.length - 1].distance_km || 1;
  const elevations = displayPoints.map((point) => point.elevation_m);
  let minElevation = Math.min(...elevations);
  let maxElevation = Math.max(...elevations);
  if (maxElevation - minElevation < 1) {
    maxElevation = minElevation + 1;
  }
  const padElevation = (maxElevation - minElevation) * 0.12;
  minElevation -= padElevation;
  maxElevation += padElevation;

  const innerWidth = width - padLeft - padRight;
  const innerHeight = height - padTop - padBottom;

  const x = (distance: number) =>
    padLeft + (distance / (maxDistance || 1)) * innerWidth;
  const y = (elevation: number) =>
    padTop +
    innerHeight -
    ((elevation - minElevation) /
      (maxElevation - minElevation || 1)) *
      innerHeight;

  const baselineY = padTop + innerHeight;

  // One path per component. Nothing is drawn between the last point of one
  // component and the first point of the next.
  const componentPaths = drawComponents.map((component) => {
    const line = component
      .map(
        (point, index) =>
          `${index === 0 ? "M" : "L"}${x(
            point.distance_km
          ).toFixed(1)},${y(point.elevation_m).toFixed(1)}`
      )
      .join(" ");
    const area = `${line} L${x(
      component[component.length - 1].distance_km
    ).toFixed(1)},${baselineY.toFixed(1)} L${x(
      component[0].distance_km
    ).toFixed(1)},${baselineY.toFixed(1)} Z`;
    return { line, area };
  });

  /*
   * Axis ticks share the data scale: fraction 0 is the minimum elevation
   * at the bottom of the plot, fraction 1 the maximum at the top.
   */
  const elevationTicks = [0, 0.5, 1].map((fraction) => ({
    value:
      minElevation +
      (maxElevation - minElevation) * fraction,
    y: padTop + innerHeight * (1 - fraction),
  }));
  const distanceTicks = [0, 0.25, 0.5, 0.75, 1].map(
    (fraction, tickIndex, allTicks) => ({
      value: maxDistance * fraction,
      x: padLeft + innerWidth * fraction,
      anchor:
        tickIndex === 0
          ? "start"
          : tickIndex === allTicks.length - 1
            ? "end"
            : "middle",
    }),
  );

  const gain = oriented.displayGain;
  const loss = oriented.displayLoss;

  /*
   * Hover readout. The tooltip always snaps to a real sampled point, so
   * both values describe one actual sample: its cumulative distance and
   * its elevation, plus which disconnected piece it belongs to. Nothing
   * is interpolated, and nothing bridges a gap between pieces.
   */
  const hoverPoint =
    hoverIndex !== null
      ? (displayPoints[hoverIndex] ?? null)
      : null;
  const hoverX =
    hoverPoint !== null ? x(hoverPoint.distance_km) : 0;
  const hoverY =
    hoverPoint !== null ? y(hoverPoint.elevation_m) : 0;
  const hoverPieceIndex =
    hoverPoint !== null && drawComponents.length > 1
      ? drawComponents.findIndex(
          (component) =>
            component.length > 0 &&
            component[0].component_index ===
              hoverPoint.component_index
        )
      : -1;
  const hoverPiece =
    hoverPieceIndex >= 0 ? hoverPieceIndex + 1 : null;
  const hoverLines =
    hoverPoint !== null
      ? [
          `${hoverPoint.distance_km.toFixed(2)} km`,
          `${Math.round(hoverPoint.elevation_m).toLocaleString("en-US")} m`,
          ...(hoverPiece !== null
            ? [`Piece ${hoverPiece} of ${drawComponents.length}`]
            : []),
        ]
      : [];
  const tooltipBoxWidth = 132;
  const tooltipBoxHeight = 14 + hoverLines.length * 15;
  const tooltipBoxX =
    hoverPoint !== null
      ? Math.min(
          Math.max(
            tooltipSide(hoverX, width - padRight, tooltipBoxWidth) ===
              "right"
              ? hoverX + 10
              : hoverX - 10 - tooltipBoxWidth,
            padLeft
          ),
          width - padRight - tooltipBoxWidth
        )
      : 0;
  const tooltipBoxY =
    hoverPoint !== null
      ? Math.min(
          Math.max(hoverY - tooltipBoxHeight - 10, padTop),
          baselineY - tooltipBoxHeight
        )
      : 0;

  return (
    <div className="mt-5">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="Elevation profile: horizontal axis is distance along the route, vertical axis is elevation in metres"
      >
        <defs>
          <linearGradient
            id="terrainFill"
            x1="0"
            y1="0"
            x2="0"
            y2="1"
          >
            <stop
              offset="0%"
              stopColor="#34d399"
              stopOpacity="0.45"
            />
            <stop
              offset="100%"
              stopColor="#34d399"
              stopOpacity="0.05"
            />
          </linearGradient>
        </defs>

        {elevationTicks.map((tick) => (
          <g key={`e-${tick.value.toFixed(0)}`}>
            <line
              x1={padLeft}
              x2={width - padRight}
              y1={tick.y}
              y2={tick.y}
              stroke="rgba(255,255,255,0.07)"
              strokeWidth={1}
            />
            <text
              x={padLeft - 6}
              y={tick.y + 3}
              textAnchor="end"
              className="fill-white/35"
              style={{ fontSize: 9 }}
            >
              {Math.round(tick.value)} m
            </text>
          </g>
        ))}

        {componentPaths.map((path, index) => (
          <g key={`c-${index}`}>
            <path d={path.area} fill="url(#terrainFill)" />
            <path
              d={path.line}
              fill="none"
              stroke="#6ee7b7"
              strokeWidth={1.8}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </g>
        ))}

        {distanceTicks.map((tick) => (
          <text
            key={`d-${tick.value.toFixed(2)}`}
            x={tick.x}
            y={height - 6}
            textAnchor={tick.anchor as "start" | "middle" | "end"}
            className="fill-white/35"
            style={{ fontSize: 9 }}
          >
            {tick.value.toFixed(1)} km
          </text>
        ))}

        {hoverPoint !== null ? (
          <g>
            <line
              x1={hoverX}
              x2={hoverX}
              y1={padTop}
              y2={baselineY}
              stroke="rgba(255,255,255,0.35)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <circle
              cx={hoverX}
              cy={hoverY}
              r={4}
              fill="#0b1724"
              stroke="#6ee7b7"
              strokeWidth={2}
            />
            <rect
              x={tooltipBoxX}
              y={tooltipBoxY}
              width={tooltipBoxWidth}
              height={tooltipBoxHeight}
              rx={8}
              fill="#0b1724"
              fillOpacity={0.94}
              stroke="rgba(255,255,255,0.18)"
              strokeWidth={1}
            />
            {hoverLines.map((line, lineIndex) => (
              <text
                key={`h-${lineIndex}`}
                x={tooltipBoxX + 10}
                y={tooltipBoxY + 17 + lineIndex * 15}
                className="fill-white/90"
                style={{ fontSize: 11, fontWeight: 600 }}
              >
                {line}
              </text>
            ))}
          </g>
        ) : null}

        <rect
          x={padLeft}
          y={padTop}
          width={innerWidth}
          height={innerHeight}
          fill="transparent"
          style={{ cursor: "crosshair" }}
          onPointerMove={(event) => {
            const svg = event.currentTarget.ownerSVGElement;
            if (!svg) {
              return;
            }
            const rect = svg.getBoundingClientRect();
            if (rect.width <= 0) {
              return;
            }
            const svgX =
              (event.clientX - rect.left) * (width / rect.width);
            const target =
              ((svgX - padLeft) / innerWidth) * maxDistance;
            setHover(nearestDisplayPointIndex(displayPoints, target));
          }}
          onPointerLeave={() => setHover(null)}
        />
      </svg>

      {drawComponents.length > 1 ? (
        <p className="mt-2 text-[10px] leading-4 text-white/30">
          {drawComponents.length} disconnected pieces are drawn
          separately. The gaps between them are not distance, and
          no climb is counted across them.
        </p>
      ) : null}

      <p className="mt-2 text-[10px] leading-4 text-white/30">
        {oriented.directionNote}
      </p>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {(
          [
            [
              "Distance",
              `${maxDistance.toFixed(2)} km`,
            ],
            [
              "Ascent",
              gain !== null && gain !== undefined
                ? `${Math.round(gain)} m`
                : "Not available",
            ],
            [
              "Descent",
              loss !== null && loss !== undefined
                ? `${Math.round(loss)} m`
                : "Not available",
            ],
            [
              "Elevation range",
              metrics.elevation_range_m !== null &&
              metrics.elevation_range_m !== undefined
                ? `${Math.round(metrics.elevation_range_m)} m`
                : "Not available",
            ],
          ] as const
        ).map(
          ([label, value]) => (
            <div
              key={label}
              className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2"
            >
              <p className="text-[9px] uppercase tracking-[0.12em] text-white/35">
                {label}
              </p>
              <p className="mt-0.5 text-[13px] font-semibold text-white/80">
                {value}
              </p>
            </div>
          )
        )}
      </div>
    </div>
  );
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
function mappedLengthLabel(): string {
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
function suggestedQuestions(
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
function weatherPointLabel(
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
const CONDITION_STATE_LABELS: Record<
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

function ExplorePageContent() {

  const searchParams =
    useSearchParams();


  const initialQuery =
    searchParams.get(
      "query"
    ) ??
    searchParams.get(
      "q"
    ) ??
    "";


  const [
    location,
    setLocation,
  ] = useState<Location | null>(
    null
  );


  const [
    locationName,
    setLocationName,
  ] = useState<string | null>(
    null
  );


  const [
    trails,
    setTrails,
  ] = useState<Trail[]>(
    []
  );


  const [
    mapTrails,
    setMapTrails,
  ] = useState<MapTrail[]>(
    []
  );


  const [
    resultCounts,
    setResultCounts,
  ] = useState<
    TrailDiscoveryResponse["result_counts"] | undefined
  >(undefined);

  const [
    coverage,
    setCoverage,
  ] = useState<
    TrailDiscoveryResponse["coverage"] | undefined
  >(undefined);


  const [
    activeSection,
    setActiveSection,
  ] = useState("trail-discovery");


  const [
    peakSearch,
    setPeakSearch,
  ] = useState<
    TrailDiscoveryResponse["peak_search"] | undefined
  >(undefined);

  const [
    pagination,
    setPagination,
  ] = useState<
    TrailDiscoveryResponse["pagination"] | undefined
  >(undefined);

  const [
    loadingMore,
    setLoadingMore,
  ] = useState(false);

  // The most recent search, kept so "load more" continues the same query
  // instead of re-running discovery.
  const lastSearchQueryRef = useRef("");
  const lastSearchNameRef = useRef("");
  const lastSearchBroadRef = useRef(false);
  const lastPlaceKindRef = useRef("area");
  const lastSearchLocationRef = useRef<Location | null>(null);


  const [
    loadingTrails,
    setLoadingTrails,
  ] = useState(false);


  const [
    trailError,
    setTrailError,
  ] = useState<string | null>(
    null
  );


  const [
    enriching,
    setEnriching,
  ] = useState(false);


  const [
    selectedTrailSummary,
    setSelectedTrailSummary,
  ] = useState<Trail | null>(
    null
  );


  const [
    selectedTrailGeometry,
    setSelectedTrailGeometry,
  ] = useState<TrailGeometry | null>(
    null
  );


  const [
    selectedAnalysis,
    setSelectedAnalysis,
  ] = useState<SelectedTrailAnalysis | null>(
    null
  );


  const [
    selectedIntelligence,
    setSelectedIntelligence,
  ] = useState<TrailIntelligenceResponse | null>(
    null
  );


  const [
    mapExpanded,
    setMapExpanded,
  ] = useState(false);

  const discoveryRequestRef = useRef(0);
  const discoveryAbortRef = useRef<AbortController | null>(null);
  const enrichmentAbortRef = useRef<AbortController | null>(null);
  const intelligenceAbortRef = useRef<AbortController | null>(null);
  const productsAbortRef = useRef<AbortController | null>(null);
  const assistantAbortRef = useRef<AbortController | null>(null);


  /*
   * ============================================================
   * WEATHER STATE
   * ============================================================
   */

  const [
    weather,
    setWeather,
  ] = useState<WeatherResponse | null>(
    null
  );


  const [
    loadingWeather,
    setLoadingWeather,
  ] = useState(false);


  const [
    weatherError,
    setWeatherError,
  ] = useState<string | null>(
    null
  );


  /*
   * ============================================================
   * ELEVATION STATE
   * ============================================================
   */

  const [
    elevation,
    setElevation,
  ] = useState<ElevationResponse | null>(
    null
  );


  const [
    loadingElevation,
    setLoadingElevation,
  ] = useState(false);


  const [
    elevationError,
    setElevationError,
  ] = useState<string | null>(
    null
  );


  const [
    productResults,
    setProductResults,
  ] = useState<ProductSearchResponse | null>(
    null
  );


  const [
    productsLoading,
    setProductsLoading,
  ] = useState(false);


  const [
    productsError,
    setProductsError,
  ] = useState<string | null>(
    null
  );


  const [
    assistantQuestion,
    setAssistantQuestion,
  ] = useState("");


  const [
    assistantOpen,
    setAssistantOpen,
  ] = useState(false);

  const assistantDockRef = useRef<HTMLDivElement | null>(null);

  const [
    assistantAnswer,
    setAssistantAnswer,
  ] = useState<AssistantResponse | null>(
    null
  );


  const [
    assistantLoading,
    setAssistantLoading,
  ] = useState(false);


  const [
    assistantError,
    setAssistantError,
  ] = useState<string | null>(
    null
  );


  /*
   * ============================================================
   * TRAIL DISCOVERY
   * ============================================================
   */

  /*
   * Continue through the ranked result set.
   *
   * The backend ranks the complete discovered population and returns one
   * page of it, so continuing is a cheap, cached request rather than a new
   * search. Page 1 is requested without the semantic layer so results paint
   * immediately; later pages skip it because the ranking is identical.
   */
  async function loadMoreTrails() {
    if (
      loadingMore ||
      !pagination?.has_more ||
      pagination.next_page === null
    ) {
      return;
    }

    setLoadingMore(true);
    const nextPage = pagination.next_page;

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/osm/trails/discover?${new URLSearchParams(
          {
            latitude: String(
              lastSearchLocationRef.current?.latitude ?? ""
            ),
            longitude: String(
              lastSearchLocationRef.current?.longitude ?? ""
            ),
            search_query: lastSearchQueryRef.current,
            location_name: lastSearchNameRef.current,
            scope: lastSearchBroadRef.current ? "area" : "local",
            place_kind: lastPlaceKindRef.current,
            page: String(nextPage),
          }
        ).toString()}`,
        {
          cache: "no-store",
          // Paged results come from cache, so a hung connection is a
          // stall rather than a slow provider: bound it tightly.
          signal: AbortSignal.timeout(120000),
        }
      );

      if (!response.ok) {
        throw new Error(String(response.status));
      }

      const data = (await response.json()) as TrailDiscoveryResponse;
      const incoming = Array.isArray(data.trails) ? data.trails : [];

      // Append only ids we have not already shown, so repeated or reordered
      // pages can never duplicate a row in the list.
      setTrails((previous) => {
        const seen = new Set(previous.map((trail) => trail.trail_id));
        return [
          ...previous,
          ...incoming.filter((trail) => !seen.has(trail.trail_id)),
        ];
      });
      setMapTrails((previous) => {
        const seen = new Set(previous.map((trail) => trail.trail_id));
        const additions: MapTrail[] = incoming
          .filter(
            (trail) =>
              trail.map_ready &&
              trail.geometry &&
              !seen.has(trail.trail_id)
          )
          .map((trail) => ({
            trail_id: trail.trail_id,
            osm_id: trail.osm_id,
            osm_type: trail.osm_type,
            name: trail.name,
            geometry: trail.geometry!,
          }));
        return [...previous, ...additions];
      });
      setPagination(data.pagination);
      setPeakSearch(data.peak_search ?? undefined);
    } catch {
      setTrailError(
        "More results could not be loaded. The results already shown are unaffected."
      );
    } finally {
      setLoadingMore(false);
    }
  }

  async function discoverTrails(
    newLocation: Location,
    searchQuery: string,
    newLocationName: string,
    broadAreaSearch: boolean,
    searchBounds: [number, number, number, number] | null,
    placeKind = "area"
  ) {
    const requestId = ++discoveryRequestRef.current;
    discoveryAbortRef.current?.abort();
    enrichmentAbortRef.current?.abort();
    const controller = new AbortController();
    discoveryAbortRef.current = controller;

    setActiveSection("trail-discovery");
    lastSearchQueryRef.current = searchQuery;
    lastSearchNameRef.current = newLocationName;
    lastSearchBroadRef.current = broadAreaSearch;
    lastPlaceKindRef.current = placeKind;
    lastSearchLocationRef.current = newLocation;
    setPagination(undefined);
    // Counts, coverage and peak context describe the response currently on
    // screen. A new search must clear them together with the trail lists:
    // otherwise a failed or still-running search keeps displaying the
    // previous search's numbers next to an empty list, which reads as
    // trails that vanished.
    setResultCounts(undefined);
    setCoverage(undefined);
    setPeakSearch(undefined);
    setLoadingMore(false);

    setTrails([]);
    setMapTrails([]);
    setSelectedTrailSummary(null);
    setSelectedTrailGeometry(null);
    intelligenceAbortRef.current?.abort();
    productsAbortRef.current?.abort();
    assistantAbortRef.current?.abort();
    setSelectedAnalysis(null);
    setSelectedIntelligence(null);
    setProductResults(null);
    setProductsError(null);
    setAssistantAnswer(null);
    setAssistantError(null);
    setWeather(null);
    setWeatherError(null);
    setElevation(null);
    setElevationError(null);
    setTrailError(null);
    setEnriching(false);
    setLoadingTrails(true);

    const params = new URLSearchParams({
      latitude: String(newLocation.latitude),
      longitude: String(newLocation.longitude),
      search_query: searchQuery,
      location_name: newLocationName,
      scope: broadAreaSearch ? "area" : "local",
      place_kind: placeKind,
    });

    if (searchBounds) {
      params.set("bbox", searchBounds.join(","));
    }

    const applyResults = (
      data: TrailDiscoveryResponse,
      isFinal: boolean
    ) => {
      const nextTrails = Array.isArray(data.trails)
        ? data.trails
        : [];
      const nextMapTrails: MapTrail[] = nextTrails
        .filter((trail) => trail.map_ready && trail.geometry)
        .map((trail) => ({
          trail_id: trail.trail_id,
          osm_id: trail.osm_id,
          osm_type: trail.osm_type,
          name: trail.name,
          geometry: trail.geometry!,
        }));

      setTrails(nextTrails);
      setMapTrails(nextMapTrails);
      setCoverage(data.coverage);
      setResultCounts(data.result_counts);
      setPagination(data.pagination);

      if (
        data.status === "no_provider_data" ||
        data.status === "unavailable"
      ) {
        /*
         * Two distinct situations, one message, and deliberately never
         * "this place has no trails". `unavailable` means the source could
         * not be reached, so the area was never successfully searched.
         * `no_provider_data` means it was reached and held nothing for the
         * whole area, which may be a gap in the partial public mirror or
         * coordinates that did not point where intended.
         */
        setTrailError(
          data.status === "unavailable"
            ? "The OpenStreetMap data source could not be reached, so this area was not searched. That says nothing about whether there are trails here — try again shortly."
            : "The OpenStreetMap data source answered, but returned no data for this whole area. Either the public mirror is a partial snapshot that does not currently hold it, or the searched coordinates did not point where intended — neither of which says the area has no trails.",
        );
      } else if (data.status === "partial") {
        setTrailError(
          "Some discovery sources are unavailable; results may be incomplete."
        );
      } else if (
        nextTrails.length === 0 &&
        data.status !== "success"
      ) {
        setTrailError(
          "Trail data is temporarily unavailable."
        );
      } else if (
        (data.mapped_count ?? 0) === 0 &&
        (data.unmapped_count ?? 0) > 0
      ) {
        setTrailError(
          "Candidates were found, but verified OSM geometry is not yet available."
        );
      } else {
        setTrailError(null);
      }

      if (isFinal) {
        setEnriching(false);
      }
    };

    /*
     * Stage 1: verified Postpass/OSM results. These paint immediately.
     * Stage 2: semantic enrichment + honest UNMAPPED candidates, which
     * replace stage 1 because the full result is a superset of it.
     *
     * The wait is bounded: Overpass-served fallbacks can legitimately take
     * minutes, so the bound is generous, but an unbounded fetch that hangs
     * silently would leave the loading state on forever with no error,
     * which reads as a broken product rather than a slow provider.
     */
    let stageOneTimedOut = false;
    const stageOneTimeout = setTimeout(() => {
      stageOneTimedOut = true;
      controller.abort();
    }, DISCOVERY_TIMEOUT_MS);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/osm/trails/discover?${params.toString()}`,
        {
          cache: "no-store",
          signal: controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`Trail discovery failed: ${response.status}`);
      }

      const data = (await response.json()) as TrailDiscoveryResponse;

      if (requestId !== discoveryRequestRef.current) {
        return;
      }

      applyResults(data, false);

      if (!data.enrichment_pending) {
        setLoadingTrails(false);
        return;
      }
    } catch (error) {
      if (
        error instanceof DOMException &&
        error.name === "AbortError"
      ) {
        if (
          stageOneTimedOut &&
          requestId === discoveryRequestRef.current
        ) {
          // Our own bound fired while this search was still current: the
          // provider never answered, so say so instead of hanging.
          console.error("Trail discovery timed out");
          setTrails([]);
          setMapTrails([]);
          setTrailError(
            "Trail discovery timed out waiting for the map data source. Try again shortly."
          );
          setLoadingTrails(false);
          setEnriching(false);
        }
        return;
      }

      if (requestId !== discoveryRequestRef.current) {
        return;
      }

      console.error("Trail discovery failed:", error);
      setTrails([]);
      setMapTrails([]);
      setTrailError("Trail discovery is temporarily unavailable.");
      setLoadingTrails(false);
      setEnriching(false);
      return;
    } finally {
      clearTimeout(stageOneTimeout);
    }

    /*
     * Stage 2 runs after the verified results are already on screen.
     * It has its own controller so a newer search still cancels it.
     */
    const enrichmentController = new AbortController();
    enrichmentAbortRef.current = enrichmentController;
    setEnriching(true);

    let stageTwoTimedOut = false;
    const stageTwoTimeout = setTimeout(() => {
      stageTwoTimedOut = true;
      enrichmentController.abort();
    }, DISCOVERY_TIMEOUT_MS);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/osm/trails/enrichment?${params.toString()}`,
        {
          cache: "no-store",
          signal: enrichmentController.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`Trail enrichment failed: ${response.status}`);
      }

      const data = (await response.json()) as TrailDiscoveryResponse;

      if (requestId !== discoveryRequestRef.current) {
        return;
      }

      applyResults(data, true);
    } catch (error) {
      if (
        error instanceof DOMException &&
        error.name === "AbortError"
      ) {
        if (
          stageTwoTimedOut &&
          requestId === discoveryRequestRef.current
        ) {
          // Our own bound fired: stage 1 stays on screen, and the stall
          // is reported instead of leaving the enriching state on.
          console.error("Trail enrichment timed out");
          setEnriching(false);
          setTrailError(
            (t) =>
              t ??
              "Verified trails are shown; the supplemental search timed out waiting for the map data source.",
          );
        }
        return;
      }

      if (requestId !== discoveryRequestRef.current) {
        return;
      }

      // Stage 1 results are already displayed and remain valid; only the
      // supplemental layer failed, so this is reported without discarding
      // verified trails.
      console.error("Trail enrichment failed:", error);
      setEnriching(false);
      setTrailError(
        (t) => t ?? "Verified trails are shown; semantic enrichment is unavailable."
      );
    } finally {
      clearTimeout(stageTwoTimeout);
      if (
        enrichmentAbortRef.current === enrichmentController
      ) {
        enrichmentAbortRef.current = null;
      }
      if (requestId === discoveryRequestRef.current) {
        setLoadingTrails(false);
      }
    }
  }

  /*
   * ============================================================
   * LOCATION FOUND
   * ============================================================
   */

  function handleLocationFound(
    newLocation: Location,
    newLocationName: string,
    searchQuery: string,
    broadAreaSearch: boolean,
    searchBounds: [number, number, number, number] | null,
    placeKind: string
  ) {

    setLocation(
      newLocation
    );


    setLocationName(
      newLocationName
    );


    void discoverTrails(
      newLocation,
      searchQuery,
      newLocationName,
      broadAreaSearch,
      searchBounds,
      placeKind
    );
  }


  /*
   * ============================================================
   * TRAIL SELECTION
   * ============================================================
   */

  function isSelectedTrail(
    trail: Trail
  ): trail is SelectedTrail {
    return (
      trail.map_ready &&
      trail.state === "MAP_READY" &&
      trail.geometry !== null &&
      Number.isFinite(trail.distance_km ?? trail.length_km)
    );
  }

  function handleTrailSelect(
    trail: Trail
  ) {
    if (!isSelectedTrail(trail)) {
      return;
    }

    /*
     * Re-clicking the already-selected trail must not rebuild state and
     * refire intelligence: setSelectedTrailGeometry always creates a new
     * object identity, which would retrigger the intelligence effect for
     * identical content. Skip only when results already exist, so a click
     * after a failed load still retries.
     */
    if (
      selectedTrailGeometry?.trail_id === trail.trail_id &&
      selectedIntelligence
    ) {
      return;
    }

    const selectedTrail: SelectedTrail = {
      ...trail,
      distance_km: Number(
        trail.distance_km ?? trail.length_km
      ),
    };

    setSelectedTrailSummary(selectedTrail);
    setSelectedTrailGeometry(selectedTrail);
    intelligenceAbortRef.current?.abort();
    productsAbortRef.current?.abort();
    assistantAbortRef.current?.abort();
    setSelectedAnalysis(null);
    setSelectedIntelligence(null);
    setProductResults(null);
    setProductsError(null);
    setAssistantAnswer(null);
    setAssistantError(null);
    setWeather(null);
    setWeatherError(null);
    setElevation(null);
    setElevationError(null);
  }


  /*
   * ============================================================
   * SELECTED TRAIL INTELLIGENCE
   * ============================================================
   */

  /*
   * Section navigation active state.
   *
   * The nav is a single control, so its highlight must follow the section
   * the user is actually looking at. An IntersectionObserver is used rather
   * than a scroll handler so this stays cheap on long pages.
   */
  useEffect(() => {
    if (!selectedTrailGeometry) {
      return;
    }

    const sectionIds = [
      "trail-discovery",
      "trail-overview",
      "elevation",
      "conditions",
      "suitability",
      "gear",
      "products",
      "assistant",
    ];
    const elements = sectionIds
      .map((id) => document.getElementById(id))
      .filter(
        (element): element is HTMLElement => element !== null
      );

    if (elements.length === 0) {
      return;
    }

    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            visible.set(entry.target.id, entry.intersectionRatio);
          } else {
            visible.delete(entry.target.id);
          }
        }

        const best = [...visible.entries()].sort(
          (a, b) => b[1] - a[1]
        )[0];
        if (best) {
          setActiveSection(best[0]);
        }
      },
      {
        rootMargin: "-96px 0px -55% 0px",
        threshold: [0, 0.15, 0.4, 0.75],
      }
    );

    elements.forEach((element) => observer.observe(element));

    return () => {
      observer.disconnect();
    };
  }, [selectedTrailGeometry]);

  /* The assistant is reachable as a floating control at all times, but it
     opens itself once the user reaches the assistant section, so the button
     and the in-page section never disagree. */
  useEffect(() => {
    const section = document.getElementById("assistant");
    if (!section) {
      return;
    }
    const seen = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setAssistantOpen(true);
          }
        }
      },
      { threshold: 0.25 }
    );
    seen.observe(section);
    return () => {
      seen.disconnect();
    };
  }, [selectedTrailGeometry]);

  useEffect(() => {
    if (!selectedTrailGeometry) {
      return;
    }

    const selectedTrail = selectedTrailGeometry;
    const controller = new AbortController();
    intelligenceAbortRef.current = controller;
    const bound = abortAfter(controller, INTELLIGENCE_TIMEOUT_MS);

    async function loadIntelligence() {
      setLoadingWeather(true);
      setLoadingElevation(true);
      setWeatherError(null);
      setElevationError(null);

      try {
        const response = await fetch(
          `${API_BASE_URL}/api/trails/intelligence`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              trail: selectedTrail,
            }),
            cache: "no-store",
            signal: controller.signal,
          }
        );

        if (!response.ok) {
          throw new Error(
            `Selected trail intelligence failed: ${response.status}`
          );
        }

        const intelligence =
          (await response.json()) as TrailIntelligenceResponse;

        if (
          !intelligence.analysis?.midpoint_coordinate ||
          !Number.isFinite(intelligence.analysis?.distance_km)
        ) {
          throw new Error(
            "Selected trail intelligence returned incomplete geometry"
          );
        }

        if (
          controller.signal.aborted ||
          intelligenceAbortRef.current !== controller
        ) {
          return;
        }

        setSelectedIntelligence(intelligence);
        setSelectedAnalysis(intelligence.analysis);
        setWeather(intelligence.weather);
        setElevation(intelligence.terrain);
        setWeatherError(
          intelligence.weather
            ? null
            : "Live weather is temporarily unavailable."
        );
        setElevationError(
          intelligence.terrain
            ? null
            : "Elevation data is temporarily unavailable."
        );
      } catch (error) {
        if (
          error instanceof DOMException &&
          error.name === "AbortError"
        ) {
          if (
            bound.timedOut() &&
            intelligenceAbortRef.current === controller
          ) {
            console.error("Selected trail intelligence timed out");
            setSelectedIntelligence(null);
            setSelectedAnalysis(null);
            setWeather(null);
            setElevation(null);
            setWeatherError(
              "Selected trail intelligence timed out waiting for live data. Try again shortly."
            );
            setElevationError(
              "Selected trail intelligence timed out waiting for live data. Try again shortly."
            );
          }
          return;
        }

        if (intelligenceAbortRef.current !== controller) {
          return;
        }

        console.error(
          "Selected trail intelligence failed:",
          error
        );
        setSelectedIntelligence(null);
        setSelectedAnalysis(null);
        setWeather(null);
        setElevation(null);
        setWeatherError(
          "Selected trail intelligence is temporarily unavailable."
        );
        setElevationError(
          "Selected trail intelligence is temporarily unavailable."
        );
      } finally {
        bound.clear();
        if (intelligenceAbortRef.current === controller) {
          intelligenceAbortRef.current = null;
          setLoadingWeather(false);
          setLoadingElevation(false);
        }
      }
    }

    void loadIntelligence();

    return () => {
      controller.abort();
    };
  }, [selectedTrailGeometry]);


  async function handleProductSearch() {
    if (!selectedIntelligence) {
      return;
    }

    productsAbortRef.current?.abort();
    const controller = new AbortController();
    productsAbortRef.current = controller;
    const bound = abortAfter(controller, PRODUCTS_TIMEOUT_MS);
    setProductsLoading(true);
    setProductsError(null);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/trails/products`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            intelligence: {
              gear: selectedIntelligence.gear,
              condition: selectedIntelligence.condition,
            },
          }),
          cache: "no-store",
          signal: controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`Product search failed: ${response.status}`);
      }

      const data =
        (await response.json()) as ProductSearchResponse;
      if (
        controller.signal.aborted ||
        productsAbortRef.current !== controller
      ) {
        return;
      }
      setProductResults(data);
    } catch (error) {
      if (
        error instanceof DOMException &&
        error.name === "AbortError"
      ) {
        if (
          bound.timedOut() &&
          productsAbortRef.current === controller
        ) {
          console.error("Product search timed out");
          setProductsError(
            "Product search timed out waiting for shopping results. Try again shortly."
          );
        }
        return;
      }
      if (productsAbortRef.current !== controller) {
        return;
      }
      console.error("Product search failed:", error);
      setProductsError(
        "Product search is temporarily unavailable."
      );
    } finally {
      bound.clear();
      if (productsAbortRef.current === controller) {
        productsAbortRef.current = null;
        setProductsLoading(false);
      }
    }
  }


  async function handleAssistantSubmit(
    event: FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();
    if (
      !selectedTrailGeometry ||
      !selectedIntelligence ||
      !assistantQuestion.trim()
    ) {
      return;
    }

    assistantAbortRef.current?.abort();
    const controller = new AbortController();
    assistantAbortRef.current = controller;
    const bound = abortAfter(controller, ASSISTANT_TIMEOUT_MS);
    setAssistantLoading(true);
    setAssistantError(null);

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/trails/assistant`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            question: assistantQuestion.trim(),
            trail: {
              trail_id: selectedTrailGeometry.trail_id,
              osm_type: selectedTrailGeometry.osm_type,
              osm_id: selectedTrailGeometry.osm_id,
              name: selectedTrailGeometry.name,
              route_type: selectedTrailGeometry.route_type,
              highway_type: selectedTrailGeometry.highway_type,
              source: selectedTrailGeometry.source,
              geometry_hash: selectedTrailGeometry.geometry_hash,
              member_way_ids: selectedTrailGeometry.member_way_ids,
            },
            intelligence: {
              analysis: {
                distance_km: selectedIntelligence.analysis.distance_km,
                component_count: selectedIntelligence.analysis.component_count,
              },
              terrain: selectedIntelligence.terrain
                ? { metrics: selectedIntelligence.terrain.metrics }
                : null,
              weather: selectedIntelligence.weather,
              condition: selectedIntelligence.condition,
              suitability: selectedIntelligence.suitability,
              gear: selectedIntelligence.gear,
              difficulty: selectedIntelligence.difficulty,
            },
          }),
          cache: "no-store",
          signal: controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`Assistant request failed: ${response.status}`);
      }

      const data =
        (await response.json()) as AssistantResponse;
      if (
        controller.signal.aborted ||
        assistantAbortRef.current !== controller
      ) {
        return;
      }
      setAssistantAnswer(data);
    } catch (error) {
      if (
        error instanceof DOMException &&
        error.name === "AbortError"
      ) {
        if (
          bound.timedOut() &&
          assistantAbortRef.current === controller
        ) {
          console.error("Trail assistant timed out");
          setAssistantError(
            "The assistant timed out waiting for an answer. Try again shortly."
          );
        }
        return;
      }
      if (assistantAbortRef.current !== controller) {
        return;
      }
      console.error("Trail assistant failed:", error);
      setAssistantError(
        "The assistant is temporarily unavailable."
      );
    } finally {
      bound.clear();
      if (assistantAbortRef.current === controller) {
        assistantAbortRef.current = null;
        setAssistantLoading(false);
      }
    }
  }


  /*
   * ============================================================
   * ELEVATION PROFILE HEIGHT
   * ============================================================
   */

  function getDisplayProfile(
    profile: ElevationProfilePoint[]
  ): Array<ElevationProfilePoint & {
    elevation_m: number;
  }> {
    return profile.filter(
      (point): point is ElevationProfilePoint & {
        elevation_m: number;
      } => point.elevation_m !== null
    );
  }


  return (
    <main className="min-h-screen bg-[#07111f] text-white">

      {/* ============================================================
          HEADER
      ============================================================ */}

      <header className="border-b border-white/[0.06]">

        <div className="mx-auto flex max-w-[1440px] items-center justify-between px-6 py-6 md:px-10 lg:px-14">

          <div>

            <p className="text-[10px] uppercase tracking-[0.22em] text-white/40">
              Explore
            </p>

            <h1 className="mt-1 text-[22px] font-semibold tracking-[-0.03em]">
              Find your trail.
            </h1>

          </div>


          {locationName && (
            <div className="hidden max-w-[500px] truncate rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-xs text-white/55 md:block">
              {
                locationName
              }
            </div>
          )}

        </div>

      </header>


      {/* ============================================================
          SECTION NAVIGATION
          Primary Explore navigation: navbar, then search, then map.
          Sticky within the page scroll container; horizontally scrolls
          on narrow screens instead of wrapping.
      ============================================================ */}

      {selectedTrailGeometry && (
        <div className="sticky top-0 z-30 mx-auto w-full max-w-[1440px] px-6 pt-3 md:px-10 lg:px-14">
          <nav
            aria-label="Selected trail sections"
            className="flex items-center gap-1 overflow-x-auto rounded-2xl border border-white/10 bg-[#0b1724] px-2 py-1.5 text-[11px] shadow-[0_8px_24px_rgba(0,0,0,0.28)]"
          >
            {[
              ["trail-discovery", "Trail"],
              ["elevation", "Elevation"],
              ["conditions", "Weather"],
              ["suitability", "Suitability"],
              ["gear", "Gear"],
              ["products", "Products"],
              ["assistant", "Assistant"],
            ].map(([target, label]) => {
              const isActive =
                activeSection === target;
              return (
                <button
                  key={target}
                  type="button"
                  aria-current={
                    isActive
                      ? "true"
                      : undefined
                  }
                  onClick={() => {
                    const element =
                      document.getElementById(
                        target
                      );
                    element?.scrollIntoView({
                      behavior: "smooth",
                      block: "start",
                    });
                    setActiveSection(target);
                  }}
                  className={`whitespace-nowrap rounded-xl px-3 py-2 font-semibold transition ${
                    isActive
                      ? "bg-sky-300/12 text-sky-200"
                      : "text-white/50 hover:bg-white/[0.06] hover:text-white/85"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </nav>
        </div>
      )}


      {/* ============================================================
          SEARCH
      ============================================================ */}

      <section className="mx-auto max-w-[1440px] px-6 pt-7 md:px-10 lg:px-14">

        <ExploreSearch
          initialQuery={
            initialQuery
          }

          onLocationFound={
            handleLocationFound
          }

        />

      </section>


      {/* ============================================================
          MAP + SIDEBAR
      ============================================================ */}

      {!mapExpanded && (
        <section
          id="trail-discovery"
          className="mx-auto max-w-[1440px] scroll-mt-20 px-6 py-7 md:px-10 lg:px-14"
        >

          <div className="grid h-[620px] min-h-0 grid-cols-1 overflow-hidden rounded-[24px] border border-white/10 bg-[#0d1825] shadow-[0_25px_70px_rgba(0,0,0,0.22)] lg:grid-cols-[380px_minmax(0,1fr)]">

            <div className="min-h-0 overflow-hidden border-b border-white/10 lg:border-b-0 lg:border-r">

              <TrailSidebar
                
                trails={trails}
                
                loading={loadingTrails}
                
                selectedTrail={selectedTrailSummary}
                
                onTrailSelect={handleTrailSelect}

                counts={resultCounts}
              
              />


              {/* A provider problem and the coverage accounting are two
                  different facts, so both are shown rather than one replacing
                  the other. */}
              {trailError && (
                <div className="border-t border-white/10 bg-[#0b1724] px-5 py-3">

                  <p className="text-[11px] leading-5 text-white/45">
                    {
                      trailError
                    }
                  </p>

                </div>
              )}


              {!loadingTrails &&
                !enriching &&
                coverage && (
                  <div className="border-t border-white/10 bg-[#0b1724] px-5 py-3">

                    <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                      Searched coverage
                    </p>

                    <p className="mt-1.5 text-[11px] leading-5 text-white/45">
                      {
                        coverage.provider_returned_no_rows
                          ? "The map data source was searched and returned no rows at all for this area, so nothing can be concluded here about what is mapped."
                          : coverage.coverage_complete
                            ? `Searched ${(
                                coverage.area_km2 ?? 0
                              ).toLocaleString(
                                undefined,
                                { maximumFractionDigits: 0 }
                              )} km²${
                                coverage.tiled
                                  ? ` in ${coverage.tiles_queried} of ${
                                      coverage.tiles_total
                                    } searched regions`
                                  : ""
                              }.`
                            : `Only ${coverage.tiles_queried} of ${
                                coverage.tiles_total
                              } searched regions returned, so this area is not fully covered.`
                      }
                      {
                        pagination && !coverage.provider_returned_no_rows
                          ? ` ${pagination.total_ranked.toLocaleString()} verified ${
                              pagination.total_ranked === 1
                                ? "trail was"
                                : "trails were"
                            } ranked, showing ${
                              trails.length
                            }.`
                          : ""
                      }
                    </p>
                    {peakSearch?.is_peak_search ? (
                      <div className="mt-2.5 rounded-xl border border-sky-300/15 bg-sky-300/[0.04] p-3">
                        <p className="text-[10px] uppercase tracking-[0.15em] text-sky-200/60">
                          Summit
                        </p>
                        <p className="mt-1 text-[11px] leading-5 text-white/60">
                          {peakSearch.summit_note}
                        </p>
                      </div>
                    ) : null}

                    {pagination?.has_more ? (
                      <button
                        type="button"
                        onClick={loadMoreTrails}
                        disabled={loadingMore}
                        className="mt-2.5 w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[11px] font-semibold text-white/70 transition hover:border-sky-300/30 hover:text-white disabled:opacity-50"
                      >
                        {loadingMore
                          ? "Loading more…"
                          : `Show more (${Math.max(
                              pagination.total_ranked - trails.length,
                              0
                            ).toLocaleString()} more ranked)`}
                      </button>
                    ) : pagination && pagination.total_ranked > 0 ? (
                      <p className="mt-2 text-[10px] text-white/25">
                        End of the ranked results for this search.
                      </p>
                    ) : null}

                  </div>
                )}

            </div>


            <div className="relative min-h-0 overflow-hidden">

              <CesiumMap

                location={
                  location
                }

                locationName={
                  locationName
                }

                mapTrails={
                  mapTrails
                }

                selectedTrail={
                  selectedTrailGeometry
                }

                expanded={
                  false
                }

                onExpand={() =>
                  setMapExpanded(
                    true
                  )
                }

              />

            </div>

          </div>

        </section>
      )}


      {/* ============================================================
          EXPANDED MAP
      ============================================================ */}

      {mapExpanded && (
        <div className="fixed inset-0 z-[100] bg-black">

          <CesiumMap

            location={
              location
            }

            locationName={
              locationName
            }

            mapTrails={
              mapTrails
            }

            selectedTrail={
              selectedTrailGeometry
            }

            expanded={
              true
            }

            onCollapse={() =>
              setMapExpanded(
                false
              )
            }

          />

        </div>
      )}


      {/* ============================================================
          SELECTED TRAIL INFORMATION
      ============================================================ */}

      {!mapExpanded &&
        selectedTrailGeometry && (

          <section className="mx-auto max-w-[1440px] px-6 pb-20 md:px-10 lg:px-14">

            {/* ========================================================
                SELECTED TRAIL
            ======================================================== */}

            <section id="trail-overview" className="scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-6 md:p-8">

              <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_420px]">

                <div>

                  <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                    Selected trail
                  </p>


                  <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em]">

                    {
                    selectedTrailGeometry.name ??
                     selectedTrailSummary?.name ??
                     "Unnamed OSM hiking route"}

                  </h2>


                  <p className="mt-4 max-w-2xl text-sm leading-7 text-white/45">

                    The selected route is shown from
                    its verified geographic geometry.
                    Terrain, weather and analytical
                    condition likelihood,
                    suitability, gear, and difficulty evidence
                     are calculated from this same route.

                  </p>

                </div>


                {/* ====================================================
                    TRAIL SUMMARY TILES
                ==================================================== */}

                <div className="grid grid-cols-2 gap-3">

                  {/* DISTANCE */}

                  <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-all duration-200 hover:-translate-y-[1px] hover:border-emerald-400/20 hover:bg-white/[0.065]">

                    <div className="flex items-center justify-between">

                      <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/35">
                        Distance
                      </p>

                      <div className="h-1.5 w-1.5 rounded-full bg-emerald-400/70 shadow-[0_0_10px_rgba(52,211,153,0.35)]" />

                    </div>


                    <p className="mt-3 text-[22px] font-semibold tracking-[-0.035em] text-white">

                      {
                        Number.isFinite(
                          selectedTrailGeometry.distance_km
                        )
                          ? selectedTrailGeometry.distance_km.toFixed(
                              1
                            )
                          : "—"
                      }

                      <span className="ml-1 text-sm font-medium text-white/35">
                        km
                      </span>

                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      {mappedLengthLabel()}
                    </p>

                    {/*
                      The disclosure is about what OpenStreetMap records for
                      this object, so it only appears when the source itself
                      establishes that the mapping is partial. A multi-member
                      relation is not that evidence and does not trigger it.
                    */}
                    {selectedTrailGeometry.relation_completeness?.note ? (
                      <details className="group mt-2">
                        <summary className="cursor-pointer list-none rounded-lg border border-amber-300/20 bg-amber-300/[0.05] px-2.5 py-1.5 text-[10px] leading-4 text-amber-100/80">
                          OpenStreetMap currently maps only this section of
                          the named route. The full real-world trek may be
                          longer.
                        </summary>
                        <p className="mt-1.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-[10px] leading-4 text-white/40">
                          {selectedTrailGeometry.relation_completeness.note}
                        </p>
                      </details>
                    ) : null}

                  </div>


                  {/* DIFFICULTY */}

                  <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-all duration-200 hover:-translate-y-[1px] hover:border-amber-300/20 hover:bg-white/[0.065]">

                    <div className="flex items-center justify-between">

                      <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/35">
                        Difficulty
                      </p>

                      <div className="h-1.5 w-1.5 rounded-full bg-amber-300/70 shadow-[0_0_10px_rgba(252,211,77,0.25)]" />

                    </div>


                    <p className="mt-3 text-[17px] font-semibold tracking-[-0.02em] text-white">
                      {/*
                        The discovery payload carries the recorded OSM
                        `sac_scale` verbatim, which is an activity grade
                        ("hiking"), not a difficulty word. Printing it raw
                        would put "Difficulty: hiking" in front of a user, so
                        it goes through the same documented mapping the
                        sidebar uses.
                      */}
                      {
                        difficultyLabel(
                          selectedTrailGeometry.difficulty
                        )
                      }
                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      {
                        selectedTrailGeometry.difficulty
                          ? "Official OSM scale"
                          : "No recorded difficulty"
                      }
                    </p>

                  </div>


                  {/* SURFACE */}

                  <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-all duration-200 hover:-translate-y-[1px] hover:border-sky-300/20 hover:bg-white/[0.065]">

                    <div className="flex items-center justify-between">

                      <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/35">
                        Surface
                      </p>

                      <div className="h-1.5 w-1.5 rounded-full bg-sky-300/70 shadow-[0_0_10px_rgba(125,211,252,0.25)]" />

                    </div>


                    <p className="mt-3 text-[17px] font-semibold capitalize tracking-[-0.02em] text-white">
                      {
                        selectedTrailGeometry.surface ??
                        "Not available"
                      }
                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      Trail surface
                    </p>

                  </div>


                  {/* ROUTE */}

                  <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-all duration-200 hover:-translate-y-[1px] hover:border-violet-300/20 hover:bg-white/[0.065]">

                    <div className="flex items-center justify-between">

                      <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/35">
                        Route
                      </p>

                      <div className="h-1.5 w-1.5 rounded-full bg-violet-300/70 shadow-[0_0_10px_rgba(196,181,253,0.22)]" />

                    </div>


                    <p className="mt-3 text-[17px] font-semibold tracking-[-0.02em] text-white">
                      {
                        selectedTrailGeometry.route_type ??
                        "Trail path"
                      }
                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      Route classification
                    </p>

                  </div>

                </div>

              </div>

            </section>


            {/* ========================================================
                CONDITIONS / WEATHER
            ======================================================== */}

            <section id="conditions" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">

                <div>

                  <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                    Conditions
                  </p>


                  <h3 className="mt-3 text-[22px] font-semibold">
                    Weather & trail conditions
                  </h3>

                </div>


                {weather && (
                  <p className="text-[11px] text-white/30">
                    Live weather at trail midpoint
                  </p>
                )}

              </div>


              {/* WEATHER LOADING */}

              {loadingWeather && (
                <div className="mt-6 rounded-2xl border border-white/[0.08] bg-white/[0.025] px-5 py-6">

                  <div className="flex items-center gap-3">

                    <div className="h-2 w-2 animate-pulse rounded-full bg-emerald-400/70" />

                    <p className="text-sm text-white/55">
                      Loading current weather…
                    </p>

                  </div>

                </div>
              )}


              {/* WEATHER ERROR */}

              {!loadingWeather &&
                weatherError && (

                  <div className="mt-6 rounded-2xl border border-amber-300/10 bg-amber-300/[0.03] px-5 py-5">

                    <p className="text-sm text-white/55">
                      {
                        weatherError
                      }
                    </p>

                  </div>
                )}


              {/* WEATHER DATA */}

              {!loadingWeather &&
                !weatherError &&
                weather && (

                  <div className="mt-6">

                    <div className="rounded-[22px] border border-white/[0.08] bg-gradient-to-br from-white/[0.05] to-white/[0.02] p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.025)]">

                      <div className="grid gap-5 md:grid-cols-[1.3fr_1fr_1fr]">

                        {/* Temperature */}

                        <div>

                          <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/30">
                            Current
                          </p>


                          <div className="mt-3 flex items-end gap-2">

                            <p className="text-4xl font-semibold tracking-[-0.05em] text-white">

                              {
                                weather.current.temperature !==
                                null
                                  ? weather.current.temperature.toFixed(
                                      1
                                    )
                                  : "—"
                              }

                            </p>


                            <span className="mb-1 text-lg text-white/35">
                              °C
                            </span>

                          </div>


                          <p className="mt-2 text-sm text-emerald-300/70">
                            {
                              weather.current.weather_condition
                            }
                          </p>

                        </div>


                        {/* Rain chance */}

                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">

                          <p className="text-[10px] uppercase tracking-[0.16em] text-white/30">
                            Rain chance
                          </p>


                          <p className="mt-3 text-2xl font-semibold tracking-[-0.035em] text-white">

                            {
                              weather.current
                                .precipitation_probability !==
                              null
                                ? `${weather.current.precipitation_probability}%`
                                : "—"
                            }

                          </p>


                          <p className="mt-1 text-[11px] text-white/30">
                            Current hour
                          </p>

                        </div>


                        {/* Precipitation */}

                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] p-4">

                          <p className="text-[10px] uppercase tracking-[0.16em] text-white/30">
                            Precipitation
                          </p>


                          <p className="mt-3 text-2xl font-semibold tracking-[-0.035em] text-white">

                            {
                              weather.current.precipitation !==
                              null
                                ? `${weather.current.precipitation.toFixed(
                                    1
                                  )} mm`
                                : "—"
                            }

                          </p>


                          <p className="mt-1 text-[11px] text-white/30">
                            Current
                          </p>

                        </div>

                      </div>


                      {/* Secondary weather values */}

                      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">

                        {/* Humidity */}

                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                          <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                            Humidity
                          </p>


                          <p className="mt-2 text-sm font-semibold text-white/80">

                            {
                              weather.current.humidity !==
                              null
                                ? `${weather.current.humidity}%`
                                : "—"
                            }

                          </p>

                        </div>


                        {/* Wind */}

                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                          <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                            Wind
                          </p>


                          <p className="mt-2 text-sm font-semibold text-white/80">

                            {
                              weather.current.wind_speed !==
                              null
                                ? `${weather.current.wind_speed.toFixed(
                                    1
                                  )} km/h`
                                : "—"
                            }

                          </p>

                        </div>


                        {/* Rain */}

                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                          <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                            Rain
                          </p>


                          <p className="mt-2 text-sm font-semibold text-white/80">

                            {
                              weather.current.rain !==
                              null
                                ? `${weather.current.rain.toFixed(
                                    1
                                  )} mm`
                                : "—"
                            }

                          </p>

                        </div>


                        {/* Updated */}

                        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.025] px-4 py-3">

                          <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                            Updated
                          </p>


                          <p className="mt-2 text-sm font-semibold text-white/80">

                            {
                              weather.current.time
                                ? weather.current.time.slice(
                                    11,
                                    16
                                  )
                                : "—"
                            }

                          </p>


                          <p className="text-[10px] text-white/25">
                            Local time
                          </p>

                        </div>

                      </div>

                    </div>


                    {(weather.recent_rain || weather.recent_precipitation) && (
                      <div className="mt-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] px-4 py-4">
                        <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                          Recent rainfall
                        </p>
                        <div className="mt-3 grid grid-cols-3 gap-3 text-center">
                          <div>
                            <p className="text-sm font-semibold text-white/80">
                              {weather.recent_rain?.["24h_mm"] != null
                                ? `${weather.recent_rain["24h_mm"]!.toFixed(1)} mm`
                                : weather.recent_precipitation?.["24h_mm"] != null
                                  ? `${weather.recent_precipitation?.["24h_mm"]!.toFixed(1)} mm`
                                  : "—"}
                            </p>
                            <p className="mt-1 text-[10px] text-white/25">24h</p>
                          </div>
                          <div>
                            <p className="text-sm font-semibold text-white/80">
                              {weather.recent_rain?.["48h_mm"] != null
                                ? `${weather.recent_rain["48h_mm"]!.toFixed(1)} mm`
                                : weather.recent_precipitation?.["48h_mm"] != null
                                  ? `${weather.recent_precipitation?.["48h_mm"]!.toFixed(1)} mm`
                                  : "—"}
                            </p>
                            <p className="mt-1 text-[10px] text-white/25">48h</p>
                          </div>
                          <div>
                            <p className="text-sm font-semibold text-white/80">
                              {weather.recent_rain?.["72h_mm"] != null
                                ? `${weather.recent_rain["72h_mm"]!.toFixed(1)} mm`
                                : weather.recent_precipitation?.["72h_mm"] != null
                                  ? `${weather.recent_precipitation?.["72h_mm"]!.toFixed(1)} mm`
                                  : "—"}
                            </p>
                            <p className="mt-1 text-[10px] text-white/25">72h</p>
                          </div>
                        </div>
                      </div>
                    )}


                    <p className="mt-3 text-[10px] leading-5 text-white/25">
                      Weather source: {weather.source}
                      {" · measured at "}
                      {weatherPointLabel(
                        selectedIntelligence
                          ? selectedIntelligence.weather_coordinate
                          : undefined,
                      )}
                      {" — a point on the selected route, not the place you searched."}
                    </p>

                  </div>
                )}

            </section>


            {/* ========================================================
                TERRAIN
            ======================================================== */}

            <section id="elevation" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Terrain
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Elevation &amp; slope
              </h3>

              {/*
                Explanation sits between the title and the figures, at full
                width. It used to share a row with the heading, which left it
                baseline-aligned against the title and crowded the metrics
                directly beneath it.
              */}
              <p className="mt-2.5 max-w-[74ch] text-[12px] leading-6 text-white/40">
                How high the route climbs and descends, sampled along the
                geometry drawn on the map
                {selectedAnalysis
                  ? `, which has ${selectedAnalysis.component_count} component${
                      selectedAnalysis.component_count === 1 ? "" : "s"
                    }`
                  : ""}
                . Disconnected parts are kept apart, so no distance or
                climb is counted across the gap between them.
              </p>


              {/* ELEVATION LOADING */}

              {loadingElevation && (
                <div className="mt-6 rounded-2xl border border-white/[0.08] bg-white/[0.025] px-5 py-6">

                  <div className="flex items-center gap-3">

                    <div className="h-2 w-2 animate-pulse rounded-full bg-emerald-400/70" />

                    <p className="text-sm text-white/55">
                      Analysing trail elevation…
                    </p>

                  </div>

                </div>
              )}


              {/* ELEVATION ERROR */}

              {!loadingElevation &&
                elevationError && (

                  <div className="mt-6 rounded-2xl border border-amber-300/10 bg-amber-300/[0.03] px-5 py-5">

                    <p className="text-sm text-white/55">
                      {
                        elevationError
                      }
                    </p>

                  </div>
                )}


              {/* ELEVATION DATA */}

              {!loadingElevation &&
                !elevationError &&
                elevation && (

                  <div className="mt-6">

                    {/* Metrics */}

                    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">

                      {/* Minimum */}

                      <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-emerald-400/20">

                        <div className="flex items-center justify-between">

                          <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-white/30">
                            Minimum
                          </p>

                          <div className="h-1.5 w-1.5 rounded-full bg-emerald-400/60" />

                        </div>


                        <p className="mt-3 text-xl font-semibold tracking-[-0.03em]">

                          {
                            elevation.metrics.min_elevation_m !==
                            null
                              ? `${elevation.metrics.min_elevation_m.toFixed(
                                  0
                                )} m`
                              : "—"
                          }

                        </p>


                        <p className="mt-1 text-[11px] text-white/25">
                          Lowest sampled point
                        </p>

                      </div>


                      {/* Maximum */}

                      <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-amber-300/20">

                        <div className="flex items-center justify-between">

                          <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-white/30">
                            Maximum
                          </p>

                          <div className="h-1.5 w-1.5 rounded-full bg-amber-300/60" />

                        </div>


                        <p className="mt-3 text-xl font-semibold tracking-[-0.03em]">

                          {
                            elevation.metrics.max_elevation_m !==
                            null
                              ? `${elevation.metrics.max_elevation_m.toFixed(
                                  0
                                )} m`
                              : "—"
                          }

                        </p>


                        <p className="mt-1 text-[11px] text-white/25">
                          Highest sampled point
                        </p>

                      </div>


                      {/* Gain */}

                      <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-sky-300/20">

                        <div className="flex items-center justify-between">

                          <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-white/30">
                            Elevation gain
                          </p>

                          <div className="h-1.5 w-1.5 rounded-full bg-sky-300/60" />

                        </div>


                        <p className="mt-3 text-xl font-semibold tracking-[-0.03em]">

                          {
                            elevation.metrics.elevation_gain_m !==
                            null
                              ? `+${elevation.metrics.elevation_gain_m.toFixed(
                                  0
                                )} m`
                              : "—"
                          }

                        </p>


                        <p className="mt-1 text-[11px] text-white/25">
                          Total uphill movement
                        </p>

                      </div>


                      {/* Loss */}

                      <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-violet-300/20">

                        <div className="flex items-center justify-between">

                          <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-white/30">
                            Elevation loss
                          </p>

                          <div className="h-1.5 w-1.5 rounded-full bg-violet-300/60" />

                        </div>


                        <p className="mt-3 text-xl font-semibold tracking-[-0.03em]">

                          {
                            elevation.metrics.elevation_loss_m !==
                            null
                              ? `-${elevation.metrics.elevation_loss_m.toFixed(
                                  0
                                )} m`
                              : "—"
                          }

                        </p>


                        <p className="mt-1 text-[11px] text-white/25">
                          Total downhill movement
                        </p>

                      </div>


                      {/* Average slope */}

                      <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-orange-300/20">

                        <div className="flex items-center justify-between">

                          <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-white/30">
                            Average slope
                          </p>

                          <div className="h-1.5 w-1.5 rounded-full bg-orange-300/60" />

                        </div>


                        <p className="mt-3 text-xl font-semibold tracking-[-0.03em]">

                          {
                            elevation.metrics.average_slope_percent !==
                            null
                              ? `${elevation.metrics.average_slope_percent.toFixed(
                                  1
                                )}%`
                              : "—"
                          }

                        </p>


                        <p className="mt-1 text-[11px] text-white/25">
                          Across sampled sections
                        </p>

                      </div>


                      {/* Maximum slope */}

                      <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-red-300/20">

                        <div className="flex items-center justify-between">

                          <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-white/30">
                            Maximum slope
                          </p>

                          <div className="h-1.5 w-1.5 rounded-full bg-red-300/60" />

                        </div>


                        <p className="mt-3 text-xl font-semibold tracking-[-0.03em]">

                          {
                            elevation.metrics.max_slope_percent !==
                            null
                              ? `${elevation.metrics.max_slope_percent.toFixed(
                                  1
                                )}%`
                              : "—"
                          }

                        </p>


                        <p className="mt-1 text-[11px] text-white/25">
                          Steepest sampled section
                        </p>

                      </div>

                    </div>


                    {/* Elevation profile */}

                    {elevation.profile.length >
                      0 && (() => {

                        const displayProfile =
                          getDisplayProfile(
                            elevation.profile
                          );

                        return (

                      <div className="mt-5 rounded-[22px] border border-white/[0.08] bg-gradient-to-br from-white/[0.04] to-white/[0.015] p-5 pt-4">

                        {/*
                          The chart speaks for itself under the section
                          heading, so it carries no second title. What it does
                          need is its axes named, and that belongs below the
                          plot rather than above it.
                        */}
                        <ElevationProfile
                          profile={displayProfile}
                          metrics={elevation.metrics}
                          orientation={{
                            profile_orientation:
                              elevation.profile_orientation,
                            reversal_needed:
                              elevation.reversal_needed,
                          }}
                        />


                        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-white/[0.06] pt-3">

                          <p className="text-[10px] text-white/30">
                            Horizontal axis: distance along
                            the route (km). Vertical axis:
                            elevation (m).
                          </p>

                          <p className="text-[10px] text-white/30">
                            {/*
                              The same evidence rule as the card above: this
                              is the length of the geometry the profile was
                              sampled from, so it is never presented as the
                              whole-trek distance.
                            */}
                            {mappedLengthLabel()}{" "}
                            {
                              displayProfile[
                                displayProfile.length - 1
                              ].distance_km.toFixed(2)
                            }{" "}
                            km
                          </p>

                        </div>

                      </div>
                        );
                      })()}


                    <p className="mt-3 text-[10px] leading-5 text-white/25">

                      Elevation source: {
                        elevation.source
                      } · Every value above is
                      derived from the same sampled
                      points on the selected trail
                      geometry, so the chart, the
                      figures and the difficulty
                      estimate all describe one route.

                    </p>

                  </div>
                )}

            </section>


            {/* ========================================================
                INTELLIGENCE
            ======================================================== */}

            <section id="suitability" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Intelligence
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Difficulty, conditions & suitability
              </h3>


              {selectedIntelligence ? (
                <div className="mt-6 space-y-4">
                  <div className="grid gap-3 md:grid-cols-2">
                    {/*
                      Measured route demands are integrated into the
                      Difficulty card below as supporting evidence, never
                      as a separate competing score. There is exactly one
                      difficulty presentation: official when recorded,
                      estimated otherwise.
                    */}
                    {/*
                      The official card and the estimated card answer two
                      different questions and are never merged. When
                      OpenStreetMap records a grade, that card is the answer
                      and the estimate stays inside the technical disclosure
                      below, so the interface never shows two competing
                      difficulty labels.
                    */}
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                      <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                        {selectedIntelligence.difficulty.source.class
                          ? "Official difficulty"
                          : selectedIntelligence.difficulty.ml
                                .available
                            ? "Model-estimated difficulty"
                            : "Difficulty"}
                      </p>

                      {(() => {
                        const view = difficultyPresentation({
                          recordedGrade:
                            selectedIntelligence.difficulty.source
                              .sac_scale,
                          officialTier:
                            selectedIntelligence.difficulty.source
                              .tier,
                          estimateTier:
                            selectedIntelligence.difficulty.ml
                              .estimate,
                          estimateAvailable:
                            selectedIntelligence.difficulty.ml
                              .available,
                        });
                        return (
                          <>
                            {view.label ? (
                              <p className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-white">
                                {view.label}
                              </p>
                            ) : (
                              <p className="mt-3 text-xl font-semibold text-white/60">
                                Not enough evidence
                              </p>
                            )}

                            <p className="mt-1.5 text-[12px] leading-5 text-white/45">
                              {view.provenance}
                              {view.qualification
                                ? ` ${view.qualification}`
                                : ""}
                            </p>

                            {/*
                              Provenance detail: the recorded grade stays
                              verbatim, and an estimate keeps its native
                              tier plus the product range that tier spans,
                              so the conservative label is visible rather
                              than implied.
                            */}
                            {view.recordedGrade ? (
                              <p className="mt-1 text-[11px] leading-5 text-white/30">
                                Recorded grade {view.recordedGrade}.
                              </p>
                            ) : view.nativeTier ? (
                              <p className="mt-1 text-[11px] leading-5 text-white/30">
                                Model tier {view.nativeTier}
                                {view.estimatedRange
                                  ? ` · covers ${view.estimatedRange} recorded grades`
                                  : ""}
                                .
                              </p>
                            ) : null}
                          </>
                        );
                      })()}
                      {/*
                        Route answers are a vote across verified member ways,
                        never a prediction from route totals. The split is
                        shown so a divided route reads as divided, and the
                        majority is never presented as unanimous.
                      */}
                      {selectedIntelligence.difficulty.ml.aggregation ? (
                        <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                          <p className="text-[10px] uppercase tracking-[0.12em] text-white/30">
                            Route sections scored
                          </p>
                          <p className="mt-1.5 text-[10px] leading-4 text-white/40">
                            {selectedIntelligence.difficulty.ml
                              .aggregation.scored_members ===
                            selectedIntelligence.difficulty.ml
                              .aggregation.total_members
                              ? `All ${selectedIntelligence.difficulty.ml.aggregation.scored_members} verified sections scored individually.`
                              : `${selectedIntelligence.difficulty.ml.aggregation.scored_members} of ${selectedIntelligence.difficulty.ml.aggregation.total_members} verified sections scored individually.`}{" "}
                            {Object.entries(
                              selectedIntelligence.difficulty.ml
                                .aggregation.tier_counts,
                            )
                              .filter(([, count]) => count > 0)
                              .map(
                                ([tier, count]) =>
                                  `${count} ${tier}`,
                              )
                              .join(" · ")}
                            .
                          </p>
                          {selectedIntelligence.difficulty.ml
                            .aggregation.members_disagree ? (
                            <p className="mt-1.5 text-[10px] leading-4 text-amber-200/60">
                              Sections of this route disagree about its
                              difficulty, so treat the majority as a hint
                              and check the harder sections before
                              committing to it.
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                      {/*
                        States what the ESTIMATE did not use. It says
                        nothing about whether the route has elevation: the
                        measured profile can exist and simply not have been
                        fed to the model, so the two facts are kept apart.
                      */}
                      {!selectedIntelligence.difficulty.source
                        .class &&
                      selectedIntelligence.difficulty.ml.available &&
                      selectedIntelligence.difficulty.ml
                        .terrain_features_used === false ? (
                        <p className="mt-1.5 text-[11px] leading-5 text-white/35">
                          The estimate did not use this route&apos;s
                          measured elevation; it rests on recorded tags
                          and route shape. Measured terrain is shown above
                          regardless.
                        </p>
                      ) : null}
                      {/*
                        Measured demands live inside Difficulty as
                        supporting evidence. Each line is a measured
                        quantity from the verified route geometry — what
                        the route asks of a walker — never a second
                        difficulty grade and never combined with the
                        estimate into one score.
                      */}
                      {selectedIntelligence.route_complexity
                        ?.available &&
                      (selectedIntelligence.route_complexity
                        .components ?? []).length > 0 ? (
                        <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                          <p className="text-[10px] uppercase tracking-[0.12em] text-white/30">
                            Measured demands
                          </p>
                          <ul className="mt-2 space-y-1">
                            {selectedIntelligence.route_complexity.components.map(
                              (part) => (
                                <li
                                  key={part.component}
                                  className="text-[11px] leading-5 text-white/45"
                                >
                                  • {part.measured}
                                </li>
                              ),
                            )}
                          </ul>
                          {selectedIntelligence.route_complexity
                            .missing_evidence.length > 0 ? (
                            <p className="mt-1.5 text-[10px] leading-4 text-amber-200/60">
                              Not measured:{" "}
                              {selectedIntelligence.route_complexity.missing_evidence.join(
                                ", ",
                              )}
                              .
                            </p>
                          ) : null}
                        </div>
                      ) : null}
                    </div>
                    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                      <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                        Trail conditions
                      </p>
                      {(() => {
                        const { headline, tone } =
                          conditionHeadline(
                            selectedIntelligence.condition
                              .status,
                            selectedIntelligence.weather,
                          );
                        const observed = observedEvidence(
                          selectedIntelligence.weather,
                        );
                        const toneClass =
                          tone === "good"
                            ? "text-emerald-200"
                            : tone === "warn"
                              ? "text-amber-200"
                              : tone === "bad"
                                ? "text-rose-200"
                                : "text-white/50";
                        return (
                          <>
                            <p
                              className={`mt-3 text-3xl font-semibold tracking-[-0.03em] ${toneClass}`}
                            >
                              {headline}
                            </p>

                            {/*
                              Observed evidence. Each row is a block-level
                              pair with a real gap and its own line, so a
                              label and its value can never run together
                              visually. Missing values are omitted, never
                              rendered as zero.
                            */}
                            {tone === "neutral" ? (
                              <p className="mt-2 text-[12px] leading-5 text-white/40">
                                Live weather evidence is unavailable
                                for this route right now.
                              </p>
                            ) : observed.length > 0 ? (
                              <ul className="mt-3 space-y-2">
                                {observed.map((row) => (
                                  <li
                                    key={row.label}
                                    className="flex items-baseline justify-between gap-4 text-[12px] leading-5"
                                  >
                                    <span className="text-white/45">
                                      {row.label}
                                    </span>
                                    <span className="shrink-0 font-semibold text-white/90">
                                      {row.value}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <p className="mt-2 text-[12px] leading-5 text-white/40">
                                No observed weather values were
                                returned for this route.
                              </p>
                            )}

                            {/*
                              The inferred statement, explicitly marked as
                              inference. It is a likelihood drawn from the
                              observations above plus route evidence, never
                              a measurement of the ground.
                            */}
                            <p className="mt-3 text-[12px] leading-5 text-white/50">
                              <span className="text-white/35">
                                Inferred:{" "}
                              </span>
                              {
                                selectedIntelligence.condition
                                  .summary
                              }
                            </p>
                          </>
                        );
                      })()}
                    </div>
                  </div>

                  {/*
                    The per-factor evidence behind the inferred condition
                    is genuine working, but it is not what a reader needs
                    in order to act, so it sits collapsed under the card
                    rather than filling the visible area. Missing evidence
                    stays visible, because a skipped check is a fact about
                    what the user is NOT being told.
                  */}
                  {(selectedIntelligence.condition.factors ?? [])
                    .length > 0 ||
                  selectedIntelligence.condition
                    .missing_evidence.length > 0 ? (
                    <details className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
                      <summary className="cursor-pointer list-none text-[11px] text-white/40">
                        How this condition was worked out
                      </summary>

                      {(selectedIntelligence.condition.factors ?? [])
                        .length > 0 ? (
                        <ul className="mt-3 space-y-2">
                          {(
                            selectedIntelligence.condition
                              .factors ?? []
                          ).map((factor) => (
                            <li
                              key={`${factor.factor}-${factor.state}`}
                              className="space-y-1 text-[11px] leading-5 text-white/45"
                            >
                              <span className="mr-2 inline-block rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[9px] uppercase tracking-[0.1em] text-white/50">
                                {CONDITION_STATE_LABELS[
                                  factor.state
                                ] ?? factor.state.replaceAll("_", " ")}
                              </span>
                              <span>
                                {factor.detail}
                              </span>
                            </li>
                          ))}
                        </ul>
                      ) : null}

                      {selectedIntelligence.condition
                        .missing_evidence.length > 0 ? (
                        <p className="mt-3 text-[11px] leading-5 text-amber-200/60">
                          Not available for this route:{" "}
                          {missingEvidenceText(
                            selectedIntelligence.condition
                              .missing_evidence
                          )}
                          . Those checks were skipped rather than guessed.
                        </p>
                      ) : null}
                    </details>
                  ) : null}

                  <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                    <p className="text-[10px] uppercase tracking-[0.15em] text-white/30">
                      Suitability
                    </p>
                    {(() => {
                      const { headline, tone } =
                        suitabilityHeadline(
                          selectedIntelligence.suitability
                            .level,
                        );
                      const decisive = selectDecisiveFactors(
                        selectedIntelligence.suitability
                          .factors,
                      );
                      const toneClass =
                        tone === "good"
                          ? "text-emerald-200"
                          : tone === "warn"
                            ? "text-amber-200"
                            : tone === "bad"
                              ? "text-rose-200"
                              : "text-white";
                      return (
                        <>
                          <p
                            className={`mt-3 text-3xl font-semibold tracking-[-0.03em] ${toneClass}`}
                          >
                            {headline}
                          </p>
                          <p className="mt-2 text-[12px] leading-5 text-white/50">
                            {
                              selectedIntelligence
                                .suitability.headline
                            }
                          </p>
                          {decisive.length > 0 ? (
                            <ul className="mt-3 space-y-2">
                              {decisive.map((factor) => (
                                <li
                                  key={factor.factor}
                                  className="text-[12px] leading-5 text-white/60"
                                >
                                  • {factor.evidence}
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </>
                      );
                    })()}
                    {/*
                      Scope only. The full factor list is a duplicate of the
                      reasons already shown above plus the sections they come
                      from, so it is not repeated here.
                    */}
                    <p className="mt-3 text-[10px] leading-4 text-white/25">
                      {selectedIntelligence.suitability.assessment_scope}
                    </p>
                  </div>

                  {selectedIntelligence.difficulty.ml.available ||
                  selectedIntelligence.difficulty.model_readiness.ready ? (
                    <details className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
                      <summary className="cursor-pointer list-none text-[10px] uppercase tracking-[0.15em] text-white/30">
                        How the difficulty estimate works
                      </summary>
                      <p className="mt-3 text-[11px] leading-5 text-white/35">
                        {selectedIntelligence.difficulty.reconciliation
                          ?.message ??
                          selectedIntelligence.difficulty.model_readiness
                            .message}
                      </p>
                      <dl className="mt-3 grid grid-cols-2 gap-2 text-[10px] text-white/25">
                        <dt>Native model tier</dt>
                        <dd className="text-white/40">
                          {selectedIntelligence.difficulty.ml.estimate ??
                            "unavailable"}
                        </dd>
                        <dt>
                          {selectedIntelligence.difficulty.ml
                            .confidence_kind ===
                          "member_agreement_share"
                            ? "Member agreement"
                            : "Model score"}
                        </dt>
                        <dd className="text-white/40">
                          {selectedIntelligence.difficulty.ml.available
                            ? selectedIntelligence.difficulty.ml
                                .confidence_kind ===
                              "member_agreement_share"
                              ? `${Math.round(
                                  (selectedIntelligence.difficulty.ml
                                    .confidence ?? 0) * 100,
                                )}% of scored sections`
                              : `${(
                                  selectedIntelligence.difficulty.ml
                                    .confidence ?? 0
                                ).toFixed(2)} (uncalibrated — relative, not a probability)`
                            : "n/a"}
                        </dd>
                        <dt>Model</dt>
                        <dd className="text-white/40">
                          {selectedIntelligence.difficulty.model_readiness
                            .model_name ??
                            selectedIntelligence.difficulty.ml
                              .model_name ??
                            "n/a"}
                        </dd>
                        <dt>Feature coverage</dt>
                        <dd className="text-white/40">
                          {Math.round(
                            (selectedIntelligence.difficulty.ml
                              .feature_coverage ?? 0) * 100
                          )}
                          %
                        </dd>
                        {(() => {
                          const reliability =
                            selectedIntelligence.difficulty.ml
                              .reliability;
                          const perClass = reliability?.per_class;
                          if (!perClass) {
                            return null;
                          }
                          return Object.entries(perClass).map(
                            ([tier, row]) => (
                              <div
                                key={tier}
                                className="col-span-2 flex items-baseline justify-between gap-3"
                              >
                                <dt className="truncate">
                                  {selectedIntelligence.difficulty
                                    .model_readiness.tier_labels?.[
                                    tier
                                  ] ?? tier}
                                </dt>
                                <dd className="shrink-0 text-white/40">
                                  precision {Math.round(row.precision * 100)}%
                                  {" · "}
                                  recall {Math.round(row.recall * 100)}%
                                </dd>
                              </div>
                            ),
                          );
                        })()}
                      </dl>
                      <p className="mt-3 text-[10px] leading-4 text-white/20">
                        The model predicts which difficulty tier a path&apos;s
                        recorded OpenStreetMap grade falls into: walkable,
                        mountain, or alpine. It reads only label-free
                        attributes of the path and its measured elevation
                        profile, and it was evaluated on regions held out of
                        training entirely. It is an estimate and never
                        overrides a recorded grade.
                      </p>
                      {/*
                        The measured comparison against the alternative a
                        reader would otherwise have used. Real OpenStreetMap is
                        dominated by easy graded ways, so "74% accurate" is
                        meaningless without the majority baseline beside it.
                      */}
                      {selectedIntelligence.difficulty.ml.reliability
                        ?.held_out_accuracy != null ? (
                        <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                          <p className="text-[10px] uppercase tracking-[0.12em] text-white/30">
                            Measured on real trails
                          </p>
                          <p className="mt-1.5 text-[10px] leading-4 text-white/40">
                            Scored on geographically held-out OpenStreetMap
                            ways, under the real grade mix rather than a
                            balanced one. Accuracy{" "}
                            {Math.round(
                              (selectedIntelligence.difficulty.ml.reliability
                                ?.held_out_accuracy ?? 0) * 100,
                            )}
                            % against{" "}
                            {Math.round(
                              (selectedIntelligence.difficulty.ml.reliability
                                ?.majority_baseline_accuracy ?? 0) * 100,
                            )}
                            % for always answering with the most common tier;
                            macro-F1{" "}
                            {Math.round(
                              (selectedIntelligence.difficulty.ml.reliability
                                ?.held_out_macro_f1 ?? 0) * 100,
                            )}
                            % against{" "}
                            {Math.round(
                              (selectedIntelligence.difficulty.ml.reliability
                                ?.majority_baseline_macro_f1 ?? 0) * 100,
                            )}
                            %. The alpine tier is its weakest, so treat an
                            alpine estimate as a prompt to check the route.
                          </p>
                        </div>
                      ) : null}
                      {selectedIntelligence.difficulty.model_readiness
                        .limitations ? (
                        <p className="mt-2 text-[10px] leading-4 text-white/20">
                          {selectedIntelligence.difficulty.model_readiness
                            .limitations}
                        </p>
                      ) : null}
                    </details>
                  ) : null}
                </div>
              ) : (
                <p className="mt-5 text-sm leading-7 text-white/45">
                  Select a verified trail to load route difficulty, condition likelihood, and suitability context.
                </p>
              )}

            </section>


            {/* ========================================================
                PREPARATION
            ======================================================== */}

            <section id="gear" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Preparation
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                What to bring
              </h3>

              {selectedIntelligence ? (
                <>
                  <p className="mt-2 max-w-[70ch] text-[12px] leading-6 text-white/40">
                    Built from this route&apos;s measured length, ascent,
                    steepest section and recorded surface, plus the weather
                    observed on it right now. Each item appears because
                    something about this route asked for it.
                  </p>

                  {(
                    [
                      ["essential", "Essential"],
                      ["recommended", "Recommended"],
                      ["conditional", "Only if relevant"],
                    ] as const
                  ).map(([tier, label]) => {
                    const group =
                      selectedIntelligence.gear.items.filter(
                        (item) => item.priority === tier
                      );
                    if (group.length === 0) {
                      return null;
                    }
                    return (
                      <div key={tier} className="mt-5">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-white/40">
                          {label}
                        </p>
                        <ul className="mt-2 space-y-1.5">
                          {group.map((item) => (
                            <li
                              key={`${item.category}-${item.item}`}
                              className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3"
                            >
                              <p className="text-[13px] font-semibold text-white/90">
                                {item.item}
                              </p>
                              <p className="mt-1 text-[12px] leading-5 text-white/45">
                                {item.reason}
                              </p>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}

                  {selectedIntelligence.gear.missing_evidence.length > 0 ? (
                    <p className="mt-4 text-[11px] leading-5 text-amber-200/60">
                      Not available for this route:{" "}
                      {missingEvidenceText(
                        selectedIntelligence.gear.missing_evidence
                      )}
                      . Recommendations that would have used them were left
                      out.
                    </p>
                  ) : null}
                </>
              ) : (
                <p className="mt-5 text-sm leading-7 text-white/45">
                  Select a verified trail to see the preparation its measured
                  route and current conditions actually justify.
                </p>
              )}

            </section>


            {/* ========================================================
                PRODUCTS
            ======================================================== */}

            <section id="products" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Products
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Shop the preparation that matters
              </h3>


              {selectedIntelligence ? (
                <div className="mt-5">
                  <p className="max-w-[72ch] text-[12px] leading-6 text-white/40">
                    Every item your route asked for has a card here: a
                    real product when the search found one, otherwise a
                    shopping destination for that kind of gear. Prices,
                    ratings and stock are never shown because the source
                    does not return them reliably and they are not invented.
                  </p>

                  <button
                    type="button"
                    onClick={() => void handleProductSearch()}
                    disabled={productsLoading}
                    className="mt-4 rounded-xl border border-emerald-300/20 bg-emerald-300/[0.08] px-4 py-2 text-xs font-semibold text-emerald-200 transition hover:bg-emerald-300/[0.14] disabled:cursor-wait disabled:opacity-60"
                  >
                    {productsLoading
                      ? "Searching products…"
                      : productResults
                        ? "Search again"
                        : "Find real products"}
                  </button>

                  {productsError ? (
                    <p className="mt-3 text-xs text-amber-200/70">
                      {productsError}
                    </p>
                  ) : null}

                  {productResults ? (
                    productResults.groups.length > 0 ? (
                      <div className="mt-5 space-y-5">
                        {(
                          [
                            ["essential", "Essential gear"],
                            ["recommended", "Recommended"],
                            ["conditional", "Only if relevant"],
                          ] as const
                        ).map(([tier, label]) => {
                          const cards =
                            productResults.groups.filter(
                              (group) =>
                                group.priority === tier &&
                                group.card
                            );
                          if (cards.length === 0) {
                            return null;
                          }
                          return (
                            <div key={tier}>
                              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-white/40">
                                {label}
                              </p>
                              <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                {cards.map((group) =>
                                  group.card ? (
                                    <ProductCard
                                      key={`${group.category}-${group.item}`}
                                      card={group.card}
                                    />
                                  ) : null
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="mt-4 text-sm text-white/40">
                        None of the preparation items for this trail is a
                        product that can be bought, so no product search was
                        made.
                      </p>
                    )
                  ) : (
                    <p className="mt-4 text-sm text-white/40">
                      Search for real products matching the preparation
                      recommended for this trail.
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-5 text-sm text-white/40">
                  Select a verified trail to see products for the
                  preparation it actually calls for.
                </p>
              )}

            </section>


            {/* ========================================================
                ASSISTANT
            ======================================================== */}

            <section id="assistant" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Assistant
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Ask about the trail
              </h3>


              {selectedIntelligence ? (
                <form
                  onSubmit={handleAssistantSubmit}
                  className="mt-5"
                >
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <input
                      value={assistantQuestion}
                      onChange={(event) =>
                        setAssistantQuestion(event.target.value)
                      }
                      placeholder="Ask about conditions, difficulty, or gear…"
                      maxLength={600}
                      className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none placeholder:text-white/25 focus:border-sky-300/40"
                    />
                    <button
                      type="submit"
                      disabled={
                        assistantLoading ||
                        !assistantQuestion.trim()
                      }
                      className="rounded-xl border border-sky-300/20 bg-sky-300/[0.08] px-4 py-2 text-xs font-semibold text-sky-200 transition hover:bg-sky-300/[0.14] disabled:cursor-wait disabled:opacity-60"
                    >
                      {assistantLoading ? "Thinking…" : "Ask"}
                    </button>
                  </div>
                  {assistantError ? (
                    <p className="mt-3 text-xs text-amber-200/70">
                      {assistantError}
                    </p>
                  ) : null}
                  {assistantAnswer ? (
                    <div className="mt-4 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
                      {assistantAnswer.status ===
                        "not_in_context" ? (
                        <p className="mb-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.05] px-3 py-2 text-[10px] leading-4 text-amber-100/75">
                          This question is not answered from the trail
                          data, so nothing was invented for it.
                        </p>
                      ) : null}
                      {/*
                        The answer is the conversation. Which engine
                        wrote the prose is a property of the system,
                        not something the person asking asked about, so
                        the generator label and the provider note are
                        not shown. The contract still carries
                        `grounded` and `generated_by` for anything that
                        needs them.
                      */}
                      <p className="whitespace-pre-line text-sm leading-6 text-white/75">
                        {assistantAnswer.answer}
                      </p>
                    </div>
                  ) : null}
                </form>
              ) : (
                <p className="mt-5 text-sm leading-7 text-white/45">
                  Select a verified trail to ask a grounded question about its intelligence.
                </p>
              )}

            </section>

          </section>
        )}

      {/* ============================================================
          FLOATING ASSISTANT
          Always reachable while exploring. Sits above the page flow and
          never covers the map, which is in the top panel.
      ============================================================ */}

      {selectedIntelligence ? (
        <div
          ref={assistantDockRef}
          className="pointer-events-none fixed bottom-5 right-5 z-40 flex w-[min(24rem,calc(100vw-2.5rem))] flex-col items-end gap-3"
        >
          {assistantOpen ? (
            <div className="pointer-events-auto max-h-[min(32rem,70vh)] w-full overflow-y-auto rounded-2xl border border-white/12 bg-[#0b1724]/97 p-4 shadow-[0_24px_60px_rgba(0,0,0,0.55)] backdrop-blur">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-[10px] uppercase tracking-[0.15em] text-white/35">
                    Trail assistant
                  </p>
                  <p className="mt-0.5 text-[12px] font-semibold text-white/85">
                    {selectedIntelligence.trail.name ??
                      "Selected trail"}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label="Close assistant"
                  onClick={() => setAssistantOpen(false)}
                  className="shrink-0 rounded-lg px-2 py-1 text-[11px] text-white/40 transition hover:bg-white/[0.07] hover:text-white"
                >
                  Close
                </button>
              </div>

              <p className="mt-2 text-[10px] leading-4 text-white/30">
                Answers come only from this trail&apos;s verified data.
              </p>

              <form
                onSubmit={handleAssistantSubmit}
                className="mt-3"
              >
                <div className="flex gap-2">
                  <input
                    value={assistantQuestion}
                    onChange={(event) =>
                      setAssistantQuestion(event.target.value)
                    }
                    placeholder="Ask about this trail…"
                    maxLength={600}
                    className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[13px] text-white outline-none placeholder:text-white/25 focus:border-sky-300/40"
                  />
                  <button
                    type="submit"
                    disabled={
                      assistantLoading ||
                      !assistantQuestion.trim()
                    }
                    className="shrink-0 rounded-xl border border-sky-300/20 bg-sky-300/[0.08] px-3 py-2 text-[11px] font-semibold text-sky-200 transition hover:bg-sky-300/[0.14] disabled:cursor-wait disabled:opacity-60"
                  >
                    {assistantLoading ? "…" : "Ask"}
                  </button>
                </div>
              </form>

              <div className="mt-3 flex flex-wrap gap-1.5">
                {suggestedQuestions(
                  selectedIntelligence
                ).map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() =>
                      setAssistantQuestion(prompt)
                    }
                    className="rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[10px] text-white/50 transition hover:border-sky-300/25 hover:text-white/80"
                  >
                    {prompt}
                  </button>
                ))}
              </div>

              {assistantError ? (
                <p className="mt-3 text-[11px] text-amber-200/70">
                  {assistantError}
                </p>
              ) : null}

              {assistantAnswer ? (
                <div className="mt-3 rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
                  <p className="whitespace-pre-line text-[12px] leading-5 text-white/75">
                    {assistantAnswer.answer}
                  </p>
                </div>
              ) : null}
            </div>
          ) : null}

          <button
            type="button"
            aria-label={
              assistantOpen
                ? "Hide trail assistant"
                : "Ask the trail assistant"
            }
            onClick={() => setAssistantOpen(!assistantOpen)}
            className="pointer-events-auto flex items-center gap-2 rounded-full border border-sky-300/25 bg-[#0d1825]/95 px-4 py-3 text-[12px] font-semibold text-sky-100 shadow-[0_12px_32px_rgba(0,0,0,0.45)] backdrop-blur transition hover:border-sky-300/50"
          >
            <span
              aria-hidden="true"
              className="grid h-5 w-5 place-items-center rounded-full bg-sky-300/15 text-[11px]"
            >
              ?
            </span>
            {assistantOpen
              ? "Hide assistant"
              : "Ask about this trail"}
          </button>
        </div>
      ) : null}

    </main>
  );
}
export default function ExplorePage() {
  return (
    <Suspense fallback={null}>
      <ExplorePageContent />
    </Suspense>
  );
}