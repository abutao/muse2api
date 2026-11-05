"""DB-backed outbound API keys (hashed); env keys remain compatible."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import get_db

TZ = ZoneInfo("Asia/Shanghai")


def hash_token(token: str) -> str:
    pepper = get_settings().session_secret or "muse-proxy"
    return hashlib.sha256(f"{pepper}\n{token}".encode("utf-8")).hexdigest()


def make_token(prefix: str | None = None) -> str:
    settings = get_settings()
    p = (prefix or settings.token_prefix or "mprox_").rstrip("_") + "_"
    return f"{p}u_{secrets.token_urlsafe(24)}"


def token_prefix_of(token: str) -> str:
    if len(token) <= 8:
        return token[:2] + "***"
    return token[:10] + "…"


def _today() -> str:
    return datetime.now(TZ).date().isoformat()


def _parse_models_allow(raw: str | None) -> list[str] | None:
    if raw is None or str(raw).strip() == "":
        return None  # allow all
    s = str(raw).strip()
    if s.startswith("["):
        try:
            data = json.loads(s)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def models_allow_to_store(value: Any) -> str | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, list):
        parts = [str(x).strip() for x in value if str(x).strip()]
        return ",".join(parts) if parts else None
    s = str(value).strip()
    return s or None


def row_to_public(row: Any, *, include_secret: str | None = None) -> dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else dict(row)
    out = {
        "id": d["id"],
        "name": d.get("name") or "",
        "token_prefix": d.get("token_prefix"),
        "kind": d.get("kind") or "user",
        "enabled": bool(d.get("enabled", 1)),
        "quota_total": d.get("quota_total"),
        "quota_used": int(d.get("quota_used") or 0),
        "daily_limit": d.get("daily_limit"),
        "daily_used": int(d.get("daily_used") or 0),
        "daily_reset_on": d.get("daily_reset_on"),
        "models_allow": _parse_models_allow(d.get("models_allow")),
        "expires_at": d.get("expires_at"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
        "last_used_at": d.get("last_used_at"),
    }
    if include_secret:
        out["token"] = include_secret
        out["token_once"] = True
    return out


def list_keys() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE kind='user' ORDER BY id DESC"
        ).fetchall()
    return [row_to_public(r) for r in rows]


def get_key(key_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE id=? AND kind='user'", (key_id,)
        ).fetchone()
    return row_to_public(row) if row else None


def create_key(
    *,
    name: str = "",
    quota_total: int | None = None,
    daily_limit: int | None = None,
    models_allow: Any = None,
    enabled: bool = True,
    expires_at: str | None = None,
) -> dict[str, Any]:
    token = make_token()
    th = hash_token(token)
    prefix = token_prefix_of(token)
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO api_keys(
                name, token_hash, token_prefix, kind, enabled,
                quota_total, quota_used, daily_limit, daily_used, daily_reset_on,
                models_allow, expires_at
            ) VALUES (?,?,?,?,?,?,0,?,0,?,?,?)
            """,
            (
                (name or "").strip()[:120],
                th,
                prefix,
                "user",
                1 if enabled else 0,
                quota_total,
                daily_limit,
                _today(),
                models_allow_to_store(models_allow),
                expires_at,
            ),
        )
        kid = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone()
    return row_to_public(row, include_secret=token)


