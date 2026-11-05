"""Legacy muse2api reverse-proxy (REMOVED).

P5: /v1 no longer forwards to UPSTREAM/19080. Kept as a hard stub so any
stray import fails loudly instead of silently proxying.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth_api import openai_http_error
from app.config import Settings, get_settings


def upstream_url(path: str, settings: Settings | None = None) -> str:
    raise RuntimeError("UPSTREAM reverse-proxy removed; use local muse_engine")


async def forward(
    request: Request,
    upstream_path: str,
    *,
    prefer_stream: bool | None = None,
    api_key: str | None = None,
    api_type: str = "unknown",
    source: str = "external",
    require_upstream_key: bool = True,
    rewrite_json: bool = True,
):
    raise openai_http_error(
        503,
        "UPSTREAM/_fwd/19080 reverse-proxy removed; ENGINE_BACKEND=local only",
        code="upstream_removed",
    )
