"""Outlook IMAP / Microsoft Graph mail reader for OTP + recovery codes.

Material fields (already stored encrypted in ``signup_items.secret_enc``)::

    email, password, client_id, refresh_token, aux_email, aux_password

Auth order per call:
  1. IMAP with XOAUTH2 (access token refreshed from ``refresh_token``)
  2. IMAP with the plain password (app password / basic auth)
  3. Graph REST with the access token

Microsoft rotates refresh tokens, so a successful refresh returns the new token
and the caller persists it (``signup_items.secret_enc``).
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from email import policy
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

import httpx

logger = logging.getLogger("muse-proxy.mail")

_MS_DOMAINS = (
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "outlook.jp",
    "outlook.de",
    "outlook.fr",
    "windowslive.com",
)
_FOLDERS = ("INBOX", "Junk", "Spam", "Junk Email")

_CODE_RE = re.compile(r"(?<!\d)(\d{6,8})(?!\d)")
_CTX = re.compile(r"(code|otp|verif|验证码|verification|passcode|security)", re.I)


class MailError(Exception):
    """Mail access failed (bad credentials / unreachable / no token)."""


@dataclass
class MailMessage:
    id: str
    subject: str = ""
    sender: str = ""
    to: str = ""
    received_at: float = 0.0
    text: str = ""
    html: str = ""
    unread: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "from": self.sender,
            "to": self.to,
            "received_at": _iso(self.received_at),
            "text": self.text,
            "html": self.html,
            "unread": self.unread,
        }


def _iso(ts: float) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _domain(email_addr: str) -> str:
    return (email_addr.split("@")[-1] if "@" in email_addr else "").lower()


def imap_host_for(email_addr: str, secret: dict[str, Any] | None = None) -> str:
    secret = secret or {}
    host = str(secret.get("imap_host") or "").strip()
    if host:
        return host
    if _domain(email_addr) in _MS_DOMAINS:
        return "outlook.office365.com"
    return f"imap.{_domain(email_addr)}" if _domain(email_addr) else ""


# --------------------------------------------------------------------------- #
# token refresh
# --------------------------------------------------------------------------- #
_TOKEN_ATTEMPTS: list[tuple[str, str, str]] = [
    ("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "https://outlook.office.com/IMAP.AccessAsUser.All offline_access", ""),
    ("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "https://graph.microsoft.com/Mail.Read offline_access", ""),
    ("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "User.Read Mail.Read offline_access", ""),
    ("https://login.live.com/oauth20_token.srf", "https://outlook.office.com/IMAP.AccessAsUser.All offline_access", "https://login.live.com/oauth20_desktop.srf"),
    ("https://login.live.com/oauth20_token.srf", "https://graph.microsoft.com/Mail.Read offline_access", "https://login.live.com/oauth20_desktop.srf"),
]


async def refresh_access_token(secret: dict[str, Any]) -> tuple[str, str]:
    """Return ``(access_token, rotated_refresh_token)``; raises MailError."""
    client_id = str(secret.get("client_id") or "").strip()
    refresh = str(secret.get("refresh_token") or "").strip()
    if not client_id or not refresh:
        raise MailError("缺少 client_id / refresh_token")
    last_err = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        for endpoint, scope, redirect in _TOKEN_ATTEMPTS:
            form = {
                "client_id": client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "scope": scope,
            }
            if redirect:
                form["redirect_uri"] = redirect
            try:
                r = await client.post(endpoint, data=form)
            except httpx.HTTPError as exc:
                last_err = f"network: {exc}"
                continue
            try:
                data = r.json()
            except Exception:
                last_err = f"http {r.status_code}"
                continue
            token = str(data.get("access_token") or "")
            if r.status_code < 400 and token:
                return token, str(data.get("refresh_token") or refresh)
            last_err = str(data.get("error_description") or data.get("error") or r.status_code)[:200]
    raise MailError(f"refresh_token 刷新失败: {last_err}")


# --------------------------------------------------------------------------- #
# IMAP (blocking, run in a worker thread)
# --------------------------------------------------------------------------- #
def _decode_header(raw: Any) -> str:
    if raw is None:
        return ""
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(str(raw))))
    except Exception:
        return str(raw)


def _imap_connect(email_addr: str, secret: dict[str, Any], access: str | None):
    host = imap_host_for(email_addr, secret)
    if not host:
        raise MailError("无法确定 IMAP 服务器")
    port = int(secret.get("imap_port") or 993)
    try:
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailError(f"IMAP 连接失败 {host}:{port}: {exc}") from exc
    try:
        if access:
            conn.authenticate(
                "XOAUTH2",
                lambda _challenge: f"user={email_addr}\x01auth=Bearer {access}\x01\x01".encode(),
            )
        else:
            password = str(secret.get("password") or "")
            if not password:
                raise MailError("缺少邮箱密码")
            conn.login(email_addr, password)
    except imaplib.IMAP4.error as exc:
        try:
            conn.logout()
        except Exception:
            pass
        raise MailError(f"IMAP 登录失败: {exc}") from exc
    return conn


def _imap_list(email_addr: str, secret: dict[str, Any], access: str | None, limit: int) -> list[MailMessage]:
    conn = _imap_connect(email_addr, secret, access)
    out: list[MailMessage] = []
    seen: set[str] = set()
    try:
        for folder in _FOLDERS:
            try:
                typ, _ = conn.select(f'"{folder}"', readonly=True)
                if typ != "OK":
                    conn.select(folder, readonly=True)
            except imaplib.IMAP4.error:
                continue
            try:
                typ, data = conn.uid("search", None, "ALL")
                if typ != "OK" or not data or not data[0]:
                    continue
                uids = data[0].split()[-limit:]
            except imaplib.IMAP4.error:
                continue
            for uid in reversed(uids):
                uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
                try:
                    typ, msg_data = conn.uid(
                        "fetch",
                        uid_s,
                        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)] FLAGS)",
                    )
                except imaplib.IMAP4.error:
                    continue
                if typ != "OK":
                    continue
                raw = b""
                flags = ""
                for part in msg_data:
                    if isinstance(part, tuple):
                        raw = part[1] or b""
                        meta = part[0].decode("utf-8", "replace") if isinstance(part[0], bytes) else str(part[0])
                        flags = meta
                if not raw:
                    continue
                msg = email.message_from_bytes(raw, policy=policy.default)
                mid = f"imap:{folder}:{uid_s}"
                if mid in seen:
                    continue
                seen.add(mid)
                received = 0.0
                try:
                    dt = parsedate_to_datetime(str(msg.get("Date") or ""))
                    received = dt.timestamp()
                except Exception:
                    received = 0.0
                out.append(
                    MailMessage(
                        id=mid,
                        subject=_decode_header(msg.get("Subject")),
                        sender=_decode_header(msg.get("From")),
                        to=_decode_header(msg.get("To")),
                        received_at=received,
                        unread="\\Seen" not in flags,
                    )
                )
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    out.sort(key=lambda m: m.received_at, reverse=True)
    return out[:limit]


def _imap_get(email_addr: str, secret: dict[str, Any], access: str | None, msg_id: str) -> MailMessage:
    parts = msg_id.split(":")
    if len(parts) < 3 or parts[0] != "imap":
        raise MailError(f"非 IMAP 消息 id: {msg_id}")
    folder, uid = parts[1], parts[2]
    conn = _imap_connect(email_addr, secret, access)
    try:
        conn.select(f'"{folder}"', readonly=True)
        typ, data = conn.uid("fetch", uid, "(RFC822)")
        if typ != "OK":
            raise MailError(f"读取邮件失败: {uid}")
        raw = b""
        for part in data:
            if isinstance(part, tuple):
                raw = part[1] or b""
                break
        if not raw:
            raise MailError("邮件正文为空")
        return _parse_rfc822(msg_id, raw)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _parse_rfc822(msg_id: str, raw: bytes) -> MailMessage:
    msg = email.message_from_bytes(raw, policy=policy.default)
    text, html = "", ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if part.get_content_disposition() == "attachment":
                    continue
                try:
                    content = part.get_content()
                except Exception:
                    continue
                if ctype == "text/plain":
                    text += str(content)
                elif ctype == "text/html":
                    html += str(content)
        else:
            content = msg.get_content()
            if msg.get_content_type() == "text/html":
                html = str(content)
            else:
                text = str(content)
    except Exception:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode("utf-8", "replace")
    received = 0.0
    try:
        received = parsedate_to_datetime(str(msg.get("Date") or "")).timestamp()
    except Exception:
        pass
    return MailMessage(
        id=msg_id,
        subject=_decode_header(msg.get("Subject")),
        sender=_decode_header(msg.get("From")),
        to=_decode_header(msg.get("To")),
        received_at=received,
        text=text,
        html=html,
    )


# --------------------------------------------------------------------------- #
# Graph REST
# --------------------------------------------------------------------------- #
async def _graph_list(email_addr: str, access: str, limit: int) -> list[MailMessage]:
    out: list[MailMessage] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        for folder in ("inbox", "junkemail"):
            url = (
                f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
                f"?$top={min(limit, 20)}&$select=id,subject,from,toRecipients,receivedDateTime,bodyPreview,isRead"
                f"&$orderby=receivedDateTime desc"
            )
            try:
                r = await client.get(url, headers={"Authorization": f"Bearer {access}"})
            except httpx.HTTPError:
                continue
            if r.status_code >= 400:
                continue
            for row in (r.json().get("value") or []):
                frm = ((row.get("from") or {}).get("emailAddress") or {})
                sender = frm.get("name") or frm.get("address") or ""
                if frm.get("address") and frm.get("name"):
                    sender = f"{frm['name']} <{frm['address']}>"
                when = 0.0
                try:
                    when = time.mktime(time.strptime(row.get("receivedDateTime", "")[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    pass
                out.append(
                    MailMessage(
                        id="graph:" + str(row.get("id") or ""),
                        subject=str(row.get("subject") or ""),
                        sender=sender,
                        received_at=when,
                        text=str(row.get("bodyPreview") or ""),
                        unread=not bool(row.get("isRead")),
                    )
                )
    out.sort(key=lambda m: m.received_at, reverse=True)
    return out[:limit]


async def _graph_get(email_addr: str, access: str, msg_id: str) -> MailMessage:
    gid = msg_id.split("graph:", 1)[-1]
    url = (
        "https://graph.microsoft.com/v1.0/me/messages/"
        + httpx.URL(gid).path.split("/")[-1]
        + "?$select=subject,from,receivedDateTime,body"
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {access}"})
    if r.status_code >= 400:
        raise MailError(f"graph 读取失败 http {r.status_code}")
    row = r.json()
    body = row.get("body") or {}
    content = str(body.get("content") or "")
    is_html = "html" in str(body.get("contentType") or "").lower()
    return MailMessage(
        id=msg_id,
        subject=str(row.get("subject") or ""),
        sender=str(((row.get("from") or {}).get("emailAddress") or {}).get("address") or ""),
        received_at=0.0,
        text="" if is_html else content,
        html=content if is_html else "",
    )


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
async def _access_token(secret: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best-effort access token; None means 'use the plain password'."""
    if str(secret.get("refresh_token") or "").strip() and str(secret.get("client_id") or "").strip():
        try:
            token, rotated = await refresh_access_token(secret)
            return token, rotated
        except MailError as exc:
            logger.info("mail refresh failed: %s", exc)
    return None, None


