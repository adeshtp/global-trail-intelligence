# GoBeyond

GoBeyond is a hiking and trekking trail-intelligence application. You search
for a real place, peak, trail, village, town, district, region or country. It
discovers relevant real routes, keeps their real OSM identity and real
geometry, and shows you one selected route in Cesium together with the
terrain, current conditions, suitability, preparation and grounded answers
about that exact route.

Nothing in the system is invented. If a trail cannot be verified against a
real geometry source, it stays honestly unmapped.

## Product flow

```text
search a place / peak / trail / region
  -> Nominatim place resolution (geocoder decides what was searched)
  -> search scope resolved (point, locality, district, region, country)
  -> large areas split into a deterministic, complete tile grid
  -> Postpass/OSM relations and named ways + optional semantic discovery
  -> multi-channel hiking-relevance evidence model
  -> MAP_READY (real verified geometry) or honest UNMAPPED
  -> deterministic ranking, paginated
  -> select one exact route
  -> exact verified geometry re-checked in Cesium
  -> route measurements from that geometry
  -> elevation profile + current weather at the route
  -> condition inference, route demands, suitability
  -> prioritised preparation
  -> optional real product search
  -> local retrieval + optional grounded assistant
```

## Running it

### Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp ../.env.example .env        # then fill in the keys you have
.venv/bin/python run.py        # http://127.0.0.1:8001
```

### Frontend

```bash
cd frontend
npm install
npm run dev                    # http://127.0.0.1:3000
```

### Configuration

Copy the root `.env.example`. Every provider is optional except the
OpenStreetMap/Postpass query path, which is what makes a route mappable.

| Setting | Required | If it is missing |
| --- | --- | --- |
| `POSTPASS_URL` | effectively yes | Discovery cannot verify any geometry, so results stay `UNMAPPED` |
| `NEXT_PUBLIC_API_BASE_URL` | yes (frontend) | Frontend cannot reach the backend |
| `GEMINI_API_KEY` | no | Assistant answers compose from the trail's own verified data; the Products and semantic layers fall back to conservative local extraction |
| `TAVILY_API_KEY` | no | Product section offers real search links only |
| `SEARXNG_URL` | no | Semantic enrichment is skipped; OSM discovery is unaffected |

SearXNG can be started locally with:

```bash
docker compose -f searxng/docker-compose.yml up -d
```

### Health check

`GET /health` and `GET /api/health` both return the same payload. There is one
health endpoint, reachable under either name.

## Architecture

One implementation per responsibility.

| File | Responsibility |
| --- | --- |
| `backend/app/routes/discovery.py` | Search scope, tiling, evidence model, ranking, pagination, coverage |
| `backend/app/services/postpass.py` | The OSM/Postpass identity and geometry authority |
| `backend/app/services/trail_discovery.py` | Supplemental semantic discovery; never creates geometry |
| `backend/app/routes/trails.py` | Selected-trail verification, measurements, intelligence, products, assistant |
| `backend/app/services/intelligence.py` | Condition, suitability, prioritised preparation |
| `backend/app/services/route_complexity.py` | Interpretable route-demand profile from measured geometry |
| `backend/app/services/difficulty.py` | The recorded OSM grade and the single learned estimate |
| `backend/app/ml/feature_contract.py` | The one feature, value and terrain contract shared by training and serving |
| `backend/app/ml/geographic_groups.py` | The split group: one-degree cells merged across connected route fragments |
| `backend/app/ml/train.py` | Trains, evaluates and saves the one difficulty model |
| `backend/app/ml/natural_holdout.py` | Draws the natural-prevalence test sample from held-out geography |
| `backend/app/ml/difficulty_dataset.py`, `feature_engineering.py` | Dataset preparation: labels, geometry, sampled terrain |
| `backend/app/services/assistant.py` | Local retrieval corpus and retrieval scoring |
| `backend/app/services/products.py` | Gear-derived product search with truthful labelling and URL validation |
| `backend/app/services/weather.py`, `elevation.py` | Cached external providers with truthful partial failure |
| `frontend/app/explore/page.tsx` | Two-stage orchestration, selection state, section UI |

### Discovery evidence model

Six independent channels are evaluated before any decision: structural, name,
geographic, access, surface/physical metadata, and semantic. A weak channel
cannot delete a candidate before the others are read. Accepted candidates are
reported as `strong` or `weak`:

- **strong** — carries real hiking metadata (route, SAC scale, trail
  visibility, designated footway, official trail designation).
- **weak** — a real, named, locally-named path with physical metadata but no
  route or trail-scale tags.

`weak` describes the strength of the *relevance evidence only*. Weak evidence
is still MAP_READY, which means the geometry is real and verified.

### Search scope and geographic radius

The search radius follows what was asked, not one flat default. This is
decided from the words in the user's query and the resolved place type — never
from a list of known place or trail names.

| What was searched | Radius | Source string |
| --- | --- | --- |
| A peak / summit (`place_kind=peak`) | 6 km | `peak_radius` |
| An exact trail name (query carries a route word *and* a name) | 25 km | `local_radius` |
| A place — town, hill station, district | 40 km | `place_association_radius` |
| A broad area (`scope=area`) | 60 km | `area_radius` |
| A client-supplied bbox | as requested | `requested_bbox` |

The place radius is the one that mattered. It was previously a flat 25 km for
every non-peak query, which quietly confined a hill-station search to a
town-sized circle and excluded the very destinations the place is known for —
Anamudi is about 41 km from Munnar's town centre. It is still a bounded circle
around the resolved point (about 6,400 km², one tile), so a place search never
becomes a country sweep, and a genuine region search passes a real bbox.

Exact-trail detection deliberately reads the raw token set rather than the
similarity token set, because the latter strips generic words like `footpath`,
`trail` and `loop` — which made every trail-shaped query read as a bare place
name.

### Semantic discovery and recall

Semantic discovery is supplemental. It can only ever *nominate* a trail; Postpass
and OSM remain the authority for identity and geometry, and Gemini never creates
geometry.

Search engines answer a regional query with enumeration pages — *"Top 6
Trekking Trails in Munnar"* — whose **titles are editorial** and which keep
every trail name in the **body**. Reading only the title therefore returned
almost nothing for a place search: 19 real results for Munnar produced 0
candidates. Two changes fixed it:

- Names are now also read from the result body. A phrase qualifies only if it
  carries a route or destination word *in itself*; requiring merely a nearby
  occurrence was too weak, because `trail` appears once on a long page and then
  every capitalised word for miles of text passes. Settlement and business words
  (`station`, `village`, `estate`) disqualify a name unless it also claims to be
  a route, so `Top Station Sunrise Trek` survives and `Munnar Hill Station` does
  not.
- The query list no longer leads with two near-identical generic phrasings, and
  the configured query count has a floor of three, because two phrasings of one
  intent return the same pages.

Snippets are passed to the model at 2500 characters rather than 700, because
the shorter cap kept the lead paragraph of a listicle and discarded the
enumeration itself.

The same Munnar search now returns **37 trails (16 MAP_READY, 21 UNMAPPED)**
against 10 before, and Wayanad **27 (12 / 15)** against 9, with `Chokramudi
Trail`, `Lakshmi Hills`, `Meesapulimala`, `Kozhiparamba footpath`,
`Chembra Peak`, `Banasura Hills` and `Pakshipathalam` all reachable.

### Large areas and coverage

Any area wider than `MAX_TILE_SPAN_DEG` is split into a uniform deterministic
grid. The grid is a **complete partition**, not a sample, so total tile area
equals the requested area. Coverage is reported honestly and separately:

- `area_considered` / `area_considered_source` / `area_km2`
- `tiles_total` / `tiles_queried` / `tiles_failed` / `tiles_skipped`
- `candidates_found` → `candidates_accepted` → `candidates_ranked` → `candidates_returned`
- `coverage_complete`

Failed or unsearched tiles are reported, never hidden. The ranked result set
is fully computed and then returned one page at a time, so a country search
reporting 5,900 ranked trails is navigable rather than pretending to be 100.

**A provider that fails, and a provider that answers with nothing, are
different facts — and neither is an empty area.**

| What happened | `status` | `coverage` |
| --- | --- | --- |
| Provider could not be reached | `unavailable` | `provider_failed: true`, `coverage_complete: false` |
| Provider answered, zero rows for the whole area | `no_provider_data` | `provider_returned_no_rows: true`, `coverage_complete: false` |
| Provider answered with rows, some accepted | `success` / `partial` | as reached |
| Provider answered with rows, none accepted | `empty` | coverage reached |

The `unavailable` case was a real bug, found live: Postpass answered HTTP 503
and the response reported `status: "empty"` with `coverage_complete: true`,
which claimed the area had been searched and found empty. It had not been
searched at all. The condition that reported a failure used to require results
to exist first, so a total outage looked like a successful empty search. It now
does not, and a test pins it.

For `no_provider_data`, two different things produce it and the response does
not pick one, because it cannot: either the public Postpass mirror — a partial,
moving snapshot — does not currently hold the area, **or** the searched
coordinates did not point where intended. Range validation cannot catch a
transposed latitude/longitude, because a longitude is a perfectly valid
latitude, so the exact box that was queried is always reported in
`coverage.area_considered` and an implausible search area stays diagnosable.
(This is not hypothetical: a latitude of 77.09 with Munnar's correct longitude
of 77.06 is a coordinate transposition, and it places the box in the Arctic
Ocean.) Neither cause means the area has no trails, and the interface never
says it does.

### Peak-aware search

When the geocoder says the searched place is a summit, the backend measures a
real relationship between every route and the summit using the route's own
already-fetched geometry (no extra query, no routing). Results are grouped
into `summit_route` (within 150 m of the summit point), `peak_approach` (within
1.5 km) and `nearby_route`, and summit routes rank first.

The response states which of these is true, and never conflates them:

- "no mapped route touches the summit point" is a different statement from
  "no relevant associated hiking trail was found".

A peak never causes a route to be synthesised, extended or snapped.

## Geometry and identity rules

- Postpass/OpenStreetMap decides OSM identity and geometry.
- Nominatim decides what the user searched for.
- Web search and LLMs may supplement names and evidence but never create
  geography.
- Geometry is never fabricated, snapped to a nearby road, stitched from
  unrelated ways, or produced by graph synthesis.
- Disconnected components are never joined; per-component ascent is measured
  per component so no climb is invented between them.
- Relations preserve relation ID, member way IDs, member order and roles.
- Same-name physical objects are never merged. Distinct OSM objects stay
  independently selectable identities. They are only *presented* under one
  trail-family heading when the name is genuinely shared, so a reader sees
  grouped sections rather than apparently duplicated results.
- A member way that carries its route's own name is not a second trail: when
  an accepted relation or component lists the way as a member and the names
  agree, the route card represents it and the member stays reachable through
  `member_way_ids`. A member with a genuinely different name stays its own
  card. This is decided by OSM identity plus name agreement, never by
  geometric proximity, and it applies globally — no place-specific rules.
- Coordinates are always `[longitude, latitude]`.

### What the relevance filter costs, measured

A live district search returned **58** real named candidates and accepted
**3**. Every one of the 58 was inspected with its rejection reason, because an
acceptance rate of 5% is either excellent precision or a broken filter and the
response is the same either way.

**Precision: measured, and high.** All 55 rejections carry a defensible
reason. Grouped by cause: 24 road-oriented names ("CHC Thariyode Road",
"Enn Ooru Access Road"), 7 non-descriptive names that are survey or node
markers ("426", "435,436()"), 6 with insufficient evidence, 5 generic path
identities ("Path", "Trail"), 4 infrastructure names ("Family Health Centre
Kottathara Entry", "guest quarters road"), 3 structure-access stubs, 2
religious-site access paths. No road, building entrance or numbered marker
reached the user.

**Recall: one specific gap was found, and is now closed.** The audit found
genuine named local trails being rejected: 0.5–1.1 km paths with real
toponym-style names and verified geometry, dropped solely for being shorter
than `MIN_LOCAL_PATH_STRONG_KM` (1.5 km) with no recorded metadata.

The first fix — a lower length bar keyed on name distinctiveness — recovered 4
candidates and immediately admitted "electricity officie way" and "harbour
roadd" in the same test suite. Nothing lexical separates a local toponym from a
facility descriptor: both are a non-generic word next to "way". That fix was
**reverted**, because it traded measured precision for unmeasured recall.

The fix that works asks a different question: does the name identify a real
**place**, and is there independent physical evidence? Admission now requires
both, and specifically:

- a **facility or function word** ("college", "guest quarters", "electricity",
  "temple", "shed", "local") disqualifies the name outright, so "Al Azhar
  College Path" and "Local Path" stay out;
- a **misspelt road word** ("roadd") disqualifies it, so "harbour roadd" stays
  out even though the road-name check matches whole words only;
- a **built-up surface or an urban highway** disqualifies it, so "North Giri
  Veethi" on asphalt stays out however well it is named;
- a name of **only generic route words** ("Path", "Trail", "footway") never
  counts as name evidence at all, which also fixes a bare "Trail" having been
  accepted as a confirmed route.

Conversely, "Anamudi Ghat Road path" is **accepted**, because it names a
destination and says in so many words that it is a path. The recovery is 13
real trails against 25 noise names with zero mistakes, and every case is pinned
in `tests/test_scope_and_evidence.py` so it cannot silently regress.

Naming a place is real evidence, but it is not by itself a claim that
something is a trail, so recovered trails are reported in the **weak** evidence
class and counted separately in the interface.

**No recall figure is claimed.** One district is a single observation and there
is no reference set to measure recall against; a measured recall number would
need provider budget this run did not spend.

## Data Science: what was built, evaluated, and why

This is the honest account for a technical audience. It is deliberately kept
out of the normal user view.

### The task

**Supervised prediction of the difficulty tier a path's recorded OpenStreetMap
grade falls into.** The label source is `sac_scale`, used exactly as mappers
record it, across all seven of its levels:

`strolling`, `hiking`, `mountain_hiking`, `demanding_mountain_hiking`,
`alpine_hiking`, `demanding_alpine_hiking`, `difficult_alpine_hiking`

Those seven levels are mapped onto three **skill tiers** by a fixed, published
mapping in `app/ml/feature_contract.py`:

| Tier | Recorded grades | What it means |
| --- | --- | --- |
| `walking` | strolling, hiking | Walkable by anyone; no scrambling |
| `mountain` | mountain_hiking, demanding_mountain_hiking | Needs mountain fitness and sure footing |
| `alpine` | alpine_hiking, demanding_alpine_hiking, difficult_alpine_hiking | Scrambling, possibly climbing or a rope |

**The recorded value is never altered, imputed or invented.** The mapping is
total over the seven levels, is applied identically by training and by serving,
and the raw `sac_scale` is still shown wherever OpenStreetMap records one.

This is a real product distinction rather than a convenient grouping, and it
was chosen on measured evidence, not on the resulting score. The seven levels
separate degrees of difficulty *within* a skill tier, which is not the question
a walker asks. Predicting them separately was built and evaluated first, and it
scored **0.3460 against a 0.5689 majority baseline** on geographically held-out
data under the real grade distribution — worse than answering with the most
common grade. The tier model scores **0.7406 against 0.5708**, above the
baseline. The seven-grade target was abandoned on its result, not defended on
principle; the reasoning for keeping the recorded grade itself is that it is
the only real label available.

**Observation unit: one OSM way.** The model is fitted — and only ever
scored — on individual ways. A selected route (relation or component) is
answered by scoring each verified member way with the same artifact and
taking the majority tier, with the full per-tier split published; route
totals are never fed to the model. See “Route answers” below.

### The dataset

`data/difficulty/difficulty_features.csv` — **16,571 real OSM ways** carrying a
recorded `sac_scale`.

- **Unique OSM ways:** 16,571. **Duplicate geometries: 0**, measured rather than
  assumed.
- **Class distribution (as sampled):** 2,500 each for six grades, 1,571 for
  `difficult_alpine_hiking`.
- **Natural OSM prior** (from `difficulty_audit.json`, before sampling): 6,350
  `strolling`, 520,799 `hiking`, 302,978 `mountain_hiking`, and far fewer alpine
  grades. The sample is capped at 2,500 per class and 20 rows per one-degree cell
  so every grade is learnable. **That balance is designed, not natural**, and
  both distributions are recorded so the numbers cannot be misread.
- **Terrain coverage:** 11,048 of 16,571 rows (66.7%) have a sampled Copernicus
  DEM GLO-90 profile.
- **Synthetic rows: 0.** Nothing is generated, copied or oversampled.

Build it with:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.ml.difficulty_dataset
PYTHONPATH=backend backend/.venv/bin/python -m app.ml.feature_engineering
```

