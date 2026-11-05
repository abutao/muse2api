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


def _fresh_log(created_at: Any, seconds: int = 20 * 60) -> bool:
    if not created_at:
        return False
    try:
        from datetime import datetime, timezone

        created = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds() < seconds
    except Exception:
        return False


def _stored_media(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [u.strip() for u in parsed if isinstance(u, str) and u.strip()]


def _task_media(task_id: str) -> tuple[list[str], str, str]:
    if not task_id:
        return [], "", ""
    try:
        from app.muse_engine import tasks as task_store

        task = task_store.get_task(task_id) or {}
    except Exception:
        return [], "", ""
    url = task.get("url")
    if not url and isinstance(task.get("result"), dict):
        url = task["result"].get("url")
    urls = [url.strip()] if isinstance(url, str) and url.strip() else []
    return urls, str(task.get("status") or ""), str(task.get("error") or "")


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
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, path, api_type, model, status, media_urls, source,
                   prompt_preview, upstream_task_id, method, error
            FROM call_logs
            WHERE api_type IN ('image', 'video')
              AND ifnull(path,'') NOT LIKE '%/chat/completions%'
              AND UPPER(ifnull(method,'POST')) = 'POST'
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim * 4,),
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        tid = (row["upstream_task_id"] or "").strip()
        key = "task:" + tid if tid else "log:" + str(row["id"])
        urls = _stored_media(row["media_urls"])
        task_urls, task_status, task_error = _task_media(tid)
        if task_urls and not urls:
            urls = task_urls
            try:
                from app.call_log import finish_task_log

                finish_task_log(tid, media_urls=urls, status=200)
            except Exception:
                pass
        status = row["status"]
        error = (row["error"] or task_error or "")[:400]
        failed = task_status == "failed" or (status is not None and int(status) >= 400)
        fresh = _fresh_log(row["created_at"])
        running = (not urls) and (not failed) and (
            task_status in ("queued", "processing", "running", "in_progress")
            or (not task_status and fresh and status in (200, 202, None))
        )
        kind = "video" if row["api_type"] == "video" or any(
            u.lower().split("?", 1)[0].endswith((".mp4", ".webm", ".mov")) for u in urls
        ) else "image"
        item = {
            "id": row["id"],
            "created_at": row["created_at"],
            "path": row["path"],
            "api_type": row["api_type"],
            "model": row["model"],
            "source": row["source"],
            "status": 200 if urls else (502 if failed else (status or 202)),
            "prompt": (row["prompt_preview"] or "").strip(),
            "prompt_preview": (row["prompt_preview"] or "").strip(),
            "upstream_task_id": tid or None,
            "method": row["method"],
            "url": urls[0] if urls else None,
            "media_urls": urls,
            "media_name": _media_name_from_url(urls[0]) if urls else None,
            "kind": kind,
            "running": running,
            "error": (error or "生成失败") if failed else "",
        }
        prev = grouped.get(key)
        if prev is None:
            grouped[key] = item
            order.append(key)
            continue
        # 同一个任务只留一条：有结果的盖过「已受理」
        if item["media_urls"] and not prev["media_urls"]:
            grouped[key] = item
    items = [
        grouped[k]
        for k in order
        if grouped[k]["media_urls"] or grouped[k]["running"] or grouped[k]["error"]
    ]
    items = items[:lim]
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