async def list_messages(
    email_addr: str, secret: dict[str, Any], *, limit: int = 15
) -> tuple[list[MailMessage], str | None]:
    """List newest mail. Returns ``(messages, rotated_refresh_token)``."""
    access, rotated = await _access_token(secret)
    errs: list[str] = []
    try:
        msgs = await asyncio.to_thread(_imap_list, email_addr, secret, access, limit)
        return msgs, rotated
    except MailError as exc:
        errs.append(str(exc))
    if access:
        try:
            return await _graph_list(email_addr, access, limit), rotated
        except MailError as exc:
            errs.append(str(exc))
    raise MailError("；".join(errs) or "收件失败")


async def get_message(
    email_addr: str, secret: dict[str, Any], msg_id: str
) -> MailMessage:
    access, _ = await _access_token(secret)
    if msg_id.startswith("graph:"):
        if not access:
            raise MailError("graph 消息需要 access_token")
        return await _graph_get(email_addr, access, msg_id)
    return await asyncio.to_thread(_imap_get, email_addr, secret, access, msg_id)


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
    return re.sub(r"\s+", " ", text)


def extract_code(text: str, *, lengths: Iterable[int] = (6, 8)) -> str | None:
    """Pick an OTP-looking digit run; prefer matches near 'code/verification'."""
    want = {int(x) for x in lengths}
    body = text or ""
    candidates = [m for m in _CODE_RE.finditer(body) if len(m.group(1)) in want]
    if not candidates:
        return None
    for m in candidates:
        start = max(0, m.start() - 80)
        if _CTX.search(body[start : m.end() + 80]):
            return m.group(1)
    return candidates[0].group(1)


