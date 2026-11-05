"""Ephemeral console (playground) API keys — memory only, M4."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

_LOCK = threading.Lock()
_TOKENS: dict[str, "ConsoleToken"] = {}

DEFAULT_TTL_SEC = 15 * 60


@dataclass
class ConsoleToken:
    token: str
    expires_at: float
    admin_user: str
    created_at: float


def _purge_locked(now: float | None = None) -> None:
    now = now if now is not None else time.time()
    dead = [k for k, v in _TOKENS.items() if v.expires_at <= now]
    for k in dead:
        _TOKENS.pop(k, None)


def mint(
    admin_user: str,
    *,
    ttl_sec: int = DEFAULT_TTL_SEC,
    prefix: str = "mprox_",
) -> dict:
    """Create a short-lived Bearer key for admin console /v1 calls."""
    ttl = max(60, min(int(ttl_sec), 60 * 60))
    now = time.time()
    raw = secrets.token_urlsafe(24)
    # Distinct from long-lived env keys: …c_…
    token = f"{prefix}c_{raw}"
    entry = ConsoleToken(
        token=token,
        expires_at=now + ttl,
        admin_user=admin_user,
        created_at=now,
    )
    with _LOCK:
        _purge_locked(now)
        _TOKENS[token] = entry
    return {
        "token": token,
        "expires_in": ttl,
        "expires_at": int(entry.expires_at),
        "kind": "console",
        "prefix": token[:12] + "…",
    }


def validate(token: str | None) -> ConsoleToken | None:
    if not token:
        return None
    now = time.time()
    with _LOCK:
        _purge_locked(now)
        entry = _TOKENS.get(token)
        if not entry:
            return None
        if entry.expires_at <= now:
            _TOKENS.pop(token, None)
            return None
        return entry


def revoke(token: str) -> bool:
    with _LOCK:
        return _TOKENS.pop(token, None) is not None


def active_count() -> int:
    with _LOCK:
        _purge_locked()
        return len(_TOKENS)