### Features — 26, and identical at training and serving time

`backend/app/ml/feature_contract.py` is not just a list of column names. The
dataset builder, the trainer and the runtime service all import it, so the
*definitions* cannot drift apart, not merely the labels. It owns:

- the 26 feature columns;
- the OSM value parsers (`incline` to percent, `width` to metres, incline
  direction, spelling variants of `trail_visibility`, `rope`/`ropes`);
- the explicit `<MISSING>` level, so an absent tag is one category the model can
  learn rather than a hole the imputer fills with the most common tag;
- the terrain derivation: per connected component, resampled to the 90 m DEM
  resolution, slope per sampling interval, no smoothing, and the same 90 m
  minimum component length.

**20 base features** (17 real attributes plus 3 coverage flags):

| Group | Features |
| --- | --- |
| Geometry | `length_km`, `mean_turn`, `verts_per_km`, `incline_pct`, `width_m` |
| Sampled terrain | `elevation_gain_m`, `elevation_loss_m`, `elevation_range_m`, `average_slope_pct`, `max_slope_pct` |
| OSM tagging | `highway`, `surface`, `smoothness`, `tracktype`, `trail_visibility`, `incline_direction`, `assisted_trail` |
| Coverage flags | `terrain_available`, `incline_numeric_missing`, `width_missing` |

