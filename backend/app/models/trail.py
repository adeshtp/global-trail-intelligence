from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class Trail(SQLModel, table=True):
    __tablename__ = "trails"

    id: Optional[int] = Field(default=None, primary_key=True)

    name: str
    description: Optional[str] = None

    distance_km: Optional[float] = None
    elevation_gain_m: Optional[float] = None

    difficulty: Optional[str] = None

    location: Optional[object] = Field(
        default=None,
        sa_column=Column(
            Geometry(geometry_type="POINT", srid=4326)
        ),
    )

    route: Optional[object] = Field(
        default=None,
        sa_column=Column(
            Geometry(geometry_type="LINESTRING", srid=4326)
        ),
    )