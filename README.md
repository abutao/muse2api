# muse-proxy

OpenAI 兼容反代网关 + 小AD 同构七页管理台，上游为本机 muse2api。

> 权威方案：`/workspace/muse-proxy-full-plan.md`  
> 当前进度：**M8 完成**（refills + overview + settings 收口；七页可用）

> 信息架构：侧栏为 **总览 / 生图生视频 / 生成记录 / 号池 / 卡池 / 补号系统 / 系统设置**（已去掉「子号激活」；**补号系统 = Muse 注册 + 号池保活**，注册后自动用**卡池**跑 $1 年龄验证 checkout）。设置页含代理列表、结果模式与本地 R2 账号池。前端文案中文。


## 要求

- Python 3.10+
- 默认端口 `8090`（勿占用 Firefly `:8080` / muse2api `:19080`）

## 本地启动

```bash
cd /workspace/muse-proxy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env
# 编辑 .env：ADMIN_PASSWORD、MUSE_PROXY_KEY、UPSTREAM_API_KEY、UPSTREAM_BASE_URL
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

管理台：http://127.0.0.1:8090/login.html（默认 `admin` / `.env` 中密码）

## 对外 `/v1` 反代

鉴权：`Authorization: Bearer <env Key 或设置页创建的 DB Key>`（env：`MUSE_PROXY_KEY` / `PROXY_API_KEYS`；DB Key 哈希存库）  
回源：替换为 `Authorization: Bearer <UPSTREAM_API_KEY>`，不转发客户端 Cookie。  
媒体：`MEDIA_AUTH_REQUIRED`（默认 true）控制 `GET /v1/media/{name}` 是否要求 Bearer；`REWRITE_MEDIA_URLS`（默认 true）把响应 JSON 里上游 media URL 改写为 `PUBLIC_BASE/v1/media/...`。

```bash
export KEY=mprox_dev_test_key   # 与 .env 中 MUSE_PROXY_KEY 一致

# 模型
curl -sS http://127.0.0.1:8090/v1/models -H "Authorization: Bearer $KEY"

# 流式对话
curl -N http://127.0.0.1:8090/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-spark","stream":true,"messages":[{"role":"user","content":"hi"}]}'

# 图片生成（JSON 内 url 应指向本机 :8090/v1/media/...）
curl -sS http://127.0.0.1:8090/v1/images/generations \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-image","prompt":"a cat"}'

# 图片编辑（multipart）
curl -sS http://127.0.0.1:8090/v1/images/edits \
  -H "Authorization: Bearer $KEY" \
  -F model=muse-image -F prompt=edit -F image=@/tmp/tiny.png

# 异步图片任务
curl -sS -X POST http://127.0.0.1:8090/v1/images/tasks \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-image","prompt":"async"}'
# GET /v1/images/tasks/{id}

# 视频
curl -sS -X POST http://127.0.0.1:8090/v1/videos \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"muse-video","prompt":"wave"}'
# GET /v1/videos/{id}  （别名：/v1/videos/generations、/v1/videos/generations/{id}）

# 媒体回源（默认要求 Bearer）
curl -sS -o /tmp/out.png -w "%{http_code}\n" \
  http://127.0.0.1:8090/v1/media/mock-demo.png \
  -H "Authorization: Bearer $KEY"
```

无 Key → 401；上游不可达 → 502 `upstream_unreachable`；上游超时 → 504；并发满 → 429 `proxy_busy`；非法 media 名 → 400。

每次 `/v1` 调用写入 `call_logs`（时间、path、model、status、耗时、错误摘要、Key 前缀）；不存完整密钥与大字段（image/base64 等）。

### 本机无 muse2api 时用 mock

```bash
# 终端 A：mock 上游（key = mock-upstream-key），覆盖 chat/images/videos/media
python scripts/mock_upstream.py

# .env（勿提交真实生产 Key）
# UPSTREAM_BASE_URL=http://127.0.0.1:19080
# UPSTREAM_API_KEY=mock-upstream-key
# MUSE_PROXY_KEY=mprox_dev_test_key
```

真实 muse2api 在 `:19080` 时，把 `UPSTREAM_API_KEY` 设为上游 Key 即可，无需 mock。

## M4：管理台试调 `#generate`

