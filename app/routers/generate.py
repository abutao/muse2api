"""Console generate APIs — short-lived token, models, gallery (M4)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from app import console_tokens
from app.auth_admin import require_admin
from app.config import get_settings
from app.db import get_db

router = APIRouter(prefix="/generate", tags=["generate"])


def _playground_on(settings) -> bool:
    return bool(settings.console_generate_enabled)


@router.post("/token")
async def generate_token(request: Request):
    """Issue a short-lived outbound key for console /v1 calls (admin session)."""
    user = require_admin(request)
    settings = get_settings()
    if not _playground_on(settings):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "console_disabled",
                "message": "试调已关闭（CONSOLE_GENERATE_ENABLED / GENERATE_PLAYGROUND）",
            },
        )
    payload = console_tokens.mint(
        user,
        ttl_sec=settings.console_token_ttl_sec,
        prefix=settings.token_prefix or "mprox_",
    )
    return {
        "ok": True,
        "token": payload["token"],
        "expires_in": payload["expires_in"],
        "expires_at": payload["expires_at"],
        "kind": payload["kind"],
        "prefix": payload["prefix"],
        "usage": "Authorization: Bearer <token> → /v1/*",
    }


@router.get("/models")
async def generate_models(request: Request):
    require_admin(request)
    settings = get_settings()
    if not _playground_on(settings):
        raise HTTPException(
            status_code=403,
            detail={"error": "console_disabled", "message": "试调已关闭"},
        )
    from app.muse_engine.service import local_models

    body = local_models()
    return {"ok": True, "object": "list", "data": body.get("data") or []}


def _media_name_from_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
    except Exception:
        path = url
    if "/v1/media/" in path:
        name = path.rsplit("/v1/media/", 1)[-1].strip("/")
        if not name or "/" in name or ".." in name:
            return None
        return name
    # External / rewritten URL fallback — still show in gallery/history
    if url.startswith(("http://", "https://", "/")):
        leaf = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
        return (leaf or url)[:180]
    return None


@router.get("/gallery")
async def generate_gallery(request: Request, limit: int = 18):
    require_admin(request)
    settings = get_settings()
    if not _playground_on(settings):
        raise HTTPException(
            status_code=403,
            detail={"error": "console_disabled", "message": "试调已关闭"},
        )
    lim = max(1, min(int(limit or 18), 48))
    items: list[dict[str, Any]] = []
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, path, api_type, model, status, media_urls, source,
                   prompt_preview, upstream_task_id, method
            FROM call_logs
            WHERE status IS NOT NULL AND status >= 200 AND status < 300
              AND media_urls IS NOT NULL AND media_urls != '' AND media_urls != '[]'
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim * 4,),
        ).fetchall()
        failed_rows = conn.execute(
            """
            SELECT id, created_at, path, api_type, model, status, media_urls, source,
                   prompt_preview, upstream_task_id, method, error
            FROM call_logs
            WHERE status IS NOT NULL AND status >= 400
              AND api_type IN ('image', 'video')
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        prompt_by_task: dict[str, str] = {}
        need_prompts = {
            (r["upstream_task_id"] or "")
            for r in rows
            if r["upstream_task_id"] and not (r["prompt_preview"] or "").strip()
        }
        need_prompts.discard("")
        if need_prompts:
            qmarks = ",".join("?" for _ in need_prompts)
            for pr in conn.execute(
                f"""
                SELECT upstream_task_id, prompt_preview FROM call_logs
                WHERE upstream_task_id IN ({qmarks})
                  AND prompt_preview IS NOT NULL AND prompt_preview != ''
                ORDER BY id ASC
                """,
                tuple(need_prompts),
            ).fetchall():
                tid = pr["upstream_task_id"]
                if tid and tid not in prompt_by_task:
                    prompt_by_task[tid] = pr["prompt_preview"]
    seen: set[str] = set()
    for row in failed_rows:
        if len(items) >= lim:
            break
        items.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "path": row["path"],
                "api_type": row["api_type"],
                "model": row["model"],
                "source": row["source"],
                "status": row["status"],
                "prompt": (row["prompt_preview"] or "").strip(),
                "prompt_preview": (row["prompt_preview"] or "").strip(),
                "upstream_task_id": row["upstream_task_id"],
                "method": row["method"],
                "url": None,
                "media_urls": [],
                "media_name": None,
                "kind": "video" if row["api_type"] == "video" else "image",
                "error": (row["error"] or "生成失败")[:400],
            }
        )
    for row in rows:
        try:
            urls = json.loads(row["media_urls"] or "[]")
        except Exception:
            continue
        if not isinstance(urls, list):
            continue
        clean = [u for u in urls if isinstance(u, str) and u.strip()]
        if not clean:
            continue
        # Prefer create(POST) rows after backfill; still accept GET poll rows
        names = []
        for u in clean:
            name = _media_name_from_url(u)
            if not name or name in seen:
                continue
            names.append((name, u))
        if not names:
            continue
        for name, u in names:
            seen.add(name)
        kind = "video" if any(n.endswith(".mp4") for n, _ in names) else "image"
        tid = row["upstream_task_id"]
        prompt = (row["prompt_preview"] or "").strip() or (
            prompt_by_task.get(tid or "") if tid else ""
        )
        items.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "path": row["path"],
                "api_type": row["api_type"],
                "model": row["model"],
                "source": row["source"],
                "status": row["status"],
                "prompt": prompt,
                "prompt_preview": prompt,
                "upstream_task_id": tid,
                "method": row["method"],
                "url": names[0][1],
                "media_urls": [u for _, u in names],
                "media_name": names[0][0],
                "kind": kind,
            }
        )
        if len(items) >= lim:
            break
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/status")
async def generate_status(request: Request):
    """Lightweight flag for the SPA (playground on/off)."""
    require_admin(request)
    settings = get_settings()
    return {
        "ok": True,
        "enabled": _playground_on(settings),
        "console_token_ttl_sec": settings.console_token_ttl_sec,
        "active_console_tokens": console_tokens.active_count(),
        "rewrite_media_urls": settings.rewrite_media_urls,
        "media_auth_required": settings.media_auth_required,
    }
