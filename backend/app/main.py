"""ShelfReady API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import router
from app.config import get_settings

app = FastAPI(title="ShelfReady API", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    return {"status": "ok", "version": __version__, "llm_mode": settings.llm_mode}
