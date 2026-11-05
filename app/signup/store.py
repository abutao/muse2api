"""SQLite store for signup_items / signup_task_logs."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db import get_db
from app.signup.crypto import decrypt_dict, encrypt_dict
from app.signup.import_parse import MailCreds, mask_email
from app.signup.fingerprint import fingerprint_label_zh


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return "s_" + uuid.uuid4().hex[:12]


def row_to_public(row: Any) -> dict[str, Any]:
    d = dict(row)
    profile = {}
    otp_meta = {}
    try:
        profile = json.loads(d.get("profile_json") or "{}")
    except json.JSONDecodeError:
        pass
    try:
        otp_meta = json.loads(d.get("otp_meta_json") or "{}")
    except json.JSONDecodeError:
        pass
    status = d.get("status") or "pending"
    needs: list[str] = []
    if status in ("awaiting_otp", "otp_sent"):
        needs.append("otp_code")
    if status in ("awaiting_profile", "completion_required"):
        needs.append("date_of_birth")
    if status == "awaiting_external_payment":
        needs.append("payment_external")
    if status in ("awaiting_card", "paying"):
        needs.append("card")
    if status == "awaiting_password_login":
        needs.append("password")
    if status == "awaiting_recovery_code":
        needs.append("recovery_code")
    if status == "awaiting_set_password":
        needs.append("new_password")
    if not d.get("mail_ready"):
        needs.append("email")

    payment_status = "none"
    if status == "awaiting_external_payment":
        payment_status = "awaiting"
    elif status in ("awaiting_card", "paying"):
        payment_status = "card_checkout"
    elif d.get("payment_ref"):
        payment_status = "resumed"

    card_info = None
    if d.get("card_id"):
        with get_db() as conn:
            crow = conn.execute(
                "SELECT id, brand, last4, status, last_error FROM cards WHERE id=?",
                (d["card_id"],),
            ).fetchone()
        if crow:
            card_info = {
                "id": crow["id"],
                "masked": f"{crow['brand']} •••• {crow['last4']}",
                "status": crow["status"],
                "last_error": crow["last_error"],
            }

    secret = decrypt_dict(d.get("secret_enc")) or {}
    meta = decrypt_dict(d.get("csrf_meta_enc")) or {}
    fp = meta.get("fingerprint") if isinstance(meta.get("fingerprint"), dict) else {}
    fp_id = (fp or {}).get("profile_id") if isinstance(fp, dict) else None
    has_login_pwd = bool(secret.get("muse_login_password"))
    has_cookie = bool(d.get("cookie_preview") or d.get("cookie_blob") or meta.get("hatch_sess"))
    pooled = status == "pooled" or bool(d.get("upstream_account_id"))
    # 注册结果摘要（对齐小AD：成功 / 设密 / Cookie / 指纹 / 入池）
    result_bits: list[str] = []
    if status in ("activated", "pooled") or d.get("activated_at"):
        result_bits.append("注册成功")
    if has_login_pwd:
        result_bits.append("已设密")
    if has_cookie:
        result_bits.append("有Cookie")
    fp_label = fingerprint_label_zh(fp_id) if fp_id else ""
    if fp_id:
        result_bits.append(f"指纹:{fp_label}")
    if pooled:
        result_bits.append("已入池")
    if status == "failed":
        result_bits = ["失败"] + ([d.get("last_error") or d.get("message") or ""][:40] and [str(d.get("last_error") or d.get("message") or "")[:40]])
    elif status == "awaiting_external_payment":
        result_bits.append("待外部支付")
    elif status == "awaiting_card":
        result_bits.append("待卡池支付")
    elif status == "paying":
        result_bits.append("支付中")
    result_summary = " · ".join([b for b in result_bits if b]) or (d.get("message") or "-")

    display_name = (
        profile.get("display_name")
        or " ".join(
            x for x in [profile.get("first_name") or "", profile.get("last_name") or ""] if x
        ).strip()
        or None
    )

    return {
        "id": d["id"],
        "email": d.get("email"),
        "email_masked": mask_email(d.get("email") or ""),
        "status": status,
        "ui_status": _ui_status(status),
        "region": d.get("region") or "US",
        "country": d.get("country") or d.get("region") or "US",
        "into_pool": bool(d.get("into_pool")),
        "mail_ready": bool(d.get("mail_ready")),
        "proxy": d.get("proxy"),
        "needs": needs,
        "profile": {
            "date_of_birth": profile.get("date_of_birth"),
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "display_name": profile.get("display_name"),
            "tos_content_version": profile.get("tos_content_version"),
            "remember_device": profile.get("remember_device", True),
            "note": profile.get("note"),
        },
        "display_name": display_name,
        "fingerprint_profile": fp_id,
        "fingerprint_label": fp_label if fp_id else None,
        "fingerprint": (
            {"profile_id": fp_id, "label_zh": fp_label}
            if fp_id
            else (fp or None)
        ),
        "has_muse_login_password": has_login_pwd,
        "otp": {
            "masked_contact": otp_meta.get("masked_contact")
            or mask_email(d.get("email") or ""),
            "sent_at": otp_meta.get("sent_at"),
        },
        "payment": {
            "status": payment_status,
            "payment_ref": d.get("payment_ref"),
            "provider": d.get("payment_provider"),
            "resumed_at": d.get("payment_resumed_at"),
        },
        "card": card_info,
        "card_masked": card_info["masked"] if card_info else None,
        "cookie_preview": d.get("cookie_preview"),
        "hasCookie": has_cookie,
        "upstream_account_id": d.get("upstream_account_id"),
        "pooled": pooled,
        "has_password": has_login_pwd or bool(
            (otp_meta.get("password_login_available") if isinstance(otp_meta, dict) else False)
        ),
        "password_login_available": bool(otp_meta.get("password_login_available")) if isinstance(otp_meta, dict) else False,
        "flow": otp_meta.get("flow") if isinstance(otp_meta, dict) else None,
        "tries": d.get("tries") or 0,
        "message": d.get("message") or "",
        "error": d.get("last_error"),
        "result_summary": result_summary,
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
        "activated_at": d.get("activated_at"),
        "hasAux": bool(secret.get("aux_email")),
        "aux_email_masked": mask_email(secret.get("aux_email") or "") if secret.get("aux_email") else "",
    }


def _ui_status(status: str) -> str:
    if status in ("pending",):
        return "pending"
    if status in (
        "restarting",
        "otp_sent",
        "awaiting_otp",
        "completion_required",
        "awaiting_profile",
        "bootstrapped",
        "post_payment",
        "activating",
        "running",
        "awaiting_recovery_code",
        "awaiting_set_password",
        "awaiting_password_login",
        "awaiting_card",
        "paying",
    ):
        return "running"
    if status == "awaiting_external_payment":
        return "awaiting_external_payment"
    if status == "geo_gated":
        return "geo_gated"
    if status in ("activated", "pooled"):
        return status
    if status in ("failed", "stopped"):
        return status
    return status


def get_item(item_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM signup_items WHERE id=?", (item_id,)
        ).fetchone()
    return dict(row) if row else None


def get_secret(item_id: str) -> dict[str, Any]:
    item = get_item(item_id)
    if not item:
        return {}
    return decrypt_dict(item.get("secret_enc"))


def merge_secret(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge keys into encrypted secret_enc (e.g. muse_login_password)."""
    item = get_item(item_id)
    if not item:
        return {}
    secret = decrypt_dict(item.get("secret_enc")) or {}
    secret.update({k: v for k, v in patch.items() if v is not None})
    update_item(item_id, secret_enc=encrypt_dict(secret))
    return secret


