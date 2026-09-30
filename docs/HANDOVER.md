# Handover: bug-fix and improvement pass

This records what was done to the 15 known problems, what was added beyond
them, the decisions behind it, and what is still open. Everything is in one
branch (`sinan/bug-fixes-and-improvements`) and one PR (#4, targeting `main`).
For the file map see `docs/TECH_STACK_AND_FILE_MAP.md`; this document only adds
what changed and why.

## How to work in the repo

| Task | Command |
|---|---|
| Backend tests | `PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests` |
| Offline guard (denies sockets, counts outbound calls) | `PYTHONPATH=backend backend/.venv/bin/python backend/tests/offline_guard.py` |
| Live recall benchmark (needs the API running; never part of the suite) | `PYTHONPATH=backend backend/.venv/bin/python backend/tests/benchmark_recall.py [area ...]` |
| Backend dev server (port 8001, no auto-reload, restart after edits) | `cd backend && .venv/bin/python run.py` |
| Frontend checks | `cd frontend && npx tsc --noEmit && npm run lint && npm run build && npm run check:bundle` |

Things that cost time and are easy to trip over:

- **Baseline test errors.** 7 `test_difficulty` tests error on `main` too:
  `data/difficulty/difficulty_features.csv` is gitignored and not in the repo.
  The offline guard therefore prints `VERDICT: FAIL`; what matters is
  `outbound calls: 0`.
- **Environment files.** The backend reads `.env` at the repo root, the frontend
  reads `frontend/.env.local`. Both are gitignored. `.env.example` lists every
  setting, including the new ones below.
- **Serve the frontend on port 3000.** The backend's CORS allow-list does not
  include other ports, so a frontend on, say, 3100 shows "Could not find this
  location" even though the API works.
- **Run browser checks against `next build && next start`, not only `next dev`.**
  A production-only minifier bug blanked the map (see "Additional work").
- **There is no frontend test framework.** Frontend changes were verified with
  `tsc`, `eslint`, `next build`, `check:bundle`, and real-browser runs
  (puppeteer-core with system Chrome).
- macOS has no `timeout` command.

New optional settings: `DISCOVERY_TIME_BUDGET_SECONDS` (60),
`MAX_DISCOVERY_SPLIT_QUERIES` (200), `DISCOVERY_CACHE_TTL_SECONDS` (300),
`DISCOVERY_CACHE_MAX_ENTRIES` (2), `GEMINI_TIMEOUT_SECONDS` (30).

## The 15 items

Each fix started from a reproduction against live data or the code path, got a
failing test first, then a minimal fix. Numbers are from real runs.

| # | Problem | Root cause | What was done | Where |
|---|---|---|---|---|
| 1 | Search recall misses trails | Per-tile `LIMIT` sorted by name, and nothing reported rows being cut; time budget not enforced for queued queries; every page re-ran discovery and ranked a different set; a same-name/bbox rule dropped distinct trails | Capped tiles are re-queried as quadrants under their own budget; truncation reported in `coverage.rows_truncated`; provider rows read once per search (harvest cache); ways merge into a same-name route only when they lie along it; numbered marked paths kept. **Chamonix relations 59% -> 100% of what Postpass renders (99% of what OSM holds)** | `routes/discovery.py`, `services/postpass.py` |
| 2 | Non-hiking routes in results | `foot=designated` counted as hiking evidence, but mappers put it on pedestrian streets and paved paths | No longer counts on a street or built-up surface; `pedestrian` is not trail-capable | `routes/discovery.py` |
| 3 | Fragmented geometry | Pieces were counted, gaps never measured | `measure_geometry_completeness`: connected / gaps / separate_pieces with total and largest gap. Measurement only; nothing is joined or drawn | `services/postpass.py` |
| 4 | Slow pipeline | Tiles awaited in batches of six; no overall bound; no Gemini timeout; each of a route's member ways fetched serially (TMB timed out at 300 s) | No batch barrier; time budget; Gemini timeout; `diagnostics.timings_ms`; bulk `get_ways`. Chamonix cold 29.8 s -> 15-19 s; TMB selection >300 s -> 4.4 s | `routes/discovery.py`, `routes/trails.py`, `services/postpass.py` |
| 5, 6 | Difficulty model quality / whole-trail model | Needs retraining | **Skipped** (not a code fix) | |
| 7 | Gear ignores activity | Gear read distance, ascent, surface and current weather only | `classify_activity` (day hike / multi-day / high altitude / technical alpine) with reasons; activity-specific gear; product search uses the activity term. Scattered networks are judged by their largest piece | `services/intelligence.py`, `services/products.py` |
| 8 | Shop links go to reviews | Fallback destination was the first "relevant" result, including editorial pages | Editorial results are never a destination; falls back to a search link | `services/products.py` |
| 9 | Weather from one point | Midpoint only, at the provider's idea of its height, and only "now" | Read at start / highest / lowest / end / midpoint in one call, each at its own elevation; then judged over the walk (Naismith window, freezing level vs highest point, inferred snow). Gear, conditions and suitability all use the same window and cite their source | `services/weather.py`, `services/intelligence.py`, `routes/trails.py` |
| 10 | No live hazards | Needs live feeds | **Skipped** | |
| 11 | External failures cascade | Every call paid the full retry/backoff during an outage | `CircuitBreaker` on Postpass, Overpass, Open-Meteo (shared by elevation and weather), Nominatim, Tavily; timeouts not retried; an open Postpass breaker still falls back to Overpass | `services/rate_limit.py` and each provider |
| 12 | Not production-hardened | Deployment | **Skipped** | |
| 13 | Assistant shallow | Page never sent products; one intent per question; no activity context | Real retailer links, up to three topics per question, activity answers, shop cards in the corpus | `services/assistant.py` |
| 14 | No benchmark | Needs ground-truth labelling | **Skipped**; `benchmark_recall.py` is a regression check against Postpass, not ground truth | `tests/benchmark_recall.py` |
| 15 | Explore page unmaintainable | 4,809-line component | **4,809 -> 1,439 lines**; types, helpers, request helpers and 13 section/UI components extracted; JSX moved verbatim, state and hooks unchanged | `frontend/app/explore/*`, `frontend/components/explore/*` |

Outage measurements (hung and refused providers, real timeouts):

| Outage | Before | After |
|---|---|---|
| Postpass hangs | 94.6 s for the first two searches | 30.1 s, then 0 s |
| Open-Meteo hangs | 50-52 s on every request | 32.7 s / 30.1 s, then 0.1 s |
| Nominatim hangs | 21 s on every search | 10 s for the first three, then 0 s |
| Tavily hangs | 60 s on every request | 20 s once, then 0 s |

## Additional work beyond the 15

- **Frontend bugs found while exploring**, each reproduced in a browser on the old
  build and confirmed gone on the new one: "Show more" appended a stale page to
  a new search; "Show more" omitted `bbox`; a failed "Show more" overwrote the
  search's own error; the section nav highlighted nothing on the overview and
  its order did not match the page.
- **Pages and enrichment disagreed.** The same country-sized search returned
  13,412 / 20,292 / 23,639 ranked trails across page 1, page 2 and enrichment
  (and 84 s per page). A harvest cache keyed by the search makes them agree and
  page 2 costs 17.7 s with no provider calls.
- **Condition and suitability follow the walk window**, not "now", and report
  `assessed_over`.
- **Tiles are searched nearest the searched place first**, so when the time
  budget runs out the unread tiles are the far ones.
- **The Cesium map never rendered in production builds.** Turbopack's minifier
  rewrote a WebAssembly string in Cesium into octal escapes the browser refuses
  to parse. Fixed with `turbopackMinify: false` (commented in
  `next.config.ts`); `npm run check:bundle` fails if any built chunk does not
  parse. I first called this error harmless; it was not.
- **A bug I introduced:** an unanchored search in a patch script duplicated
  about 250 lines (`gear_recommendations` defined twice) and the suite stayed
  green. `test_module_hygiene.py` now fails on any duplicated top-level name.
- **Regressions I introduced and caught by measuring outages:** serial
  Open-Meteo calls (50 s) and a Nominatim retry that doubled its timeout (21 s).
  Both fixed.
- **Location type-ahead** (requested after the fixes, not one of the 15). The
  Explore search box now suggests places as you type. Suggestions come from
  **Photon** (`/api/search/suggest`), because the public Nominatim usage policy
  forbids autocomplete. Picking one calls `/api/search/lookup`, which asks
  Nominatim for that exact OSM id and returns the same shape a typed search
  does, so peak/broad-area detection is unchanged and the place searched is the
  place clicked (no re-ranking). Photon has its own breaker, cache, in-flight
  sharing and rate limiter (`suggest_limiter`, so typing never spends the
  submitted search's budget); it is never retried. The UI is a debounced
  (250 ms, 3+ characters) ARIA combobox with arrow keys, Escape and mouse-down
  selection, and stale responses are aborted. Plain Enter behaves exactly as
  before. It is driven from `onChange` only, so `/explore?query=X` does not open
  the list. Checked in a browser on a production build (stale-response hold,
  keyboard, click picks the clicked place, auto-search leaves the list closed).
- **Readability pass** (requested after the fixes). Secondary text was 25-40%
  white on a near-black surface, which measures 2.2-3.8:1 (WCAG wants 4.5:1),
  at 9-11px. The theme is unchanged; text opacities were remapped in one
  monotone scale (25/30 -> 55, 35/40 -> 60, 45/50 -> 70, 55/60 -> 75, 65/70 ->
  80; every step is now 6:1 or better) and the size scale moved up one step
  (9->11, 10->12, 11/12/`text-xs`->13px; `text-sm` unchanged), across the
  landing page, Explore, the sidebar, the map controls and every section.
  Disabled states keep their reduced opacity on purpose. Checked in screenshots
  before and after (weather, gear, discovery list); no overflow.
- **Sort and filter for the trail list** (requested after the fixes).
  `/trails/discover` and `/trails/enrichment` take `sort` (relevance, nearest,
  longest, shortest, easiest), repeated `grade` (recorded OSM grades, validated
  against the model contract) and `min_length_km` / `max_length_km`. They apply
  to the **whole ranked set before it is cut into pages**, so page 2 continues
  the order, every count follows the filter, and a change reads the cached
  harvest (no provider calls). Ties fall back to the default ranking so the
  order is total. Trails with no shape have no length or grade, so a filter
  hides them rather than pretending they match. The response carries a `view`
  summary. Bug found and fixed on the way: a filter that removed every trail
  while a provider had also failed was reported as `unavailable` (an outage);
  `_discovery_status` now reports `partial` or `success`.
  **UI:** one row above the list in the discovery panel: a Sort select and a
  Filters popover (Difficulty Easy/Moderate/Hard/Very Hard, Length, "On the map
  only", Clear) with an active-count badge and a "3 of 61 trails match" line.
  Changing sort, difficulty or length is a new search of the same place at page
  1 through the guarded discovery path (coalesced for 400 ms, and enrichment
  and "Show more" carry the same view); "On the map only" hides rows in the
  browser. A filter matching nothing shows an empty state with Clear.
  **shadcn/ui** was introduced for this (Select, Popover, ToggleGroup, Switch,
  Button, Badge in `components/ui`, `lib/utils.ts`); its theme tokens are
  mapped to the existing dark palette in `globals.css` and do not touch the
  body's `--background`/`--foreground`. Note the shadcn CLI resolved `cn` as an
  unrelated npm package; it was removed in favour of the standard
  `clsx` + `tailwind-merge` helper.
- **Two whole-branch code reviews** (backend and frontend). Backend findings, all
  fixed with tests in `test_review_findings.py`: half-read harvests were cached;
  the breaker admitted every caller as a probe after cooldown; an untestable
  geometry was dropped as a duplicate; route-weather coordinates lost precision
  (`:g`); a route collapsing to one sample lost its elevation. Frontend findings,
  all fixed: scroll observers went stale after expanding and collapsing the map
  (reproduced in a browser: the nav stayed on "Trail"), an unused ref, hand-copied
  prop types in `DiscoveryPanel`, and non-unique sample keys.

## Decisions and why

| Decision | Why |
|---|---|
| One branch, one PR (#4) | Chosen by the user partway through; the earlier per-item PRs (#2, #3) were closed in favour of it. It stays a **draft** until the user marks it ready. |
| No backend restructuring | A modular-monolith layout was proposed and declined; the pass is fixes only. New helpers went into the module that already owned the concern. |
| Skip 5, 6, 10, 12, 14 | Not code fixes: model retraining, live feeds, deployment, ground-truth labelling. |
| Measure before fixing | Several first guesses were wrong. `distance == 0` is not "along" a route; a bbox `&&` overlap is not an intersection; a profiler's quadratic-scan finding was 1 s of a 15 s cost. Each correction changed the code or the benchmark. |
| Geometry memo rejected | Measured 7-13% faster for +56 MB per 8,700 candidates, so hundreds of MB at country scale. A name cache and a by-name index were kept instead. |
| Cache time-limited harvests for 5 min | It is what keeps pages consistent and fast; without it every page re-reads the providers and lands on a different set. Failed or half-read harvests are never cached. |
| Weather is an inference, labelled as one | Nothing can observe a trail. The window, freezing-level and snow thresholds are named constants and every reason cites its forecast source. Hazards and closures (item 10) are not covered. |
| No shared `httpx` client | Measured (about 0.34 s per Postpass call) but clients are bound to an event loop and tests use `asyncio.run`. |
| No Gemini circuit breaker | Its timeout is already bounded and tested, and an outage falls back to deterministic extraction. |
| No provider status in `/health` | Optional and the first thing to cut. |
| Photon for suggestions, Nominatim for the search | Nominatim's policy forbids autocomplete; Photon is OSM-based and built for it, so suggestions match our data. The public Photon instance has a fair-use limit: watch it, and self-host or switch provider if usage grows. |
| Suggestion pick uses a lookup by OSM id, not a text search | Photon's fields differ from Nominatim's (`osm_key`/`osm_value`, no `addresstype`, different extent order), and re-searching the label could rank a different place than the one clicked. |
| Sort/filter on the server, controls beside the list (not in the search box) | A page-only sort is wrong whenever more pages exist ("nearest of the loaded ones"). The search box picks the place; these act on the list, so they sit above it where the result changes. |
| Turn minification off, not work around it | Smallest change that restores the map; guarded by `check:bundle`. |
| Commit format `<type>(api): 4-8 word message`, tests first, dataclasses/pydantic for new structures | The user's standing rules. |

## Still open

- **Country-sized searches are time-limited, not exhaustive** (searched outward
  from the place). Assembly is about 15 s per page at that scale (2.6 s on
  Chamonix). No single hot spot is left; the next lever is caching the ranked
  list, which means restructuring a roughly 550-line function.
- **Very long routes are missing from Postpass** (Alta Via 2, Balcon du Leman,
  Cammino Balteo, Boucle de Feissons), so this pipeline cannot map them.
- **`ERR_BLOCKED_BY_RESPONSE.NotSameOrigin` console line.** Seen in earlier
  full-flow runs, not reproduced in a targeted one. Cause unverified (guess: a
  retailer image CDN blocking hotlinks). Unrelated to the map.
- **Type-ahead depends on the public Photon instance** (fair use, no SLA).
  When it is down the box simply shows no suggestions and typed search still
  works.
- **`get_way` requires a `name` tag**, so unnamed member ways are not scored in
  route difficulty (TMB scored 25 of 261 named members). Pre-existing.
- **Recall figures are against the Postpass mirror in five areas**, not human
  ground truth, and say nothing about precision.
- **The item-15 before/after browser comparison ran on builds with a blank map**
  (the minifier bug). The full flow was re-run afterwards with the map present.
  `next dev` was not verified.
- **Housekeeping:** two stale remote branches (`fix/01-recall-truncation`,
  `fix/08-shop-links`) and a few local `fix/*` / `diag/*` branches remain. Their
  commits are already in this branch. They are not deleted.
