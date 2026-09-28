"use client";

type Trail = {
  trail_id: string;
  osm_id: number | null;
  osm_type: "way" | "relation" | "component" | null;

  name: string | null;
  aliases?: string[];

  candidate_type: string;
  priority_tier: number;
  relevance_score: number;

  route_type: string | null;
  highway_type: string | null;
  description: string | null;

  source_difficulty: string | null;
  difficulty: string | null;
  surface: string | null;
  smoothness?: string | null;
  tracktype?: string | null;
  incline?: string | null;
  incline_pct?: number | null;
  incline_direction?: string | null;
  width?: string | null;
  width_m?: number | null;
  assisted_trail?: string | null;
  trail_visibility: string | null;
  source?: string;

  operator: string | null;
  network: string | null;

  length_km: number | null;
  distance_km?: number | null;
  distance_from_search_km: number | null;

  segment_count?: number | null;
  ordered_segment_count?: number | null;
  member_way_ids?: number[];
  ordered_way_ids?: number[];
  relation_members?: Array<{
    type: string;
    ref: number;
    role: string | null;
  }>;
  osm_names?: string[];

  map_ready: boolean;
  state: "MAP_READY" | "UNMAPPED";
  evidence_class?: "strong" | "weak" | "none";
  unmapped_kind?: string;
  peak_association?:
    | "summit_route"
    | "peak_approach"
    | "nearby_route";
  peak_closest_approach_m?: number;
  relation_completeness?: {
    member_count: number;
    network: string | null;
    route: string | null;
    state: string;
    note: string | null;
    source_length_km: number | null;
    basis: string;
  };
  geometry_resolution?: {
    attempted: boolean;
    sources_tried: string[];
    names_tried: string[];
    matched_geometry: boolean;
    reason: string;
  };
  geometry: {
    type: "LineString" | "MultiLineString";
    coordinates: number[][] | number[][][];
  } | null;
  geometry_hash?: string | null;
  geometry_provenance?: string | null;
  evidence?: string[];
};

export type { Trail };

import {
  buildTrailGroups,
  type TrailGroup,
} from "./trailIdentity";

type TrailSidebarProps = {
  trails: Trail[];
  loading: boolean;

  selectedTrail: Trail | null;

  onTrailSelect: (
    trail: Trail
  ) => void;

  counts?: ResultCounts;
};

function formatDistance(
  value: unknown
): string {
  const distance =
    Number(value);

  if (
    !Number.isFinite(distance)
  ) {
    return "—";
  }

  if (distance < 1) {
    return `${Math.round(
      distance * 1000
    )} m`;
  }

  return `${distance.toFixed(1)} km`;
}

function humanize(
  value: string | null
): string {
  if (!value) {
    return "";
  }

  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) =>
      letter.toUpperCase()
    );
}

function trailLabel(trail: Trail): string {
  if (trail.name?.trim()) {
    return trail.name.trim();
  }

  if (trail.candidate_type === "trail_network") {
    return "Connected trail network";
  }

  if (trail.highway_type === "track") {
    return "Unnamed outdoor track";
  }

  return "Unnamed OSM hiking route";
}

function trailType(
  trail: Trail
): string {
  if (
    trail.candidate_type ===
    "hiking_route"
  ) {
    return "Hiking route";
  }

  if (
    trail.candidate_type ===
    "walking_route"
  ) {
    return "Walking route";
  }

  if (
    trail.candidate_type ===
    "trail_network"
  ) {
    return "Trail network";
  }

  if (
    trail.candidate_type ===
    "named_local_path"
  ) {
    return "Named local path";
  }

  if (
    trail.highway_type ===
    "track"
  ) {
    return "Track";
  }

  return "Trail path";
}

/**
 * Map a raw OSM `sac_scale` value to a human difficulty class.
 *
 * `sac_scale` values are activity/technical grades, not difficulty words.
 * Showing "hiking" under a Difficulty label is wrong: it is the route
 * classification, not a difficulty value. This mirrors the backend
 * SAC_TO_CLASS mapping in `backend/app/services/difficulty.py`, and it is
 * exported so every surface in the interface renders the recorded grade the
 * same way instead of each place inventing its own wording.
 */
