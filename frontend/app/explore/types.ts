import type { Trail } from "@/components/TrailSidebar";

export type Location = {
  latitude: number;
  longitude: number;
};


export type SelectedTrail = Omit<
  Trail,
  "map_ready" | "geometry" | "distance_km" | "state"
> & {
  map_ready: true;
  state: "MAP_READY";
  distance_km: number;
  geometry: NonNullable<Trail["geometry"]>;
};


export type TrailGeometry = SelectedTrail;


export type MapTrail = {
  trail_id: string;
  osm_id: number | null;
  osm_type: "way" | "relation" | "component" | null;
  name: string | null;
  geometry: NonNullable<Trail["geometry"]>;
};


export type TrailDiscoveryResponse = {
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


export type WeatherCurrent = {
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


export type WeatherResponse = {
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
  // The worst hourly values over the estimated walking time. Inferred from the
  // forecast at sample points; nothing here is observed on the trail.
  window?: {
    hours: number;
    min_temperature: number | null;
    max_wind_speed: number | null;
    max_wind_gust: number | null;
    max_precipitation_mm: number | null;
    snowfall_cm: number | null;
    max_snow_depth_m: number | null;
    min_freezing_level_m: number | null;
    recent_snowfall_cm_72h: number | null;
  };
  inference?: {
    basis: string;
    window_hours: number;
    window_basis: string;
    highest_point_m: number | null;
    freezing_level_m: number | null;
    upper_route_above_freezing_level: boolean;
    margin_m: number | null;
    snow_on_route_likely: boolean;
    snow_reasons: string[];
  };
  aggregation?: "worst_case";
  sample_count?: number;
  samples?: Array<{
    labels: string[];
    latitude: number;
    longitude: number;
    elevation_m: number;
    temperature: number | null;
    wind_speed: number | null;
    snowfall: number | null;
    precipitation: number | null;
    weather_condition: string | null;
  }>;
};


export type SelectedTrailAnalysis = {
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
  completeness?: {
    status: "connected" | "gaps" | "separate_pieces";
    part_count: number;
    chain_count: number;
    largest_gap_km: number | null;
    total_gap_km: number | null;
    main_chain_share: number;
    note: string | null;
  };
};


export type ElevationProfilePoint = {
  component_index: number;
  component_distance_km: number;
  distance_km: number;
  longitude: number;
  latitude: number;
  elevation_m: number | null;
};


export type ElevationMetrics = {
  min_elevation_m: number | null;
  max_elevation_m: number | null;
  elevation_range_m: number | null;
  elevation_gain_m: number | null;
  elevation_loss_m: number | null;
  average_slope_percent: number | null;
  max_slope_percent: number | null;
  terrain_available: boolean;
};


export type ElevationResponse = {
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

export type TrailIntelligenceResponse = {
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
    // What the assessment covers: the estimated walk (cold, wind and snow from
    // the forecast over the walking time) or only the current reading.
    assessed_over?: "walk" | "now";
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
    assessed_over?: "walk" | "now";
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
    activity?: {
      type: "day_hike" | "multi_day_trek" | "high_altitude_trek" | "technical_alpine";
      label: string;
      query_term: string;
      reasons: string[];
    };
  };
  providers: Record<string, "ok" | "unavailable">;
};

export type ProductSearchResponse = {
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


export type AssistantResponse = {
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
