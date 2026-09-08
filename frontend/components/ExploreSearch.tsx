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
    searchedQuery: string,
  ) => void;
};

function normalizeText(
  value: string,
): string {
  return value
    .toLowerCase()
    .replace(
      /[(),./_-]/g,
      " ",
    )
    .replace(
      /\s+/g,
      " ",
    )
    .trim();
}

function chooseBestLocation(
  query: string,
  results: LocationResult[],
): LocationResult | null {
  if (results.length === 0) {
    return null;
  }

  const normalizedQuery =
    normalizeText(query);

  const queryTokens =
    normalizedQuery
      .split(" ")
      .filter(Boolean);

  let best =
    results[0];

  let bestScore =
    Number.NEGATIVE_INFINITY;

  results.forEach(
    (
      result,
      index,
    ) => {
      const name =
        normalizeText(
          result.display_name,
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
          normalizedQuery,
        )
      ) {
        score += 600;
      }

      for (
        const token of
          queryTokens
      ) {
        if (
          name.includes(
            token,
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
        "hike",
        "ridge",
        "viewpoint",
        "falls",
        "pass",
        "gorge",
        "sanctuary",
        "reserve",
      ];

      for (
        const keyword of
          outdoorKeywords
      ) {
        if (
          normalizedQuery.includes(
            keyword,
          ) &&
          name.includes(
            keyword,
          )
        ) {
          score += 150;
        }
      }

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
    },
  );

  return best;
}

export default function ExploreSearch({
  initialQuery,
  onLocationFound,
}: ExploreSearchProps) {
  const [search, setSearch] =
    useState(
      initialQuery ??
        "",
    );

  const [
    isSearching,
    setIsSearching,
  ] = useState(false);

  const [
    error,
    setError,
  ] = useState<
    string | null
  >(null);

  const automaticSearchRef =
    useRef<
      string | null
    >(null);

  const requestRef =
    useRef<
      AbortController | null
    >(null);

  async function performSearch(
    queryOverride?: string,
  ) {
    const query = (
      queryOverride ??
      search
    ).trim();

    if (!query) {
      return;
    }

    requestRef.current?.abort();

    const controller =
      new AbortController();

    requestRef.current =
      controller;

    setIsSearching(
      true,
    );

    setError(null);

    try {
      const response =
        await fetch(
          `${API_BASE_URL}/api/search?q=${encodeURIComponent(
            query,
          )}`,
          {
            cache:
              "no-store",
            signal:
              controller.signal,
          },
        );

      if (!response.ok) {
        throw new Error(
          `Location search failed: ${response.status}`,
        );
      }

      const data =
        (await response.json()) as SearchResponse;

      if (
        !Array.isArray(
          data.results,
        ) ||
        data.results.length ===
          0
      ) {
        throw new Error(
          "No location found",
        );
      }

      const result =
        chooseBestLocation(
          query,
          data.results,
        );

      if (!result) {
        throw new Error(
          "No usable result",
        );
      }

      const latitude =
        Number(
          result.latitude,
        );

      const longitude =
        Number(
          result.longitude,
        );

      if (
        !Number.isFinite(
          latitude,
        ) ||
        !Number.isFinite(
          longitude,
        )
      ) {
        throw new Error(
          "Invalid coordinates",
        );
      }

      setSearch(
        query,
      );

      onLocationFound(
        {
          latitude,
          longitude,
        },
        result.display_name,
        query,
      );
    } catch (caughtError) {
      if (
        caughtError instanceof
          DOMException &&
        caughtError.name ===
          "AbortError"
      ) {
        return;
      }

      console.error(
        "Location search failed:",
        caughtError,
      );

      setError(
        "Could not find this location.",
      );
    } finally {
      if (
        requestRef.current ===
        controller
      ) {
        requestRef.current =
          null;

        setIsSearching(
          false,
        );
      }
    }
  }

  function handleSubmit(
    event: FormEvent<HTMLFormElement>,
  ) {
    event.preventDefault();

    void performSearch();
  }

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
      query,
    );

    void performSearch(
      query,
    );

    // Do not abort the automatic request
    // during React development cleanup.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    initialQuery,
  ]);

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
            value={search}
            onChange={(
              event,
            ) => {
              setSearch(
                event.target
                  .value,
              );

              setError(null);
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
            {isSearching
              ? "Searching..."
              : "Explore"}
          </button>
        </div>
      </form>

      {error && (
        <p className="mt-3 px-2 text-xs text-red-300">
          {error}
        </p>
      )}
    </div>
  );
}