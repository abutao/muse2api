"""Pure-HTTP session renew + VM wake (muse.ai)."""

from __future__ import annotations

from typing import Any

from app.muse_engine.engine import MuseEngine


def renew_session(
    cookies: dict[str, str],
    expires: dict | None = None,
    *,
    wake_vm: bool = True,
    proxy: dict | None = None,
) -> dict[str, Any]:
    return MuseEngine.renew_session_http(
        cookies, expires, wake_vm=wake_vm, proxy=proxy
    )