1. 打开 http://127.0.0.1:8090/login.html ，用 `.env` 中 `ADMIN_USER` / `ADMIN_PASSWORD` 登录  
2. 侧栏进入「生图生视频」或直接访问 http://127.0.0.1:8090/#generate  
3. 页面会 `POST /api/generate/token` 拿短时 Key，再带 Bearer 调 `/v1/chat/completions`、`/v1/images/generations`、`/v1/videos`  
4. 右侧画廊来自 `GET /api/generate/gallery`（`call_logs` 最近成功 media）；`<img>` 可用管理员 Cookie 拉 `/v1/media`（无需 Bearer）  
5. 关闭试调：`CONSOLE_GENERATE_ENABLED=false` 或 `GENERATE_PLAYGROUND=0` 后重启

```bash
# curl 拿试调 Key（先登录拿 cookie）
curl -sS -c /tmp/mp.txt -X POST http://127.0.0.1:8090/api/login \
  -H 'Content-Type: application/json' \
  -d '{"user":"admin","password":"change-me"}'
TOKEN=$(curl -sS -b /tmp/mp.txt -X POST http://127.0.0.1:8090/api/generate/token \
  -H 'Content-Type: application/json' -d '{}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -sS http://127.0.0.1:8090/v1/images/generations \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"model":"muse-image","prompt":"playground"}'
```


## M5：生成记录 + 多对外 Key

### 管理台

- `#records`：`call_logs` 筛选（状态/类型/关键词）、分页、详情 modal、清理（失败 / N 天前 / 勾选 / 清空；跳过进行中）
- `#settings` 底部「对外密钥」：创建（明文只显示一次）、启停、额度/日限、模型白名单、编辑/轮换/删除

### 管理 API（需管理员 Cookie）

```bash
# 登录拿 cookie
curl -sS -c /tmp/mp.txt -X POST http://127.0.0.1:8090/api/login   -H 'Content-Type: application/json'   -d '{"user":"admin","password":"change-me"}'

curl -sS -b /tmp/mp.txt 'http://127.0.0.1:8090/api/records?status=all&page=1'
curl -sS -b /tmp/mp.txt http://127.0.0.1:8090/api/records/1
curl -sS -b /tmp/mp.txt -X POST http://127.0.0.1:8090/api/records/delete   -H 'Content-Type: application/json' -d '{"mode":"failed"}'

curl -sS -b /tmp/mp.txt http://127.0.0.1:8090/api/settings/summary
curl -sS -b /tmp/mp.txt http://127.0.0.1:8090/api/settings/keys
# 创建 Key（响应里 item.token 仅此一次）
curl -sS -b /tmp/mp.txt -X POST http://127.0.0.1:8090/api/settings/keys   -H 'Content-Type: application/json'   -d '{"name":"demo","quota_total":100,"daily_limit":20,"models_allow":"muse-spark,muse-image"}'
# PATCH /api/settings/keys/{id} · POST .../rotate · DELETE .../{id}
```

### `/v1` 扣费与限流

- **env Key / console 试调 Key**：不扣费
- **DB user Key**：成功 2xx 后 `quota_used` / `daily_used` +1（`CHARGE_ON_UPSTREAM_ERROR=true` 时上游出错也扣）
- 超额 → **429** `quota_exceeded` / `daily_limit_exceeded`；停用 → 403；模型不在白名单 → 403
- `GET /v1/media/*` 不扣费

```bash
# 用设置页刚创建的 Key（勿把真实 token 写入仓库）
export KEY='mprox_u_…'   # 创建响应里的 item.token
curl -sS http://127.0.0.1:8090/v1/models -H "Authorization: Bearer $KEY"
# 把 quota_total 设为 2 后连调三次，第三次应 429
```

库表：`api_keys`（`token_hash` = SHA-256(session_secret + token)，仅存 `token_prefix`）；`call_logs.key_id` 关联。


## M6 号池（accounts）

实时代理 muse2api `/admin/accounts*`（`UPSTREAM_BASE` + `UPSTREAM_API_KEY`）。**本库不存 Cookie 权威副本**；列表/导出只含元数据（`cookie_count` 等）。

