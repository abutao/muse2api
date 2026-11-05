"""subs — 导号/注册激活（无卡）；profile / otp / resume / recovery / password / invite."""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth_admin import get_session_user, require_admin
from app.config import get_settings
from app.signup import machine, runner, store
from app.signup.guard import reject_card_fields
from app.signup.html_sanitize import sanitize_html
from app.signup.import_parse import parse_import_text
from app.signup.protocol import get_muse_client

logger = logging.getLogger("muse-proxy.subs")

router = APIRouter(prefix="/subs", tags=["subs"])


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
    raise HTTPException(status_code=401, detail={"error": "unauthorized"})


def _raw_body_dict(body: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(body, BaseModel):
        return body.model_dump(exclude_none=False)
    return dict(body)


# ---------- list / import / batch / task (static paths first) ----------


@router.get("")
async def list_subs(
    request: Request,
    status: str = Query(default="all"),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
):
    require_admin(request)
    data = store.list_items(status=status, q=q, page=page, page_size=page_size)
    return {"ok": True, **data}


class ImportBody(BaseModel):
    text: str = ""
    region: str = "US"
    country: str | None = None
    into_pool: bool = True
    intoPool: bool | None = None  # 小AD alias


@router.post("/import")
async def import_subs(body: ImportBody, request: Request):
    require_admin(request)
    reject_card_fields(_raw_body_dict(body))
    into = body.into_pool if body.intoPool is None else bool(body.intoPool)
    creds = parse_import_text(body.text)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_import",
                "message": "无有效行；需 6 段或 4 段：邮箱----密码----ClientID----RefreshToken[----辅邮----辅密]",
            },
        )
    created = 0
    updated = 0
    ids = []
    for c in creds:
        iid, is_new = store.upsert_from_creds(
            c,
            region=body.region or "US",
            country=body.country,
            into_pool=into,
        )
        ids.append(iid)
        if is_new:
            created += 1
        else:
            updated += 1
    store.add_log(None, "info", f"导入 {created} 新建 / {updated} 更新凭据")
    return {
        "ok": True,
        "added": created,
        "updated": updated,
        "total": created + updated,
        "ids": ids,
    }


class BatchBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    all: bool = False
    action: Literal["activate", "retry", "reset", "delete"] = "activate"
    country: str | None = None
    region: str | None = None
    intoPool: bool | None = None
    into_pool: bool | None = None


@router.post("/batch")
async def batch_subs(body: BatchBody, request: Request):
    require_admin(request)
    reject_card_fields(_raw_body_dict(body))
    ids = [str(x) for x in (body.ids or []) if str(x).strip()]
    if body.all and not ids:
        data = store.list_items(page=1, page_size=200)
        # gather all ids via direct query
        from app.db import get_db

        with get_db() as conn:
            ids = [r["id"] for r in conn.execute("SELECT id FROM signup_items").fetchall()]

    if body.action == "delete":
        n = store.delete_items(ids)
        return {"ok": True, "affected": n, "action": "delete"}

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
        # reset failed→pending already; start runner
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
        started = runner.start_batch(run_ids)
        return {"ok": True, "action": body.action, "queued": len(run_ids), **started}

    return {"ok": True, "action": body.action, "affected": len(ids)}


@router.post("/stop")
async def stop_subs(request: Request):
    require_admin(request)
    return runner.stop_batch()


@router.get("/task")
async def subs_task(request: Request, after: int = Query(default=0, ge=0)):
    require_admin(request)
    return runner.task_snapshot(after=after)


@router.get("/export")
async def export_subs(request: Request):
    require_admin(request)
    data = store.list_items(page=1, page_size=200)
    # export all via loop
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
        headers={"Content-Disposition": 'attachment; filename="subs-export.csv"'},
    )


# ---------- per-item ----------


@router.get("/{item_id}")
async def get_sub(
    item_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
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
    # trap fields — validated via reject_card_fields on raw dump
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
    # also reject if extra keys sneaked via request json
    try:
        extra = await request.json()
    except Exception:
        extra = {}
    if isinstance(extra, dict):
        reject_card_fields(extra)
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
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
    otp_code: str
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
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.apply_otp(
        item_id, body.otp_code.strip(), remember_device=body.remember_device
    )
    item = store.get_item(item_id)
    return {"ok": True, "result": result, "item": store.row_to_public(item) if item else None}


class ResumeBody(BaseModel):
    payment_ref: str
    provider: str = "external"
    note: str | None = None
    # traps
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
            status_code=400, detail={"error": "payment_ref_required", "message": "需要 payment_ref"}
        )
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
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
        raise HTTPException(status_code=400, detail={"error": "empty_cookie"})
    into = body.into_pool if body.into_pool is not None else body.intoPool
    result = await machine.apply_cookie(item_id, ck, into_pool=into)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    # never echo cookie
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
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.start_recovery(item_id)
    item = store.get_item(item_id)
    status = 200 if result.get("ok") else 400
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail=result)
    return {"ok": bool(result.get("ok")), "result": result, "item": store.row_to_public(item) if item else None}


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
        raise HTTPException(status_code=400, detail={"error": "recovery_code_required"})
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.apply_recovery_code(item_id, code)
    item = store.get_item(item_id)
    return {"ok": bool(result.get("ok")), "result": result, "item": store.row_to_public(item) if item else None}


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
    if pwd and len(pwd) < 6:
        raise HTTPException(status_code=400, detail={"error": "weak_password", "message": "密码至少 6 位"})
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.apply_set_password(
        item_id, pwd, logout_other_sessions=body.logout_other_sessions
    )
    if result.get("error") == "invalid_status":
        raise HTTPException(status_code=409, detail=result)
    item = store.get_item(item_id)
    return {"ok": bool(result.get("ok")), "result": result, "item": store.row_to_public(item) if item else None}


@router.post("/{item_id}/password/start")
async def post_password_start(
    item_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.start_password_login(item_id)
    item = store.get_item(item_id)
    return {"ok": bool(result.get("ok")), "result": result, "item": store.row_to_public(item) if item else None}


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
        raise HTTPException(status_code=400, detail={"error": "password_required"})
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.apply_password_login(
        item_id, body.password.strip(), remember_device=body.remember_device
    )
    item = store.get_item(item_id)
    return {"ok": bool(result.get("ok")), "result": result, "item": store.row_to_public(item) if item else None}


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
        raise HTTPException(status_code=400, detail={"error": "code_required"})
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.apply_invite_redeem(item_id, code)
    if result.get("error") == "invalid_status":
        raise HTTPException(status_code=409, detail=result)
    item = store.get_item(item_id)
    return {"ok": bool(result.get("ok")), "result": result, "item": store.row_to_public(item) if item else None}


@router.get("/{item_id}/invite")
async def get_invite(
    item_id: str,
    request: Request,
    create: bool = Query(default=False),
    authorization: str | None = Header(default=None),
):
    _auth_admin_or_webhook(request, authorization)
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.invite_panel(item_id, create=create)
    return result


@router.get("/{item_id}/mail")
async def list_mail(item_id: str, request: Request):
    require_admin(request)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
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
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    secret = store.get_secret(item_id)
    client = get_muse_client()
    msg = await client.get_mail_message(item["email"], secret, id)
    if not msg:
        raise HTTPException(status_code=404, detail={"error": "mail_not_found"})
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