**6 derived features**, each a function of the base features alone:
`log_length`, `gain_per_km`, `descent_ratio`, `steepness_squared`,
`relief_per_km`, `length_x_no_terrain`.

**Preprocessing**, fitted inside each training fold only: median imputation and
standardisation for numeric columns, most-frequent imputation and one-hot
encoding for tag columns.

### Terrain parity, precisely

The runtime derives the model's terrain columns from the selected route's own
sampled elevation profile using the *same function* the training rows were
produced by (`model_terrain_features`). Gain, loss, range, average slope and
maximum slope therefore mean the same quantity on both sides, and a component
shorter than one DEM cell is dropped on both sides. A test raises a second
component 500 m and asserts that ascent and descent do not move.

What parity cannot fix is the elevation **source**. Training read a static
Copernicus GLO-90 DEM; serving reads a live Open-Meteo profile. Both are real
global DEMs and the arithmetic applied to them is identical, but the numbers are
not expected to be identical. That is a stated limitation, not a hidden one.

The user-facing elevation figures on the page are a **separate presentation** of
the same profile — smoothed, with slope measured over a 150 m window, which
reads better on a chart — and are deliberately not the model's inputs.

Splitting the same held-out set by whether a sampled profile exists
(scored under the real grade distribution, like everything else):

