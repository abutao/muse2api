"""Browser fingerprint profiles distilled from real muse.ai HAR / DevTools captures.

True source for registration (2026-11):
  /workspace/user-win-fingerprint.devtools — Windows Edge 156 (Win64)
  (same persona as user-capture.devtools / user-capture2.devtools)

Registration / auto-signup ALWAYS picks edge156-win:
  UA … Chrome/156 … Edg/156, sec-ch-ua Microsoft Edge 156, platform "Windows".
Linux / mac / Chrome-win profiles stay in the catalog only so legacy sessions
can still deserialize via from_dict — they are never chosen as defaults.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserFingerprint:
    """One browser persona for a signup session (stable for the session lifetime)."""

    profile_id: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str = "?0"
    sec_ch_ua_platform: str = '"Windows"'
    sec_ch_ua_full_version_list: str = ""
    sec_ch_prefers_color_scheme: str = "light"
    accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6"
    accept_encoding: str = "gzip, deflate, br, zstd"
    accept: str = "*/*"
    origin: str = "https://muse.ai"
    referer: str = "https://muse.ai/"
    sec_fetch_site: str = "same-origin"
    sec_fetch_mode: str = "cors"
    sec_fetch_dest: str = "empty"
    caa_reg_entry_point: str = "aymh"
    activation_source: str = ""
    wd: str = "1596x972"
    dpr: str = "1"
    registration_default: bool = False
    label_zh: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "user_agent": self.user_agent,
            "sec_ch_ua": self.sec_ch_ua,
            "sec_ch_ua_mobile": self.sec_ch_ua_mobile,
            "sec_ch_ua_platform": self.sec_ch_ua_platform,
            "sec_ch_ua_full_version_list": self.sec_ch_ua_full_version_list,
            "sec_ch_prefers_color_scheme": self.sec_ch_prefers_color_scheme,
            "accept_language": self.accept_language,
            "accept_encoding": self.accept_encoding,
            "accept": self.accept,
            "origin": self.origin,
            "referer": self.referer,
            "sec_fetch_site": self.sec_fetch_site,
            "sec_fetch_mode": self.sec_fetch_mode,
            "sec_fetch_dest": self.sec_fetch_dest,
            "caa_reg_entry_point": self.caa_reg_entry_point,
            "activation_source": self.activation_source,
            "wd": self.wd,
            "dpr": self.dpr,
            "registration_default": bool(self.registration_default),
            "label_zh": self.label_zh or fingerprint_label_zh(self.profile_id),
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BrowserFingerprint | None:
        """Restore a previously persisted persona (may be linux/mac/chrome legacy)."""
        if not data or not isinstance(data, dict):
            return None
        ua = data.get("user_agent")
        if not ua:
            return None
        pid = str(data.get("profile_id") or "custom")
        return cls(
            profile_id=pid,
            user_agent=str(ua),
            sec_ch_ua=str(data.get("sec_ch_ua") or ""),
            sec_ch_ua_mobile=str(data.get("sec_ch_ua_mobile") or "?0"),
            sec_ch_ua_platform=str(data.get("sec_ch_ua_platform") or '"Windows"'),
            sec_ch_ua_full_version_list=str(data.get("sec_ch_ua_full_version_list") or ""),
            sec_ch_prefers_color_scheme=str(
                data.get("sec_ch_prefers_color_scheme") or "light"
            ),
            accept_language=str(
                data.get("accept_language")
                or "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6"
            ),
            accept_encoding=str(data.get("accept_encoding") or "gzip, deflate, br, zstd"),
            accept=str(data.get("accept") or "*/*"),
            origin=str(data.get("origin") or "https://muse.ai"),
            referer=str(data.get("referer") or "https://muse.ai/"),
            sec_fetch_site=str(data.get("sec_fetch_site") or "same-origin"),
            sec_fetch_mode=str(data.get("sec_fetch_mode") or "cors"),
            sec_fetch_dest=str(data.get("sec_fetch_dest") or "empty"),
            caa_reg_entry_point=str(data.get("caa_reg_entry_point") or "aymh"),
            activation_source=str(data.get("activation_source") or ""),
            wd=str(data.get("wd") or "1596x972"),
            dpr=str(data.get("dpr") or "1"),
            registration_default=bool(data.get("registration_default", False)),
            label_zh=str(data.get("label_zh") or fingerprint_label_zh(pid)),
            extra=dict(data.get("extra") or {}),
        )

    def request_headers(
        self,
        *,
        csrf_token: str = "",
        waterfall_id: str = "",
        content_type: str = "application/json",
        include_caa: bool = True,
    ) -> dict[str, str]:
        h: dict[str, str] = {
            "user-agent": self.user_agent,
            "accept": self.accept,
            "accept-language": self.accept_language,
            "accept-encoding": self.accept_encoding,
            "origin": self.origin,
            "referer": self.referer,
            "sec-fetch-site": self.sec_fetch_site,
            "sec-fetch-mode": self.sec_fetch_mode,
            "sec-fetch-dest": self.sec_fetch_dest,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "sec-ch-prefers-color-scheme": self.sec_ch_prefers_color_scheme,
        }
        if self.sec_ch_ua_full_version_list:
            h["sec-ch-ua-full-version-list"] = self.sec_ch_ua_full_version_list
        if content_type:
            h["content-type"] = content_type
        if csrf_token:
            h["x-hatch-csrf-token"] = csrf_token
        if waterfall_id:
            h["x-hatch-waterfall-id"] = waterfall_id
        if include_caa and self.caa_reg_entry_point:
            h["x-hatch-caa-reg-entry-point"] = self.caa_reg_entry_point
        if self.activation_source:
            h["x-hatch-activation-source"] = self.activation_source
        for k, v in self.extra.items():
            if v:
                h[str(k)] = str(v)
        return h


_LABEL_ZH: dict[str, str] = {
    "edge156-win": "Windows Edge 156",
    "chrome156-win": "Windows Chrome 156（非注册默认）",
    "chrome156-win-zh": "Windows Chrome 156 中文（非注册默认）",
    "chrome156-linux": "Linux Chrome 156（已禁用注册）",
    "chrome156-mac": "macOS Chrome 156（已禁用注册）",
}


def fingerprint_label_zh(profile_id: str | None) -> str:
    """Chinese UI label for a fingerprint profile_id."""
    if not profile_id:
        return "无"
    pid = str(profile_id)
    if pid in _LABEL_ZH:
        return _LABEL_ZH[pid]
    low = pid.lower()
    if "edge" in low and ("win" in low or "windows" in low):
        return f"Windows Edge（{pid}）"
    if "win" in low or "windows" in low:
        return f"Windows（{pid}）"
    if "linux" in low:
        return f"Linux（遗留·{pid}）"
    if "mac" in low:
        return f"macOS（遗留·{pid}）"
    return pid


# Captured Windows Edge 156 persona (user-win-fingerprint.devtools).
# Full-version-list / platform-version / model taken from the richer twin capture
# user-capture2.devtools (same Edge 156 Win64 session shape).
_EDGE156_WIN = BrowserFingerprint(
    profile_id="edge156-win",
    user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0"
    ),
    sec_ch_ua='"Not:A-Brand";v="8", "Chromium";v="156", "Microsoft Edge";v="156"',
    sec_ch_ua_mobile="?0",
    sec_ch_ua_platform='"Windows"',
    sec_ch_ua_full_version_list=(
        '"Not:A-Brand";v="8.0.0.0", "Chromium";v="156.0.8078.80", '
        '"Microsoft Edge";v="156.0.4314.58"'
    ),
    sec_ch_prefers_color_scheme="light",
    accept_language="zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    accept_encoding="gzip, deflate, br, zstd",
    accept="*/*",
    origin="https://muse.ai",
    referer="https://muse.ai/",
    sec_fetch_site="same-origin",
    sec_fetch_mode="cors",
    sec_fetch_dest="empty",
    caa_reg_entry_point="aymh",
    wd="1596x972",
    dpr="1",
    registration_default=True,
    label_zh="Windows Edge 156",
    extra={
        "sec-ch-ua-model": '""',
        "sec-ch-ua-platform-version": '"19.0.0"',
        "sec-ch-dpr": "1",
        "sec-ch-viewport-width": "1596",
        "sec-ch-viewport-height": "972",
    },
)

_BROWSER_PROFILES: list[BrowserFingerprint] = [
    _EDGE156_WIN,
    # Catalog only — never chosen for new / auto registration
    BrowserFingerprint(
        profile_id="chrome156-win",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"',
        sec_ch_ua_platform='"Windows"',
        sec_ch_ua_full_version_list=(
            '"Not:A-Brand";v="10.0.0.4", "Chromium";v="156.0.0.0", '
            '"Google Chrome";v="156.0.0.0"'
        ),
        accept_language="en-US,en;q=0.9",
        caa_reg_entry_point="login_home",
        wd="1536x864",
        dpr="1.25",
        registration_default=False,
        label_zh="Windows Chrome 156（非注册默认）",
    ),
    BrowserFingerprint(
        profile_id="chrome156-linux",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"',
        sec_ch_ua_platform='"Linux"',
        sec_ch_ua_full_version_list=(
            '"Not:A-Brand";v="10.0.0.4", "Chromium";v="156.0.0.0", '
            '"Google Chrome";v="156.0.0.0"'
        ),
        accept_language="en-US,en-GB;q=0.9,en;q=0.8",
        caa_reg_entry_point="login_home",
        wd="1920x1080",
        dpr="1",
        registration_default=False,
        label_zh="Linux Chrome 156（已禁用注册）",
    ),
    BrowserFingerprint(
        profile_id="chrome156-mac",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156"',
        sec_ch_ua_platform='"macOS"',
        sec_ch_ua_full_version_list=(
            '"Not:A-Brand";v="10.0.0.4", "Chromium";v="156.0.0.0", '
            '"Google Chrome";v="156.0.0.0"'
        ),
        accept_language="en-US,en;q=0.9",
        caa_reg_entry_point="login_home",
        wd="1440x900",
        dpr="2",
        registration_default=False,
        label_zh="macOS Chrome 156（已禁用注册）",
    ),
]


def _clone(p: BrowserFingerprint) -> BrowserFingerprint:
    return BrowserFingerprint(**{**p.to_dict(), "extra": dict(p.extra)})


def _registration_pool() -> list[BrowserFingerprint]:
    pool = [p for p in _BROWSER_PROFILES if p.registration_default]
    if pool:
        return pool
    # Safety net: never fall back to linux/mac
    return [_EDGE156_WIN]


def list_profiles(*, registration_only: bool = False) -> list[str]:
    if registration_only:
        return [p.profile_id for p in _registration_pool()]
    return [p.profile_id for p in _BROWSER_PROFILES]


def list_profile_labels(*, registration_only: bool = True) -> list[dict[str, str]]:
    src = _registration_pool() if registration_only else _BROWSER_PROFILES
    return [
        {
            "profile_id": p.profile_id,
            "label_zh": p.label_zh or fingerprint_label_zh(p.profile_id),
            "platform": p.sec_ch_ua_platform,
            "registration_default": str(bool(p.registration_default)).lower(),
        }
        for p in src
    ]


def pick_fingerprint(profile_id: str | None = None) -> BrowserFingerprint:
    """Pick the Windows Edge 156 registration persona.

    Any missing / unknown / linux / mac / chrome pin remaps to edge156-win.
    Legacy sessions keep their stored fingerprint via from_dict, not this picker.
    """
    pool = _registration_pool()
    if profile_id:
        for p in pool:
            if p.profile_id == profile_id:
                return _jitter(_clone(p))
    base = random.choice(pool)
    return _jitter(_clone(base))


def _jitter(fp: BrowserFingerprint) -> BrowserFingerprint:
    """Light viewport jitter; keep Windows Edge identity intact."""
    # Stay near the captured 1596x972 desktop viewport
    widths = (1366, 1440, 1536, 1596, 1680, 1920)
    heights = (768, 864, 900, 972, 1050, 1080)
    w = random.choice(widths)
    h = random.choice(heights)
    fp.wd = f"{w}x{h}"
    fp.extra = dict(fp.extra or {})
    fp.extra["sec-ch-viewport-width"] = str(w)
    fp.extra["sec-ch-viewport-height"] = str(h)
    if random.random() < 0.25:
        fp.dpr = random.choice(["1", "1.25", "1.5"])
        fp.extra["sec-ch-dpr"] = fp.dpr
    if random.random() < 0.35:
        fp.caa_reg_entry_point = random.choice(
            ["aymh", "login_home", "create_account", "web_onboarding"]
        )
    fp.label_zh = fp.label_zh or fingerprint_label_zh(fp.profile_id)
    # Hard guarantee: registration picks always claim Windows Edge
    fp.sec_ch_ua_platform = '"Windows"'
    if "Edg/" not in fp.user_agent or "Windows NT" not in fp.user_agent:
        fp.user_agent = _EDGE156_WIN.user_agent
        fp.sec_ch_ua = _EDGE156_WIN.sec_ch_ua
        fp.profile_id = "edge156-win"
        fp.label_zh = "Windows Edge 156"
    return fp


def ensure_waterfall_id(existing: str = "") -> str:
    if existing and 8 <= len(existing) <= 64:
        return existing
    return str(uuid.uuid4())
