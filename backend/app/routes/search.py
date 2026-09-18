import httpx

from fastapi import APIRouter, HTTPException, Query


router = APIRouter(
    prefix="/api/search",
    tags=["Search"],
)


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {
    "User-Agent": "OutdoorIntelligenceProject/0.1 (educational project)"
}


@router.get("")
async def search_location(
    q: str = Query(..., min_length=2)
):
    params = {
        "q": q,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
    }

    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            headers=HEADERS,
        ) as client:
            response = await client.get(
                NOMINATIM_URL,
                params=params,
            )

        response.raise_for_status()

    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail=f"Location search failed: {error}",
        )

    results = response.json()

    return {
        "query": q,
        "results": [
            {
                "display_name": result.get("display_name"),
                "latitude": float(result["lat"]),
                "longitude": float(result["lon"]),
                "osm_type": result.get("osm_type"),
                "osm_id": result.get("osm_id"),
                "type": result.get("type"),
                "address": result.get("address"),
            }
            for result in results
        ],
    }