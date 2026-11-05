"""Verify app.signup.pwd_browser against the documented #PWD_BROWSER:5 format.

Default run = full offline proof (structure, crypto round-trip, tamper checks,
mock compatibility). With --envelope + --private-key it additionally decrypts a
real captured envelope that you hold the X25519 private key for.

    python scripts/verify_pwd_browser.py
    python scripts/verify_pwd_browser.py --envelope '#PWD_BROWSER:5:...' --private-key <hex64|b64>
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from nacl.public import PrivateKey  # noqa: E402

from app.signup.pwd_browser import (  # noqa: E402
    is_real_wrapped,
    key_id_of,
    mock_password_encryption,
    parse_public_key,
    split_wrapped,
    unwrap_pwd_browser,
    unwrap_pwd_browser_mock,
    wrap_pwd_browser,
)

OK = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global _failures
    if cond:
        print(f"  [{OK}] {name}" + (f"  {extra}" if extra else ""))
    else:
        _failures += 1
        print(f"  [{FAIL}] {name}" + (f"  {extra}" if extra else ""))


def decode_blob(wrapped: str) -> bytes:
    parsed = split_wrapped(wrapped)
    assert parsed is not None, "not a #PWD_BROWSER:5 envelope"
    return base64.b64decode(parsed[1], validate=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--envelope", help="real captured #PWD_BROWSER:5 string")
    ap.add_argument("--private-key", help="matching X25519 private key (hex64 or base64)")
    args = ap.parse_args()

    server = PrivateKey.generate()
    server_pub_hex = bytes(server.public_key).hex()
    pe = {"key_id": 253, "public_key": server_pub_hex}
    password = "Mu$e-Str0ng-Passw0rd!"

    print("\n== 1. public key parsing ==")
    check("hex64 key parses to 32 bytes", parse_public_key(pe) == bytes(server.public_key))
    check(
        "base64 key parses identically",
        parse_public_key({"keyId": 7, "publicKey": base64.b64encode(bytes(server.public_key)).decode()})
        == bytes(server.public_key),
    )
    check("camelCase keyId accepted", key_id_of({"keyId": 12}) == 12)
    check("out-of-range key_id clamps to 0", key_id_of({"key_id": 999}) == 0)

    print("\n== 2. envelope structure ==")
    env = wrap_pwd_browser(password, pe)
    check("prefix '#PWD_BROWSER:5:'", env.startswith("#PWD_BROWSER:5:"))
    parsed = split_wrapped(env)
    check("splits into ts + b64", parsed is not None)
    assert parsed is not None
    ts, _ = parsed
    blob = decode_blob(env)
    sealed_len = int.from_bytes(blob[2:4], "little")
    check("inner version byte == 0x01", blob[0] == 0x01, f"got {blob[0]:#04x}")
    check("key_id byte == 253", blob[1] == 253, f"got {blob[1]}")
    check("sealed length == 80 (48 + 32)", sealed_len == 80, f"got {sealed_len} (LE uint16)")
    body_len = len(blob) - 4 - sealed_len - 16
    check("body length == len(password)", body_len == len(password.encode()), f"got {body_len}")
    check("total blob = 4 + 80 + 16 + body", len(blob) == 4 + 80 + 16 + len(password))
    check("wire length matches §4.4 (~174 for 20-char pwd)", 90 < len(env) < 220, f"len={len(env)}")
    check("is_real_wrapped()", is_real_wrapped(env))

    print("\n== 3. server-side round-trip ==")
    plain, got_ts, got_key_id = unwrap_pwd_browser(env, bytes(server))
    check("password decrypts", plain == password, f"{plain!r}")
    check("timestamp matches prefix", got_ts == ts)
    check("key_id preserved", got_key_id == 253)

    print("\n== 4. independent re-implementation (no app modules) ==")
    # Re-do the documented math from scratch: seal the AES key ourselves and
    # confirm the wire blob is byte-identical in shape, then decrypt with raw
    # libsodium bindings to prove no helper is hiding a detail.
    import nacl.bindings as nb

    k = os.urandom(32)
    iv = bytes(12)
    aad = str(ts).encode()
    ct_tag = AESGCM(k).encrypt(iv, password.encode(), aad)
    sealed = nb.crypto_box_seal(k, bytes(server.public_key))
    rebuilt = bytes([1, 253]) + len(sealed).to_bytes(2, "little") + sealed + ct_tag[-16:] + ct_tag[:-16]
    check("sealed box is 80 bytes (crypto_box_seal)", len(sealed) == 80)
    check("rebuilt blob length matches", len(rebuilt) == len(blob))
    # decrypt the app-produced blob using the raw binding path
    opened = nb.crypto_box_seal_open(blob[4 : 4 + sealed_len], bytes(server.public_key), bytes(server))
    rest = blob[4 + sealed_len :]
    check(
        "raw crypto_box_seal_open + AESGCM recovers password",
        AESGCM(opened).decrypt(iv, rest[16:] + rest[:16], str(ts).encode()).decode() == password,
    )

    print("\n== 5. tamper / negative cases ==")
    for label, mutate in (
        ("AAD timestamp mismatch", lambda: (str(ts + 1), blob)),
        ("bit flip in ciphertext body", lambda: (str(ts), blob[:-1] + bytes([blob[-1] ^ 0x01]))),
        ("bit flip in GCM tag", lambda: (str(ts), blob[:-17] + bytes([blob[-17] ^ 0x01]) + blob[-16:])),
    ):
        new_ts, new_blob = mutate()
        tampered = f"#PWD_BROWSER:5:{new_ts}:{base64.b64encode(new_blob).decode()}"
        try:
            unwrap_pwd_browser(tampered, bytes(server))
            check(label + " rejected", False)
        except Exception as exc:  # noqa: BLE001
            check(label + " rejected", True, type(exc).__name__)
    try:
        unwrap_pwd_browser(env, bytes(PrivateKey.generate()))
        check("wrong private key rejected", False)
    except Exception as exc:  # noqa: BLE001
        check("wrong private key rejected", True, type(exc).__name__)

    print("\n== 6. mock envelope (offline QA path) ==")
    mock_env = wrap_pwd_browser("mock-pass", mock_password_encryption())
    check("auto-detects placeholder key → mock", not is_real_wrapped(mock_env))
    check("mock unwrap round-trips", unwrap_pwd_browser_mock(mock_env) == "mock-pass")
    forced = wrap_pwd_browser("forced-pass", pe, mock=True)
    check("mock=True override works", unwrap_pwd_browser_mock(forced) == "forced-pass")
    check("pre-wrapped input passes through", wrap_pwd_browser(env, pe) == env)

    print("\n== 7. real capture (optional) ==")
    if args.envelope and args.private_key:
        raw = args.private_key.strip()
        pk = bytes.fromhex(raw) if len(raw) == 64 else base64.b64decode(raw)
        plain, ts2, kid = unwrap_pwd_browser(args.envelope, pk)
        print(f"  [{OK}] decrypted real envelope: ts={ts2} key_id={kid} password_len={len(plain)}")
    else:
        print("  (skipped — pass --envelope + --private-key to decode a captured sample)")

    print(f"\n{'ALL CHECKS PASSED' if not _failures else f'{_failures} CHECK(S) FAILED'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
