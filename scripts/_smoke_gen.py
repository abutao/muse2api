import json, time, urllib.request, http.cookiejar
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def req(method, path, data=None, headers=None, token=None):
    h = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request("http://127.0.0.1:8090"+path, data=body, headers=h, method=method)
    try:
        with opener.open(r, timeout=60) as resp:
            raw = resp.read()
            return resp.status, raw.decode("utf-8", "replace")[:4000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:4000]

print("LOGIN", req("POST", "/api/login", {"user":"admin","password":"change-me"}))
st, body = req("POST", "/api/generate/token", {})
print("TOKEN", st, body[:300])
tok = json.loads(body)["token"]
st, body = req("GET", "/api/generate/models")
print("MODELS", st, body[:500])
# image
st, body = req("POST", "/v1/images/tasks", {"model":"muse-image","prompt":"smoke t2i ui-path","resolution":"1K","aspect_ratio":"1:1"}, token=tok)
print("IMG CREATE", st, body)
j = json.loads(body); tid=j.get("id")
time.sleep(0.5)
st, body = req("GET", f"/v1/images/tasks/{tid}", token=tok)
print("IMG POLL", st, body[:800])
jp = json.loads(body)
urls = []
if isinstance(jp.get("result"), dict):
    for it in (jp["result"].get("data") or []):
        if isinstance(it, dict) and it.get("url"): urls.append(it["url"])
print("IMG URLS", urls, "status", jp.get("status"))
# video
st, body = req("POST", "/v1/videos", {"model":"muse-video","prompt":"smoke t2v ui-path","duration":5,"resolution":"720p","aspect_ratio":"16:9"}, token=tok)
print("VID CREATE", st, body)
j = json.loads(body); vid=j.get("id")
time.sleep(0.5)
st, body = req("GET", f"/v1/videos/{vid}", token=tok)
print("VID POLL", st, body[:800])
jv = json.loads(body)
print("VID URL", jv.get("url"), "status", jv.get("status"))
# gallery
st, body = req("GET", "/api/generate/gallery?limit=5")
print("GALLERY", st, body[:1000])
# records
st, body = req("GET", "/api/records?page_size=8")
print("RECORDS", st, body[:1200])
