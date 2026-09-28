from fastapi import APIRouter, HTTPException

from app.services.elevation import get_elevation_profile


router = APIRouter(
    prefix="/api/elevation",
    tags=["Elevation"],
)


@router.post("")
async def elevation(payload: dict):
    geometry = payload.get("geometry")
    if not isinstance(geometry, dict):
        raise HTTPException(
            status_code=422,
            detail="geometry is required",
        )
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type not in {"LineString", "MultiLineString"}:
        raise HTTPException(
            status_code=422,
            detail="geometry.type must be LineString or MultiLineString",
        )
    if not isinstance(coordinates, list) or not coordinates:
        raise HTTPException(
            status_code=422,
            detail="geometry.coordinates is required",
        )
    return await get_elevation_profile(geometry)