const SAC_SCALE_TO_DIFFICULTY: Record<
  string,
  string
> = {
  strolling: "Easy",
  hiking: "Easy",
  mountain_hiking: "Moderate",
  demanding_mountain_hiking: "Hard",
  alpine_hiking: "Very Hard",
  demanding_alpine_hiking: "Very Hard",
  difficult_alpine_hiking: "Very Hard",
  // An older four-level vocabulary, accepted so a value from an
  // unrecognised producer still reads as something rather than
  // "unknown". The model itself emits only the seven native grades.
  easy: "Easy",
  moderate: "Moderate",
  hard: "Hard",
  very_hard: "Very Hard",
};


export function difficultyLabel(
  difficulty: string | null
): string {
  if (!difficulty) {
    return "Not available";
  }

  return SAC_SCALE_TO_DIFFICULTY[
    difficulty
      .toLowerCase()
      .replaceAll(" ", "_")
  ] ?? humanize(difficulty);
}

export type ResultCounts = {
  relevance_accepted: number;
  mapped: number;
  unmapped: number;
  ranked: number;
  shown: number;
  weak_evidence: number;
};

/**
 * Identity-first grouping: one verified OSM trail identity is one primary
 * card. See `./trailIdentity` for the precedence rules. Distinct relations,
 * distinct ways, components and unmapped candidates are never merged by
 * name or proximity; same-named member ways of an accepted route are
 * already folded into the route card by the backend and never arrive here
 * as competing objects.
 */


