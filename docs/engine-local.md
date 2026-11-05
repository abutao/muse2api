# Local muse_engine (plan §12)

- `ENGINE_BACKEND=local` (default): OpenAI `/v1/models`、`/v1/chat/completions`、`/v1/images/*`、`/v1/videos*`、`/v1/media/{name}` 全部走内嵌 Chromium+CDP（muse.ai），**不再** `_fwd` 到 19080。
- 账号 Cookie 密文在 SQLite `pool_accounts.cookie_enc`（Fernet）。注册成功只入本地池。
- 异步图任务：`POST /v1/images/tasks` → 轮询 `GET /v1/images/tasks/{id}`；视频：`POST /v1/videos` → `GET /v1/videos/{id}`。成品落盘 `data/media/`，由本机 `/v1/media/` 直出。
- 号池空：接口返回 502，`code=empty_pool`，文案引导到管理台「号池」导入含 `hatch_sess` 的 Cookie，或走导号/补号入池。
- `UPSTREAM_BASE_URL` 空 = 无 HTTP 反代。仅双跑回滚时设 `ENGINE_BACKEND=upstream` + URL。
- CDP 默认 `19211`。Chromium 自动探测（Windows 含 Edge）。
- 无卡池 / checkout。


## P4 本地号池 keepalive

- 管理台 / API：`POST /api/refills/keepalive` → `jobs_keepalive` 对本机号池逐号：
  - `keepalive` / `renew`：`GET https://muse.ai/api/session` + 按需 `POST /api/hatch/vm/wake`
  - `quota`：CDP 读额度
- **只写入真实 Set-Cookie `expires`**（`cookies_exp`）；`account_expiry` **禁止** `now+48h` 虚增。无真实 expires → `expires_at=null` + `expires_estimated`。
- `PROXY_SCHEDULED_KEEPALIVE=true` 时进程内每小时自动跑一轮。

## P5 拆除 UPSTREAM/_fwd/19080

- `/v1/*` 仅本地 muse_engine；`app.proxy.forward` 恒 503。
- 设置页不再暴露上游地址/Key；保存时强制清空 `UPSTREAM_*`、`ENGINE_BACKEND=local`。
- `.env` 默认 `UPSTREAM_BASE_URL=` / `UPSTREAM_API_KEY=`。
