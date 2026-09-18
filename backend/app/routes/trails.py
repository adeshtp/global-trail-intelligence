import json

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import engine


router = APIRouter(
    prefix="/api/trails",
    tags=["Trails"],
)


@router.get("")
def get_trails():
    query = text("""
        SELECT
            id,
            name,
            description,
            distance_km,
            elevation_gain_m,
            difficulty,
            ST_AsGeoJSON(location) AS location,
            ST_AsGeoJSON(route) AS route
        FROM trails
        ORDER BY id;
    """)

    with engine.connect() as connection:
        result = connection.execute(query)

        trails = []

        for row in result:
            trails.append({
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "distance_km": row.distance_km,
                "elevation_gain_m": row.elevation_gain_m,
                "difficulty": row.difficulty,
                "location": json.loads(row.location) if row.location else None,
                "route": json.loads(row.route) if row.route else None,
            })

        return trails