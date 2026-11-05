import asyncio, json, sys
sys.path.insert(0, r"C:\Users\fahai\muse-proxy")
from app.config import reload_settings
from app.signup.protocol import (
    MuseSession, get_muse_client, mask_proxy_url, MuseProtocolError
)

s = reload_settings()
print("signup_client=", s.signup_client)
print("signup_muse_base=", s.signup_muse_base)
first = ""
for line in (s.proxies or "").splitlines():
    if line.strip() and not line.strip().startswith("#"):
        first = line.strip()
        break
print("proxies_masked=", mask_proxy_url(first))
print("proxies_configured=", bool(first))

client = get_muse_client()
print("client_type=", type(client).__name__)
print("client_base=", getattr(client, "base", None))
print("client_proxy_masked=", mask_proxy_url(getattr(client, "proxy", None)))

async def main():
    sess = MuseSession()
    try:
        data = await client.restart(sess, resume=False)
        print("RESTART_OK")
        print("keys=", sorted(list(data.keys()))[:30])
        print("csrf=", bool(sess.csrf_token), "len=", len(sess.csrf_token or ""))
        print("pe=", bool(sess.password_encryption))
        print("fp=", (sess.fingerprint or {}).get("profile_id"))
        print("cookies=", list(sess.cookies.keys()))
        print("proxy_session=", mask_proxy_url(sess.proxy_url))
        try:
            otp = await client.send_otp(sess, "muse_proxy_probe_no_mail@example.com", "US")
            print("SEND_OTP_OK", json.dumps({k: otp.get(k) for k in list(otp)[:12]}, ensure_ascii=False)[:500])
        except MuseProtocolError as e:
            print("SEND_OTP_ERR", e.status_code, e.message)
            if isinstance(e.detail, dict):
                safe = {k: e.detail.get(k) for k in ("status_code","headers","proxy","proxy_used","url","error","raw","flow","message")}
                print("SEND_OTP_DETAIL", json.dumps(safe, ensure_ascii=False)[:800])
    except MuseProtocolError as e:
        print("RESTART_ERR", e.status_code, e.message)
        if isinstance(e.detail, dict):
            safe = {k: e.detail.get(k) for k in ("status_code","headers","proxy","proxy_used","url","error","raw")}
            print("RESTART_DETAIL", json.dumps(safe, ensure_ascii=False)[:1000])
    except Exception as e:
        print("RESTART_EXCEPTION", type(e).__name__, str(e)[:500])

asyncio.run(main())
