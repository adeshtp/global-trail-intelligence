"use client";

type Trail = {
  osm_id: number;

  osm_type:
    | "way"
    | "relation"
    | "component";

  name: string | null;

  candidate_type: string;

  priority_tier: number;

  relevance_score: number;

  route_type: string | null;

  highway_type: string | null;

  description: string | null;

  difficulty: string | null;

  surface: string | null;

  trail_visibility: string | null;

  operator: string | null;

  network: string | null;

  length_km: number;

  distance_from_search_km:
    | number
    | null;

  hiking_evidence?: number;

  evidence_reasons?: string[];

  member_way_ids?: number[];
};

export type {
  Trail,
};

type TrailSidebarProps = {
  trails: Trail[];

  loading: boolean;

  selectedTrail:
    | Trail
    | null;

  onTrailSelect: (
    trail: Trail,
  ) => void;
};

function formatDistance(
  value: unknown,
): string {
  const distance =
    Number(value);

  if (
    !Number.isFinite(
      distance,
    )
  ) {
    return "—";
  }

  if (
    distance < 1
  ) {
    return `${Math.round(
      distance * 1000,
    )} m`;
  }

  return `${distance.toFixed(
    1,
  )} km`;
}

function humanize(
  value: string | null,
): string {
  if (!value) {
    return "";
  }

  return value
    .replaceAll(
      "_",
      " ",
    )
    .replace(
      /\b\w/g,
      (
        letter,
      ) =>
        letter.toUpperCase(),
    );
}

function trailLabel(
  trail: Trail,
): string {
  const name =
    trail.name?.trim();

  if (name) {
    return name;
  }

  switch (
    trail.candidate_type
  ) {
    case "hiking_route":
      return "Hiking route";

    case "walking_route":
      return "Walking trail";

    case "trail_network":
      return "Hiking trail";

    case "path_network":
      return "Outdoor trail";

    case "hiking_path":
      return "Hiking path";

    case "footway_trail":
      return "Hiking footway";

    case "track_trail":
      return "Trail track";

    default:
      return "Hiking trail";
  }
}

function trailType(
  trail: Trail,
): string {
  switch (
    trail.candidate_type
  ) {
    case "hiking_route":
      return "Hiking route";

    case "walking_route":
      return "Walking trail";

    case "trail_network":
      return "Hiking trail network";

    case "path_network":
      return "Connected path";

    case "hiking_path":
      return "Hiking path";

    case "footway_trail":
      return "Footway trail";

    case "track_trail":
      return "Trail track";

    default:
      return trail.highway_type ===
        "footway"
        ? "Footway"
        : "Outdoor path";
  }
}

function difficultyLabel(
  value: string | null,
): string {
  if (!value) {
    return "Not available";
  }

  const map: Record<
    string,
    string
  > = {
    easy: "Easy",
    moderate: "Moderate",
    difficult: "Difficult",
    hiking: "Hiking",
    mountain_hiking:
      "Mountain hiking",
    demanding_mountain_hiking:
      "Demanding mountain hiking",
    alpine_hiking:
      "Alpine hiking",
    demanding_alpine_hiking:
      "Demanding alpine hiking",
  };

  return (
    map[
      value.toLowerCase()
    ] ??
    humanize(
      value,
    )
  );
}

function evidenceLabel(
  trail: Trail,
): string {
  if (
    trail.candidate_type ===
    "hiking_route"
  ) {
    return "Mapped hiking route";
  }

  if (
    trail.candidate_type ===
    "walking_route"
  ) {
    return "Walking route evidence";
  }

  if (
    trail.priority_tier ===
    2
  ) {
    return "Strong hiking evidence";
  }

  if (
    trail.candidate_type ===
    "footway_trail"
  ) {
    return "Hiking-compatible footway";
  }

  if (
    trail.candidate_type ===
    "track_trail"
  ) {
    return "Outdoor track";
  }

  if (
    trail.candidate_type ===
    "trail_network"
  ) {
    return "Connected hiking geometry";
  }

  if (
    trail.candidate_type ===
    "path_network"
  ) {
    return "Connected outdoor geometry";
  }

  return "Mapped outdoor path";
}

