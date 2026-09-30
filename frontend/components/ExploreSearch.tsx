"use client";

import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";


const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "";


type Location = {
  latitude: number;
  longitude: number;
};


type LocationResult = {
  name?: string;
  display_name: string;
  latitude: number;
  longitude: number;
  boundingbox?: [number, number, number, number] | null;

  /*
   * These fields come from the geocoding result when available.
   * They let the Explore page distinguish a populated place, which is searched
   * as a broad area, from a specific outdoor feature such as a summit, which
   * is searched as a point with peak association enabled.
   */
  osm_type?: string;
  osm_id?: number;
  class?: string;
  type?: string;
  addresstype?: string;
};


type Suggestion = {
  label: string;
  detail: string;
  osm_type: "node" | "way" | "relation";
  osm_id: number;
  latitude: number;
  longitude: number;
};

const SUGGEST_MIN_CHARS = 3;
const SUGGEST_DEBOUNCE_MS = 250;
const SUGGEST_LIST_ID = "location-suggestions";


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
    broadAreaSearch: boolean,
    searchBounds: [number, number, number, number] | null,
    placeKind: string
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
      "administrative",
      "state_district",
      "province",
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


function getAdministrativeSearchBounds(
  result: LocationResult
): [number, number, number, number] | null {
  const type = normalizeText(result.type ?? "");
  const addressType = normalizeText(result.addresstype ?? "");
  const administrativeTypes = new Set([
    "administrative",
    "state_district",
    "district",
    "county",
    "province",
    "state",
    "region",
  ]);

  if (
    !administrativeTypes.has(type) &&
    !administrativeTypes.has(addressType)
  ) {
    return null;
  }

  const bounds = result.boundingbox;
  if (
    !Array.isArray(bounds) ||
    bounds.length !== 4
  ) {
    return null;
  }

  const [west, south, east, north] = bounds.map(Number);
  if (
    ![west, south, east, north].every(Number.isFinite) ||
    west >= east ||
    south >= north
  ) {
    return null;
  }

  return [west, south, east, north];
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


  const [
    suggestions,
    setSuggestions,
  ] = useState<Suggestion[]>([]);


  const [
    highlighted,
    setHighlighted,
  ] = useState(-1);


  const suggestTimerRef =
    useRef<ReturnType<typeof setTimeout> | null>(
      null
    );


  const suggestAbortRef =
    useRef<AbortController | null>(
      null
    );


  /*
   * ============================================================
   * SUGGESTIONS (type-ahead)
   * ============================================================
   *
   * Driven from the input's onChange only, never from an effect on `search`:
   * the initial query and a finished search both set `search` too, and neither
   * should pop the list open.
   */

  function closeSuggestions() {

    if (suggestTimerRef.current) {
      clearTimeout(
        suggestTimerRef.current
      );
      suggestTimerRef.current = null;
    }

    suggestAbortRef.current?.abort();
    suggestAbortRef.current = null;

    setSuggestions([]);
    setHighlighted(-1);
  }


  function requestSuggestions(
    text: string
  ) {

    closeSuggestions();

    const query = text.trim();

    if (query.length < SUGGEST_MIN_CHARS) {
      return;
    }

    suggestTimerRef.current = setTimeout(
      async () => {

        suggestTimerRef.current = null;

        const controller =
          new AbortController();

        suggestAbortRef.current =
          controller;

        try {

          const response = await fetch(
            `${API_BASE_URL}/api/search/suggest?q=${encodeURIComponent(
              query
            )}`,
            {
              signal:
                controller.signal,
            }
          );

          if (!response.ok) {
            return;
          }

          const data =
            (
              await response.json()
            ) as Suggestion[];

          // A newer keystroke aborted this request and started its own.
          if (
            suggestAbortRef.current !==
            controller
          ) {
            return;
          }

          setSuggestions(
            Array.isArray(data)
              ? data
              : []
          );

        } catch {
          // Suggestions are a convenience; a failure just shows no list.
        }
      },
      SUGGEST_DEBOUNCE_MS
    );
  }


  async function selectSuggestion(
    suggestion: Suggestion
  ) {

    closeSuggestions();

    requestRef.current?.abort();

    const controller =
      new AbortController();

    requestRef.current =
      controller;

    setSearch(
      suggestion.label
    );

    setIsSearching(
      true
    );

    setError(
      null
    );

    try {

      const response = await fetch(
        `${API_BASE_URL}/api/search/lookup?osm_type=${
          suggestion.osm_type
        }&osm_id=${suggestion.osm_id}`,
        {
          cache:
            "no-store",

          signal:
            controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(
          `Location lookup failed: ${response.status}`
        );
      }

      const data =
        (
          await response.json()
        ) as SearchResponse;

      // The user already chose: no ranking of alternatives here.
      const result =
        data.results?.[0];

      if (!result) {
        throw new Error(
          "No usable result"
        );
      }

      applyResult(
        result,
        suggestion.label
      );

    } catch (
      error
    ) {

      if (
        error instanceof
          DOMException &&
        error.name ===
          "AbortError"
      ) {
        return;
      }

      console.error(
        "Location lookup failed:",
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


  function handleKeyDown(
    event: KeyboardEvent<HTMLInputElement>
  ) {

    if (event.key === "Escape") {
      closeSuggestions();
      return;
    }

    if (suggestions.length === 0) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlighted(
        (current) =>
          (current + 1) %
          suggestions.length
      );
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlighted(
        (current) =>
          (current - 1 + suggestions.length) %
          suggestions.length
      );
      return;
    }

    // Enter on a highlighted row picks it; with nothing highlighted it
    // submits the typed text exactly as before.
    if (
      event.key === "Enter" &&
      highlighted >= 0
    ) {
      event.preventDefault();
      void selectSuggestion(
        suggestions[highlighted]
      );
    }
  }


  /*
   * ============================================================
   * LOCATION SEARCH
   * ============================================================
   */

  /*
   * Everything that happens once a place has been chosen, whether by typed
   * search or by picking a suggestion: check it, classify it, hand it to the
   * Explore page.
   */
  function applyResult(
    result: LocationResult,
    query: string
  ) {

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
      broadAreaSearch,
      broadAreaSearch
        ? getAdministrativeSearchBounds(result)
        : null,
      /*
       * The geocoder's own classification. Passing it through lets the
       * backend recognise a summit search in any language, without a
       * hardcoded list of place names.
       */
      [
        result.class ?? "",
        result.type ?? "",
        result.addresstype ?? "",
      ]
        .filter(Boolean)
        .join("=")
    );
  }


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


      applyResult(
        result,
        query
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

    closeSuggestions();

    void performSearch();
  }


  /*
   * ============================================================
   * AUTOMATIC SEARCH FROM LANDING PAGE
   * ============================================================
   *
   * Example:
   *
   *   /explore?query=<place name>
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

  useEffect(
    () => () => {
      if (suggestTimerRef.current) {
        clearTimeout(
          suggestTimerRef.current
        );
      }
      suggestAbortRef.current?.abort();
    },
    []
  );


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

    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
      if (automaticSearchRef.current === query) {
        automaticSearchRef.current = null;
      }
    };

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
        className="relative w-full"
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


              requestSuggestions(
                event.target.value
              );
            }}
            onKeyDown={
              handleKeyDown
            }
            onBlur={
              closeSuggestions
            }
            role="combobox"
            aria-expanded={
              suggestions.length > 0
            }
            aria-controls={
              SUGGEST_LIST_ID
            }
            aria-autocomplete="list"
            aria-activedescendant={
              highlighted >= 0
                ? `${SUGGEST_LIST_ID}-${highlighted}`
                : undefined
            }
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

        {suggestions.length > 0 && (
          <ul
            id={
              SUGGEST_LIST_ID
            }
            role="listbox"
            className="absolute inset-x-0 top-full z-[70] mt-2 overflow-hidden rounded-2xl border border-white/10 bg-[#0d1825] py-1.5 shadow-[0_18px_50px_rgba(0,0,0,0.35)]"
          >
            {suggestions.map(
              (
                suggestion,
                index
              ) => (
                <li
                  key={`${suggestion.osm_type}-${suggestion.osm_id}`}
                  id={`${SUGGEST_LIST_ID}-${index}`}
                  role="option"
                  aria-selected={
                    index === highlighted
                  }
                  // mousedown, not click: the input's blur closes the list
                  // before a click would land.
                  onMouseDown={(
                    event
                  ) => {
                    event.preventDefault();
                    void selectSuggestion(
                      suggestion
                    );
                  }}
                  onMouseEnter={() =>
                    setHighlighted(
                      index
                    )
                  }
                  className={`flex cursor-pointer items-baseline gap-2 px-5 py-2.5 text-sm ${
                    index === highlighted
                      ? "bg-white/[0.08]"
                      : ""
                  }`}
                >
                  <span className="text-white">
                    {
                      suggestion.label
                    }
                  </span>

                  {suggestion.detail && (
                    <span className="truncate text-xs text-white/40">
                      {
                        suggestion.detail
                      }
                    </span>
                  )}
                </li>
              )
            )}
          </ul>
        )}

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