| 管理 API | 上游 |
|----------|------|
| `GET /api/accounts` | `GET /admin/accounts`（本侧分页/筛选） |
| `POST /api/accounts/import` | `POST /admin/accounts`（`name----cookie` / `label \| cookie`） |
| `PATCH/DELETE /api/accounts/{id}` | 同路径 |
| `POST /api/accounts/{id}/check` | `POST /admin/accounts/{id}/test` |
| `POST /api/accounts/batch` | 循环 enable/disable/delete/check |
| `GET /api/accounts/export` | 元数据 CSV，无 Cookie |
| `POST /api/accounts/{id}/cookies` | 写入上游；响应脱敏 |

SPA `#accounts`：表格、分页、导入 modal、行操作、批量。`scripts/mock_upstream.py` 含精简 `/admin/accounts*` 供无真上游验收。

本地验收（mock + 代理）：

```bash
# 需已启 mock :19080 与 uvicorn :8090
curl -c /tmp/cj -X POST http://127.0.0.1:8090/api/login \
  -H 'Content-Type: application/json' -d '{"user":"admin","password":"change-me"}'
curl -b /tmp/cj -X POST http://127.0.0.1:8090/api/accounts/import \
  -H 'Content-Type: application/json' \
  -d '{"text":"demo----hatch_sess=x; a=1"}'
curl -b /tmp/cj 'http://127.0.0.1:8090/api/accounts?page=1'
```


## M7 导号 / 注册（subs · 无卡）

状态机（**全协议**，对齐 Muse native-auth；无卡）：`pending → restart/send-otp → awaiting_otp → awaiting_profile → awaiting_external_payment → resume(payment_ref) → accept-tos / wake → pooled`；可选 Recovery 设密 / 密码登录 / 兑码。

- 表：`signup_items` / `signup_task_logs`（**无** card/cvv/pan 列）
- 凭据 Fernet 加密（`SIGNUP_CREDENTIALS_KEY` 或回退 `SESSION_SECRET`）
- **指纹**：`app/signup/fingerprint.py` 按真浏览器 HAR（Chrome/Edge 156）生成 UA / `sec-ch-ua*` / hatch 头；`HttpMuseClient` 与 mock 均挂载
- **资料**：名/姓/生日缺省时服务端随机（成人 DOB）；设密空密码时随机强密码写入 `secret_enc`（不落前端日志）
- `SIGNUP_CLIENT=mock`（本地）或 `http` + `SIGNUP_MUSE_BASE`（真站全协议）
- 卡字段提交 → **400** `card_fields_forbidden`
- Cookie 逃生舱：`POST /api/subs/{id}/cookie` → 上游 accounts
- SPA `#refills`：导入 / 批量 / 资料 / OTP / Resume / Recovery / 收件（HTML 消毒）

```bash
# 验收摘要
curl -b cj -X POST …/api/subs/import -d '{"text":"a@b.com----p----cid----rt----aux@b.com----ap"}'
curl -b cj -X POST …/api/subs/batch -d '{"ids":["s_…"],"action":"activate"}'
# → awaiting_profile 后
curl -b cj -X POST …/api/refills/s_…/profile -d '{}'   # 空 body → 随机名+生日
# → awaiting_external_payment
curl -b cj -X POST …/api/subs/s_…/resume -d '{"payment_ref":"ext_pay_xxx"}'
# → pooled；带 cvv/card_number 的请求返回 400
```

## 明确不做

- 上游 muse2api 的代扣/发卡对接
- 真实密钥写入文档或仓库（`.env` 已 gitignore）
- 部署到 82.29

> 卡池与 checkout 现已实现（M9）：卡数据只经 `/api/cards` 与 checkout 客户端，注册/补号 API 仍拒绝卡字段。

## 目录

```text
muse-proxy/
├── app/
│   ├── main.py
│   ├── config.py / db.py / call_log.py / media_rewrite.py
│   ├── auth_admin.py / auth_api.py
│   ├── proxy.py / upstream_admin.py
│   ├── signup/              # crypto / machine / protocol(mock|http)
│   └── routers/             # … accounts(M6) subs(M7) …
├── scripts/mock_upstream.py
├── static/
├── data/
├── .env.example
└── requirements.txt
```