def update_key(key_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {
        "name",
        "enabled",
        "quota_total",
        "daily_limit",
        "models_allow",
        "expires_at",
        "quota_used",
        "daily_used",
    }
    sets: list[str] = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "models_allow":
            v = models_allow_to_store(v)
        if k == "enabled":
            v = 1 if v else 0
        if k == "name" and v is not None:
            v = str(v).strip()[:120]
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return get_key(key_id)
    sets.append("updated_at=datetime('now')")
    vals.append(key_id)
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE api_keys SET {', '.join(sets)} WHERE id=? AND kind='user'",
            vals,
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
    return row_to_public(row) if row else None


def rotate_key(key_id: int) -> dict[str, Any] | None:
    token = make_token()
    th = hash_token(token)
    prefix = token_prefix_of(token)
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE api_keys SET token_hash=?, token_prefix=?, updated_at=datetime('now')
            WHERE id=? AND kind='user'
            """,
            (th, prefix, key_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
    return row_to_public(row, include_secret=token) if row else None


def delete_key(key_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM api_keys WHERE id=? AND kind='user'", (key_id,)
        )
        return cur.rowcount > 0


def _ensure_daily(row: dict[str, Any]) -> dict[str, Any]:
    today = _today()
    if (row.get("daily_reset_on") or "") != today:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE api_keys SET daily_used=0, daily_reset_on=?, updated_at=datetime('now')
                WHERE id=?
                """,
                (today, row["id"]),
            )
            fresh = conn.execute(
                "SELECT * FROM api_keys WHERE id=?", (row["id"],)
            ).fetchone()
        return dict(fresh)
    return row


def lookup_by_token(token: str) -> dict[str, Any] | None:
    th = hash_token(token)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE token_hash=? AND kind='user'", (th,)
        ).fetchone()
    if not row:
        return None
    return _ensure_daily(dict(row))


def assert_key_usable(row: dict[str, Any], *, model: str | None = None) -> None:
    """Raise openai-shaped errors via callers — return reason codes here as ValueError with code."""
    from app.auth_api import openai_http_error

    if not row.get("enabled"):
        raise openai_http_error(403, "API key disabled", code="key_disabled")
    exp = row.get("expires_at")
    if exp:
        try:
            exp_s = str(exp).replace("Z", "+00:00")
            exp_dt = datetime.fromisoformat(exp_s)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=TZ)
            if datetime.now(timezone.utc) > exp_dt.astimezone(timezone.utc):
                raise openai_http_error(403, "API key expired", code="key_expired")
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                raise

    qt = row.get("quota_total")
    if qt is not None and int(qt) >= 0:
        if int(row.get("quota_used") or 0) >= int(qt):
            raise openai_http_error(
                429, "Quota exceeded", code="quota_exceeded", err_type="rate_limit_error"
            )

    dl = row.get("daily_limit")
    if dl is not None and int(dl) >= 0:
        if int(row.get("daily_used") or 0) >= int(dl):
            raise openai_http_error(
                429,
                "Daily limit exceeded",
                code="daily_limit_exceeded",
                err_type="rate_limit_error",
            )

    allow = _parse_models_allow(row.get("models_allow"))
    if allow is not None and model:
        if model not in allow:
            raise openai_http_error(
                403,
                f"Model '{model}' not allowed for this key",
                code="model_not_allowed",
            )



def check_model_allowed(row: dict[str, Any], model: str | None) -> None:
    allow = _parse_models_allow(row.get("models_allow"))
    if allow is None or not model:
        return
    if model not in allow:
        from app.auth_api import openai_http_error

        raise openai_http_error(
            403,
            f"Model '{model}' not allowed for this key",
            code="model_not_allowed",
        )


def charge_key(key_id: int, *, amount: int = 1) -> None:
    if amount <= 0:
        return
    today = _today()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
        if not row:
            return
        d = dict(row)
        if (d.get("daily_reset_on") or "") != today:
            conn.execute(
                "UPDATE api_keys SET daily_used=0, daily_reset_on=? WHERE id=?",
                (today, key_id),
            )
        conn.execute(
            """
            UPDATE api_keys SET
              quota_used = quota_used + ?,
              daily_used = daily_used + ?,
              last_used_at = datetime('now'),
              updated_at = datetime('now')
            WHERE id=?
            """,
            (amount, amount, key_id),
        )


def touch_last_used(key_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE api_keys SET last_used_at=datetime('now') WHERE id=?",
            (key_id,),
        )
