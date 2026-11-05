"""R2 account pool APIs (local SQLite CRUD; optional mock refresh)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app import r2_store
from app.auth_admin import require_admin
from app.signup.guard import reject_card_fields

router = APIRouter(prefix="/r2", tags=["r2"])


@router.get("/accounts")
async def list_accounts(
    request: Request,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=200),
    q: str = Query(default=""),
    status: str = Query(default="all"),
):
    require_admin(request)
    return r2_store.list_accounts(status=status, q=q, page=page, size=size)


class AccountBody(BaseModel):
    name: str = ""
    bucket: str = ""
    prefix: str = "generated"
    region: str = "auto"
    endpoint: str = ""
    accessKey: str | None = None
    secretKey: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    publicUrl: str | None = None
    public_base_url: str | None = None
    quotaGB: int | None = 10
    quota: int | None = None
    enabled: bool = True


@router.post("/accounts")
async def create_account(body: AccountBody, request: Request):
    require_admin(request)
    raw = body.model_dump(exclude_none=False)
    reject_card_fields(raw)
    if not (body.name or "").strip() or not (body.bucket or "").strip():
        raise HTTPException(400, detail={"error": "name_bucket_required", "message": "名称与 Bucket 必填"})
    item = r2_store.create(raw)
    return {"ok": True, "item": item}


@router.patch("/accounts/{item_id}")
async def patch_account(item_id: str, body: AccountBody, request: Request):
    require_admin(request)
    raw = body.model_dump(exclude_unset=True)
    reject_card_fields(raw)
    item = r2_store.update(item_id, raw)
    if not item:
        raise HTTPException(404, detail={"error": "not_found", "message": "账号不存在"})
    return {"ok": True, "item": item}


class BatchBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    action: str


@router.post("/batch")
async def batch_accounts(body: BatchBody, request: Request):
    require_admin(request)
    reject_card_fields(body.model_dump())
    n = r2_store.batch(body.ids or [], body.action)
    return {"ok": True, "affected": n}


class ImportBody(BaseModel):
    text: str = ""


@router.post("/import")
async def import_accounts(body: ImportBody, request: Request):
    require_admin(request)
    reject_card_fields(body.model_dump())
    try:
        result = r2_store.import_json(body.text or "")
    except ValueError as exc:
        raise HTTPException(400, detail={"error": str(exc), "message": "JSON 无效"}) from exc
    return result


class RefreshBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


@router.post("/refresh")
async def refresh_usage(body: RefreshBody, request: Request):
    require_admin(request)
    return {"ok": True, **r2_store.start_refresh(body.ids or None)}
