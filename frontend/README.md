# GoBeyond frontend

Next.js App Router client for the GoBeyond API. The project description,
architecture and setup live in the repository root `README.md`.

```bash
npm install
npm run dev     # http://localhost:3000
```

Point the client at a running backend with `NEXT_PUBLIC_API_BASE_URL`
(see `.env.example`). The default target is the local backend on port 8001.

## What lives here

| Path | Responsibility |
| --- | --- |
| `app/page.tsx` | Landing page and search entry point |
| `app/explore/page.tsx` | Search → discovery → selection → trail intelligence, including the elevation profile, conditions, difficulty, suitability, preparation, products and the floating assistant |
| `components/TrailSidebar.tsx` | Result counts and the trail list. Same-name objects are grouped for presentation only, and only when a real OSM name plus geographic proximity supports it; each object stays distinct and separately selectable |
| `components/CesiumMap.tsx` | 3D globe, real verified geometry, disconnected components, selected-trail highlight, terrain and imagery |
| `components/ExploreSearch.tsx` | Search input and query handling |
| `public/cesium/` | Vendored Cesium build and static assets, loaded at runtime |

## Validation

```bash
npm run lint
npx tsc --noEmit
npm run build
```
