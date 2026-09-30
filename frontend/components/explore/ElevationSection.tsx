"use client";

import { mappedLengthLabel } from "@/app/explore/helpers";
import type { ElevationProfilePoint, ElevationResponse, SelectedTrailAnalysis } from "@/app/explore/types";
import ElevationProfile from "@/components/explore/ElevationProfile";

type ElevationSectionProps = {
  elevation: ElevationResponse | null;
  elevationError: string | null;
  getDisplayProfile: (profile: ElevationProfilePoint[]) => Array<ElevationProfilePoint & { elevation_m: number; }>;
  loadingElevation: boolean;
  selectedAnalysis: SelectedTrailAnalysis | null;
};

export default function ElevationSection({
  elevation,
  elevationError,
  getDisplayProfile,
  loadingElevation,
  selectedAnalysis,
}: ElevationSectionProps) {
  return (
    <>
      <section id="elevation" className="mt-5 scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-7">

        <p className="text-[13px] uppercase tracking-[0.2em] text-white/60">
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
        <p className="mt-2.5 max-w-[74ch] text-[13px] leading-6 text-white/60">
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

        {selectedAnalysis?.completeness?.note ? (
          <p className="mt-2 max-w-[74ch] rounded-lg border border-amber-300/20 bg-amber-300/[0.05] px-2.5 py-1.5 text-[13px] leading-5 text-amber-100/80">
            {selectedAnalysis.completeness.note}
          </p>
        ) : null}


        {/* ELEVATION LOADING */}

        {loadingElevation && (
          <div className="mt-6 rounded-2xl border border-white/[0.08] bg-white/[0.025] px-5 py-6">

            <div className="flex items-center gap-3">

              <div className="h-2 w-2 animate-pulse rounded-full bg-emerald-400/70" />

              <p className="text-sm text-white/75">
                Analysing trail elevation…
              </p>

            </div>

          </div>
        )}


        {/* ELEVATION ERROR */}

        {!loadingElevation &&
          elevationError && (

            <div className="mt-6 rounded-2xl border border-amber-300/10 bg-amber-300/[0.03] px-5 py-5">

              <p className="text-sm text-white/75">
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

                    <p className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/55">
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


                  <p className="mt-1 text-[13px] text-white/55">
                    Lowest sampled point
                  </p>

                </div>


                {/* Maximum */}

                <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-amber-300/20">

                  <div className="flex items-center justify-between">

                    <p className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/55">
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


                  <p className="mt-1 text-[13px] text-white/55">
                    Highest sampled point
                  </p>

                </div>


                {/* Gain */}

                <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-sky-300/20">

                  <div className="flex items-center justify-between">

                    <p className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/55">
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


                  <p className="mt-1 text-[13px] text-white/55">
                    Total uphill movement
                  </p>

                </div>


                {/* Loss */}

                <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-violet-300/20">

                  <div className="flex items-center justify-between">

                    <p className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/55">
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


                  <p className="mt-1 text-[13px] text-white/55">
                    Total downhill movement
                  </p>

                </div>


                {/* Average slope */}

                <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-orange-300/20">

                  <div className="flex items-center justify-between">

                    <p className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/55">
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


                  <p className="mt-1 text-[13px] text-white/55">
                    Across sampled sections
                  </p>

                </div>


                {/* Maximum slope */}

                <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 transition-all duration-200 hover:-translate-y-[1px] hover:border-red-300/20">

                  <div className="flex items-center justify-between">

                    <p className="text-[12px] font-medium uppercase tracking-[0.15em] text-white/55">
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


                  <p className="mt-1 text-[13px] text-white/55">
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

                    <p className="text-[12px] text-white/55">
                      Horizontal axis: distance along
                      the route (km). Vertical axis:
                      elevation (m).
                    </p>

                    <p className="text-[12px] text-white/55">
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


              <p className="mt-3 text-[12px] leading-5 text-white/55">

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
    </>
  );
}
