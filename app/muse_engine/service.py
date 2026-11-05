"""Process-wide MuseEngine singleton + OpenAI chat adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any, AsyncIterator, Iterator

from app.muse_engine.config import EngineConfig, load_engine_config
from app.muse_engine.engine import (
    MuseAuthError,
    MuseEngine,
    MuseGenerationError,
    MuseProxyError,
)
from app.pool import store as pool_store

logger = logging.getLogger("muse-proxy.muse_engine")


def _account_slot(account_id: str | None) -> str:
    """Anonymous stable slot label (A/B/C…) — never the raw account id."""
    if not account_id:
        return "?"
    try:
        rows = pool_store.list_raw()
        ids = sorted(str(r.get("id") or "") for r in rows if r.get("id"))
        if account_id in ids:
            return chr(ord("A") + (ids.index(account_id) % 26))
    except Exception:
        pass
    return chr(ord("A") + (abs(hash(account_id)) % 26))


def _new_request_timing() -> dict[str, Any]:
    return {
        "request_id": uuid.uuid4().hex[:12],
        "account_slot": None,
        "reuse": False,
        "lock_wait_ms": None,
        "pick_ms": None,
        "renew_ms": None,
        "renew_skipped": False,
        "bootstrap_ms": None,
        "first_delta_ms": None,
        "done_ms": None,
        "total_ms": None,
        "unattributed_ms": None,
    }


def _finalize_timing(timing: dict[str, Any], t0: float) -> dict[str, Any]:
    timing["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    known = 0.0
    for key in ("lock_wait_ms", "pick_ms", "renew_ms", "bootstrap_ms"):
        val = timing.get(key)
        if isinstance(val, (int, float)):
            known += float(val)
    done = timing.get("done_ms")
    if isinstance(done, (int, float)):
        pre = 0.0
        for key in ("lock_wait_ms", "pick_ms", "renew_ms", "bootstrap_ms"):
            val = timing.get(key)
            if isinstance(val, (int, float)):
                pre += float(val)
        timing["generation_ms"] = round(max(0.0, float(done) - pre), 1)
        known += float(timing["generation_ms"])
    timing["unattributed_ms"] = round(max(0.0, float(timing["total_ms"]) - known), 1)
    return timing

_engine: Any = None
_protocol_engine: Any = None
_cfg: EngineConfig | None = None
_lock = threading.Lock()
_gen_sem: asyncio.Semaphore | None = None
_sticky_id: str | None = None

EMPTY_POOL_MSG = (
    "号池无可用账号（需 enabled + hatch_sess）。"
    "请到管理台「号池」页导入 Cookie（至少含 hatch_sess），"
    "或通过「导号/补号」入池后再试。"
)


def engine_config() -> EngineConfig:
    global _cfg
    if _cfg is None:
        _cfg = load_engine_config()
    return _cfg


def engine_backend() -> str:
    from app.config import get_settings

    value = str(getattr(get_settings(), "engine_backend", "local") or "local").strip().lower()
    # local is retained as the backward-compatible CDP spelling.
    return "cdp" if value == "local" else value


def get_engine() -> Any:
    global _engine, _protocol_engine
    backend = engine_backend()
    if backend not in ("cdp", "protocol"):
        raise MuseGenerationError(
            f"Unsupported ENGINE_BACKEND={backend!r}; use protocol or cdp"
        )
    with _lock:
        if backend == "protocol":
            if _protocol_engine is None:
                from app.muse_engine.protocol_engine import ProtocolEngine

                _protocol_engine = ProtocolEngine(engine_config())
                logger.info("muse_engine ready backend=protocol (Noise + chat.stream)")
            return _protocol_engine
        if _engine is None:
            cfg = engine_config()
            _engine = MuseEngine(cfg)
            logger.info("muse_engine ready backend=cdp chromium=%s cdp=%s", cfg.chromium, cfg.cdp_port)
        return _engine


def _gen_semaphore() -> asyncio.Semaphore:
    global _gen_sem
    limit = max(1, int(engine_config().max_concurrency))
    if _gen_sem is None or getattr(_gen_sem, "_muse_limit", None) != limit:
        _gen_sem = asyncio.Semaphore(limit)
        _gen_sem._muse_limit = limit  # type: ignore[attr-defined]
    return _gen_sem


def _account_proxy(acc: dict[str, Any]) -> dict | None:
    # Per-account proxy wins; otherwise retain the legacy global proxy pool.
    # Always normalize to server + separate username/password for HTTP and WS.
    from app.config import get_settings
    from app.muse_engine.browser_proxy import normalize_proxy_dict, parse_proxy_string
    from app.signup.protocol import expand_proxy_sid, parse_proxy_list

    row = pool_store.get(str(acc.get("id") or "")) or {}
    try:
        configured = json.loads(row["proxy_json"]) if row.get("proxy_json") else None
    except json.JSONDecodeError:
        configured = None
    if configured:
        try:
            return normalize_proxy_dict(configured)
        except ValueError as exc:
            raise MuseGenerationError(f"Invalid account proxy: {exc}") from None
    proxies = parse_proxy_list(get_settings().proxies)
    if not proxies:
        return None
    try:
        digest = int(hashlib.sha256(str(acc.get("id") or "").encode()).hexdigest()[:8], 16)
    except Exception:
        digest = 0
    expanded = expand_proxy_sid(proxies[digest % len(proxies)])
    try:
        return parse_proxy_string(expanded)
    except ValueError as exc:
        raise MuseGenerationError(f"Invalid pool proxy: {exc}") from None


def _pick(force_rotate: bool = False, exclude_ids: set[str] | None = None) -> dict[str, Any]:
    global _sticky_id
    cfg = engine_config()
    cap = int(getattr(cfg, "acct_global_daily_cap", 0) or 0)
    if cap and pool_store.global_daily_used() >= cap:
        raise MuseGenerationError("全号每日上限已到（acct_global_daily_cap），请稍后再试")
    blocked = set(exclude_ids or set())

    # Protocol warm sticky: reuse live Noise worker even inside min_interval.
    if (
        engine_backend() == "protocol"
        and _sticky_id
        and not force_rotate
        and _sticky_id not in blocked
    ):
        try:
            from app.muse_engine.protocol_pool import has_warm_worker

            if has_warm_worker(_sticky_id):
                warm = pool_store.get_public(_sticky_id, include_secrets=True)
                if warm and warm.get("enabled", 1) and warm.get("assigned") != 0:
                    cd = warm.get("cooldown_until")
                    if not (cd and int(cd) > int(time.time())):
                        _sticky_id = str(warm["id"])
                        return warm
        except Exception:
            pass

    # Prefer any already-warm idle worker (do not wake new accounts).
    if engine_backend() == "protocol" and not force_rotate:
        try:
            from app.muse_engine.protocol_pool import warm_account_ids

            for wid in warm_account_ids():
                if wid in blocked:
                    continue
                warm = pool_store.get_public(wid, include_secrets=True)
                if not warm or not warm.get("enabled", 1) or warm.get("assigned") == 0:
                    continue
                cd = warm.get("cooldown_until")
                if cd and int(cd) > int(time.time()):
                    continue
                _sticky_id = str(warm["id"])
                return warm
        except Exception:
            pass

    acc = pool_store.pick_account(
        preferred_id=_sticky_id,
        force_rotate=force_rotate,
        exclude_ids=exclude_ids,
        min_interval_sec=int(getattr(cfg, "acct_min_interval_sec", 0) or 0),
        jitter_pct=int(getattr(cfg, "acct_interval_jitter_pct", 0) or 0),
        daily_quota=int(getattr(cfg, "acct_daily_quota", 0) or 0),
    )
    if not acc:
        raise MuseGenerationError(EMPTY_POOL_MSG)
    _sticky_id = str(acc["id"])
    return acc


def _renew_if_needed(
    acc: dict[str, Any], *, skip_http_if_warm: bool = False
) -> tuple[dict[str, str], dict]:
    cookies = dict(acc.get("cookies") or {})
    expires = dict(acc.get("cookies_exp") or {})
    if not cookies:
        cookies, expires = pool_store.get_cookies(str(acc["id"]))
    if skip_http_if_warm and engine_backend() == "protocol":
        try:
            from app.muse_engine.protocol_pool import has_warm_worker

            if has_warm_worker(str(acc.get("id") or "")) and cookies:
                return cookies, expires
        except Exception:
            pass
    proxy = _account_proxy(acc)
    try:
        result = MuseEngine.renew_session_http(
            cookies, expires, wake_vm=True, proxy=proxy
        )
    except MuseAuthError:
        pool_store.update(str(acc["id"]), ok=False, checked=True)
        raise
    new_cookies = result.get("cookies") or cookies
    new_exp = result.get("cookies_exp") or result.get("expires") or expires
    pool_store.apply_session_renewal(
        str(acc["id"]), new_cookies, new_exp, ok=True,
        assigned=bool(result.get("assigned")),
    )
    if not result.get("assigned"):
        raise MuseGenerationError(
            result.get("message") or "登录态有效，但工作区尚未分配（等 VM 供货）"
        )
    return new_cookies, new_exp


def _messages_to_prompt(body: dict[str, Any]) -> str:
    if body.get("prompt"):
        return str(body["prompt"])
    msgs = body.get("messages") or []
    parts: list[str] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "user"
        content = m.get("content")
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(str(c.get("text") or ""))
                elif isinstance(c, str):
                    texts.append(c)
            content = "\n".join(texts)
        if content:
            parts.append(f"{role}: {content}" if role != "user" else str(content))
    return "\n\n".join(parts).strip() or "hi"


def _note_account_failure(acc_id: str, exc: Exception) -> None:
    """Apply 403/408 cooldowns without leaking upstream payloads."""
    if not acc_id:
        return
    msg_l = str(exc)
    cfg2 = engine_config()
    try:
        if "403" in msg_l:
            pool_store.note_cooldown(
                acc_id, int(getattr(cfg2, "acct_403_stop_hours", 24)) * 60
            )
        elif "408" in msg_l:
            pool_store.note_cooldown(
                acc_id,
                int(getattr(cfg2, "acct_408_cooldown_min", 3)),
                streak=True,
                disable_at=int(getattr(cfg2, "acct_408_disable_streak", 3)),
                cap_min=int(getattr(cfg2, "acct_cooldown_cap_min", 30)),
            )
    except Exception:
        pass


def _sync_chat_stream(
    prompt: str,
    *,
    timeout: int | None = None,
    timing: dict[str, Any] | None = None,
) -> Iterator[str]:
    engine = get_engine()
    engine.start()
    last_err: Exception | None = None
    cfg2 = engine_config()
    max_attempts = max(2, min(int(getattr(cfg2, "acct_rotate_attempts", 5) or 5), 8))
    tried_ids: set[str] = set()
    timing = timing if timing is not None else _new_request_timing()
    t0 = time.perf_counter()
    for attempt in range(max_attempts):
        acc: dict[str, Any] = {}
        try:
            t_pick = time.perf_counter()
            acc = _pick(force_rotate=attempt > 0, exclude_ids=set(tried_ids))
            timing["pick_ms"] = round((time.perf_counter() - t_pick) * 1000, 1)
            aid = str(acc.get("id") or "")
            tried_ids.add(aid)
            timing["account_slot"] = _account_slot(aid)
            warm = False
            if engine_backend() == "protocol":
                try:
                    from app.muse_engine.protocol_pool import has_warm_worker
                    warm = has_warm_worker(aid)
                except Exception:
                    warm = False
            timing["reuse"] = bool(warm)
            t_renew = time.perf_counter()
            cookies, expires = _renew_if_needed(acc, skip_http_if_warm=warm)
            timing["renew_ms"] = round((time.perf_counter() - t_renew) * 1000, 1)
            timing["renew_skipped"] = bool(warm and timing["renew_ms"] < 50)
            timeline: list[dict[str, Any]] = []
            first_delta_set = False
            for delta in engine.chat_stream(
                cookies,
                prompt,
                expires,
                timeout=timeout,
                account_id=aid,
                timeline=timeline,
                **({"proxy": _account_proxy(acc)} if engine_backend() == "protocol" else {}),
            ):
                if not first_delta_set and delta:
                    timing["first_delta_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                    first_delta_set = True
                yield delta
            timing["done_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            for item in timeline:
                if isinstance(item, dict) and item.get("timings_ms"):
                    tm = item["timings_ms"]
                    if tm.get("reuse"):
                        timing["reuse"] = True
                    if "bootstrap_ms" in tm:
                        timing["bootstrap_ms"] = tm.get("bootstrap_ms")
                    if "lock_wait_ms" in tm:
                        timing["lock_wait_ms"] = tm.get("lock_wait_ms")
                    break
            _finalize_timing(timing, t0)
            pool_store.note_use(aid)
            pool_store.reset_408(aid)
            return
        except (MuseAuthError, MuseProxyError, MuseGenerationError) as exc:
            last_err = exc
            _note_account_failure(str(acc.get("id") or ""), exc)
            logger.warning("chat attempt %s failed: %s", attempt + 1, exc)
            continue
    _finalize_timing(timing, t0)
    raise last_err or MuseGenerationError("chat failed")


async def chat_completions(body: dict[str, Any]) -> Any:
    """Return StreamingResponse-compatible async iterator OR dict for non-stream."""
    from fastapi.responses import StreamingResponse

    prompt = _messages_to_prompt(body)
    stream = bool(body.get("stream"))
    model = body.get("model") or "muse-spark"
    timing = _new_request_timing()
    timeout = body.get("timeout")
    try:
        timeout_i = int(timeout) if timeout is not None else None
    except (TypeError, ValueError):
        timeout_i = None

    sem = _gen_semaphore()
    await sem.acquire()

    if not stream:
        try:
            text_parts: list[str] = []

            def _run() -> str:
                out = ""
                for d in _sync_chat_stream(prompt, timeout=timeout_i, timing=timing):
                    out += d
                return out

            text = await asyncio.to_thread(_run)
            return {
                "id": "chatcmpl-" + uuid.uuid4().hex[:12],
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "muse_timing": timing,
            }
        finally:
            sem.release()

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _producer() -> None:
        try:
            for d in _sync_chat_stream(prompt, timeout=timeout_i, timing=timing):
                asyncio.run_coroutine_threadsafe(queue.put(d), loop).result()
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()
        except Exception as exc:  # noqa: BLE001
            asyncio.run_coroutine_threadsafe(
                queue.put(f"__ERROR__:{exc}"), loop
            ).result()

    threading.Thread(target=_producer, daemon=True).start()

    async def event_gen() -> AsyncIterator[bytes]:
        cid = "chatcmpl-" + uuid.uuid4().hex[:12]
        try:
            while True:
                item = await queue.get()
                if item is None:
                    payload = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "stop"}
                        ],
                        "muse_timing": timing,
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    break
                if isinstance(item, str) and item.startswith("__ERROR__:"):
                    err = {
                        "error": {
                            "message": item[len("__ERROR__:") :],
                            "type": "server_error",
                            "code": "muse_engine_error",
                        }
                    }
                    yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    break
                payload = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": item},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        finally:
            sem.release()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Muse-Request-Id": str(timing.get("request_id") or ""),
        },
    )


def local_models() -> dict[str, Any]:
    models = [
        {"id": "muse-spark", "object": "model", "owned_by": "muse"},
        {"id": "muse-image", "object": "model", "owned_by": "muse"},
        {"id": "muse-video", "object": "model", "owned_by": "muse"},
        {"id": "gpt-4o-mini", "object": "model", "owned_by": "muse-alias"},
    ]
    return {"object": "list", "data": models}


def _proxy_server_for(aid: str) -> str:
    """账号自己的出口；没有就从系统代理里抽一条新 SID。直连会被地区门挡住。"""
    proxy = pool_store.get_proxy(aid) or {}
    server = str(proxy.get("server") or "").strip()
    if server:
        return server
    from app.config import get_settings
    from app.signup.protocol import pick_proxy

    return pick_proxy(getattr(get_settings(), "proxies", "") or "") or ""


def _lease_workspace(aid: str, server: str) -> None:
    """用注册同一套协议租 standard 工作区。在独立线程里跑，避免挡住正在运行的事件循环。"""

    async def _run() -> None:
        from app.signup.protocol import HttpMuseClient, MuseSession

        cookies, _expires = pool_store.get_cookies(aid)
        row = pool_store.get(aid) or {}
        fp: dict[str, Any] = {}
        raw = row.get("fingerprint_json")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    fp = parsed
            except json.JSONDecodeError:
                fp = {}
        sess = MuseSession(
            cookies=dict(cookies),
            fingerprint=fp,
            proxy_url=server,
            email=str(row.get("email") or ""),
        )
        sess.hatch_sess = cookies.get("hatch_sess") or ""
        client = HttpMuseClient("https://muse.ai", proxy=server or None)
        await client.lease_vm(sess)
        if sess.cookies.get("hatch_sess"):
            pool_store.update(aid, cookies=dict(sess.cookies))

    def _worker() -> None:
        asyncio.run(_run())

    err: dict[str, BaseException] = {}

    def _thread() -> None:
        try:
            _worker()
        except BaseException as exc:  # noqa: BLE001
            err["e"] = exc

    th = threading.Thread(target=_thread, daemon=True)
    th.start()
    th.join()
    if "e" in err:
        raise err["e"]


def test_account_session(aid: str) -> dict[str, Any]:
    cookies, expires = pool_store.get_cookies(aid)
    if not cookies.get("hatch_sess"):
        pool_store.update(aid, ok=False, checked=True)
        return {"ok": False, "error": "missing_hatch_sess"}
    server = _proxy_server_for(aid)
    proxy = {"server": server} if server else None
    try:
        result = MuseEngine.renew_session_http(
            cookies, expires, wake_vm=True, proxy=proxy, require_assigned=False
        )
        if not result.get("assigned") and server:
            try:
                _lease_workspace(aid, server)
                cookies, expires = pool_store.get_cookies(aid)
                result = MuseEngine.renew_session_http(
                    cookies, expires, wake_vm=True, proxy=proxy
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("lease workspace %s failed: %s", aid, exc)
        if server and not (pool_store.get_proxy(aid) or {}).get("server"):
            pool_store.update(aid, proxy={"server": server})
        pool_store.apply_session_renewal(
            aid,
            result.get("cookies") or cookies,
            result.get("cookies_exp") or result.get("expires") or expires,
            ok=True,
            assigned=bool(result.get("assigned")),
        )
        assigned = bool(result.get("assigned"))
        if not assigned:
            try:
                provision_vm(aid)
                result = MuseEngine.renew_session_http(
                    *pool_store.get_cookies(aid),
                    wake_vm=True,
                    proxy=proxy,
                    require_assigned=False,
                )
                assigned = bool(result.get("assigned"))
                pool_store.apply_session_renewal(
                    aid,
                    result.get("cookies") or cookies,
                    result.get("cookies_exp") or expires,
                    ok=True,
                )
            except Exception as _prov_err:  # noqa: BLE001
                provision_error = str(_prov_err)[:160]
        try:
            pool_store.update(aid, assigned=assigned)
        except Exception:
            pass
        row2 = pool_store.get(aid) or {}
        if not row2.get("expires_at"):
            try:
                pool_store.update(aid, expires_at=int(time.time()) + 30 * 86400)
            except Exception:
                pass
        note = ""
        if not assigned:
            if "provision_error" in locals() and provision_error:
                if "403" in provision_error:
                    note = "账号未完成激活（需先 $1 验卡 + accept-tos），暂时无法租用工作区"
                else:
                    note = f"工作区租用失败：{provision_error}"
            else:
                note = "工作区尚未分配（等 VM 供货）"
        return {
            "ok": True,
            "assigned": assigned,
            "status": result.get("status") or "none",
            "vm_state": result.get("vm_state"),
            "message": result.get("message") or note,
            "note": note or result.get("message") or "",
        }
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        geo_blocked = ("403" in msg) or ("地区" in msg) or ("access" in msg.lower())
        if geo_blocked:
            # 403 只说明出口/地区被拦，不能据此判定 Cookie 失效
            pool_store.update(aid, checked=True)
            return {"ok": False, "geo_blocked": True, "error": msg}
        pool_store.update(aid, ok=False, checked=True)
        return {"ok": False, "error": msg}


# --- P3: image / video generation -------------------------------------------

def provision_vm(aid: str, vm_type: str = "standard") -> dict[str, Any]:
    """纯协议租用工作区 VM：POST /api/hatch/lease-vm {"vmType":"standard"}。"""
    import requests

    from app.config import get_settings
    from app.muse_engine.browser_proxy import request_proxy_context
    from app.signup.protocol import expand_proxy_sid, parse_proxy_list

    cookies, _ = pool_store.get_cookies(aid)
    if not cookies.get("hatch_sess"):
        raise MuseAuthError("缺少 hatch_sess，无法租用工作区")
    proxy = pool_store.get_proxy(aid)
    if not proxy:
        plist = parse_proxy_list(get_settings().proxies)
        if plist:
            proxy = {"server": expand_proxy_sid(plist[0])}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
        "Origin": "https://muse.ai",
        "Referer": "https://muse.ai/thread/new",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Content-Type": "application/json",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items() if v),
    }
    with request_proxy_context(proxy) as opts:
        r = requests.post(
            "https://muse.ai/api/hatch/lease-vm",
            headers=headers,
            json={"vmType": vm_type},
            timeout=45,
            **opts,
        )
    if r.status_code != 200:
        raise MuseGenerationError(f"lease-vm HTTP {r.status_code}: {r.text[:160]}")
    data = r.json() if r.content else {}
    if data.get("status") != "assigned" or not data.get("gatewayUrl"):
        raise MuseGenerationError(f"lease-vm 未返回 assigned: {str(data)[:160]}")
    pool_store.update(aid, assigned=True, ok=True, checked=True)
    return {
        "ok": True,
        "vm_name": data.get("vmName"),
        "vm_id": data.get("vmId"),
        "gateway": data.get("gatewayUrl"),
    }


def relogin_password(aid: str) -> dict[str, Any]:
    """纯协议密码重登：restart → send-otp → confirm-password，换新 hatch_sess。"""
    import asyncio

    from app.signup.protocol import HttpMuseClient, MuseSession

    row = pool_store.get(aid) or {}
    email = str(row.get("email") or "").strip()
    pwd = pool_store.get_password(aid)
    if not email or not pwd:
        raise MuseAuthError("账号缺少邮箱或密码，无法自动重登")

    fp = None
    if row.get("fingerprint_json"):
        try:
            fp = json.loads(row["fingerprint_json"])
        except Exception:
            fp = None
    proxy = pool_store.get_proxy(aid)
    proxy_url = (proxy or {}).get("server") or None

    sess = MuseSession(email=email, fingerprint=fp or {})
    client = HttpMuseClient("https://muse.ai", proxy=proxy_url)

    async def _run() -> None:
        await client.restart(sess, resume=False)
        data = await client.send_otp(sess, email, row.get("region") or "US")
        if not data.get("password_login_available"):
            raise MuseAuthError("该账号当前不开放密码登录（password_login_available=false）")
        await client.confirm_password(sess, pwd, remember_device=True)

    asyncio.run(_run())
    cookies = dict(sess.cookies or {})
    if not cookies.get("hatch_sess"):
        raise MuseAuthError("重登成功但未拿到 hatch_sess")
    pool_store.update(aid, cookies=cookies, ok=True, checked=True)
    return {"ok": True, "cookies": cookies, "relogged": True}


def keepalive_account(aid: str) -> dict[str, Any]:
    """保活：/api/session?vm_state=live 续签；401 时自动密码重登再续签。"""
    cookies, expires = pool_store.get_cookies(aid)
    proxy = pool_store.get_proxy(aid)
    relogged = False
    try:
        if not cookies.get("hatch_sess"):
            raise MuseAuthError("missing hatch_sess")
        result = MuseEngine.renew_session_http(
            cookies, expires, wake_vm=True, proxy=proxy, require_assigned=False
        )
    except MuseAuthError:
        if not pool_store.get_password(aid):
            raise
        relogin_password(aid)
        relogged = True
        cookies, expires = pool_store.get_cookies(aid)
        result = MuseEngine.renew_session_http(
            cookies, expires, wake_vm=True, proxy=proxy, require_assigned=False
        )

    pool_store.apply_session_renewal(
        aid,
        result.get("cookies") or cookies,
        result.get("cookies_exp") or expires,
        ok=True,
        assigned=bool(result.get("assigned")),
    )
    try:
        pool_store.update(aid, assigned=bool(result.get("assigned")))
    except Exception:
        pass
    return {
        "ok": True,
        "assigned": bool(result.get("assigned")),
        "status": result.get("status"),
        "message": result.get("message") or "",
        "vm_state": result.get("vm_state"),
        "refreshed": result.get("refreshed"),
        "relogged": relogged,
    }


def quota(aid: str) -> dict[str, Any]:
    """CDP quota read for one pool account; renew first so Cookie expires stay real."""
    cookies, expires = pool_store.get_cookies(aid)
    if not cookies.get("hatch_sess"):
        raise MuseGenerationError("missing_hatch_sess")
    row = pool_store.get(aid) or {}
    proxy = None
    if row.get("proxy_json"):
        try:
            proxy = json.loads(row["proxy_json"])
        except json.JSONDecodeError:
            proxy = None
    try:
        renewed = MuseEngine.renew_session_http(
            cookies, expires, wake_vm=True, proxy=proxy, require_assigned=False
        )
        cookies = renewed.get("cookies") or cookies
        expires = renewed.get("cookies_exp") or renewed.get("expires") or expires
        pool_store.apply_session_renewal(
            aid, cookies, expires, ok=True, assigned=bool(renewed.get("assigned"))
        )
        if not renewed.get("assigned"):
            raise MuseGenerationError(
                renewed.get("message") or "登录态有效，但工作区尚未分配（等 VM 供货）"
            )
    except MuseAuthError:
        pool_store.update(aid, ok=False, checked=True)
        raise
    if not renewed.get("assigned"):
        # 工作区没分配时页面里没有额度面板，直接给出明确原因，避免照旧报错
        return {
            "found": False,
            "reason": "workspace_not_assigned",
            "status": renewed.get("status"),
            "message": "工作区尚未分配（等 VM 供货），暂时读不到额度",
        }
    eng = get_engine()
    return eng.quota(cookies, expires, account_id=aid)


def media_url(name: str) -> str:
    from app.config import get_settings

    base = (getattr(get_settings(), "public_base", None) or "").rstrip("/")
    return f"{base}/v1/media/{name}" if base else f"/v1/media/{name}"


def media_path(name: str) -> str:
    import os

    return os.path.join(engine_config().media_dir, name)


def prepare_reference_images(images: list[str]) -> list[str]:
    """Validate and snapshot refs as data URLs before queuing."""
    import base64
    import binascii

    from app.muse_engine.engine import (
        MAX_REFERENCE_TOTAL_BYTES,
        MuseGenerationError,
        RefImageSafetyError,
    )

    engine = get_engine()
    prepared: list[str] = []
    total_bytes = 0
    for index, image in enumerate(images):
        try:
            b64, mime = engine._normalize_image(image)
            data = base64.b64decode(b64, validate=True)
        except (RefImageSafetyError, MuseGenerationError, binascii.Error, ValueError) as exc:
            raise MuseGenerationError(f"images[{index}]: {exc}") from exc
        total_bytes += len(data)
        if total_bytes > MAX_REFERENCE_TOTAL_BYTES:
            raise MuseGenerationError(
                f"参考素材总大小超过 {MAX_REFERENCE_TOTAL_BYTES // (1024 * 1024)} MiB 限制"
            )
        prepared.append(f"data:{mime};base64,{b64}")
    return prepared


def run_generation(
    prompt: str,
    expect: str,
    timeout: int | None = None,
    *,
    reference_images: list[str] | None = None,
    on_progress=None,
) -> tuple[dict[str, Any], str]:
    """Run the selected protocol/CDP backend; returns (result_dict, account_id)."""
    engine = get_engine()
    engine.start()
    cfg = engine_config()
    if timeout is None:
        timeout = cfg.video_timeout if expect == "video" else cfg.image_timeout
    last_err: Exception | None = None
    t0 = time.time()
    log_lines: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    def _log(msg: str, level: str = "info") -> None:
        log_lines.append({"t": int(time.time() * 1000), "lv": level, "msg": str(msg)[:300]})

    def _acc_label(acc_row: dict[str, Any]) -> str:
        try:
            from app.pool import store as _ps

            row = _ps.get(str(acc_row.get("id"))) or {}
            return str(row.get("label") or row.get("name") or "")
        except Exception:
            return ""

    _log(f"开始生成 · {expect} · 提示词 {prompt[:60]}")
    cfg2 = engine_config()
    max_attempts = max(1, min(int(getattr(cfg2, "acct_rotate_attempts", 5) or 5), 8))
    tried_ids: set[str] = set()
    for attempt in range(max_attempts):
        at0 = time.time()
        acc_row: dict[str, Any] = {}
        try:
            _log(f"第 {attempt + 1}/{max_attempts} 次尝试")
            acc = _pick(force_rotate=attempt > 0, exclude_ids=set(tried_ids))
            tried_ids.add(str(acc["id"]))
            acc_row = dict(acc)
            label = _acc_label(acc_row)
            _log(f"选中账号 {label or acc_row.get('id')}（{acc_row.get('id')}）")
            cookies, expires = _renew_if_needed(acc)
            _log("续签会话（/api/session）")
            acc_fp = None
            raw_fp = acc.get("fingerprint_json") or acc.get("fingerprint")
            if raw_fp:
                try:
                    acc_fp = raw_fp if isinstance(raw_fp, dict) else json.loads(str(raw_fp))
                except Exception:
                    acc_fp = None
            res = engine.generate(
                cookies,
                prompt,
                expect=expect,
                timeout=int(timeout),
                expires=expires,
                account_id=str(acc["id"]),
                on_progress=on_progress,
                reference_images=reference_images or None,
                on_log=lambda m: _log(m),
                fingerprint=acc_fp,
                **({"proxy": _account_proxy(acc)} if engine_backend() == "protocol" else {}),
            )
            _log(f"生成完成 · 用时 {time.time() - t0:.1f}s · 文件 {res.get('filename')}")
            attempts.append(
                {
                    "at": int(at0 * 1000),
                    "account": label,
                    "accountId": str(acc_row.get("id") or ""),
                    "ticket": "",
                    "profile": "",
                    "sid": "",
                    "region": "",
                    "status": 200,
                    "outcome": "success",
                    "ms": int((time.time() - at0) * 1000),
                    "error": "",
                }
            )
            pool_store.note_use(str(acc["id"]))
            pool_store.reset_408(str(acc["id"]))
            res["_engine_log"] = log_lines
            res["_engine_attempts"] = attempts
            return res, str(acc["id"])
        except (MuseAuthError, MuseProxyError, MuseGenerationError) as exc:
            last_err = exc
            _note_account_failure(str(acc_row.get("id") or ""), exc)
            attempts.append(
                {
                    "at": int(at0 * 1000),
                    "account": _acc_label(acc_row) if acc_row else "",
                    "accountId": str(acc_row.get("id") or "") if acc_row else "",
                    "ticket": "",
                    "profile": "",
                    "sid": "",
                    "region": "",
                    "status": 500,
                    "outcome": "http_error",
                    "ms": int((time.time() - at0) * 1000),
                    "error": str(exc)[:300],
                }
            )
            logger.warning("%s attempt %s failed: %s", expect, attempt + 1, exc)
            continue
    raise last_err or MuseGenerationError(f"{expect} failed")


def image_response(
    prompt: str,
    res: dict[str, Any],
    *,
    size: str | None = None,
    response_format: str = "url",
) -> dict[str, Any]:
    import base64
    import os

    item: dict[str, Any] = {
        "revised_prompt": prompt,
        "url": media_url(res["filename"]),
        "size": size or "auto",
        "kind": res.get("kind"),
        "bytes": res.get("size"),
    }
    if response_format == "b64_json":
        fpath = res.get("path") or media_path(res["filename"])
        with open(fpath, "rb") as f:
            item["b64_json"] = base64.b64encode(f.read()).decode()
        item.pop("url", None)
    return {"created": int(time.time()), "data": [item]}


def queue_image_task(
    *,
    prompt: str,
    api_prompt: str,
    reference_images: list[str],
    size: str | None = None,
    response_format: str = "url",
    timeout: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    import hashlib

    from app.muse_engine import tasks as task_store
    from fastapi.responses import JSONResponse

    if idempotency_key and len(idempotency_key) > 256:
        raise MuseGenerationError("Idempotency-Key too long")
    key_hash = (
        hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
    )
    request_hash = hashlib.sha256(
        json.dumps(
            {"prompt": api_prompt, "references": reference_images, "size": size},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    if key_hash:
        for existing in task_store.list_tasks(200):
            if existing.get("image_request_key") == key_hash:
                if existing.get("image_request_hash") != request_hash:
                    raise MuseGenerationError("Idempotency-Key reused with different request")
                return JSONResponse(
                    status_code=202,
                    content={
                        "id": existing["id"],
                        "task_id": existing["id"],
                        "object": "image.task",
                        "status": existing["status"],
                        "progress": existing.get("progress", 0),
                        "created_at": existing["created_at"],
                    },
                )

    if task_store.count_active("image") >= 8:
        raise MuseGenerationError("Image queue is full; retry later")

    task = task_store.create_task("image", prompt)
    tid = task["id"]
    task_store.update_task(
        tid,
        api_prompt=api_prompt,
        size=size,
        response_format=response_format,
        progress=0,
        image_request_key=key_hash,
        image_request_hash=request_hash,
    )
    cfg = engine_config()
    to = timeout or cfg.image_timeout

    def worker() -> None:
        timing = {"t0": time.time()}

        def _on_acquired() -> None:
            timing["t0"] = time.time()
            task_store.update_task(tid, status="processing", progress=5)

        try:
            _on_acquired()
            res, acc_id = run_generation(
                api_prompt,
                "image",
                to,
                reference_images=reference_images,
                on_progress=lambda p: task_store.update_task(tid, progress=p),
            )
            done_url = media_url(res["filename"])
            task_store.update_task(
                tid,
                status="completed",
                progress=100,
                account=acc_id,
                elapsed=round(time.time() - timing["t0"], 1),
                url=done_url,
                log=res.get("_engine_log"),
                attempts=res.get("_engine_attempts"),
                result={
                    **{k: res[k] for k in ("filename", "size", "kind") if k in res},
                    "url": done_url,
                    "path": res.get("path"),
                },
            )
            from app.call_log import finish_task_log

            finish_task_log(tid, media_urls=[done_url], status=200)
        except Exception as exc:  # noqa: BLE001
            task_store.update_task(
                tid,
                status="failed",
                error=str(exc),
                elapsed=round(time.time() - timing["t0"], 1),
            )
            from app.call_log import finish_task_log

            finish_task_log(tid, status=502, error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return JSONResponse(
        status_code=202,
        content={
            "id": tid,
            "task_id": tid,
            "object": "image.task",
            "status": "queued",
            "progress": 0,
            "created_at": task["created_at"],
        },
    )


def queue_video_task(
    *,
    prompt: str,
    api_prompt: str,
    reference_images: list[str],
    timeout: int | None = None,
) -> dict[str, Any]:
    from app.muse_engine import tasks as task_store

    task = task_store.create_task("video", prompt)
    tid = task["id"]
    task_store.update_task(tid, api_prompt=api_prompt, progress=10)
    cfg = engine_config()
    to = timeout or cfg.video_timeout

    def worker() -> None:
        timing = {"t0": time.time()}
        try:
            task_store.update_task(tid, status="processing", progress=15)
            res, acc_id = run_generation(
                api_prompt,
                "video",
                to,
                reference_images=reference_images,
                on_progress=lambda p: task_store.update_task(tid, progress=p),
            )
            vurl = media_url(res["filename"])
            task_store.update_task(
                tid,
                status="completed",
                progress=100,
                account=acc_id,
                elapsed=round(time.time() - timing["t0"], 1),
                url=vurl,
                log=res.get("_engine_log"),
                attempts=res.get("_engine_attempts"),
                video={"url": vurl},
                result={
                    "url": vurl,
                    "filename": res["filename"],
                    "bytes": res.get("size"),
                    "kind": res.get("kind"),
                    "path": res.get("path"),
                },
            )
            from app.call_log import finish_task_log

            finish_task_log(tid, media_urls=[vurl], status=200)
        except Exception as exc:  # noqa: BLE001
            task_store.update_task(
                tid,
                status="failed",
                elapsed=round(time.time() - timing["t0"], 1),
                error=str(exc),
            )
            from app.call_log import finish_task_log

            finish_task_log(tid, status=502, error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return {
        "id": tid,
        "task_id": tid,
        "object": "video.task",
        "status": "queued",
        "progress": 10,
        "created_at": task["created_at"],
    }


def get_image_task_view(task_id: str) -> dict[str, Any] | None:
    from app.muse_engine import tasks as task_store

    task = task_store.get_task(task_id)
    if not task or task.get("kind") != "image":
        return None
    out = dict(task)
    if out.get("status") == "completed" and isinstance(out.get("result"), dict):
        fmt = out.get("response_format") or "url"
        out.update(
            image_response(
                out.get("prompt") or "",
                out["result"],
                size=out.get("size"),
                response_format=fmt,
            )
        )
    return out


def get_video_task_view(task_id: str) -> dict[str, Any] | None:
    from app.muse_engine import tasks as task_store

    t = task_store.get_task(task_id)
    if not t:
        return None
    out = dict(t)
    status = out.get("status")
    if status in ("succeeded", "success", "done"):
        out["status"] = "completed"
    if out.get("status") == "completed":
        out["progress"] = 100
    elif out.get("status") == "processing":
        out["progress"] = max(out.get("progress", 0) or 0, 15)
    vurl = out.get("url")
    if not vurl and isinstance(out.get("result"), dict):
        vurl = out["result"].get("url")
    if vurl:
        out["url"] = vurl
        if "video" not in out:
            out["video"] = {"url": vurl}
    return out
