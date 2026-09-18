from fastapi import APIRouter, HTTPException

from app.services.elevation import get_elevation_profile


router = APIRouter(
    prefix="/api/elevation",
    tags=["Elevation"],
)


@router.post("")
async def elevation(
    payload: dict,
):
    geometry = payload.get("geometry")

    if not geometry:
        raise HTTPException(
            status_code=400,
            detail="geometry is required",
        )

    coordinates = geometry.get(
        "coordinates"
    )

    if not coordinates:
        raise HTTPException(
            status_code=400,
            detail="geometry.coordinates is required",
        )

    geometry_type = geometry.get(
        "type"
    )

    if geometry_type not in {
        "LineString",
        "MultiLineString",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "geometry.type must be "
                "'LineString' or 'MultiLineString'"
            ),
        )

    if geometry_type == "LineString":

        trail_coordinates = coordinates

    else:

        trail_coordinates = [
            coordinate
            for segment in coordinates
            for coordinate in segment
        ]

    return await get_elevation_profile(
        trail_coordinates
    )