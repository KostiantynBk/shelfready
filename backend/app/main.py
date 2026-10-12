"""ShelfReady API."""

from fastapi import FastAPI

from app import __version__
from app.config import get_settings

app = FastAPI(title="ShelfReady API", version=__version__)


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    return {"status": "ok", "version": __version__, "llm_mode": settings.llm_mode}
