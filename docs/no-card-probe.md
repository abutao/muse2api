# 免卡（0 元 / 试用）逆向结论

日期：2026-11-06 · 账号：新注册未验卡号（`awaiting_card`）· 工具：`HttpMuseClient`（curl_cffi 指纹伪装）

## 1. 重要前提：必须用浏览器 TLS 指纹

直接用 `requests` 打 `/api/checkout/init`、`/api/hatch/*` 一律 **403 Forbidden**；换成 `curl_cffi` 伪装（项目里的 `HttpMuseClient`）后同一请求正常。

说明 403 大多来自 TLS/HTTP2 指纹，不是账号状态。

## 2. `checkout/init` 不校验金额

用伪装客户端依次创建订单：

| amount | 结果 |
|---|---|
| 1.00 | 200 · orderId=1434854094815526 |
| 0.01 | 200 · orderId=1440097521433924 |
| 0.00 | 200 · orderId=29176388175379067 |

即 **0 元订单可以被创建**，服务端在 init 阶段不看金额。

init 返回字段：`orderId / ecpCheckoutSessionId / receiverName / receiverNameFull / paymentContainerMode / webSandboxActive / paymentMethod / terms / stripeLinkPaymentIntent`。

## 3. 但 finalize 需要真实凭证

对 0 元订单直接调 `/api/checkout/finalize`：

| credential_id | 结果 |
|---|---|
| `"0"` | 500 Internal server error |
| `""` | 200 但 `checkoutStatus=FAILURE`（出错了/请重试） |
| 随机 uuid | 500 Internal server error |

也就是说 finalize 必须带一次真实授权成功的 credential（PTT 提交卡后由 `/api/checkout/credential` 返回的 id）。

## 4. admission 仍被卡住

未完成 finalize 前：

```text
POST /api/hatch/accept-tos         403
POST /api/hatch/stateful-sharing   403
POST /api/hatch/lease-vm           403
POST /api/auth/check               403
GET  /api/session?vm_state=live    200 status=none（无 defaultVm）
```

## 5. 结论

- `checkout/init` 的 0 元单只是“能建单”，**不能替代验卡**；
- hatch admission 取决于 **finalize 成功**（真实卡授权），无法用 amount=0 / 试用 product 绕过；
- 因此免卡路径当前不可行，卡池仍是唯一稳定方案。

## 6. 后续可试

1. 找是否存在 `productId` 为试用/免费套餐（服务端可能对该 product 跳过 credential）——需要 product 枚举字典；
2. 观察 `webSandboxActive` / `stripeLinkPaymentIntent` 等字段，看是否有 sandbox 结账通道可被利用。

## 7. 真实下单请求（HAR 反查）

download.devtools 里抓到浏览器真实的下单三连，用于对照产品号：

`
POST /api/checkout/init
{amount:1.00,currency:USD,sessionId:upl_...,productId:766358168544755,
 receiverId:352501280694489,productData:[]}

POST /api/checkout/credential
{operation:CREATE,uplSessionId:upl_...,ecpCheckoutSessionId:null,
 orderId:1118011934432327,amount:1.00,currencyCode:USD,
 paymentContainerMode:LIVE,productId:766358168544755,
 receiverId:352501280694489,platformTrustToken:...}

POST /api/checkout/finalize
{ecpCheckoutSessionId:null,orderId:1118011934432327,uplSessionId:upl_...,
 selectedCredentialId:29369606102725938,credentialType:CREDIT_CARD,
 countryCode:US,amount:1.00,currencyCode:USD,
 payButtonClickId:<uuid>,paymentRequestId:<uuid>,platformTrustToken:...}
`

- 与服务端约定一致：项目 checkout_product_id=766358168544755、checkout_receiver_id=352501280694489、金额 1.00 USD；
- finalize 必须带 selectedCredentialId（由 /api/checkout/credential 提交真实卡后返回），没有它只会 FAILURE/500。

## 8. 是否有免费试用（free_trial）

前端存在 offerInfo.offerType === free_trial 的分支，但它只挂在**订阅套餐**的 checkout 上（plan change / 订阅升级），与注册时的 $1 Age Verification 无关；没有找到能让 Age Verification 免卡的 offer 或 coupon。
