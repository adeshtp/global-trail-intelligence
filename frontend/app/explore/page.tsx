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

import { Trail } from "@/components/TrailSidebar";

import CesiumMap from "@/components/CesiumMap";
import AssistantDock from "@/components/explore/AssistantDock";
import AssistantSection from "@/components/explore/AssistantSection";
import ConditionsSection from "@/components/explore/ConditionsSection";
import DiscoveryPanel from "@/components/explore/DiscoveryPanel";
import ElevationSection from "@/components/explore/ElevationSection";
import ExploreHeader from "@/components/explore/ExploreHeader";
import GearSection from "@/components/explore/GearSection";
import ProductsSection from "@/components/explore/ProductsSection";
import SectionNav from "@/components/explore/SectionNav";
import SuitabilitySection from "@/components/explore/SuitabilitySection";
import TrailOverviewSection from "@/components/explore/TrailOverviewSection";

import {
  buildAssistantRequest,
  buildProductsRequest,
  postJson,
} from "./api";
import {
  API_BASE_URL,
  ASSISTANT_TIMEOUT_MS,
  DISCOVERY_TIMEOUT_MS,
  INTELLIGENCE_TIMEOUT_MS,
  PRODUCTS_TIMEOUT_MS,
  abortAfter,
} from "./helpers";
import type {
  AssistantResponse,
  ElevationProfilePoint,
  ElevationResponse,
  Location,
  MapTrail,
  ProductSearchResponse,
  SelectedTrail,
  SelectedTrailAnalysis,
  TrailDiscoveryResponse,
  TrailGeometry,
  TrailIntelligenceResponse,
  WeatherResponse,
} from "./types";































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

  // A failed "show more" is reported next to the button. It must not replace
  // trailError, which may be explaining a provider problem with the search
  // itself.
  const [
    loadMoreError,
    setLoadMoreError,
  ] = useState<string | null>(null);

  // The most recent search, kept so "load more" continues the same query
  // instead of re-running discovery.
  const lastSearchQueryRef = useRef("");
  const lastSearchNameRef = useRef("");
  const lastSearchBroadRef = useRef(false);
  const lastPlaceKindRef = useRef("area");
  const lastSearchLocationRef = useRef<Location | null>(null);
  // The bounds the search was run with. Later pages are ranked against the
  // same area only if they are asked for it.
  const lastSearchBoundsRef = useRef<
    [number, number, number, number] | null
  >(null);
  const loadMoreAbortRef = useRef<AbortController | null>(null);


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

    // A page belongs to the search it was requested for. If a new search
    // starts while this is in flight, its result is dropped.
    const requestId = discoveryRequestRef.current;
    loadMoreAbortRef.current?.abort();
    const controller = new AbortController();
    loadMoreAbortRef.current = controller;
    // Paged results come from cache, so a hung connection is a stall rather
    // than a slow provider: bound it tightly.
    const bound = abortAfter(controller, 120000);
    const isCurrent = () =>
      requestId === discoveryRequestRef.current &&
      loadMoreAbortRef.current === controller;

    setLoadingMore(true);
    setLoadMoreError(null);
    const nextPage = pagination.next_page;

    try {
      const params = new URLSearchParams({
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
      });
      if (lastSearchBoundsRef.current) {
        params.set("bbox", lastSearchBoundsRef.current.join(","));
      }

      const response = await fetch(
        `${API_BASE_URL}/api/osm/trails/discover?${params.toString()}`,
        {
          cache: "no-store",
          signal: controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(String(response.status));
      }

      const data = (await response.json()) as TrailDiscoveryResponse;
      if (!isCurrent()) {
        return;
      }
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
    } catch (error) {
      // Superseded by a newer search or request: nothing to report.
      if (!isCurrent()) {
        return;
      }
      if (
        error instanceof DOMException &&
        error.name === "AbortError" &&
        !bound.timedOut()
      ) {
        return;
      }
      setLoadMoreError(
        "More results could not be loaded. The results already shown are unaffected."
      );
    } finally {
      bound.clear();
      if (loadMoreAbortRef.current === controller) {
        loadMoreAbortRef.current = null;
        setLoadingMore(false);
      }
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
    lastSearchBoundsRef.current = searchBounds;
    loadMoreAbortRef.current?.abort();
    loadMoreAbortRef.current = null;
    setLoadMoreError(null);
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
      "conditions",
      "elevation",
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
          // The overview is part of the "Trail" group in the nav; it has no
          // button of its own, so it highlights the button for its group.
          setActiveSection(
            best[0] === "trail-overview" ? "trail-discovery" : best[0]
          );
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
        const intelligence = await postJson<TrailIntelligenceResponse>(
          "/api/trails/intelligence",
          { trail: selectedTrail },
          controller.signal,
          "Selected trail intelligence"
        );

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
      const data = await postJson<ProductSearchResponse>(
        "/api/trails/products",
        buildProductsRequest(selectedIntelligence),
        controller.signal,
        "Product search"
      );
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
      const data = await postJson<AssistantResponse>(
        "/api/trails/assistant",
        buildAssistantRequest({
          question: assistantQuestion,
          trail: selectedTrailGeometry,
          intelligence: selectedIntelligence,
          products: productResults,
        }),
        controller.signal,
        "Assistant request"
      );
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

      <ExploreHeader
        locationName={locationName}
      />


      {/* ============================================================
          SECTION NAVIGATION
          Primary Explore navigation: navbar, then search, then map.
          Sticky within the page scroll container; horizontally scrolls
          on narrow screens instead of wrapping.
      ============================================================ */}

      <SectionNav
        activeSection={activeSection}
        selectedTrailGeometry={selectedTrailGeometry}
        setActiveSection={setActiveSection}
      />


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
      <DiscoveryPanel
        coverage={coverage}
        enriching={enriching}
        handleTrailSelect={handleTrailSelect}
        loadMoreTrails={loadMoreTrails}
        loadMoreError={loadMoreError}
        loadingMore={loadingMore}
        loadingTrails={loadingTrails}
        location={location}
        locationName={locationName}
        mapTrails={mapTrails}
        pagination={pagination}
        peakSearch={peakSearch}
        resultCounts={resultCounts}
        selectedTrailGeometry={selectedTrailGeometry}
        selectedTrailSummary={selectedTrailSummary}
        setMapExpanded={setMapExpanded}
        trailError={trailError}
        trails={trails}
      />
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

      <TrailOverviewSection
        selectedTrailGeometry={selectedTrailGeometry}
        selectedTrailSummary={selectedTrailSummary}
      />


            {/* ========================================================
                CONDITIONS / WEATHER
            ======================================================== */}

      <ConditionsSection
        loadingWeather={loadingWeather}
        selectedIntelligence={selectedIntelligence}
        weather={weather}
        weatherError={weatherError}
      />


            {/* ========================================================
                TERRAIN
            ======================================================== */}

      <ElevationSection
        elevation={elevation}
        elevationError={elevationError}
        getDisplayProfile={getDisplayProfile}
        loadingElevation={loadingElevation}
        selectedAnalysis={selectedAnalysis}
      />


            {/* ========================================================
                INTELLIGENCE
            ======================================================== */}

      <SuitabilitySection
        selectedIntelligence={selectedIntelligence}
      />


            {/* ========================================================
                PREPARATION
            ======================================================== */}

      <GearSection
        selectedIntelligence={selectedIntelligence}
      />


            {/* ========================================================
                PRODUCTS
            ======================================================== */}

      <ProductsSection
        handleProductSearch={handleProductSearch}
        productResults={productResults}
        productsError={productsError}
        productsLoading={productsLoading}
        selectedIntelligence={selectedIntelligence}
      />


            {/* ========================================================
                ASSISTANT
            ======================================================== */}

      <AssistantSection
        assistantAnswer={assistantAnswer}
        assistantError={assistantError}
        assistantLoading={assistantLoading}
        assistantQuestion={assistantQuestion}
        handleAssistantSubmit={handleAssistantSubmit}
        selectedIntelligence={selectedIntelligence}
        setAssistantQuestion={setAssistantQuestion}
      />

          </section>
        )}

      {/* ============================================================
          FLOATING ASSISTANT
          Always reachable while exploring. Sits above the page flow and
          never covers the map, which is in the top panel.
      ============================================================ */}

      <AssistantDock
        assistantAnswer={assistantAnswer}
        assistantDockRef={assistantDockRef}
        assistantError={assistantError}
        assistantLoading={assistantLoading}
        assistantOpen={assistantOpen}
        assistantQuestion={assistantQuestion}
        handleAssistantSubmit={handleAssistantSubmit}
        selectedIntelligence={selectedIntelligence}
        setAssistantOpen={setAssistantOpen}
        setAssistantQuestion={setAssistantQuestion}
      />

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