def get_session_meta(item_id: str) -> dict[str, Any]:
    item = get_item(item_id)
    if not item:
        return {}
    return decrypt_dict(item.get("csrf_meta_enc"))


def save_session_meta(item_id: str, meta: dict[str, Any]) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE signup_items SET csrf_meta_enc=?, updated_at=? WHERE id=?",
            (encrypt_dict(meta), _now(), item_id),
        )


def update_item(item_id: str, **fields: Any) -> None:
    if not fields:
        return
    # Never allow card columns
    banned = {
        "card_number",
        "pan",
        "cvv",
        "cvc",
        "expiry",
        "card_holder",
        "billing_address",
        "ptt",
        "jwe",
    }
    allowed = {"card_id"}  # pool reference only, never card data
    for k in fields:
        kl = k.lower()
        if kl in allowed:
            continue
        if kl in banned or kl.startswith("card_"):
            raise ValueError(f"forbidden_column:{k}")
    fields = dict(fields)
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [item_id]
    with get_db() as conn:
        conn.execute(f"UPDATE signup_items SET {cols} WHERE id=?", vals)


def upsert_from_creds(
    creds: MailCreds,
    *,
    region: str = "US",
    country: str | None = None,
    into_pool: bool = True,
) -> tuple[str, bool]:
    """Insert or update credentials by email. Returns (id, created)."""
    country = country or region
    secret = {
        "password": creds.password,
        "client_id": creds.client_id,
        "refresh_token": creds.refresh_token,
        "aux_email": creds.aux_email,
        "aux_password": creds.aux_password,
    }
    enc = encrypt_dict(secret)
    mail_ready = 1 if creds.mail_ready else 0
    now = _now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, status FROM signup_items WHERE lower(email)=lower(?)",
            (creds.email,),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE signup_items SET secret_enc=?, mail_ready=?, region=?, country=?,
                   into_pool=?, updated_at=?, message=? WHERE id=?""",
                (
                    enc,
                    mail_ready,
                    region,
                    country,
                    1 if into_pool else 0,
                    now,
                    "凭据已更新",
                    row["id"],
                ),
            )
            return row["id"], False
        iid = _new_id()
        conn.execute(
            """INSERT INTO signup_items(
                id, email, secret_enc, mail_ready, region, country, proxy, status,
                profile_json, otp_meta_json, payment_ref, payment_provider,
                payment_resumed_at, cookie_preview, cookie_blob, into_pool,
                upstream_account_id, tries, last_error, message, csrf_meta_enc,
                created_at, updated_at, activated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                iid,
                creds.email,
                enc,
                mail_ready,
                region,
                country,
                None,
                "pending",
                "{}",
                "{}",
                None,
                None,
                None,
                None,
                None,
                1 if into_pool else 0,
                None,
                0,
                None,
                "已导入",
                None,
                now,
                now,
                None,
            ),
        )
        return iid, True