| Held-out subset | Rows | Accuracy | Macro-F1 |
| --- | --- | --- | --- |
| Rows with a sampled terrain profile | 2,278 | 0.7281 | **0.5817** |
| Rows without | 1,035 | **0.7631** | 0.5452 |

Rows lacking terrain are measurably weaker on macro-F1 — which is why
`terrain_available` is itself a feature, so the model knows when those
predictors are absent rather than measured.

### Leakage controls

- `sac_scale` is the label and is **never** a feature.
- `difficulty_class` is derived from `sac_scale` and is a target, not a feature.
- `split_group` only keeps whole geographic areas inside one fold.
- `osm_id` is never a feature.
- Every derived feature is a function of base features alone, so no
  transformation can smuggle the label in.
- Preprocessing is fitted inside training folds, never on the full set.
- **Duplicate geometry: 0 rows**, counted in the report.
- **Route-fragment leakage: closed.** The split group is a one-degree centroid
  cell, *merged across ways that share an endpoint*. 1,828 connected way pairs
  were found, 16 of which crossed a cell boundary; after the merge **0 connected
  pairs cross a split**. The merge is measured, not claimed.
- A test proves the inference row is **identical** when the recorded `sac_scale`
  is changed, so the estimate cannot be echoing the answer.
- **The tier mapping cannot leak.** It is a total function of the recorded
  grade applied to the *label* column only, and it is never applied to a
  feature. The two tests that pin it also prove every recorded grade maps to
  exactly one tier and that the same table is used by training and by serving.

**What leakage control cannot do here, stated plainly.** Group overlap of zero
is not proof of complete route-family isolation, and is not presented as such:

- Postpass's way table does not expose relation membership, so two
  non-adjacent ways belonging to the same named route — two loops sharing a
  trailhead, say — cannot be *proven* to share a group.
- The 5 km spatial buffer is one robustness check on adjacency, not a
  substitute for knowing route membership.
- Duplicate-geometry and connected-fragment leakage are closed and measured.
  Same-route and same-relation leakage are **reduced and bounded, not proven
  closed**, because the source does not carry the information that would prove
  it. The residual risk is recorded in the report rather than glossed.

### Split methodology

`StratifiedGroupKFold`, `random_state=20260927`. One-degree centroid cells are
merged across ways that share an endpoint, so connected fragments of one route
cannot straddle the split. One whole fold of merged groups is removed as the
test set before anything is fitted: **13,258 training rows, 3,313 test rows,
group overlap 0**, across **1,490 merged groups** (from 1,501 cells).

All selection runs as 5-fold grouped cross-validation **inside the training
split only**, and the test set is read once, afterwards.

**Spatial sensitivity.** Groups are disjoint but neighbouring cells are
adjacent, so this is checked rather than assumed: restricting the test to rows
at least 5 km from any training row leaves 3,185 of 3,313 and moves accuracy to
0.7413, so proximity is not doing the work. It is one robustness check, not
proof that all route-family leakage is eliminated — see the leakage section.

### Candidates compared

Three estimators crossed with seven prior-correction strengths, on identical
folds, features and preprocessing: 21 candidates in total. The selection rule is
fixed in advance: a candidate must beat the majority-class baseline on
validation accuracy **under the real grade distribution**, and among those the
highest macro-F1 wins. The test set takes no part.

| | Validation accuracy | Validation macro-F1 |
| --- | --- | --- |
| **Logistic regression, prior 0.5** | **0.7430** | **0.5961** |
| Logistic regression, prior 0.35 | 0.7285 | 0.5942 |
| Random forest, prior 0.5 | 0.7331 | 0.5745 |
| XGBoost, prior 0.5 | 0.7182 | 0.5845 |
| Logistic regression, unweighted | 0.6145 | 0.4997 |
| *majority baseline* | *0.5705* | *0.2423* |

**Selected: logistic regression with prior strength 0.5.** The narrow spread
across estimators at the same prior strength is itself the finding — the
algorithm is not the bottleneck, the label is. The feature-group ablation says
the same thing: tagging carries the signal (giving up 0.0881 macro-F1 when
removed), terrain 0.0104, route shape 0.0047, geometry and measured attributes
nothing measurable.

What a mapper writes on a way predicts their grade judgement far better than
the way's physical measurements do. That is why the interface says "estimate"
rather than "measured".

### Why the score is not higher — three measurements

Two very different causes produce a weak score, with opposite remedies: too
little data, or a label that is not recoverable. They are not equivalent and
neither is guessed. The training script measures them on the training split,
records the results in the artifact, and separates what is **proven** from what
is only a **supported hypothesis**.

**1. Label collision — does the recorded grade follow from the recorded tags?**
This is a property of the data rather than an inference from a score. Ways are
grouped by their complete set of categorical OSM tags:

| | |
| --- | --- |
| Unique tagging combinations | 1,884 |
| Ways | 16,571 |
| Combinations carrying **more than one grade** | 584 |
| Ways inside those ambiguous combinations | 14,681 (**88.6%**) |
| Largest single ambiguous group | 3,257 ways spanning **all 7 grades** |
| In-sample majority share per combination | 0.8508 |

Almost nine ways in ten sit in a tagging group that is recorded at several
different grades, and the largest group — `highway=path` with no other tags,
3,257 ways — spans **all seven grades** with 22% purity.

Collision alone would not prove the label is the problem, because the remaining
features could in principle explain the variation. So that was measured
directly, inside that one group, on the coarse question *any alpine grade vs
everything else*:

| Feature, within the group | AUC |
| --- | --- |
| `max_slope_pct` | 0.6936 |
| `average_slope_pct` | 0.6814 |
| `elevation_range_m` | 0.6279 |
| `elevation_gain_m` | 0.5924 |
| `length_km` | 0.5233 |

0.5 is no signal. The physical features separate the alpine grades from the
easier ones only weakly, so most of the within-group disagreement is not
recoverable from anything the dataset records.

> **PROVEN:** the recorded grade is not a function of the recorded tagging.
> Ways with byte-identical tag sets carry different recorded grades, so no
> classifier reading those columns can resolve the disagreement. Within the
> largest such group, the remaining features reach only AUC 0.69 on the
> coarsest possible distinction.
>
> **SUPPORTED HYPOTHESIS:** volunteer mappers apply these seven labels
> inconsistently enough to be the binding constraint on achievable accuracy.
> The collision count and the weak within-group AUC are the direct evidence;
> concluding that this is the *limiting* factor is the reasoned step, and it is
> labelled as a hypothesis rather than a measurement.

**2. Tagging ceiling — is the model just re-reading the tags?** A majority-tier
lookup per tagging combination, learned on the training split only and applied
to the held-out groups (scored under the real grade distribution):

