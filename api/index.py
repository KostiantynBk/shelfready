"""Vercel entry point: the FastAPI app from backend/app, with the repo's data/ folder alongside."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
# Vercel's file system is read-only except /tmp: decisions live there while the instance is warm
os.environ.setdefault("DATA_DIR", "/tmp/shelfready")
os.environ.setdefault("LLM_MODE", "replay")

from app.main import app  # noqa: E402

__all__ = ["app"]
