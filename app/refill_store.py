"""SQLite store for refill_items (email materials only — no cards)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db import get_db
from app.signup.crypto import decrypt_dict, encrypt_dict
from app.signup.import_parse import MailCreds, mask_email


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return "r_" + uuid.uuid4().hex[:12]


def row_to_public(row: Any) -> dict[str, Any]:
    d = dict(row)
    return {
        "id": d["id"],
        "email": d.get("email"),
        "email_masked": mask_email(d.get("email") or ""),
        "status": d.get("status") or "pending",
        "mode": d.get("mode") or "signup",
        "region": d.get("region") or "US",
        "into_pool": bool(d.get("into_pool")),
        "mail_ready": bool(d.get("mail_ready")),
        "cookie_preview": d.get("cookie_preview"),
        "upstream_account_id": d.get("upstream_account_id"),
        "signup_item_id": d.get("signup_item_id"),
        "tries": d.get("tries") or 0,
        "message": d.get("message") or "",
        "error": d.get("last_error"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def import_creds(
    creds: list[MailCreds],
    *,
    region: str = "US",
    into_pool: bool = True,
    mode: str = "signup",
) -> dict[str, Any]:
    created = 0
    updated = 0
    now = _now()
    with get_db() as conn:
        for c in creds:
            secret = encrypt_dict(
                {
                    "password": c.password,
                    "client_id": c.client_id,
                    "refresh_token": c.refresh_token,
                    "aux_email": c.aux_email,
                    "aux_password": c.aux_password,
                }
            )
            existing = conn.execute(
                "SELECT id FROM refill_items WHERE lower(email)=lower(?) LIMIT 1",
                (c.email,),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE refill_items SET secret_enc=?, mail_ready=?, region=?, "
                    "into_pool=?, mode=?, status='pending', last_error=NULL, message='reimported', "
                    "updated_at=? WHERE id=?",
                    (
                        secret,
                        1 if c.mail_ready else 0,
                        region,
                        1 if into_pool else 0,
                        mode,
                        now,
                        existing["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO refill_items(id,email,secret_enc,mail_ready,region,into_pool,mode,"
                    "status,tries,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _new_id(),
                        c.email,
                        secret,
                        1 if c.mail_ready else 0,
                        region,
                        1 if into_pool else 0,
                        mode,
                        "pending",
                        0,
                        "imported",
                        now,
                        now,
                    ),
                )
                created += 1
    return {"created": created, "updated": updated, "total": created + updated}


def list_items(
    *,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    where = ["1=1"]
    args: list[Any] = []
    if status:
        where.append("status=?")
        args.append(status)
    if q:
        where.append("(email LIKE ? OR id LIKE ? OR upstream_account_id LIKE ?)")
        like = f"%{q}%"
        args.extend([like, like, like])
    wh = " AND ".join(where)
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM refill_items WHERE {wh}", args
        ).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM refill_items WHERE {wh} ORDER BY updated_at DESC "
            f"LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [row_to_public(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_item(item_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM refill_items WHERE id=?", (item_id,)
        ).fetchone()
    return row_to_public(row) if row else None


def delete_ids(ids: list[str]) -> int:
    if not ids:
        return 0
    with get_db() as conn:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(f"DELETE FROM refill_items WHERE id IN ({marks})", ids)
        return cur.rowcount


def update_item(item_id: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {
        "status",
        "message",
        "last_error",
        "tries",
        "signup_item_id",
        "upstream_account_id",
        "cookie_preview",
        "into_pool",
        "region",
        "mode",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return get_item(item_id)
    cols = ", ".join(f"{k}=?" for k in patch)
    vals = list(patch.values())
    with get_db() as conn:
        conn.execute(
            f"UPDATE refill_items SET {cols}, updated_at=? WHERE id=?",
            [*vals, _now(), item_id],
        )
    return get_item(item_id)


def load_secret(item_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT secret_enc FROM refill_items WHERE id=?", (item_id,)
        ).fetchone()
    if not row or not row["secret_enc"]:
        return None
    return decrypt_dict(row["secret_enc"])


def stats() -> dict[str, int]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM refill_items GROUP BY status"
        ).fetchall()
    out = {r["status"]: r["c"] for r in rows}
    out["total"] = sum(out.values())
    return out
