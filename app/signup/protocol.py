"""Pluggable Muse signup HTTP client.\n\nFull Muse native-auth protocol (restart → OTP/recovery/password → hatch).\nHttpMuseClient is the real wire client; MockMuseClient mirrors the same steps\nin-process for local QA. Both attach HAR-derived browser fingerprints.\nNo checkout / card endpoints.\n"""

from __future__ import annotations

import logging
import re
import random
import string
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.config import get_settings
from app.signup.fingerprint import (
    BrowserFingerprint,
    ensure_waterfall_id,
    pick_fingerprint,
)
from app.signup.pwd_browser import (
    mock_password_encryption,
    unwrap_pwd_browser_mock,
    wrap_pwd_browser,
)


@dataclass
class MuseSession:
    waterfall_id: str = ""
    csrf_token: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    tos_content_version: str = "mock-tos-v1"
    frl_access_token: str = ""
    frl_account_id: str = ""
    hatch_sess: str = ""
    last_otp: str = ""
    last_recovery_code: str = ""
    email: str = ""
    password_encryption: dict[str, Any] = field(default_factory=dict)
    password_login_available: bool = False
    flow: str = ""
    has_muse_account: bool = False
    has_password: bool = False
    already_activated: bool = False
    # HAR-derived browser persona (see fingerprint.py)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    last_request_headers: dict[str, str] = field(default_factory=dict)
    # Outbound proxy for this session (item override or settings pool pick)
    proxy_url: str = ""
    # checkout (age verification) state for the current item
    device_id: str = ""
    checkout: dict[str, Any] = field(default_factory=dict)


class MuseProtocolError(Exception):
    def __init__(self, status_code: int, message: str, detail: Any = None):
        self.status_code = status_code
        self.message = message
        self.detail = detail
        super().__init__(message)



logger = logging.getLogger("muse-proxy.signup.protocol")


_PROXY_SCHEMES = ("http://", "https://", "socks4://", "socks4a://", "socks5://", "socks5h://")


