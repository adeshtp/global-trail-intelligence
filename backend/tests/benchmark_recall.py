#!/usr/bin/env python
"""
Live recall benchmark: how much of what Postpass holds does discovery return?

For each area this runs the real discovery API, then asks Postpass directly
(ids only, no geometry) what it holds in the very box discovery reported
searching:

  relations  named route=hiking relations with a rendered line that really
             crosses the box (not merely a bounding box that overlaps it: the
             California Coastal Trail's box overlaps Yosemite's, its line is
             230 km away)
  ways       named path-like ways carrying sac_scale or trail_visibility

and reports how many the API returned, alongside the truncation and coverage
fields, so a drop caused by a cap or the time budget is visible and is not
mistaken for a filter.

A way counts as found when it is returned, when a returned route lists it, or
when it lies on a returned route's line (within about 5 m): discovery shows one
card for a trail, not one per way that happens to be part of it.

What this is NOT: human ground truth. It measures recall against what the
Postpass mirror holds, in the areas listed. It cannot see trails OpenStreetMap
holds but Postpass does not render (very long routes are the known case), and
it says nothing about precision. It is a regression check on retrieval.

This is a LIVE script. It is never part of the offline unittest suite (it is
named benchmark_*, not test_*) and it is paced: one reference query per second.

Usage:

    PYTHONPATH=backend backend/.venv/bin/python backend/tests/benchmark_recall.py
    PYTHONPATH=backend backend/.venv/bin/python backend/tests/benchmark_recall.py chamonix wayanad

The API must be running (default http://127.0.0.1:8001, or PUBLIC_API_URL).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

API = os.getenv("PUBLIC_API_URL", "http://127.0.0.1:8001").rstrip("/")
PACE_SECONDS = 1.0


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    latitude: float
    longitude: float
    place_kind: str = "town"
    scope: str = "local"
    bbox: str | None = None  # "west,south,east,north" for a broad-area search


AREAS: list[Area] = [
    Area("chamonix", "Chamonix, France (dense alpine)", 45.9231, 6.87),
    Area("yosemite", "Yosemite Valley, USA (trails mostly ways)", 37.7456, -119.5936),
    Area("fuji", "Mount Fuji, Japan (non-Latin names)", 35.3606, 138.7274, "peak"),
    Area("wayanad", "Wayanad, India (sparse, semantic-dependent)", 11.6854, 76.132),
    Area(
        "lakedistrict",
        "Lake District, UK (broad area)",
        54.45,
        -3.0,
        "national_park",
        "area",
        "-3.35,54.25,-2.65,54.65",
    ),
]


def _discover(area: Area) -> tuple[dict, list[dict], float]:
    """Run discovery and collect every page."""
    trails: list[dict] = []
    first: dict = {}
    started = time.time()
    for page in range(1, 200):
        params = {
            "latitude": area.latitude,
            "longitude": area.longitude,
            "search_query": area.label.split(",")[0],
            "location_name": area.label.split(",")[0],
            "scope": area.scope,
            "place_kind": area.place_kind,
            "page_size": 100,
            "page": page,
        }
        if area.bbox:
            params["bbox"] = area.bbox
        url = f"{API}/api/osm/trails/discover?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=900) as response:
            data = json.load(response)
        if page == 1:
            first = data
        trails.extend(data.get("trails") or [])
        if not (data.get("pagination") or {}).get("has_more"):
            break
    return first, trails, time.time() - started


async def _reference(box: list[float]) -> tuple[set[int], set[int]]:
    from app.services import postpass

    west, south, east, north = box
    envelope = f"ST_MakeEnvelope({west}, {south}, {east}, {north}, 4326)"
    relations_sql = f"""
SELECT r.id AS id
FROM planet_osm_rels AS r
JOIN postpass_line AS l ON l.osm_type = 'R' AND l.osm_id = r.id
WHERE r.tags->>'type' = 'route'
  AND r.tags->>'route' = 'hiking'
  AND (r.tags ? 'name' OR r.tags ? 'name:en' OR r.tags ? 'int_name'
       OR r.tags ? 'official_name' OR r.tags ? 'alt_name'
       OR r.tags ? 'loc_name' OR r.tags ? 'short_name')
  AND l.geom IS NOT NULL
  AND l.geom && {envelope}
  AND ST_Intersects(l.geom, {envelope})
"""
    ways_sql = f"""
SELECT l.osm_id AS id
FROM postpass_line AS l
WHERE l.osm_type = 'W'
  AND l.geom && {envelope}
  AND ST_Intersects(l.geom, {envelope})
  AND l.tags ? 'name'
  AND l.tags->>'highway' IN ('path', 'footway', 'track', 'steps', 'bridleway')
  AND (l.tags->>'sac_scale' IS NOT NULL OR l.tags->>'trail_visibility' IS NOT NULL)
