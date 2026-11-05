"""Local enrichment for upstream号池账号（邮箱 / 名字 / 密码 / 注册指纹）。

上游 muse2api 只存 label+cookie；补号成功入池后把邮箱、随机名字、登录密码、
抓包指纹快照写到本地，供号池编辑页展示。指纹只读，不可通过 PATCH 改写。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.db import get_db
from app.signup.crypto import decrypt_dict, encrypt_dict
from app.signup.fingerprint import fingerprint_label_zh


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def get(upstream_id: str) -> dict[str, Any] | None:
    if not upstream_id:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM pool_account_meta WHERE upstream_id=?", (upstream_id,)
        ).fetchone()
    return dict(row) if row else None


def get_many(ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [str(x) for x in ids if x]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM pool_account_meta WHERE upstream_id IN ({q})", ids
        ).fetchall()
    return {r["upstream_id"]: dict(r) for r in rows}


def upsert(
    upstream_id: str,
    *,
    email: str | None = None,
    name: str | None = None,
    password: str | None = None,
    fingerprint: dict[str, Any] | list[Any] | str | None = None,
    signup_item_id: str | None = None,
    clear_password: bool = False,
) -> None:
    if not upstream_id:
        return
    existing = get(upstream_id) or {}
    email_v = email if email is not None else existing.get("email")
    name_v = name if name is not None else existing.get("name")
    signup_v = (
        signup_item_id if signup_item_id is not None else existing.get("signup_item_id")
    )
    if clear_password:
        pwd_enc = None
    elif password is not None:
        pwd_enc = encrypt_dict({"muse_login_password": password}) if password else None
    else:
        pwd_enc = existing.get("password_enc")

    if fingerprint is None:
        fp_json = existing.get("fingerprint_json")
    elif isinstance(fingerprint, str):
        fp_json = fingerprint
    else:
        fp_json = json.dumps(fingerprint, ensure_ascii=False)

    now = _now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO pool_account_meta(
                upstream_id, email, name, password_enc, fingerprint_json,
                signup_item_id, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(upstream_id) DO UPDATE SET
                email=excluded.email,
                name=excluded.name,
                password_enc=excluded.password_enc,
                fingerprint_json=excluded.fingerprint_json,
                signup_item_id=COALESCE(excluded.signup_item_id, pool_account_meta.signup_item_id),
                updated_at=excluded.updated_at
            """,
            (upstream_id, email_v, name_v, pwd_enc, fp_json, signup_v, now),
        )


def public_view(
    upstream_id: str, *, include_password: bool = False
) -> dict[str, Any]:
    """SPA fields. Password only when editing; fingerprint always readonly."""
    row = get(upstream_id)
    out: dict[str, Any] = {
        "email": "",
        "name": "",
        "has_password": False,
        "password": "",
        "fingerprint": None,
        "fingerprint_text": "",
        "signup_item_id": None,
        "fingerprint_readonly": True,
    }
    if not row:
        return out
    out["email"] = row.get("email") or ""
    out["name"] = row.get("name") or ""
    out["signup_item_id"] = row.get("signup_item_id")
    secret = decrypt_dict(row.get("password_enc")) if row.get("password_enc") else {}
    pwd = (secret or {}).get("muse_login_password") or ""
    out["has_password"] = bool(pwd)
    if include_password:
        out["password"] = pwd
    fp_raw = row.get("fingerprint_json") or ""
    fp_obj: Any = None
    if fp_raw:
        try:
            fp_obj = json.loads(fp_raw)
        except json.JSONDecodeError:
            fp_obj = fp_raw
    out["fingerprint"] = fp_obj
    if isinstance(fp_obj, dict):
        pid = fp_obj.get("profile_id") or ""
        ua = (fp_obj.get("user_agent") or fp_obj.get("ua") or "")[:80]
        out["fingerprint_text"] = (
            json.dumps(fp_obj, ensure_ascii=False, indent=2)
            if fp_obj
            else ""
        )
        out["fingerprint_profile"] = pid
        label = fingerprint_label_zh(pid) if pid else ""
        out["fingerprint_label"] = label or None
        out["fingerprint_summary"] = label or (ua[:40] + "…" if ua else "已记录")
    elif fp_obj:
        out["fingerprint_text"] = str(fp_obj)
        out["fingerprint_summary"] = str(fp_obj)[:60]
    return out