## 里程碑

| 里程碑 | 状态 |
|--------|------|
| M1 脚手架 + 登录 + 七页壳 | 完成 |
| M2 models + chat SSE | 完成 |
| M3 images/videos/media + call_logs | 完成 |
| M4 generate 试调页 + console token | 完成 |
| M5 | **已完成** records + api_keys |
| M6 | **已完成** accounts 代理上游号池 |
| M7 | **已完成** subs 无卡注册状态机 + resume |
| M8 | **已完成** 补号 / 总览 / 设置收口 |
| M9 | **已完成** 卡池 + $1 年龄验证 checkout + 卡管理页 |


## M8 补号 / 总览 / 设置收口

### 七页一览

| Hash | 能力 |
|------|------|
| `#overview` | 今日/累计成功率、按类型、序列（`range=6h|24h|7d|30d`）、上游 health/ready、号池 stats、Key 启用数、近期失败 |
| `#generate` | 试调（M4） |
| `#records` | call_logs（M5） |
| `#accounts` | 上游号池代理（M6） |
| `#subs` | 无卡注册激活（M7） |
| `#refills` | 邮箱物料 + keepalive 队列（test/relogin/quota/keepalive） |
| `#settings` | 上游遮罩 Key、超时并发、媒体、试调、保留天数、管理员密码、测试连通、对外 Key |

### 补号 `#refills`

- **邮箱 Tab**：导入 6/4 段物料 → 本库 `refill_items`（任务态）；`mode=signup` 可交给 subs 状态机；`keepalive_only` 仅存物料
- **保活 Tab**：全池/勾选 → `POST /api/refills/keepalive`；经 `upstream_admin` 调 mock/真上游；进度写 `keepalive_jobs` / `keepalive_logs`
- **硬约束**：无卡字段；本库不存 Cookie 权威副本

```bash
# 登录后
curl -b /tmp/mp.txt -X POST http://127.0.0.1:8090/api/refills/import \
  -H 'Content-Type: application/json' \
  -d '{"text":"a@b.com----p----cid----rt----aux@b.com----ap","mode":"keepalive_only"}'

# 先在号池或上游准备账号，再跑 keepalive
curl -b /tmp/mp.txt -X POST http://127.0.0.1:8090/api/refills/keepalive \
  -H 'Content-Type: application/json' -d '{"action":"keepalive","scope":"all"}'
curl -b /tmp/mp.txt http://127.0.0.1:8090/api/refills/task
```

### 总览 `#overview`

```bash
curl -b /tmp/mp.txt 'http://127.0.0.1:8090/api/overview?range=24h'
curl -b /tmp/mp.txt http://127.0.0.1:8090/api/metrics
curl -b /tmp/mp.txt 'http://127.0.0.1:8090/api/stats/series?range=6h'
```

### 设置 `#settings`

- `GET/PATCH /api/settings`：写入 SQLite `app_settings` 覆盖项（**不**把真实密钥写进仓库）
- 上游 Key GET 仅遮罩；PATCH 传 `****` / 空则不改
- `POST /api/settings/test-upstream`：healthz + admin/status

```bash
curl -b /tmp/mp.txt -X POST http://127.0.0.1:8090/api/settings/test-upstream -d '{}'
curl -b /tmp/mp.txt -X PATCH http://127.0.0.1:8090/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"max_concurrency":8,"console_generate_enabled":true}'
```

### M1–M8 启动总览

```bash
cd /workspace/muse-proxy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env   # 填 ADMIN_PASSWORD / MUSE_PROXY_KEY / UPSTREAM_*（示例值即可）

# 终端 A：无真 muse2api 时启 mock（:19080，key=mock-upstream-key）
python scripts/mock_upstream.py

# 终端 B：代理 + 管理台（:8090）
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

| 验收项 | 命令 / 操作 |
|--------|-------------|
| 健康 | `curl :8090/healthz`、`:19080/healthz` |
| 登录七页 | http://127.0.0.1:8090/login.html → 侧栏七页非空壳 |
| 反代 | `Bearer $MUSE_PROXY_KEY` → `/v1/models` |
| overview 有数字 | `#overview` 或 `GET /api/overview` |
| keepalive | `#refills` 保活 Tab 或上文 curl，mock 上 `ok_count≥1` |
| settings 连通 | 「测试连通」或 `POST /api/settings/test-upstream` |

