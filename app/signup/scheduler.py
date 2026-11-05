"""按设置的「看门狗日补」每天定时自动补号（纯协议）。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.config import get_settings
from app.signup import runner, store

logger = logging.getLogger("muse-proxy.signup.scheduler")

_task: asyncio.Task | None = None


def _pending_ids(limit: int) -> list[str]:
    try:
        data = store.list_items(status="pending", page=1, page_size=max(1, limit))
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else []
    return [str(it["id"]) for it in (items or [])][:limit]


def tick() -> dict | None:
    """一分钟调用一次；命中 HH:MM 且今天没跑过就启动一批。"""
    s = get_settings()
    if not s.signup_daily_enabled:
        return None
    now = datetime.now()
    target = (int(s.signup_daily_hour), int(s.signup_daily_minute))
    if (now.hour, now.minute) < target:
        return None
    try:
        from app.runtime_settings import load_overrides, set_overrides

        today = now.strftime("%Y-%m-%d")
        if load_overrides().get("signup_daily_last_run") == today:
            return None
    except Exception:
        today = now.strftime("%Y-%m-%d")

    count = max(1, int(s.signup_daily_count or 20))
    ids = _pending_ids(count)
    if not ids:
        logger.info("daily refill: no pending items")
        try:
            set_overrides({"signup_daily_last_run": today})
        except Exception:
            pass
        return None
    try:
        info = runner.start_batch(ids, concurrency=int(s.signup_concurrency or 1))
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily refill start failed: %s", exc)
        return None
    try:
        set_overrides({"signup_daily_last_run": today})
    except Exception:
        pass
    logger.info("daily refill started: %s items (concurrency=%s)", len(ids), s.signup_concurrency)
    return info


async def loop() -> None:
    while True:
        try:
            tick()
        except Exception:  # noqa: BLE001
            logger.exception("signup daily scheduler tick failed")
        await asyncio.sleep(60)


def ensure_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(loop())