export default function TrailSidebar({
  trails,
  loading,
  selectedTrail,
  onTrailSelect,
}: TrailSidebarProps) {
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
              : `${trails.length} found`}
          </div>
        </div>

        <p className="mt-3 text-xs leading-5 text-white/40">
          Hiking routes are prioritised first, followed by connected hiking paths, hiking-compatible footways and lower-confidence outdoor paths.
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {loading && (
          <div className="space-y-3">
            {Array.from({
              length: 6,
            }).map(
              (
                _,
                index,
              ) => (
                <div
                  key={index}
                  className="animate-pulse rounded-2xl border border-white/10 bg-white/[0.035] p-4"
                >
                  <div className="h-4 w-3/4 rounded bg-white/10" />

                  <div className="mt-3 h-3 w-1/2 rounded bg-white/[0.07]" />

                  <div className="mt-4 h-10 rounded-xl bg-white/[0.06]" />
                </div>
              ),
            )}
          </div>
        )}

        {!loading &&
          trails.length ===
            0 && (
            <div className="rounded-2xl border border-dashed border-white/10 bg-white/[0.025] px-5 py-8 text-center">
              <p className="text-sm text-white/55">
                No useful mapped trails were found here.
              </p>

              <p className="mt-2 text-xs leading-5 text-white/30">
                Trail coverage and tagging vary by location.
              </p>
            </div>
          )}

        {!loading &&
          trails.length >
            0 && (
            <div className="space-y-3">
              {trails.map(
                (
                  trail,
                  index,
                ) => {
                  const selected =
                    selectedTrail?.osm_id ===
                      trail.osm_id &&
                    selectedTrail?.osm_type ===
                      trail.osm_type;

                  return (
                    <button
                      key={`${trail.osm_type}-${trail.osm_id}`}
                      type="button"
                      onClick={() =>
                        onTrailSelect(
                          trail,
                        )
                      }
                      className={[
                        "w-full rounded-2xl border p-4 text-left transition-all",
                        selected
                          ? "border-[#ff9f43]/50 bg-[#ff9f43]/[0.09] shadow-[0_12px_30px_rgba(0,0,0,0.18)]"
                          : "border-white/10 bg-white/[0.025] hover:border-white/20 hover:bg-white/[0.045]",
                      ].join(
                        " ",
                      )}
                    >
                      <div className="flex items-start gap-3">
                        <div
                          className={[
                            "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                            selected
                              ? "bg-[#ff9f43] text-[#0b1724]"
                              : "bg-white/[0.08] text-white/45",
                          ].join(
                            " ",
                          )}
                        >
                          {index +
                            1}
                        </div>

                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-3">
                            <h3 className="line-clamp-2 text-sm font-semibold leading-5 text-white">
                              {trailLabel(
                                trail,
                              )}
                            </h3>

                            {selected && (
                              <span className="shrink-0 rounded-full bg-[#ff9f43]/15 px-2 py-1 text-[9px] font-medium uppercase tracking-[0.12em] text-[#ffb66d]">
                                Selected
                              </span>
                            )}
                          </div>

                          <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-white/40">
                            <span>
                              {trailType(
                                trail,
                              )}
                            </span>

                            <span className="text-white/15">
                              •
                            </span>

                            <span>
                              {formatDistance(
                                trail.length_km,
                              )}
                            </span>

                            {trail.distance_from_search_km !==
                              null && (
                              <>
                                <span className="text-white/15">
                                  •
                                </span>

                                <span>
                                  {formatDistance(
                                    trail.distance_from_search_km,
                                  )}{" "}
                                  from search
                                </span>
                              </>
                            )}
                          </div>

                          <div className="mt-4 grid grid-cols-2 gap-2">
                            <div className="rounded-xl border border-white/[0.07] bg-black/10 px-3 py-2.5">
                              <p className="text-[9px] uppercase tracking-[0.12em] text-white/25">
                                Difficulty
                              </p>

                              <p className="mt-1 text-[11px] font-medium text-white/70">
                                {difficultyLabel(
                                  trail.difficulty,
                                )}
                              </p>
                            </div>

                            <div className="rounded-xl border border-white/[0.07] bg-black/10 px-3 py-2.5">
                              <p className="text-[9px] uppercase tracking-[0.12em] text-white/25">
                                Surface
                              </p>

                              <p className="mt-1 truncate text-[11px] font-medium text-white/70">
                                {trail.surface
                                  ? humanize(
                                      trail.surface,
                                    )
                                  : "Not available"}
                              </p>
                            </div>
                          </div>

                          <div className="mt-3 flex flex-wrap gap-2">
                            <span className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-[9px] text-white/40">
                              {evidenceLabel(
                                trail,
                              )}
                            </span>

                            {trail.trail_visibility && (
                              <span className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-[9px] text-white/35">
                                Visibility:{" "}
                                {humanize(
                                  trail.trail_visibility,
                                )}
                              </span>
                            )}

                            {trail.network && (
                              <span className="rounded-full border border-white/10 bg-white/[0.035] px-2.5 py-1 text-[9px] text-white/35">
                                {trail.network}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                },
              )}
            </div>
          )}
      </div>

      <div className="shrink-0 border-t border-white/10 px-5 py-4">
        <p className="text-[9px] leading-4 text-white/25">
          Trail geometry and attributes come from OpenStreetMap. Ranking is a project-level relevance filter, not an official trail certification.
        </p>
      </div>
    </aside>
  );
}