"""Vercel entry point: the FastAPI app from backend/app, with the repo's data/ folder alongside."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
# Vercel's file system is read-only except /tmp: decisions live there while the instance is warm
os.environ.setdefault("DATA_DIR", "/tmp/shelfready")
os.environ.setdefault("LLM_MODE", "replay")
# The demo's Evolus workspace (IDs are not secrets; EVOLUS_API_KEY and EVOLUS_APP_ID come from Vercel settings)
os.environ.setdefault("EVOLUS_AGENT_ID", "3debbedf-56c0-4635-bb23-8023c0fb91bc")
os.environ.setdefault("EVOLUS_LIBRARY_PRODUCTS_ID", "7db052de-70ca-f111-bf5b-6045bd9ce552")

from app.main import app  # noqa: E402

__all__ = ["app"]
