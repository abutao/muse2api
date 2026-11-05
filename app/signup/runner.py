"""Background batch activation for subs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.signup import machine, store

logger = logging.getLogger("muse-proxy.signup.runner")

_task: asyncio.Task | None = None
_state: dict[str, Any] = {
    "running": False,
    "total": 0,
    "done": 0,
    "ok": 0,
    "failed": 0,
    "current": None,
}


def task_snapshot(*, after: int = 0) -> dict[str, Any]:
    raw = store.list_logs(after=after, limit=200)
    last_id = raw[-1]["id"] if raw else after
    total = int(_state["total"] or 0)
    done = int(_state["done"] or 0)
    progress = int(round(100.0 * done / total)) if total else 0
    # SPA 期望：id / level / t / msg；兼容 message / created_at
    logs = []
    for r in raw:
        msg = r.get("message") or ""
        iid = r.get("item_id")
        if iid:
            msg = f"[{iid}] {msg}"
        logs.append(
            {
                "id": r["id"],
                "level": r.get("level") or "info",
                "t": r.get("created_at") or "",
                "msg": msg,
                "item_id": iid,
                "message": r.get("message") or "",
                "created_at": r.get("created_at"),
            }
        )
    return {
        "ok": True,
        "running": bool(_state["running"]),
        "total": total,
        "done": done,
        "success": _state["ok"],
        "ok_count": _state["ok"],
        # 前端用 t.ok / t.fail
        "ok": _state["ok"],
        "fail": _state["failed"],
        "failed": _state["failed"],
        "progress": progress,
        "message": (
            f"当前 {_state['current']}" if _state.get("current") else ""
        ),
        "current": _state["current"],
        "after": last_id,
        "logs": logs,
    }


async def _run_ids(ids: list[str], concurrency: int = 1) -> None:
    machine.clear_stop()
    conc = max(1, min(int(concurrency or 1), 8))
    _state.update(
        running=True, total=len(ids), done=0, ok=0, failed=0, current=None
    )
    store.add_log(None, "info", f"批量启动 {len(ids)} 个任务（并发 {conc}）")
    sem = asyncio.Semaphore(conc)

    async def run_one(iid: str) -> None:
        async with sem:
            if machine.stopped():
                return
            _state["current"] = iid
            item = store.get_item(iid)
            if not item:
                _state["failed"] += 1
                _state["done"] += 1
                return
            # Skip terminal waiting for external payment unless retry from pending
            if item.get("status") == "awaiting_external_payment":
                store.add_log(iid, "info", "已在支付卡点，跳过（请 resume）")
                _state["done"] += 1
                _state["ok"] += 1
                return
            if item.get("status") in ("pooled", "activated"):
                _state["done"] += 1
                _state["ok"] += 1
                return
            try:
                result = await machine.run_until_blocked(iid)
            except Exception as exc:  # noqa: BLE001
                store.add_log(iid, "error", f"任务异常: {exc}"[:200])
                result = {"ok": False}
            _state["done"] += 1
            if result.get("ok"):
                _state["ok"] += 1
            else:
                _state["failed"] += 1

    try:
        from app.config import get_settings

        interval = max(0, int(get_settings().signup_interval_sec or 0))
        tasks = []
        for idx, iid in enumerate(ids):
            tasks.append(asyncio.create_task(run_one(iid)))
            if interval and idx < len(ids) - 1:
                # 同线程间隔：错开每个任务的启动时间
                await asyncio.sleep(interval)
        await asyncio.gather(*tasks)
        if machine.stopped():
            store.add_log(None, "warn", "批量已停止")
    finally:
        _state["running"] = False
        _state["current"] = None
        store.add_log(None, "info", "批量任务结束")


def start_batch(ids: list[str], concurrency: int = 1) -> dict[str, Any]:
    global _task
    if _state["running"]:
        return {"ok": False, "error": "busy", "message": "已有任务在跑"}
    if not ids:
        return {"ok": False, "error": "empty", "message": "无任务"}
    loop = asyncio.get_running_loop()
    _task = loop.create_task(_run_ids(ids))
    return {"ok": True, "total": len(ids)}


def stop_batch() -> dict[str, Any]:
    machine.request_stop()
    store.add_log(None, "warn", "收到停止请求")
    return {"ok": True, "stopping": True}