def list_items(
    *,
    status: str = "all",
    q: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    clauses: list[str] = []
    args: list[Any] = []
    if status and status != "all":
        if status == "running":
            clauses.append(
                "status IN ('restarting','otp_sent','awaiting_otp','completion_required',"
                "'awaiting_profile','bootstrapped','post_payment','activating','running',"
                "'awaiting_recovery_code','awaiting_set_password','awaiting_password_login',"
                "'awaiting_card','paying')"
            )
        else:
            clauses.append("status=?")
            args.append(status)
    if q.strip():
        clauses.append("(email LIKE ? OR id LIKE ? OR message LIKE ?)")
        like = f"%{q.strip()}%"
        args.extend([like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM signup_items {where}", args
        ).fetchone()["c"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), pages)
        offset = (page - 1) * page_size
        rows = conn.execute(
            f"SELECT * FROM signup_items {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            args + [page_size, offset],
        ).fetchall()
        stats_rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM signup_items GROUP BY status"
        ).fetchall()
    stats = {r["status"]: r["c"] for r in stats_rows}
    return {
        "items": [row_to_public(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "stats": stats,
    }


def delete_items(ids: list[str]) -> int:
    if not ids:
        return 0
    with get_db() as conn:
        q = ",".join("?" * len(ids))
        cur = conn.execute(f"DELETE FROM signup_items WHERE id IN ({q})", ids)
        conn.execute(f"DELETE FROM signup_task_logs WHERE item_id IN ({q})", ids)
        return cur.rowcount


def add_log(item_id: str | None, level: str, message: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO signup_task_logs(item_id, level, message, created_at) VALUES(?,?,?,?)",
            (item_id, level, message, _now()),
        )
        return int(cur.lastrowid)


def list_logs(*, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, item_id, level, message, created_at FROM signup_task_logs "
            "WHERE id>? ORDER BY id ASC LIMIT ?",
            (after, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def clear_cookie_blob(item_id: str) -> None:
    update_item(item_id, cookie_blob=None)