**不做**：发卡/卡池、部署到 82.29、把真实密钥写入 git。


## M9 卡池 + checkout（$1 年龄验证）

注册主路径到 `register-completion` 后，不再停在「待外部支付」：服务端会自动从卡池取一张
可用卡跑完 checkout，再继续 `accept-tos → vm/wake → 入池`。

```text
register-completion → Meta bootstrap → checkout/init → ptt/encryption-key
  → checkout/credential（PAN/CVV 封进 platformTrustToken JWE）
  → checkout/finalize(SUCCESS) → accept-tos → vm/wake → pooled
```

### 卡池页 `#cards`

| 能力 | 说明 |
|------|------|
| 导入卡 | 文件 / 粘贴 / 服务器本地路径；`卡号|MM/YY|CVV|持卡人|地址|城市|州|ZIP|国家|电话|邮箱|IP|状态` |
| 短格式 | `卡号|MM/YY|CVV|ZIP|US`、Tab / `----` / 逗号分隔亦可 |
| 状态 | `available → in_use → used`；软失败 `failed`（可「恢复」重试）；硬拒 `dead`；手动 `disabled` |
| 掩码 | 列表只回 `Visa •••• 1111`、有效期、ZIP；PAN/CVV 以 Fernet 密文存 `cards.data_enc` |
| 批量 | 恢复 / 启用 / 停用 / 删除 / 导出（默认掩码，`full=1` 导出明文）|

补号页 `#refills` 行内新增「卡池支付 / 换卡重试」按钮（`data-a=cardpay`），可指定某张卡。

### 管理 API（需管理员 Cookie）

```bash
curl -c /tmp/cj -X POST http://127.0.0.1:8090/api/login \
  -H 'Content-Type: application/json' -d '{"user":"admin","password":"change-me"}'

# 导入（文本 / 本地文件）
curl -b /tmp/cj -X POST http://127.0.0.1:8090/api/cards/import \
  -H 'Content-Type: application/json' \
  -d '{"path":"C:\\Users\\you\\Downloads\\50 (1).txt","source":"50"}'

curl -b /tmp/cj 'http://127.0.0.1:8090/api/cards?status=available&page=1&size=20'
curl -b /tmp/cj 'http://127.0.0.1:8090/api/cards/c_xxx?reveal=1'      # 完整卡（仅管理员本机）
curl -b /tmp/cj -X PATCH http://127.0.0.1:8090/api/cards/c_xxx \
  -H 'Content-Type: application/json' -d '{"note":"备用","status":"available"}'
curl -b /tmp/cj -X POST http://127.0.0.1:8090/api/cards/batch \
  -H 'Content-Type: application/json' -d '{"ids":["c_xxx"],"action":"reset"}'
curl -b /tmp/cj 'http://127.0.0.1:8090/api/cards/export?full=0'

# 任务卡在 awaiting_card 时，用卡池继续支付（可指定卡）
curl -b /tmp/cj -X POST http://127.0.0.1:8090/api/cards/pay/s_xxx \
  -H 'Content-Type: application/json' -d '{"card_id":"c_xxx"}'
```

### platformTrustToken

- `/api/ptt/encryption-key` 返回 `trustChain`（证书链）→ 取链上 EC 叶证书公钥做
  **ECDH-ES + A256GCM** JWE（RFC 7518 concat KDF），外层再包
  `base64(json({"payload": jwe, "signatures": []}))`，即抓包里的 `platformTrustToken`。
- 明文载荷：`{"credit_card": {number, exp_month, exp_year, cardholder_name, zip, country, brand}, "csc", "device_id", "issued_at"}`。
- 卡号/CVV 不出现在 `checkout/credential` 的 JSON 里（只有 `cardFirst6`/`cardLast4`/`expiry`/`zip`/`countryCode`）。
- 若目标返回的 `trustChain` 无可解析 EC 公钥，会抛 `ptt_unavailable`（任务日志带 `ptt` 摘要），
  此时可临时 `CHECKOUT_PTT_MODE=direct` 只做链路联调（明文 base64，勿用于生产）。