export default function TrailSidebar({
  trails,
  loading,
  selectedTrail,
  onTrailSelect,
  counts,
}: TrailSidebarProps) {
  /*
   * Groups are computed once and shared by the header counts and the list
   * below, so the numbers on screen always describe the cards on screen.
   * Identity-first: every group holds exactly one verified trail identity,
   * so the mapped count below is a count of primary trails, not of
   * presentation duplicates.
   */
  const groups: Array<TrailGroup<Trail>> = buildTrailGroups(
    trails,
    trailLabel
  );
  const mappedCount = trails.filter(
    (trail) => trail.map_ready
  ).length;

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[#0b1724]">
      <div className="shrink-0 border-b border-white/10 px-5 py-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.2em] text-white/35">
              Discover
            </p>

            <h2 className="mt-1 text-lg font-semibold tracking-[-0.025em]">
              Trails
            </h2>
          </div>

          <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[11px] text-white/45">
            {loading
              ? "Searching…"
              : counts
                ? `${counts.relevance_accepted} found`
                : `${trails.length} found`}
          </div>
        </div>

        {counts ? (
          <>
            {/*
              Two numbers only, because they partition the total exactly.
              Weak-evidence trails are a SUBSET of the verified ones, so
              listing it as a third peer number made 47 look like it did not
              add up.
            */}
            <div className="mt-3 grid grid-cols-2 gap-1.5">
              {(
                [
                  [
                    "On the map",
                    mappedCount,
                    "text-sky-200/90",
                    "Primary routes with identity and shape checked against OpenStreetMap",
                  ],
                  [
                    "No shape yet",
                    counts.unmapped,
                    "text-white/45",
                    "Real trails, but no trustworthy geometry found",
                  ],
                ] as const
              ).map(
                ([label, value, tone, help]) => (
                  <div
                    key={label}
                    className="rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 py-2"
                    title={help}
                  >
                    <p
                      className={`text-[15px] font-semibold leading-none ${tone}`}
                    >
                      {value.toLocaleString()}
                    </p>
                    <p className="mt-1 text-[9px] leading-3 text-white/40">
                      {label}
                    </p>
                  </div>
                )
              )}
            </div>

            {counts.weak_evidence > 0 ? (
              <p className="mt-2 text-[10px] leading-4 text-amber-200/60">
                {counts.weak_evidence} mapped section
                {counts.weak_evidence === 1 ? "" : "s"} rely on
                weaker hiking-tag evidence, though the route shape
                itself is verified.
              </p>
            ) : null}
          </>
        ) : null}

        <p className="mt-3 text-xs leading-5 text-white/40">
          {counts
            ? `${counts.relevance_accepted.toLocaleString()} relevant trail${
                counts.relevance_accepted === 1 ? "" : "s"
              } found in the searched area, shown as ${mappedCount} route${
                mappedCount === 1 ? "" : "s"
              } on the map and ${counts.unmapped} without verified shape yet.`
            : "Available paths are ranked using the geographic and OpenStreetMap evidence returned for the searched area."}
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {loading && (
          <div className="space-y-3">
            {Array.from({
              length: 6,
            }).map((_, index) => (
              <div
                key={index}
                className="animate-pulse rounded-2xl border border-white/10 bg-white/[0.035] p-4"
              >
                <div className="h-4 w-3/4 rounded bg-white/10" />

                <div className="mt-3 h-3 w-1/2 rounded bg-white/[0.07]" />

                <div className="mt-4 h-10 rounded-xl bg-white/[0.06]" />
              </div>
            ))}
          </div>
        )}

        {!loading &&
          trails.length === 0 && (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.025] px-5 py-8 text-center">
              <p className="text-sm text-white/55">
                Search a location to discover available
                trails.
              </p>

              <p className="mt-2 text-xs leading-5 text-white/30">
                Trail coverage depends on the underlying
                geographic data.
              </p>
            </div>
          )}

        {!loading &&
          trails.length > 0 &&
            /*
             * The on-screen ordinal is the ranking position of the trail
             * identity itself, so the number on the card and the number in
             * the map's provenance list refer to the same OSM object.
             */
            <div className="space-y-4">
              {groups.map((group, index) => {
                /*
                 * Identity-first: each group holds exactly one verified
                 * trail identity, rendered as its own primary card.
                 * Distinct relations, ways, components and unmapped
                 * candidates are never merged by name or proximity.
                 */
                const trail = group.primary;
                const selected =
                  selectedTrail?.trail_id === trail.trail_id;
                return (
                  <button
                    key={trail.trail_id}
                    type="button"
                    disabled={!trail.map_ready || !trail.geometry}
                    onClick={() =>
                      onTrailSelect(
                        trail
                      )
                    }
                    className={[
                      "w-full rounded-2xl border p-4 text-left transition-all",
                      selected
                        ? "border-[#ff9f43]/45 bg-[#ff9f43]/[0.09] shadow-[0_12px_30px_rgba(0,0,0,0.18)]"
                        : "border-white/10 bg-white/[0.025] hover:border-white/20 hover:bg-white/[0.045]",
                      !trail.map_ready || !trail.geometry
                        ? "cursor-not-allowed opacity-65 hover:border-white/10 hover:bg-white/[0.025]"
                        : "",
                    ].join(" ")}
                  >
                    <div className="flex items-start gap-3">
                      <div
                        className={[
                          "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                          selected
                            ? "bg-[#ff9f43] text-[#0b1724]"
                            : "bg-white/[0.08] text-white/45",
                        ].join(" ")}
                      >
                        {index + 1}
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-3">
                          <h3 className="line-clamp-2 text-sm font-semibold leading-5 text-white">
                            {/*
                              The card carries the real trail name plus its
                              authoritative OSM identity, so provenance is
                              visible without a second competing card.
                            */}
                            {trailLabel(trail)}
                            <span className="mt-0.5 block text-[10px] font-normal leading-4 text-white/35">
                              {trailType(trail)}
                              {trail.osm_type
                                ? ` · OSM ${
                                    trail.osm_type === "relation"
                                      ? "relation"
                                      : trail.osm_type === "component"
                                        ? "connected component"
                                        : "way"
                                  }${trail.osm_id == null ? "" : ` ${trail.osm_id}`}`
                                : ""}
                            </span>
                          </h3>

                          {selected ? (
                            <span className="shrink-0 rounded-full bg-[#ff9f43]/15 px-2 py-1 text-[9px] font-medium uppercase tracking-[0.12em] text-[#ffb66d]">
                              Selected
                            </span>
                          ) : trail.state === "UNMAPPED" ? (
                            <span
                              className="shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-1 text-[9px] font-medium uppercase tracking-[0.12em] text-white/40"
                              title={
                                trail.geometry_resolution?.reason ??
                                "Named externally but no verified OpenStreetMap geometry was found"
                              }
                            >
                              Unmapped
                            </span>
                          ) : trail.evidence_class === "weak" ? (
                            <span
                              className="shrink-0 rounded-full border border-amber-300/20 bg-amber-300/[0.07] px-2 py-1 text-[9px] font-medium uppercase tracking-[0.12em] text-amber-200/80"
                              title="Real verified geometry, but the OSM tags give weak hiking evidence"
                            >
                              Weak evidence
                            </span>
                          ) : null}
                        </div>

                        <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-white/40">
                          <span>
                            {trailType(
                              trail
                            )}
                          </span>

                          <span className="text-white/15">
                            •
                          </span>

                          <span>
                            {formatDistance(
                              trail.length_km
                            )}
                          </span>

                          <span className="text-white/15">
                            •
                          </span>

                          <span>
                            {formatDistance(
                              trail.distance_from_search_km
                            )}{" "}
                            away
                          </span>
                        </div>

                        <div className="mt-4 grid grid-cols-2 gap-2">
                          <div className="rounded-xl border border-white/[0.07] bg-black/10 px-3 py-2.5">
                            <p className="text-[9px] uppercase tracking-[0.12em] text-white/25">
                              Difficulty
                            </p>

                            <p className="mt-1 text-[11px] font-medium text-white/70">
                              {difficultyLabel(
                                trail.difficulty
                              )}
                            </p>
                            {trail.difficulty ? (
                              <p className="mt-0.5 text-[9px] text-white/25">
                                Official OSM scale
                              </p>
                            ) : null}
                          </div>

                          <div className="rounded-xl border border-white/[0.07] bg-black/10 px-3 py-2.5">
                            <p className="text-[9px] uppercase tracking-[0.12em] text-white/25">
                              Surface
                            </p>

                            <p className="mt-1 truncate text-[11px] font-medium text-white/70">
                              {trail.surface ??
                                "Not available"}
                            </p>
                          </div>
                        </div>

                        {(trail.trail_visibility ||
                          trail.network ||
                          trail.operator) && (
                          <div className="mt-3 flex flex-wrap gap-2">
                            {trail.peak_association ? (
                              <span
                                className={`rounded-full border px-2.5 py-1 text-[9px] ${
                                  trail.peak_association ===
                                  "summit_route"
                                    ? "border-sky-300/30 bg-sky-300/10 text-sky-200"
                                    : trail.peak_association ===
                                        "peak_approach"
                                      ? "border-white/15 bg-white/[0.05] text-white/60"
                                      : "border-white/10 bg-white/[0.03] text-white/35"
                                }`}
                                title={
                                  trail.peak_closest_approach_m != null
                                    ? `Closest approach to the searched summit: ${
                                        Math.round(
                                          trail.peak_closest_approach_m
                                        )
                                      } m, measured on the route geometry`
                                    : undefined
                                }
                              >
                                {trail.peak_association ===
                                "summit_route"
                                  ? "Reaches the summit"
                                  : trail.peak_association ===
                                      "peak_approach"
                                    ? "Approaches the summit"
                                    : "In the surrounding area"}
                                {trail.peak_closest_approach_m != null
                                  ? ` · ${(
                                      trail.peak_closest_approach_m /
                                      1000
                                    ).toFixed(1)} km away`
                                  : ""}
                              </span>
                            ) : null}
                            {trail.surface && (
                              <span className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-[9px] text-white/35">
                                {humanize(
                                  trail.surface
                                )}
                              </span>
                            )}

                            {trail.trail_visibility && (
                              <span className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-[9px] text-white/35">
                                Visibility:{" "}
                                {humanize(
                                  trail.trail_visibility
                                )}
                              </span>
                            )}

                            {trail.network && (
                              <span className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-[9px] text-white/35">
                                {trail.network}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          }
      </div>

      <div className="shrink-0 border-t border-white/10 px-5 py-4">
        <p className="text-[9px] leading-4 text-white/25">
          Trail geometry and attributes come from
          OpenStreetMap. Display names may use the
          searched destination when an individual mapped
          path has no useful name.
        </p>
      </div>
    </aside>
  );
}