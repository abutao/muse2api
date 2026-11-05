"""#PWD_BROWSER:5 password envelope — real hatch/Meta wire format.

Observed wire string (see 协议注册.MD §4.4)::

    #PWD_BROWSER:5:<unix_ts>:<base64(binary)>

``binary`` layout::

    offset 0      : 0x01                      internal version
    offset 1      : key_id                    uint8, from restart.password_encryption.key_id
    offset 2..3   : sealed_len                uint16 little-endian
    offset 4..    : sealed                    crypto_box_seal(K, server_pubkey)  (48+32 bytes)
    then          : tag                       16 bytes (AES-256-GCM tag)
    then          : body                      AES-256-GCM ciphertext of the UTF-8 password

Password encryption (done locally, plaintext never leaves the process):

  * ``K`` = 32 random bytes (AES-256 key)
  * AES-GCM with ``iv`` = 12 zero bytes, ``additionalData`` = UTF-8(str(unix_ts)),
    ``tagLength`` = 128 bits → ``ct || tag``
  * ``K`` itself is sealed to the server's X25519 key with libsodium sealed box
    (ephemeral key + crypto_box, i.e. ``crypto_box_seal`` / ``SealedBox.seal``)
  * server side: opens the sealed box, then AES-GCM-decrypts with AAD = the same
    timestamp string carried in the wire prefix.

``mock=True`` (or a placeholder all-zero public key) keeps the legacy mock
envelope so MockMuseClient round-trips without libsodium.
"""

from __future__ import annotations

import base64
import os
import re
import time
from typing import Any

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
PUBLIC_KEY_BYTES = 32
PREFIX = "#PWD_BROWSER:"


# --------------------------------------------------------------------------- #
# key material
# --------------------------------------------------------------------------- #
def parse_public_key(password_encryption: dict[str, Any] | None) -> bytes | None:
    """Accept ``public_key`` as 64 hex chars or base64; must be 32 bytes."""
    if not isinstance(password_encryption, dict):
        return None
    raw = password_encryption.get("public_key") or password_encryption.get("publicKey")
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        text = str(raw).strip()
        if _HEX64.match(text):
            data = bytes.fromhex(text)
        else:
            try:
                data = base64.b64decode(text, validate=False)
            except Exception:
                return None
    return data if len(data) == PUBLIC_KEY_BYTES else None


def key_id_of(password_encryption: dict[str, Any] | None) -> int:
    """``key_id`` / ``keyId`` must be a safe 0-255 integer (defaults to 0)."""
    if not isinstance(password_encryption, dict):
        return 0
    raw = password_encryption.get("key_id")
    if raw is None:
        raw = password_encryption.get("keyId")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value & 0xFF if 0 <= value <= 255 else 0


def _is_placeholder(key: bytes | None) -> bool:
    """All-zero key = MockMuseClient / unit-test placeholder, not a real X25519 key."""
    return key is None or key == b"\x00" * PUBLIC_KEY_BYTES


# --------------------------------------------------------------------------- #
# wrap (client → server)
# --------------------------------------------------------------------------- #
def wrap_pwd_browser(
    plaintext: str,
    password_encryption: dict[str, Any] | None = None,
    *,
    mock: bool | None = None,
) -> str:
    """Wrap a plaintext password into the wire envelope.

    ``mock=None`` auto-detects: a placeholder/absent public key produces the
    legacy mock envelope; a real 32-byte key produces the libsodium + AES-GCM
    envelope. Already-wrapped strings pass through untouched.
    """
    if not plaintext:
        raise ValueError("password is empty")
    if plaintext.startswith(PREFIX):
        return plaintext

    ts = int(time.time())
    public_key = parse_public_key(password_encryption)
    if mock is None:
        mock = _is_placeholder(public_key)
    if mock:
        return _wrap_mock(plaintext, ts, key_id_of(password_encryption))
    if public_key is None:
        raise ValueError("password_encryption.public_key is not a 32-byte key")
    return _wrap_real(plaintext, ts, key_id_of(password_encryption), public_key)


