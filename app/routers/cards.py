"""cards — 卡池管理（导入 / 查询 / 编辑 / 启停 / 删除 / 支付重试）.

PAN / CVV 只以 Fernet 密文落库（data_enc），列表接口一律返回掩码；
仅 `GET /cards/{id}?reveal=1` 与导出 full=1 会给管理员完整卡数据（本机工具）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth_admin import require_admin
from app.cards import store as cards
from app.signup import machine, store

router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("")
async def list_cards(
    request: Request,
    status: str = Query(default="all"),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
    page_size: int | None = Query(default=None, ge=1, le=200),
):
    require_admin(request)
    data = cards.list_cards(
        status=status, q=q, page=page, page_size=page_size or size
    )
    return {"ok": True, **data}


@router.get("/stats")
async def card_stats(request: Request):
    require_admin(request)
    return {"ok": True, "stats": cards.stats()}


class ImportBody(BaseModel):
    text: str = ""
    source: str = ""
    path: str | None = None  # 服务端本地文件（管理员本机工具）


@router.post("/import")
async def import_cards(body: ImportBody, request: Request):
    require_admin(request)
    text = body.text or ""
    if not text.strip() and body.path:
        p = Path(body.path).expanduser()
        if not p.is_file():
            raise HTTPException(status_code=400, detail={"error": "file_not_found", "path": str(p)})
        text = p.read_text(encoding="utf-8", errors="ignore")
        body.source = body.source or p.name
    if not text.strip():
        raise HTTPException(status_code=400, detail={"error": "empty_import"})
    result = cards.import_cards(text, source=body.source or "paste")
    store.add_log(
        None,
        "info",
        f"卡池导入：新增 {result['added']} · 重复 {result['duplicate']} · 无效 {result['invalid']}",
    )
    return {"ok": True, **result, "stats": cards.stats()}


class CardCreate(BaseModel):
    pan: str
    exp: str = ""
    exp_month: int | None = None
    exp_year: int | None = None
    cvv: str
    holder: str = ""
    zip: str = ""
    country: str = "US"
    address: str = ""
    city: str = ""
    state: str = ""
    phone: str = ""
    email: str = ""
    note: str = ""
    source: str = "manual"


@router.post("")
async def create_card(body: CardCreate, request: Request):
    require_admin(request)
    line = "|".join(
        [
            body.pan,
            body.exp
            or (
                f"{int(body.exp_month or 0):02d}/{str(body.exp_year or '')[-2:]}"
                if body.exp_month
                else ""
            ),
            body.cvv,
            body.holder,
            body.address,
            body.city,
            body.state,
            body.zip,
            body.country,
            body.phone,
            body.email,
            "manual",
        ]
    )
    result = cards.import_cards(line, source=body.source or "manual")
    if not result["added"]:
        detail = result["errors"][0] if result["errors"] else "重复卡号或字段无效"
        raise HTTPException(status_code=400, detail={"error": "card_invalid", "message": detail})
    latest = cards.list_cards(page=1, page_size=1)
    card = latest["items"][0] if latest["items"] else None
    if card and body.note and card.get("id"):
        cards.update_card(card["id"], {"note": body.note})
        card = cards.get_card(card["id"])
    return {"ok": True, "card": card, "stats": cards.stats()}


class CardPatch(BaseModel):
    holder: str | None = None
    zip: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    phone: str | None = None
    email: str | None = None
    note: str | None = None
    status: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    pan: str | None = None
    cvv: str | None = None


@router.patch("/{card_id}")
async def patch_card(card_id: str, body: CardPatch, request: Request):
    require_admin(request)
    try:
        card = cards.update_card(card_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "card_invalid", "message": str(exc)}) from exc
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "card": card}


@router.delete("/{card_id}")
async def delete_card(card_id: str, request: Request):
    require_admin(request)
    n = cards.delete_cards([card_id])
    if not n:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "affected": n}


class BatchBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    action: Literal["delete", "reset", "enable", "disable", "export"] = "reset"


@router.post("/batch")
async def batch_cards(body: BatchBody, request: Request):
    require_admin(request)
    ids = [str(x) for x in (body.ids or []) if str(x).strip()]
    if not ids:
        raise HTTPException(status_code=400, detail={"error": "ids_required"})
    if body.action == "delete":
        return {"ok": True, "affected": cards.delete_cards(ids)}
    if body.action == "reset":
        return {"ok": True, "affected": cards.reset_cards(ids)}
    if body.action == "enable":
        return {"ok": True, "affected": cards.set_enabled(ids, True)}
    if body.action == "disable":
        return {"ok": True, "affected": cards.set_enabled(ids, False)}
    return {"ok": True, "affected": 0}


@router.get("/export")
async def export_cards(
    request: Request,
    ids: str = Query(default=""),
    full: int = Query(default=0),
):
    require_admin(request)
    id_list = [x.strip() for x in ids.split(",") if x.strip()] or None
    text = cards.export_text(id_list, full=bool(full))
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="cards-export.txt"',
        },
    )


@router.get("/{card_id}")
async def get_card(
    card_id: str,
    request: Request,
    reveal: int = Query(default=0),
):
    require_admin(request)
    card = cards.get_card(card_id, reveal=bool(reveal))
    if not card:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "card": card}


class PayBody(BaseModel):
    card_id: str | None = None


@router.post("/pay/{item_id}")
async def pay_signup(item_id: str, request: Request, body: PayBody | None = None):
    """用卡池里的卡继续该注册任务的 checkout。

    传 card_id 时固定用这张卡，否则按导入顺序挑一张 available。
    """
    require_admin(request)
    if not store.get_item(item_id):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    result = await machine.apply_card_retry(item_id, body.card_id if body else None)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    if result.get("error") in ("card_unavailable", "no_session"):
        raise HTTPException(status_code=409, detail=result)
    return {"ok": bool(result.get("ok")), "result": result}
