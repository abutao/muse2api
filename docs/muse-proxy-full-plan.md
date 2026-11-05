# muse-proxy 完整实施方案

> **权威文档**：本文（`/workspace/muse-proxy-full-plan.md`）为准。  
> `/workspace/muse-proxy-design.md` 已同步对齐；若冲突，**以本 full-plan 为准**。  
> 产品名：`muse-proxy`（建议 FastAPI）  
> 对外端口：`:8090`  
> 上游 muse2api：`http://127.0.0.1:19080`（容器常见映射 `19080→18610`）  
> 同机对照：Firefly / 神奇的小AD 5.1 `:8080`  
> 协议依据：`/workspace/muse-signup-protocol-v2.md`  
> 前端对照：`/workspace/firefly-frontend-map.md`、`/tmp/ad_app.js`  
> 上游分析：`/workspace/muse2api-analysis.md`  
> 修订日：2026-11-05（Asia/Shanghai）  
> **文档禁止写入真实 Cookie / 密钥 / 卡号 / CVV / 有效期。**

---

## 0. 文档优先级与已确认范围摘要

| 项 | 结论 |
|----|------|
| 产品形态 | muse2api **反代** + 管理台 **七页对齐小AD** |
| 上游 | `127.0.0.1:19080`；SSE **原样透传**；本库 **SQLite** |
| 号池真相 | **仅** muse2api `/admin/accounts*`；本服务 **不建第二套权威号池** |
| 注册 | 状态机 + **填资料 API**；支付 = 外部完成；`awaiting_external_payment` + `resume` |
| 邮箱口令 / Cookie | 导入格式、任务态、收信、入池 **对齐小AD 补号/子号** |
| **禁止** | 卡池、卡批量导入、checkout/PTT/JWE/代扣实现；**禁止**卡号/CVV/有效期入库 |

---

## 1. 产品定位与非目标

### 1.1 定位（一句话）

给本机 muse2api 加一层 **OpenAI 兼容反代网关** + **小AD 同构七页运维后台**：对外多 Key 计量转发；对内代理号池/保活；并提供 **Muse 注册编排**（邮箱 OTP → 资料补全 → **外部支付卡点** → accept-tos / wake → Cookie 入上游池）。

### 1.2 目标（首版必须交付）

1. `/v1/*` 反代：chat（SSE）、images、videos、models、media（可鉴权回源）。
2. 管理台七页：`overview / generate / records / accounts / subs / refills / settings`，交互贴近小AD（hash 路由、cookie 登录、toast/ask/modal、分页、401→登录）。
3. 号池页代理 muse2api admin，**本库不存完整号池 Cookie 权威副本**。
4. 导号/注册（`subs`）+ 补号/保活与邮箱物料（`refills`）任务流；邮箱口令导入对齐小AD。
5. 注册填资料 API；支付仅 `awaiting_external_payment` + `POST …/resume`（`payment_ref` 占位）。
6. 调用/生成记录、多对外 Key、设置页上游与网关配置。

### 1.3 非目标与禁止项

| 不做 / 禁止 | 说明 |
|-------------|------|
| 卡池、卡批量导入 | 用户明确「卡不要你做」；界面与表结构 **无** 卡字段 |
| checkout / PTT / JWE / 代扣脚本 | 支付由 **外部系统** 完成 |
| 卡号、CVV、有效期、持卡人地址入库 | **硬禁止**；resume 只用 `payment_ref` 等非卡密字段 |
| 本进程重做 Chromium/CDP 生成引擎 | 生成仍由 muse2api `engine` |
| SQLite 第二套 Muse 号池真相 | 列表以每次上游响应为准 |
| Adobe 邮箱注册协议本体 / ARP 铸票 / R2（小AD 专属） | 不移植；仅复用「邮箱物料 + 任务 UX」 |
| 支付商城、自助套餐发卡 | 多 Key 为自用计量 |
| 文档或日志打印真实 Cookie/上游总 Key | 脱敏 / 前后缀预览 |

---

## 2. 系统架构

```mermaid
flowchart LR
  subgraph Clients
    C1[OpenAI 兼容客户端]
    C2[浏览器管理台七页]
    C3[外部支付/编排系统]
  end

  subgraph MuseProxy["muse-proxy :8090"]
    UI[静态 SPA]
    ADM[管理 API /api/*]
    GW[反代 /v1/*]
    AUTH[对外 Key / 试调旁路]
    REG[注册状态机]
    MAIL[邮箱取信适配]
    JOB[导号/补号/保活任务]
    DB[(SQLite)]
  end

  subgraph Upstream["muse2api :19080"]
    UAPI["/v1 chat|images|videos|media"]
    UADM["/admin/accounts|keepalive|…"]
    UENG[CDP + accounts.json]
  end

  subgraph MuseWeb["muse.ai 协议"]
    NAT[native OTP/completion]
    EXT[外部支付完成]
    ACT[accept-tos / vm/wake]
  end

  C1 -->|Bearer 对外 Key| GW
  C2 -->|Cookie| UI --> ADM
  C3 -->|填资料 / resume| REG
  ADM --> AUTH
  ADM --> JOB
  ADM --> REG
  REG --> MAIL
  REG --> NAT
  REG -.->|awaiting_external_payment| EXT
  EXT -->|payment_ref resume| REG
  REG --> ACT
  REG -->|Cookie 入池| UADM
  GW --> AUTH --> DB
  GW -->|上游总 Key| UAPI
  ADM -->|上游总 Key| UADM
  JOB --> DB
  UAPI --> UENG
  UADM --> UENG
```

