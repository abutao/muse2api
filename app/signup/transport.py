"""HTTP transport with an optional real-browser TLS fingerprint.

httpx/OpenSSL has a distinctive TLS + HTTP/2 signature; muse.ai (Meta edge)
rejects it unless the browser headers are convincing, and JA3/JA4 checks can
still flag it. When ``curl_cffi`` is installed we drive libcurl-impersonate with
a Chrome/Edge client hello, so the TLS/JA3/JA4 + HTTP/2 SETTINGS match a real
browser — and we still send the HAR-derived headers from ``fingerprint.py``.

Everything is normalised into :class:`HttpResult`, so the protocol client does
not care which backend ran.
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("muse-proxy.signup.transport")

try:  # optional dependency
    from curl_cffi.requests import AsyncSession as _CurlSession  # type: ignore

    CURL_CFFI = True
except Exception:  # pragma: no cover - import guard
    _CurlSession = None  # type: ignore
    CURL_CFFI = False


@dataclass
class HttpResult:
    status_code: int
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    url: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def header(self, name: str) -> str:
        low = name.lower()
        for k, v in self.headers:
            if k.lower() == low:
                return v
        return ""

    def header_list(self, name: str) -> list[str]:
        low = name.lower()
        return [v for k, v in self.headers if k.lower() == low]

    def json(self) -> Any:
        return _json.loads(self.body.decode("utf-8", "replace"))

    def cookies(self) -> dict[str, str]:
        """Apply Set-Cookie in order (Max-Age=0 deletes)."""
        jar: dict[str, str] = {}
        for raw in self.header_list("set-cookie"):
            first = raw.split(";", 1)[0]
            if "=" not in first:
                continue
            name, value = first.split("=", 1)
            name = name.strip()
            if not name:
                continue
            low = raw.lower()
            deleted = "max-age=0" in low.replace(" ", "") or "expires=thu, 01 jan 1970" in low
            if deleted:
                jar.pop(name, None)
                continue
            jar[name] = value.strip()
        return jar


def impersonate_target() -> str:
    """Which browser client-hello to imitate ('' disables curl_cffi)."""
    from app.config import get_settings

    try:
        raw = (getattr(get_settings(), "signup_tls_impersonate", "") or "").strip()
    except Exception:  # pragma: no cover
        raw = ""
    return raw.lower()


def _backend_curl(target: str) -> bool:
    if not target or target in ("off", "none", "0", "false", "httpx"):
        return False
    if not CURL_CFFI:
        logger.debug("curl_cffi not installed; falling back to httpx (OpenSSL TLS)")
        return False
    return True


async def send(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Any = None,
    form: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
    proxy: str | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 8,
    timeout: float = 30.0,
    connect_timeout: float = 20.0,
    impersonate: str | None = None,
) -> HttpResult:
    target = impersonate_target() if impersonate is None else impersonate
    if _backend_curl(target):
        try:
            return await _send_curl(
                method,
                url,
                headers=headers,
                json_body=json_body,
                form=form,
                cookies=cookies,
                proxy=proxy,
                follow_redirects=follow_redirects,
                max_redirects=max_redirects,
                timeout=timeout,
                target=target,
            )
        except Exception as exc:  # noqa: BLE001
            # libcurl-impersonate can fail on some SOCKS5 proxies / h2 edge cases;
            # fall back to httpx (socksio) rather than failing the whole flow.
            logger.warning(
                "curl_cffi failed (%s: %s); retrying with httpx%s",
                type(exc).__name__,
                str(exc)[:160],
                " via proxy" if proxy else "",
            )
    return await _send_httpx(
        method,
        url,
        headers=headers,
        json_body=json_body,
        form=form,
        cookies=cookies,
        proxy=proxy,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        timeout=timeout,
        connect_timeout=connect_timeout,
    )


async def _send_curl(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Any = None,
    form: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
    proxy: str | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 8,
    timeout: float = 30.0,
    target: str = "chrome",
) -> HttpResult:
    kwargs: dict[str, Any] = {
        "impersonate": target,
        "timeout": timeout,
        "allow_redirects": follow_redirects,
        "max_redirects": max_redirects,
    }
    if proxy:
        kwargs["proxy"] = proxy
    async with _CurlSession(**kwargs) as session:  # type: ignore[misc]
        req_kwargs: dict[str, Any] = {"headers": headers}
        if json_body is not None:
            req_kwargs["json"] = json_body
        if form is not None:
            req_kwargs["data"] = form
        if cookies:
            req_kwargs["cookies"] = cookies
        r = await session.request(method.upper(), url, **req_kwargs)
        raw_headers = [(str(k), str(v)) for k, v in r.headers.multi_items()]
        return HttpResult(
            status_code=int(r.status_code),
            headers=raw_headers,
            body=r.content or b"",
            url=str(r.url),
        )


async def _send_httpx(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Any = None,
    form: dict[str, Any] | None = None,
    cookies: dict[str, str] | None = None,
    proxy: str | None = None,
    follow_redirects: bool = False,
    max_redirects: int = 8,
    timeout: float = 30.0,
    connect_timeout: float = 20.0,
) -> HttpResult:
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout, connect=connect_timeout),
        "follow_redirects": follow_redirects,
        "max_redirects": max_redirects,
        "trust_env": False,
    }
    if proxy:
        kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**kwargs) as client:
        if cookies:
            client.cookies.update(cookies)
        if form is not None:
            r = await client.request(method, url, headers=headers, data=form)
        else:
            r = await client.request(method, url, headers=headers, json=json_body)
        raw_headers = [(k, v) for k, v in r.headers.multi_items()]
        return HttpResult(
            status_code=r.status_code,
            headers=raw_headers,
            body=r.content,
            url=str(r.url),
        )
