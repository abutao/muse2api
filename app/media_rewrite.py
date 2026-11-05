"""Rewrite upstream /v1/media URLs to PUBLIC_BASE."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from app.config import Settings

_MEDIA_PATH_RE = re.compile(r"^(?P<prefix>https?://[^/]+)?(?P<path>/v1/media/(?P<name>[^/?#]+))(?P<rest>[^\"']*)?$")


def is_safe_media_name(name: str) -> bool:
    if not name or len(name) > 200:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if name.startswith("."):
        return False
    return True


def rewrite_media_string(value: str, settings: Settings) -> str:
    if "/v1/media/" not in value:
        return value
    public = settings.public_base.rstrip("/")

    # Relative
    if value.startswith("/v1/media/"):
        return public + value

    # Any host …/v1/media/name
    parsed = urlparse(value)
    if parsed.path.startswith("/v1/media/"):
        name = parsed.path[len("/v1/media/") :]
        if is_safe_media_name(name.split("/")[0]):
            q = f"?{parsed.query}" if parsed.query else ""
            return f"{public}/v1/media/{name.split('/')[0]}{q}"
    return value


def rewrite_media_urls(obj: Any, settings: Settings) -> Any:
    if isinstance(obj, str):
        return rewrite_media_string(obj, settings)
    if isinstance(obj, list):
        return [rewrite_media_urls(x, settings) for x in obj]
    if isinstance(obj, dict):
        return {k: rewrite_media_urls(v, settings) for k, v in obj.items()}
    return obj


def maybe_rewrite_json_bytes(raw: bytes, settings: Settings) -> tuple[bytes, Any | None]:
    """Return (bytes, parsed_or_none). Parsed is post-rewrite if JSON object/list."""
    if not settings.rewrite_media_urls or not raw:
        try:
            return raw, json.loads(raw)
        except Exception:
            return raw, None
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw, None
    rewritten = rewrite_media_urls(parsed, settings)
    return (
        json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        rewritten,
    )
