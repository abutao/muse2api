"""Admin records API over call_logs (M5)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth_admin import require_admin
from app.db import get_db

router = APIRouter(prefix="/records", tags=["records"])


def _row_public(row: Any) -> dict[str, Any]:
    d = dict(row)
    media = None
    raw = d.get("media_urls")
    if raw:
        try:
            media = json.loads(raw)
        except Exception:
            media = raw
    # Async generations (video / image tasks) only measure the submit call when
    # they are queued; pull the real elapsed time + media address from the
    # engine task store once the worker finishes.
    def _json_list(raw: Any) -> list:
        if not raw:
            return []
        try:
            v = json.loads(raw) if isinstance(raw, str) else raw
            return v if isinstance(v, list) else []
        except Exception:
            return []

    account = d.get("account") or ""
    attempts = _json_list(d.get("attempts_json"))
    log = _json_list(d.get("log_json"))
    latency = d.get("latency_ms")
    if d.get("upstream_task_id"):
        try:
            from app.muse_engine import tasks as task_store

            t = task_store.get_task(str(d["upstream_task_id"])) or {}
            url = t.get("url")
            if not url and isinstance(t.get("result"), dict):
                url = t["result"].get("url")
            if url and not media:
                media = [url]
            acc_id = str(t.get("account") or d.get("account") or "")
            if acc_id and not account:
                try:
                    from app.pool import store as pool_store

                    row = pool_store.get(acc_id) or {}
                    account = row.get("label") or row.get("name") or ""
                except Exception:
                    account = ""
            status = str(t.get("status") or "")
            if isinstance(t.get("log"), list) and t.get("log"):
                log = t["log"]
            if isinstance(t.get("attempts"), list) and t.get("attempts"):
                attempts = t["attempts"]
            if not attempts and status:
                done = status in ("completed", "succeeded", "success", "done")
                elapsed_s = float(t.get("elapsed") or 0)
                attempts = [
                    {
                        "at": int((t.get("created_at") or 0) * 1000),
                        "account": account,
                        "accountId": acc_id,
                        "ticket": "",
                        "profile": "",
                        "sid": "",
                        "region": "",
                        "status": None,
                        "outcome": "success" if done else ("http_error" if status == "failed" else "running"),
                        "ms": int(elapsed_s * 1000),
                        "error": str(t.get("error") or ""),
                    }
                ]
            if not log:
                created_ms = int((t.get("created_at") or 0) * 1000)
                updated_ms = int((t.get("updated_at") or 0) * 1000) or created_ms
                elapsed_s = float(t.get("elapsed") or 0)
                log = [
                    {"t": created_ms, "lv": "info", "msg": "任务已创建（排队）"},
                    {"t": updated_ms, "lv": "info" if status != "failed" else "err",
                     "msg": (f"完成 · 用时 {elapsed_s:.1f}s" if status != "failed" else f"失败：{t.get('error') or ''}")[:200]},
                ]
            elapsed = t.get("elapsed")
            if elapsed:
                latency = int(float(elapsed) * 1000)
            elif t.get("status") in ("completed", "succeeded", "success", "done"):
                created, updated = t.get("created_at"), t.get("updated_at")
                if isinstance(created, (int, float)) and isinstance(updated, (int, float)):
                    latency = int(max(0.0, updated - created) * 1000)
        except Exception:
            pass
    if not attempts or not log:
        def _ts_ms(val: Any) -> int:
            if not val:
                return 0
            try:
                from datetime import datetime, timezone

                return int(
                    datetime.strptime(str(val), "%Y-%m-%d %H:%M:%S")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                    * 1000
                )
            except Exception:
                return 0

        created_ms = _ts_ms(d.get("created_at"))
        status_int = d.get("status")
        done = status_int is not None and 200 <= int(status_int) < 300
        ms = int(latency or 0)
        if not attempts and status_int is not None:
            attempts = [
                {
                    "at": created_ms,
                    "account": account,
                    "accountId": "",
                    "ticket": "",
                    "profile": "",
                    "sid": "",
                    "region": "",
                    "status": status_int,
                    "outcome": "success" if done else "http_error",
                    "ms": ms,
                    "error": str(d.get("error") or ""),
                }
            ]
        if not log:
            log = [
                {"t": created_ms, "lv": "info", "msg": "请求开始"},
                {
                    "t": created_ms + ms,
                    "lv": "info" if done else "err",
                    "msg": ("完成" if done else "失败") + f" · {ms / 1000:.1f}s",
                },
            ]

    return {
        "id": d["id"],
        "created_at": d.get("created_at"),
        "method": d.get("method"),
        "path": d.get("path"),
        "api_type": d.get("api_type"),
        "model": d.get("model"),
        "stream": bool(d.get("stream")),
        "status": d.get("status"),
        "upstream_status": d.get("upstream_status"),
        "latency_ms": latency,
        "source": d.get("source"),
        "key_prefix": d.get("key_prefix"),
        "key_id": d.get("key_id"),
        "error": d.get("error"),
        "prompt_preview": d.get("prompt_preview"),
        "upstream_task_id": d.get("upstream_task_id"),
        "media_urls": media,
        "request_id": d.get("request_id"),
        "account": account or None,
        "accountId": d.get("account_id") or (attempts[0].get("accountId") if attempts else ""),
        "client": d.get("source") or "api",
        "ticketId": "",
        "attempts": attempts,
        "log": log,
    }


def _status_bucket(status: int | None) -> str:
    if status is None:
        return "running"
    if 200 <= int(status) < 300:
        return "success"
    if int(status) >= 400:
        return "failed"
    return "other"


@router.get("")
async def list_records(
    request: Request,
    status: str = Query(default="all"),  # all|success|failed|running
    api_type: str = Query(default=""),
    source: str = Query(default=""),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    require_admin(request)
    where = ["1=1"]
    args: list[Any] = []
    st = (status or "all").lower()
    if st == "success":
        where.append("status IS NOT NULL AND status >= 200 AND status < 300")
    elif st == "failed":
        where.append("(status IS NOT NULL AND status >= 400)")
    elif st == "running":
        where.append("status IS NULL")

    if api_type.strip():
        types = [x.strip() for x in api_type.split(",") if x.strip()]
        if len(types) == 1:
            where.append("api_type = ?")
            args.append(types[0])
        elif types:
            where.append("api_type IN (" + ",".join("?" * len(types)) + ")")
            args.extend(types)
    else:
        # 生成记录只看生图 / 生视频，对话另算
        where.append("ifnull(api_type,'') != 'chat'")
        where.append("ifnull(path,'') NOT LIKE '%/chat/completions%'")
    if source.strip():
        where.append("source = ?")
        args.append(source.strip())
    if q.strip():
        like = f"%{q.strip()}%"
        where.append(
            "("
            "CAST(id AS TEXT) LIKE ? OR ifnull(path,'') LIKE ? OR ifnull(model,'') LIKE ? "
            "OR ifnull(prompt_preview,'') LIKE ? OR ifnull(key_prefix,'') LIKE ? "
            "OR ifnull(error,'') LIKE ? OR ifnull(upstream_task_id,'') LIKE ?"
            ")"
        )
        args.extend([like] * 7)

    clause = " AND ".join(where)
    offset = (page - 1) * page_size
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM call_logs WHERE {clause}", args
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT * FROM call_logs
            WHERE {clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            [*args, page_size, offset],
        ).fetchall()
    items = [_row_public(r) for r in rows]
    for it in items:
        it["status_bucket"] = _status_bucket(it.get("status"))
    return {
        "ok": True,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "pages": max(1, (int(total) + page_size - 1) // page_size),
    }


@router.get("/{record_id}")
async def get_record(record_id: int, request: Request):
    require_admin(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM call_logs WHERE id=?", (record_id,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    item = _row_public(row)
    item["status_bucket"] = _status_bucket(item.get("status"))
    return {"ok": True, "item": item}


class DeleteBody(BaseModel):
    mode: Literal["failed", "before_days", "ids", "all"] = "failed"
    days: int | None = Field(default=30, ge=1, le=3650)
    ids: list[int] | None = None
    exclude_api_type: str | None = None


@router.post("/delete")
async def delete_records(body: DeleteBody, request: Request):
    require_admin(request)
    # Skip "running" (status IS NULL)
    skip = " AND status IS NOT NULL"
    extra = ""
    extra_args: list[Any] = []
    if (body.exclude_api_type or "").strip():
        extra = " AND ifnull(api_type,'') != ?"
        extra_args.append(body.exclude_api_type.strip())
    with get_db() as conn:
        if body.mode == "failed":
            cur = conn.execute(
                f"DELETE FROM call_logs WHERE status >= 400{skip}{extra}",
                extra_args,
            )
        elif body.mode == "before_days":
            days = int(body.days or 30)
            cur = conn.execute(
                f"""
                DELETE FROM call_logs
                WHERE created_at < datetime('now', ?)
                {skip}{extra}
                """,
                (f"-{days} days", *extra_args),
            )
        elif body.mode == "ids":
            ids = [int(x) for x in (body.ids or []) if int(x) > 0]
            if not ids:
                return {"ok": True, "deleted": 0}
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"DELETE FROM call_logs WHERE id IN ({placeholders}) AND status IS NOT NULL",
                ids,
            )
        elif body.mode == "all":
            cur = conn.execute(
                f"DELETE FROM call_logs WHERE 1=1{skip}{extra}",
                extra_args,
            )
        else:
            raise HTTPException(status_code=400, detail={"error": "bad_mode"})
        deleted = cur.rowcount
    return {"ok": True, "deleted": int(deleted), "mode": body.mode}