**对外 API 主路径**

```text
Client Bearer 对外Key
  → 鉴权 / 日限 / 额度 / 模型白名单
  → Authorization: Bearer <UPSTREAM_KEY>
  → 转发 UPSTREAM_BASE + path（SSE 逐块透传）
  → 可选改写 /v1/media URL
  → 写 call_logs + 扣减额度
```

**注册主路径（无卡）**

```text
导入邮箱物料 → restart → send-otp →（取信 OTP）→ confirm-otp
  → register-completion（生日等填资料）
  → Meta bootstrap（服务端代跑，若实现）
  → 状态 awaiting_external_payment   ← 卡点，本系统停
  → 外部完成支付后 POST …/resume { payment_ref }
  → accept-tos → vm/wake
  → 抽出 Cookie → 可选 POST 上游 /admin/accounts → pooled
```

---

## 3. 七页功能说明书

侧栏与小AD一致：

```text
location.hash → overview|generate|records|accounts|subs|refills|settings
默认 #overview；非法 → overview
```

交互横切：`api()` + `credentials:'same-origin'`、401→`/login.html`、`toast`/`ask`/`modal`、`esc()`、统一分页、切页 `clearInterval(timer)`。  
登录：`POST /api/login` `{ user, password }`；`GET /api/me`；`POST /api/logout`。

### 3.1 总览 `overview`

| 维 | 内容 |
|----|------|
| **用户操作** | 看今日/累计调用成功率（可分 chat/image/video）；切换 6h/24h/7d；看上游 health/ready 与号池概况；看对外 Key 启用数与近 24h Top；事件流；手动刷新 |
| **数据来源** | 本库 `call_logs` 聚合；上游 `GET /admin/status`、`/healthz`、`/readyz`；可选本库 `events` |
| **关键 API** | `GET /api/overview`；`GET /api/metrics`；`GET /api/stats/series?range=` |

### 3.2 生图生视频 `generate`（含对话试调）

| 维 | 内容 |
|----|------|
| **用户操作** | 模式：`chat` / `t2i` / `i2i` / `t2v` / `i2v`；选模型；提示词与参考图；立即生成；右侧画廊≈9；对话 SSE 流式区；本地偏好 `gen_sel`（**不**存上游总 Key） |
| **数据来源** | `GET /v1/models`（经反代）；任务状态来自 `/v1/images/tasks/{id}`、`/v1/videos/{id}` 与 records |
| **关键 API** | `POST /api/generate/token`；`GET /api/generate/models`；`GET /api/generate/gallery`；实际生成走 **`/v1/chat/completions`、`/v1/images/*`、`/v1/videos*`**（内部短时 Key 或 Cookie+CSRF 旁路） |

轮询：优先单一 `watchTask`，避免小AD 双轮询打架。

### 3.3 生成记录 `records`

| 维 | 内容 |
|----|------|
| **用户操作** | 状态筛（全部/成功/失败/进行中）；搜 path/model/prompt/Key/错误/ID；详情 modal（媒体、日志感字段、latency、source）；清理失败/N 天前/ids/全部（跳过进行中） |
| **数据来源** | 本库 `call_logs`（+ 可选异步 `generation_jobs`） |
| **关键 API** | `GET /api/records`；`GET /api/records/{id}`；`POST /api/records/delete` |

### 3.4 号池管理 `accounts`

| 维 | 内容 |
|----|------|
| **用户操作** | 列表/筛/搜/分页；导入 Cookie（文本或文件，格式兼容 muse2api）；行：检测、编辑标签/代理/Cookie、启停、删除；批量检测/启停/删；导出 |
| **数据来源** | **实时代理** muse2api `/admin/accounts*`；概况可拼 `/admin/status` |
| **关键 API** | `/api/accounts`、`/import`、`/{id}`、`/{id}/cookies`、`/{id}/check`、`/batch`、`/export` → 上游 `/admin/accounts*` |

**硬约束**：本库 **不** 存完整 Cookie 权威副本。

### 3.5 导号 / 注册激活 `subs`（对齐小AD「子号激活」位）

