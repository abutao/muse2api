"""HTTP-level smoke for /api/cards (login → import → list → patch → export → batch)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB = ROOT / ".tmp" / "smoke-cards-api.db"
DB.parent.mkdir(parents=True, exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    p = Path(str(DB) + suffix)
    if p.exists():
        p.unlink()

os.environ["DATABASE_URL"] = "sqlite:///./.tmp/smoke-cards-api.db"
os.environ["SIGNUP_CLIENT"] = "mock"
os.environ["CHECKOUT_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

FAILS = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global FAILS
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))
    if not ok:
        FAILS += 1


def main() -> int:
    c = TestClient(app)
    r = c.get("/api/cards")
    check("unauthenticated → 401", r.status_code == 401, str(r.status_code))

    r = c.post("/api/login", json={"user": "admin", "password": "change-me"})
    check("login ok", r.status_code == 200, r.text[:120])

    text = "4111111111111111|09/30|123|Test User|1 Main St|Austin|TX|73301|US|555|t@x.com|1.2.3.4|Live"
    r = c.post("/api/cards/import", json={"text": text, "source": "smoke"})
    check("import ok", r.status_code == 200 and r.json().get("added") == 1, r.text[:160])

    r = c.get("/api/cards?page=1&size=10")
    body = r.json()
    check("list ok", r.status_code == 200 and body["total"] == 1)
    item = body["items"][0]
    check("masked in list", item["masked"].endswith("1111") and "pan" not in item, item["masked"])
    card_id = item["id"]

    r = c.get(f"/api/cards/{card_id}?reveal=1")
    check("reveal ok", r.status_code == 200 and r.json()["card"]["secret"]["pan"].startswith("4111"))

    r = c.patch(f"/api/cards/{card_id}", json={"note": "smoke-note", "zip": "99999"})
    check("patch ok", r.status_code == 200 and r.json()["card"]["note"] == "smoke-note")

    r = c.get("/api/cards/export?full=0")
    check("export masked", r.status_code == 200 and "411111xxxxxx1111" in r.text, r.text[:80])
    r = c.get("/api/cards/export?full=1")
    check("export full", "4111111111111111" in r.text)

    r = c.post("/api/cards/batch", json={"ids": [card_id], "action": "disable"})
    check("batch disable", r.status_code == 200 and r.json()["affected"] == 1)
    r = c.post("/api/cards/batch", json={"ids": [card_id], "action": "reset"})
    check("batch reset", r.status_code == 200 and r.json()["affected"] == 1)

    r = c.post("/api/cards/import", json={"path": str(Path.home() / "Downloads" / "50 (1).txt")})
    check("server-side file import", r.status_code == 200 and r.json()["added"] >= 40, r.text[:120])

    r = c.get("/api/cards?status=available")
    check("filter available", r.status_code == 200 and r.json()["total"] >= 40)

    r = c.delete(f"/api/cards/{card_id}")
    check("delete ok", r.status_code == 200 and r.json()["affected"] == 1)

    print("\n== card pay retry (awaiting_card → pooled) ==")
    from app.signup import store as sstore
    from app.signup.import_parse import MailCreds

    iid, _ = sstore.upsert_from_creds(
        MailCreds(email="api-pay@example.com", password="p", client_id="c", refresh_token="r"),
        region="US",
        into_pool=True,
    )
    sstore.save_session_meta(
        iid,
        {
            "hatch_sess": "mock_hatch_sess",
            "cookies": {"hatch_sess": "mock_hatch_sess"},
            "csrf_token": "mock_csrf",
            "waterfall_id": "mock_wf",
            "fingerprint": {},
        },
    )
    sstore.update_item(
        iid, status="awaiting_card", cookie_blob="hatch_sess=mock_hatch_sess", message="等待卡"
    )
    c.post("/api/cards/import", json={"text": "5500005555555559|09/31|321|Pay Test|3 Main|Austin|TX|73301|US||||Live"})
    r = c.post(f"/api/cards/pay/{iid}", json={})
    body = r.json()
    item = sstore.get_item(iid) or {}
    check("card pay ok", r.status_code == 200 and body.get("ok"), r.text[:200])
    check("item pooled after pay", item.get("status") == "pooled", f"status={item.get('status')}")

    print(f"\n{'ALL API CHECKS PASSED' if not FAILS else f'{FAILS} CHECK(S) FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
