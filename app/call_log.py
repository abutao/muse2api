"""Write truncated /v1 call_logs (no full secrets or large payloads)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.db import get_db

logger = logging.getLogger("muse-proxy.call_log")

_MEDIA_RE = re.compile(r"/v1/media/([^\"'?\s]+)")


def key_prefix(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 6:
        return key[:2] + "***"
    return key[:8] + "…"


def extract_model_from_body(body: bytes | None, content_type: str | None = None) -> str | None:
    if not body:
        return None
    ctype = (content_type or "").lower()
    if "multipart/" in ctype:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    model = data.get("model")
    return str(model)[:120] if model else None


def extract_prompt_preview(body: bytes | None, content_type: str | None = None) -> str | None:
    """Short prompt preview only — never store images/base64/cookies."""
    if not body:
        return None
    ctype = (content_type or "").lower()
    if "multipart/" in ctype:
        return "[multipart]"
    try:
        data = json.loads(body)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    prompt = data.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()[:160]
    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict):
            content = last.get("content")
            if isinstance(content, str):
                return content.strip()[:160]
    return None


def extract_task_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for k in ("task_id", "id"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v[:120]
    data = payload.get("data")
    if isinstance(data, dict):
        for k in ("task_id", "id"):
            v = data.get(k)
            if isinstance(v, str) and v:
                return v[:120]
    return None


def extract_task_id_from_path(path: str | None) -> str | None:
    if not path:
        return None
    m = re.search(r"/(?:images/tasks|videos)/([^/?#]+)", path)
    if m:
        return m.group(1)[:120]
    return None


def backfill_create_media_urls(upstream_task_id: str | None, media_urls: list[str] | None) -> None:
    """Write poll media_urls onto the matching POST create log (same upstream_task_id)."""
    if not upstream_task_id or not media_urls:
        return
    try:
        payload = json.dumps(media_urls, ensure_ascii=False)
        with get_db() as conn:
            conn.execute(
                """
                UPDATE call_logs
                SET media_urls = ?
                WHERE upstream_task_id = ?
                  AND UPPER(method) = 'POST'
                  AND (media_urls IS NULL OR media_urls = '' OR media_urls = '[]')
                """,
                (payload, upstream_task_id),
            )
    except Exception:
        logger.exception("failed to backfill create media_urls")


def collect_media_urls(payload: Any, limit: int = 8) -> list[str]:
    found: list[str] = []

    def add(url: str) -> None:
        if len(found) >= limit:
            return
        u = url.strip()[:300]
        if u and u not in found:
            found.append(u)

    def walk(obj: Any, key: str | None = None) -> None:
        if len(found) >= limit:
            return
        if isinstance(obj, str):
            if "/v1/media/" in obj:
                add(obj)
            elif key in ("url", "image_url", "video_url") and obj.startswith(
                ("http://", "https://", "/v1/")
            ):
                add(obj)
            return
        if isinstance(obj, list):
            for x in obj:
                walk(x, key)
                if len(found) >= limit:
                    return
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, str(k) if k is not None else None)
                if len(found) >= limit:
                    return

    walk(payload)
    return found


def write_call_log(
    *,
    method: str,
    path: str,
    api_type: str,
    source: str = "external",
    model: str | None = None,
    stream: bool = False,
    status: int | None = None,
    upstream_status: int | None = None,
    latency_ms: int | None = None,
    key: str | None = None,
    key_id: int | None = None,
    error: str | None = None,
    prompt_preview: str | None = None,
    upstream_task_id: str | None = None,
    media_urls: list[str] | None = None,
    request_id: str | None = None,
    account: str | None = None,
    account_id: str | None = None,
    attempts: list[dict[str, Any]] | None = None,
    log: list[dict[str, Any]] | None = None,
) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO call_logs(
                    method, path, api_type, source, model, stream,
                    status, upstream_status, latency_ms, key_prefix, key_id, error,
                    prompt_preview, upstream_task_id, media_urls, request_id,
                    account, account_id, attempts_json, log_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    method.upper(),
                    path[:240],
                    api_type[:40],
                    source[:40],
                    (model or None),
                    1 if stream else 0,
                    status,
                    upstream_status,
                    latency_ms,
                    key_prefix(key),
                    key_id,
                    (error or None)[:500] if error else None,
                    (prompt_preview or None)[:200] if prompt_preview else None,
                    upstream_task_id,
                    json.dumps(media_urls, ensure_ascii=False) if media_urls else None,
                    request_id,
                    account,
                    account_id,
                    json.dumps(attempts, ensure_ascii=False) if attempts else None,
                    json.dumps(log, ensure_ascii=False) if log else None,
                ),
            )
    except Exception:
        logger.exception("failed to write call_log")
