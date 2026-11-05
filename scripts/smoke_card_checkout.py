"""End-to-end smoke: card pool → mock signup → checkout → pooled account.

Runs fully offline (SIGNUP_CLIENT=mock) against a throwaway SQLite DB, so it is
safe to run at any time. Verifies:

  1. card file import (PAN|MM/YY|CVV|…|Live format)
  2. OTP registration through register-completion
  3. checkout via the pool (init → PTT → credential → finalize)
  4. card marked ``used`` and the signup item ``pooled``
  5. decline path → card ``dead``, item ``awaiting_card``
  6. empty pool → item ``awaiting_card`` (no crash)
  7. PTT JWE (ECDH-ES/A256GCM) encrypts to a real cert key and round-trips

    python scripts/smoke_card_checkout.py [--cards path/to/cards.txt]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / ".tmp" / "smoke-cards.db"
DB.parent.mkdir(parents=True, exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()

os.environ["DATABASE_URL"] = "sqlite:///./.tmp/smoke-cards.db"
os.environ["SIGNUP_CLIENT"] = "mock"
os.environ["CHECKOUT_ENABLED"] = "true"
os.environ["CHECKOUT_MAX_CARD_TRIES"] = "2"
os.environ["SIGNUP_CREDENTIALS_KEY"] = "smoke-credentials-key"
os.environ["PROXIES"] = ""
os.environ["DEFAULT_INTO_POOL"] = "true"

import asyncio  # noqa: E402

from app.cards import store as cards  # noqa: E402
from app.cards.ptt import (  # noqa: E402
    build_platform_trust_token,
    decrypt_jwe,
    jwe_ecdh_es,
)
from app.db import init_db  # noqa: E402
from app.signup import machine, store  # noqa: E402
from app.signup.import_parse import MailCreds  # noqa: E402

FAILS = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global FAILS
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAILS += 1


async def new_signup(email: str) -> str:
    iid, _ = store.upsert_from_creds(
        MailCreds(email=email, password="p", client_id="cid", refresh_token="rt"),
        region="US",
        into_pool=True,
    )
    return iid


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default=str(Path.home() / "Downloads" / "50 (1).txt"))
    args = ap.parse_args()

    init_db()

    print("\n== 1. card import ==")
    src = Path(args.cards)
    if src.is_file():
        text = src.read_text(encoding="utf-8", errors="ignore")
        res = cards.import_cards(text, source=src.name)
        print(f"  file: {src.name} · 新增 {res['added']} 重复 {res['duplicate']} 无效 {res['invalid']}")
        check("cards imported", res["added"] > 0, f"added={res['added']}")
        first = cards.list_cards(page=1, page_size=3)["items"][0]
        check("masked PAN returned", "••••" in first["masked"], first["masked"])
        check("no full PAN in list", "pan" not in first and "cvv" not in first)
        full = cards.get_card(first["id"], reveal=True)
        check("reveal returns PAN", bool((full or {}).get("secret", {}).get("pan")))
    else:
        print(f"  ! 未找到 {src}，改用内置样例")
        res = cards.import_cards(
            "4111111111111111|09/30|123|Test User|1 Main St|Austin|TX|73301|US|5551234|t@example.com|1.2.3.4|Live",
            source="inline",
        )
        check("inline card imported", res["added"] == 1)

    print("\n== 2. registration → checkout → pooled ==")
    iid = await new_signup("smoke-card-1@example.com")
    out = await machine.run_until_blocked(iid)
    item = store.get_item(iid) or {}
    check("signup pooled", item.get("status") == "pooled", f"status={item.get('status')} out={out}")
    used = [c for c in cards.list_cards(page=1, page_size=200)["items"] if c["used_by_item"] == iid]
    check("one card marked used", len(used) == 1 and used[0]["status"] == "used",
          used[0]["masked"] if used else "-")
    logs = [r for r in store.list_logs(after=0, limit=500) if r["item_id"] == iid]
    joined = " | ".join(r["message"] for r in logs)
    check("checkout ran", "checkout SUCCESS" in joined)
    check("accept-tos + wake ran", "accept-tos OK" in joined and "vm/wake OK" in joined)
    check("auto password set", "自动设密成功" in joined)
    from app.pool import store as pool_store

    pool = pool_store.list_raw()
    check("pool row carries password", bool(pool and (pool[0].get("password_enc") or "")), f"rows={len(pool)}")
    check("no PAN in task logs", "4111" not in joined and "1111111111111111" not in joined)
    pub = store.row_to_public(item)
    check("item carries masked card", bool(pub.get("card_masked")), str(pub.get("card_masked")))

    print("\n== 3. decline path ==")
    bad = cards.import_cards(
        "4000000000000000|12/30|999|Decline Test|2 Main St|Austin|TX|73301|US|||Live",
        source="decline",
    )
    check("decline card added", bad["added"] == 1)
    # make sure only the declining card is available
    for c in cards.list_cards(page=1, page_size=200)["items"]:
        if c["last4"] != "0000" and c["status"] == "available":
            cards.set_enabled([c["id"]], False)
    iid2 = await new_signup("smoke-card-2@example.com")
    out2 = await machine.run_until_blocked(iid2)
    item2 = store.get_item(iid2) or {}
    check("item awaits card after decline", item2.get("status") == "awaiting_card",
          f"status={item2.get('status')}")
    dead = [c for c in cards.list_cards(page=1, page_size=200)["items"] if c["last4"] == "0000"]
    check("declined card marked dead", bool(dead) and dead[0]["status"] == "dead",
          dead[0]["status"] if dead else "-")

    print("\n== 4. empty pool ==")
    cards.delete_cards([c["id"] for c in cards.list_cards(page=1, page_size=200)["items"]])
    iid3 = await new_signup("smoke-card-3@example.com")
    out3 = await machine.run_until_blocked(iid3)
    item3 = store.get_item(iid3) or {}
    check("no card → awaiting_card", item3.get("status") == "awaiting_card",
          f"status={item3.get('status')}")
    check("needs card", "card" in (out3.get("needs") or []))

    print("\n== 5. PTT JWE ==")
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ptt-test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    chain = [
        __import__("base64").b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    ]
    ptt_key = {"id": "ptt-42", "trustChain": chain, "platformTrustToken": "", "devExternal": False}
    card = {
        "pan": "4111111111111111",
        "exp_month": 9,
        "exp_year": 2030,
        "cvv": "123",
        "holder": "Test User",
        "zip": "73301",
        "country": "US",
        "brand": "Visa",
    }
    token = build_platform_trust_token(ptt_key, card, mode="jwe", device_id="dev-1")
    check("token is base64 JSON", token[:1] != "{" and len(token) > 100)
    import base64 as b64
    import json as _json

    outer = _json.loads(b64.b64decode(token))
    check("outer payload is JWE compact", outer["payload"].count(".") == 4)
    check("kid carried", outer.get("key_id") == "ptt-42")
    inner = _json.loads(decrypt_jwe(outer["payload"], key))
    check("PAN sealed inside JWE", inner["credit_card"]["number"] == "4111111111111111")
    check("CSC sealed inside JWE", inner["csc"] == "123")
    check("PAN never in outer JSON", "4111111111111111" not in token)

    print(f"\n{'ALL CHECKS PASSED' if not FAILS else f'{FAILS} CHECK(S) FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