def _wrap_real(plaintext: str, ts: int, key_id: int, public_key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from nacl import bindings as nb

    aes_key = os.urandom(32)  # K
    iv = b"\x00" * 12  # all-zero nonce, as observed in the browser bundle
    aad = str(ts).encode("utf-8")
    ct_and_tag = AESGCM(aes_key).encrypt(iv, plaintext.encode("utf-8"), aad)
    body, tag = ct_and_tag[:-16], ct_and_tag[-16:]

    # libsodium crypto_box_seal (PyNaCl exposes it only through bindings)
    sealed = nb.crypto_box_seal(aes_key, public_key)
    if len(sealed) > 0xFFFF:
        raise ValueError("sealed key too large for uint16 length prefix")

    blob = (
        bytes([0x01, key_id])
        + len(sealed).to_bytes(2, "little")
        + sealed
        + tag
        + body
    )
    return f"{PREFIX}5:{ts}:{base64.b64encode(blob).decode('ascii')}"


def _wrap_mock(plaintext: str, ts: int, key_id: int) -> str:
    """Legacy mock: version(1) + key_id(1) + utf8 password (no crypto)."""
    raw = bytes([0x01, key_id]) + plaintext.encode("utf-8")
    return f"{PREFIX}5:{ts}:{base64.b64encode(raw).decode('ascii')}"


# --------------------------------------------------------------------------- #
# unwrap (server-side parity check / self-test)
# --------------------------------------------------------------------------- #
def split_wrapped(wrapped: str) -> tuple[int, str] | None:
    """Return ``(ts, b64)`` from ``#PWD_BROWSER:5:<ts>:<b64>``."""
    if not wrapped or not wrapped.startswith(PREFIX):
        return None
    parts = wrapped.split(":", 3)
    if len(parts) != 4 or parts[1] != "5":
        return None
    try:
        return int(parts[2]), parts[3]
    except ValueError:
        return None


def unwrap_pwd_browser(wrapped: str, private_key: bytes) -> tuple[str, int, int]:
    """Decrypt a *real* envelope with the matching X25519 private key.

    Returns ``(plaintext, unix_ts, key_id)``. Raises ``ValueError`` on any
    structural or cryptographic mismatch.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from nacl import bindings as nb
    from nacl.public import PrivateKey

    parsed = split_wrapped(wrapped)
    if parsed is None:
        raise ValueError("not a #PWD_BROWSER:5 envelope")
    ts, b64 = parsed
    blob = base64.b64decode(b64, validate=True)
    if len(blob) < 4 + 48 + 16:
        raise ValueError("envelope too short")
    if blob[0] != 0x01:
        raise ValueError(f"unsupported inner version {blob[0]}")
    key_id = blob[1]
    sealed_len = int.from_bytes(blob[2:4], "little")
    head = 4
    sealed = blob[head : head + sealed_len]
    rest = blob[head + sealed_len :]
    if len(sealed) != sealed_len or len(rest) <= 16:
        raise ValueError("sealed key / tag truncated")
    pk = PrivateKey(private_key)
    aes_key = nb.crypto_box_seal_open(sealed, bytes(pk.public_key), bytes(pk))
    tag, body = rest[:16], rest[16:]  # wire order: tag then body
    plaintext = AESGCM(aes_key).decrypt(b"\x00" * 12, body + tag, str(ts).encode("utf-8"))
    return plaintext.decode("utf-8"), ts, key_id


def unwrap_pwd_browser_mock(wrapped: str) -> str | None:
    """Best-effort unwrap of the legacy mock envelope; None if not mock-shaped."""
    parsed = split_wrapped(wrapped)
    if parsed is None:
        return None
    _, b64 = parsed
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if len(raw) < 2 or raw[0] != 0x01:
        return None
    try:
        return raw[2:].decode("utf-8")
    except UnicodeDecodeError:
        return None


def is_real_wrapped(wrapped: str) -> bool:
    """True when the envelope carries a sealed key (real wire format)."""
    parsed = split_wrapped(wrapped)
    if parsed is None:
        return False
    try:
        blob = base64.b64decode(parsed[1])
    except Exception:
        return False
    if len(blob) < 4:
        return False
    sealed_len = int.from_bytes(blob[2:4], "little")
    return sealed_len >= 48 and len(blob) > 4 + sealed_len


# --------------------------------------------------------------------------- #
# mock session key (offline QA only)
# --------------------------------------------------------------------------- #
def mock_password_encryption() -> dict[str, Any]:
    """Placeholder restart payload; all-zero key routes to the mock envelope."""
    return {
        "key_id": 253,
        "public_key": "00" * PUBLIC_KEY_BYTES,  # 64 hex chars
    }
