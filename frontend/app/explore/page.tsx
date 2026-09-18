"use client";

import {
  Suspense,
  useEffect,
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

  start_coordinate?: [
    number,
    number
  ] | null;

  end_coordinate?: [
    number,
    number
  ] | null;

  endpoint_available?: boolean;

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
};


type ElevationProfilePoint = {
  distance_km: number;

  elevation_m: number;
};


type ElevationMetrics = {
  min_elevation_m: number | null;

  max_elevation_m: number | null;

  elevation_gain_m: number | null;

  elevation_loss_m: number | null;

  average_slope_percent: number | null;

  maximum_slope_percent: number | null;
};


type ElevationResponse = {
  source: string;

  sampled_points: number;

  profile: ElevationProfilePoint[];

  metrics: ElevationMetrics;
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
    loadingTrail,
    setLoadingTrail,
  ] = useState(false);


  const [
    mapExpanded,
    setMapExpanded,
  ] = useState(false);


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


  /*
   * ============================================================
   * TRAIL DISCOVERY
   * ============================================================
   */

  async function discoverTrails(
    newLocation: Location,
    searchQuery: string,
    newLocationName: string,
    broadAreaSearch: boolean
  ) {

    setTrails([]);

    setMapTrails([]);

    setSelectedTrailSummary(null);

    setSelectedTrailGeometry(null);

    setWeather(null);

    setWeatherError(null);

    setElevation(null);

    setElevationError(null);

    setTrailError(null);

    setLoadingTrails(true);


    const params =
      new URLSearchParams({
        latitude:
          String(
            newLocation.latitude
          ),

        longitude:
          String(
            newLocation.longitude
          ),

        radius_m:
          broadAreaSearch
            ? "25000"
            : "10000",

        scope:
          broadAreaSearch
            ? "area"
            : "local",

        search_query:
          searchQuery,

        location_name:
          newLocationName,
      });


    /*
     * Only one retry.
     */

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
            }
          );


        if (
          response.ok
        ) {

          const data =
            (
              await response.json()
            ) as TrailDiscoveryResponse;


          setTrails(
            Array.isArray(
              data.trails
            )
              ? data.trails
              : []
          );


          setMapTrails(
            Array.isArray(
              data.map_trails
            )
              ? data.map_trails
              : []
          );


          setTrailError(
            null
          );


          setLoadingTrails(
            false
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
              resolve
            ) =>
              setTimeout(
                resolve,
                800
              )
          );

          continue;
        }


        throw new Error(
          `Trail discovery failed: ${response.status}`
        );

      } catch (
        error
      ) {

        if (
          attempt === 0
        ) {

          await new Promise(
            (
              resolve
            ) =>
              setTimeout(
                resolve,
                800
              )
          );

          continue;
        }


        console.error(
          "Trail discovery failed:",
          error
        );


        setTrails([]);

        setMapTrails([]);


        setTrailError(
          "Trail discovery is temporarily unavailable."
        );


        setLoadingTrails(
          false
        );


        return;
      }
    }


    setLoadingTrails(false);
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
    broadAreaSearch: boolean
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
      broadAreaSearch
    );
  }


  /*
   * ============================================================
   * TRAIL SELECTION
   * ============================================================
   */

  async function handleTrailSelect(
    trail: Trail
  ) {

    setSelectedTrailSummary(
      trail
    );


    setSelectedTrailGeometry(
      null
    );


    setWeather(
      null
    );


    setWeatherError(
      null
    );


    setElevation(
      null
    );


    setElevationError(
      null
    );


    setLoadingTrail(
      true
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
          !trail.member_way_ids ||
          trail.member_way_ids.length ===
            0
        ) {

          throw new Error(
            "Selected trail contains no member way IDs."
          );
        }


        const params =
          new URLSearchParams({
            member_way_ids:
              trail.member_way_ids.join(
                ","
              ),

            trail_name:
              trail.name ??
              "Unnamed hiking trail",
          });


        url =
          `${API_BASE_URL}/api/osm/trails/component/${trail.osm_id}?${params.toString()}`;

      } else {

        throw new Error(
          "Unsupported trail type."
        );
      }


      const response =
        await fetch(
          url,
          {
            cache:
              "no-store",
          }
        );


      if (
        !response.ok
      ) {

        throw new Error(
          `Trail geometry failed: ${response.status}`
        );
      }


      const data =
        (
          await response.json()
        ) as TrailGeometry;


      setSelectedTrailGeometry(
        data
      );

    } catch (
      error
    ) {

      console.error(
        "Trail selection failed:",
        error
      );


      setSelectedTrailGeometry(
        null
      );

    } finally {

      setLoadingTrail(
        false
      );
    }
  }


  /*
   * ============================================================
   * TRAIL REPRESENTATIVE COORDINATE
   * ============================================================
   *
   * GeoJSON coordinates:
   *
   *     [longitude, latitude]
   *
   * Weather request:
   *
   *     latitude
   *     longitude
   */

  function getTrailRepresentativeCoordinate(
    geometry: TrailGeometry["geometry"]
  ): [
    number,
    number
  ] | null {

    const coordinates =
      geometry.coordinates;


    if (
      geometry.type ===
      "LineString"
    ) {

      const line =
        coordinates as number[][];


      if (
        line.length ===
        0
      ) {

        return null;
      }


      const midpointIndex =
        Math.floor(
          line.length / 2
        );


      const coordinate =
        line[midpointIndex];


      if (
        !coordinate ||
        coordinate.length <
          2
      ) {

        return null;
      }


      return [
        Number(
          coordinate[0]
        ),
        Number(
          coordinate[1]
        ),
      ];
    }


    const multiLine =
      coordinates as number[][][];


    const validSegments =
      multiLine.filter(
        (
          segment
        ) =>
          Array.isArray(
            segment
          ) &&
          segment.length >
            0
      );


    if (
      validSegments.length ===
      0
    ) {

      return null;
    }


    const totalPoints =
      validSegments.reduce(
        (
          total,
          segment
        ) =>
          total +
          segment.length,
        0
      );


    if (
      totalPoints ===
      0
    ) {

      return null;
    }


    const targetIndex =
      Math.floor(
        totalPoints / 2
      );


    let runningIndex =
      0;


    for (
      const segment of
      validSegments
    ) {

      if (
        targetIndex <
        runningIndex +
          segment.length
      ) {

        const localIndex =
          targetIndex -
          runningIndex;


        const coordinate =
          segment[localIndex];


        if (
          !coordinate ||
          coordinate.length <
            2
        ) {

          return null;
        }


        return [
          Number(
            coordinate[0]
          ),
          Number(
            coordinate[1]
          ),
        ];
      }


      runningIndex +=
        segment.length;
    }


    return null;
  }


  /*
   * ============================================================
   * WEATHER
   * ============================================================
   */

  useEffect(
    () => {

      if (
        !selectedTrailGeometry
      ) {

        setWeather(
          null
        );

        setWeatherError(
          null
        );

        return;
      }


      const coordinate =
        getTrailRepresentativeCoordinate(
          selectedTrailGeometry.geometry
        );


      if (
        !coordinate
      ) {

        setWeather(
          null
        );

        setWeatherError(
          "Weather location could not be determined from this trail."
        );

        return;
      }


      const [
        longitude,
        latitude,
      ] = coordinate;


      if (
        !Number.isFinite(
          latitude
        ) ||
        !Number.isFinite(
          longitude
        )
      ) {

        setWeather(
          null
        );

        setWeatherError(
          "Trail coordinates are invalid."
        );

        return;
      }


      let cancelled =
        false;


      async function loadWeather() {

        setLoadingWeather(
          true
        );

        setWeather(
          null
        );

        setWeatherError(
          null
        );


        try {

          const params =
            new URLSearchParams({
              latitude:
                String(
                  latitude
                ),

              longitude:
                String(
                  longitude
                ),
            });


          const response =
            await fetch(
              `${API_BASE_URL}/api/weather?${params.toString()}`,
              {
                cache:
                  "no-store",
              }
            );


          if (
            !response.ok
          ) {

            throw new Error(
              `Weather request failed: ${response.status}`
            );
          }


          const data =
            (
              await response.json()
            ) as WeatherResponse;


          if (
            !cancelled
          ) {

            setWeather(
              data
            );

            setWeatherError(
              null
            );
          }

        } catch (
          error
        ) {

          console.error(
            "Weather loading failed:",
            error
          );


          if (
            !cancelled
          ) {

            setWeather(
              null
            );

            setWeatherError(
              "Weather is temporarily unavailable."
            );
          }

        } finally {

          if (
            !cancelled
          ) {

            setLoadingWeather(
              false
            );
          }
        }
      }


      void loadWeather();


      return () => {

        cancelled =
          true;
      };

    },
    [
      selectedTrailGeometry,
    ]
  );


  /*
   * ============================================================
   * ELEVATION
   * ============================================================
   */

  useEffect(
    () => {

      if (
        !selectedTrailGeometry
      ) {

        setElevation(
          null
        );

        setElevationError(
          null
        );

        return;
      }


      const geometry =
        selectedTrailGeometry.geometry;


      let cancelled =
        false;


      async function loadElevation() {

        setLoadingElevation(
          true
        );

        setElevation(
          null
        );

        setElevationError(
          null
        );


        try {

          const response =
            await fetch(
              `${API_BASE_URL}/api/elevation`,
              {
                method:
                  "POST",

                headers: {
                  "Content-Type":
                    "application/json",
                },

                body:
                  JSON.stringify({
                    geometry:
                      geometry,
                  }),

                cache:
                  "no-store",
              }
            );


          if (
            !response.ok
          ) {

            throw new Error(
              `Elevation request failed: ${response.status}`
            );
          }


          const data =
            (
              await response.json()
            ) as ElevationResponse;


          if (
            !cancelled
          ) {

            setElevation(
              data
            );

            setElevationError(
              null
            );
          }

        } catch (
          error
        ) {

          console.error(
            "Elevation loading failed:",
            error
          );


          if (
            !cancelled
          ) {

            setElevation(
              null
            );

            setElevationError(
              "Elevation data is temporarily unavailable."
            );
          }

        } finally {

          if (
            !cancelled
          ) {

            setLoadingElevation(
              false
            );
          }
        }
      }


      void loadElevation();


      return () => {

        cancelled =
          true;
      };

    },
    [
      selectedTrailGeometry,
    ]
  );


  /*
   * ============================================================
   * ELEVATION PROFILE HEIGHT
   * ============================================================
   */

  function getProfileHeight(
    elevationValue: number,
    profile: ElevationProfilePoint[]
  ): number {

    if (
      profile.length ===
      0
    ) {

      return 0;
    }


    const values =
      profile.map(
        (
          point
        ) =>
          point.elevation_m
      );


    const minimum =
      Math.min(
        ...values
      );


    const maximum =
      Math.max(
        ...values
      );


    const range =
      maximum -
      minimum;


    if (
      range ===
      0
    ) {

      return 50;
    }


    return (
      (
        elevationValue -
        minimum
      )
      /
      range
    ) *
      75 +
      25;
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
        <section className="mx-auto max-w-[1440px] px-6 py-7 md:px-10 lg:px-14">

          <div className="grid h-[620px] min-h-0 grid-cols-1 overflow-hidden rounded-[24px] border border-white/10 bg-[#0d1825] shadow-[0_25px_70px_rgba(0,0,0,0.22)] lg:grid-cols-[380px_minmax(0,1fr)]">

            <div className="min-h-0 overflow-hidden border-b border-white/10 lg:border-b-0 lg:border-r">

              <TrailSidebar
                
                trails={trails}
                
                loading={loadingTrails}
                
                selectedTrail={selectedTrailSummary}
                
                locationName={locationName}
                
                onTrailSelect={handleTrailSelect}
              
              />


              {trailError && (
                <div className="border-t border-white/10 bg-[#0b1724] px-5 py-3">

                  <p className="text-[11px] leading-5 text-white/45">
                    {
                      trailError
                    }
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
                    true
                  )
                }

              />


              {loadingTrail && (
                <div className="pointer-events-none absolute bottom-4 left-4 z-40 rounded-xl border border-white/10 bg-[#07111f]/90 px-3 py-2 text-xs text-white/65 shadow-xl backdrop-blur-xl">
                  Loading selected trail…
                </div>
              )}

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

            <section className="rounded-[24px] border border-white/10 bg-[#0d1825] p-6 md:p-8">

              <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_420px]">

                <div>

                  <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                    Selected trail
                  </p>


                  <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em]">

                    {
                    selectedTrailGeometry.name ??
                     selectedTrailSummary?.name ??
                     (locationName
                       ? `${locationName.split(",")[0].trim()} Route`
                       : "Unnamed hiking trail")}

                  </h2>


                  <p className="mt-4 max-w-2xl text-sm leading-7 text-white/45">

                    The selected route is shown from
                    its available geographic geometry.
                    Terrain, weather and analytical
                    intelligence are being built
                    progressively from the actual trail.

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
                      Route length
                    </p>

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
                      {
                        selectedTrailGeometry.difficulty ??
                        "Not available"
                      }
                    </p>


                    <p className="mt-1 text-[11px] text-white/30">
                      Trail difficulty
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

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

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


                    <p className="mt-3 text-[10px] leading-5 text-white/25">

                      Weather source: {
                        weather.source
                      } · Coordinates represent the
                      selected trail's approximate midpoint.

                    </p>

                  </div>
                )}

            </section>


            {/* ========================================================
                TERRAIN
            ======================================================== */}

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">

                <div>

                  <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                    Terrain
                  </p>


                  <h3 className="mt-3 text-[22px] font-semibold">
                    Elevation & slope
                  </h3>

                </div>


                {elevation && (
                  <p className="text-[11px] text-white/30">
                    {
                      elevation.sampled_points
                    } sampled points
                  </p>
                )}

              </div>


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
                            elevation.metrics.maximum_slope_percent !==
                            null
                              ? `${elevation.metrics.maximum_slope_percent.toFixed(
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
                      0 && (

                      <div className="mt-5 rounded-[22px] border border-white/[0.08] bg-gradient-to-br from-white/[0.04] to-white/[0.015] p-5">

                        <div className="flex items-end justify-between">

                          <div>

                            <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-white/30">
                              Elevation profile
                            </p>


                            <p className="mt-2 text-sm text-white/45">
                              Terrain change along the selected route
                            </p>

                          </div>


                          <p className="text-[11px] text-white/25">

                            {
                              elevation.profile[
                                elevation.profile.length - 1
                              ].distance_km.toFixed(
                                2
                              )
                            } km

                          </p>

                        </div>


                        <div className="mt-7 flex h-[190px] items-end gap-[3px] overflow-hidden">

                          {
                            elevation.profile.map(
                              (
                                point,
                                index
                              ) => {

                                const height =
                                  getProfileHeight(
                                    point.elevation_m,
                                    elevation.profile
                                  );


                                return (
                                  <div
                                    key={`${point.distance_km}-${index}`}
                                    className="group relative flex h-full flex-1 items-end"
                                  >

                                    <div
                                      className="w-full min-w-[2px] rounded-t-[4px] bg-emerald-400/40 transition-all duration-200 group-hover:bg-emerald-300/65"
                                      style={{
                                        height:
                                          `${height}%`,
                                      }}
                                      title={`${point.distance_km.toFixed(
                                        2
                                      )} km · ${point.elevation_m.toFixed(
                                        0
                                      )} m`}
                                    />

                                  </div>
                                );
                              }
                            )
                          }

                        </div>


                        <div className="mt-3 flex justify-between text-[10px] text-white/25">

                          <span>
                            0 km
                          </span>


                          <span>

                            {
                              elevation.metrics.min_elevation_m !==
                              null
                                ? `${elevation.metrics.min_elevation_m.toFixed(
                                    0
                                  )} m min`
                                : "—"
                            }

                          </span>


                          <span>

                            {
                              elevation.metrics.max_elevation_m !==
                              null
                                ? `${elevation.metrics.max_elevation_m.toFixed(
                                    0
                                  )} m max`
                                : "—"
                            }

                          </span>


                          <span>

                            {
                              elevation.profile[
                                elevation.profile.length - 1
                              ].distance_km.toFixed(
                                2
                              )
                            } km

                          </span>

                        </div>

                      </div>
                    )}


                    <p className="mt-3 text-[10px] leading-5 text-white/25">

                      Elevation source: {
                        elevation.source
                      } · Values are derived from sampled
                      points along the selected trail geometry.

                    </p>

                  </div>
                )}

            </section>


            {/* ========================================================
                INTELLIGENCE
            ======================================================== */}

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Intelligence
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Difficulty & suitability
              </h3>


              <p className="mt-2 text-sm leading-7 text-white/45">
                Difficulty estimation and suitability will
                combine route, terrain, weather, rainfall,
                condition likelihood and user requirements.
              </p>

            </section>


            {/* ========================================================
                PREPARATION
            ======================================================== */}

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Preparation
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Required gear
              </h3>


              <p className="mt-2 text-sm leading-7 text-white/45">
                Gear requirements will be derived from the
                analytical outputs rather than generated
                independently.
              </p>

            </section>


            {/* ========================================================
                PRODUCTS
            ======================================================== */}

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Products
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Relevant outdoor products
              </h3>


              <p className="mt-2 text-sm leading-7 text-white/45">
                Required gear categories will later feed a
                product-discovery layer returning real
                products, images and purchase links.
              </p>

            </section>


            {/* ========================================================
                ASSISTANT
            ======================================================== */}

            <section className="mt-5 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

              <p className="text-[11px] uppercase tracking-[0.2em] text-white/35">
                Assistant
              </p>


              <h3 className="mt-3 text-[22px] font-semibold">
                Ask about the trail
              </h3>


              <p className="mt-2 text-sm leading-7 text-white/45">
                Later, RAG will retrieve trail, weather,
                analytical and gear evidence before the LLM
                produces a grounded explanation.
              </p>

            </section>

          </section>
        )}

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