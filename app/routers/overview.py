"""Overview metrics — call_logs aggregates + upstream health + pool + keys."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request

from app import api_keys
from app.auth_admin import require_admin
from app.config import get_settings
from app.db import get_db
from app.pool import store as pool_store

router = APIRouter(tags=["overview"])

TZ = ZoneInfo("Asia/Shanghai")

RANGE_HOURS = {
    "6h": 6,
    "24h": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
}


def _parse_created(s: str) -> datetime | None:
    if not s:
        return None
    raw = s.strip()
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        try:
            return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None


def _success(status: int | None) -> bool:
    return status is not None and 200 <= int(status) < 400


def _window_start(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _today_start_shanghai() -> datetime:
    now = datetime.now(TZ)
    local_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


def _agg(rows: list[Any]) -> dict[str, Any]:
    total = len(rows)
    ok = sum(1 for r in rows if _success(r["status"]))
    by_type: dict[str, dict[str, int]] = {}
    for r in rows:
        t = (r["api_type"] or r["path"] or "other")[:40]
        slot = by_type.setdefault(t, {"total": 0, "ok": 0, "fail": 0})
        slot["total"] += 1
        if _success(r["status"]):
            slot["ok"] += 1
        else:
            slot["fail"] += 1
    return {
        "total": total,
        "ok": ok,
        "fail": total - ok,
        "success_rate": round(100.0 * ok / total, 2) if total else None,
        "by_type": by_type,
    }


@router.get("/overview")
async def overview(
    request: Request,
    ov_range: str = Query(default="24h", alias="range"),
):
    require_admin(request)
    hours = RANGE_HOURS.get(ov_range, 24)
    since = _window_start(hours)
    today = _today_start_shanghai()
    since_s = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    today_s = today.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_db() as conn:
        all_rows = conn.execute(
            "SELECT api_type, path, status, created_at, error, model, latency_ms "
            "FROM call_logs"
        ).fetchall()
        events = conn.execute(
            "SELECT id, level, message, created_at FROM events "
            "WHERE level IN ('error','warn') OR message LIKE '%FAIL%' "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
        # also recent failed call_logs as events fallback
        fail_logs = conn.execute(
            "SELECT id, status, path, api_type, error, created_at FROM call_logs "
            "WHERE status IS NULL OR status >= 400 "
            "ORDER BY id DESC LIMIT 15"
        ).fetchall()

    def in_win(row: Any, start: datetime) -> bool:
        dt = _parse_created(row["created_at"] or "")
        return bool(dt and dt >= start)

    today_rows = [r for r in all_rows if in_win(r, today)]
    range_rows = [r for r in all_rows if in_win(r, since)]
    cumulative = _agg(all_rows)
    today_agg = _agg(today_rows)
    range_agg = _agg(range_rows)

    keys = api_keys.list_keys()
    keys_enabled = sum(1 for k in keys if k.get("enabled"))

    local_stats = pool_store.stats()
    pool: dict[str, Any] = {
        "ok": True,
        "backend": "local",
        "stats": local_stats,
        "account_count": local_stats.get("total") or local_stats.get("count") or 0,
        "source": "local_pool",
    }
    health: dict[str, Any] = {
        "ok": True,
        "backend": "local",
        "message": "local muse_engine",
    }

    fail_events: list[dict[str, Any]] = []
    for e in events:
        fail_events.append(
            {
                "id": f"e{e['id']}",
                "level": e["level"],
                "message": e["message"],
                "created_at": e["created_at"],
                "source": "events",
            }
        )
    for r in fail_logs:
        fail_events.append(
            {
                "id": f"c{r['id']}",
                "level": "error",
                "message": f"{r['api_type'] or r['path']} → {r['status']}: {r['error'] or ''}",
                "created_at": r["created_at"],
                "source": "call_logs",
            }
        )
    fail_events.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    fail_events = fail_events[:20]

    s = get_settings()
    return {
        "ok": True,
        "range": ov_range if ov_range in RANGE_HOURS else "24h",
        "tz": "Asia/Shanghai",
        "generated_at": datetime.now(TZ).isoformat(),
        "today": today_agg,
        "cumulative": cumulative,
        "range_stats": range_agg,
        "upstream": {"health": health, "pool": pool},
        "keys": {
            "db_total": len(keys),
            "db_enabled": keys_enabled,
            "env_count": len(s.outbound_keys()),
        },
        "recent_failures": fail_events,
        "since": since_s,
        "today_since": today_s,
    }


@router.get("/metrics")
async def metrics(request: Request):
    """Compact metrics alias for overview cards."""
    require_admin(request)
    data = await overview(request, ov_range="24h")
    return {
        "ok": True,
        "today_success_rate": data["today"]["success_rate"],
        "today_total": data["today"]["total"],
        "cumulative_success_rate": data["cumulative"]["success_rate"],
        "cumulative_total": data["cumulative"]["total"],
        "keys_enabled": data["keys"]["db_enabled"],
        "upstream_ok": bool((data["upstream"]["health"] or {}).get("ok")),
        "pool_stats": (data["upstream"]["pool"] or {}).get("stats") or {},
    }


@router.get("/stats/series")
async def stats_series(
    request: Request,
    ov_range: str = Query(default="24h", alias="range"),
):
    require_admin(request)
    hours = RANGE_HOURS.get(ov_range, 24)
    since = _window_start(hours)
    # bucket size
    if hours <= 6:
        bucket_min = 15
    elif hours <= 24:
        bucket_min = 60
    else:
        bucket_min = 360

    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, created_at, api_type FROM call_logs"
        ).fetchall()

    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        dt = _parse_created(r["created_at"] or "")
        if not dt or dt < since:
            continue
        local = dt.astimezone(TZ)
        # floor to bucket
        minute = (local.minute // bucket_min) * bucket_min
        key_dt = local.replace(minute=minute, second=0, microsecond=0)
        key = key_dt.isoformat()
        slot = buckets.setdefault(key, {"total": 0, "ok": 0, "fail": 0})
        slot["total"] += 1
        if _success(r["status"]):
            slot["ok"] += 1
        else:
            slot["fail"] += 1

    series = [
        {"t": k, **buckets[k], "success_rate": round(100.0 * buckets[k]["ok"] / buckets[k]["total"], 2)}
        for k in sorted(buckets)
    ]
    return {
        "ok": True,
        "range": ov_range if ov_range in RANGE_HOURS else "24h",
        "bucket_minutes": bucket_min,
        "tz": "Asia/Shanghai",
        "points": series,
    }