### 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `CHECKOUT_ENABLED` | `true` | `false` 时退回旧的「待外部支付 + payment_ref」路径 |
| `CHECKOUT_AMOUNT` / `CHECKOUT_CURRENCY` | `1.00` / `USD` | 年龄验证临时授权金额 |
| `CHECKOUT_PRODUCT_ID` / `CHECKOUT_RECEIVER_ID` | `766358168544755` / `352501280694489` | 抓包观测常量 |
| `CHECKOUT_MAX_CARD_TRIES` | `3` | 每单最多换几张卡 |
| `CHECKOUT_PTT_MODE` | `jwe` | `jwe` / `direct` |

### 离线验收

```bash
# 注册 → 卡池 checkout → 入池（mock 客户端，临时库）
python scripts/smoke_card_checkout.py --cards "C:\Users\you\Downloads\50 (1).txt"

# /api/cards 全套 HTTP 验收（登录/导入/掩码/改/导出/批量）
python scripts/smoke_cards_api.py

# #PWD_BROWSER:5 加密器（真实 libsodium sealed box + AES-GCM）
python scripts/verify_pwd_browser.py
```

## M10 注册自动化：物料取码 → 卡池 $1 → 自动设密 → 入池

参考 firefly-system（`internal/mail`）的邮件管线与 muse2api 的协议实现，注册现在可以全自动跑完：

```text
导入邮箱物料(6段/4段)
  → 待注册队列
  → restart → send-otp
  → 邮箱自动取 OTP（IMAP XOAUTH2 → IMAP 密码 → Graph）
  → confirm-otp → register-completion → Meta bootstrap
  → 卡池取卡 → checkout/init → ptt/encryption-key → credential(JWE) → finalize SUCCESS
  → accept-tos → vm/wake
  → restart → recovery-start → 邮箱自动取恢复码 → recovery-confirm-code
  → recovery-complete(随机强密码) → 密码 Fernet 加密保存
  → 推入号池（号池行带加密登录密码 + Cookie + 指纹）
```

### 邮箱物料
- 支持 `邮箱----密码----ClientID----RefreshToken[----辅邮----辅密]`、`|` / Tab 分隔
- 取信顺序：IMAP(XOAUTH2) → IMAP(密码) → Graph；refresh_token 按 firefly 的多端点重试
  （`login.microsoftonline.com/consumers` + `login.live.com`），轮换后自动回写 `secret_enc`
- `GET  /api/refills/{id}/mail/code?kind=otp|recovery` 手动取码
- `POST /api/refills/{id}/mail/check` 校验物料是否可收件
- 补号页新增「检测收件」批量验证，OTP/Recovery 弹窗内置「自动取码」

### 浏览器指纹（TLS）
- `SIGNUP_TLS_IMPERSONATE=chrome`（默认，可 `edge101` / `chrome131`…，`off` 退回 httpx）
- 用 curl_cffi 的 libcurl-impersonate 提供真实 Chrome/Edge 的 **JA3/JA4 + HTTP/2 SETTINGS**，
  再叠加 HAR 提取的 UA / `sec-ch-ua*` / `sec-fetch-*` / hatch 头
- 实测：带完整指纹头时 `POST /api/auth/native/restart` 返回 201（csrf 43 字符，`password_encryption.key_id=254`）

### 相关配置
| 变量 | 默认 | 说明 |
|------|------|------|
| `SIGNUP_TLS_IMPERSONATE` | `chrome` | 浏览器 client-hello；`off` = httpx |
| `SIGNUP_AUTO_PASSWORD` | `true` | 激活后自动 recovery 设密 |
| `SIGNUP_MAIL_WAIT_SEC` | `90` | 等 OTP/恢复码的最长秒数 |
| `SIGNUP_MAIL_POLL_SEC` | `5` | 收件箱轮询间隔 |

### 验收
```bash
python scripts/smoke_card_checkout.py   # 注册→checkout→自动设密→入池（含断言）
python scripts/smoke_cards_api.py       # /api/cards 全接口
python scripts/verify_pwd_browser.py    # #PWD_BROWSER:5 真实加密器
```