def delete(upstream_id: str) -> None:
    if not upstream_id:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM pool_account_meta WHERE upstream_id=?", (upstream_id,))


def hydrate_from_signup(upstream_id: str) -> dict[str, Any]:
    """若本地无元数据，按补号 upstream_account_id 回填邮箱/名字/密码/指纹。"""
    if not upstream_id:
        return public_view(upstream_id)
    existing = get(upstream_id)
    if existing and (existing.get("email") or existing.get("fingerprint_json")):
        return public_view(upstream_id, include_password=False)
    from app.signup import store as signup_store
    from app.signup.crypto import decrypt_dict

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM signup_items WHERE upstream_account_id=? ORDER BY updated_at DESC LIMIT 1",
            (upstream_id,),
        ).fetchone()
    if not row:
        # 也尝试 label=邮箱：上游 id 对不上时不硬猜
        return public_view(upstream_id)
    d = dict(row)
    email = d.get("email") or ""
    profile = {}
    try:
        profile = json.loads(d.get("profile_json") or "{}")
    except json.JSONDecodeError:
        profile = {}
    name = (
        profile.get("display_name")
        or " ".join(
            x for x in [profile.get("first_name") or "", profile.get("last_name") or ""] if x
        ).strip()
    )
    secret = decrypt_dict(d.get("secret_enc")) or {}
    meta = decrypt_dict(d.get("csrf_meta_enc")) or {}
    fp = meta.get("fingerprint") if isinstance(meta.get("fingerprint"), dict) else None
    upsert(
        upstream_id,
        email=email or None,
        name=name or None,
        password=secret.get("muse_login_password") or None,
        fingerprint=fp,
        signup_item_id=d.get("id"),
    )
    return public_view(upstream_id)


def hydrate_for_account(upstream_id: str, *, label: str | None = None, email_hint: str | None = None) -> dict[str, Any]:
    """入池展示用：优先本地，其次补号 upstream_id，再按邮箱/标签匹配。"""
    view = hydrate_from_signup(upstream_id)
    if view.get("email"):
        return view
    hint = (email_hint or label or "").strip()
    if not hint:
        return view
    from app.signup.crypto import decrypt_dict

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM signup_items WHERE email=? OR email LIKE ? ORDER BY updated_at DESC LIMIT 1",
            (hint, hint + "@%"),
        ).fetchone()
    if not row:
        return view
    d = dict(row)
    email = d.get("email") or ""
    profile = {}
    try:
        profile = json.loads(d.get("profile_json") or "{}")
    except json.JSONDecodeError:
        profile = {}
    name = (
        profile.get("display_name")
        or " ".join(
            x for x in [profile.get("first_name") or "", profile.get("last_name") or ""] if x
        ).strip()
    )
    secret = decrypt_dict(d.get("secret_enc")) or {}
    meta = decrypt_dict(d.get("csrf_meta_enc")) or {}
    fp = meta.get("fingerprint") if isinstance(meta.get("fingerprint"), dict) else None
    upsert(
        upstream_id,
        email=email or None,
        name=name or None,
        password=secret.get("muse_login_password") or None,
        fingerprint=fp,
        signup_item_id=d.get("id"),
    )
    # 回写补号上的 upstream id，避免下次再猜
    try:
        from app.signup import store as signup_store
        if not d.get("upstream_account_id"):
            signup_store.update_item(d["id"], upstream_account_id=upstream_id)
    except Exception:
        pass
    return public_view(upstream_id)
