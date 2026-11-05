# Muse Noise 生成通道（纯协议逆向笔记）

来源：muse.ai 前端 chunk `383ckj4tpepa1.js`、`2_qr_ztx7hr-h.js`（2026-11-06 抓取）
抓包：CDP 监听 `wss://hatch.metaaivm.com/v1/noise`

## 1. 连接

```
wss://hatch.metaaivm.com/v1/noise
  ?vm_id=<uuid>
  &auth_token=<POST /api/hatch/token 返回的 token>
  &notary_token=<可选>
  &app_id=hatch-web
  &request_id=<uuid>
```

先决：Cookie `hatch_sess` → `GET /api/session?vm_state=live` 得 `assigned`，可选 `POST /api/hatch/vm/wake`。

## 2. Noise 握手

前端实现要点（chunk 383ckj4tpepa1.js）：

- 协议名：`Noise_XX_25519_AESGCM_SHA256`
- `h` 初值 = 协议名（32 字节），随后 `mixHash(空)` → `h = SHA256(h)`
- 曲线：X25519（WebCrypto 或 libsodium）
- 哈希：SHA-256；HKDF：HMAC-SHA256（Extract: salt=ck, ikm=data；Expand: 0x01 / 0x02）
- 加密：AES-256-GCM，Noise 标准 nonce（4 字节 0 + 8 字节计数器）
- 握手模式：XX（→ e / e,ee,s,es / s,se）
- 无 prologue：`mixHash(new Uint8Array(0))`

## 3. 帧格式

WebSocket 文本帧 = **base64( protobuf `NoiseTransportFrame` )**

```proto
syntax = "proto3";
package ingress_rev_proxy;
message NoiseTransportFrame {
  int64  chunk_id     = 1;
  uint32 chunk_index  = 2;
  uint32 total_chunks = 3;
  bytes  payload      = 4;   // Noise 握手/传输消息
}
```

大消息按 `chunk_id` 分片（chunk_index/total_chunks），收端重组。

## 4. RPC（Noise 内）

方法表 chunk `2_qr_ztx7hr-h.js`：

- `model.get` / `model.set`（`/model`）
- `chat.stream`（`/chat/stream`，noiseOnly，subscription）—— 唯一生成入口
- `chat.stream_events` / `chat.subscribe` / `chat.cancel`

`chat.stream` 请求体：

```json
{
  "items": [
    {"type": "text", "text": "画一只猫"},
    {"type": "image", "mime_type": "image/png", "data_base64": "...", "filename": "ref.png"}
  ],
  "node_id": "<device id>",
  "session_id": "<thread id>",
  "capabilities": {}
}
```

结果通过 subscription 事件返回附件元数据（`path` / `mime` / `duration_ms`），再从 sandbox / fileserver 取回文件。

## 5. 实测帧

冷启动 `thread/new` 会自动建连，首帧序列（base64 解码后字节数）：

```
sent 66, recv 166, sent 64, sent 264, sent 269, recv 156, recv 161, recv 32 ...
```

原始帧保存在 `.tmp/noise_raw_frames.json`（未提交，属于临时抓包）。

## 6. 下一步

1. Python 复刻 XX 握手（x25519 + HKDF-SHA256 + AES-GCM），与上面帧对拍；
2. 确认首帧 66 字节的外层包装（疑似长度前缀 + Noise 消息）；
3. 解析 subscription 事件，拿到结果路径并拉回文件。