| | Accuracy | Macro-F1 |
| --- | --- | --- |
| Pure tagging lookup, held out | 0.5603 | 0.4496 |
| Full 26-feature model, held out | **0.7406** | **0.5779** |

The model beats pure tag reading by +0.18 accuracy, so the continuous, derived,
terrain and shape features contribute real information rather than restating
the tags.

**3. Feature-group ablation — what does each kind of evidence buy?** Whole
groups removed one at a time, same folds, same estimator, on the tier target:

| Group removed | Macro-F1 without it | Given up |
| --- | --- | --- |
| *(nothing — full contract)* | 0.5961 | — |
| OSM tagging (7 categoricals) | 0.5080 | **+0.0881** |
| terrain / elevation (6) | 0.5857 | +0.0104 |
| route shape (2) | 0.5914 | +0.0047 |
| route geometry (7) | 0.5964 | −0.0003 |
| measured `incline`, `width` (4) | 0.5957 | +0.0004 |

Tagging carries most of it, which is the direct answer to whether this model
is predicting physical difficulty or recovering mapping habits: **it is largely
the latter**, and the interface says "estimate" rather than "measured" for that
reason. The route-shape group earns a consistent +0.0047 — small, but measured
across paired folds rather than asserted, and it is the only feature family
that reads the route's own twistiness instead of its tags.

#### Route-shape features, and why they were added

`mean_turn` (average bearing change per vertex) and `verts_per_km` (digitised
vertices per kilometre) are computed from the route's own coordinates through
one shared function, `route_shape_features`, used identically by the dataset
build and the runtime. Length is measured from those same coordinates rather
than taken from a length column, so the two sides cannot disagree about the
denominator. Measured on validation before adoption: +0.0048 macro-F1, winning
4 of 5 paired folds at two regularisation strengths, with `mean_turn` alone
worth +0.0042. A broader set (sinuosity, switchback density) added nothing
further and was not adopted. A test proves dataset values and serving values
agree to 9 decimal places on real rows.

#### Terrain source parity, measured rather than asserted

Training reads a static Copernicus GLO-90 DEM; serving reads a live Open-Meteo
profile. The *derivation* is identical, so the columns mean the same quantity,
but the DEMs are not the same data. 100 real training ways that already carry a
GLO-90 profile were re-sampled from Open-Meteo and run through the identical
function:

| Quantity | GLO-90 ↔ Open-Meteo correlation |
| --- | --- |
| Elevation range | 0.9786 |
| Elevation gain | 0.9461 |
| **Average slope** | **0.5316** |
| **Max slope** | **0.4748** |

Aggregate elevation transfers closely. **Slopes transfer poorly**, which is
expected: slope is a derivative and is the quantity most sensitive to DEM
resolution and sampling. (The measurement used 5 sample points per way where
training used about 9, so these are a lower bound on true agreement.)

**This is not the same as the ablation figure, and the two must not be
conflated.** The +0.0040 ablation is the total value of the terrain group. The
*score impact* of the source mismatch was not isolated and is **not quoted as a
number** here. What is measured is the numeric disagreement above. The features
are retained because removing the terrain group lowers grouped-CV macro-F1, and
grouped CV — not the test set — is the selection signal.

All three measurements run on the training split. The tagging-lookup baseline is
scored on the held-out groups but participates in no selection. No prediction is
adjusted after the fact, and the final test set is read once.

**A learning curve was also measured, and it is deliberately not the headline.**
Grouped-CV macro-F1 is flat over the last quarter of the data (0.3434 at 75% of
groups, 0.3432 at 100%, on the seven-grade target), which *suggests* more rows
of the same kind would not help. That is a statement about diminishing returns
in one experiment, not proof that the label is the ceiling, and the two are
kept distinct here. It is not a claim the shipped model rests on.

### The held-out test

One fifth of the merged geographic groups is removed whole as the test set
before anything is fitted. **13,258 training rows, 3,313 test rows, group
overlap 0** across 1,490 merged groups. Everything — the tier grouping, the
estimator, and the prior-correction strength — was chosen by grouped
cross-validation **inside the training split**, scored under the real
OpenStreetMap grade distribution. The test set is read once, afterwards.

**Scored under the real grade distribution**, which is the mix a user actually
meets rather than the designed balance of the retained sample:

| Metric | Model | Majority-class baseline |
| --- | --- | --- |
| Accuracy | **0.7406** | 0.5708 |
| Macro-F1 | **0.5779** | 0.2423 |
| Balanced accuracy | **0.5743** | 0.3333 |

Per tier:

| Tier | Precision | Recall | F1 | Held-out rows | Natural share |
| --- | --- | --- | --- | --- | --- |
| walking | 0.812 | 0.820 | 0.816 | 1,000 | 57.6% |
| mountain | 0.677 | 0.675 | 0.676 | 999 | 39.5% |
| alpine | 0.257 | 0.228 | 0.242 | 1,314 | 2.9% |

Confusion matrix (rows true, columns predicted):

| | walking | mountain | alpine |
| --- | --- | --- | --- |
| **walking** | 820 | 177 | 3 |
| **mountain** | 265 | 674 | 60 |
| **alpine** | 150 | 864 | 300 |

**The usefulness gate: beats the majority baseline on all three metrics.** This
is asserted in the artifact, re-checked by a test, and published on the
readiness endpoint, so it cannot silently stop being true.

**Independent natural sample.** 520 real OSM ways drawn by uniform random hash
from 26 held-out one-degree cells across 6 regions, with no per-class control,
zero overlap with training rows, and never used for fitting or selection:
accuracy **0.7019**, macro-F1 **0.4563**. It corroborates the held-out result
from a genuinely separate sample. It is regional and small, so it is a
corroborating number and not a global performance claim.

**Spatial robustness.** Restricting the test to rows at least 5 km from any
training row leaves 3,185 of 3,313 and moves accuracy to 0.7458, so proximity
is not doing the work. This is one robustness check, not proof that all
route-family leakage is eliminated.

**Why the training weights are not neutral.** The retained dataset is capped
per recorded grade, so its distribution is designed. Weighting each row by
(natural share / retained share) raised accuracy from 0.6145 to 0.7430 on
validation. The strength is a validation choice, not a constant, and an
unweighted fit is one of the compared candidates.

**What the tier model is worth.** It is genuinely better than the seven-grade
model on the metric that matters: 0.7406 against 0.5708 rather than 0.3460
against 0.5689. The **alpine tier remains weak** (F1 0.242) — it is 2.9% of real
trails, so it is hard to learn and it is the tier where being wrong matters
most. The interface treats an alpine estimate as a prompt to check the route,
and that is stated on the difficulty card itself.

