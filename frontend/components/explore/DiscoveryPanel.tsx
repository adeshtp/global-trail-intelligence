"use client";

import { type Dispatch, type SetStateAction } from "react";

import type {
  Location,
  MapTrail,
  SelectedTrail,
  TrailDiscoveryResponse,
} from "@/app/explore/types";
import CesiumMap from "@/components/CesiumMap";
import TrailSidebar, { Trail } from "@/components/TrailSidebar";

type DiscoveryPanelProps = {
  coverage: TrailDiscoveryResponse["coverage"];
  enriching: boolean;
  handleTrailSelect: (trail: Trail) => void;
  loadMoreError: string | null;
  loadMoreTrails: () => Promise<void>;
  loadingMore: boolean;
  loadingTrails: boolean;
  location: Location | null;
  locationName: string | null;
  mapTrails: MapTrail[];
  pagination: TrailDiscoveryResponse["pagination"];
  peakSearch: TrailDiscoveryResponse["peak_search"];
  resultCounts: TrailDiscoveryResponse["result_counts"];
  selectedTrailGeometry: SelectedTrail | null;
  selectedTrailSummary: Trail | null;
  setMapExpanded: Dispatch<SetStateAction<boolean>>;
  trailError: string | null;
  trails: Trail[];
};

export default function DiscoveryPanel({
  coverage,
  enriching,
  handleTrailSelect,
  loadMoreError,
  loadMoreTrails,
  loadingMore,
  loadingTrails,
  location,
  locationName,
  mapTrails,
  pagination,
  peakSearch,
  resultCounts,
  selectedTrailGeometry,
  selectedTrailSummary,
  setMapExpanded,
  trailError,
  trails,
}: DiscoveryPanelProps) {
  return (
    <>
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

                <p className="text-[13px] leading-5 text-white/70">
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

                  <p className="text-[12px] uppercase tracking-[0.15em] text-white/55">
                    Searched coverage
                  </p>

                  <p className="mt-1.5 text-[13px] leading-5 text-white/70">
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
                      <p className="text-[12px] uppercase tracking-[0.15em] text-sky-200/80">
                        Summit
                      </p>
                      <p className="mt-1 text-[13px] leading-5 text-white/75">
                        {peakSearch.summit_note}
                      </p>
                    </div>
                  ) : null}

                  {pagination?.has_more ? (
                    <button
                      type="button"
                      onClick={loadMoreTrails}
                      disabled={loadingMore}
                      className="mt-2.5 w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-[13px] font-semibold text-white/80 transition hover:border-sky-300/30 hover:text-white disabled:opacity-50"
                    >
                      {loadingMore
                        ? "Loading more…"
                        : `Show more (${Math.max(
                            pagination.total_ranked - trails.length,
                            0
                          ).toLocaleString()} more ranked)`}
                    </button>
                  ) : pagination && pagination.total_ranked > 0 ? (
                    <p className="mt-2 text-[12px] text-white/55">
                      End of the ranked results for this search.
                    </p>
                  ) : null}

                  {loadMoreError ? (
                    <p className="mt-2 text-[13px] leading-5 text-amber-200/85">
                      {loadMoreError}
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
    </>
  );
}
