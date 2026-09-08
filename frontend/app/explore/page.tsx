"use client";

import {
  useState,
} from "react";

import {
  useSearchParams,
} from "next/navigation";

import ExploreSearch from "@/components/ExploreSearch";

import TrailSidebar, {
  Trail,
} from "@/components/TrailSidebar";

import CesiumMap from "@/components/CesiumMap";

const API_BASE_URL =
  "http://127.0.0.1:8000";

type Location = {
  latitude: number;
  longitude: number;
};

type TrailGeometry = {
  osm_id: number;

  osm_type:
    | "way"
    | "relation"
    | "component";

  name: string | null;

  route_type: string | null;

  highway_type: string | null;

  description: string | null;

  difficulty: string | null;

  surface: string | null;

  trail_visibility: string | null;

  distance_km: number;

  segment_count?: number;

  ordered_segment_count?: number;

  geometry: {
    type:
      | "LineString"
      | "MultiLineString";

    coordinates:
      | number[][]
      | number[][][];
  };
};

type MapTrail = {
  osm_id: number;

  osm_type:
    | "way"
    | "relation"
    | "component";

  geometry: {
    type:
      | "LineString"
      | "MultiLineString";

    coordinates:
      | number[][]
      | number[][][];
  };
};

type TrailDiscoveryResponse = {
  trails?: Trail[];

  map_trails?: MapTrail[];
};

const OUTDOOR_TERMS = [
  "peak",
  "mountain",
  "hill",
  "trail",
  "trek",
  "hiking",
  "hike",
  "summit",
  "waterfall",
  "falls",
  "forest",
  "viewpoint",
  "ridge",
  "pass",
  "gorge",
  "sanctuary",
  "reserve",
  "national park",
  "wildlife",
];

function isBroadPlaceSearch(
  query: string,
): boolean {
  const text =
    query
      .toLowerCase()
      .trim();

  if (!text) {
    return false;
  }

  if (
    OUTDOOR_TERMS.some(
      (
        term,
      ) =>
        text.includes(
          term,
        ),
    )
  ) {
    return false;
  }

  const tokens =
    text
      .split(/\s+/)
      .filter(Boolean);

  return (
    tokens.length <= 3
  );
}

