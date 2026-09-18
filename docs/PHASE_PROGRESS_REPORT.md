# Phase Progress Report

## Phase 1 — Project foundation and environment

### Goal
Set up the technical foundation for a data-driven geographic trail project.

### Main work
- Defined the project concept and end-to-end direction.
- Set up the local development environment.
- Established the initial backend/frontend project structure.
- Began PostgreSQL and PostGIS setup.

### Important outcome
The project moved from concept planning into a working local development environment.

---

## Phase 2 — Database and backend foundation

### Goal
Create the spatial backend foundation.

### Technologies
- Python
- FastAPI
- PostgreSQL
- PostGIS
- SQLModel / SQLAlchemy
- Postman for API testing

### Main work
- Connected the Python backend to PostgreSQL.
- Enabled PostGIS for spatial functionality.
- Added initial trail-oriented data structures.
- Created API routes and tested backend responses.

### Why PostGIS
The project works with coordinates, trail geometry, distances and spatial relationships, so a spatial database is required.

---

## Phase 3 — Frontend foundation

### Goal
Create the user-facing explore experience.

### Technologies
- Next.js
- React
- TypeScript
- Tailwind CSS
- Cesium

### Main work
- Built the search-to-explore flow.
- Added the trail sidebar/list.
- Connected the frontend to the FastAPI backend.
- Added Cesium map visualization.
- Added selected-trail visualization.
- Added 3D terrain controls.

### User flow
```text
Search place
  ↓
Resolve location
  ↓
Discover trails
  ↓
Select trail
  ↓
Show trail on Cesium
```

---

## Phase 4 — Trail intelligence foundation

### Current focus
The current Phase 4 work is primarily the trail discovery and selected-trail data layer.

### Why this became the focus
The first trail results exposed a major data problem: OSM can represent one real trail using multiple ways and relations, while broad searches can also return small or unrelated path fragments.

### Main technical areas being developed
- OSM way handling
- OSM relation handling
- Relation member parsing
- Relation geometry assembly
- MultiLineString / LineString handling
- Component identity for connected trail sections
- Trail candidate filtering and ranking
- Selected-trail geometry contract
- Elevation integration
- Weather integration

### Current working baseline
The development baseline being kept for the current work is the 6,591-line `osm.py` version.

It is not considered final. It still has known trail-quality issues.

### Known issues
- Small trail/path fragments can still appear.
- Some connected sections can still be split.
- Some unrelated paths can still appear.
- Naming can be weak when OSM data has no useful name.
- Some selected trails may still have weather/elevation availability problems.

### Important principle
Do not move to final ML and recommendation claims until the selected trail and its geometry are reliable enough.

---

## Next implementation order

1. Improve trail discovery quality.
2. Improve relation/component identity and geometry handling.
3. Reduce irrelevant fragments and weak candidates.
4. Validate multiple locations.
5. Validate selected geometry directly with elevation and weather APIs.
6. Build feature engineering on validated trail data.
7. Build and evaluate difficulty ML.
8. Build condition-likelihood modelling.
9. Add general suitability logic.
10. Add gear recommendation and product discovery.
11. Add RAG/LLM explanation.
