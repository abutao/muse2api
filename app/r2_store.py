"""Local R2 account pool CRUD (SQLite). No Adobe; secrets encrypted at rest."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db import get_db
from app.signup.crypto import decrypt_dict, encrypt_dict


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return "r2_" + uuid.uuid4().hex[:12]


def _public(row: Any, *, include_secret: bool = False) -> dict[str, Any]:
    d = dict(row)
    secret = decrypt_dict(d.get("secret_enc")) or {}
    out = {
        "id": d["id"],
        "name": d.get("name") or "",
        "bucket": d.get("bucket") or "",
        "prefix": d.get("prefix") or "",
        "region": d.get("region") or "auto",
        "endpoint": d.get("endpoint") or "",
        "accessKey": secret.get("access_key_id") or d.get("access_key_id") or "",
        "publicUrl": d.get("public_url") or "",
        "quotaBytes": int(d.get("quota") or 0),
        "usedBytes": int(d.get("used") or 0),
        "quota": int((int(d.get("quota") or 0) + (1 << 29)) >> 30),  # GB for UI
        "used": int((int(d.get("used") or 0) + (1 << 29)) >> 30),
        "objects": int(d.get("objects") or 0),
        "uploads": int(d.get("uploads") or 0),
        "fails": int(d.get("fails") or 0),
        "enabled": bool(d.get("enabled", 1)),
        "partial": bool(d.get("partial")),
        "usageAt": d.get("usage_at"),
        "usageErr": d.get("usage_err"),
        "lastErr": d.get("last_err"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }
    if include_secret:
        out["secretKeySet"] = bool(secret.get("secret_access_key"))
    return out


def list_accounts(
    *,
    status: str = "all",
    q: str = "",
    page: int = 1,
    size: int = 10,
) -> dict[str, Any]:
    where = ["1=1"]
    args: list[Any] = []
    st = (status or "all").lower()
    if st == "enabled":
        where.append("enabled=1")
    elif st == "disabled":
        where.append("enabled=0")
    elif st == "full":
        where.append("quota>0 AND used*100.0/quota >= 95")
    elif st == "error":
        where.append("(usage_err IS NOT NULL AND usage_err!='' OR last_err IS NOT NULL AND last_err!='')")
    if q:
        where.append(
            "(name LIKE ? OR bucket LIKE ? OR endpoint LIKE ? OR public_url LIKE ?)"
        )
        like = f"%{q}%"
        args.extend([like, like, like, like])
    clause = " AND ".join(where)
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM r2_accounts WHERE {clause}", args
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM r2_accounts WHERE {clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*args, size, (page - 1) * size],
        ).fetchall()
        all_rows = conn.execute(
            "SELECT enabled, quota, used, objects FROM r2_accounts"
        ).fetchall()
    items = [_public(r) for r in rows]
    quota = sum(int(r["quota"] or 0) for r in all_rows)
    used = sum(int(r["used"] or 0) for r in all_rows)
    objects = sum(int(r["objects"] or 0) for r in all_rows)
    enabled = sum(1 for r in all_rows if r["enabled"])
    usable = sum(
        1
        for r in all_rows
        if r["enabled"]
        and (not r["quota"] or (r["used"] or 0) / max(r["quota"], 1) < 0.95)
    )
    pages = max(1, (total + size - 1) // size)
    return {
        "ok": True,
        "items": items,
        "page": page,
        "pages": pages,
        "total": total,
        "summary": {
            "total": len(all_rows),
            "enabled": enabled,
            "disabled": len(all_rows) - enabled,
            "usable": usable,
            "full": sum(
                1
                for r in all_rows
                if r["quota"] and (r["used"] or 0) * 100.0 / max(r["quota"], 1) >= 95
            ),
            "error": 0,
            "quota": int((quota + (1 << 29)) >> 30),
            "used": int((used + (1 << 29)) >> 30),
            "objects": objects,
        },
        "refresh": _refresh_state(),
    }


_REFRESH: dict[str, Any] = {"running": False, "done": 0, "total": 0}


def _refresh_state() -> dict[str, Any]:
    return dict(_REFRESH)


def get(item_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM r2_accounts WHERE id=?", (item_id,)
        ).fetchone()
    return _public(row, include_secret=True) if row else None


def create(body: dict[str, Any]) -> dict[str, Any]:
    iid = _new_id()
    now = _now()
    secret = {
        "access_key_id": (body.get("accessKey") or body.get("access_key_id") or "").strip(),
        "secret_access_key": (
            body.get("secretKey") or body.get("secret_access_key") or ""
        ).strip(),
    }
    quota_gb = body.get("quotaGB")
    if quota_gb is None:
        quota = int(body.get("quota") or (10 << 30))
    else:
        quota = max(1, int(quota_gb)) << 30
    with get_db() as conn:
        conn.execute(
            """INSERT INTO r2_accounts(
                id, name, bucket, prefix, region, endpoint, public_url,
                access_key_id, secret_enc, quota, used, objects, uploads, fails,
                enabled, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0,0,?,?,?)""",
            (
                iid,
                (body.get("name") or "").strip() or iid,
                (body.get("bucket") or "").strip(),
                (body.get("prefix") or "generated").strip(),
                (body.get("region") or "auto").strip() or "auto",
                (body.get("endpoint") or "").strip(),
                (body.get("publicUrl") or body.get("public_base_url") or "").strip(),
                secret["access_key_id"],
                encrypt_dict(secret),
                quota,
                1 if body.get("enabled", True) else 0,
                now,
                now,
            ),
        )
    return get(iid)  # type: ignore[return-value]


def update(item_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM r2_accounts WHERE id=?", (item_id,)
        ).fetchone()
        if not row:
            return None
        secret = decrypt_dict(row["secret_enc"]) or {}
        if body.get("accessKey") or body.get("access_key_id"):
            secret["access_key_id"] = (
                body.get("accessKey") or body.get("access_key_id") or ""
            ).strip()
        sk = body.get("secretKey") or body.get("secret_access_key")
        if sk:
            secret["secret_access_key"] = str(sk).strip()
        fields = {
            "name": body.get("name"),
            "bucket": body.get("bucket"),
            "prefix": body.get("prefix"),
            "region": body.get("region"),
            "endpoint": body.get("endpoint"),
            "public_url": body.get("publicUrl") or body.get("public_base_url"),
            "access_key_id": secret.get("access_key_id"),
            "secret_enc": encrypt_dict(secret),
            "updated_at": _now(),
        }
        if "enabled" in body and body["enabled"] is not None:
            fields["enabled"] = 1 if body["enabled"] else 0
        if body.get("quotaGB") is not None:
            fields["quota"] = max(1, int(body["quotaGB"])) << 30
        elif body.get("quota") is not None:
            fields["quota"] = int(body["quota"])
        sets = []
        vals: list[Any] = []
        for k, v in fields.items():
            if v is None and k not in ("secret_enc", "access_key_id", "updated_at"):
                continue
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(item_id)
        conn.execute(
            f"UPDATE r2_accounts SET {', '.join(sets)} WHERE id=?", vals
        )
    return get(item_id)


def delete_ids(ids: list[str]) -> int:
    if not ids:
        return 0
    q = ",".join("?" * len(ids))
    with get_db() as conn:
        cur = conn.execute(f"DELETE FROM r2_accounts WHERE id IN ({q})", ids)
        return cur.rowcount


def batch(ids: list[str], action: str) -> int:
    action = (action or "").lower()
    if action == "delete":
        return delete_ids(ids)
    if action in ("enable", "disable"):
        en = 1 if action == "enable" else 0
        if not ids:
            return 0
        q = ",".join("?" * len(ids))
        with get_db() as conn:
            cur = conn.execute(
                f"UPDATE r2_accounts SET enabled=?, updated_at=? WHERE id IN ({q})",
                [en, _now(), *ids],
            )
            return cur.rowcount
    return 0


def import_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json:{exc}") from exc
    if isinstance(data, dict):
        accounts = data.get("accounts") or data.get("items") or []
    else:
        accounts = data
    if not isinstance(accounts, list):
        raise ValueError("accounts_must_be_list")
    added = updated = skipped = 0
    errors: list[str] = []
    with get_db() as conn:
        for raw in accounts:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            name = (raw.get("name") or "").strip()
            bucket = (raw.get("bucket") or "").strip()
            if not name or not bucket:
                skipped += 1
                continue
            existing = conn.execute(
                "SELECT id FROM r2_accounts WHERE name=?", (name,)
            ).fetchone()
            body = {
                "name": name,
                "bucket": bucket,
                "prefix": raw.get("prefix") or "generated",
                "region": raw.get("region") or "auto",
                "endpoint": raw.get("endpoint") or "",
                "accessKey": raw.get("access_key_id") or raw.get("accessKey") or "",
                "secretKey": raw.get("secret_access_key") or raw.get("secretKey") or "",
                "publicUrl": raw.get("public_base_url")
                or raw.get("publicUrl")
                or "",
                "enabled": raw.get("enabled", True),
                "quotaGB": raw.get("quotaGB")
                or (
                    int(raw["quota"]) >> 30
                    if isinstance(raw.get("quota"), int) and raw.get("quota")
                    else 10
                ),
            }
            try:
                if existing:
                    update(existing["id"], body)
                    updated += 1
                else:
                    create(body)
                    added += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                skipped += 1
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped, "imported": added + updated, "errors": errors}


def start_refresh(ids: list[str] | None = None) -> dict[str, Any]:
    """Mock capacity refresh — marks usage_at; no real Cloudflare call required."""
    if _REFRESH.get("running"):
        return {"started": False, **_refresh_state()}
    with get_db() as conn:
        if ids:
            q = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id, quota, used FROM r2_accounts WHERE id IN ({q})", ids
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, quota, used FROM r2_accounts").fetchall()
    _REFRESH.update(running=True, done=0, total=len(rows))
    now = _now()
    with get_db() as conn:
        for r in rows:
            # Soft bump: keep used, clear errors, stamp usage_at
            used = int(r["used"] or 0)
            quota = int(r["quota"] or (10 << 30))
            if used <= 0:
                used = min(quota // 20, 1 << 20)  # tiny mock usage
            conn.execute(
                """UPDATE r2_accounts SET used=?, usage_at=?, usage_err=NULL,
                   partial=0, updated_at=? WHERE id=?""",
                (used, now, now, r["id"]),
            )
            _REFRESH["done"] = int(_REFRESH["done"]) + 1
    _REFRESH["running"] = False
    return {"started": True, "refreshed": len(rows), "imported": 0, **_refresh_state()}