| 维 | 内容 |
|----|------|
| **用户操作** | 导入邮箱物料（见 §6）；选 region/国家、是否入池；批量启动注册 / 重试 / 重置 / 删除；进度条+停止；运行日志；单行收件箱；对卡在支付步的任务展示「等待外部支付」并支持管理员触发/查看 resume 状态 |
| **数据来源** | 本库 `signup_jobs` / `signup_items` / 邮箱密文；上游仅在入池时写入 |
| **关键 API** | `/api/subs*`（列表/import/batch/task/stop/export/mail）；填资料与 resume 见 §5（可挂在 `/api/subs/{id}/…` 或 `/api/signup/…`，实现时二选一，文档推荐挂 `subs` 以对齐七页） |

状态展示对齐小AD芯片：`pending / running / activated / failed`，并扩展：`awaiting_profile`、`awaiting_otp`、`awaiting_external_payment`、`pooled`。

### 3.6 补号系统 `refills`（对齐小AD「补号」位）

小AD 补号 = 邮箱导入 → 普号注册 → Cookie 入池 + 看门狗。本系统映射为两块能力（同一页分区或 Tab）：

**A. 邮箱物料与入池（对齐小AD 补号 UX）**

- 同一套邮箱口令导入格式、任务条、收件箱、导出、入池开关。
- 执行策略可配置：`mode=signup`（走与 subs 相同注册状态机）或 `mode=cookie_import`（行内已有 Cookie 则直接推上游）。

**B. 保活 / 续签队列（对齐 muse2api 运维）**

- 全池/单号 keepalive、test、relogin、quota；进度与日志同构小AD 补号任务条。

| 维 | 内容 |
|----|------|
| **用户操作** | 导入邮箱；启动补号/入池；看门狗设置（设置页）；批量 keepalive/test/relogin/quota；日志与停止 |
| **数据来源** | 本库 `refill_items` + `keepalive_jobs`；账号列表合并上游 |
| **关键 API** | `/api/refills*`；`/api/refills/keepalive/*` 或 batch action 区分 |

### 3.7 系统设置 `settings`

| 卡片 | 内容 |
|------|------|
| 上游连接 | base URL、总 Key（遮罩）、测试连通 |
| 网关 | 超时、并发、`max_body_bytes` |
| 媒体 | `media_auth_required`、`rewrite_media_urls`、`PUBLIC_BASE` |
| 控制台试调 | `console_generate_enabled` |
| 记录 | `record_keep_days` |
| 注册 / 补号 | 默认 region、入池默认、并发、OTP 超时、是否自动取信、`PROXY_SCHEDULED_KEEPALIVE` |
| 对外默认限额 | 新建 Key 默认额度/日限 |
| 安全 | 管理员、新密码、会话 TTL |
| **对外密钥**（页底） | 多 Key CRUD（明文仅创建时一次） |

---

## 4. 反代 `/v1` 路由与鉴权

### 4.1 鉴权通道

| 通道 | 方式 | 失败 |
|------|------|------|
| 管理 `/api/*`（除 login） | Cookie 会话 | 401 |
| 对外 `/v1/*` | `Authorization: Bearer <对外Key>` | 401/403/429 |
| 管理台试调 | 短时 `kind=console` Key 或 Cookie+CSRF 旁路 | 403 若关闭试调 |
| 健康/静态 | 无 | — |

回源 muse2api：**始终**换上游总 Key；**不**转发客户端 Cookie 到上游。

### 4.2 路由白名单

| Method | Path | 备注 |
|--------|------|------|
| GET | `/v1/models` | 可按 Key 白名单过滤 |
| POST | `/v1/chat/completions` | **SSE 原样透传**（含 `[DONE]`） |
| POST | `/v1/responses` | 流式透传 |
| POST | `/v1/images/generations` | |
| POST | `/v1/images/edits` | multipart 透传 |
| POST | `/v1/images/tasks` | 异步 |
| GET | `/v1/images/tasks/{task_id}` | |
| POST | `/v1/videos` | |
| POST | `/v1/videos/generations` | 别名 |
| GET | `/v1/videos/{task_id}` | |
| GET | `/v1/videos/generations/{task_id}` | |
| GET | `/v1/media/{name}` | 可要求 Bearer；禁路径穿越；回源 |

**不对外暴露** muse2api `/admin/*`（仅本服务管理 Cookie 经 `/api/accounts|subs|refills` 使用）。

### 4.3 多对外 Key 规则

- 表 `api_keys`：`token_hash`、前缀、`quota_total/used`、`daily_limit/used`、`models_allow`、`enabled`、`kind=user|console`。
- 拒绝：无效 Key、禁用、超额、模型不在白名单 → 对应 401/403/429。
- 计量：已回源默认扣减（`CHARGE_ON_UPSTREAM_ERROR` 可配）；`admin_console` 默认不扣对外额度。
- 并发满：429 `proxy_busy`；上游超时 504；不可达 502。

### 4.4 转发要点