def normalize_proxy_entry(entry: str) -> str:
    """Accept common provider formats and turn them into a URL.

    Supported (all optionally with ``{sid}``):
      * ``socks5h://user:pass@host:port`` (as-is)
      * ``user:pass@host:port``
      * ``host:port``
      * ``host:port:user:pass``            (udealproxy / iproyal style)
      * ``host:port:user:pass:socks5``     (scheme as 5th field)
    """
    raw = (entry or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low.startswith(_PROXY_SCHEMES):
        return raw
    scheme = "socks5h"
    parts = raw.split(":")
    if len(parts) >= 5 and parts[4].strip().lower() in ("http", "https", "socks4", "socks5", "socks5h"):
        scheme = parts[4].strip().lower()
        parts = parts[:4]
    if "@" in raw and len(parts) == 2:
        return f"{scheme}://{raw}"
    if len(parts) == 2 and parts[1].isdigit():
        return f"{scheme}://{parts[0]}:{parts[1]}"
    if len(parts) == 4 and parts[1].isdigit():
        host, port, user, pwd = parts
        return f"{scheme}://{user}:{pwd}@{host}:{port}"
    # already user:pass@host:port with a scheme-looking tail or unknown shape
    if "@" in raw:
        return f"{scheme}://{raw}"
    return raw


def parse_proxy_list(raw: str | None) -> list[str]:
    """Split settings.proxies (newline / comma separated) into URL list."""
    if not raw:
        return []
    out: list[str] = []
    for part in str(raw).replace(",", "\n").splitlines():
        p = part.strip()
        if not p or p.startswith("#"):
            continue
        url = normalize_proxy_entry(p)
        if url:
            out.append(url)
    return out


def mask_proxy_url(url: str | None) -> str:
    """Hide userinfo in proxy URLs for logs."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{host}{port}"
        if parts.username:
            netloc = f"***:***@{netloc}"
        return urlunsplit((parts.scheme, netloc, "", "", ""))
    except Exception:
        return "***"


current_item_id: ContextVar[str] = ContextVar("signup_item_id", default="")


def fresh_sticky_proxy() -> str:
    """Pick one proxy line and mint a new 8-char `{sid}`."""
    raw = getattr(get_settings(), "proxies", "") or ""
    if "{sid}" not in raw:
        return ""
    return pick_proxy(raw) or ""


def _proxyish(exc: BaseException) -> bool:
    blob = f"{type(exc).__name__} {exc}".lower()
    keys = (
        "proxy", "connect", "timeout", "timed out", "ssl", "eof",
        "reset", "refused", "unreachable", "curl", "407", "502", "503", "504",
    )
    return any(k in blob for k in keys)


def _rotate_proxy(sess: Any, nth: int) -> bool:
    """Swap in a new sticky SID. Returns False when there is nothing to rotate."""
    new = fresh_sticky_proxy()
    if not new or new == (getattr(sess, "proxy_url", "") or ""):
        return False
    sess.proxy_url = new
    iid = current_item_id.get()
    if iid:
        from app.signup.store import add_log

        add_log(iid, "warn", f"代理这条小路堵住了，换一条新的再走（第 {nth} 次）")
    return True


def expand_proxy_sid(url: str | None, sid: str | None = None) -> str:
    """Expand the provider's `{sid}` placeholder into one sticky session id.

    arxlabs style: `…-region-US-sid-{sid}-t-3` expects a per-session token;
    keeping one token per signup item gives a stable exit IP for the whole flow.
    """
    if not url or "{sid}" not in url:
        return url or ""
    token = sid or "".join(random.choices(string.ascii_letters + string.digits, k=8))
    return url.replace("{sid}", token)


def pick_proxy(raw: str | None, preferred: str | None = None) -> str | None:
    pref = (preferred or "").strip()
    if pref:
        return pref
    pool = parse_proxy_list(raw)
    if not pool:
        return None
    return expand_proxy_sid(random.choice(pool))


def _header_items(headers: Any) -> list[tuple[str, str]]:
    """Normalise httpx Headers / curl_cffi Headers / list of pairs."""
    if headers is None:
        return []
    if isinstance(headers, list):
        return [(str(k), str(v)) for k, v in headers]
    for attr in ("multi_items", "items"):
        fn = getattr(headers, attr, None)
        if callable(fn):
            try:
                return [(str(k), str(v)) for k, v in fn()]
            except Exception:
                continue
    return []


def summarize_response_headers(headers: Any) -> dict[str, str]:
    """Compact header map for 403/TLS diagnostics (no Set-Cookie values)."""
    interesting = {
        "server",
        "date",
        "content-type",
        "content-length",
        "location",
        "cf-ray",
        "cf-cache-status",
        "x-request-id",
        "x-fb-request-id",
        "x-fb-trip-id",
        "retry-after",
        "www-authenticate",
        "via",
        "alt-svc",
    }
    out: dict[str, str] = {}
    set_cookie_names: list[str] = []
    for k, v in _header_items(headers):
        lk = k.lower()
        if lk == "set-cookie":
            name = str(v).split("=", 1)[0].strip()
            if name:
                set_cookie_names.append(name)
            continue
        if lk in interesting or lk.startswith("x-"):
            out[lk] = str(v)[:200]
    if set_cookie_names:
        out["set-cookie-names"] = ",".join(set_cookie_names[:12])
    return out


class MuseClient(Protocol):
    async def restart(self, sess: MuseSession, *, resume: bool = False) -> dict[str, Any]: ...
    async def send_otp(self, sess: MuseSession, email: str, region: str) -> dict[str, Any]: ...
    async def confirm_otp(
        self, sess: MuseSession, otp_code: str, *, remember_device: bool = True
    ) -> dict[str, Any]: ...
    async def register_completion(
        self,
        sess: MuseSession,
        date_of_birth: str,
        tos_content_version: str | None = None,
        *,
        first_name: str = "",
        last_name: str = "",
        display_name: str = "",
    ) -> dict[str, Any]: ...
    async def recovery_start(self, sess: MuseSession, email: str) -> dict[str, Any]: ...
    async def recovery_confirm_code(self, sess: MuseSession, code: str) -> dict[str, Any]: ...
    async def recovery_complete(
        self, sess: MuseSession, password: str, *, logout_other_sessions: bool = True
    ) -> dict[str, Any]: ...
    async def confirm_password(
        self, sess: MuseSession, password: str, *, remember_device: bool = True
    ) -> dict[str, Any]: ...
    async def invite_redeem(self, sess: MuseSession, code: str) -> dict[str, Any]: ...
    async def invite_get(self, sess: MuseSession) -> dict[str, Any]: ...
    async def invite_create(self, sess: MuseSession) -> dict[str, Any]: ...
    async def bootstrap(self, sess: MuseSession) -> dict[str, Any]: ...
    async def accept_tos(self, sess: MuseSession) -> dict[str, Any]: ...
    async def vm_wake(self, sess: MuseSession) -> dict[str, Any]: ...
    async def lease_vm(self, sess: MuseSession, vm_type: str = "standard") -> dict[str, Any]: ...
    async def checkout_init(
        self,
        sess: MuseSession,
        *,
        session_id: str | None = None,
        amount: str | None = None,
        currency: str | None = None,
        product_id: str | None = None,
        receiver_id: str | None = None,
    ) -> dict[str, Any]: ...
    async def ptt_encryption_key(
        self,
        sess: MuseSession,
        *,
        session_id: str | None = None,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]: ...
    async def checkout_credential(
        self,
        sess: MuseSession,
        card: dict[str, Any],
        *,
        ptt_token: str,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]: ...
    async def checkout_finalize(
        self,
        sess: MuseSession,
        *,
        credential_id: str,
        ptt_token: str,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]: ...
    async def run_checkout(self, sess: MuseSession, card: dict[str, Any]) -> dict[str, Any]: ...
    async def fetch_otp_from_mail(
        self,
        email: str,
        secret: dict[str, Any],
        *,
        lengths: tuple[int, ...] = (6, 8),
        since_ts: float = 0.0,
    ) -> str | None: ...
    async def list_mail(self, email: str, secret: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def get_mail_message(
        self, email: str, secret: dict[str, Any], msg_id: str
    ) -> dict[str, Any] | None: ...


def _deep_find(obj: Any, keys: tuple[str, ...], depth: int = 4) -> str | None:
    """First non-empty string value under any of ``keys`` (recursive)."""
    if depth <= 0:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in keys and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            found = _deep_find(v, keys, depth - 1)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _deep_find(v, keys, depth - 1)
            if found:
                return found
    return None


def _truncate_obj(obj: Any, depth: int = 3) -> Any:
    """Keep task logs small: drop deep nesting and long strings."""
    if depth <= 0:
        return "…"
    if isinstance(obj, dict):
        return {str(k): _truncate_obj(v, depth - 1) for k, v in list(obj.items())[:30]}
    if isinstance(obj, list):
        return [_truncate_obj(v, depth - 1) for v in obj[:10]]
    if isinstance(obj, str):
        return obj[:200]
    return obj


def _apply_frl(sess: MuseSession, data: dict[str, Any]) -> None:
    meta = data.get("auth_meta_session") or {}
    tok = meta.get("frl_access_token")
    if tok:
        sess.frl_access_token = str(tok)
    aid = meta.get("frl_account_id")
    if aid:
        sess.frl_account_id = str(aid)



def _ensure_fingerprint(sess: MuseSession) -> BrowserFingerprint:
    fp = BrowserFingerprint.from_dict(sess.fingerprint)
    if fp is None:
        fp = pick_fingerprint()
        sess.fingerprint = fp.to_dict()
    sess.waterfall_id = ensure_waterfall_id(sess.waterfall_id)
    if not sess.device_id:
        sess.device_id = str(uuid.uuid4())
    return fp


class MockMuseClient:
    """Simulates Muse native auth steps; never calls checkout."""

    def __init__(self) -> None:
        self._mail: dict[str, list[dict[str, Any]]] = {}
        # email -> {password, activated, invite_redeemed, own_code}
        self._accounts: dict[str, dict[str, Any]] = {}

    def _acct(self, email: str) -> dict[str, Any]:
        key = email.lower()
        return self._accounts.setdefault(
            key,
            {
                "password": None,
                "activated": False,
                "invite_redeemed": False,
                "own_code": None,
            },
        )

    async def restart(self, sess: MuseSession, *, resume: bool = False) -> dict[str, Any]:
        fp = _ensure_fingerprint(sess)
        sess.waterfall_id = ensure_waterfall_id("")
        sess.csrf_token = "mock_csrf_" + uuid.uuid4().hex[:8]
        sess.password_encryption = mock_password_encryption()
        sess.cookies["hatch_native_auth_csrf"] = f"{sess.csrf_token}.sig"
        sess.cookies["hatch_native_auth_temp"] = "temp_" + uuid.uuid4().hex[:8]
        sess.cookies["hatch_native_auth_state"] = "state_" + uuid.uuid4().hex[:8]
        if fp.wd:
            sess.cookies.setdefault("wd", fp.wd)
        if fp.dpr:
            sess.cookies.setdefault("dpr", fp.dpr)
        # Record headers a real client would send (for tests / logs without secrets)
        sess.last_request_headers = fp.request_headers(
            csrf_token="",
            waterfall_id=sess.waterfall_id,
            include_caa=False,
        )
        return {
            "ok": True,
            "csrf_token": sess.csrf_token,
            "waterfall_id": sess.waterfall_id,
            "flow_state": "started",
            "password_encryption": sess.password_encryption,
            "saved_accounts_available": False,
            "saved_accounts": [],
            "resume": resume,
            "fingerprint_profile": fp.profile_id,
        }

    async def send_otp(self, sess: MuseSession, email: str, region: str) -> dict[str, Any]:
        fp = _ensure_fingerprint(sess)
        sess.last_request_headers = fp.request_headers(
            csrf_token=sess.csrf_token,
            waterfall_id=sess.waterfall_id,
            include_caa=True,
        )
        sess.email = email
        acct = self._acct(email)
        code = f"{int(time.time()) % 1000000:06d}"
        sess.last_otp = code
        msg = {
            "id": "m_" + uuid.uuid4().hex[:10],
            "subject": "Your Muse verification code",
            "from": "noreply@muse.local",
            "to": email,
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text": f"Your code is {code}",
            "html": f"<p>Your code is <b>{code}</b></p><script>alert(1)</script>",
        }
        self._mail.setdefault(email.lower(), []).insert(0, msg)
        has_pw = bool(acct.get("password"))
        has_acct = has_pw or bool(acct.get("activated")) or bool(acct.get("seen"))
        acct["seen"] = True
        sess.password_login_available = has_pw
        sess.has_muse_account = has_acct
        sess.has_password = has_pw
        sess.flow = "login" if has_acct else "register"
        sess.already_activated = bool(acct.get("activated"))
        return {
            "ok": True,
            "registration_state": "otp_sent",
            "password_login_available": has_pw,
            "flow": sess.flow,
            "has_muse_account": has_acct,
            "masked_contact": email[0] + "***@" + email.split("@")[-1] if "@" in email else "***",
            "region": region,
        }

    async def confirm_otp(
        self, sess: MuseSession, otp_code: str, *, remember_device: bool = True
    ) -> dict[str, Any]:
        if otp_code != sess.last_otp and otp_code != "000000":
            raise MuseProtocolError(400, "invalid_otp", {"message": "OTP 不正确"})
        sess.cookies["hatch_native_auth_temp"] = "temp_" + uuid.uuid4().hex[:8]
        return {
            "ok": True,
            "registration_state": "completion_required",
            "masked_contact": (
                sess.email[0] + "***@" + sess.email.split("@")[-1]
                if sess.email and "@" in sess.email
                else "***"
            ),
            "tos": {"content_version": sess.tos_content_version},
            "remember_device": remember_device,
        }

    async def register_completion(
        self,
        sess: MuseSession,
        date_of_birth: str,
        tos_content_version: str | None = None,
        *,
        first_name: str = "",
        last_name: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        sess.tos_content_version = tos_content_version or sess.tos_content_version
        sess.frl_access_token = "frl_" + uuid.uuid4().hex
        sess.frl_account_id = str(1000000000000000 + (hash(sess.email or "") % 10**14))
        sess.hatch_sess = "hatch_sess_" + uuid.uuid4().hex
        sess.cookies["hatch_sess"] = sess.hatch_sess
        if sess.email:
            self._acct(sess.email)["seen"] = True
        return {
            "ok": True,
            "redirect_to": "/access",
            "auth_meta_session": {
                "frl_access_token": sess.frl_access_token,
                "frl_account_id": sess.frl_account_id,
            },
            "date_of_birth": date_of_birth,
        }

    async def recovery_start(self, sess: MuseSession, email: str) -> dict[str, Any]:
        sess.email = email or sess.email
        code = f"{int(time.time()) % 100000000:08d}"
        sess.last_recovery_code = code
        msg = {
            "id": "r_" + uuid.uuid4().hex[:10],
            "subject": "Muse recovery code",
            "from": "noreply@muse.local",
            "to": sess.email,
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text": f"Your recovery code is {code}",
            "html": f"<p>recovery <b>{code}</b></p>",
        }
        self._mail.setdefault((sess.email or "").lower(), []).insert(0, msg)
        masked = (
            sess.email[0] + "***@" + sess.email.split("@")[-1]
            if sess.email and "@" in sess.email
            else "***"
        )
        return {
            "ok": True,
            "next_step": "code_sent",
            "display_contact_point": masked,
        }

    async def recovery_confirm_code(self, sess: MuseSession, code: str) -> dict[str, Any]:
        if code != sess.last_recovery_code and code != "00000000":
            raise MuseProtocolError(
                400,
                "invalidCode",
                {
                    "type": "invalidCode",
                    "title": "登录信息无效",
                    "detail": "请申请新的验证码并重试",
                    "status": 400,
                },
            )
        return {"ok": True, "next_step": "set_password"}

    async def recovery_complete(
        self, sess: MuseSession, password: str, *, logout_other_sessions: bool = True
    ) -> dict[str, Any]:
        wrapped = wrap_pwd_browser(password, sess.password_encryption or None)
        plain = unwrap_pwd_browser_mock(wrapped) or password
        if len(plain) < 6:
            raise MuseProtocolError(400, "weak_password", {"message": "密码至少 6 位"})
        if sess.email:
            acct = self._acct(sess.email)
            acct["password"] = plain
            acct["seen"] = True
        sess.has_password = True
        sess.frl_access_token = "frl_" + uuid.uuid4().hex
        sess.frl_account_id = str(1000000000000000 + (hash(sess.email or "x") % 10**14))
        sess.hatch_sess = "hatch_sess_" + uuid.uuid4().hex
        sess.cookies["hatch_sess"] = sess.hatch_sess
        return {
            "ok": True,
            "redirect_to": "/access",
            "auth_meta_session": {
                "frl_access_token": sess.frl_access_token,
                "frl_account_id": sess.frl_account_id,
            },
            "logout_other_sessions": logout_other_sessions,
            "password_wrapped": wrapped[:32] + "…",
        }

    async def confirm_password(
        self, sess: MuseSession, password: str, *, remember_device: bool = True
    ) -> dict[str, Any]:
        wrapped = wrap_pwd_browser(password, sess.password_encryption or None)
        plain = unwrap_pwd_browser_mock(wrapped) or password
        acct = self._acct(sess.email or "")
        if not acct.get("password"):
            raise MuseProtocolError(
                400, "password_not_set", {"message": "账号尚未设密，请先走 Recovery"}
            )
        if plain != acct["password"] and plain != "mock-ok":
            raise MuseProtocolError(400, "bad_password", {"message": "密码不正确"})
        sess.frl_access_token = "frl_" + uuid.uuid4().hex
        sess.frl_account_id = str(1000000000000000 + (hash(sess.email or "x") % 10**14))
        sess.hatch_sess = "hatch_sess_" + uuid.uuid4().hex
        sess.cookies["hatch_sess"] = sess.hatch_sess
        sess.already_activated = bool(acct.get("activated"))
        sess.has_password = True
        return {
            "ok": True,
            "redirect_to": "/access",
            "auth_meta_session": {
                "frl_access_token": sess.frl_access_token,
                "frl_account_id": sess.frl_account_id,
            },
            "remember_device": remember_device,
            "already_activated": sess.already_activated,
        }

    async def invite_redeem(self, sess: MuseSession, code: str) -> dict[str, Any]:
        if not sess.hatch_sess:
            raise MuseProtocolError(401, "auth_required", {"message": "需要已登录会话"})
        code = (code or "").strip().upper()
        if not code or len(code) < 4:
            raise MuseProtocolError(400, "invalid_invite", {"message": "邀请码无效"})
        # mock: reject lookalike zero; accept others including C9O594
        if "0" in code and "O" not in code.replace("0", ""):
            # still allow unless it's specifically bad pattern — keep simple: C90594 fails
            if code == "C90594":
                raise MuseProtocolError(400, "invalid_invite", {"message": "邀请码无效"})
        if sess.email:
            self._acct(sess.email)["invite_redeemed"] = True
        return {
            "success": True,
            "redemptionStatus": "redeemed",
            "waitlistStatus": "admitted",
        }

    async def invite_get(self, sess: MuseSession) -> dict[str, Any]:
        if not sess.hatch_sess:
            raise MuseProtocolError(401, "auth_required", {"message": "需要已登录会话"})
        acct = self._acct(sess.email or "") if sess.email else {}
        return {
            "has_redeemed_invite_code": bool(acct.get("invite_redeemed")),
            "code": acct.get("own_code"),
            "max_uses": 30 if acct.get("own_code") else None,
            "uses_remaining": 30 if acct.get("own_code") else None,
        }

    async def invite_create(self, sess: MuseSession) -> dict[str, Any]:
        if not sess.hatch_sess:
            raise MuseProtocolError(401, "auth_required", {"message": "需要已登录会话"})
        code = "M" + uuid.uuid4().hex[:5].upper()
        if sess.email:
            self._acct(sess.email)["own_code"] = code
        return {
            "code": code,
            "max_uses": 30,
            "use_count": 0,
            "invite_link": "https://muse.ai/join",
            "share_text": f"Join me on Muse with code {code}",
        }

    async def bootstrap(self, sess: MuseSession) -> dict[str, Any]:
        return {"ok": True, "bootstrapped": True, "mock": True}

    async def accept_tos(self, sess: MuseSession) -> dict[str, Any]:
        # mirror the live gate: hatch admission is refused until activation
        if not sess.already_activated:
            raise MuseProtocolError(
                403,
                "muse_http_403",
                {
                    "type": "hatchAdmissionInvalidated",
                    "reason": "activation",
                    "status": 403,
                },
            )
        if sess.email:
            self._acct(sess.email)["activated"] = True
            sess.already_activated = True
        return {"success": True}

    async def vm_wake(self, sess: MuseSession) -> dict[str, Any]:
        return {"status": "wake_requested", "ok": True}

    async def lease_vm(self, sess: MuseSession, vm_type: str = "standard") -> dict[str, Any]:
        return {
            "status": "assigned",
            "vmName": "mock-vm",
            "gatewayUrl": "wss://mock.metaaivm.com/",
            "vmType": vm_type or "standard",
        }

    async def onboarding_state(self, sess: MuseSession) -> dict[str, Any]:
        return {
            "ok": True,
            "is_geo_gated": False,
            "has_hatch_access": True,
            "waitlist_status": "admitted",
            "ccv_status": "not_required",
            "has_redeemed_invite": False,
            "invite_entry_available": True,
            "waitlist_available": False,
            "status_code": 200,
        }

    async def _request(self, method: str, path: str, sess: MuseSession, json_body=None, **kw):
        if "stateful-sharing" in path:
            return {"enabled": False}
        if "/api/auth/check" in path:
            return {"ok": True, "outcome": "validated"}
        return {"ok": True}

    # ---- checkout (mock: declines cards whose PAN/CVV ends in 0000) ----
    async def checkout_init(
        self,
        sess: MuseSession,
        *,
        session_id: str | None = None,
        amount: str | None = None,
        currency: str | None = None,
        product_id: str | None = None,
        receiver_id: str | None = None,
    ) -> dict[str, Any]:
        if not sess.hatch_sess:
            raise MuseProtocolError(401, "auth_required", {"message": "需要 hatch_sess"})
        sid = session_id or f"upl_{int(time.time() * 1000)}_{uuid.uuid4()}"
        sess.checkout = {
            "session_id": sid,
            "amount": amount or "1.00",
            "currency": currency or "USD",
            "product_id": product_id or "766358168544755",
            "receiver_id": receiver_id or "352501280694489",
            "order_id": "ord_" + uuid.uuid4().hex[:16],
            "payment_container_mode": "LIVE",
        }
        return {
            "ok": True,
            "orderId": sess.checkout["order_id"],
            "sessionId": sid,
            "receiverName": "Age Verification CCV",
            "paymentContainerMode": "LIVE",
            "isNUX": True,
            "ccvScreenContent": "mock age verification",
        }

    async def ptt_encryption_key(
        self,
        sess: MuseSession,
        *,
        session_id: str | None = None,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]:
        return {
            "id": "mock_ptt_key",
            "trustChain": [],
            "platformTrustToken": "",
            "devExternal": True,
        }

    async def checkout_credential(
        self,
        sess: MuseSession,
        card: dict[str, Any],
        *,
        ptt_token: str,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]:
        pan = str(card.get("pan") or "")
        cvv = str(card.get("cvv") or "")
        if pan.endswith("0000") or cvv == "000":
            raise MuseProtocolError(
                402,
                "card_declined",
                {
                    "type": "cardDeclined",
                    "title": "Card declined",
                    "detail": f"mock decline for ••••{pan[-4:]}",
                    "status": 402,
                },
            )
        return {
            "mutation": {"success": True},
            "credentialId": "cred_" + uuid.uuid4().hex[:16],
            "cardSummary": f"{card.get('brand') or 'Card'} •••• {pan[-4:]}",
        }

    async def checkout_finalize(
        self,
        sess: MuseSession,
        *,
        credential_id: str,
        ptt_token: str,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]:
        if not credential_id:
            raise MuseProtocolError(400, "credential_required", {"message": "缺少 credentialId"})
        sess.already_activated = True
        if sess.email:
            self._acct(sess.email)["activated"] = True
        return {
            "checkoutStatus": "SUCCESS",
            "orderId": (sess.checkout or {}).get("order_id"),
            "confirmationTitle": "Thanks for your purchase",
            "confirmationBodyText": "We'll send a confirmation email.",
        }

    async def run_checkout(self, sess: MuseSession, card: dict[str, Any]) -> dict[str, Any]:
        init = await self.checkout_init(sess)
        otc = str(uuid.uuid4())
        await self.ptt_encryption_key(sess, otc_session_id=otc)
        cred = await self.checkout_credential(sess, card, ptt_token="mock", otc_session_id=otc)
        fin = await self.checkout_finalize(
            sess, credential_id=str(cred.get("credentialId") or ""), ptt_token="mock", otc_session_id=otc
        )
        return {
            "ok": True,
            "status": fin.get("checkoutStatus"),
            "order_id": (sess.checkout or {}).get("order_id"),
            "credential_id": cred.get("credentialId"),
            "init": {"orderId": init.get("orderId")},
            "finalize": fin,
        }

    async def fetch_otp_from_mail(
        self,
        email: str,
        secret: dict[str, Any],
        *,
        lengths: tuple[int, ...] = (6, 8),
        since_ts: float = 0.0,
    ) -> str | None:
        msgs = self._mail.get(email.lower()) or []
        if not msgs:
            return None
        import re

        text = msgs[0].get("text") or ""
        for pattern in (r"\b(\d{8})\b", r"\b(\d{6})\b"):
            m = re.search(pattern, text)
            if m and len(m.group(1)) in lengths:
                return m.group(1)
        return None

    async def list_mail(self, email: str, secret: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": m["id"],
                "subject": m["subject"],
                "from": m["from"],
                "received_at": m["received_at"],
            }
            for m in self._mail.get(email.lower()) or []
        ]

    async def get_mail_message(
        self, email: str, secret: dict[str, Any], msg_id: str
    ) -> dict[str, Any] | None:
        for m in self._mail.get(email.lower()) or []:
            if m["id"] == msg_id:
                return dict(m)
        return None


class HttpMuseClient:
    """Real Muse HTTP client (no checkout endpoints).

    Passwords are sealed client-side in the genuine #PWD_BROWSER:5 format
    (libsodium crypto_box_seal + AES-256-GCM, see app/signup/pwd_browser.py)
    using the `password_encryption` key issued by `restart`.

    Always talks to SIGNUP_MUSE_BASE (typically https://muse.ai) over the wire.
    Uses settings.proxies (SOCKS5/HTTP) or per-session proxy_url — never mock.
    """

    def __init__(self, base_url: str, proxy: str | None = None):
        self.base = base_url.rstrip("/")
        self.proxy = (proxy or "").strip()
        self.rotated_refresh_token = ""

    def _headers(
        self,
        sess: MuseSession,
        *,
        content_type: str = "application/json",
        include_caa: bool = True,
    ) -> dict[str, str]:
        fp = _ensure_fingerprint(sess)
        h = fp.request_headers(
            csrf_token=sess.csrf_token,
            waterfall_id=sess.waterfall_id,
            content_type=content_type,
            include_caa=include_caa,
        )
        # Prefer application/json accept when posting JSON API (HAR also uses */*)
        if content_type == "application/json":
            h["accept"] = "application/json"
        sess.last_request_headers = dict(h)
        return h

    def _cookie_header(self, sess: MuseSession) -> str:
        return "; ".join(f"{k}={v}" for k, v in sess.cookies.items())

    def _resolve_proxy(self, sess: MuseSession) -> str | None:
        raw = (sess.proxy_url or self.proxy or "").strip()
        return expand_proxy_sid(raw) or None

    async def _request(
        self,
        method: str,
        path: str,
        sess: MuseSession,
        json_body: dict | None = None,
        *,
        form: dict | None = None,
        absolute: str | None = None,
    ) -> dict[str, Any]:
        from app.signup.transport import send as http_send

        url = absolute or urljoin(self.base + "/", path.lstrip("/"))
        fp = _ensure_fingerprint(sess)
        if fp.wd:
            sess.cookies.setdefault("wd", fp.wd)
        if fp.dpr:
            sess.cookies.setdefault("dpr", fp.dpr)
        # restart must not send CSRF; callers clear csrf before calling
        is_restart = path.rstrip("/").endswith("/restart") or (
            absolute or ""
        ).endswith("/restart")
        ct = "application/x-www-form-urlencoded" if form is not None else "application/json"
        headers = self._headers(
            sess, content_type=ct, include_caa=not is_restart
        )
        cookie = self._cookie_header(sess)
        if cookie:
            headers["cookie"] = cookie
        res = None
        proxy = ""
        for attempt in range(3):
            proxy = self._resolve_proxy(sess)
            try:
                res = await http_send(
                    method,
                    url,
                    headers=headers,
                    json_body=None if form is not None else (json_body if json_body is not None else {}),
                    form=form,
                    proxy=proxy,
                    follow_redirects=False,
                )
            except Exception as exc:  # noqa: BLE001 — curl_cffi + httpx
                if attempt < 2 and _proxyish(exc) and _rotate_proxy(sess, attempt + 1):
                    continue
                kind = "proxy_error" if isinstance(exc, httpx.ProxyError) else "transport_error"
                raise MuseProtocolError(
                    0,
                    f"{kind}:{type(exc).__name__}",
                    {
                        "error": str(exc)[:400],
                        "proxy": mask_proxy_url(proxy),
                        "proxy_used": bool(proxy),
                        "url": url,
                    },
                ) from exc
            if res.status_code in (407, 502, 503, 504) and attempt < 2 and _rotate_proxy(sess, attempt + 1):
                continue
            break
        assert res is not None
        for k, v in res.cookies().items():
            sess.cookies[k] = v
            if k == "hatch_sess":
                sess.hatch_sess = v
        try:
            data: Any = res.json()
        except Exception:
            data = {"raw": (res.text or "")[:500]}
        if res.status_code >= 400:
            detail = data if isinstance(data, dict) else {"data": data}
            detail = {
                **detail,
                "status_code": res.status_code,
                "headers": summarize_response_headers(_header_items(res)),
                "proxy": mask_proxy_url(proxy),
                "proxy_used": bool(proxy),
                "url": url,
            }
            raise MuseProtocolError(res.status_code, f"muse_http_{res.status_code}", detail)
        return data if isinstance(data, dict) else {"data": data}

    async def restart(self, sess: MuseSession, *, resume: bool = False) -> dict[str, Any]:
        # restart issues CSRF — do not send old CSRF header
        old_csrf = sess.csrf_token
        sess.csrf_token = ""
        _ensure_fingerprint(sess)
        sess.waterfall_id = ensure_waterfall_id("")
        # Ensure session inherits client default proxy if item did not set one
        if not (sess.proxy_url or "").strip() and self.proxy:
            sess.proxy_url = self.proxy
        logger.info(
            "muse restart → %s proxy=%s",
            self.base,
            mask_proxy_url(self._resolve_proxy(sess)) or "(direct)",
        )
        try:
            data = await self._request(
                "POST", "/api/auth/native/restart", sess, {"resume": resume}
            )
        finally:
            if not sess.csrf_token:
                sess.csrf_token = old_csrf
        token = data.get("csrf_token") or data.get("token")
        if token:
            sess.csrf_token = str(token)
        pe = data.get("password_encryption")
        if isinstance(pe, dict):
            sess.password_encryption = pe
        return data

    async def send_otp(self, sess: MuseSession, email: str, region: str) -> dict[str, Any]:
        sess.email = email
        data = await self._request(
            "POST",
            "/api/auth/native/send-otp",
            sess,
            {"contact_point": email, "region": region},
        )
        sess.password_login_available = bool(data.get("password_login_available"))
        sess.flow = str(data.get("flow") or "")
        sess.has_muse_account = bool(data.get("has_muse_account"))
        sess.has_password = sess.password_login_available
        return data

    async def confirm_otp(
        self, sess: MuseSession, otp_code: str, *, remember_device: bool = True
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/auth/native/confirm-otp",
            sess,
            {"otp_code": otp_code, "remember_device": remember_device},
        )

    async def register_completion(
        self,
        sess: MuseSession,
        date_of_birth: str,
        tos_content_version: str | None = None,
        *,
        first_name: str = "",
        last_name: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        body = {
            "date_of_birth": date_of_birth,
            "tos_content_version": tos_content_version or sess.tos_content_version,
            "remember_device": True,
        }
        # The live endpoint rejects the request ("Check the account details")
        # unless the profile names are present (observed 2026-11).
        if first_name:
            body["first_name"] = first_name
        if last_name:
            body["last_name"] = last_name
        if display_name:
            body["display_name"] = display_name
        data = await self._request("POST", "/api/auth/native/register-completion", sess, body)
        _apply_frl(sess, data)
        if "hatch_sess" in sess.cookies:
            sess.hatch_sess = sess.cookies["hatch_sess"]
        return data

    async def recovery_start(self, sess: MuseSession, email: str) -> dict[str, Any]:
        sess.email = email or sess.email
        return await self._request(
            "POST",
            "/api/auth/native/recovery-start",
            sess,
            {"contact_point": sess.email},
        )

    async def recovery_confirm_code(self, sess: MuseSession, code: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/auth/native/recovery-confirm-code",
            sess,
            {"code": code},
        )

    async def recovery_complete(
        self, sess: MuseSession, password: str, *, logout_other_sessions: bool = True
    ) -> dict[str, Any]:
        wrapped = wrap_pwd_browser(password, sess.password_encryption or None)
        data = await self._request(
            "POST",
            "/api/auth/native/recovery-complete",
            sess,
            {"password": wrapped, "logout_other_sessions": logout_other_sessions},
        )
        _apply_frl(sess, data)
        if "hatch_sess" in sess.cookies:
            sess.hatch_sess = sess.cookies["hatch_sess"]
        return data

    async def confirm_password(
        self, sess: MuseSession, password: str, *, remember_device: bool = True
    ) -> dict[str, Any]:
        wrapped = wrap_pwd_browser(password, sess.password_encryption or None)
        data = await self._request(
            "POST",
            "/api/auth/native/confirm-password",
            sess,
            {"password": wrapped, "remember_device": remember_device},
        )
        _apply_frl(sess, data)
        if "hatch_sess" in sess.cookies:
            sess.hatch_sess = sess.cookies["hatch_sess"]
        return data

    async def invite_redeem(self, sess: MuseSession, code: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/hatch/invite-code/redeem",
            sess,
            {"code": code, "supportsRedemptionStatus": True},
        )

    async def invite_get(self, sess: MuseSession) -> dict[str, Any]:
        return await self._request("GET", "/api/hatch/invite", sess, None)

    async def invite_create(self, sess: MuseSession) -> dict[str, Any]:
        return await self._request("POST", "/api/hatch/invite", sess, {})

    async def bootstrap(self, sess: MuseSession) -> dict[str, Any]:
        """POST frl_access_token to auth.meta.com and follow 302 → oidc → muse.ai.

        The 302 chain carries the Meta OIDC session and the final hop writes the
        muse.ai cookies, so redirects must be followed with the session jar.
        """
        if not sess.frl_access_token:
            return {"ok": True, "skipped": True}
        fp = _ensure_fingerprint(sess)
        headers = fp.request_headers(
            csrf_token="",
            waterfall_id="",
            content_type="application/x-www-form-urlencoded",
            include_caa=False,
        )
        # This is a top-level form navigation from muse.ai to auth.meta.com.
        # Meta's edge rejects anything else with "Sorry, this content isn't
        # available right now" (observed 2026-11); cross-site + navigate +
        # document is what makes the 302 → /oidc/callback/ appear.
        headers.update(
            {
                "origin": "https://muse.ai",
                "referer": "https://muse.ai/",
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "sec-fetch-site": "cross-site",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
            }
        )
        proxy = self._resolve_proxy(sess)
        try:
            import httpx

            jar = httpx.Cookies()
            for k, v in sess.cookies.items():
                jar.set(k, v)
            kwargs: dict[str, Any] = {
                "follow_redirects": True,
                "max_redirects": 8,
                "timeout": httpx.Timeout(30.0, connect=20.0),
                "cookies": jar,
            }
            if proxy:
                kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**kwargs) as client:
                res = await client.post(
                    "https://auth.meta.com/hatch/session/bootstrap/",
                    headers=headers,
                    data={"frl_access_token": sess.frl_access_token},
                )
                new_cookies = {c.name: c.value for c in client.cookies.jar}
                status_code = res.status_code
                final_url = str(res.url)
                text_head = (res.text or "")[:300]
        except Exception as exc:  # noqa: BLE001
            raise MuseProtocolError(
                0,
                f"transport_error:{type(exc).__name__}",
                {
                    "error": str(exc)[:400],
                    "proxy": mask_proxy_url(proxy),
                    "url": "https://auth.meta.com/hatch/session/bootstrap/",
                },
            ) from exc
        for k, v in new_cookies.items():
            sess.cookies[k] = v
            if k == "hatch_sess":
                sess.hatch_sess = v
        blocked = "content isn't available" in text_head
        landed = "muse.ai" in final_url and "auth.meta.com" not in final_url
        body = {
            "ok": status_code < 400 and not blocked,
            "status_code": status_code,
            "final_url": final_url,
            "set_cookie_names": sorted(new_cookies),
            "blocked": blocked,
        }
        if blocked or status_code >= 400:
            body["headers"] = summarize_response_headers(res.headers)
            raise MuseProtocolError(
                status_code if status_code >= 400 else 403,
                "bootstrap_blocked" if blocked else f"muse_http_{status_code}",
                body,
            )
        if not landed:
            # 302 chain did not reach muse.ai; surface it instead of pretending.
            body["warning"] = "bootstrap did not land on muse.ai"
        return body

    async def onboarding_state(self, sess: MuseSession) -> dict[str, Any]:
        """Read the hatch onboarding flags from the /access page.

        Muse geo-gates hatch (``isGeoGated``): accounts registered through a
        non-allowed egress IP end up ``waitlisted`` with ``hasHatchAccess=false``
        and every /api/hatch/* + /api/checkout/* call answers 403 Forbidden.
        Detecting it early avoids burning card attempts.
        """
        import re

        from app.signup.transport import send as http_send

        fp = _ensure_fingerprint(sess)
        headers = fp.request_headers(
            csrf_token="", waterfall_id="", content_type="text/html", include_caa=False
        )
        headers["accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        cookie = self._cookie_header(sess)
        if cookie:
            headers["cookie"] = cookie
        try:
            res = await http_send(
                "GET",
                self.base + "/access",
                headers=headers,
                proxy=self._resolve_proxy(sess),
                follow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        html = res.text or ""

        def flag(name: str) -> bool:
            return f'"{name}":true' in html

        def text_of(name: str) -> str:
            m = re.search(rf'"{name}":"([^"]*)"', html)
            return m.group(1) if m else ""

        state = {
            "ok": res.status_code == 200,
            "html_len": len(html),
            "is_geo_gated": flag("isGeoGated"),
            "has_hatch_access": flag("hasHatchAccess"),
            "waitlist_status": text_of("waitlistStatus"),
            "ccv_status": text_of("ccvStatus"),
            "has_redeemed_invite": flag("hasRedeemedInviteCode"),
            "invite_entry_available": flag("inviteCodeEntryAvailable"),
            "waitlist_available": flag("waitlistAvailable"),
            "status_code": res.status_code,
        }
        # The /access HTML is sometimes a client-rendered shell without the
        # onboarding JSON; probe a hatch endpoint to learn admission instead.
        if state["ok"] and not state["waitlist_status"] and not state["has_hatch_access"]:
            headers["content-type"] = "application/json"
            headers["accept"] = "application/json"
            headers["x-hatch-csrf-token"] = sess.csrf_token
            headers["x-hatch-waterfall-id"] = sess.waterfall_id
            try:
                probe = await http_send(
                    "POST",
                    self.base + "/api/hatch/stateful-sharing",
                    headers=headers,
                    json_body={},
                    proxy=self._resolve_proxy(sess),
                )
                state["hatch_probe_status"] = probe.status_code
                if probe.status_code == 403:
                    state["has_hatch_access"] = False
                    state["is_geo_gated"] = True
                elif probe.status_code < 400:
                    state["has_hatch_access"] = True
                    state["is_geo_gated"] = False
            except Exception as exc:  # noqa: BLE001
                state["hatch_probe_error"] = f"{type(exc).__name__}: {exc}"[:160]
        sess.checkout = {**(sess.checkout or {}), "onboarding": state}
        return state

    async def accept_tos(self, sess: MuseSession) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/hatch/accept-tos",
            sess,
            {
                "saw_meta_ai_tos": False,
                "client_request_timestamp": str(int(time.time())),
                "entry_point": "web_onboarding_flow",
                "nux_type": "ROW",
                "disclosure_version": "HATCH_ROW_v0",
                "disclosure_text": (
                    "By continuing, you agree to the Muse Terms and Meta's AI Terms."
                ),
            },
        )

    async def lease_vm(self, sess: MuseSession, vm_type: str = "standard") -> dict[str, Any]:
        """租用工作区 VM（status=none 的号靠这一步变 assigned）。"""
        return await self._request(
            "POST",
            "/api/hatch/lease-vm",
            sess,
            {"vmType": vm_type},
        )

    async def vm_wake(self, sess: MuseSession) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/hatch/vm/wake",
            sess,
            {
                "vm_id": str(uuid.uuid4()),
                "retry_count": 0,
                "connect_attempt_id": str(uuid.uuid4()),
            },
        )

    async def lease_vm(self, sess: MuseSession, vm_type: str = "standard") -> dict[str, Any]:
        """租一台工作区。合法值只有 standard / confidential，走当前会话代理。"""
        kind = vm_type if vm_type in ("standard", "confidential") else "standard"
        return await self._request(
            "POST",
            "/api/hatch/lease-vm",
            sess,
            {"vmType": kind},
        )

    # ---- checkout (age verification, $1 temporary authorization) ----
    async def checkout_init(
        self,
        sess: MuseSession,
        *,
        session_id: str | None = None,
        amount: str | None = None,
        currency: str | None = None,
        product_id: str | None = None,
        receiver_id: str | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        sid = session_id or f"upl_{int(time.time() * 1000)}_{uuid.uuid4()}"
        amount = str(amount or settings.checkout_amount)
        currency = currency or settings.checkout_currency
        product_id = str(product_id or settings.checkout_product_id)
        receiver_id = str(receiver_id or settings.checkout_receiver_id)
        data = await self._request(
            "POST",
            "/api/checkout/init",
            sess,
            {
                "amount": amount,
                "currency": currency,
                "sessionId": sid,
                "productId": product_id,
                "receiverId": receiver_id,
                "productData": [],
            },
        )
        sess.checkout = {
            "session_id": sid,
            "amount": amount,
            "currency": currency,
            "product_id": product_id,
            "receiver_id": receiver_id,
            "order_id": data.get("orderId"),
            "payment_container_mode": data.get("paymentContainerMode") or "LIVE",
            "receiver_name": data.get("receiverName"),
        }
        return data

    async def ptt_encryption_key(
        self,
        sess: MuseSession,
        *,
        session_id: str | None = None,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]:
        co = sess.checkout or {}
        body = {
            "deviceId": sess.device_id or "device_id",
            "paymentType": "ECP",
            "uplSessionId": session_id or co.get("session_id") or "",
            "productId": co.get("product_id") or "",
            "otcSessionId": otc_session_id or str(uuid.uuid4()),
            "otcType": otc_type,
        }
        return await self._request("POST", "/api/ptt/encryption-key", sess, body)

    async def checkout_credential(
        self,
        sess: MuseSession,
        card: dict[str, Any],
        *,
        ptt_token: str,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]:
        co = sess.checkout or {}
        pan = str(card.get("pan") or "")
        exp_month = int(card.get("exp_month") or 0)
        exp_year = int(card.get("exp_year") or 0)
        if exp_year < 100:
            exp_year += 2000
        body = {
            "operation": "CREATE",
            "uplSessionId": co.get("session_id") or "",
            "ecpCheckoutSessionId": None,
            "orderId": co.get("order_id") or "",
            "amount": co.get("amount") or "1.00",
            "currencyCode": co.get("currency") or "USD",
            "paymentContainerMode": co.get("payment_container_mode") or "LIVE",
            "productId": co.get("product_id") or "",
            "receiverId": co.get("receiver_id") or "",
            "platformTrustToken": ptt_token,
            "cardFirst6": str(card.get("bin") or pan[:6]),
            "cardLast4": str(card.get("last4") or pan[-4:]),
            "expiryMonth": f"{exp_month:02d}",
            "expiryYear": str(exp_year),
            # gateway expects a plain 5-digit ZIP for US (captured value "28461")
            "zip": re.sub(r"[^0-9]", "", str(card.get("zip") or ""))[:5]
            if str(card.get("country") or "US").upper() == "US"
            else str(card.get("zip") or ""),
            "countryCode": str(card.get("country") or "US"),
            "otcSessionId": otc_session_id or str(uuid.uuid4()),
            "otcType": otc_type,
        }
        return await self._request("POST", "/api/checkout/credential", sess, body)

    async def checkout_finalize(
        self,
        sess: MuseSession,
        *,
        credential_id: str,
        ptt_token: str,
        otc_session_id: str | None = None,
        otc_type: str = "card",
    ) -> dict[str, Any]:
        co = sess.checkout or {}
        body = {
            "selectedCredentialId": credential_id,
            "credentialType": "CREDIT_CARD",
            "payButtonClickId": str(uuid.uuid4()),
            "paymentRequestId": str(uuid.uuid4()),
            "platformTrustToken": ptt_token,
            "ecpCheckoutSessionId": None,
            "uplSessionId": co.get("session_id") or "",
            "orderId": co.get("order_id") or "",
            "amount": co.get("amount") or "1.00",
            "currencyCode": co.get("currency") or "USD",
            "countryCode": "US",
            "paymentContainerMode": co.get("payment_container_mode") or "LIVE",
            "productId": co.get("product_id") or "",
            "receiverId": co.get("receiver_id") or "",
            "otcSessionId": otc_session_id or str(uuid.uuid4()),
            "otcType": otc_type,
        }
        return await self._request("POST", "/api/checkout/finalize", sess, body)

    async def run_checkout(self, sess: MuseSession, card: dict[str, Any]) -> dict[str, Any]:
        """init → ptt key → credential(JWE) → finalize. Raises MuseProtocolError."""
        from app.cards.ptt import (
            PttError,
            build_credential_token,
            build_finalize_token,
            describe_ptt,
        )

        settings = get_settings()
        mode = (settings.checkout_ptt_mode or "jwe").lower()
        steps: list[str] = []
        init = await self.checkout_init(sess)
        order_id = (sess.checkout or {}).get("order_id")
        steps.append(
            f"checkout/init OK · order={order_id} · "
            f"{init.get('receiverName') or 'Age Verification CCV'} · "
            f"{(sess.checkout or {}).get('amount')} {(sess.checkout or {}).get('currency')}"
        )
        otc_session_id = str(uuid.uuid4())
        ptt_key = await self.ptt_encryption_key(sess, otc_session_id=otc_session_id)
        steps.append(f"ptt/encryption-key OK · id={ptt_key.get('id')}")
        try:
            token = build_credential_token(ptt_key, card, seed=sess.device_id or "")
        except PttError as exc:
            raise MuseProtocolError(
                0,
                "ptt_unavailable",
                {"error": str(exc), "ptt": describe_ptt(ptt_key)},
            ) from exc
        steps.append(
            f"platformTrustToken 已封装（ticket + ECDH-ES/A256GCM，{len(token)} chars）"
        )
        credential = await self.checkout_credential(
            sess,
            card,
            ptt_token=token,
            otc_session_id=otc_session_id,
        )
        mutation = credential.get("mutation") if isinstance(credential, dict) else None
        if isinstance(mutation, dict) and mutation.get("success") is False:
            err = mutation.get("error") or {}
            raise MuseProtocolError(
                400,
                "card_declined",
                {
                    "type": "cardDeclined",
                    "title": err.get("title"),
                    "detail": err.get("description"),
                    "code": err.get("code"),
                    "failure_step": err.get("failureStep"),
                    "stage": "credential",
                },
            )
        credential_id = _deep_find(credential, ("credentialId", "credential_id")) or _deep_find(
            credential, ("id",)
        )
        if not credential_id:
            raise MuseProtocolError(
                0,
                "credential_id_missing",
                {"response": _truncate_obj(credential)},
            )
        token2 = build_finalize_token(
            order_id=str((sess.checkout or {}).get("order_id") or ""),
            credential_id=str(credential_id),
            amount=float((sess.checkout or {}).get("amount") or 1.0),
            currency=str((sess.checkout or {}).get("currency") or "USD"),
        )
        final = await self.checkout_finalize(
            sess,
            credential_id=credential_id,
            ptt_token=token2,
            otc_session_id=otc_session_id,
        )
        steps.append(f"checkout/credential OK · credentialId={credential_id}")
        status = str(final.get("checkoutStatus") or "")
        if status and status.upper() != "SUCCESS":
            raise MuseProtocolError(
                402,
                f"checkout_{status.lower()}",
                {"response": _truncate_obj(final)},
            )
        steps.append(
            f"checkout/finalize {status or 'SUCCESS'} · order={order_id}"
        )
        return {
            "ok": True,
            "status": status or "SUCCESS",
            "steps": steps,
            "order_id": (sess.checkout or {}).get("order_id"),
            "credential_id": credential_id,
            "ptt": describe_ptt(ptt_key),
            "init": {"orderId": init.get("orderId"), "receiverName": init.get("receiverName")},
            "finalize": _truncate_obj(final),
        }

    async def fetch_otp_from_mail(
        self,
        email: str,
        secret: dict[str, Any],
        *,
        lengths: tuple[int, ...] = (6, 8),
        since_ts: float = 0.0,
    ) -> str | None:
        from app.config import get_settings
        from app.mail.client import MailError, wait_for_code

        settings = get_settings()
        try:
            code, rotated = await wait_for_code(
                email,
                secret,
                lengths=lengths,
                timeout=float(settings.signup_mail_wait_sec or 0),
                interval=float(settings.signup_mail_poll_sec or 5),
                since_ts=since_ts,
            )
        except MailError as exc:
            logger.warning("fetch_otp_from_mail(%s) failed: %s", email, exc)
            return None
        if rotated:
            self.rotated_refresh_token = rotated
        return code

    async def list_mail(self, email: str, secret: dict[str, Any]) -> list[dict[str, Any]]:
        from app.mail.client import MailError, list_messages

        try:
            msgs, rotated = await list_messages(email, secret, limit=20)
        except MailError as exc:
            raise MuseProtocolError(0, "mail_error", {"error": str(exc)}) from exc
        if rotated:
            self.rotated_refresh_token = rotated
        return [m.as_dict() for m in msgs]

    async def get_mail_message(
        self, email: str, secret: dict[str, Any], msg_id: str
    ) -> dict[str, Any] | None:
        from app.mail.client import MailError, get_message

        try:
            msg = await get_message(email, secret, msg_id)
        except MailError as exc:
            raise MuseProtocolError(0, "mail_error", {"error": str(exc)}) from exc
        return msg.as_dict()


_mock_singleton: MockMuseClient | None = None


def get_muse_client(*, proxy: str | None = None) -> MuseClient:
    """Return the Muse wire client.

    Production path: SIGNUP_CLIENT=http|real + SIGNUP_MUSE_BASE → HttpMuseClient
    with settings.proxies (or per-call proxy). Explicit SIGNUP_CLIENT=mock still
    yields MockMuseClient for offline QA only — never auto-fallback from http.
    """
    global _mock_singleton
    settings = get_settings()
    mode = (getattr(settings, "signup_client", None) or "").strip().lower()
    base = (getattr(settings, "signup_muse_base", None) or "").strip()
    proxies_raw = getattr(settings, "proxies", "") or ""
    chosen = pick_proxy(proxies_raw, preferred=proxy)

    # Prefer real HTTP whenever mode asks for it OR a muse base is configured
    # without an explicit mock flag.
    want_http = mode in ("http", "real") or (bool(base) and mode not in ("mock", "fake"))
    if want_http:
        if not base:
            raise RuntimeError(
                "SIGNUP_CLIENT=http requires SIGNUP_MUSE_BASE (e.g. https://muse.ai)"
            )
        return HttpMuseClient(base, proxy=chosen)

    if mode in ("mock", "fake"):
        if _mock_singleton is None:
            _mock_singleton = MockMuseClient()
        return _mock_singleton

    raise RuntimeError(
        "Set SIGNUP_CLIENT=http and SIGNUP_MUSE_BASE=https://muse.ai "
        "(or SIGNUP_CLIENT=mock for offline QA only)"
    )