async def wait_for_code(
    email_addr: str,
    secret: dict[str, Any],
    *,
    lengths: Iterable[int] = (6, 8),
    timeout: float = 90.0,
    interval: float = 5.0,
    since_ts: float = 0.0,
    sender_hints: Iterable[str] = ("muse", "meta", "facebook", "hatch"),
) -> tuple[str | None, str | None]:
    """Poll the mailbox until a code arrives.

    Returns ``(code, rotated_refresh_token)``. ``since_ts`` filters out older
    mail so a stale code from a previous attempt is never reused.
    """
    deadline = time.time() + max(0.0, timeout)
    rotated_out: str | None = None
    hints = tuple(h.lower() for h in sender_hints)
    first = True
    while True:
        try:
            msgs, rotated = await list_messages(email_addr, secret, limit=10)
            if rotated:
                rotated_out = rotated
            for msg in msgs:
                if since_ts and msg.received_at and msg.received_at + 5 < since_ts:
                    continue
                haystack = f"{msg.subject} {msg.sender}".lower()
                if hints and not any(h in haystack for h in hints):
                    continue
                detail = msg
                if not (msg.text or msg.html):
                    try:
                        detail = await get_message(email_addr, secret, msg.id)
                    except MailError:
                        continue
                body = f"{detail.text} {html_to_text(detail.html)}"
                code = extract_code(f"{detail.subject}\n{body}", lengths=lengths)
                if code:
                    return code, rotated_out
        except MailError as exc:
            logger.info("mail poll failed: %s", exc)
        if time.time() >= deadline:
            return None, rotated_out
        first = False
        await asyncio.sleep(max(1.0, interval))
