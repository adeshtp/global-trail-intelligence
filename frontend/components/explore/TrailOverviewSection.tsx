"use client";

import { mappedLengthLabel } from "@/app/explore/helpers";
import type { SelectedTrail } from "@/app/explore/types";
import { Trail, difficultyLabel } from "@/components/TrailSidebar";

type TrailOverviewSectionProps = {
  selectedTrailGeometry: SelectedTrail;
  selectedTrailSummary: Trail | null;
};

export default function TrailOverviewSection({
  selectedTrailGeometry,
  selectedTrailSummary,
}: TrailOverviewSectionProps) {
  return (
    <>
      <section id="trail-overview" className="scroll-mt-20 rounded-[24px] border border-white/10 bg-[#0d1825] p-6 md:p-8">

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_420px]">

          <div>

            <p className="text-[13px] uppercase tracking-[0.2em] text-white/60">
              Selected trail
            </p>


            <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em]">

              {
              selectedTrailGeometry.name ??
               selectedTrailSummary?.name ??
               "Unnamed OSM hiking route"}

            </h2>


            <p className="mt-4 max-w-2xl text-sm leading-7 text-white/70">

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

                <p className="text-[12px] font-medium uppercase tracking-[0.16em] text-white/60">
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

                <span className="ml-1 text-sm font-medium text-white/60">
                  km
                </span>

              </p>


              <p className="mt-1 text-[13px] text-white/55">
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
                  <summary className="cursor-pointer list-none rounded-lg border border-amber-300/20 bg-amber-300/[0.05] px-2.5 py-1.5 text-[12px] leading-4 text-amber-100/80">
                    OpenStreetMap currently maps only this section of
                    the named route. The full real-world trek may be
                    longer.
                  </summary>
                  <p className="mt-1.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-[12px] leading-4 text-white/60">
                    {selectedTrailGeometry.relation_completeness.note}
                  </p>
                </details>
              ) : null}

            </div>


            {/* DIFFICULTY */}

            <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-all duration-200 hover:-translate-y-[1px] hover:border-amber-300/20 hover:bg-white/[0.065]">

              <div className="flex items-center justify-between">

                <p className="text-[12px] font-medium uppercase tracking-[0.16em] text-white/60">
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


              <p className="mt-1 text-[13px] text-white/55">
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

                <p className="text-[12px] font-medium uppercase tracking-[0.16em] text-white/60">
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


              <p className="mt-1 text-[13px] text-white/55">
                Trail surface
              </p>

            </div>


            {/* ROUTE */}

            <div className="group rounded-2xl border border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.02] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] transition-all duration-200 hover:-translate-y-[1px] hover:border-violet-300/20 hover:bg-white/[0.065]">

              <div className="flex items-center justify-between">

                <p className="text-[12px] font-medium uppercase tracking-[0.16em] text-white/60">
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


              <p className="mt-1 text-[13px] text-white/55">
                Route classification
              </p>

            </div>

          </div>

        </div>

      </section>
    </>
  );
}
