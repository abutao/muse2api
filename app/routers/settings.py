"""Settings APIs — full config cards + outbound API keys CRUD. Local engine only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app import api_keys
from app.auth_admin import require_admin
from app.config import get_settings, reload_settings
from app.runtime_settings import load_overrides, set_overrides

router = APIRouter(prefix="/settings", tags=["settings"])


def _public_config() -> dict[str, Any]:
    s = get_settings()
    return {
        "public_base": s.public_base,
        "engine_backend": "local",
        "chromium_path": getattr(s, "chromium_path", "") or "",
        "cdp_port": getattr(s, "cdp_port", 19211),
        "engine_concurrency": getattr(s, "engine_concurrency", 2),
        "connect_timeout_sec": s.connect_timeout_sec,
        "request_timeout_sec": s.request_timeout_sec,
        "stream_idle_timeout_sec": s.stream_idle_timeout_sec,
        "max_concurrency": s.max_concurrency,
        "media_auth_required": s.media_auth_required,
        "rewrite_media_urls": s.rewrite_media_urls,
        "console_generate_enabled": s.console_generate_enabled,
        "record_keep_days": s.record_keep_days,
        "charge_on_upstream_error": s.charge_on_upstream_error,
        "proxy_scheduled_keepalive": s.proxy_scheduled_keepalive,
        "signup_client": s.signup_client,
        "signup_muse_base": s.signup_muse_base,
        "default_region": s.default_region,
        "default_into_pool": s.default_into_pool,
        "queue_wait_sec": getattr(s, "queue_wait_sec", 25),
        "signup_concurrency": getattr(s, "signup_concurrency", 2),
        "signup_interval_sec": getattr(s, "signup_interval_sec", 30),
        "signup_daily_enabled": getattr(s, "signup_daily_enabled", False),
        "signup_daily_hour": getattr(s, "signup_daily_hour", 10),
        "signup_daily_minute": getattr(s, "signup_daily_minute", 0),
        "signup_daily_count": getattr(s, "signup_daily_count", 20),
        "acct_min_interval_sec": getattr(s, "acct_min_interval_sec", 20),
        "acct_interval_jitter_pct": getattr(s, "acct_interval_jitter_pct", 40),
        "acct_global_daily_cap": getattr(s, "acct_global_daily_cap", 0),
        "acct_daily_quota": getattr(s, "acct_daily_quota", 3),
        "acct_rotate_attempts": getattr(s, "acct_rotate_attempts", 5),
        "acct_408_cooldown_min": getattr(s, "acct_408_cooldown_min", 3),
        "acct_408_disable_streak": getattr(s, "acct_408_disable_streak", 3),
        "acct_cooldown_cap_min": getattr(s, "acct_cooldown_cap_min", 30),
        "acct_403_stop_hours": getattr(s, "acct_403_stop_hours", 24),
        "proxies": getattr(s, "proxies", "") or "",
        "result_mode": getattr(s, "result_mode", "direct") or "direct",
        "admin_user": s.admin_user,
        "admin_password_set": bool(s.admin_password),
        "env_outbound_keys": len(s.outbound_keys()),
        "token_prefix": s.token_prefix,
        "outbound_key": (load_overrides().get("outbound_key_display") or ""),
        "overrides": sorted(load_overrides().keys()),
        # deprecated dual-run fields always empty in API surface
        "upstream_base_url": "",
        "upstream_api_key_masked": "",
        "upstream_key_configured": False,
    }


@router.get("/summary")
async def settings_summary(request: Request):
    require_admin(request)
    return {"ok": True, **_public_config()}


@router.get("")
async def settings_get(request: Request):
    require_admin(request)
    return {"ok": True, "config": _public_config()}


class SettingsPatch(BaseModel):
    public_base: str | None = None
    engine_backend: str | None = None  # forced local; ignored if upstream
    chromium_path: str | None = None
    cdp_port: int | None = None
    engine_concurrency: int | None = None
    connect_timeout_sec: float | None = None
    request_timeout_sec: float | None = None
    stream_idle_timeout_sec: float | None = None
    max_concurrency: int | None = None
    media_auth_required: bool | None = None
    rewrite_media_urls: bool | None = None
    console_generate_enabled: bool | None = None
    record_keep_days: int | None = None
    charge_on_upstream_error: bool | None = None
    proxy_scheduled_keepalive: bool | None = None
    signup_client: str | None = None
    signup_muse_base: str | None = None
    default_region: str | None = None
    default_into_pool: bool | None = None
    queue_wait_sec: int | None = None
    signup_concurrency: int | None = Field(default=None, ge=1, le=10)
    signup_interval_sec: int | None = None
    signup_daily_enabled: bool | None = None
    signup_daily_hour: int | None = None
    signup_daily_minute: int | None = None
    signup_daily_count: int | None = None
    acct_min_interval_sec: int | None = None
    acct_interval_jitter_pct: int | None = None
    acct_global_daily_cap: int | None = None
    acct_daily_quota: int | None = None
    acct_rotate_attempts: int | None = None
    acct_408_cooldown_min: int | None = None
    acct_408_disable_streak: int | None = None
    acct_cooldown_cap_min: int | None = None
    acct_403_stop_hours: int | None = None
    proxies: str | None = None
    result_mode: str | None = None
    admin_password: str | None = None
    admin_password_confirm: str | None = None
    # accepted but cleared — no 19080 dual-run
    upstream_base_url: str | None = None
    upstream_api_key: str | None = None


@router.patch("")
async def settings_patch(body: SettingsPatch, request: Request):
    require_admin(request)
    data = body.model_dump(exclude_unset=True)
    data.pop("admin_password_confirm", None)

    if "admin_password" in data:
        pwd = data.get("admin_password")
        confirm = body.admin_password_confirm
        if pwd:
            if confirm is not None and pwd != confirm:
                raise HTTPException(400, detail={"error": "password_mismatch"})
            if len(pwd) < 6:
                raise HTTPException(400, detail={"error": "password_too_short"})
        else:
            data.pop("admin_password", None)

    # Force local; clear any upstream dual-run leftovers
    data["engine_backend"] = "local"
    data["upstream_base_url"] = ""
    data["upstream_api_key"] = ""

    patch = {
        k: v
        for k, v in data.items()
        if v is not None or k in ("signup_muse_base", "proxies", "chromium_path", "upstream_base_url", "upstream_api_key")
    }
    set_overrides(patch)
    reload_settings()
    return {"ok": True, "config": _public_config()}


@router.post("/test-upstream")
@router.post("/test-engine")
async def test_engine(request: Request):
    """Local muse_engine health (Chromium + pool). Path /test-upstream kept as alias."""
    require_admin(request)
    from app.pool import store as pool_store
    from app.muse_engine.config import detect_chromium, load_engine_config

    local: dict[str, Any] = {"ok": False, "backend": "local"}
    try:
        cfg = load_engine_config()
        chrome = detect_chromium()
        stats = pool_store.stats()
        local = {
            "ok": bool(chrome),
            "backend": "local",
            "chromium": chrome,
            "cdp_port": cfg.cdp_port,
            "pool": stats,
            "message": "local muse_engine ready" if chrome else "chromium not found",
        }
    except Exception as exc:  # noqa: BLE001
        local = {"ok": False, "backend": "local", "error": str(exc)}

    return {
        "ok": bool(local.get("ok")),
        "local": local,
        "health": {"ok": True, "skipped": True, "message": "no UPSTREAM (local only)"},
        "admin": {"ok": True, "skipped": True},
    }


@router.post("/proxy-check")
async def proxy_check(request: Request):
    """Report each proxy's real exit IP / geo / proxy+hosting flags.

    Muse hatch（checkout/chat）按出口 IP 地域开放：需要美区且非机房/代理标记的
    住宅出口，否则账号会被 waitlist/geo-gate，hatch 接口全 403。
    """
    require_admin(request)
    import httpx

    from app.signup.protocol import mask_proxy_url, parse_proxy_list

    settings = get_settings()
    from app.signup.protocol import expand_proxy_sid

    raw = ""
    try:
        body = await request.json()
        if isinstance(body, dict):
            raw = str(body.get("proxies") or body.get("proxy") or "").strip()
    except Exception:
        raw = ""
    source = raw if raw else (settings.proxies or "")
    pool = [expand_proxy_sid(p) for p in parse_proxy_list(source)]
    if not pool:
        return {"ok": True, "results": [], "hint": "未配置代理，请先在代理列表中填写"}
    results: list[dict[str, Any]] = []
    fields = "status,country,countryCode,regionName,city,isp,org,proxy,hosting,query"
    for raw in pool[:30]:
        rec: dict[str, Any] = {"proxy": mask_proxy_url(raw) or "(direct)", "ok": False}
        kwargs: dict[str, Any] = {"timeout": 20, "trust_env": False}
        if raw:
            kwargs["proxy"] = raw
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                r = await client.get(f"http://ip-api.com/json/?fields={fields}")
                data = r.json()
            rec.update(
                {
                    "ok": data.get("status") == "success",
                    "ip": data.get("query"),
                    "country": data.get("country"),
                    "country_code": data.get("countryCode"),
                    "city": data.get("city"),
                    "isp": data.get("isp"),
                    "org": data.get("org"),
                    "proxy_flag": bool(data.get("proxy")),
                    "hosting": bool(data.get("hosting")),
                }
            )
            rec["muse_open"] = (
                rec["country_code"] == "US"
                and not rec["proxy_flag"]
                and not rec["hosting"]
            )
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"[:160]
        results.append(rec)
    if results and not any(r.get("ok") for r in results):
        errs = " ".join(str(r.get("error") or "") for r in results)
        if any(k in errs for k in ("SSL", "EOF", "502", "ProtocolError", "Malformed")):
            return {
                "ok": True,
                "results": results,
                "system_proxy_hint": (
                    "全部出口失败且带 SSL/EOF/502：通常是本机系统代理或其节点失效拦截了出站，"
                    "请关闭系统代理或换节点后重试"
                ),
            }
    return {
        "ok": True,
        "results": results,
        "hint": "muse_open=true 才可能通过 hatch 地域门（US 住宅、非机房/代理出口）",
    }


@router.get("/outbound-key")
async def get_outbound_key(request: Request):
    require_admin(request)
    from app.runtime_settings import load_overrides

    return {"ok": True, "token": load_overrides().get("outbound_key_display") or ""}


@router.post("/outbound-key/regenerate")
async def regenerate_outbound_key(request: Request):
    require_admin(request)
    from app import api_keys
    from app.runtime_settings import set_overrides

    name = "对外调用"
    existing = next((k for k in api_keys.list_keys() if (k.get("name") or "") == name), None)
    if existing:
        item = api_keys.rotate_key(int(existing["id"]))
    else:
        item = api_keys.create_key(name=name)
    token = (item or {}).get("token") or ""
    if not token:
        raise HTTPException(status_code=500, detail={"error": "rotate_failed"})
    set_overrides({"outbound_key_display": token})
    return {"ok": True, "token": token, "id": (item or {}).get("id")}


@router.get("/keys")
async def list_keys(request: Request):
    require_admin(request)
    return {"ok": True, "items": api_keys.list_keys()}


class KeyCreate(BaseModel):
    name: str = ""
    quota_total: int | None = Field(default=None)
    daily_limit: int | None = Field(default=None)
    models_allow: str | list[str] | None = None
    enabled: bool = True
    expires_at: str | None = None


class KeyPatch(BaseModel):
    name: str | None = None
    quota_total: int | None = None
    daily_limit: int | None = None
    models_allow: str | list[str] | None = None
    enabled: bool | None = None
    expires_at: str | None = None
    reset_quota_used: bool | None = None
    reset_daily_used: bool | None = None


@router.post("/keys")
async def create_key(body: KeyCreate, request: Request):
    require_admin(request)
    item = api_keys.create_key(
        name=body.name,
        quota_total=body.quota_total,
        daily_limit=body.daily_limit,
        models_allow=body.models_allow,
        enabled=body.enabled,
        expires_at=body.expires_at,
    )
    return {"ok": True, "item": item}


@router.patch("/keys/{key_id}")
async def patch_key(key_id: int, body: KeyPatch, request: Request):
    require_admin(request)
    data = body.model_dump(exclude_unset=True)
    reset_q = data.pop("reset_quota_used", None)
    reset_d = data.pop("reset_daily_used", None)
    if reset_q:
        data["quota_used"] = 0
    if reset_d:
        data["daily_used"] = 0
    item = api_keys.update_key(key_id, **data)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "item": item}


@router.post("/keys/{key_id}/rotate")
async def rotate_key(key_id: int, request: Request):
    require_admin(request)
    item = api_keys.rotate_key(key_id)
    if not item:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "item": item}


@router.delete("/keys/{key_id}")
async def delete_key(key_id: int, request: Request):
    require_admin(request)
    ok = api_keys.delete_key(key_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    return {"ok": True, "deleted": key_id}
