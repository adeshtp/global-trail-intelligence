# Technology and Main File Map

A reference for where each responsibility lives. The project narrative,
methodology and limitations are in the repository root `README.md`; this file
is the map.

## Backend

### `backend/app/main.py`
FastAPI application. Registers routers, exposes `/`, `/health` and
`/api/health`, and configures CORS. The health payload reports provider
*configuration*, never an unverified claim that a provider is healthy.

### `backend/app/core/config.py`
All settings and environment variables, including the canonical difficulty
model path.

### Persistence

None. GoBeyond serves provider-backed data with in-process caches; the
former optional PostGIS layer (`core/database.py`, `init_db.py`,
`models/trail.py`, `ENABLE_DATABASE`) was removed because no endpoint ever
read from or wrote to it. `/health` carries no database state.

### `backend/app/routes/search.py`
Place resolution through Nominatim, with caching. Decides *what was searched*:
coordinates, bounding box, administrative context, and the geocoder's own
`place_kind`.

### `backend/app/routes/discovery.py`
Place-to-trail discovery orchestration and the response contract: search-scope
resolution, deterministic complete tiling, the six-channel hiking-relevance
evidence model, ranking, real pagination, coverage accounting, peak
association, and identity recovery diagnostics for candidates that stay
`UNMAPPED`.

### `backend/app/services/postpass.py`
The OSM/Postpass identity and geometry authority. Relations, member ways,
tags, aliases, member order and roles, and real geometry. Also re-verifies a
client-supplied selection on the way in. Each public query falls back to
Overpass only when the Postpass request itself raises.

### `backend/app/services/overpass.py`
Overpass API fallback for real OSM identity and geometry during a Postpass
outage. Same dataclasses, same validation gates, provenance-marked rows
(`source: OpenStreetMap via Overpass`). Relation geometry is assembled per
member way without stitching; unusable shapes are dropped by the shared gate.
One in-flight request, capped output, cached, one polite retry on 429 only.

### `backend/app/services/trail_discovery.py`
Supplemental semantic name and identity discovery via SearXNG (preferred, may
run locally) with Tavily as the external fallback, then Gemini for extraction
and deterministic candidates if Gemini is unavailable. Deterministic extraction
reads both the result title and the result body, because search engines answer a
place query with enumeration pages whose titles are editorial and keep every
trail name in the body. It never creates geometry. Reports which provider
actually answered.

### `backend/app/routes/trails.py`
Selected-trail geometry analysis and the combined intelligence endpoint:
re-verification, component-aware measurements, weather and elevation at the
route midpoint, condition, difficulty, suitability, preparation, route demand,
plus the products and assistant endpoints.

### `backend/app/routes/elevation.py`, `weather.py`
Endpoints for elevation and weather over a selected geometry.

### `backend/app/services/elevation.py`
Sampled elevation profile per component, with gain, loss, range, average and
maximum slope. Disconnected components are measured separately so no distance
or climb is invented across a gap.

### `backend/app/services/weather.py`
Cached external weather provider. Missing values stay missing; a provider
failure is reported as a failure, never as zero.

### `backend/app/services/difficulty.py`
The recorded OSM hiking grade and the single learned estimate. Loads the one
canonical artifact, builds the inference row through the shared contract
(including terrain derived from the measured profile by the same function
training used), publishes the held-out error *and* the natural-prevalence
reliability with every prediction, and reconciles official against estimated so
only one difficulty value is ever displayed.

### `backend/app/ml/feature_contract.py`
The one contract for the model's inputs, imported by the dataset builder, the
trainer and the runtime. It owns the feature columns, the OSM value parsers,
the explicit missing-value level, and the terrain derivation, so training and
serving cannot drift apart in definition rather than only in name.

### `backend/app/ml/geographic_groups.py`
The split group. A one-degree centroid cell, then union-find across ways that
share an endpoint, so two halves of one mapped route can never land on opposite
sides of the split. Reports what the merge changed.

