#!/usr/bin/env python
"""
Discovery coverage benchmark over geographically diverse real areas.

This measures what the product actually does in a place, rather than what a
unit test says it should do. It runs the real discovery service against real
provider data and reports, per area, what was found, what was accepted, what
was verified, and why candidates were rejected.

Two modes:

  --live     query the providers (costs quota; use sparingly)
  --fixture  replay captured responses, offline and deterministic (default)

Usage:

    PYTHONPATH=backend backend/.venv/bin/python backend/tests/benchmark_discovery.py --live

Captured responses live in ``backend/tests/fixtures/discovery/`` and are
committed, so the offline run needs no provider at all. Meesapulimala is
included as a regression case: it must be recovered through the ordinary
global discovery path, with no special-casing anywhere in the product.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures" / "discovery"

sys.path.insert(0, str(REPO_ROOT / "backend"))


# Areas spanning different trail environments and continents, so a pass here is
# evidence about the product rather than about one region.
BENCHMARK: list[dict[str, Any]] = [
    {
        "id": "meesapulimala",
        "label": "Meesapulimala, Kerala, India",
        "place": "Meesapulimala",
        "latitude": 10.0876,
        "longitude": 77.2044,
        "kind": "peak",
        "expect": "an alpine summit trail, recovered by the normal path",
    },
    {
        "id": "chamonix",
        "label": "Chamonix-Mont-Blanc, France",
        "place": "Chamonix-Mont-Blanc",
        "latitude": 45.9231,
        "longitude": 6.8700,
        "kind": "alpine_town",
        "expect": "high-alpine routes via relations and named ways",
    },
    {
        "id": "torres_del_paine",
        "label": "Torres del Paine, Chile",
        "place": "Torres del Paine",
        "latitude": -51.0827,
        "longitude": -72.9254,
        "kind": "national_park",
        "expect": "southern-hemisphere Patagonian trails",
    },
    {
        "id": "zermatt",
        "label": "Zermatt, Switzerland",
        "place": "Zermatt",
        "latitude": 46.0212,
        "longitude": 7.7493,
        "kind": "alpine_town",
        "expect": "graded alpine network trails",
    },
    {
        "id": "wayanad",
        "label": "Wayanad, Kerala, India",
        "place": "Wayanad",
        "latitude": 11.7151291,
        "longitude": 76.1271185,
        "kind": "district",
        "expect": "forest and plantation trails",
    },
    {
        "id": "kilauea",
        "label": "Kilauea, Hawaii, USA",
        "place": "Kilauea",
        "latitude": 22.2086,
        "longitude": -159.4067,
        "kind": "volcanic",
        "expect": "volcanic ridge trails, oceanic tagging conventions",
    },
    {
        "id": "mount_fuji",
        "label": "Mount Fuji, Japan",
        "place": "Mount Fuji",
        "latitude": 35.3606,
        "longitude": 138.7274,
        "kind": "peak",
        "expect": "a heavily-mapped summit route",
    },
    {
        "id": "ben_nevis",
        "label": "Ben Nevis, Scotland",
        "place": "Ben Nevis",
        "latitude": 56.7969,
        "longitude": -5.0036,
        "kind": "peak",
        "expect": "a long-established UK summit route",
    },
]


async def _run_live(area: dict[str, Any]) -> dict[str, Any]:
    """
    Discovery against the real providers for one area.

    Called through the real HTTP route rather than the service function, so
    this measures what a user actually gets: same validation, same tiling,
    same coverage accounting, same response shape.
    """
    import httpx

    from app.core.config import settings

    base = str(getattr(settings, "PUBLIC_API_URL", "")).strip() or (
        "http://127.0.0.1:8001"
    )
    params = {
        "latitude": area["latitude"],
        "longitude": area["longitude"],
        "search_query": area["place"],
        "location_name": area["place"],
        "scope": "local",
        "place_kind": area["kind"],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        response = await client.get(
            f"{base}/api/osm/trails/discover", params=params
        )
    response.raise_for_status()
    return response.json()


def _run_fixture(area: dict[str, Any]) -> dict[str, Any]:
    """Replay the captured discovery response for this area."""
    path = FIXTURES / f"{area['id']}.json"
    if not path.is_file():
        return {
            "status": "no_capture",
            "trails": [],
            "result_counts": {},
            "coverage": {},
        }
    return json.loads(path.read_text())


def _summarise(area: dict[str, Any], result: dict[str, Any]) -> dict:
    counts = result.get("result_counts") or {}
    coverage = result.get("coverage") or {}
    trails = result.get("trails") or []
    mapped = [t for t in trails if t.get("map_ready")]
    return {
        "id": area["id"],
        "label": area["label"],
        "kind": area["kind"],
        "expect": area["expect"],
        "status": result.get("status"),
        "candidates_found": coverage.get("candidates_found"),
        "accepted": counts.get("relevance_accepted"),
        "mapped": counts.get("mapped"),
        "unmapped": counts.get("unmapped"),
        "weak_evidence": counts.get("weak_evidence"),
        "coverage_complete": coverage.get("coverage_complete"),
        "provider_returned_no_rows": coverage.get(
            "provider_returned_no_rows"
        ),
        "tiles_queried": coverage.get("tiles_queried"),
        "tiles_failed": coverage.get("tiles_failed"),
        "returned": len(trails),
        "osm_types": sorted(
            {str(t.get("osm_type")) for t in trails if t.get("osm_type")}
        ),
        "named": sum(1 for t in trails if t.get("name")),
        "real_ids": sum(
            1 for t in trails if t.get("osm_type") and t.get("osm_id")
        ),
        "provenances": sorted(
            {
                str(t.get("geometry_provenance"))
                for t in trails
                if t.get("geometry_provenance")
            }
        ),
    }


def _print_table(rows: list[dict]) -> None:
    header = (
        f"{'area':22s} {'found':>6s} {'acc':>4s} {'map':>4s} "
        f"{'unmap':>5s} {'weak':>4s} {'named':>5s} {'ids':>4s}  status"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['id']:22s} "
            f"{str(row['candidates_found']):>6s} "
            f"{str(row['accepted']):>4s} "
            f"{str(row['mapped']):>4s} "
            f"{str(row['unmapped']):>5s} "
            f"{str(row['weak_evidence']):>4s} "
            f"{str(row['named']):>5s} "
            f"{str(row['real_ids']):>4s}  {row['status']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="query providers (costs quota) instead of replaying captures",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="with --live, save the responses as fixtures for later replay",
    )
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated benchmark ids to run",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full summary as JSON",
    )
    args = parser.parse_args()

    wanted = {
        value.strip() for value in args.only.split(",") if value.strip()
    }
    areas = [
        area for area in BENCHMARK if not wanted or area["id"] in wanted
    ]

    rows: list[dict] = []
    for area in areas:
        if args.live:
            result = asyncio.run(_run_live(area))
            if args.capture:
                FIXTURES.mkdir(parents=True, exist_ok=True)
                (FIXTURES / f"{area['id']}.json").write_text(
                    json.dumps(result, indent=2, sort_keys=True) + "\n"
                )
        else:
            result = _run_fixture(area)
        row = _summarise(area, result)
        rows.append(row)
        if not args.json:
            print(f"\n{row['label']}  ({row['kind']})")
            print(f"  expecting: {row['expect']}")
            print(
                f"  found {row['candidates_found']}, "
                f"accepted {row['accepted']}, mapped {row['mapped']}, "
                f"unmapped {row['unmapped']}, weak {row['weak_evidence']}"
            )
            print(
                f"  returned {row['returned']} "
                f"({row['named']} named, {row['real_ids']} with real OSM ids), "
                f"types {row['osm_types'] or '-'}"
            )
            print(f"  status: {row['status']}")

    if not args.json:
        print("\n" + "=" * 72)
        _print_table(rows)
        captured = [r for r in rows if r["status"] != "no_capture"]
        with_data = [r for r in captured if (r["mapped"] or 0) > 0]
        print()
        print(f"  areas run                     : {len(rows)}")
        print(f"  areas with a capture          : {len(captured)}")
        print(f"  areas with verified geometry  : {len(with_data)}")
        if captured:
            total = sum(r["candidates_found"] or 0 for r in captured)
            accepted = sum(r["accepted"] or 0 for r in captured)
            mapped = sum(r["mapped"] or 0 for r in captured)
            print(f"  real candidates examined      : {total}")
            print(
                f"  accepted for relevance        : {accepted}"
                f" ({accepted / total:.1%})" if total else ""
            )
            print(
                f"  verified with real geometry   : {mapped}"
                f" ({mapped / total:.1%})" if total else ""
            )
        kinds = sorted({r["kind"] for r in rows})
        print(f"  distinct trail environments   : {len(kinds)} {kinds}")
    else:
        print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