export default function ExplorePage() {
  const searchParams =
    useSearchParams();

  const initialQuery =
    searchParams.get(
      "query",
    ) ??
    searchParams.get(
      "q",
    ) ??
    "";

  const [
    location,
    setLocation,
  ] = useState<
    Location | null
  >(null);

  const [
    locationName,
    setLocationName,
  ] = useState<
    string | null
  >(null);

  const [
    trails,
    setTrails,
  ] = useState<
    Trail[]
  >([]);

  const [
    mapTrails,
    setMapTrails,
  ] = useState<
    MapTrail[]
  >([]);

  const [
    loadingTrails,
    setLoadingTrails,
  ] = useState(false);

  const [
    trailError,
    setTrailError,
  ] = useState<
    string | null
  >(null);

  const [
    selectedTrailSummary,
    setSelectedTrailSummary,
  ] = useState<
    Trail | null
  >(null);

  const [
    selectedTrailGeometry,
    setSelectedTrailGeometry,
  ] = useState<
    TrailGeometry | null
  >(null);

  const [
    loadingTrail,
    setLoadingTrail,
  ] = useState(false);

  const [
    mapExpanded,
    setMapExpanded,
  ] = useState(false);

  async function discoverTrails(
    newLocation: Location,
    searchedQuery: string,
  ) {
    setTrails([]);

    setMapTrails([]);

    setSelectedTrailSummary(
      null,
    );

    setSelectedTrailGeometry(
      null,
    );

    setTrailError(
      null,
    );

    setLoadingTrails(
      true,
    );

    const broadSearch =
      isBroadPlaceSearch(
        searchedQuery,
      );

    /*
     * Specific outdoor searches:
     * tighter radius so the results are
     * actually around the requested place.
     *
     * Broad place searches:
     * larger radius so a place like Wayanad
     * is not treated as only a tiny 10 km point search.
     */
    const radius =
      broadSearch
        ? 25000
        : 8000;

    const scope =
      broadSearch
        ? "area"
        : "local";

    const params =
      new URLSearchParams({
        latitude:
          String(
            newLocation.latitude,
          ),

        longitude:
          String(
            newLocation.longitude,
          ),

        radius_m:
          String(
            radius,
          ),

        scope,
      });

    for (
      let attempt = 0;
      attempt < 2;
      attempt += 1
    ) {
      try {
        const response =
          await fetch(
            `${API_BASE_URL}/api/osm/trails?${params.toString()}`,
            {
              cache:
                "no-store",
            },
          );

        if (
          response.ok
        ) {
          const data =
            (await response.json()) as TrailDiscoveryResponse;

          const nextTrails =
            Array.isArray(
              data.trails,
            )
              ? data.trails
              : [];

          const nextMapTrails =
            Array.isArray(
              data.map_trails,
            )
              ? data.map_trails
              : [];

          setTrails(
            nextTrails,
          );

          setMapTrails(
            nextMapTrails,
          );

          setTrailError(
            nextTrails.length >
              0
              ? null
              : "No useful mapped trails were found in this search area.",
          );

          setLoadingTrails(
            false,
          );

          return;
        }

        if (
          response.status >=
            500 &&
          attempt === 0
        ) {
          await new Promise(
            (
              resolve,
            ) =>
              setTimeout(
                resolve,
                800,
              ),
          );

          continue;
        }

        throw new Error(
          `Trail discovery failed: ${response.status}`,
        );
      } catch (error) {
        if (
          attempt === 0
        ) {
          await new Promise(
            (
              resolve,
            ) =>
              setTimeout(
                resolve,
                800,
              ),
          );

          continue;
        }

        console.error(
          "Trail discovery failed:",
          error,
        );

        setTrails([]);

        setMapTrails([]);

        setTrailError(
          "Trail discovery is temporarily unavailable.",
        );

        setLoadingTrails(
          false,
        );

        return;
      }
    }

    setLoadingTrails(
      false,
    );
  }

  function handleLocationFound(
    newLocation: Location,
    newLocationName: string,
    searchedQuery: string,
  ) {
    /*
     * The map location updates immediately.
     * OSM loading happens separately.
     */
    setLocation(
      newLocation,
    );

    setLocationName(
      newLocationName,
    );

    void discoverTrails(
      newLocation,
      searchedQuery,
    );
  }

  async function handleTrailSelect(
    trail: Trail,
  ) {
    setSelectedTrailSummary(
      trail,
    );

    setSelectedTrailGeometry(
      null,
    );

    setLoadingTrail(
      true,
    );

    try {
      let url: string;

      if (
        trail.osm_type ===
        "way"
      ) {
        url =
          `${API_BASE_URL}/api/osm/trails/way/${trail.osm_id}`;
      } else if (
        trail.osm_type ===
        "relation"
      ) {
        url =
          `${API_BASE_URL}/api/osm/trails/relation/${trail.osm_id}`;
      } else if (
        trail.osm_type ===
        "component"
      ) {
        if (
          !trail.member_way_ids
            ?.length
        ) {
          throw new Error(
            "Selected trail contains no member way IDs.",
          );
        }

        const params =
          new URLSearchParams(
            {
              member_way_ids:
                trail.member_way_ids.join(
                  ",",
                ),

              trail_name:
                trail.name ??
                "Hiking trail",
            },
          );

        url =
          `${API_BASE_URL}/api/osm/trails/component/${trail.osm_id}?${params.toString()}`;
      } else {
        throw new Error(
          "Unsupported trail type.",
        );
      }

      const response =
        await fetch(
          url,
          {
            cache:
              "no-store",
          },
        );

      if (
        !response.ok
      ) {
        throw new Error(
          `Trail geometry failed: ${response.status}`,
        );
      }

      const data =
        (await response.json()) as TrailGeometry;

      setSelectedTrailGeometry(
        data,
      );
    } catch (error) {
      console.error(
        "Trail selection failed:",
        error,
      );

      setSelectedTrailGeometry(
        null,
      );
    } finally {
      setLoadingTrail(
        false,
      );
    }
  }

  return (
    <main className="min-h-screen bg-[#07111f] text-white">
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
            <div className="hidden max-w-[560px] truncate rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-xs text-white/55 md:block">
              {locationName}
            </div>
          )}
        </div>
      </header>

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

      {!mapExpanded && (
        <section className="mx-auto max-w-[1440px] px-6 py-7 md:px-10 lg:px-14">
          <div className="grid h-[620px] min-h-0 grid-cols-1 overflow-hidden rounded-[24px] border border-white/10 bg-[#0d1825] shadow-[0_25px_70px_rgba(0,0,0,0.22)] lg:grid-cols-[380px_minmax(0,1fr)]">
            <div className="min-h-0 overflow-hidden border-b border-white/10 lg:border-b-0 lg:border-r">
              <TrailSidebar
                trails={
                  trails
                }
                loading={
                  loadingTrails
                }
                selectedTrail={
                  selectedTrailSummary
                }
                onTrailSelect={
                  handleTrailSelect
                }
              />

              {trailError && (
                <div className="border-t border-white/10 bg-[#0b1724] px-5 py-3">
                  <p className="text-[11px] leading-5 text-white/45">
                    {trailError}
                  </p>
                </div>
              )}
            </div>

            <div className="relative min-h-0 overflow-hidden">
              <CesiumMap
                location={
                  location
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
                    true,
                  )
                }
              />

              {loadingTrails && (
                <div className="pointer-events-none absolute left-4 top-4 z-40 rounded-xl border border-white/10 bg-[#07111f]/90 px-3 py-2 text-xs text-white/65 shadow-xl backdrop-blur-xl">
                  Finding useful trails…
                </div>
              )}

              {loadingTrail && (
                <div className="pointer-events-none absolute bottom-4 left-4 z-40 rounded-xl border border-white/10 bg-[#07111f]/90 px-3 py-2 text-xs text-white/65 shadow-xl backdrop-blur-xl">
                  Loading selected trail…
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {mapExpanded && (
        <div className="fixed inset-0 z-[100] bg-black">
          <CesiumMap
            location={
              location
            }
            mapTrails={
              mapTrails
            }
            selectedTrail={
              selectedTrailGeometry
            }
            expanded
            onCollapse={() =>
              setMapExpanded(
                false,
              )
            }
          />
        </div>
      )}

      {!mapExpanded &&
        selectedTrailGeometry && (
          <section className="mx-auto max-w-[1440px] px-6 pb-20 md:px-10 lg:px-14">
            <section className="rounded-[24px] border border-white/10 bg-[#0d1825] p-6 md:p-8">
              <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_420px]">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                    Selected trail
                  </p>

                  <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em]">
                    {selectedTrailGeometry.name ??
                      selectedTrailSummary?.name ??
                      "Hiking trail"}
                  </h2>

                  <p className="mt-4 max-w-2xl text-sm leading-7 text-white/45">
                    The selected route is shown from the available OpenStreetMap geometry. Terrain, weather and analytical intelligence will be added below.
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Distance
                    </p>

                    <p className="mt-2 text-lg font-semibold">
                      {Number.isFinite(
                        selectedTrailGeometry.distance_km,
                      )
                        ? `${selectedTrailGeometry.distance_km.toFixed(
                            1,
                          )} km`
                        : "—"}
                    </p>
                  </div>

                  <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Difficulty
                    </p>

                    <p className="mt-2 text-sm text-white/70">
                      {selectedTrailGeometry.difficulty ??
                        "Not available"}
                    </p>
                  </div>

                  <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Surface
                    </p>

                    <p className="mt-2 text-sm text-white/70">
                      {selectedTrailGeometry.surface ??
                        "Not available"}
                    </p>
                  </div>

                  <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
                    <p className="text-[10px] uppercase tracking-[0.14em] text-white/30">
                      Route
                    </p>

                    <p className="mt-2 text-sm text-white/70">
                      {selectedTrailGeometry.route_type ??
                        "Trail path"}
                    </p>
                  </div>
                </div>
              </div>
            </section>

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">
              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Conditions
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Weather & trail conditions
              </h3>

              <p className="mt-2 text-sm leading-7 text-white/45">
                Current weather, forecast rainfall and data-driven wetness likelihood will be integrated here.
              </p>
            </section>

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">
              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Terrain
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Elevation & slope
              </h3>

              <p className="mt-2 text-sm leading-7 text-white/45">
                Elevation gain, average slope, maximum slope and terrain characteristics will be derived from DEM data.
              </p>
            </section>

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">
              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Intelligence
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Difficulty & suitability
              </h3>

              <p className="mt-2 text-sm leading-7 text-white/45">
                Difficulty estimation and suitability will combine route, terrain, weather, rainfall, condition likelihood and user requirements.
              </p>
            </section>

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">
              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Preparation
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Required gear
              </h3>

              <p className="mt-2 text-sm leading-7 text-white/45">
                Gear requirements will be derived from the analytical outputs rather than generated independently.
              </p>
            </section>

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">
              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Products
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Relevant outdoor products
              </h3>

              <p className="mt-2 text-sm leading-7 text-white/45">
                Required gear categories will later feed a product-discovery layer returning real products, images and purchase links.
              </p>
            </section>

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">
              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Assistant
              </p>

              <h3 className="mt-3 text-[22px] font-semibold">
                Ask about the trail
              </h3>

              <p className="mt-2 text-sm leading-7 text-white/45">
                Later, RAG will retrieve trail, weather, analytical and gear evidence before the LLM produces a grounded explanation.
              </p>
            </section>
          </section>
        )}
    </main>
  );
}