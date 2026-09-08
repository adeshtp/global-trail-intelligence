from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.init_db import init_db
from app.routes.trails import router as trails_router
from app.routes.osm import router as osm_router
from app.routes.search import router as search_router


app = FastAPI(
    title="TerraPath API",
    description="Data-driven trail intelligence platform",
    version="0.1.0",
)


# Allow the Next.js frontend to communicate
# with the FastAPI backend during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(trails_router)
app.include_router(osm_router)
app.include_router(search_router)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def root():
    return {
        "project": "TerraPath",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
    }