"""
    relations = {int(row["id"]) for row in await postpass._execute_sql(relations_sql)}
    await asyncio.sleep(PACE_SECONDS)
    ways = {int(row["id"]) for row in await postpass._execute_sql(ways_sql)}
    await asyncio.sleep(PACE_SECONDS)
    return relations, ways


def _returned_ids(trails: list[dict]) -> tuple[set[int], set[int]]:
    relations: set[int] = set()
    ways: set[int] = set()
    for trail in trails:
        osm_id = trail.get("osm_id")
        if trail.get("osm_type") == "relation" and osm_id is not None:
            relations.add(int(osm_id))
        elif trail.get("osm_type") == "way" and osm_id is not None:
            ways.add(int(osm_id))
        for key in ("member_way_ids", "ordered_way_ids"):
            ways.update(int(i) for i in (trail.get(key) or []) if i is not None)
    return relations, ways


async def _covered_by_returned_lines(
    missing: set[int], trails: list[dict]
) -> tuple[set[int], dict[int, object]]:
    """Missing ways whose line lies on a returned trail's line."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    from app.services import postpass

    if not missing:
        return set(), {}
    geometries = [
        shape(t["geometry"]) for t in trails if t.get("geometry")
    ]
    tree = STRtree(geometries)
    ways = await postpass.get_ways(sorted(missing))
    covered: set[int] = set()
    for way_id, way in ways.items():
        if not way.geometry:
            continue
        line = shape(way.geometry)
        for index in tree.query(line.buffer(0.00005)):
            if line.distance(geometries[int(index)]) < 0.00005:
                covered.add(way_id)
                break
    return covered, ways


def _ratio(found: int, total: int) -> str:
    return f"{found}/{total} = {found / total:.0%}" if total else "n/a (none held)"


async def run(area: Area) -> None:
    print(f"\n== {area.label}")
    try:
        first, trails, elapsed = _discover(area)
    except Exception as exc:  # the API being down is the operator's to see
        print(f"   discovery failed: {exc}")
        return
    coverage = first.get("coverage") or {}
    box = coverage.get("area_considered")
    if not box:
        print("   no area reported; cannot build a reference")
        return
    ref_relations, ref_ways = await _reference(box)
    got_relations, got_ways = _returned_ids(trails)
    print(f"   discovery: {elapsed:.0f}s, {len(trails)} trails returned")
    print(
        "   coverage : tiles "
        f"{coverage.get('tiles_queried')}/{coverage.get('tiles_total')} "
        f"(skipped {coverage.get('tiles_skipped')}), split {coverage.get('tiles_split')}, "
        f"rows_truncated {coverage.get('rows_truncated')}, "
        f"complete {coverage.get('coverage_complete')}"
    )
    print(
        "   relations: "
        + _ratio(len(ref_relations & got_relations), len(ref_relations))
        + "   (named route=hiking with a rendered line)"
    )
    missing_ways = ref_ways - got_ways
    on_line, fetched = await _covered_by_returned_lines(missing_ways, trails)
    print(
        "   ways     : "
        + _ratio(len(ref_ways & got_ways) + len(on_line), len(ref_ways))
        + f"   (named path-like with sac_scale or trail_visibility; "
        f"{len(ref_ways & got_ways)} returned or listed by a returned route, "
        f"{len(on_line)} on a returned route's line)"
    )
    missing = sorted(ref_relations - got_relations)[:5]
    if missing:
        print(f"   missing relation ids (first 5): {missing}")
    gone = sorted(missing_ways - on_line)
    if gone:
        from app.routes import discovery

        print(f"   ways still missing ({len(gone)}):")
        for way_id in gone[:12]:
            way = fetched.get(way_id)
            if way is None:
                print(f"     {way_id}: not returned by Postpass lookup")
                continue
            _, _, reasons, _ = discovery._named_way_evidence(
                way, place=area.label.split(",")[0]
            )
            print(
                f"     {way_id} {way.name!r} {way.highway} "
                f"{way.length_km:g} km -> {reasons[0]}"
            )


def main() -> None:
    wanted = {arg.lower() for arg in sys.argv[1:]}
    areas = [a for a in AREAS if not wanted or a.key in wanted]
    if not areas:
        raise SystemExit(f"unknown area; choose from {[a.key for a in AREAS]}")
    for area in areas:
        asyncio.run(run(area))


if __name__ == "__main__":
    main()
