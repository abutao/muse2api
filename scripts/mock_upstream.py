#!/usr/bin/env python3
"""Minimal muse2api-like upstream for local M2/M3 acceptance.

Covers: models, chat SSE, images (gen/edits/tasks), videos, media.
Does NOT talk to muse.ai. Default bind :19080.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
import uvicorn

UPSTREAM_KEY = "mock-upstream-key"
PUBLIC = "http://127.0.0.1:19080"

# 1x1 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

MODELS = [
    {
        "id": "muse-spark",
        "object": "model",
        "owned_by": "muse",
        "description": "mock text",
    },
    {
        "id": "muse-image",
        "object": "model",
        "owned_by": "muse",
        "description": "mock image",
    },
    {
        "id": "muse-video",
        "object": "model",
        "owned_by": "muse",
        "description": "mock video",
    },
]

# in-memory task store
_TASKS: dict[str, dict[str, Any]] = {}
_VIDEOS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="mock-muse2api", version="0.0.1-m3")


def auth(authorization: str | None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 Authorization: Bearer <key>")
    token = authorization.split(" ", 1)[1].strip()
    if token != UPSTREAM_KEY:
        raise HTTPException(401, "API key 无效")


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "message": str(exc.detail),
                    "type": "invalid_request_error",
                    "param": None,
                    "code": exc.status_code,
                }
            },
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/healthz")
def healthz():
    return {"ok": True, "mock": True, "covers": ["chat", "images", "videos", "media", "admin", "keepalive"]}


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {"object": "list", "data": MODELS}


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/completions")
async def chat(request: Request, authorization: str | None = Header(default=None)):
    auth(authorization)
    body = await request.json()
    model = body.get("model") or "muse-spark"
    stream = bool(body.get("stream"))
    messages = body.get("messages") or []
    prompt = ""
    if messages:
        prompt = str(messages[-1].get("content") or "")
    reply = f"[mock] echo: {prompt[:200]}"
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    if not stream:
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    def gen():
        yield _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
            }
        )
        for i in range(0, len(reply), 8):
            piece = reply[i : i + 8]
            yield _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": piece},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            time.sleep(0.02)
        yield _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _media_url(name: str) -> str:
    return f"{PUBLIC}/v1/media/{name}"


def _image_payload(model: str, prompt: str = "") -> dict[str, Any]:
    name = f"mock-{uuid.uuid4().hex[:12]}.png"
    return {
        "created": int(time.time()),
        "data": [
            {
                "url": _media_url(name),
                "revised_prompt": prompt[:200] or None,
            }
        ],
        "model": model,
    }


@app.post("/v1/images/generations")
async def images_generations(
    request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    body = await request.json()
    model = body.get("model") or "muse-image"
    prompt = str(body.get("prompt") or "")
    return _image_payload(model, prompt)


@app.post("/v1/images/edits")
async def images_edits(
    request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    # multipart or JSON — consume raw body; do not require python-multipart
    ctype = (request.headers.get("content-type") or "").lower()
    prompt = ""
    model = "muse-image"
    raw = await request.body()
    if "application/json" in ctype:
        try:
            body = json.loads(raw or b"{}")
            prompt = str(body.get("prompt") or "")
            model = str(body.get("model") or model)
        except Exception:
            pass
    else:
        # naive multipart field scrape (enough for local acceptance)
        try:
            text = raw.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
        for key in ("prompt", "model"):
            marker = f'name="{key}"'
            if marker in text:
                part = text.split(marker, 1)[1]
                # skip headers blank line
                if "\r\n\r\n" in part:
                    val = part.split("\r\n\r\n", 1)[1]
                    val = val.split("\r\n")[0].strip()
                    if key == "prompt":
                        prompt = val
                    else:
                        model = val or model
    return _image_payload(model, prompt)


@app.post("/v1/images/tasks")
async def images_tasks_create(
    request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    body = await request.json()
    model = body.get("model") or "muse-image"
    prompt = str(body.get("prompt") or "")
    tid = "img_" + uuid.uuid4().hex[:16]
    name = f"task-{tid}.png"
    _TASKS[tid] = {
        "id": tid,
        "object": "image.task",
        "status": "completed",
        "created_at": int(time.time()),
        "model": model,
        "prompt": prompt[:200],
        "result": {"data": [{"url": _media_url(name)}]},
    }
    return JSONResponse(
        status_code=202,
        content={"id": tid, "object": "image.task", "status": "queued", "model": model},
    )


@app.get("/v1/images/tasks/{task_id}")
async def images_tasks_get(
    task_id: str, authorization: str | None = Header(default=None)
):
    auth(authorization)
    task = _TASKS.get(task_id)
    if not task:
        # fabricate completed for unknown ids (easy curl)
        name = f"task-{task_id}.png"
        return {
            "id": task_id,
            "object": "image.task",
            "status": "completed",
            "created_at": int(time.time()),
            "model": "muse-image",
            "result": {"data": [{"url": _media_url(name)}]},
        }
    return task


@app.post("/v1/videos")
async def videos_create(
    request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    body = await request.json()
    model = body.get("model") or "muse-video"
    prompt = str(body.get("prompt") or "")
    vid = "vid_" + uuid.uuid4().hex[:16]
    name = f"{vid}.mp4"
    _VIDEOS[vid] = {
        "id": vid,
        "object": "video",
        "status": "completed",
        "created_at": int(time.time()),
        "model": model,
        "prompt": prompt[:200],
        "url": _media_url(name),
    }
    return {
        "id": vid,
        "object": "video",
        "status": "queued",
        "model": model,
        "created_at": int(time.time()),
    }


@app.get("/v1/videos/{video_id}")
async def videos_get(
    video_id: str, authorization: str | None = Header(default=None)
):
    auth(authorization)
    video = _VIDEOS.get(video_id)
    if not video:
        return {
            "id": video_id,
            "object": "video",
            "status": "completed",
            "created_at": int(time.time()),
            "model": "muse-video",
            "url": _media_url(f"{video_id}.mp4"),
        }
    return video


@app.get("/v1/media/{name}")
async def get_media(name: str):
    """No auth on muse2api media; proxy may require Bearer separately."""
    if not name or "/" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    if name.endswith(".mp4"):
        # tiny fake bytes
        return Response(
            content=b"\x00\x00\x00\x18ftypmp42",
            media_type="video/mp4",
            headers={"Content-Disposition": f'inline; filename="{name}"'},
        )
    return Response(
        content=_PNG,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )



# ------------------------- M6: admin accounts (in-memory) -------------------------
import re
import threading

_ACCOUNTS: list[dict[str, Any]] = []
_ACC_LOCK = threading.Lock()
_ESSENTIAL = ("hatch_sess",)


def _parse_cookie_text(text: str) -> dict[str, str]:
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return {str(k): str(v) for k, v in obj.items()}
        except Exception:
            pass
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _parse_batch(text: str) -> list[tuple[str, dict[str, str]]]:
    out: list[tuple[str, dict[str, str]]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label = ""
        body = line
        if "|" in line:
            head, tail = line.split("|", 1)
            if "=" not in head:
                label, body = head.strip(), tail.strip()
        cookies = _parse_cookie_text(body)
        if cookies:
            out.append((label, cookies))
    return out


def _public_account(acc: dict[str, Any]) -> dict[str, Any]:
    item = {k: v for k, v in acc.items() if k not in ("cookies", "cookies_exp")}
    item["cookie_count"] = len(acc.get("cookies") or {})
    item["essential_ok"] = all((acc.get("cookies") or {}).get(n) for n in _ESSENTIAL)
    item["expires_estimated"] = True
    return item


def _stats() -> dict[str, Any]:
    total = len(_ACCOUNTS)
    enabled = sum(1 for a in _ACCOUNTS if a.get("enabled", True))
    bad = sum(1 for a in _ACCOUNTS if a.get("enabled", True) and a.get("ok") is False)
    healthy = sum(1 for a in _ACCOUNTS if a.get("enabled", True) and a.get("ok") is True)
    return {
        "total": total,
        "enabled": enabled,
        "disabled": total - enabled,
        "healthy": healthy,
        "error": bad,
        "expiring": 0,
        "expired": 0,
    }


@app.get("/admin/status")
def admin_status(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {
        "accounts": [_public_account(a) for a in _ACCOUNTS],
        "stats": _stats(),
        "tasks": [],
        "media_count": 0,
        "browser_running": False,
        "essential_cookies": list(_ESSENTIAL),
        "base_url": f"{PUBLIC}/v1",
        "config": {"public_base": PUBLIC},
    }


@app.get("/admin/accounts")
def admin_list_accounts(authorization: str | None = Header(default=None)):
    auth(authorization)
    return {"accounts": [_public_account(a) for a in _ACCOUNTS], "stats": _stats()}


@app.post("/admin/accounts")
async def admin_add_accounts(
    request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    body = await request.json()
    added: list[dict[str, Any]] = []
    seen: list[dict[str, str]] = []
    with _ACC_LOCK:
        if body.get("batch"):
            for label, cookies in _parse_batch(str(body["batch"])):
                aid = uuid.uuid4().hex[:12]
                acc = {
                    "id": aid,
                    "label": label or aid,
                    "cookies": cookies,
                    "cookies_exp": {},
                    "expires_at": int(time.time()) + 2 * 86400,
                    "enabled": True,
                    "created_at": int(time.time()),
                    "last_used": None,
                    "last_keepalive": None,
                    "use_count": 0,
                    "ok": None,
                    "note": "",
                    "synced_at": None,
                }
                _ACCOUNTS.append(acc)
                seen.append(cookies)
                added.append(
                    {
                        "id": aid,
                        "label": acc["label"],
                        "cookie_count": len(cookies),
                        "expires_at": acc["expires_at"],
                    }
                )
        cookies = dict(body.get("cookies") or {})
        if body.get("cookie_header"):
            cookies.update(_parse_cookie_text(str(body["cookie_header"])))
        if cookies:
            aid = uuid.uuid4().hex[:12]
            label = str(body.get("label") or aid)
            acc = {
                "id": aid,
                "label": label,
                "cookies": cookies,
                "cookies_exp": {},
                "expires_at": int(time.time()) + 2 * 86400,
                "enabled": True,
                "created_at": int(time.time()),
                "last_used": None,
                "last_keepalive": None,
                "use_count": 0,
                "ok": None,
                "note": "",
                "synced_at": None,
            }
            _ACCOUNTS.append(acc)
            seen.append(cookies)
            added.append(
                {
                    "id": aid,
                    "label": label,
                    "cookie_count": len(cookies),
                    "expires_at": acc["expires_at"],
                }
            )
    if not added:
        raise HTTPException(400, "未解析到任何 cookie，请检查格式")
    missing = [n for n in _ESSENTIAL if not any(n in c for c in seen)]
    return {
        "added": added,
        "count": len(added),
        "essential_missing": missing,
        "warning": (
            f"缺少核心 cookie：{', '.join(missing)}，该账号可能无法生成"
            if missing
            else ""
        ),
    }


def _find(aid: str) -> dict[str, Any] | None:
    for a in _ACCOUNTS:
        if a["id"] == aid:
            return a
    return None


@app.patch("/admin/accounts/{aid}")
async def admin_patch_account(
    aid: str, request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    body = await request.json()
    with _ACC_LOCK:
        acc = _find(aid)
        if not acc:
            raise HTTPException(404, "账号不存在")
        if "label" in body and body["label"] is not None:
            acc["label"] = str(body["label"])
        if "enabled" in body and body["enabled"] is not None:
            acc["enabled"] = bool(body["enabled"])
        if "note" in body and body["note"] is not None:
            acc["note"] = str(body["note"])[:300]
        return _public_account(acc)


@app.delete("/admin/accounts/{aid}")
def admin_del_account(aid: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with _ACC_LOCK:
        before = len(_ACCOUNTS)
        _ACCOUNTS[:] = [a for a in _ACCOUNTS if a["id"] != aid]
        if len(_ACCOUNTS) == before:
            raise HTTPException(404, "账号不存在")
    return {"deleted": True, "id": aid}


@app.post("/admin/accounts/{aid}/test")
def admin_test_account(aid: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with _ACC_LOCK:
        acc = _find(aid)
        if not acc:
            raise HTTPException(404, "账号不存在")
        cookies = acc.get("cookies") or {}
        if not cookies:
            raise HTTPException(400, "该账号没有 cookie")
        ok = bool(cookies.get("hatch_sess"))
        acc["ok"] = ok
        acc["checked_at"] = int(time.time())
        acc["note"] = "会话有效（mock）" if ok else "缺少 hatch_sess（mock）"
        quota = None
        if ok:
            quota = {
                "found": True,
                "plan": "Free plan",
                "weekly_used_pct": 1,
                "checked_at": acc["checked_at"],
            }
            acc["quota"] = quota
        return {
            "ok": ok,
            "message": acc["note"],
            "synced": {},
            "quota": quota,
            "quota_error": None if ok else "no hatch_sess",
        }


@app.post("/admin/accounts/{aid}/cookies")
async def admin_update_cookies(
    aid: str, request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    body = await request.json()
    cookies = dict(body.get("cookies") or {})
    if body.get("cookie_header"):
        cookies.update(_parse_cookie_text(str(body["cookie_header"])))
    if not cookies:
        raise HTTPException(400, "未解析到 cookie")
    with _ACC_LOCK:
        acc = _find(aid)
        if not acc:
            raise HTTPException(404, "账号不存在")
        acc["cookies"] = cookies
        acc["ok"] = None
        acc["note"] = "已更新 cookie"
        return {
            "ok": True,
            "id": aid,
            "cookie_count": len(cookies),
            "expires_at": acc.get("expires_at"),
        }


@app.post("/admin/accounts/{aid}/quota")
def admin_quota(aid: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with _ACC_LOCK:
        acc = _find(aid)
        if not acc:
            raise HTTPException(404, "账号不存在")
        q = {
            "found": True,
            "plan": "Free plan",
            "weekly_used_pct": 3,
            "checked_at": int(time.time()),
        }
        acc["quota"] = q
        return q





@app.get("/readyz")
def readyz():
    return {"ok": True, "ready": True, "mock": True}


@app.post("/admin/accounts/{aid}/relogin")
def admin_relogin(aid: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with _ACC_LOCK:
        acc = _find(aid)
        if not acc:
            raise HTTPException(404, "账号不存在")
        acc["ok"] = True
        acc["last_keepalive"] = int(time.time())
        acc["note"] = "relogin ok (mock)"
        return {"ok": True, "message": acc["note"], "id": aid}


@app.post("/admin/accounts/{aid}/keepalive")
def admin_keepalive_one(aid: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with _ACC_LOCK:
        acc = _find(aid)
        if not acc:
            raise HTTPException(404, "账号不存在")
        acc["last_keepalive"] = int(time.time())
        acc["ok"] = True if (acc.get("cookies") or {}).get("hatch_sess") else acc.get("ok")
        acc["note"] = "keepalive ok (mock)"
        return {"ok": True, "message": acc["note"], "id": aid, "last_keepalive": acc["last_keepalive"]}


@app.post("/admin/keepalive")
async def admin_keepalive_batch(
    request: Request, authorization: str | None = Header(default=None)
):
    auth(authorization)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ids = body.get("ids") if isinstance(body, dict) else None
    results = []
    with _ACC_LOCK:
        targets = _ACCOUNTS if not ids else [a for a in _ACCOUNTS if a["id"] in set(ids)]
        for acc in targets:
            acc["last_keepalive"] = int(time.time())
            acc["note"] = "keepalive ok (mock)"
            results.append({"id": acc["id"], "ok": True})
    return {"ok": True, "count": len(results), "results": results}


# ----- Muse native auth (signup mock, no checkout) -----
_MUSE_SESS: dict[str, dict] = {}
_MUSE_MAIL: dict[str, list] = {}


def _muse_sid(request: Request) -> str:
    return request.headers.get("x-hatch-waterfall-id") or "default"


@app.post("/api/auth/native/restart")
async def muse_restart(request: Request):
    import uuid
    wid = str(uuid.uuid4())
    csrf = "mock_csrf_" + uuid.uuid4().hex[:8]
    _MUSE_SESS[wid] = {"csrf": csrf, "otp": None, "email": None, "hatch_sess": None}
    resp = JSONResponse(
        {"ok": True, "csrf_token": csrf, "waterfall_id": wid},
        status_code=201,
    )
    resp.set_cookie("hatch_native_auth_csrf", f"{csrf}.sig")
    resp.headers["x-hatch-waterfall-id"] = wid
    return resp


@app.post("/api/auth/native/send-otp")
async def muse_send_otp(request: Request):
    import time, uuid, re
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.setdefault(wid, {"csrf": "", "otp": None, "email": None, "hatch_sess": None})
    email = str(body.get("contact_point") or "")
    code = f"{int(time.time()) % 1000000:06d}"
    sess["otp"] = code
    sess["email"] = email
    _MUSE_MAIL.setdefault(email.lower(), []).insert(
        0,
        {
            "id": "m_" + uuid.uuid4().hex[:8],
            "subject": "Muse OTP",
            "from": "noreply@muse.local",
            "text": f"code {code}",
            "html": f"<p>{code}</p><script>bad()</script>",
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    has_pw = bool(sess.get("password"))
    has_acct = has_pw or bool(sess.get("activated")) or bool(sess.get("seen"))
    sess["seen"] = True
    flow = "login" if has_acct else "register"
    return JSONResponse(
        {
            "ok": True,
            "registration_state": "otp_sent",
            "flow": flow,
            "password_login_available": has_pw,
            "has_muse_account": has_acct,
            "masked_contact": (email[:1] + "***@" + email.split("@")[-1]) if "@" in email else "***",
        },
        status_code=201,
    )


@app.post("/api/auth/native/confirm-otp")
async def muse_confirm_otp(request: Request):
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.get(wid) or {}
    code = str(body.get("otp_code") or "")
    if code != sess.get("otp") and code != "000000":
        raise HTTPException(400, {"message": "invalid otp"})
    return {
        "ok": True,
        "registration_state": "completion_required",
        "tos": {"content_version": "mock-tos-v1"},
    }


@app.post("/api/auth/native/register-completion")
async def muse_register_completion(request: Request):
    import uuid
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.setdefault(wid, {})
    hs = "hatch_sess_" + uuid.uuid4().hex
    sess["hatch_sess"] = hs
    sess["seen"] = True
    resp = JSONResponse(
        {
            "ok": True,
            "redirect_to": "/access",
            "auth_meta_session": {"frl_access_token": "frl_" + uuid.uuid4().hex},
            "date_of_birth": body.get("date_of_birth"),
        }
    )
    resp.set_cookie("hatch_sess", hs)
    return resp


@app.post("/api/auth/native/recovery-start")
async def muse_recovery_start(request: Request):
    import time, uuid
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.setdefault(wid, {"csrf": "", "otp": None, "email": None, "hatch_sess": None})
    email = str(body.get("contact_point") or body.get("email") or sess.get("email") or "")
    code = f"{int(time.time()) % 100000000:08d}"
    sess["recovery_code"] = code
    sess["email"] = email
    _MUSE_MAIL.setdefault(email.lower(), []).insert(
        0,
        {
            "id": "r_" + uuid.uuid4().hex[:8],
            "subject": "Muse recovery code",
            "from": "noreply@muse.local",
            "text": f"recovery {code}",
            "html": f"<p>{code}</p>",
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    masked = (email[:1] + "***@" + email.split("@")[-1]) if "@" in email else "***"
    return {"ok": True, "next_step": "code_sent", "display_contact_point": masked}


@app.post("/api/auth/native/recovery-confirm-code")
async def muse_recovery_confirm(request: Request):
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.get(wid) or {}
    code = str(body.get("code") or body.get("recovery_code") or "")
    if code != sess.get("recovery_code") and code != "00000000":
        raise HTTPException(400, {"type": "invalidCode", "title": "登录信息无效", "detail": "请申请新的验证码并重试", "status": 400})
    sess["recovery_ok"] = True
    return {"ok": True, "next_step": "set_password"}


@app.post("/api/auth/native/recovery-complete")
async def muse_recovery_complete(request: Request):
    import uuid
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.setdefault(wid, {})
    # Accept #PWD_BROWSER wrapped or plain mock field
    pwd = str(body.get("password") or body.get("new_password") or "")
    if pwd.startswith("#PWD_BROWSER"):
        # mock unwrap: last segment after colon-ish; accept any length>=6 payload
        plain = pwd.split(":")[-1] if ":" in pwd else pwd[12:]
    else:
        plain = pwd
    if len(plain) < 6 and plain != "mock-ok":
        # Also accept full wrap as "set" for http client tests
        if len(pwd) < 6:
            raise HTTPException(400, {"message": "weak password"})
        plain = "set-via-wrap"
    sess["password"] = plain if plain != "set-via-wrap" else (sess.get("password") or "wrapped-ok")
    sess["seen"] = True
    hs = "hatch_sess_" + uuid.uuid4().hex
    sess["hatch_sess"] = hs
    resp = JSONResponse(
        {
            "ok": True,
            "redirect_to": "/access",
            "auth_meta_session": {"frl_access_token": "frl_" + uuid.uuid4().hex},
        }
    )
    resp.set_cookie("hatch_sess", hs)
    return resp


@app.post("/api/auth/native/confirm-password")
async def muse_confirm_password(request: Request):
    import uuid
    body = await request.json()
    wid = request.headers.get("x-hatch-waterfall-id") or "default"
    sess = _MUSE_SESS.setdefault(wid, {})
    pwd = str(body.get("password") or "")
    if pwd.startswith("#PWD_BROWSER"):
        plain = pwd.split(":")[-1] if ":" in pwd else pwd
    else:
        plain = pwd
    expected = sess.get("password")
    if not expected:
        raise HTTPException(400, {"message": "password not set"})
    if plain != expected and plain != "mock-ok" and pwd != expected:
        raise HTTPException(400, {"message": "bad password"})
    hs = "hatch_sess_" + uuid.uuid4().hex
    sess["hatch_sess"] = hs
    sess["activated"] = True
    resp = JSONResponse(
        {
            "ok": True,
            "redirect_to": "/access",
            "already_activated": True,
            "auth_meta_session": {"frl_access_token": "frl_" + uuid.uuid4().hex},
        }
    )
    resp.set_cookie("hatch_sess", hs)
    return resp


@app.post("/api/hatch/invite-code/redeem")
async def muse_invite_redeem(request: Request):
    body = await request.json()
    code = str(body.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(400, {"message": "code required"})
    return {"ok": True, "redeemed": True, "code": code, "mock": True}


@app.get("/api/hatch/invite")
async def muse_invite_get():
    return {"ok": True, "codes": [{"code": "MOCK01", "remaining": 3}], "mock": True}


@app.post("/api/hatch/invite")
async def muse_invite_create():
    import uuid
    return {"ok": True, "code": "NEW" + uuid.uuid4().hex[:4].upper(), "remaining": 5, "mock": True}


@app.post("/api/auth/bootstrap")
async def muse_bootstrap():
    return {"ok": True, "bootstrapped": True, "mock": True}


@app.post("/api/hatch/accept-tos")
async def muse_accept_tos():
    return {"success": True}


@app.post("/api/hatch/vm/wake")
async def muse_vm_wake():
    return {"wake_requested": True, "ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=19080)
