"""Health / readiness endpoints (no auth)."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import get_settings
from app.db import db_path

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    path = db_path()
    return {
        "ok": True,
        "service": "muse-proxy",
        "version": __version__,
        "db": str(path.name),
        "engine_backend": "local",
        "upstream": "",  # P5: always empty
        "outbound_keys_configured": bool(settings.outbound_keys()),
        "upstream_key_configured": False,
    }


@router.get("/readyz")
def readyz() -> dict:
    path = db_path()
    ready = path.exists()
    return {"ok": ready, "db_exists": ready}
