"""platformTrustToken (PTT) construction for /api/checkout/credential.

Observed wire shape (see 协议注册.MD §3.5)::

    platformTrustToken = base64(json({"payload": "<JWE compact>", "signatures": []}))

The JWE is ``alg=ECDH-ES`` + ``enc=A256GCM`` (RFC 7518 §4.6 / RFC 7516), with the
recipient key taken from the certificate chain returned by
``POST /api/ptt/encryption-key`` (``trustChain`` leaf certificate). The card
material (PAN / CSC / holder / ZIP) travels only inside that JWE; the outer
credential request keeps the masked ``cardFirst6`` / ``cardLast4`` / expiry.

``mode="direct"`` is a debug fallback that emits the same JSON without the JWE
envelope, so the exact e2ee layout can be iterated against the live endpoint
without touching the card store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PttError(Exception):
    """No usable recipient key / malformed trust chain."""


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_dec(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _b64_std(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _concat_kdf(z: bytes, alg_id: bytes, apu: bytes, apv: bytes, keybits: int = 256) -> bytes:
    """NIST SP 800-56A Concat KDF as profiled by RFC 7518 §4.6.2 (single round)."""
    supp_pub = keybits.to_bytes(4, "big")
    other_info = (
        len(alg_id).to_bytes(4, "big")
        + alg_id
        + len(apu).to_bytes(4, "big")
        + apu
        + len(apv).to_bytes(4, "big")
        + apv
        + supp_pub
    )
    return hashlib.sha256((1).to_bytes(4, "big") + z + other_info).digest()


# --------------------------------------------------------------------------- #
# trust chain → recipient public key
# --------------------------------------------------------------------------- #
def _load_cert(entry: Any) -> x509.Certificate | None:
    if isinstance(entry, x509.Certificate):
        return entry
    if not isinstance(entry, str):
        return None
    text = entry.strip()
    if not text:
        return None
    if "BEGIN CERTIFICATE" in text:
        try:
            return x509.load_pem_x509_certificate(text.encode("utf-8"))
        except ValueError:
            return None
    # base64 (std or url-safe) DER
    for decoder in (base64.b64decode, _b64u_dec):
        try:
            raw = decoder(text)
        except Exception:
            continue
        if not raw:
            continue
        try:
            return x509.load_der_x509_certificate(raw)
        except ValueError:
            continue
    return None


def parse_trust_chain(trust_chain: Any) -> list[x509.Certificate]:
    if not trust_chain:
        return []
    if isinstance(trust_chain, str):
        trust_chain = [trust_chain]
    out: list[x509.Certificate] = []
    for entry in trust_chain:
        cert = _load_cert(entry)
        if cert is not None:
            out.append(cert)
    return out


def _ec_public_key_from_chain(trust_chain: Any) -> ec.EllipticCurvePublicKey:
    for cert in parse_trust_chain(trust_chain):
        key = cert.public_key()
        if isinstance(key, ec.EllipticCurvePublicKey):
            return key
    raise PttError(
        "trustChain 中没有 EC 公钥证书（PTT 需要 ECDH-ES 接收方密钥）"
    )


def _recipient_key(ptt_key: dict[str, Any]) -> tuple[ec.EllipticCurvePublicKey, str]:
    if not isinstance(ptt_key, dict):
        raise PttError("ptt/encryption-key 响应为空")
    for name in ("public_key", "publicKey", "encryption_key", "encryptionKey", "key"):
        raw = ptt_key.get(name)
        if isinstance(raw, str) and raw.strip():
            text = raw.strip()
            try:
                if "BEGIN PUBLIC KEY" in text:
                    key = serialization.load_pem_public_key(text.encode())
                else:
                    try:
                        der = base64.b64decode(text)
                    except Exception:
                        der = _b64u_dec(text)
                    key = serialization.load_der_public_key(der)
                if isinstance(key, ec.EllipticCurvePublicKey):
                    return key, str(ptt_key.get("id") or ptt_key.get("kid") or "")
            except Exception:
                pass
    certs = parse_trust_chain(ptt_key.get("trustChain") or ptt_key.get("trust_chain"))
    for cert in certs:
        key = cert.public_key()
        if isinstance(key, ec.EllipticCurvePublicKey):
            return key, str(ptt_key.get("id") or ptt_key.get("kid") or "")
    raise PttError(
        "ptt/encryption-key 未提供可用 EC 公钥（trustChain 无法解析）"
    )


# --------------------------------------------------------------------------- #
# ECDH-ES + A256GCM
# --------------------------------------------------------------------------- #
def jwe_ecdh_es(plaintext: bytes, recipient: ec.EllipticCurvePublicKey, *, kid: str = "") -> str:
    ephemeral = ec.generate_private_key(recipient.curve)
    epk = ephemeral.public_key()
    z = ephemeral.exchange(ec.ECDH(), recipient)

    protected: dict[str, Any] = {
        "alg": "ECDH-ES",
        "enc": "A256GCM",
        "epk": {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64u(epk.public_numbers().x.to_bytes(32, "big")),
            "y": _b64u(epk.public_numbers().y.to_bytes(32, "big")),
        },
    }
    if kid:
        protected["kid"] = kid
    header_b64 = _b64u(
        json.dumps(protected, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    # RFC 7518: for direct key agreement the CEK is the KDF output; AlgorithmID
    # is the "enc" value, no apu/apv is sent.
    cek = _concat_kdf(z, b"A256GCM", b"", b"")
    iv = os.urandom(12)
    ct_tag = AESGCM(cek).encrypt(iv, plaintext, header_b64.encode("ascii"))
    ct, tag = ct_tag[:-16], ct_tag[-16:]
    return ".".join([header_b64, "", _b64u(iv), _b64u(ct), _b64u(tag)])


def decrypt_jwe(jwe: str, recipient_private: ec.EllipticCurvePrivateKey) -> bytes:
    """Self-test helper: open a JWE produced by :func:`jwe_ecdh_es`."""
    header_b64, enc_key, iv_b64, ct_b64, tag_b64 = jwe.split(".")
    if enc_key:
        raise PttError("仅支持直接密钥协商（encrypted key 为空）")
    header = json.loads(_b64u_dec(header_b64))
    epk = header["epk"]
    curve = ec.SECP256R1()
    pub = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_b64u_dec(epk["x"]), "big"),
        int.from_bytes(_b64u_dec(epk["y"]), "big"),
        curve,
    ).public_key()
    z = recipient_private.exchange(ec.ECDH(), pub)
    cek = _concat_kdf(z, b"A256GCM", b"", b"")
    ct = _b64u_dec(ct_b64)
    tag = _b64u_dec(tag_b64)
    return AESGCM(cek).decrypt(_b64u_dec(iv_b64), ct + tag, header_b64.encode("ascii"))


# --------------------------------------------------------------------------- #
# payload + token
# --------------------------------------------------------------------------- #
def e2ee_payload(card: dict[str, Any], *, device_id: str = "", issued_at_ms: int | None = None) -> dict[str, Any]:
    """Card material as it is sealed inside the PTT.

    Field names follow the ticket metadata referenced in the capture
    (``credit_card`` and ``csc`` are the ``$e2ee`` placeholders).
    """
    exp_year = int(card.get("exp_year") or 0)
    if exp_year < 100:
        exp_year += 2000
    return {
        "credit_card": {
            "number": str(card.get("pan") or ""),
            "exp_month": int(card.get("exp_month") or 0),
            "exp_year": exp_year,
            "cardholder_name": str(card.get("holder") or ""),
            "zip": str(card.get("zip") or ""),
            "country": str(card.get("country") or "US"),
            "brand": str(card.get("brand") or ""),
        },
        "csc": str(card.get("cvv") or ""),
        "device_id": device_id,
        "issued_at": issued_at_ms if issued_at_ms is not None else int(time.time() * 1000),
    }


def build_platform_trust_token(
    ptt_key: dict[str, Any],
    card: dict[str, Any],
    *,
    mode: str = "jwe",
    device_id: str = "",
) -> str:
    payload = e2ee_payload(card, device_id=device_id)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if (mode or "jwe").lower() == "direct":
        return _b64_std(raw)
    recipient, kid = _recipient_key(ptt_key or {})
    jwe = jwe_ecdh_es(raw, recipient, kid=kid)
    outer = {
        "payload": jwe,
        "signatures": [],
        "key_id": kid or (ptt_key or {}).get("id") or "",
        "issued_at": payload["issued_at"],
    }
    return _b64_std(json.dumps(outer, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def describe_ptt(ptt_key: dict[str, Any]) -> dict[str, Any]:
    """Non-secret summary for task logs."""
    if not isinstance(ptt_key, dict):
        return {"ok": False}
    chain = parse_trust_chain(ptt_key.get("trustChain") or ptt_key.get("trust_chain"))
    curve = ""
    if chain:
        key = chain[0].public_key()
        if isinstance(key, ec.EllipticCurvePublicKey):
            curve = key.curve.name
    return {
        "ok": bool(chain),
        "id": str(ptt_key.get("id") or ""),
        "chain_len": len(chain),
        "leaf_curve": curve,
        "platform_trust_token_present": bool(ptt_key.get("platformTrustToken")),
        "dev_external": bool(ptt_key.get("devExternal")),
    }


# --------------------------------------------------------------------------- #
# Meta PTT ticket format (recovered from a real credential/finalize capture)
#
# credential token = base64(json({"payload": "<ticket_b64>.<jwe_compact>",
#                                  "signatures": []}))
#   ticket  = {"data":{"app_id":"2805739562851096","auth_ticket_type":"PAN",
#              "cardholder_name":"","credit_card":"$e2ee","csc":"$e2ee",
#              "device_id":"DEVICE_ID","expiry_month":"MM","expiry_year":"YYYY"},
#              "nonce":"<uuid4>","op":"ADD_CARD","ver":1}
#   jwe     = ECDH-ES/A256GCM, protected header carries apu="" and
#             apv="fp:<b64u sha256 seed>", epk serialised as PEM; PAN/CSC travel
#             in the JWE plaintext.
# finalize token = base64(json({... "op":"CREATE_CONTAINER", "ver":1}))
# --------------------------------------------------------------------------- #
APP_ID = "2805739562851096"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _kdf(z: bytes, apv: bytes, keybits: int = 256) -> bytes:
    # Concat KDF with params ["A256GCM", "", apv] as the *strings* the web
    # client feeds in (party V info is the "fp:..." text, not its base64 body).
    alg = b"A256GCM"
    other = (
        len(alg).to_bytes(4, "big")
        + alg
        + (0).to_bytes(4, "big")
        + len(apv).to_bytes(4, "big")
        + apv
        + keybits.to_bytes(4, "big")
    )
    return hashlib.sha256((1).to_bytes(4, "big") + z + other).digest()


def _spki_fingerprint(recipient) -> str:
    der = recipient.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "fp:" + _b64u(hashlib.sha256(der).digest())


def _jwe_ticket(
    plaintext: bytes, recipient, ticket_b64: str, apv: str | None = None
) -> str:
    eph = ec.generate_private_key(ec.SECP256R1())
    z = eph.exchange(ec.ECDH(), recipient)
    pem = eph.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")
    fp = apv or _spki_fingerprint(recipient)  # "fp:<b64u sha256(spki)>"
    header = {
        "alg": "ECDH-ES",
        "apu": "",
        # header carries base64u(fp) exactly like the web client
        "apv": _b64u(fp.encode()),
        "enc": "A256GCM",
        "epk": {"crv": "P-256", "kty": "EC", "pem": pem},
    }
    protected = _b64u(json.dumps(header, separators=(",", ":")).encode())
    # AAD = protected || "." || ticket_b64  (the web client binds both)
    aad = (protected + "." + ticket_b64).encode()
    # concat-KDF party V info is the *raw* "fp:..." string
    cek = _kdf(z, fp.encode())
    iv = os.urandom(12)
    ct_tag = AESGCM(cek).encrypt(iv, plaintext, aad)
    return ".".join([protected, "", _b64u(iv), _b64u(ct_tag[:-16]), _b64u(ct_tag[-16:])])


def fingerprint_apv(seed: str = "") -> str:
    digest = hashlib.sha256((seed or "hatch-web").encode()).digest()
    return "fp:" + _b64u(digest)


def build_credential_token(
    ptt_key: dict[str, Any],
    card: dict[str, Any],
    *,
    plaintext: bytes | None = None,
    seed: str = "",
    device_id: str = "DEVICE_ID",
) -> str:
    recipient, _kid = _recipient_key(ptt_key or {})
    if plaintext is None:
        # captured order: {"csc":..., "credit_card":...}
        plaintext = json.dumps(
            {
                "csc": str(card.get("cvv") or ""),
                "credit_card": str(card.get("pan") or ""),
            },
            separators=(",", ":"),
        ).encode()
    ticket = {
        "data": {
            "app_id": APP_ID,
            "auth_ticket_type": "PAN",
            "cardholder_name": "",
            "credit_card": "$e2ee",
            "csc": "$e2ee",
            "device_id": "DEVICE_ID",
            "expiry_month": f"{int(card.get('exp_month') or 0):02d}",
            "expiry_year": str(int(card.get("exp_year") or 0)),
        },
        "nonce": str(uuid.uuid4()),
        "op": "ADD_CARD",
        "ver": 1,
    }
    ticket_b64 = _b64u(json.dumps(ticket, separators=(",", ":")).encode())
    payload = ticket_b64 + "." + _jwe_ticket(plaintext, recipient, ticket_b64)
    return base64.b64encode(
        json.dumps({"payload": payload, "signatures": []}, separators=(",", ":")).encode()
    ).decode()


def build_finalize_token(
    *, order_id: str, credential_id: str, amount: float = 1.0, currency: str = "USD"
) -> str:
    ticket = {
        "data": {
            "receiver_id": "352501280694489",
            "context_id": str(order_id),
            "container_mode": "LIVE",
            "components": [
                {"amount": amount, "cred_id": str(credential_id), "currency": currency}
            ],
            "security_origin": "https://muse.ai",
        },
        "nonce": str(uuid.uuid4()),
        "op": "CREATE_CONTAINER",
        "ver": 1,
    }
    return base64.b64encode(json.dumps(ticket, separators=(",", ":")).encode()).decode()