### What was rejected, and why

Each of these was implemented and measured, not dismissed:

| Approach | Result |
| --- | --- |
| Seven-grade `sac_scale` classification | 0.3460 vs 0.5689 baseline. Below baseline. **Abandoned.** |
| Route-relation-level target | 2,226 rows worldwide, **3** in the hardest tier. Cannot train. |
| Prior-corrected to full strength (alpha=1) | Accuracy 0.7453, but alpine recall 0.0379 — stops being a difficulty model |
| Route-shape features beyond twistiness (sinuosity, switchbacks) | +0.0000 CV macro-F1. Not adopted; `mean_turn` + `verts_per_km` were. |
| Collapsing to 4 or 5 tiers | 4-tier scores 0.7384 with macro-F1 0.4385, worse discrimination than 3 |

Collapsing the seven grades arbitrarily, duplicating rows, oversampling the
test set, tuning against the test set, or shifting predictions after inference
would all raise a headline. Every one was rejected. The result is reported as
measured, per class, with the baseline beside it.

### Honest limitations

- **Route answers are member votes, not route measurements.** A route relation
  or connected component is answered by scoring each verified member way with
  the same way-level artifact — own tags, own length, own shape — and taking
  the majority tier. Route totals are never fed in (a 12 km relation length
  would push almost every route to the hardest tier as a unit artefact).
  Members carry no per-member sampled terrain, which the model reads as
  missing exactly as its training rows without terrain did; the response
  states this. The full tier split, the agreement share and a disagree flag
  travel with the answer, and a recorded grade still overrules the vote.
  Fewer than two scored members means no answer — except the single-member
  route, whose one verified section constitutes the entire mapped geometry
  and is reported as such rather than refused. One scored section of many
  is still refused: a single section cannot speak for the rest.
- **The alpine tier is the weak part** (F1 0.242, recall 0.228). It is 2.9% of
  real graded ways, so it is the hardest tier to learn and the one where being
  wrong matters most. The interface treats an alpine estimate as a prompt to
  check the route, and says so on the card.
- **A third of rows have no terrain**, so those predictions rest on geometry and
  tagging alone. `terrain_available` is a feature precisely so the model can
  learn to distrust them.
- **The class balance is designed**, not natural, and cannot be replaced: the
  retained sample is capped per grade, so the natural distribution cannot be
  trained on. The report states the natural outcome rather than optimising for
  it.
- **The natural-prevalence sample is regional** — 6 regions — because the public
  endpoint times out on a query that must touch every graded way worldwide.
- **Labels are volunteer mapping**, uneven and skewed toward well-surveyed
  regions, so neighbouring grades overlap heavily.
- **Elevation source differs between training and serving** (GLO-90 against
  Open-Meteo). The derivation is identical; the numbers are not expected to be.
- **Relation membership is not exposed** by the data source, so route-family
  grouping can only use physical connectivity.
- The reported confidence is a raw maximum softmax probability and is
  **uncalibrated**, which is why it is labelled that way in the interface.
- `route_complexity.py` remains the interpretable route-demand profile. It is
  measured geometry, not a trained model, and it is never presented as
  difficulty.

### The artifact

| | |
| --- | --- |
| Path | `data/difficulty/trail_difficulty_model.joblib` (20 KB) |
| Model | `logistic_regression_difficulty_tier` |
| Task | classification of the difficulty tier implied by the recorded grade |
| Classes | `walking`, `mountain`, `alpine` |
| Feature contract | `difficulty_tier_v1`, 26 features |
| Observation unit | `osm_way` |
| Trained rows | 13,258 |
| Dataset checksum | SHA-256 prefix `4645a90ba6cab3c2` |
| Loaded by | `app/services/difficulty.py` |
| Reported by | `/api/trails/difficulty/readiness`, the intelligence endpoint, and the UI |

The artifact carries the held-out metrics, the majority baseline, the
per-class breakdown, the independent natural sample, the label mapping and the
limitations. A client cannot present a difficulty without the number bounding
it, and the loader **refuses** an artifact whose class list or feature contract
does not match the runtime — a model saved against the seven-grade taxonomy is
rejected rather than decoded into the wrong vocabulary.

