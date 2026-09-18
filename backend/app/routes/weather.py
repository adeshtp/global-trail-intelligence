from fastapi import APIRouter, Query

from app.services.weather import get_weather


router = APIRouter(
    prefix="/api/weather",
    tags=["Weather"],
)


@router.get("")
async def weather(
    latitude: float = Query(...),
    longitude: float = Query(...),
):
    return await get_weather(
        latitude=latitude,
        longitude=longitude,
    )