### `backend/app/ml/train.py`
Builds the one canonical model: the merged geographic split, estimator and
prior-correction comparison on identical folds scored under the real grade
distribution, one held-out test set read once, the independent natural sample,
the spatial buffer check, the feature-group ablation, environment and
provenance, and the single saved artifact plus evaluation report. Exits
nonzero if no candidate beats the majority baseline, so a weak model cannot
ship silently.

### `backend/app/ml/natural_holdout.py`
Draws a natural-prevalence test sample from the already held-out geography with
no per-class control, so the real class mix is measured rather than assumed.
One bounded Postpass request, and the training report refuses to score the file
if any of its rows appear in training.

### `backend/app/ml/difficulty_dataset.py`, `feature_engineering.py`
Offline dataset construction. `difficulty_dataset.py` samples real labelled OSM
ways from Postpass with deterministic geographic balancing; `feature_engineering.py`
attaches Copernicus GLO-90 terrain via public AWS COG tiles. Neither invents a
row or a label.

### `backend/app/services/intelligence.py`
Deterministic condition likelihood, route-context suitability, and prioritised
preparation. No personalisation, no health inference, and no weather-specific
item without a weather observation.

### `backend/app/services/route_complexity.py`
The interpretable route-demand profile from measured geometry. Not a trained
model and never presented as a difficulty grade.

### `backend/app/services/products.py`
Per-preparation-item product search. Returns product cards only for real
product-page URLs, web results separately, and a real search link otherwise.
Never fabricates product, image, price or availability data, and never pairs an
image across results.

### `backend/app/services/assistant.py`
Builds a retrieval corpus from the selected trail's verified data, retrieves
only the passages a question actually reaches, and optionally sends only those
to the language model. Falls back to the retrieved evidence itself when the
model is unavailable.

### `backend/app/services/rate_limit.py`
In-process request limiting for the enrichment endpoints.

### `backend/tests/`
Offline unit and resilience tests. `offline_guard.py` replaces every outbound
socket with a counter and fails the suite if anything tries the network.

## Frontend

### `frontend/app/layout.tsx`
Root layout, fonts, and site metadata.

### `frontend/app/page.tsx`
Landing page and search entry point.

### `frontend/app/explore/page.tsx`
Orchestrates the two-stage search, selection state, and the trail sections:
overview, elevation, conditions, difficulty and route demand, suitability,
preparation, products, and the floating assistant. The elevation chart draws
each disconnected component as its own polyline, so a gap is never bridged
visually. Product cards render a real image or an explicit "No image available",
and the difficulty card distinguishes the recorded grade from the model
estimate and carries the estimate's own reliability.

### `frontend/components/ExploreSearch.tsx`
Search input and query handling; calls the place-resolution API.

### `frontend/components/TrailSidebar.tsx`
Result counts and the trail list. Same-name objects are grouped for
presentation only, and only when there is evidence for it: a real OpenStreetMap
name plus members within 5 km of each other on their own geometry. Each object
remains distinct and separately selectable, and an unnamed path is never
presented as a section of a named trail.

### `frontend/components/CesiumMap.tsx`
Cesium viewer: OpenStreetMap and satellite imagery, terrain, the searched
location, discovered trail geometry, the selected trail with disconnected
components preserved, and the map/camera/3D controls.

## Libraries, and what each is actually for

| Library | Role |
| --- | --- |
| FastAPI, Uvicorn | HTTP API and ASGI server |
| Pydantic Settings | Configuration from environment |
| httpx | All outbound provider calls, with timeouts |
| Shapely | LineString/MultiLineString geometry and spatial predicates |
| Unidecode | Transliteration for name matching |
| pandas, NumPy | Tabular feature preparation and model I/O |
| scikit-learn | Preprocessing pipeline, baselines, metrics, grouped splits |
| XGBoost | A compared difficulty candidate (not the selected one; `requirements-train.txt` only) |
| joblib | Model artifact serialisation |
| Rasterio | Copernicus GLO-90 DEM access for offline feature building (`requirements-train.txt` only) |
| google-genai | Optional semantic extraction and assistant wording |
| Next.js, React, TypeScript, Tailwind | Frontend |
| CesiumJS | 3D globe, terrain, imagery |