Rebuild everything:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m app.ml.dataset   # rebuild the dataset
PYTHONPATH=backend backend/.venv/bin/python -m app.ml.train    # train and evaluate
```

`app/ml/train.py` is the single reproducible path: dataset
(`difficulty_dataset.py`, `feature_engineering.py`) → features
(`feature_contract.py`) → split (`geographic_groups.py`) → training → one
untouched test read → artifact + report. Nothing is fitted anywhere else.

Recorded environment: Python 3.14.3, scikit-learn 1.9.0, XGBoost 3.4.1, NumPy
2.5.2, pandas 3.0.6, joblib 1.5.3. The runtime only needs the versions that can
unpickle the estimator.

There is exactly one model, one report and one prediction path. The
seven-grade model, its report and its tests were removed rather than left
alongside as a competing "final" answer.

## Difficulty honesty

Two concepts, deliberately kept apart, and never merged into one number:

- **Official difficulty** — the grade OpenStreetMap actually records. When it
  exists it is the answer, full stop.
- **Model-estimated difficulty** — the learned estimate, shown only when no
  grade is recorded, and always labelled as an estimate.

The interface shows one difficulty value at a time. If the estimate disagrees
with the recorded grade, the estimate is marked `superseded_by_official_scale`
and hidden rather than displayed as a contradiction.

Both signals speak in the **same vocabulary** — the three difficulty tiers — so
an official value and an estimate are directly comparable and the recorded
`sac_scale` is never printed as a difficulty word. `hiking` is a route
classification, not a difficulty, so it is rendered as "Walkable trail". The
mapping is defined once in `app/ml/feature_contract.py` and used by training,
serving and the interface alike, so the three cannot drift apart.

Wherever the estimate appears it carries its own qualification. The estimate
card states, in one line, that on regions the model never saw it matched the
recorded tier about **75%** of the time against **57%** for always answering
with the most common tier, and that it is an estimate to check rather than a
measurement. The technical disclosure gives the per-tier precision and recall,
including the weak alpine figure. The readiness endpoint publishes the same
numbers, so no client can read the tier without them.

`route_complexity.py` is separate again: a weighted, monotonic saturation of
measured distance, ascent, steepest sampled slope, relief and component count.
It measures how much a route asks of you. It is not trained, and it is never
labelled a difficulty grade.

## Preparation (gear)

Preparation is prioritised, not dumped as a universal checklist.

- Every item is justified by a measured quantity (route length, sampled
  ascent, steepest section, recorded surface) or by an actual weather
  observation.
- Items are tiered **Essential / Recommended / Only if relevant**.
- With no live weather, no rain, cold, snow or wind item is produced at all.
- Two items serving the same preparation need are merged.
- The number of items scales with the route: a short flat paved walk
  typically yields two; an alpine route in snow yields more.

## Products

Product search follows the preparation priority, in that order, and skips
preparation needs that cannot be bought (offline access, first-aid
principles).

Each query is the product noun, the activity, and an explicit purchase intent.
Preparation labels are written for a person, so they carry caveats
("Headlamp, if any of the route is walked after dark") and conditions
("Broken-in trail shoes"); both are stripped, because a search engine given
that phrasing returns care guides rather than listings. The generic category
word is not added — it tells a shopping engine nothing the product noun has
not already said.

A result is shown as a **product** only when the search returned a genuine
product-page URL. That is decided from the destination's structure — a product
path segment, an explicit product identifier, or a marketplace listing path
with a numeric id (`/itm/<id>`, `/listing/<id>`, both with an optional slug
segment) — never from the page title, because retailer SEO routinely contains
"Best Price" and "Buy Online" and filtering on those words threw away real
products. The numeric-id requirement is what keeps the marketplace shapes
safe: editorial content does not address itself with a bare product id.
Anything that is not a product page is listed separately under **Web results,
not product pages**, and anything in an unrelated category is dropped with a
stated reason.

A non-product result must additionally be **about the item**. Activity and
condition words are what make a general web search drift: a live search for
trail shoes in wet weather returned `weatherapi.com` landing pages titled
"Weather in current location" and "Weather in global", which share the word
"weather" with the query and nothing else with the product. A result that
mentions **none** of the item's own words — after the gear qualifier has been
stripped, so "Broken-in trail shoes" is matched on `trail`/`shoes` and not on
`broken` — is moved out of the web results with the reason *"not about this
preparation item"*. The test is deliberately permissive: a result sharing even
one real term is kept, and a genuine product page is exempt entirely, because a
real listing is often titled with nothing but a brand and a colour. Against the
captured live results this drops 3 of 8 rows and keeps all 5 genuinely relevant
ones.

Every displayed item has a usable link. A product card links directly to the
product page. When no product page was found, the item gets one honest search
link for that exact item, so a recommendation is never shown without a way to
act on it. When the search provider itself failed, the section says
`unavailable` and says the links are plain search links — a provider outage is
never dressed up as a successful search.

Every external URL and image is put through one safety check before it leaves
the backend: absolute `http`/`https` only, a real host, no embedded
credentials, no control characters, bounded length. A `javascript:`, `data:`
or `file:` URL from a search provider is dropped and logged, not rendered.

Images are only displayed when the provider attached that image to *that
specific result*. A top-level image list is deliberately ignored, because its
ordering relative to the results is not guaranteed and can pair a product with
a different seller's photograph — a wrong product photo is worse than no photo.
Cards are requested with `referrerPolicy="no-referrer"` because retailer CDNs
commonly refuse hotlinked images, and a card whose image still fails to load
shows an honest "No image available" state rather than a broken image. A live
product page with no per-result image therefore renders the placeholder, and
that is the intended outcome rather than a defect.

No price, rating, review count, seller, stock state or specification is ever
displayed, because the source does not return them and they are not invented.

## Assistant

The assistant performs a real retrieval step over a corpus built from the
selected trail's own verified data: identity, geometry provenance and hash,
route measurements, terrain metrics, official difficulty, condition factors
with their sources, suitability factors, preparation items with their
reasons and evidence, product-result context, and a consolidated list of what
is genuinely missing.

Retrieval is local, deterministic and free: topic routing plus token overlap
plus per-passage boosts, with a domain-intent guard. It uses no embedding
model, no vector store and no per-question web search.

A question with no trail-domain intent, or with no matching evidence, returns
`not_in_context` and says so. The assistant does not expose retrieval topics,
token matching or internal state names.

Answers lead with what was asked. A gear question answers with the essential
items rather than dumping the whole corpus, a rainfall question answers with
the recorded figure rather than adding difficulty and gear, and evidence is
woven into the sentence instead of printed as a citation after every line. The
internal provenance vocabulary (`retrieved evidence`, `Evidence used:`,
`derived from route and condition evidence`) is filtered out of anything shown
to a person, and which engine wrote the prose is not displayed — a Gemini
outage and a local answer look identical to the user by design, while
`grounded` and `generated_by` remain in the API contract.

Retrieved text is treated as **untrusted data**, never as instructions. Part of
the corpus comes from an external web search, and a web page can contain text
that tries to give the model new rules. The prompt states that the evidence
block is data, and a line that looks like an instruction is to be ignored
rather than followed.

Selecting a different trail rebuilds the corpus, so assistant context always
follows the current selection. The assistant is only invoked when the user asks
a question; it is never called on page load.

## API surface

- `GET /api/search?q=` — cached place resolution with administrative metadata
- `GET /api/osm/trails/discover` — stage 1, verified `MAP_READY` results only
- `GET /api/osm/trails/enrichment` — stage 2 superset including honest `UNMAPPED`
- `POST /api/osm/trails/analysis` — component-aware selected geometry analysis
- `POST /api/trails/intelligence` — measurements, terrain, weather, condition, demands, suitability, preparation
- `GET /api/trails/difficulty/readiness` — model readiness and evaluation metadata
- `POST /api/trails/products` — gear-derived product search
- `POST /api/trails/assistant` — grounded, retrieval-backed answer
- `GET /health`, `GET /api/health` — health and provider configuration

Both discovery endpoints accept `place_kind` (the geocoder's own
classification) and `page` / `page_size`.

## Validation

```bash
# offline suite - makes zero outbound calls, enforced
cd backend && .venv/bin/python tests/offline_guard.py

# the suite itself
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests

