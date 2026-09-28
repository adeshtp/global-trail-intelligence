from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    CORS_ORIGINS: str = (
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    ENVIRONMENT: str = "development"

    CESIUM_ION_TOKEN: str = ""

    # Optional alias for GEMINI_API_KEY, used only when that value is empty.
    LLM_API_KEY: str = ""

    GEMINI_API_KEY: str = ""

    TAVILY_API_KEY: str = ""

    TRAIL_DISCOVERY_GEMINI_MODEL: str = "gemini-3.5-flash-lite"

    # The project's single canonical difficulty model. There is exactly one
    # artifact, one report and one runtime prediction path; see
    # `app/ml/train.py` and
    # `data/difficulty/trail_difficulty_model_report.json`.
    DIFFICULTY_MODEL_PATH: str = str(
        BASE_DIR
        / "data"
        / "difficulty"
        / "trail_difficulty_model.joblib"
    )

    POSTPASS_URL: str = (
        "https://postpass.geofabrik.de/api/interpreter"
    )

    SEARXNG_URL: str = "http://127.0.0.1:8080/search"
    POSTPASS_TIMEOUT_SECONDS: float = 30.0
    POSTPASS_CACHE_TTL_SECONDS: float = 300.0
    POSTPASS_RETRIES: int = 1
    # Overpass API fallback for real OSM identity/geometry when Postpass is
    # unavailable. Used only on primary failure, never to second-guess a
    # successful answer.
    OVERPASS_URL: str = "https://overpass-api.de/api/interpreter"
    OVERPASS_TIMEOUT_SECONDS: float = 200.0
    OVERPASS_CACHE_TTL_SECONDS: float = 300.0
    OVERPASS_CACHE_MAX_ENTRIES: int = 128
    OVERPASS_429_BACKOFF_SECONDS: float = 6.0
    SEARXNG_TIMEOUT_SECONDS: float = 12.0
    SEARXNG_SEARCH_QUERIES: int = 2
    AREA_RELATION_ROW_LIMIT: int = 500
    AREA_WAY_ROW_LIMIT: int = 5000
    AGENT_RESOLUTION_CONCURRENCY: int = 4
    NAME_RESOLUTION_CONCURRENCY: int = 3
    # Large-area tiling. A uniform deterministic grid keeps coverage
    # complete and bounded for country and region searches.
    MAX_TILE_SPAN_DEG: float = 0.75
    MAX_DISCOVERY_TILES: int = 40
    TILE_CONCURRENCY: int = 3
    # Weak-evidence acceptance thresholds for locally-named paths. These
    # govern relevance only; accepted geometry is always real and verified.
    MIN_LOCAL_PATH_KM: float = 0.30
    MIN_LOCAL_PATH_STRONG_KM: float = 1.50

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()