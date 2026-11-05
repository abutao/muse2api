"""Local SQLite account pool — Cookie ciphertext is the authority (plan §12 P1)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from app.db import get_db
from app.signup.crypto import decrypt_dict, encrypt_dict

ESSENTIAL_COOKIES = ("hatch_sess",)
VML_TTL = 2 * 86400  # muse.ai hatch_vml typical life (~48h); NEVER invent local +48h
_LOCK = threading.RLock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_ts() -> int:
    return int(time.time())


def _is_pos(v: Any) -> bool:
    try:
        return int(v) > 0
    except (TypeError, ValueError):
        return False


def parse_cookie_header(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k:
            out[k] = v
    return out


def cookie_header_from_dict(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in (cookies or {}).items() if v)


def account_expiry(cookies_exp: dict | None, base_ts: int | None = None) -> int | None:
    """Pick account expires_at from *real* Cookie expires only.

    Never invent base_ts+VML_TTL (+48h). Missing hatch_vml → best real cookie
    expiry, else None (UI shows expires_estimated).
    """
    ce = cookies_exp or {}
    vml = ce.get("hatch_vml")
    if _is_pos(vml):
        return int(vml)
    vals: list[int] = []
    for name in ("hatch_vml", "hatch_sess", "hatch_gw", *ESSENTIAL_COOKIES):
        if _is_pos(ce.get(name)):
            vals.append(int(ce[name]))
    if not vals:
        for v in ce.values():
            if _is_pos(v):
                vals.append(int(v))
    return min(vals) if vals else None


def _encrypt_cookies(cookies: dict[str, str], cookies_exp: dict | None = None) -> str:
    return encrypt_dict(
        {
            "cookies": cookies or {},
            "cookies_exp": cookies_exp or {},
            "cookie_header": cookie_header_from_dict(cookies or {}),
        }
    )


def _decrypt_row_secrets(row: dict[str, Any]) -> dict[str, Any]:
    blob = decrypt_dict(row.get("cookie_enc")) if row.get("cookie_enc") else {}
    cookies = blob.get("cookies") if isinstance(blob.get("cookies"), dict) else {}
    if not cookies and blob.get("cookie_header"):
        cookies = parse_cookie_header(str(blob.get("cookie_header") or ""))
    cookies_exp = blob.get("cookies_exp") if isinstance(blob.get("cookies_exp"), dict) else {}
    pwd_blob = decrypt_dict(row.get("password_enc")) if row.get("password_enc") else {}
    return {
        "cookies": cookies,
        "cookies_exp": cookies_exp,
        "cookie_header": blob.get("cookie_header") or cookie_header_from_dict(cookies),
        "password": (pwd_blob or {}).get("muse_login_password") or "",
    }


def _fp_obj(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _row_to_public(row: dict[str, Any], *, include_secrets: bool = False) -> dict[str, Any]:
    secrets = _decrypt_row_secrets(row) if (include_secrets or True) else {}
    # always need cookie_count; decrypt briefly
    if not secrets:
        secrets = _decrypt_row_secrets(row)
    cookies = secrets.get("cookies") or {}
    enabled = bool(row.get("enabled", 1))
    ok = row.get("ok")
    if ok is not None:
        ok = bool(ok)
    if not enabled:
        status = "disabled"
    elif ok is True:
        status = "ok"
    elif ok is False:
        status = "invalid"
    else:
        status = "unknown"
    proxy = None
    if row.get("proxy_json"):
        try:
            proxy = json.loads(row["proxy_json"])
        except json.JSONDecodeError:
            proxy = None
    quota = None
    if row.get("quota_cache"):
        try:
            quota = json.loads(row["quota_cache"])
        except json.JSONDecodeError:
            quota = None
    fp = _fp_obj(row.get("fingerprint_json"))
    email = row.get("email") or ""
    name = row.get("name") or ""
    label = row.get("label") or name or email or row.get("id") or ""
    account = email or label
    out: dict[str, Any] = {
        "id": row["id"],
        "label": label,
        "tag": label if label != account else (name or label),
        "name": name or label,
        "account": account,
        "email": email,
        "enabled": enabled,
        "ok": ok,
        "status": status,
        "note": row.get("note") or "",
        "remark": row.get("note") or "",
        "cookie_count": len(cookies),
        "has_cookie": bool(cookies),
        "hasCookie": bool(cookies),
        "has_password": bool(secrets.get("password")),
        "essential_ok": all(cookies.get(n) for n in ESSENTIAL_COOKIES),
        "expires_at": row.get("expires_at"),
        "expires_estimated": not _is_pos((secrets.get("cookies_exp") or {}).get("hatch_vml")),
        "created_at": row.get("created_at"),
        "last_used": row.get("last_used_at"),
        "last_used_at": row.get("last_used_at"),
        "last_keepalive": row.get("last_keepalive_at"),
        "last_keepalive_at": row.get("last_keepalive_at"),
        "checked_at": row.get("checked_at"),
        "use_count": int(row.get("use_count") or 0),
        "quota": quota,
        "proxy": (
            {
                "server": (proxy or {}).get("server"),
                "username": (proxy or {}).get("username") or "",
                "has_password": bool((proxy or {}).get("password")),
            }
            if proxy
            else None
        ),
        "fingerprint": fp,
        "fingerprint_json": row.get("fingerprint_json"),
        "fingerprint_readonly": True,
        "signup_item_id": row.get("signup_item_id"),
    }
    if include_secrets:
        out["password"] = secrets.get("password") or ""
        out["cookie"] = ""  # never echo by default; use get_cookies()
        out["cookies"] = cookies
        out["cookies_exp"] = secrets.get("cookies_exp") or {}
        out["cookie_header"] = secrets.get("cookie_header") or ""
        if isinstance(fp, dict):
            out["fingerprint_text"] = json.dumps(fp, ensure_ascii=False, indent=2)
        elif fp:
            out["fingerprint_text"] = str(fp)
        else:
            out["fingerprint_text"] = ""
    return out


def list_raw() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM pool_accounts ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_public(*, status: str = "all", q: str = "") -> list[dict[str, Any]]:
    items = [_row_to_public(r) for r in list_raw()]
    st = (status or "all").lower()
    if st and st != "all":
        items = [a for a in items if a.get("status") == st]
    qq = (q or "").strip().lower()
    if qq:
        items = [
            a
            for a in items
            if qq in str(a.get("id") or "").lower()
            or qq in str(a.get("label") or "").lower()
            or qq in str(a.get("note") or "").lower()
            or qq in str(a.get("email") or "").lower()
            or qq in str(a.get("account") or "").lower()
            or qq in str(a.get("name") or "").lower()
        ]
    return items


def stats() -> dict[str, Any]:
    items = list_public()
    return {
        "total": len(items),
        "enabled": sum(1 for a in items if a.get("enabled")),
        "ok": sum(1 for a in items if a.get("status") == "ok"),
        "invalid": sum(1 for a in items if a.get("status") == "invalid"),
        "disabled": sum(1 for a in items if a.get("status") == "disabled"),
        "with_cookie": sum(1 for a in items if a.get("has_cookie")),
    }


def get(aid: str) -> dict[str, Any] | None:
    if not aid:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT * FROM pool_accounts WHERE id=?", (aid,)).fetchone()
    return dict(row) if row else None


def get_public(aid: str, *, include_secrets: bool = False) -> dict[str, Any] | None:
    row = get(aid)
    if not row:
        return None
    return _row_to_public(row, include_secrets=include_secrets)


def get_password(aid: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM pool_accounts WHERE id=?", (aid,)).fetchone()
    if not row:
        return ""
    return str(_decrypt_row_secrets(dict(row)).get("password") or "")


def get_proxy(aid: str) -> dict | None:
    row = get(aid) or {}
    raw = row.get("proxy_json")
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def get_cookies(aid: str) -> tuple[dict[str, str], dict[str, Any]]:
    row = get(aid)
    if not row:
        return {}, {}
    secrets = _decrypt_row_secrets(row)
    return secrets.get("cookies") or {}, secrets.get("cookies_exp") or {}


def add_account(
    *,
    cookies: dict[str, str] | None = None,
    cookie_header: str = "",
    label: str = "",
    email: str = "",
    name: str = "",
    note: str = "",
    password: str | None = None,
    fingerprint: Any = None,
    signup_item_id: str | None = None,
    proxy: dict[str, Any] | None = None,
    cookies_exp: dict | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    cookies = dict(cookies or {})
    if cookie_header and not cookies:
        cookies = parse_cookie_header(cookie_header)
    if not any(cookies.get(n) for n in ESSENTIAL_COOKIES):
        raise ValueError("missing_essential_cookie:hatch_sess")
    aid = uuid.uuid4().hex[:12]
    now = _now_iso()
    ts = _now_ts()
    exp = {k: int(v) for k, v in (cookies_exp or {}).items() if _is_pos(v)}
    expires_at = account_expiry(exp, ts)
    email_v = (email or "").strip()
    label_v = (label or email_v or name or aid).strip()
    name_v = (name or "").strip()
    fp_json = None
    if fingerprint is not None:
        fp_json = (
            fingerprint
            if isinstance(fingerprint, str)
            else json.dumps(fingerprint, ensure_ascii=False)
        )
    pwd_enc = (
        encrypt_dict({"muse_login_password": password}) if password else None
    )
    cookie_enc = _encrypt_cookies(cookies, exp)
    proxy_json = json.dumps(proxy, ensure_ascii=False) if proxy else None
    with _LOCK:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO pool_accounts(
                    id, label, email, name, password_enc, cookie_enc,
                    fingerprint_json, proxy_json, status, enabled, ok,
                    quota_cache, expires_at, use_count, note, signup_item_id,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    aid,
                    label_v,
                    email_v,
                    name_v,
                    pwd_enc,
                    cookie_enc,
                    fp_json,
                    proxy_json,
                    "unknown",
                    1 if enabled else 0,
                    None,
                    None,
                    expires_at,
                    0,
                    note or "",
                    signup_item_id,
                    now,
                    now,
                ),
            )
            # keep legacy meta in sync for older readers
            conn.execute(
                """INSERT INTO pool_account_meta(
                    upstream_id, email, name, password_enc, fingerprint_json,
                    signup_item_id, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(upstream_id) DO UPDATE SET
                    email=excluded.email,
                    name=excluded.name,
                    password_enc=COALESCE(excluded.password_enc, pool_account_meta.password_enc),
                    fingerprint_json=COALESCE(excluded.fingerprint_json, pool_account_meta.fingerprint_json),
                    signup_item_id=COALESCE(excluded.signup_item_id, pool_account_meta.signup_item_id),
                    updated_at=excluded.updated_at
                """,
                (aid, email_v, name_v, pwd_enc, fp_json, signup_item_id, now),
            )
    return get_public(aid) or {"id": aid}


def import_batch(pairs: list[tuple[str, str]], *, replace: bool = False) -> dict[str, Any]:
    """pairs: (label, cookie_header)."""
    added: list[dict[str, Any]] = []
    essential_missing = 0
    with _LOCK:
        if replace:
            for row in list_raw():
                delete(row["id"])
        for label, header in pairs:
            try:
                acc = add_account(
                    cookie_header=header,
                    label=label,
                    email=label if "@" in label else "",
                    note="导入 Cookie",
                )
                added.append(acc)
            except ValueError:
                essential_missing += 1
    return {
        "added": added,
        "count": len(added),
        "essential_missing": essential_missing,
        "warning": (
            f"{essential_missing} 行缺少 hatch_sess" if essential_missing else ""
        ),
    }


def update(
    aid: str,
    *,
    label: str | None = None,
    email: str | None = None,
    name: str | None = None,
    note: str | None = None,
    enabled: bool | None = None,
    password: str | None = None,
    cookie_header: str | None = None,
    cookies: dict[str, str] | None = None,
    cookies_exp: dict | None = None,
    proxy: dict[str, Any] | None = None,
    ok: bool | None = None,
    quota: Any = None,
    touch_used: bool = False,
    touch_keepalive: bool = False,
    bump_use: bool = False,
    checked: bool = False,
    expires_at: int | None = None,
) -> dict[str, Any] | None:
    row = get(aid)
    if not row:
        return None
    now = _now_iso()
    fields: dict[str, Any] = {"updated_at": now}
    if label is not None:
        fields["label"] = label
    if email is not None:
        fields["email"] = email
    if name is not None:
        fields["name"] = name
    if note is not None:
        fields["note"] = note
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
    if ok is not None:
        fields["ok"] = 1 if ok else 0
        fields["status"] = "ok" if ok else "invalid"
    if quota is not None:
        fields["quota_cache"] = json.dumps(quota, ensure_ascii=False)
    if proxy is not None:
        fields["proxy_json"] = json.dumps(proxy, ensure_ascii=False)
    if password is not None:
        fields["password_enc"] = (
            encrypt_dict({"muse_login_password": password}) if password else None
        )
    if cookie_header is not None or cookies is not None:
        cur_cookies, cur_exp = get_cookies(aid)
        new_cookies = dict(cookies) if cookies is not None else cur_cookies
        if cookie_header is not None:
            new_cookies = parse_cookie_header(cookie_header)
        new_exp = dict(cookies_exp) if cookies_exp is not None else cur_exp
        if not any(new_cookies.get(n) for n in ESSENTIAL_COOKIES):
            raise ValueError("missing_essential_cookie:hatch_sess")
        fields["cookie_enc"] = _encrypt_cookies(new_cookies, new_exp)
        fields["expires_at"] = account_expiry(new_exp)  # real Cookie expires only
    if expires_at is not None:
        fields["expires_at"] = expires_at
    if touch_used:
        fields["last_used_at"] = now
    if touch_keepalive:
        fields["last_keepalive_at"] = now
    if checked:
        fields["checked_at"] = now
    if bump_use:
        fields["use_count"] = int(row.get("use_count") or 0) + 1

    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [aid]
    with _LOCK:
        with get_db() as conn:
            conn.execute(f"UPDATE pool_accounts SET {cols} WHERE id=?", vals)
            # sync meta subset
            meta_email = fields.get("email", row.get("email"))
            meta_name = fields.get("name", row.get("name"))
            meta_pwd = fields.get("password_enc", row.get("password_enc"))
            conn.execute(
                """INSERT INTO pool_account_meta(
                    upstream_id, email, name, password_enc, fingerprint_json,
                    signup_item_id, updated_at
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(upstream_id) DO UPDATE SET
                    email=excluded.email,
                    name=excluded.name,
                    password_enc=COALESCE(excluded.password_enc, pool_account_meta.password_enc),
                    updated_at=excluded.updated_at
                """,
                (
                    aid,
                    meta_email,
                    meta_name,
                    meta_pwd,
                    row.get("fingerprint_json"),
                    row.get("signup_item_id"),
                    now,
                ),
            )
    return get_public(aid)


def delete(aid: str) -> bool:
    with _LOCK:
        with get_db() as conn:
            cur = conn.execute("DELETE FROM pool_accounts WHERE id=?", (aid,))
            conn.execute("DELETE FROM pool_account_meta WHERE upstream_id=?", (aid,))
            return cur.rowcount > 0




def _today() -> str:
    import datetime as _dt

    return _dt.datetime.now().strftime("%Y-%m-%d")


def global_daily_used() -> int:
    day = _today()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT daily_used, daily_reset_on FROM pool_accounts"
        ).fetchall()
    return sum(int(r["daily_used"] or 0) for r in rows if (r["daily_reset_on"] or "") == day)


def note_use(aid: str) -> None:
    day = _today()
    with get_db() as conn:
        row = conn.execute(
            "SELECT daily_used, daily_reset_on FROM pool_accounts WHERE id=?", (aid,)
        ).fetchone()
        if not row:
            return
        used = int(row["daily_used"] or 0)
        if (row["daily_reset_on"] or "") != day:
            used = 0
        conn.execute(
            "UPDATE pool_accounts SET use_count=use_count+1, daily_used=?, daily_reset_on=?, "
            "last_used_at=?, updated_at=? WHERE id=?",
            (used + 1, day, _now_iso(), _now_iso(), aid),
        )


def note_cooldown(aid: str, minutes: int, *, streak: bool = False,
                  disable_at: int = 0, cap_min: int = 0) -> str:
    """408/403 后冷却；连续次数达到阈值直接停用。返回 new state。"""
    minutes = max(0, int(minutes))
    if cap_min:
        minutes = min(minutes, int(cap_min))
    until = _now_ts() + minutes * 60
    with get_db() as conn:
        row = conn.execute(
            "SELECT e408_streak, enabled FROM pool_accounts WHERE id=?", (aid,)
        ).fetchone()
        if not row:
            return "missing"
        streak_val = int(row["e408_streak"] or 0) + (1 if streak else 0)
        disable = bool(disable_at and streak_val >= disable_at)
        conn.execute(
            "UPDATE pool_accounts SET cooldown_until=?, e408_streak=?, enabled=?, updated_at=? WHERE id=?",
            (until, streak_val, 0 if disable else int(row["enabled"] or 0), _now_iso(), aid),
        )
        return "disabled" if disable else "cooldown"


def reset_408(aid: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE pool_accounts SET e408_streak=0, cooldown_until=NULL WHERE id=?", (aid,)
        )


def pick_account(
    *,
    preferred_id: str | None = None,
    force_rotate: bool = False,
    exclude_ids: set[str] | None = None,
    min_interval_sec: int = 0,
    jitter_pct: int = 0,
    daily_quota: int = 0,
) -> dict[str, Any] | None:
    """Affinity sticky ~15 uses then LRU among enabled+cookie accounts.

    会跳过：冷却中 / 当日额度用尽 / 距上次使用不足最小间隔（含抖动）。
    """
    import datetime as _dt
    import random as _random

    now = _now_ts()
    day = _today()

    def _usable(r: dict[str, Any]) -> bool:
        if not r.get("enabled", 1):
            return False
        cd = r.get("cooldown_until")
        if cd and int(cd) > now:
            return False
        if daily_quota:
            used = int(r.get("daily_used") or 0)
            if (r.get("daily_reset_on") or "") != day:
                used = 0
            if used >= daily_quota:
                return False
        if min_interval_sec and r.get("last_used_at"):
            try:
                last = _dt.datetime.strptime(str(r["last_used_at"])[:19], "%Y-%m-%d %H:%M:%S")
                last_ts = last.replace(tzinfo=_dt.timezone.utc).timestamp()
                gap = int(min_interval_sec * (1 + _random.random() * max(0, jitter_pct) / 100))
                if now - last_ts < gap:
                    return False
            except Exception:
                pass
        return True

    with _LOCK:
        rows = [
            r
            for r in list_raw()
            if _usable(r)
        ]
        live = []
        for r in rows:
            cookies, _ = get_cookies(r["id"])
            if cookies:
                live.append(r)
        if not live:
            return None
        blocked = set(exclude_ids or [])
        available = [a for a in live if a["id"] not in blocked] or (
            [] if exclude_ids else live
        )
        if not available:
            return None
        live = available
        if preferred_id and not force_rotate:
            for a in live:
                if a["id"] == preferred_id and a.get("ok") is not False:
                    uses = int(a.get("use_count") or 0)
                    if uses % 15 != 0 or uses == 0:
                        update(a["id"], bump_use=True, touch_used=True)
                        return get_public(a["id"], include_secrets=True)
        # prefer ok is not False, then least recently used
        def sort_key(a: dict[str, Any]):
            bad = 1 if a.get("ok") is False or a.get("ok") == 0 else 0
            last = a.get("last_used_at") or ""
            return (bad, last, int(a.get("use_count") or 0))

        live_sorted = sorted(live, key=sort_key)
        chosen = live_sorted[0]
        update(chosen["id"], bump_use=True, touch_used=True)
        return get_public(chosen["id"], include_secrets=True)


def apply_session_renewal(
    aid: str,
    cookies: dict[str, str],
    cookies_exp: dict | None = None,
    *,
    ok: bool = True,
) -> None:
    update(
        aid,
        cookies=cookies,
        cookies_exp=cookies_exp,
        ok=ok,
        touch_keepalive=True,
        checked=True,
        expires_at=account_expiry(cookies_exp or {}),  # real Cookie expires only; None if unknown
    )
