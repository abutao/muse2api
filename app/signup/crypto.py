"""Encrypt signup credentials at rest (Fernet)."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    raw = (settings.signup_credentials_key or settings.session_secret or "dev").encode()
    digest = hashlib.sha256(raw).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_dict(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_dict(blob: str | None) -> dict[str, Any]:
    if not blob:
        return {}
    try:
        raw = _fernet().decrypt(blob.encode())
        data = json.loads(raw.decode())
        return data if isinstance(data, dict) else {}
    except (InvalidToken, json.JSONDecodeError, TypeError, ValueError):
        return {}
