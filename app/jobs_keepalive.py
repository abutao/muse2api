"""Local pool keepalive / renew / quota (muse.ai session + vm wake). No 19080."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from app.config import get_settings
from app.muse_engine import service as engine
from app.pool import store as pool_store

_jobs: dict[str, dict[str, Any]] = {}
_logs: dict[str, list[dict[str, Any]]] = {}
_lock = asyncio.Lock()
_runner: asyncio.Task | None = None
_sched: asyncio.Task | None = None


def get_job(job_id: str) -> dict[str, Any] | None:
    j = _jobs.get(job_id)
    return dict(j) if j else None


def list_logs(job_id: str, limit: int = 200) -> list[dict[str, Any]]:
    rows = _logs.get(job_id) or []
    return list(rows[-limit:])


def _append(job_id: str, level: str, message: str, **extra: Any) -> None:
    row = {"ts": int(time.time()), "level": level, "message": message, **extra}
    _logs.setdefault(job_id, []).append(row)
    job = _jobs.get(job_id)
    if job is not None:
        job["updated_at"] = int(time.time())


async def start_job(
    action: str,
    *,
    scope: str = "all",
    account_ids: list[str] | None = None,
) -> dict[str, Any]:
    action = (action or "keepalive").strip().lower()
    if action not in ("keepalive", "renew", "quota"):
        raise ValueError(f"unsupported action: {action}")
    async with _lock:
        for j in _jobs.values():
            if j.get("status") == "running":
                raise RuntimeError("keepalive job already running")
        jid = uuid.uuid4().hex[:12]
        now = int(time.time())
        job = {
            "id": jid,
            "action": action,
            "scope": scope,
            "account_ids": list(account_ids or []),
            "status": "queued",
            "ok": 0,
            "fail": 0,
            "total": 0,
            "created_at": now,
            "updated_at": now,
            "backend": "local",
        }
        _jobs[jid] = job
        _logs[jid] = []
        global _runner
        _runner = asyncio.create_task(_run(jid))
        return dict(job)


def _targets(scope: str, account_ids: list[str]) -> list[str]:
    if account_ids:
        return [str(a) for a in account_ids]
    rows = [r for r in pool_store.list_public(status="all") if r.get("enabled")]
    if scope == "dead":
        rows = [r for r in rows if not r.get("ok")]
    elif scope == "expiring":
        now = int(time.time())
        rows = [
            r
            for r in rows
            if r.get("expires_at") and int(r["expires_at"]) - now < 12 * 3600
        ]
    return [str(r["id"]) for r in rows]


async def _run(job_id: str) -> None:
    job = _jobs[job_id]
    job["status"] = "running"
    try:
        action = job["action"]
        ids = _targets(job.get("scope") or "all", job.get("account_ids") or [])
        job["total"] = len(ids)
        _append(job_id, "info", f"local {action}: {len(ids)} account(s)")
        for aid in ids:
            try:
                result = await asyncio.to_thread(_run_one_local, action, aid)
                if result.get("ok"):
                    job["ok"] = int(job.get("ok") or 0) + 1
                    _append(
                        job_id,
                        "ok",
                        f"{aid}: ok",
                        account_id=aid,
                        detail=result,
                    )
                else:
                    job["fail"] = int(job.get("fail") or 0) + 1
                    _append(
                        job_id,
                        "error",
                        f"{aid}: {result.get('error') or 'fail'}",
                        account_id=aid,
                        detail=result,
                    )
            except Exception as exc:  # noqa: BLE001
                job["fail"] = int(job.get("fail") or 0) + 1
                _append(job_id, "error", f"{aid}: {exc}", account_id=aid)
            await asyncio.sleep(0.05)
        job["status"] = "done"
        _append(
            job_id,
            "info",
            f"done ok={job['ok']} fail={job['fail']} total={job['total']}",
        )
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        _append(job_id, "error", f"job failed: {exc}")
    finally:
        job["updated_at"] = int(time.time())


def _run_one_local(action: str, aid: str) -> dict[str, Any]:
    """session renew + optional vm wake; write real Cookie expires only."""
    if action == "quota":
        try:
            q = engine.quota(aid)
            credits = None
            if isinstance(q, dict):
                credits = q.get("credits")
                if credits is None and isinstance(q.get("data"), dict):
                    credits = q["data"].get("credits")
            pool_store.update(aid, ok=True, checked=True, credits=credits)
            return {"ok": True, "credits": credits, "quota": q}
        except Exception as exc:  # noqa: BLE001
            pool_store.update(aid, ok=False, checked=True)
            return {"ok": False, "error": str(exc)}

    # keepalive / renew → /api/session?vm_state=live（401 自动密码重登）
    try:
        result = engine.keepalive_account(aid)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
    row = pool_store.get(aid) or {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "vm_state": result.get("vm_state"),
        "refreshed": result.get("refreshed"),
        "relogged": result.get("relogged"),
        "expires_at": row.get("expires_at"),
    }


def expiring_ids(within_sec: int = 12 * 3600) -> list[str]:
    """只在 expires_at 进入提前 12 小时窗口时才续签，节省代理。

    expires_at 未知（没有真实 Set-Cookie 记录）的账号，用 last_keepalive
    兜底：超过 12 小时没续过就查一次，避免漏掉。
    """
    import datetime as _dt

    now = int(time.time())
    out: list[str] = []
    for r in pool_store.list_public(status="all"):
        if not r.get("enabled"):
            continue
        exp = r.get("expires_at")
        if exp:
            try:
                if int(exp) - now <= within_sec:
                    out.append(str(r["id"]))
                continue
            except (TypeError, ValueError):
                pass
        lk = str(r.get("last_keepalive") or "")
        stale = True
        if lk:
            try:
                ts = _dt.datetime.strptime(lk[:19], "%Y-%m-%d %H:%M:%S")
                stale = (time.time() - ts.replace(tzinfo=_dt.timezone.utc).timestamp()) > within_sec
            except Exception:
                stale = True
        if stale:
            out.append(str(r["id"]))
    return out


async def scheduled_loop() -> None:
    """每 10 分钟检查一次；只有 hatch_vml 到期前 12 小时才续签（PROXY_SCHEDULED_KEEPALIVE 开启时）。"""
    while True:
        await asyncio.sleep(10 * 60)
        try:
            settings = get_settings()
            if not settings.proxy_scheduled_keepalive:
                continue
            ids = expiring_ids(12 * 3600)
            if not ids:
                continue
            await start_job("keepalive", account_ids=ids)
        except RuntimeError:
            pass
        except Exception:  # noqa: BLE001
            pass


def ensure_scheduler() -> None:
    global _sched
    if _sched is None or _sched.done():
        _sched = asyncio.create_task(scheduled_loop())

def latest_job() -> dict[str, Any] | None:
    """Most recent keepalive job row (UI progress helper)."""
    from app.db import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM keepalive_jobs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
