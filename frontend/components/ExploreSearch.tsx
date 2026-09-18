"use client";

import {
  FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";


const API_BASE_URL =
  "http://127.0.0.1:8000";


type Location = {
  latitude: number;
  longitude: number;
};


type LocationResult = {
  display_name: string;
  latitude: number;
  longitude: number;

  /*
   * These fields come from the geocoding result when available.
   * They let the Explore page distinguish:
   *
   *   Munnar        -> broad place search
   *   Meesapulimala -> specific outdoor feature search
   */
  osm_type?: string;
  osm_id?: number;
  class?: string;
  type?: string;
  addresstype?: string;
};


type SearchResponse = {
  query: string;
  results: LocationResult[];
};


type ExploreSearchProps = {
  initialQuery?: string | null;

  onLocationFound: (
    location: Location,
    locationName: string,
    searchQuery: string,
    broadAreaSearch: boolean
  ) => void;
};


function normalizeText(
  value: string
): string {
  return value
    .toLowerCase()
    .replace(
      /[(),./_-]/g,
      " "
    )
    .replace(
      /\s+/g,
      " "
    )
    .trim();
}


/*
 * ============================================================
 * PLACE / OUTDOOR SEARCH CLASSIFICATION
 * ============================================================
 */

function isBroadAreaResult(
  result: LocationResult,
  query: string
): boolean {

  const resultType =
    normalizeText(
      result.type ?? ""
    );


  const resultClass =
    normalizeText(
      result.class ?? ""
    );


  const addressType =
    normalizeText(
      result.addresstype ?? ""
    );


  const broadPlaceTypes =
    new Set([
      "city",
      "town",
      "village",
      "municipality",
      "county",
      "state",
      "country",
      "region",
      "district",
      "borough",
      "suburb",
      "neighbourhood",
      "neighborhood",
      "hamlet",
      "locality",
    ]);


  if (
    broadPlaceTypes.has(
      resultType
    )
  ) {
    return true;
  }


  if (
    broadPlaceTypes.has(
      addressType
    )
  ) {
    return true;
  }


  const specificOutdoorTypes =
    new Set([
      "peak",
      "mountain",
      "hill",
      "summit",
      "ridge",
      "saddle",
      "waterfall",
      "viewpoint",
      "cliff",
      "gorge",
      "valley",
      "pass",
      "natural",
    ]);


  if (
    specificOutdoorTypes.has(
      resultType
    )
  ) {
    return false;
  }


  if (
    resultClass ===
      "natural" &&
    !broadPlaceTypes.has(
      resultType
    )
  ) {
    return false;
  }


  const normalizedQuery =
    normalizeText(
      query
    );


  const specificWords = [
    "peak",
    "mountain",
    "hill",
    "summit",
    "trail",
    "trek",
    "hike",
    "hiking",
    "waterfall",
    "falls",
    "ridge",
    "viewpoint",
    "pass",
    "gorge",
  ];


  if (
    specificWords.some(
      (word) =>
        normalizedQuery.includes(
          word
        )
    )
  ) {
    return false;
  }


  if (
    resultClass ===
    "place"
  ) {
    return true;
  }


  return false;
}


/*
 * ============================================================
 * BEST LOCATION RESULT
 * ============================================================
 */

function chooseBestLocation(
  query: string,
  results: LocationResult[]
): LocationResult | null {

  if (
    results.length ===
    0
  ) {
    return null;
  }


  const normalizedQuery =
    normalizeText(
      query
    );


  const queryTokens =
    normalizedQuery
      .split(" ")
      .filter(
        Boolean
      );


  let best =
    results[0];


  let bestScore =
    Number.NEGATIVE_INFINITY;


  results.forEach(
    (
      result,
      index
    ) => {

      const name =
        normalizeText(
          result.display_name
        );


      let score = 0;


      if (
        name ===
        normalizedQuery
      ) {
        score += 1000;
      }


      if (
        name.includes(
          normalizedQuery
        )
      ) {
        score += 600;
      }


      for (
        const token of queryTokens
      ) {

        if (
          name.includes(
            token
          )
        ) {
          score += 80;
        }
      }


      const outdoorKeywords = [
        "peak",
        "mountain",
        "hill",
        "trail",
        "trek",
        "summit",
        "waterfall",
        "forest",
        "park",
        "hiking",
      ];


      for (
        const keyword of
        outdoorKeywords
      ) {

        if (
          normalizedQuery.includes(
            keyword
          ) &&
          name.includes(
            keyword
          )
        ) {
          score += 150;
        }
      }


      const resultType =
        normalizeText(
          result.type ?? ""
        );


      if (
        [
          "peak",
          "mountain",
          "hill",
          "summit",
          "waterfall",
          "viewpoint",
          "ridge",
        ].includes(
          resultType
        )
      ) {
        score += 80;
      }


      /*
       * API order is only a weak tie-breaker.
       */
      score -= index;


      if (
        score >
        bestScore
      ) {

        bestScore =
          score;


        best =
          result;
      }
    }
  );


  return best;
}


/*
 * ============================================================
 * COMPONENT
 * ============================================================
 */

export default function ExploreSearch({
  initialQuery,
  onLocationFound,
}: ExploreSearchProps) {

  const [
    search,
    setSearch,
  ] = useState(
    initialQuery ?? ""
  );


  const [
    isSearching,
    setIsSearching,
  ] = useState(false);


  const [
    error,
    setError,
  ] = useState<string | null>(
    null
  );


  const automaticSearchRef =
    useRef<string | null>(
      null
    );


  const requestRef =
    useRef<AbortController | null>(
      null
    );


  /*
   * ============================================================
   * LOCATION SEARCH
   * ============================================================
   */

  async function performSearch(
    queryOverride?: string
  ) {

    const query = (
      queryOverride ??
      search
    ).trim();


    if (!query) {
      return;
    }


    /*
     * Abort any previous location request.
     */
    requestRef.current?.abort();


    const controller =
      new AbortController();


    requestRef.current =
      controller;


    setIsSearching(
      true
    );


    setError(
      null
    );


    try {

      const response =
        await fetch(
          `${API_BASE_URL}/api/search?q=${encodeURIComponent(
            query
          )}`,
          {
            cache:
              "no-store",

            signal:
              controller.signal,
          }
        );


      if (
        !response.ok
      ) {

        throw new Error(
          `Location search failed: ${response.status}`
        );
      }


      const data =
        (
          await response.json()
        ) as SearchResponse;


      if (
        !Array.isArray(
          data.results
        ) ||
        data.results.length ===
          0
      ) {

        throw new Error(
          "No location found"
        );
      }


      const result =
        chooseBestLocation(
          query,
          data.results
        );


      if (!result) {

        throw new Error(
          "No usable result"
        );
      }


      const latitude =
        Number(
          result.latitude
        );


      const longitude =
        Number(
          result.longitude
        );


      if (
        !Number.isFinite(
          latitude
        ) ||
        !Number.isFinite(
          longitude
        )
      ) {

        throw new Error(
          "Invalid coordinates"
        );
      }


      /*
       * Determine whether the selected
       * location is a broad area or a
       * specific outdoor feature.
       */
      const broadAreaSearch =
        isBroadAreaResult(
          result,
          query
        );


      setSearch(
        query
      );


      /*
       * Send all search context to
       * app/explore/page.tsx.
       */
      onLocationFound(
        {
          latitude,
          longitude,
        },
        result.display_name,
        query,
        broadAreaSearch
      );

    } catch (
      error
    ) {

      /*
       * Aborted requests are expected
       * and should not display an error.
       */
      if (
        error instanceof
          DOMException &&
        error.name ===
          "AbortError"
      ) {
        return;
      }


      console.error(
        "Location search failed:",
        error
      );


      setError(
        "Could not find this location."
      );

    } finally {

      if (
        requestRef.current ===
        controller
      ) {

        requestRef.current =
          null;


        setIsSearching(
          false
        );
      }
    }
  }


  /*
   * ============================================================
   * MANUAL SEARCH
   * ============================================================
   */

  function handleSubmit(
    event: FormEvent<HTMLFormElement>
  ) {

    event.preventDefault();


    void performSearch();
  }


  /*
   * ============================================================
   * AUTOMATIC SEARCH FROM LANDING PAGE
   * ============================================================
   *
   * Example:
   *
   *   /explore?query=Munnar
   *
   * The Explore page receives initialQuery
   * and automatically performs the search.
   *
   * IMPORTANT:
   * There is intentionally NO cleanup that
   * aborts requestRef here.
   *
   * In React development Strict Mode the effect
   * may run more than once. The ref prevents the
   * duplicate search, while the first request is
   * allowed to finish normally.
   */

  useEffect(() => {

    const query =
      initialQuery?.trim();


    if (!query) {
      return;
    }


    if (
      automaticSearchRef.current ===
      query
    ) {
      return;
    }


    automaticSearchRef.current =
      query;


    setSearch(
      query
    );


    void performSearch(
      query
    );

    /*
     * Intentionally no cleanup here.
     *
     * Aborting here can cancel the automatic
     * landing-page search during development
     * Strict Mode.
     */

    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    initialQuery,
  ]);


  /*
   * ============================================================
   * UI
   * ============================================================
   */

  return (
    <div>

      <form
        onSubmit={
          handleSubmit
        }
        className="w-full"
      >

        <div className="flex items-center rounded-2xl border border-white/10 bg-[#0d1825]/95 p-2 shadow-[0_18px_50px_rgba(0,0,0,0.20)] backdrop-blur-xl">

          <span className="px-4 text-xl text-white/40">
            ⌕
          </span>


          <input
            type="text"
            value={
              search
            }
            onChange={(
              event
            ) => {

              setSearch(
                event.target.value
              );


              setError(
                null
              );
            }}
            placeholder="Search a mountain, trail or location..."
            autoComplete="off"
            className="h-14 min-w-0 flex-1 bg-transparent px-2 text-sm text-white outline-none placeholder:text-white/30"
          />


          <button
            type="submit"
            disabled={
              isSearching
            }
            className="rounded-xl bg-white px-6 py-3.5 text-sm font-semibold text-[#07111f] transition hover:bg-white/90 disabled:cursor-wait disabled:opacity-60"
          >

            {
              isSearching
                ? "Searching..."
                : "Explore"
            }

          </button>

        </div>

      </form>


      {error && (
        <p className="mt-3 px-2 text-xs text-red-300">
          {
            error
          }
        </p>
      )}

    </div>
  );
}