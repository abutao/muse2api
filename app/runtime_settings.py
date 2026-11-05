"""SQLite KV overrides on top of env Settings (no secrets in repo)."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings, get_settings, reload_settings
from app.db import get_db

# Keys allowed to persist in app_settings (never commit these files).
ALLOWED = {
    "public_base",
    "upstream_base_url",
    "upstream_api_key",
    "connect_timeout_sec",
    "request_timeout_sec",
    "stream_idle_timeout_sec",
    "max_concurrency",
    "media_auth_required",
    "rewrite_media_urls",
    "console_generate_enabled",
    "record_keep_days",
    "charge_on_upstream_error",
    "proxy_scheduled_keepalive",
    "admin_password",
    "signup_client",
    "signup_muse_base",
    "default_region",
    "default_into_pool",
    "proxies",
    "result_mode",
    "engine_backend",
    "chromium_path",
    "cdp_port",
    "engine_concurrency",
    "acct_403_stop_hours",
    "acct_cooldown_cap_min",
    "acct_408_disable_streak",
    "acct_408_cooldown_min",
    "acct_rotate_attempts",
    "acct_daily_quota",
    "acct_global_daily_cap",
    "acct_interval_jitter_pct",
    "acct_min_interval_sec",
    "outbound_key_display",
    "signup_daily_last_run",
    "signup_daily_count",
    "signup_daily_minute",
    "signup_daily_hour",
    "signup_daily_enabled",
    "signup_interval_sec",
    "signup_concurrency",
    "queue_wait_sec",
}


def load_overrides() -> dict[str, Any]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    out: dict[str, Any] = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            out[r["key"]] = r["value"]
    return out


def set_overrides(patch: dict[str, Any]) -> dict[str, Any]:
    with get_db() as conn:
        for k, v in patch.items():
            if k not in ALLOWED:
                continue
            if v is None:
                conn.execute("DELETE FROM app_settings WHERE key=?", (k,))
                continue
            conn.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES(?,?,datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
                (k, json.dumps(v)),
            )
    reload_settings()
    return load_overrides()


def mask_secret(value: str | None, *, show: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= show:
        return "*" * len(value)
    return "*" * max(8, len(value) - show) + value[-show:]


def effective() -> Settings:
    """Return Settings with KV overrides applied (in-memory copy)."""
    base = get_settings()
    ov = load_overrides()
    if not ov:
        return base
    data = base.model_dump()
    for k, v in ov.items():
        if k in data:
            data[k] = v
    return Settings(**data)


def admin_password() -> str:
    ov = load_overrides()
    if "admin_password" in ov and ov["admin_password"]:
        return str(ov["admin_password"])
    return get_settings().admin_password
