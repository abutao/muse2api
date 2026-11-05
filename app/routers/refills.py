"""补号系统 = Muse 注册（无卡）+ 号池保活。底层复用 signup 模块。"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import jobs_keepalive
from app.auth_admin import get_session_user, require_admin
from app.config import get_settings
from app.signup import machine, runner, store
from app.signup.guard import reject_card_fields
from app.signup.html_sanitize import sanitize_html
from app.signup.import_parse import parse_import_text
from app.signup.protocol import MuseProtocolError, get_muse_client

logger = logging.getLogger("muse-proxy.refills")
router = APIRouter(prefix="/refills", tags=["refills"])


def _auth_admin_or_webhook(request: Request, authorization: str | None) -> str:
    user = get_session_user(request)
    if user:
        return user
    settings = get_settings()
    token = (settings.signup_webhook_token or "").strip()
    if token and authorization:
        raw = authorization
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if raw == token:
            return "webhook"
    raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "未授权"})


def _raw_body_dict(body: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(body, BaseModel):
        return body.model_dump(exclude_none=False)
    return dict(body)


# ---------- list / import / batch / task ----------


@router.get("")
async def list_refills(
    request: Request,
    status: str = Query(default="all"),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    size: int | None = Query(default=None),
):
    require_admin(request)
    ps = size or page_size
    data = store.list_items(status=status, q=q, page=page, page_size=ps)
    stats = data.get("stats") or {}
    summary = {
        "total": data.get("total") or 0,
        "pending": stats.get("pending") or 0,
        "running": sum(
            int(stats.get(k) or 0)
            for k in (
                "running",
                "restarting",
                "otp_sent",
                "awaiting_otp",
                "completion_required",
                "awaiting_profile",
                "bootstrapped",
                "post_payment",
                "activating",
                "awaiting_recovery_code",
                "awaiting_set_password",
                "awaiting_password_login",
            )
        ),
        "activated": (stats.get("activated") or 0) + (stats.get("already_activated") or 0),
        "failed": stats.get("failed") or 0,
        "pooled": stats.get("pooled") or 0,
        "awaiting_external_payment": stats.get("awaiting_external_payment") or 0,
    }
    return {"ok": True, **data, "summary": summary, "stats": stats}


class ImportBody(BaseModel):
    text: str = ""
    region: str = "US"
    country: str | None = None
    into_pool: bool = True
    intoPool: bool | None = None
    mode: str | None = None  # ignored; always Muse signup


@router.post("/import")
async def import_refills(body: ImportBody, request: Request):
    require_admin(request)
    reject_card_fields(_raw_body_dict(body))
    into = body.into_pool if body.intoPool is None else bool(body.intoPool)
    region = body.country or body.region or get_settings().default_region or "US"
    creds = parse_import_text(body.text)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_import",
                "message": "无有效行；需 6 段或 4 段：邮箱----密码----ClientID----RefreshToken[----辅邮----辅密]",
            },
        )
    created = updated = 0
    ids = []
    for c in creds:
        iid, is_new = store.upsert_from_creds(
            c, region=region, country=body.country or region, into_pool=into
        )
        ids.append(iid)
        if is_new:
            created += 1
        else:
            updated += 1
    store.add_log(None, "info", f"补号导入 {created} 新建 / {updated} 更新")
    return {
        "ok": True,
        "added": created,
        "updated": updated,
        "total": created + updated,
        "ids": ids,
        "invalid": 0,
        "linked": 0,
    }


class BatchBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False
    action: Literal["activate", "retry", "reset", "delete"] = "activate"
    country: str | None = None
    region: str | None = None
    intoPool: bool | None = None
    into_pool: bool | None = None
    status: str | None = None
    limit: int = 0            # 一键补号数量上限（0 = 全部）
    concurrency: int | None = None  # 1-10；空 = 用系统设置 signup_concurrency
    scope: str | None = None  # delete: failed / all / selected


@router.post("/batch")
async def batch_refills(body: BatchBody, request: Request):
    require_admin(request)
    reject_card_fields(_raw_body_dict(body))
    ids = [str(x) for x in (body.ids or []) if str(x).strip()]
    from app.db import get_db

    if body.all and not ids:
        with get_db() as conn:
            if body.status == "pending":
                ids = [
                    r["id"]
                    for r in conn.execute(
                        "SELECT id FROM signup_items WHERE status='pending' ORDER BY created_at LIMIT ?",
                        (-1 if body.limit <= 0 else body.limit,),
                    ).fetchall()
                ]
            else:
                ids = [r["id"] for r in conn.execute("SELECT id FROM signup_items").fetchall()]
    if body.limit and body.limit > 0:
        ids = ids[: body.limit]

    if body.action == "delete":
        if body.scope in ("failed", "all"):
            with get_db() as conn:
                if body.scope == "failed":
                    rows = conn.execute(
                        "SELECT id FROM signup_items WHERE status IN ('failed','stopped','geo_gated')"
                    ).fetchall()
                else:
                    rows = conn.execute("SELECT id FROM signup_items").fetchall()
            ids = [r["id"] for r in rows]
        n = store.delete_items(ids)
        return {"ok": True, "affected": n, "action": "delete", "deleted": n, "scope": body.scope}

    into = body.into_pool if body.into_pool is not None else body.intoPool
    for iid in ids:
        item = store.get_item(iid)
        if not item:
            continue
        patch: dict[str, Any] = {}
        if body.region:
            patch["region"] = body.region
            patch["country"] = body.country or body.region
        if body.country:
            patch["country"] = body.country
        if into is not None:
            patch["into_pool"] = 1 if into else 0
        if body.action == "reset":
            patch.update(
                status="pending",
                last_error=None,
                message="已重置",
                payment_ref=None,
                payment_provider=None,
                payment_resumed_at=None,
                cookie_blob=None,
                cookie_preview=None,
                upstream_account_id=None,
                csrf_meta_enc=None,
            )
        elif body.action == "retry":
            if item.get("status") in ("failed", "stopped"):
                patch["status"] = "pending"
                patch["message"] = "重试排队"
                patch["last_error"] = None
        if patch:
            store.update_item(iid, **patch)

    if body.action in ("activate", "retry"):
        run_ids = []
        for iid in ids:
            item = store.get_item(iid)
            if not item:
                continue
            if item.get("status") in ("pooled", "activated", "awaiting_external_payment"):
                continue
            if item.get("status") == "failed":
                store.update_item(iid, status="pending", last_error=None, message="重试")
            run_ids.append(iid)
        conc = body.concurrency
        if conc is None:
            from app.config import get_settings

            conc = get_settings().signup_concurrency
        started = runner.start_batch(run_ids, concurrency=conc)
        return {
            "ok": True,
            "action": body.action,
            "queued": len(run_ids),
            "count": len(run_ids),
            **started,
        }

    return {"ok": True, "action": body.action, "affected": len(ids)}


@router.post("/stop")
async def stop_refills(request: Request):
    require_admin(request)
    out = runner.stop_batch()
    try:
        jobs_keepalive.request_stop()
    except Exception:
        pass
    return {**out, "ok": True}


@router.get("/task")
async def refill_task(request: Request, after: int = Query(default=0, ge=0)):
    require_admin(request)
    snap = runner.task_snapshot(after=after)
    job = jobs_keepalive.latest_job()
    # snap.ok 是成功计数；另用 success 布尔给通用包装
    return {
        **snap,
        "success": True,
        "keepalive": job,
        "logs": snap.get("logs") or [],
    }


@router.get("/export")
async def export_refills(request: Request):
    require_admin(request)
    from app.db import get_db

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM signup_items ORDER BY created_at").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "email",
            "status",
            "mail_ready",
            "region",
            "into_pool",
            "payment_ref",
            "upstream_account_id",
            "message",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r["id"],
                r["email"],
                r["status"],
                r["mail_ready"],
                r["region"],
                r["into_pool"],
                r["payment_ref"],
                r["upstream_account_id"],
                r["message"],
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="refills-export.csv"'},
    )


# ---------- keepalive (号池) ----------


class KeepaliveBody(BaseModel):
    action: str = "keepalive"
    scope: str = "all"
    account_ids: list[str] = Field(default_factory=list)


@router.post("/keepalive")
async def start_keepalive(body: KeepaliveBody, request: Request):
    require_admin(request)
    try:
        job = await jobs_keepalive.start_job(
            body.action, scope=body.scope, account_ids=body.account_ids
        )
    except ValueError as exc:
        raise HTTPException(400, detail={"error": str(exc), "message": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(409, detail={"error": str(exc), "message": str(exc)}) from exc
    return {"ok": True, "job": job}


@router.get("/keepalive/{job_id}")
async def get_keepalive(job_id: str, request: Request):
    require_admin(request)
    job = jobs_keepalive.get_job(job_id)
    if not job:
        raise HTTPException(404, detail={"error": "not_found", "message": "任务不存在"})
    return {"ok": True, "job": job, "logs": jobs_keepalive.list_logs(job_id, limit=200)}


@router.get("/upstream-accounts")
async def upstream_accounts_brief(request: Request):
    """Deprecated alias — returns local pool accounts (no 19080)."""
    require_admin(request)
    from app.pool import store as pool_store

    rows = pool_store.list_public(status="all")
    accounts = [
        {
            "id": a.get("id"),
            "label": a.get("label") or a.get("email") or a.get("id"),
            "enabled": bool(a.get("enabled")),
            "ok": a.get("ok"),
        }
        for a in rows
    ]
    return {"ok": True, "accounts": accounts, "stats": pool_store.stats()}


# ---------- per-item (signup fill APIs) ----------


@router.get("/{item_id}")
async def get_item(
    item_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    return {"ok": True, "item": store.row_to_public(item)}


class ProfileBody(BaseModel):
    date_of_birth: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    region: str | None = None
    country: str | None = None
    proxy: str | None = None
    note: str | None = None
    remember_device: bool | None = True
    tos_content_version: str | None = None
    auto_fill: bool | None = True
    card_number: str | None = None
    pan: str | None = None
    cvv: str | None = None
    cvc: str | None = None
    expiry: str | None = None


@router.post("/{item_id}/profile")
async def post_profile(
    item_id: str,
    body: ProfileBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    raw = body.model_dump(exclude_none=False)
    reject_card_fields(raw)
    try:
        extra = await request.json()
    except Exception:
        extra = {}
    if isinstance(extra, dict):
        reject_card_fields(extra)
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_profile(
        item_id,
        {
            "date_of_birth": body.date_of_birth,
            "first_name": body.first_name,
            "last_name": body.last_name,
            "display_name": body.display_name,
            "region": body.region,
            "country": body.country,
            "proxy": body.proxy,
            "note": body.note,
            "remember_device": body.remember_device,
            "tos_content_version": body.tos_content_version,
            "auto_fill": body.auto_fill if body.auto_fill is not None else True,
        },
    )
    item = store.get_item(item_id)
    return {"ok": True, "result": result, "item": store.row_to_public(item) if item else None}


class OtpBody(BaseModel):
    otp_code: str | None = None
    code: str | None = None
    remember_device: bool = True


@router.post("/{item_id}/otp")
async def post_otp(
    item_id: str,
    body: OtpBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    reject_card_fields(body.model_dump())
    code = (body.otp_code or body.code or "").strip()
    if not code:
        raise HTTPException(400, detail={"error": "otp_required", "message": "需要验证码"})
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_otp(item_id, code, remember_device=body.remember_device)
    item = store.get_item(item_id)
    return {"ok": True, "result": result, "item": store.row_to_public(item) if item else None}


class ResumeBody(BaseModel):
    payment_ref: str
    provider: str = "external"
    note: str | None = None
    card_number: str | None = None
    cvv: str | None = None
    pan: str | None = None


@router.post("/{item_id}/resume")
async def post_resume(
    item_id: str,
    body: ResumeBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    reject_card_fields(body.model_dump(exclude_none=False))
    try:
        extra = await request.json()
    except Exception:
        extra = {}
    if isinstance(extra, dict):
        reject_card_fields(extra)
    if not (body.payment_ref or "").strip():
        raise HTTPException(
            400, detail={"error": "payment_ref_required", "message": "需要 payment_ref"}
        )
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_resume(
        item_id,
        body.payment_ref.strip(),
        provider=body.provider or "external",
        note=body.note,
    )
    if not result.get("ok") and result.get("error") == "invalid_status":
        raise HTTPException(status_code=409, detail=result)
    item = store.get_item(item_id)
    return {"ok": True, "result": result, "item": store.row_to_public(item) if item else None}


class CookieBody(BaseModel):
    cookie_header: str = ""
    cookie: str | None = None
    into_pool: bool | None = None
    intoPool: bool | None = None


@router.post("/{item_id}/cookie")
async def post_cookie(item_id: str, body: CookieBody, request: Request):
    require_admin(request)
    reject_card_fields(body.model_dump(exclude_none=False))
    ck = (body.cookie_header or body.cookie or "").strip()
    if not ck:
        raise HTTPException(400, detail={"error": "empty_cookie", "message": "Cookie 为空"})
    into = body.into_pool if body.into_pool is not None else body.intoPool
    result = await machine.apply_cookie(item_id, ck, into_pool=into)
    if result.get("error") == "not_found":
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result.pop("cookie_header", None)
    return result


class RecoveryCodeBody(BaseModel):
    recovery_code: str = ""
    code: str | None = None


@router.post("/{item_id}/recovery/start")
async def post_recovery_start(
    item_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.start_recovery(item_id)
    item = store.get_item(item_id)
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "item": store.row_to_public(item) if item else None,
    }


@router.post("/{item_id}/recovery/code")
async def post_recovery_code(
    item_id: str,
    body: RecoveryCodeBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    reject_card_fields(body.model_dump(exclude_none=False))
    code = (body.recovery_code or body.code or "").strip()
    if not code:
        raise HTTPException(400, detail={"error": "recovery_code_required", "message": "需要恢复码"})
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_recovery_code(item_id, code)
    item = store.get_item(item_id)
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "item": store.row_to_public(item) if item else None,
    }


class SetPasswordBody(BaseModel):
    new_password: str = ""
    password: str | None = None
    logout_other_sessions: bool = True


@router.post("/{item_id}/recovery/password")
async def post_set_password(
    item_id: str,
    body: SetPasswordBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    reject_card_fields(body.model_dump(exclude_none=False))
    try:
        extra = await request.json()
    except Exception:
        extra = {}
    if isinstance(extra, dict):
        reject_card_fields(extra)
    pwd = (body.new_password or body.password or "").strip()
    # 空密码交给状态机随机生成；若提供则至少 6 位
    if pwd and len(pwd) < 6:
        raise HTTPException(400, detail={"error": "weak_password", "message": "密码至少 6 位"})
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_set_password(
        item_id, pwd, logout_other_sessions=body.logout_other_sessions
    )
    if result.get("error") == "invalid_status":
        raise HTTPException(status_code=409, detail=result)
    item = store.get_item(item_id)
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "item": store.row_to_public(item) if item else None,
    }


@router.post("/{item_id}/password/start")
async def post_password_start(
    item_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.start_password_login(item_id)
    item = store.get_item(item_id)
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "item": store.row_to_public(item) if item else None,
    }


class PasswordLoginBody(BaseModel):
    password: str
    remember_device: bool = True


@router.post("/{item_id}/password")
async def post_password_login(
    item_id: str,
    body: PasswordLoginBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    reject_card_fields(body.model_dump(exclude_none=False))
    try:
        extra = await request.json()
    except Exception:
        extra = {}
    if isinstance(extra, dict):
        reject_card_fields(extra)
    if not (body.password or "").strip():
        raise HTTPException(400, detail={"error": "password_required", "message": "需要密码"})
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_password_login(
        item_id, body.password.strip(), remember_device=body.remember_device
    )
    item = store.get_item(item_id)
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "item": store.row_to_public(item) if item else None,
    }


class InviteRedeemBody(BaseModel):
    code: str


@router.post("/{item_id}/invite/redeem")
async def post_invite_redeem(
    item_id: str,
    body: InviteRedeemBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    reject_card_fields(body.model_dump(exclude_none=False))
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(400, detail={"error": "code_required", "message": "需要邀请码"})
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    result = await machine.apply_invite_redeem(item_id, code)
    if result.get("error") == "invalid_status":
        raise HTTPException(status_code=409, detail=result)
    item = store.get_item(item_id)
    return {
        "ok": bool(result.get("ok")),
        "result": result,
        "item": store.row_to_public(item) if item else None,
    }


@router.get("/{item_id}/invite")
async def get_invite(
    item_id: str,
    request: Request,
    create: bool = Query(default=False),
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    if not store.get_item(item_id):
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    return await machine.invite_panel(item_id, create=create)


@router.get("/{item_id}/mail/code")
async def get_mail_code(
    item_id: str,
    request: Request,
    kind: str = Query(default="otp", pattern="^(otp|recovery)$"),
):
    """从邮箱物料里抓取验证码（不等待；注册流程内部会自动等）。"""
    require_admin(request)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    secret = store.get_secret(item_id)
    client = get_muse_client()
    lengths = (6,) if kind == "otp" else (6, 8, 4)
    try:
        code = await client.fetch_otp_from_mail(item["email"], secret, lengths=lengths)
    except MuseProtocolError as exc:
        return {"ok": False, "error": exc.message, "detail": exc.detail}
    token = str(getattr(client, "rotated_refresh_token", "") or "")
    if token:
        store.merge_secret(item_id, {"refresh_token": token})
    return {
        "ok": bool(code),
        "code": code,
        "kind": kind,
        "error": None if code else "未取到验证码（检查邮箱物料 / 授权 / 收件夹）",
    }


@router.post("/{item_id}/mail/check")
async def check_mail_material(item_id: str, request: Request):
    """验证邮箱物料是否可用（IMAP/Graph 至少一条通路）。"""
    require_admin(request)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    secret = store.get_secret(item_id)
    client = get_muse_client()
    try:
        msgs = await client.list_mail(item["email"], secret)
    except MuseProtocolError as exc:
        return {"ok": False, "error": exc.message, "detail": exc.detail}
    token = str(getattr(client, "rotated_refresh_token", "") or "")
    if token:
        store.merge_secret(item_id, {"refresh_token": token})
    return {"ok": True, "count": len(msgs), "items": msgs[:5]}


@router.get("/{item_id}/mail")
async def list_mail(item_id: str, request: Request):
    require_admin(request)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    secret = store.get_secret(item_id)
    client = get_muse_client()
    msgs = await client.list_mail(item["email"], secret)
    return {"ok": True, "items": msgs}


@router.get("/{item_id}/mail/message")
async def get_mail_message(
    item_id: str,
    request: Request,
    id: str = Query(..., alias="id"),
):
    require_admin(request)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(404, detail={"error": "not_found", "message": "记录不存在"})
    secret = store.get_secret(item_id)
    client = get_muse_client()
    msg = await client.get_mail_message(item["email"], secret, id)
    if not msg:
        raise HTTPException(404, detail={"error": "mail_not_found", "message": "邮件不存在"})
    html = sanitize_html(msg.get("html"))
    return {
        "ok": True,
        "item": {
            "id": msg.get("id"),
            "subject": msg.get("subject"),
            "from": msg.get("from"),
            "received_at": msg.get("received_at"),
            "text": msg.get("text"),
            "html": html,
        },
    }
