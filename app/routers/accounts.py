"""Accounts pool — local SQLite authority (plan §12 P1). Cookie ciphertext never echoed."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth_admin import require_admin
from app.pool import store as pool_store
from app.signup import store as signup_store
from app.signup.fingerprint import fingerprint_label_zh

logger = logging.getLogger("muse-proxy.accounts")

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _enrich_fp(item: dict[str, Any]) -> dict[str, Any]:
    fp = item.get("fingerprint")
    if isinstance(fp, dict):
        pid = fp.get("profile_id") or ""
        label = fingerprint_label_zh(pid) if pid else ""
        ua = (fp.get("user_agent") or fp.get("ua") or "")[:80]
        item["fingerprint_profile"] = pid or None
        item["fingerprint_label"] = label or None
        item["fingerprint_summary"] = label or (ua[:40] + "…" if ua else ("已记录" if fp else ""))
        if "fingerprint_text" not in item:
            import json as _json

            item["fingerprint_text"] = _json.dumps(fp, ensure_ascii=False, indent=2)
    else:
        item.setdefault("fingerprint_summary", "")
        item.setdefault("fingerprint_text", "")
    item["fingerprint_readonly"] = True
    return item


def parse_import_lines(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = ""
        body = line
        if "----" in line:
            head, tail = line.split("----", 1)
            if "=" not in head.strip() or "hatch_" in tail or ";" in tail or "=" in tail:
                label, body = head.strip(), tail.strip()
        elif "|" in line:
            head, tail = line.split("|", 1)
            if "=" not in head:
                label, body = head.strip(), tail.strip()
        if not body.strip():
            continue
        out.append((label, body.strip()))
    return out


@router.get("")
async def list_accounts(
    request: Request,
    status: str = Query(default="all"),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    size: int | None = Query(default=None, ge=1, le=200),
):
    require_admin(request)
    if size is not None:
        page_size = size
    accounts = [_enrich_fp(a) for a in pool_store.list_public(status=status, q=q)]
    total = len(accounts)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    offset = (page - 1) * page_size
    items = accounts[offset : offset + page_size]
    return {
        "ok": True,
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "stats": pool_store.stats(),
        "source": "local",
    }


@router.get("/export")
async def export_accounts(
    request: Request,
    all: int = Query(default=1),
    ids: str = Query(default=""),
):
    require_admin(request)
    rows = pool_store.list_public()
    id_set = {x.strip() for x in ids.split(",") if x.strip()} if ids else set()
    if id_set:
        rows = [r for r in rows if str(r.get("id")) in id_set]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "label",
            "email",
            "enabled",
            "status",
            "ok",
            "expires_at",
            "cookie_count",
            "last_used",
            "note",
            "use_count",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r.get("id"),
                r.get("label"),
                r.get("email"),
                r.get("enabled"),
                r.get("status"),
                r.get("ok"),
                r.get("expires_at"),
                r.get("cookie_count"),
                r.get("last_used"),
                r.get("note"),
                r.get("use_count"),
            ]
        )
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="accounts-export.csv"'},
    )


class ImportBody(BaseModel):
    text: str = ""
    replace: bool = False
    batch: str | None = None
    cookie_header: str | None = None
    cookies: dict[str, str] | None = None
    label: str = ""
    email: str = ""
    name: str = ""
    note: str = ""


@router.post("/import")
async def import_accounts(body: ImportBody, request: Request):
    require_admin(request)
    text = body.text or body.batch or ""
    pairs = parse_import_lines(text) if text.strip() else []
    if not pairs and (body.cookie_header or body.cookies):
        header = body.cookie_header or pool_store.cookie_header_from_dict(body.cookies or {})
        pairs = [(body.label or body.email or "", header)]
    if not pairs:
        raise HTTPException(
            status_code=400, detail={"error": "empty_import", "message": "无有效 Cookie 行"}
        )
    logger.info("accounts import lines=%s replace=%s", len(pairs), body.replace)
    result = pool_store.import_batch(pairs, replace=bool(body.replace))
    # optional name/email on single import
    if len(result.get("added") or []) == 1 and (body.email or body.name or body.note):
        aid = (result["added"][0] or {}).get("id")
        if aid:
            pool_store.update(
                str(aid),
                email=body.email or None,
                name=body.name or None,
                note=body.note or None,
            )
    return {
        "ok": True,
        "added": result.get("count") or 0,
        "duplicate": 0,
        "total": pool_store.stats().get("total"),
        "essential_missing": result.get("essential_missing"),
        "warning": result.get("warning") or "",
        "source": "local",
    }


class BatchBody(BaseModel):
    ids: list[str] = Field(default_factory=list)
    action: Literal["enable", "disable", "delete", "check", "reset", "credits"] = "enable"
    all: bool = False


@router.post("/batch")
async def batch_accounts(body: BatchBody, request: Request):
    require_admin(request)
    ids = [str(x) for x in (body.ids or []) if str(x).strip()]
    if body.all and not ids:
        ids = [str(a["id"]) for a in pool_store.list_raw() if a.get("id")]
    if not ids:
        return {"ok": True, "affected": 0, "valid": 0, "invalid": 0, "failed": 0}

    from app.muse_engine.service import test_account_session

    affected = valid = invalid = failed = 0
    for aid in ids:
        try:
            if body.action == "enable":
                pool_store.update(aid, enabled=True)
                affected += 1
            elif body.action == "disable":
                pool_store.update(aid, enabled=False)
                affected += 1
            elif body.action == "delete":
                pool_store.delete(aid)
                affected += 1
            elif body.action == "credits":
                # quota panel via CDP is P3; mark checked only
                affected += 1
                invalid += 1
            elif body.action in ("check", "reset"):
                r = test_account_session(aid)
                affected += 1
                if r.get("ok"):
                    valid += 1
                else:
                    invalid += 1
        except Exception:  # noqa: BLE001
            failed += 1
    return {
        "ok": True,
        "affected": affected,
        "valid": valid,
        "invalid": invalid,
        "failed": failed,
        "action": body.action,
    }


@router.get("/{aid}")
async def get_account(aid: str, request: Request):
    require_admin(request)
    item = pool_store.get_public(aid, include_secrets=True)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    item = _enrich_fp(item)
    item["has_cookies"] = bool(item.get("cookie_count"))
    item["cookie"] = ""  # paste to replace; no echo
    item["account"] = item.get("email") or item.get("account") or item.get("label")
    # strip raw cookies from response
    item.pop("cookies", None)
    item.pop("cookies_exp", None)
    item.pop("cookie_header", None)
    return {"ok": True, "item": item}


class PatchBody(BaseModel):
    label: str | None = None
    name: str | None = None
    email: str | None = None
    note: str | None = None
    enabled: bool | None = None
    password: str | None = None
    cookie: str | None = None
    cookie_header: str | None = None
    proxy: dict[str, Any] | None = None
    fingerprint: Any | None = None
    fingerprint_text: str | None = None
    fingerprint_profile: str | None = None


@router.patch("/{aid}")
async def patch_account(aid: str, body: PatchBody, request: Request):
    require_admin(request)
    if body.fingerprint is not None or body.fingerprint_text is not None or body.fingerprint_profile is not None:
        logger.info("accounts patch ignored fingerprint edit id=%s", aid)
    if not pool_store.get(aid):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    cookie_text = (body.cookie or body.cookie_header or "").strip()
    try:
        item = pool_store.update(
            aid,
            label=body.label,
            email=body.email.strip() if body.email is not None else None,
            name=body.name,
            note=body.note,
            enabled=body.enabled,
            password=body.password,
            cookie_header=cookie_text or None,
            proxy=body.proxy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    # sync password/name to signup item if linked
    row = pool_store.get(aid) or {}
    sid = row.get("signup_item_id")
    if sid and body.password is not None:
        try:
            signup_store.merge_secret(sid, {"muse_login_password": body.password})
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync signup password failed: %s", exc)
    if sid and body.name is not None:
        try:
            srow = signup_store.get_item(sid)
            if srow:
                import json as _json

                prof = {}
                try:
                    prof = _json.loads(srow.get("profile_json") or "{}")
                except Exception:
                    prof = {}
                parts = (body.name or "").strip().split(None, 1)
                if parts:
                    prof["first_name"] = parts[0]
                    prof["last_name"] = parts[1] if len(parts) > 1 else prof.get("last_name") or ""
                prof["display_name"] = body.name
                signup_store.update_item(
                    sid, profile_json=_json.dumps(prof, ensure_ascii=False)
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync signup name failed: %s", exc)
    return {"ok": True, "item": _enrich_fp(item or {})}


@router.delete("/{aid}")
async def delete_one(aid: str, request: Request):
    require_admin(request)
    if not pool_store.delete(aid):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "deleted": aid}


@router.post("/{aid}/quota")
async def account_quota(aid: str, request: Request):
    require_admin(request)
    if not pool_store.get(aid):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    # 实时读取：走 CDP 引擎打开 Muse 设置面板解析用量
    pub = pool_store.get_public(aid) or {}
    cached = pub.get("quota")
    try:
        from app.muse_engine.service import quota as engine_quota

        q = await asyncio.to_thread(engine_quota, aid)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:300] or type(exc).__name__
        return {
            "ok": True,
            "id": aid,
            "found": bool(cached),
            "quota": cached,
            "cached": True,
            "error": msg,
            "message": ("实时额度读取失败，返回缓存：" if cached else "额度读取失败：") + msg,
        }
    try:
        pool_store.update(aid, quota=q, checked=True)
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "id": aid,
        "found": bool(q),
        "quota": q,
        "cached": False,
        "message": "" if q else "额度面板未读到有效用量",
    }


@router.post("/{aid}/check")
async def check_account(aid: str, request: Request):
    require_admin(request)
    if not pool_store.get(aid):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    from app.muse_engine.service import test_account_session

    result = test_account_session(aid)
    return {"ok": bool(result.get("ok")), **result}


@router.post("/{aid}/cookies")
async def set_cookies(aid: str, request: Request):
    require_admin(request)
    if not pool_store.get(aid):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "bad_body"})
    header = body.get("cookie_header") or body.get("cookie") or ""
    cookies = body.get("cookies") if isinstance(body.get("cookies"), dict) else None
    if not header and not cookies:
        raise HTTPException(status_code=400, detail={"error": "empty_cookies"})
    try:
        pool_store.update(aid, cookie_header=header or None, cookies=cookies)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
    return {"ok": True, "id": aid}