1. 仅白名单路径；替换 Authorization。  
2. 流式不整包缓冲；设 `STREAM_IDLE_TIMEOUT_SEC`。  
3. `rewrite_media_urls`：响应 JSON 内 media → `PUBLIC_BASE/v1/media/...`。  
4. 写 `call_logs`（path、model、latency、source、upstream_status、error 截断）。

---

## 5. 注册状态机 + 填资料 API 草案

### 5.1 状态机（对照协议 v2，支付外置）

```mermaid
stateDiagram-v2
  [*] --> pending: 导入邮箱物料
  pending --> restarting: 启动注册
  restarting --> otp_sent: restart + send-otp OK
  otp_sent --> awaiting_otp: 等待填 OTP / 自动取信
  awaiting_otp --> completion_required: confirm-otp 200
  awaiting_otp --> restarting: csrfFailed → restart 自愈
  completion_required --> awaiting_profile: 需生日等
  awaiting_profile --> bootstrapped: register-completion + bootstrap
  bootstrapped --> awaiting_external_payment: 到达年龄验证门禁
  awaiting_external_payment --> post_payment: resume(payment_ref)
  post_payment --> activating: accept-tos + vm/wake
  activating --> activated: 会话可用
  activated --> pooled: intoPool 且上游 accounts OK
  activated --> [*]: 不入池仅保存会话摘要
  pending --> failed
  restarting --> failed
  otp_sent --> failed
  awaiting_otp --> failed
  awaiting_profile --> failed
  bootstrapped --> failed
  post_payment --> failed
  activating --> failed
  failed --> pending: reset
```

