"""Engine runtime config (Chromium/CDP). Adapted from muse2api Config (MIT)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.config import DATA_DIR, ROOT_DIR, get_settings


def detect_chromium() -> str:
    configured = (
        os.environ.get("MUSE_PROXY_CHROMIUM")
        or os.environ.get("MUSE2API_CHROMIUM")
        or ""
    ).strip()
    if configured and (os.path.isfile(configured) or shutil.which(configured)):
        return configured
    try:
        s = get_settings()
        path = getattr(s, "chromium_path", "") or ""
        if path and (os.path.isfile(path) or shutil.which(path)):
            return path
    except Exception:
        pass
    candidates = [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.isfile(c) or shutil.which(c):
            return c
    return configured or "google-chrome"


@dataclass
class EngineConfig:
    base_dir: str = field(default_factory=lambda: str(ROOT_DIR))
    chromium: str = field(default_factory=detect_chromium)
    cdp_port: int = 19211
    home_dir: str = field(default_factory=lambda: os.path.expanduser("~"))
    extra_path: str = ""
    site_url: str = "https://muse.ai/"
    login_wait: int = 60
    profile_root: str = field(
        default_factory=lambda: str(DATA_DIR / "profiles")
    )
    image_timeout: int = 240
    video_timeout: int = 600
    chat_timeout: int = 300
    max_concurrency: int = 2
    ref_image_local_files: bool = False
    ref_image_private_fetch: bool = False
    tool_protocol: bool = False
    keepalive_disabled_accounts: bool = False

    @property
    def data_dir(self) -> str:
        return str(DATA_DIR)

    @property
    def media_dir(self) -> str:
        return str(DATA_DIR / "media")

    @property
    def download_dir(self) -> str:
        return str(DATA_DIR / "downloads")

    @property
    def profile_dir(self) -> str:
        return os.path.join(self.profile_root, "generate")

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.media_dir,
            self.download_dir,
            self.profile_dir,
        ):
            os.makedirs(d, exist_ok=True)



def load_engine_config() -> EngineConfig:
    s = get_settings()
    cfg = EngineConfig(
        chromium=getattr(s, "chromium_path", None) or detect_chromium(),
        cdp_port=int(getattr(s, "cdp_port", 19211) or 19211),
        chat_timeout=int(getattr(s, "request_timeout_sec", 300) or 300),
        max_concurrency=max(1, int(getattr(s, "engine_concurrency", 2) or 2)),
    )
    cfg.ensure_dirs()
    return cfg
