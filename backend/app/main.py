from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routes.discovery import router as discovery_router
from app.routes.elevation import router as elevation_router
from app.routes.search import router as search_router
from app.routes.trails import router as trails_router
from app.routes.weather import router as weather_router
from app.services.elevation import OPEN_METEO_ELEVATION_URL
from app.services.weather import OPEN_METEO_URL


logger = logging.getLogger(__name__)


def _build_revision() -> str:
    """
    Short git revision of the running code, for staleness detection.

    When a developer runs a stale backend next to a current frontend — two
    servers on nearby ports serving different code — every endpoint still
    returns 200 and the mismatch is invisible. Publishing the revision lets
    anyone confirm which code actually answered. Falls back to "unknown"
    rather than failing when git metadata is absent.
    """
    import subprocess

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        )
        value = revision.stdout.strip()
        if revision.returncode == 0 and value:
            return value
    except Exception:
        pass
    return "unknown"


app = FastAPI(
    title="GoBeyond API",
    description="Real-data hiking and trekking trail intelligence",
    version="1.0.0",
)


def _cors_origins() -> list[str]:
    origins = [
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ]
    return origins or [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


_CORS = _cors_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS,
    # Credentials and a wildcard origin are mutually exclusive in CORS, so
    # credentials are only enabled when real origins are configured.
    allow_credentials="*" not in _CORS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(discovery_router)
app.include_router(trails_router)
app.include_router(search_router)
app.include_router(weather_router)
app.include_router(elevation_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "project": "GoBeyond",
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/api/health")
@app.get("/health", include_in_schema=False)
def health() -> dict[str, object]:
    postpass_url = (
        os.getenv("POSTPASS_URL")
        or settings.POSTPASS_URL
    ).strip()
    semantic_url = (
        os.getenv("SEARXNG_URL")
        or settings.SEARXNG_URL
    ).strip()

    from app.services import overpass as overpass_module

    return {
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "build": _build_revision(),
        "capabilities": {
            # What this backend can actually do, so a client or operator
            # can tell a stale server from a current one without guessing.
            "overpass_fallback": True,
            "route_aggregation": True,
            "overpass_url": overpass_module.OVERPASS_URL,
        },
        "database": {
            "status": "removed",
            "required_for_core_apis": False,
            # The optional PostGIS persistence layer was removed: no
            # endpoint ever read from or wrote to it, and the product
            # operates on provider-backed data with in-process caches.
            # The key stays so older clients reading it do not break.
            "used_by_request_path": False,
        },
        "providers": {
            "postpass": {
                "configured": bool(postpass_url),
                "availability": "not_checked",
                "required_for_core_apis": True,
            },
            "overpass_fallback": {
                "configured": bool(overpass_module.OVERPASS_URL),
                "availability": "not_checked",
                "required_for_core_apis": False,
                "role": (
                    "fallback for real OSM identity/geometry when "
                    "Postpass fails; never used to second-guess a "
                    "successful Postpass answer"
                ),
            },
            "semantic_search": {
                "configured": bool(
                    semantic_url
                    or settings.GEMINI_API_KEY
                    or settings.TAVILY_API_KEY
                ),
                "availability": "not_checked",
                "required_for_core_apis": False,
            },
            "weather": {
                "configured": bool(OPEN_METEO_URL),
                "availability": "not_checked",
                "required_for_core_apis": False,
            },
            "elevation": {
                "configured": bool(OPEN_METEO_ELEVATION_URL),
                "availability": "not_checked",
                "required_for_core_apis": False,
            },
        },
    }
