"""In-process generation task store (JSON under data/)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import DATA_DIR

_LOCK = threading.Lock()
_TASKS: dict[str, dict[str, Any]] = {}
_LOADED = False


def _path() -> Path:
    p = DATA_DIR / "engine_tasks.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> None:
    global _LOADED, _TASKS
    if _LOADED:
        return
    path = _path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _TASKS = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            _TASKS = {}
    _LOADED = True


def _persist() -> None:
    path = _path()
    path.write_text(
        json.dumps(_TASKS, ensure_ascii=False, indent=0),
        encoding="utf-8",
    )


def create_task(kind: str, prompt: str) -> dict[str, Any]:
    with _LOCK:
        _load()
        tid = "task_" + uuid.uuid4().hex[:20]
        now = int(time.time())
        t: dict[str, Any] = {
            "id": tid,
            "object": "task",
            "kind": kind,
            "prompt": prompt,
            "status": "queued",
            "progress": 0,
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
        }
        _TASKS[tid] = t
        _persist()
        return dict(t)


def update_task(tid: str, **kw: Any) -> dict[str, Any] | None:
    with _LOCK:
        _load()
        t = _TASKS.get(tid)
        if not t:
            return None
        t.update(kw)
        t["updated_at"] = int(time.time())
        _persist()
        return dict(t)


def get_task(tid: str) -> dict[str, Any] | None:
    with _LOCK:
        _load()
        t = _TASKS.get(tid)
        return dict(t) if t else None


def count_active(kind: str) -> int:
    with _LOCK:
        _load()
        return sum(
            1
            for t in _TASKS.values()
            if t.get("kind") == kind and t.get("status") in ("queued", "processing")
        )


def list_tasks(limit: int = 200) -> list[dict]:
    with _LOCK:
        _load()
        items = sorted(
            _TASKS.values(),
            key=lambda x: x.get("created_at") or 0,
            reverse=True,
        )
        return [dict(x) for x in items[:limit]]
