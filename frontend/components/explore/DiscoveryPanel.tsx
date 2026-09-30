"use client";

import { type Dispatch, type SetStateAction, useEffect, useRef } from "react";

import type {
  Location,
  MapTrail,
  ResultView,
  SelectedTrail,
  TrailDiscoveryResponse,
} from "@/app/explore/types";
import { DEFAULT_RESULT_VIEW, activeFilterCount, serverFiltered } from "@/app/explore/helpers";
import CesiumMap from "@/components/CesiumMap";
import ResultControls from "@/components/explore/ResultControls";
import TrailSidebar, { Trail } from "@/components/TrailSidebar";

/*
 * Sizes the panel to the space left on the first screen: the window height
 * minus everything above it and the section's own bottom padding, so the header,
 * search and panel fit one screen on any desktop display, with no fixed
 * height. Measured again whenever the page above changes height (a search
 * error, the section nav appearing) or the window is resized. Until it runs,
 * and where it cannot, the CSS fallback in the class keeps a sensible height.
 */
function useFitToViewport<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }

    const fit = () => {
      const rem = parseFloat(
        getComputedStyle(document.documentElement).fontSize
      );
      const top = element.getBoundingClientRect().top + window.scrollY;
      const available = window.innerHeight - top - rem * 1;
      // Never smaller than a usable list; a very short window scrolls instead.
      element.style.setProperty(
        "--panel-height",
        `${Math.max(available, rem * 26)}px`
      );
    };

    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(document.body);
    window.addEventListener("resize", fit);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", fit);
    };
  }, []);

  return ref;
}


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
  view: ResultView;
  viewMatch: { matched: number; before: number } | null;
  onViewChange: (view: ResultView) => void;
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
  view,
  viewMatch,
  onViewChange,
}: DiscoveryPanelProps) {
  // Trails with no verified shape are always listed after the mapped ones, so
  // hiding them here changes no order.
  const visibleTrails = view.mappedOnly
    ? trails.filter((trail) => trail.map_ready)
    : trails;
  const filtersActive = activeFilterCount(view) > 0;
  // Trails exist, and the filters removed all of them. A search that found
  // nothing, or a provider that failed, says so elsewhere and is not this.
  const filteredToNothing =
    Boolean(location) &&
    filtersActive &&
    ((viewMatch !== null && viewMatch.before > 0) ||
      (view.mappedOnly && (resultCounts?.relevance_accepted ?? 0) > 0));

  const panelRef = useFitToViewport<HTMLDivElement>();

  return (
    <>
      <section
        id="trail-discovery"
        className="mx-auto max-w-[1440px] scroll-mt-20 px-6 py-4 md:px-10 lg:px-14"
      >

        <div
          ref={panelRef}
          className="grid h-[var(--panel-height,40rem)] min-h-0 grid-cols-1 overflow-hidden rounded-[24px] border border-white/10 bg-[#0d1825] shadow-[0_25px_70px_rgba(0,0,0,0.22)] lg:grid-cols-[clamp(22rem,30vw,32rem)_minmax(0,1fr)]"
        >

          <div className="min-h-0 overflow-hidden border-b border-white/10 lg:border-b-0 lg:border-r">

            <TrailSidebar
                
              trails={visibleTrails}
                
              loading={loadingTrails}
                
              selectedTrail={selectedTrailSummary}
                
              onTrailSelect={handleTrailSelect}

              counts={resultCounts}

              controls={
                location ? (
                  <ResultControls
                    view={view}
                    onChange={onViewChange}
                    match={serverFiltered(view) ? viewMatch : null}
                  />
                ) : undefined
              }

              emptyState={
                filteredToNothing ? (
                  <div className="rounded-2xl border border-dashed border-white/15 bg-white/[0.025] px-5 py-8 text-center">
                    <p className="text-sm text-white/85">
                      No trails match these filters.
                    </p>
                    <p className="mt-2 text-[13px] leading-5 text-white/65">
                      Try a wider length or another difficulty.
                    </p>
                    <button
                      type="button"
                      onClick={() =>
                        onViewChange({
                          ...DEFAULT_RESULT_VIEW,
                          sort: view.sort,
                        })
                      }
                      className="mt-4 rounded-xl border border-white/20 px-4 py-2 text-[13px] font-medium text-white transition hover:bg-white/10"
                    >
                      Clear filters
                    </button>
                  </div>
                ) : undefined
              }
              
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