| 内部状态 | 含义 | 协议步骤 |
|----------|------|----------|
| `pending` | 已导入，未跑 | — |
| `restarting` | 取 CSRF | `POST /api/auth/native/restart` |
| `otp_sent` / `awaiting_otp` | 已发码，等 OTP | `send-otp` → `confirm-otp` |
| `completion_required` / `awaiting_profile` | 等生日等资料 | `register-completion` |
| `bootstrapped` | Meta bootstrap/OIDC 完成（若实现） | bootstrap + oidc callback |
| **`awaiting_external_payment`** | **硬卡点** | 本系统 **不** 调 checkout/* |
| `post_payment` | 外部已回调 | `resume` 成功 |
| `activating` | 激活收尾 | `accept-tos`、`vm/wake` |
| `activated` | 可用会话；可抽 Cookie | `auth/check` validated（观测） |
| `pooled` | 已推上游号池 | `POST /admin/accounts` |
| `awaiting_recovery_code` | Recovery 等恢复码 | `recovery-start` |
| `awaiting_set_password` | Recovery 等设密 | `recovery-confirm-code` / `recovery-complete` |
| `awaiting_password_login` | 密码登录（免 OTP） | `confirm-password` |
| `failed` / `stopped` | 失败或人工停止 | — |

**CSRF 自愈**：`confirm-otp` 403 `csrfFailed` → 自动 `restart`（`resume:false`）→ 重发 OTP → 回到 `awaiting_otp`（与协议样本一致）。

### 5.1b 旁路：Recovery 设密 / 密码登录 / 邀请码（无卡）

对照 `docs/muse-auth-protocol-full.md`，在 OTP 主路径外增加：

```mermaid
stateDiagram-v2
  [*] --> awaiting_recovery_code: recovery-start
  awaiting_recovery_code --> awaiting_set_password: recovery-confirm-code
  awaiting_set_password --> bootstrapped: recovery-complete (#PWD_BROWSER)
  [*] --> awaiting_password_login: send-otp + pla=true
  awaiting_password_login --> bootstrapped: confirm-password
  bootstrapped --> awaiting_external_payment: 未激活
  bootstrapped --> activating: already_activated 跳过支付卡点
```

| 状态 | 含义 | 协议 |
|------|------|------|
| `awaiting_recovery_code` | 已发恢复码 | `POST /api/auth/native/recovery-start` |
| `awaiting_set_password` | 等新密码 | `recovery-confirm-code` → `recovery-complete` |
| `awaiting_password_login` | 免 OTP 密码登录 | `send-otp`（`password_login_available`）→ `confirm-password` |

**管理 / Webhook API（无卡）：**

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/subs/{id}/recovery/start` | 发恢复码 |
| POST | `/api/subs/{id}/recovery/code` | `{ "recovery_code" }` |
| POST | `/api/subs/{id}/recovery/password` | `{ "new_password" }`（拒绝卡字段） |
| POST | `/api/subs/{id}/password/start` | 探测并进入密码登录 |
| POST | `/api/subs/{id}/password` | `{ "password" }` → confirm-password |
| POST | `/api/subs/{id}/invite/redeem` | `{ "code" }` |
| GET | `/api/subs/{id}/invite?create=0\|1` | 查看 / 创建邀请码 |

`#PWD_BROWSER`：mock 客户端本地封装；`SIGNUP_CLIENT=http` 时由可插拔 HTTP 客户端发出。支付仍仅 `awaiting_external_payment` + `resume(payment_ref)`。

`needs` 增补：`recovery_code`、`new_password`、`password`。



### 5.2 填资料 API 草案（给调用方 / 管理台）

鉴权：管理 Cookie **或** 专用 `SIGNUP_WEBHOOK_TOKEN`（Bearer，仅注册回调，权限小于管理员）。

推荐前缀：`/api/subs/{id}/…`（与七页一致）。等价别名 `/api/signup/{id}/…` 可选。

#### 5.2.1 查询任务

`GET /api/subs/{id}`

响应（示意，脱敏）：

```json
{
  "id": "s_xxx",
  "email": "user@example.com",
  "status": "awaiting_external_payment",
  "region": "US",
  "into_pool": true,
  "proxy": null,
  "needs": ["payment_external"],
  "profile": { "date_of_birth": null, "tos_content_version": "…" },
  "otp": { "masked_contact": "u***@example.com", "sent_at": "…" },
  "payment": { "status": "awaiting", "payment_ref": null, "resumed_at": null },
  "upstream_account_id": null,
  "error": null,
  "updated_at": "…"
}
```

`needs` 枚举示意：`email`、`otp_code`、`date_of_birth`、`proxy`、`cookie_manual`、`payment_external`、`recovery_code`、`new_password`、`password`。

#### 5.2.2 填写 / 更新资料（无卡字段）

`POST /api/subs/{id}/profile`

```json
{
  "date_of_birth": "1992-08-16",
  "region": "US",
  "proxy": "socks5://127.0.0.1:1080",
  "note": "可选备注",
  "remember_device": true,
  "tos_content_version": "可选；空则用 confirm-otp 返回值"
}
```

**禁止字段**（出现则 400）：`card_number`、`pan`、`cvv`、`cvc`、`expiry`、`exp_month`、`exp_year`、`card_*`、持卡人地址用于支付的整包卡密等。

#### 5.2.3 提交 OTP

`POST /api/subs/{id}/otp`

```json
{ "otp_code": "123456", "remember_device": true }
```

亦可由服务端自动取信填写（见 §6），管理台仍保留手工入口。

#### 5.2.4 手工补 Cookie（可选逃生舱）

`POST /api/subs/{id}/cookie`

```json
{ "cookie_header": "hatch_sess=…; …", "into_pool": true }
```

执行后可直接标记 `activated`/`pooled`；**明文执行后清空或只留 preview**。

#### 5.2.5 外部支付完成 → 继续

`POST /api/subs/{id}/resume`

```json
{
  "payment_ref": "ext_pay_20261105_xxx",
  "provider": "external",
  "note": "optional"
}
```

语义：

- 仅当 `status == awaiting_external_payment`（或允许幂等重复 resume）时接受。
- **只存** `payment_ref` / `provider` / 时间戳；**禁止**任何卡密。
- 成功后转入 `post_payment` → 跑 `accept-tos` + `vm/wake` → 抽 Cookie → 按 `into_pool` 推上游。

#### 5.2.6 批量 / 控制（对齐小AD）

| Method | Path | Body 要点 |
|--------|------|-----------|
| POST | `/api/subs/import` | `{ text, country/region }` |
| POST | `/api/subs/batch` | `{ ids\|all, action, country, intoPool }`；action: `activate\|retry\|reset\|delete` |
| POST | `/api/subs/stop` | — |
| GET | `/api/subs/task?after=` | 进度 + 增量日志 |
| GET | `/api/subs/{id}/mail` | 收件列表 |
| GET | `/api/subs/{id}/mail/message?id=` | 单封（HTML **须消毒** 后展示） |

---

## 6. 导号 / 补号（邮箱口令 · Cookie）与小AD 对照

依据：`/tmp/ad_app.js` 子号/补号导入文案与 API；服务器 `fireflyd` 路由字符串；`app.db` 表 `sub_accounts` / `refill_accounts` / `accounts`（仅结构，样本已脱敏）。

### 6.1 邮箱口令导入格式（对齐小AD）

每行一条，分隔符：`----` 或 `|` 或 Tab。

| 格式 | 字段 |
|------|------|
| **6 段** | `主邮箱----密码----ClientID----RefreshToken----辅助邮箱----辅助密码` |
| **4 段** | `主邮箱----密码----ClientID----RefreshToken`（无辅助邮箱；取信能力可能降级） |

小AD UI 文案：重复邮箱 **只更新凭据**，不重置指纹/状态。  
`mailReady`：凭证段齐全为 true，否则芯片「缺失」。

**muse-proxy 行为**

- 导入写入 `signup_items` / `refill_items`；密码与 refresh 等 **加密落库**（Fernet/age 等），API 列表 **不回说明文**。
- `mailReady` 逻辑同小AD。
- 取信：优先 Graph（ClientID+RefreshToken）；失败可降级 IMAP（若配置）。用于抽取 Muse OTP 邮件。

### 6.2 Cookie 导入格式（对齐小AD号池 + muse2api）

| 来源 | 格式 |
|------|------|
| 小AD 号池 | 每行 `Cookie` 或 `备注名----Cookie`；`POST /api/accounts/import` `{ text, replace }` |
| 小AD 推号示例 | JSON `{"kind":"…","text":"标签----完整Cookie\\n…"}` 或 `accounts[{cookie,name,kind}]` |
| muse2api | `cookie_header` / 扩展导号；核心 Cookie 含 `hatch_sess` 等 |

**muse-proxy**

- `accounts` 页导入 → 直接代理上游 `POST /admin/accounts`（循环/批量）。
- `subs`/`refills` 成功激活后若 `intoPool`：组装 `cookie_header` 推上游，本库只留 `cookie_preview` + `upstream_account_id`。

### 6.3 任务状态对照

| 小AD（subs/refills） | muse-proxy 扩展 | 说明 |
|---------------------|-----------------|------|
| `pending` | `pending` | 待执行 |
| `running` | `running` / 细分态 | 执行中 |
| `activated` | `activated` | 注册/激活成功 |
| `failed` | `failed` | 失败 |
| `pooled` / `poolState` | `pooled` | 已入号池 |
| — | `awaiting_otp` | 等 OTP |
| — | `awaiting_profile` | 等生日等 |
| — | **`awaiting_external_payment`** | **等外部支付** |

batch body 对齐小AD Go 结构线索：

```text
ids []string | all bool | status | q | action | country | intoPool *bool
action: activate | retry | reset | delete
```

### 6.4 邮箱取信对照

| 小AD | muse-proxy |
|------|------------|
| `GET /api/subs\|refills/{id}/mail` | 同路径形状 |
| `GET …/mail/message?id=` | 同；**HTML 消毒**（小AD 直出有 XSS 面，本系统要修） |
| 辅助邮箱收验证码 | Muse OTP 同样走主/辅邮箱取信 |
| `secret` 大字段存凭据（Firefly refill 样本） | 本系统拆列加密，避免单 blob 明文 |

### 6.5 Cookie 入池对照

| 小AD | muse-proxy |
|------|------------|
| 激活成功且 `intoPool` → 写入本库 `accounts` | 激活成功且 `into_pool` → **上游** `POST /admin/accounts` |
| 号池检测 `/api/accounts/{id}/check` | 代理 `POST /admin/accounts/{aid}/test` |
| 本库 `accounts.data` JSON 含 cookie | **禁止**第二套权威 Cookie；最多临时 `cookie_blob` 执行后清空 |

### 6.6 Firefly DB 结构线索（只读归纳）

| 表 | 形态 |
|----|------|
| `accounts` / `sub_accounts` / `refill_accounts` / `r2_accounts` | `id TEXT` + `data TEXT`（JSON 文档） |
| `kv` | 设置 KV |
| `records`（库 `records.db`） | 宽表记录表 |

refill 样本键（脱敏）：`email, secret, country, status, message, tries, profile, lastSid, cookie, hasCookie, pooled, createdAt, updatedAt, activatedAt`。  
accounts 样本键：`cookie, email, enabled, credits*, success/fail, importedAt, seed, note, userId, …`。

---

## 7. 数据模型（SQLite · **无卡字段**）

路径默认：`data/muse-proxy.db`。

### 7.1 表清单

| 表 | 用途 |
|----|------|
| `admin_users` / `sessions` | 管理登录 |
| `api_keys` | 对外/试调 Key |
| `call_logs` | 反代调用记录 |
| `settings` | KV 配置 |
| `signup_items`（subs） | 注册/导号行 |
| `signup_task_logs` | 增量日志 |
| `refill_items` | 补号邮箱物料行 |
| `keepalive_jobs` / `keepalive_logs` | 保活队列 |
| `events` | 可选总览事件 |

### 7.2 `api_keys`（摘要）

`id, name, token_hash, token_prefix, kind(user|console), enabled, quota_total, quota_used, daily_limit, daily_used, daily_reset_on, models_allow, expires_at, timestamps`

### 7.3 `call_logs`（摘要）

`id, key_id, method, path, api_type, source(external|admin_console), model, stream, status, http_status, upstream_status, error, latency_ms, prompt_preview, upstream_task_id, media_urls, timestamps`

### 7.4 `signup_items`（注册 · **无卡列**）

| 字段 | 说明 |
|------|------|
| id, email | |
| secret_enc | 邮箱口令物料密文（密码/ClientID/Refresh/辅邮等） |
| mail_ready | 0/1 |
| region / country | |
| proxy | 可选 |
| status | 见状态机 |
| profile_json | **仅** dob、tos_version、remember_device 等非卡资料 |
| otp_meta_json | masked_contact、sent_at 等 |
| payment_ref | **外部**支付引用字符串，可空 |
| payment_provider | 如 `external` |
| payment_resumed_at | |
| cookie_preview | 前后缀 |
| cookie_blob | 可选临时；入池或超时后清空 |
| into_pool | 0/1 |
| upstream_account_id | |
| tries, last_error, message | |
| csrf_meta_enc | 可选：waterfall/csrf 会话态密文 |
| created_at, updated_at, activated_at | |

**显式不建列**：`card_number`、`pan`、`cvv`、`cvc`、`expiry_*`、`card_holder`、`billing_address` 用于代扣的字段、`ptt`、`jwe`、`platform_trust_token` 等。

### 7.5 `refill_items`

与 `signup_items` 同构精简版（邮箱物料 + status + into_pool + upstream_account_id）；若 `mode=keepalive_only` 可不跑注册。

### 7.6 `keepalive_jobs` / `keepalive_logs`

`action: keepalive|quota|test|relogin|retry`；`scope: all|ids`；日志按 account_id。

### 7.7 明确不存

- 卡密与支付密文  
- Muse 账号 Cookie 权威全集  
- 上游 `tasks.json` 全量镜像  

---

## 8. 目录结构 · 配置 · 部署

### 8.1 目录

```text
muse-proxy/
├── README.md
├── .env.example
├── requirements.txt / pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── app/
│   ├── main.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── auth_admin.py
│   ├── auth_api.py
│   ├── proxy.py                 # /v1 SSE 转发
│   ├── upstream_admin.py        # muse2api /admin 客户端
│   ├── signup/
│   │   ├── machine.py           # 状态机
│   │   ├── protocol.py          # restart/otp/completion/tos/wake（无 checkout）
│   │   ├── mailer.py            # Graph/IMAP 取信
│   │   └── crypto.py            # 凭据加解密
│   ├── jobs_refill.py
│   ├── jobs_keepalive.py
│   └── routers/
│       ├── health.py
│       ├── v1.py
│       ├── admin_auth.py
│       ├── overview.py
│       ├── generate.py
│       ├── records.py
│       ├── accounts.py
│       ├── subs.py              # 含 profile/otp/resume
│       ├── refills.py
│       └── settings.py
├── static/                      # login.html index.html app.css app.js
└── data/muse-proxy.db
```

### 8.2 关键环境变量

```bash
HOST=0.0.0.0
PORT=8090
PUBLIC_BASE=http://127.0.0.1:8090
TZ=Asia/Shanghai
DATABASE_URL=sqlite:///./data/muse-proxy.db
ADMIN_USER=admin
ADMIN_PASSWORD=change-me
UPSTREAM_BASE_URL=http://127.0.0.1:19080
UPSTREAM_API_KEY=
CONNECT_TIMEOUT_SEC=10
REQUEST_TIMEOUT_SEC=600
STREAM_IDLE_TIMEOUT_SEC=120
MAX_CONCURRENCY=8
MEDIA_AUTH_REQUIRED=true
REWRITE_MEDIA_URLS=true
CONSOLE_GENERATE_ENABLED=true
RECORD_KEEP_DAYS=30
SIGNUP_CREDENTIALS_KEY=          # 邮箱物料加密主密钥
SIGNUP_WEBHOOK_TOKEN=            # 外部 resume/填资料用
PROXY_SCHEDULED_KEEPALIVE=false
TOKEN_PREFIX=mprox_
```

### 8.3 同机端口共存

| 服务 | 端口 |
|------|------|
| Firefly 小AD | `:8080` |
| muse-proxy（本系统） | `:8090` |
| muse2api（宿主机） | `:19080` → 容器 `18610` |

部署：独立 systemd 或 `docker-compose`；**不要**占用 8080/19080。首版可先本机 venv 跑通，再容器化。  
**本阶段不向生产强推部署**；以文档与本地脚手架为准。

---

## 9. 里程碑 M1–M8

| 里程碑 | 交付 | 验收 |
|--------|------|------|
| **M1** | 脚手架 + SQLite + 登录 + **七页空壳** + healthz | 七页可切换；401→登录 |
| **M2** | `/v1` 反代：models + chat **SSE** + 错误映射 + 上游配置 | curl `:8090` 流式对话成功 |
| **M3** | images/videos/media；媒体鉴权与 URL 改写；`call_logs` | 图/视频/媒体经反代有记录 |
| **M4** | **generate** 试调页 + `/api/generate/token` | ✅ 管理台能对话/出图，无需 curl |
| **M5** | **records** + 设置底 **多 Key** 额度日限 | ✅ 限额生效；记录可筛可清 |
| **M6** | **accounts** 代理上游号池 | ✅ 与 muse2api 同批号；无第二套 Cookie 真相 |
| **M7** | **subs**：邮箱导入 + OTP/资料 API + 状态机至 `awaiting_external_payment` + **resume** + tos/wake + 入池；**无卡** | ✅ 外部 payment_ref 后续跑通入池；库表无卡列 |
| **M8** ✅ | **refills** 邮箱物料 UX + keepalive 队列；**overview/settings** 收口；README | ✅ 闭环：导入→注册卡点→resume→号池→保活→试调→记录；overview 有数字；mock keepalive |

注册无卡能力放在 **M7+**；M1–M6 不阻塞反代与七页壳。

延期：支付实现、Adobe/R2/ARP、本库权威号池、公网一键升级 muse2api、多节点 Postgres。

---

## 10. 验收清单

### 10.1 反代与鉴权

- [ ] `GET /healthz` 200  
- [ ] 无 Key 调 `/v1/models` → 401  
- [ ] 有效对外 Key：models / chat SSE / 文生图或视频（依赖上游有号）  
- [ ] 媒体：开启鉴权时无 Key → 401；有 Key 可拉文件  
- [ ] 超额 / 日限 → 429；错误码稳定  

### 10.2 管理台七页

- [ ] 登录登出与小AD 同形  
- [ ] 七页路由与侧栏文案齐全  
- [ ] generate 试调出结果进画廊且 records 可见 `source=admin_console`  
- [ ] records 清理不影响进行中  

### 10.3 号池

- [x] 列表与 muse2api admin 一致（经 `/api/accounts` 代理 + mock 验收）  
- [x] 导入 Cookie 后上游可见  
- [x] 本库无完整 Cookie 持久权威副本  

### 10.4 注册 / 补号（无卡）

- [x] 6 段/4 段邮箱导入；`mailReady` 正确  
- [x] 可 `profile` / `otp`；拒绝卡字段 400  
- [x] 到 `awaiting_external_payment` 后协议侧 **无** checkout 调用  
- [x] `resume { payment_ref }` 后续 accept-tos/wake/入池  
- [x] 收件 HTML 经消毒  
- [x] DB schema 扫描无卡列名  

### 10.5 同机共存

- [ ] `:8090` 与 `:8080`、`:19080` 并行无端口冲突  

### 10.6 安全文档

- [ ] README / 本方案无真实密钥 Cookie 卡号  

---

### 10.5 M8 overview / refills / settings

- [x] `#overview` 今日/累计成功率、by_type、ov_range、上游 health、号池、Key 启用、失败事件  
- [x] `#refills` 邮箱导入列表 + keepalive 队列（全池/单号 test·relogin·quota·keepalive）  
- [x] 本库仅 `refill_items` / `keepalive_*` 任务态；无卡列  
- [x] `#settings` 上游遮罩、超时并发、媒体、试调、保留、管理员密码、测试连通、密钥子区  
- [x] README 写清 M1–M8 启动与验收；不部署 82.29、不写真实密钥  


## 11. 给主助手：下一步开工顺序（≤10）

1. **以本文为权威**，把 `muse-proxy-design.md` 页眉改为「细节以 full-plan 为准」并保留实现用 API 表。  
2. 初始化仓库 `muse-proxy/`：FastAPI 骨架、`config`、SQLite、`/healthz`、login 会话、**七页空壳 SPA**（M1）。  
3. 实现 `proxy.py`：`/v1/models` + `/v1/chat/completions` SSE 透传与基础错误映射（M2）。  
4. 补齐 images/videos/media + `call_logs`（M3）。  
5. 做 generate 试调 token + 画廊（M4）与 records + 多 Key（M5）。  
6. 接 `upstream_admin`，落地 accounts 代理页（M6）。  
7. 实现邮箱导入/加密/取信骨架与 **subs 状态机到 `awaiting_external_payment`**；暴露 `profile`/`otp`/`resume`（M7）。  
8. 接 accept-tos、vm/wake、Cookie 入上游池；HTML 邮件消毒。  
9. 做 refills 页（邮箱 UX + keepalive 队列）与 overview/settings 收口（M8）。  
10. 按 §10 跑验收；**不**实现任何卡池/checkout；**不**把真实密钥写入文档或示例。

---

## 附录 A · 与旧 design 文档关系

| 主题 | design 原稿 | full-plan 调整 |
|------|-------------|----------------|
| subs 语义 | 偏 Cookie 导号 | **注册状态机 + 邮箱物料 + 外部支付钩子**；Cookie 导号仍保留为逃生舱/accounts |
| refills 语义 | 仅 keepalive | **小AD 补号 UX（邮箱）+ keepalive** |
| 支付 | 未写 | **显式外置**；禁止卡入库 |
| 权威 | design | **本文** |

## 附录 B · 参考路径

- `/workspace/muse-proxy-design.md`  
- `/workspace/muse2api-analysis.md`  
- `/workspace/firefly-frontend-map.md`  
- `/workspace/muse-signup-protocol-v2.md`  
- `/tmp/ad_app.js`（小AD 前端）  
- 服务器：`/opt/firefly`、`/opt/muse2api`（只读对照；本文不写其密钥）

