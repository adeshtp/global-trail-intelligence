from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    reload_enabled = os.getenv(
        "RELOAD",
        "false",
    ).lower() in {"1", "true", "yes"}
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8001")),
        reload=reload_enabled,
        reload_dirs=["app"] if reload_enabled else None,
    )
