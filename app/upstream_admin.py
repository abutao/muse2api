"""muse2api /admin HTTP client (Bearer = UPSTREAM_API_KEY)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("muse-proxy.upstream_admin")

# Keys that must never reach the admin SPA / logs.
_SECRET_KEYS = frozenset(
    {
        "cookies",
        "cookie",
        "cookie_header",
        "cookie_blob",
        "cookies_exp",
        "proxy_password",
        "password",
    }
)


class UpstreamAdminError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        detail: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail


def sanitize_account(obj: Any) -> Any:
    """Strip cookie/secret fields recursively; keep cookie_count etc."""
    if isinstance(obj, list):
        return [sanitize_account(x) for x in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in _SECRET_KEYS:
                continue
            if k == "proxy" and isinstance(v, dict):
                # public_proxy shape: server/username/has_password — drop password
                out[k] = {
                    pk: pv
                    for pk, pv in v.items()
                    if pk not in ("password", "proxy_password")
                }
            else:
                out[k] = sanitize_account(v)
        return out
    return obj


def _base() -> str:
    s = get_settings()
    if not s.upstream_base_url:
        raise UpstreamAdminError("upstream_base_url not configured", status_code=503)
    if not s.upstream_api_key:
        raise UpstreamAdminError("upstream_api_key not configured", status_code=503)
    return s.upstream_base_url.rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().upstream_api_key}",
        "Accept": "application/json",
    }


def _timeout() -> httpx.Timeout:
    s = get_settings()
    return httpx.Timeout(
        connect=s.connect_timeout_sec,
        read=min(120.0, float(s.request_timeout_sec)),
        write=30.0,
        pool=10.0,
    )


async def request(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    url = _base() + "/" + path.lstrip("/")
    # Never log bodies that may contain cookies
    logger.info("upstream_admin %s %s", method.upper(), path)
    try:
        async with httpx.AsyncClient(timeout=_timeout(), follow_redirects=False) as client:
            resp = await client.request(
                method.upper(),
                url,
                headers=_headers(),
                json=json_body,
                params=params,
            )
    except httpx.ConnectError as exc:
        raise UpstreamAdminError(
            f"Upstream unreachable: {_base()}",
            status_code=502,
            detail=str(exc),
        ) from exc
    except httpx.TimeoutException as exc:
        raise UpstreamAdminError(
            "Upstream timeout",
            status_code=504,
            detail=str(exc),
        ) from exc
    except httpx.HTTPError as exc:
        raise UpstreamAdminError(
            f"Upstream request failed: {exc}",
            status_code=502,
        ) from exc

    ctype = (resp.headers.get("content-type") or "").lower()
    data: Any
    if "application/json" in ctype:
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}
    else:
        data = {"raw": (resp.text or "")[:500]}

    if resp.status_code >= 400:
        msg = None
        if isinstance(data, dict):
            msg = data.get("detail") or data.get("error") or data.get("message")
        raise UpstreamAdminError(
            str(msg) if msg else f"Upstream HTTP {resp.status_code}",
            status_code=resp.status_code if resp.status_code < 500 else 502,
            detail=sanitize_account(data) if isinstance(data, (dict, list)) else data,
        )
    return sanitize_account(data)


async def list_accounts() -> dict[str, Any]:
    data = await request("GET", "/admin/accounts")
    if not isinstance(data, dict):
        return {"accounts": [], "stats": {}}
    return data


async def admin_status() -> dict[str, Any]:
    return await request("GET", "/admin/status")


async def add_accounts(body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", "/admin/accounts", json_body=body)


async def patch_account(aid: str, body: dict[str, Any]) -> dict[str, Any]:
    return await request("PATCH", f"/admin/accounts/{aid}", json_body=body)


async def delete_account(aid: str) -> dict[str, Any]:
    return await request("DELETE", f"/admin/accounts/{aid}")


async def update_cookies(aid: str, body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", f"/admin/accounts/{aid}/cookies", json_body=body)


async def test_account(aid: str) -> dict[str, Any]:
    return await request("POST", f"/admin/accounts/{aid}/test")


async def query_quota(aid: str) -> dict[str, Any]:
    return await request("POST", f"/admin/accounts/{aid}/quota")


async def public_health() -> dict[str, Any]:
    """Unauthenticated upstream /healthz (best-effort)."""
    s = get_settings()
    base = (s.upstream_base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "no_upstream"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            r = await client.get(base + "/healthz")
            ready = None
            try:
                rr = await client.get(base + "/readyz")
                ready = {"status": rr.status_code, "body": rr.json() if "json" in (rr.headers.get("content-type") or "") else rr.text[:200]}
            except Exception:
                ready = None
            body: Any
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:200]}
            return {"ok": r.status_code < 400, "healthz": body, "readyz": ready, "status_code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


async def relogin_account(aid: str) -> dict[str, Any]:
    return await request("POST", f"/admin/accounts/{aid}/relogin")


async def keepalive_account(aid: str) -> dict[str, Any]:
    return await request("POST", f"/admin/accounts/{aid}/keepalive")


async def keepalive_batch(ids: list[str] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if ids:
        body["ids"] = ids
    return await request("POST", "/admin/keepalive", json_body=body)