# frontend
cd frontend && npm run lint && npx tsc --noEmit && npm run build
```

`backend/tests/offline_guard.py` replaces every outbound socket with a counter
and fails if the suite tries to reach the network. Ordinary tests must not
call Nominatim, Postpass, Tavily, Gemini or Open-Meteo.

| Test file | What it protects |
| --- | --- |
| `test_difficulty.py` | Label mapping, artifact identity, the usefulness gate, leakage, the response contract |
| `test_api_contract.py` | Every endpoint's response shape, in-process, against a real app |
| `test_discovery.py`, `test_scope_and_evidence.py` | Discovery evidence, recall recovery, tiling, identity, route completeness |
| `test_environment.py` | Weather/elevation provider contracts, disconnected geometry |
| `test_intelligence.py` | Condition, suitability, preparation, difficulty reconciliation |
| `test_enrichment.py` | Product labelling and images, assistant retrieval and grounding |
| `test_resilience.py` | Caching, in-flight dedupe, provider failure, bounded retries, Postpass retry policy |

`backend/tests/benchmark_discovery.py` is separate from the test suite: it
measures discovery coverage over eight geographically diverse real areas
(Meesapulimala, Chamonix, Torres del Paine, Zermatt, Wayanad, Kilauea, Mount
Fuji, Ben Nevis) by running the real HTTP route. It replays committed captures
offline by default and queries providers only with `--live`. It reports
candidates found, accepted, mapped, unmapped and the rejection reasons per area.
Meesapulimala is a regression case reached through the ordinary global discovery
path — there is no Kerala-specific code anywhere in the product.

## Attribution

Route identity and geometry, and the difficulty labels: © OpenStreetMap
contributors, ODbL, via the Postpass public SQL endpoint. Place resolution:
Nominatim. Elevation and weather: Open-Meteo. Training elevation: Copernicus DEM
GLO-90 (European Space Agency / Copernicus Programme), via its public AWS
Cloud-Optimized GeoTIFF bucket. Base map imagery and terrain: Cesium. Product
results: external web search, returning real third-party pages that this project
does not own or control.

The OpenStreetMap credit is not documentation-only: it is rendered on the
landing page footer and permanently on the map itself, linking to the
copyright page, because that is a condition of using the tiles and data. The
Cesium credit container is hidden and replaced by this project's own, so the
required attribution stays visible instead of disappearing behind a hover.

## Known limitations

- OSM coverage and tagging vary by region. A real trail may remain
  `UNMAPPED`, and the application does not manufacture a map identity to hide
  that.
- Condition is an inference from weather and route evidence. It is never an
  observation of the actual trail surface, and it never claims a route is
  safe.
- Where live weather is unavailable, condition is `unknown` and suitability is
  `insufficient data` rather than a guess.
- The learned difficulty estimate is **not shown when OpenStreetMap records a
  grade**, and is always labelled an estimate when it is shown. On
  geographically held-out data scored under the real grade distribution it
  reaches accuracy 0.7406, macro-F1 0.5779 and balanced accuracy 0.5743 against
  a 0.5708 / 0.2423 / 0.3333 majority baseline — above the baseline on all
  three. It is a way-level estimate, never a route judgement, and the per-tier
  numbers travel with every prediction so a weak tier is visible rather than
  averaged away.
- **The alpine tier is the weak part** (F1 0.242, recall 0.228), because it is
  2.9% of real graded ways. The interface treats an alpine estimate as a prompt
  to check the route and says so on the card. See
  [Data Science](#data-science-what-was-built-evaluated-and-why) for the full
  tier analysis.
- **Why the score is not higher** is measured rather than asserted: 88.6% of
  training ways share identical OpenStreetMap tagging yet carry different
  recorded grades, and inside the largest such group the physical features
  reach only AUC 0.69 at separating alpine routes from easier ones. The
  recorded grade is not a function of the recorded tags. That is a property of
  volunteer mapping, and it bounds what any tag-reading model can do.
- Route relations and connected components deliberately get **no** model
  estimate, for the unit-mismatch reason given above. Their difficulty signal
  is the recorded grade, if any, plus the measured route-demand profile.
- A verified `MAP_READY` route is a verified *mapped section*. The distance
  figure is **always** labelled **"Length of the mapped section"**, on the card
  and under the elevation chart alike. This wording is deliberately not varied
  by object type: a multi-member route relation records how the mapped geometry
  is split, not whether the real trail is fully drawn, and a relation holding
  forty member ways is equally consistent with a partially mapped trail. An
  amber disclosure appears only where the source itself establishes partiality
  (a single-member relation, or one inside a named network). The application
  never extends, stitches or invents the missing parts, so it has no basis for a
  total and does not print one.
- Cesium satellite imagery and terrain require a configured Cesium token; the
  application reports the capability when it is missing.
- Peak association is a measured proximity relationship, not a verified
  hiking-route classification by any official authority.
- **Postpass is the primary geometry authority, Overpass is the fallback.**
  Postpass answered HTTP 503 for multiple consecutive sessions, verified from
  a bare `curl` outside the application, and during that outage the product
  mapped nothing at all. That is a demonstrated availability problem, not a
  cosmetic one, so a fallback was added — narrowly. `backend/app/services/
  overpass.py` speaks Overpass QL and returns the same dataclasses with the
  same validation gates; it runs **only when a Postpass query raises**, never
  to second-guess a successful answer (even an empty one). Relation geometry
  is assembled as one MultiLineString component per member way, in member
  order, never stitched; member types are normalised at the boundary
  (`way`→`W`) so every downstream identity check keeps working. Relations
  without member geometry are dropped by the shared usability gate and
  surface as UNMAPPED. Provenance names the serving source per row
  (`postpass_way` vs `overpass_way`, `postpass_relation_rendered` vs
  `overpass_relation_members`), provider blocks report `source: postpass |
  overpass | mixed | none`, and HTTP 429 gets exactly one polite retry —
  anything else fails as provider failure with UNAVAILABLE preserved.
- **Postpass availability is still honestly reported.** A provider that
  **fails on both sources** is never reported as an empty area: status is
  `unavailable`, `coverage.provider_failed` is `true` and
  `coverage_complete` is `false`.
- Route-level supervised difficulty is not built. The provider census behind that
  decision is recorded above: 2,226 relation-level rows globally, 3 in the
  hardest tier.
- Discovery recall is not measured. A precision audit over every rejected
  candidate is published above, and one specific recall gap was found and
  closed with an evidence rule that admits named local trails while keeping
  facility and road noise out. No recall figure is claimed, because none was
  measured against a reference set.
- The natural-prevalence sample is regional: 6 regions, 26 one-degree cells, no
  per-class control. It is a real independent check on real rows, not evidence
  of global OpenStreetMap performance, and it is not described as such.
- No connected browser was available in this environment, so the interface was
  verified by lint, TypeScript, production build, code and layout analysis, and
  the full offline contract suite — **not by eye in a browser**. A build passing
  is not evidence that a chart is unclipped or that two labels do not collide.
