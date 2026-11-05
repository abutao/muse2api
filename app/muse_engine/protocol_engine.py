"""Pure-protocol generation engine compatible with the existing CDP engine."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import mimetypes
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.muse_engine.engine import (
    MuseEngine,
    MuseGenerationError,
    MAX_REFERENCE_IMAGES,
)
from app.muse_engine.protocol_client import HatchProtocolClient

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
WEBP_RIFF = b"RIFF"
WEBP_WEBP = b"WEBP"


def image_magic_mime(data: bytes) -> str | None:
    if not data:
        return None
    if data.startswith(PNG_MAGIC):
        return "image/png"
    if len(data) >= 12 and data.startswith(WEBP_RIFF) and data[8:12] == WEBP_WEBP:
        return "image/webp"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    return None


def to_png_bytes(data: bytes) -> bytes:
    """Lossless-ish PNG re-encode for API delivery (RGBA preserved when present)."""
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(data))
    if image.mode in ("RGBA", "LA"):
        converted = image
    elif image.mode == "P" and "transparency" in image.info:
        converted = image.convert("RGBA")
    else:
        converted = image.convert("RGB")
    buf = io.BytesIO()
    converted.save(buf, format="PNG", optimize=True)
    out = buf.getvalue()
    if not out.startswith(PNG_MAGIC):
        raise MuseGenerationError("PNG conversion produced invalid bytes")
    return out


def timing_deltas(marks: dict[str, float], meta: dict | None = None) -> dict[str, float]:
    """Exclusive stage durations in ms (end_mark - start_mark for that stage only)."""
    order = [
        ("session", "session_start"),
        ("wake", "wake_start"),
        ("token", "token_start"),
        ("noise", "noise_start"),
        ("subscribe", "subscribe_start"),
        ("first_event", "stream_start"),
        ("generation_done", "first_event"),
        ("fs_read", "fs_read_start"),
        ("png_convert", "png_convert_start"),
    ]
    out: dict[str, float] = {}
    for name, start in order:
        if name in marks and start in marks:
            out[name + "_ms"] = round((marks[name] - marks[start]) * 1000, 1)
    if "reuse_hit" in marks:
        out["reuse"] = 1.0
        if "stream_start" in marks:
            out["warm_to_stream_ms"] = round((marks["stream_start"] - marks["reuse_hit"]) * 1000, 1)
    if "connect_start" in marks and "subscribe" in marks:
        out["bootstrap_ms"] = round((marks["subscribe"] - marks["connect_start"]) * 1000, 1)
    elif "reuse_hit" in marks and "stream_start" in marks:
        out["bootstrap_ms"] = out.get("warm_to_stream_ms", 0.0)
    if "stream_start" in marks and "fs_read" in marks:
        out["stream_to_download_ms"] = round((marks["fs_read"] - marks["stream_start"]) * 1000, 1)
    if marks:
        out["total_marked_ms"] = round((max(marks.values()) - min(marks.values())) * 1000, 1)
    return out


FILE_REF_DEFAULT_BYTES = 10 * 1024 * 1024
FILE_REF_VIDEO_FALLBACK_BYTES = 60 * 1024 * 1024


def should_use_file_ref(size: int, mime: str, capabilities: list[Any] | None) -> bool:
    """Match consumer-web thresholds from the live protocol notes."""
    caps = {str(item) for item in (capabilities or [])}
    mime_l = (mime or "").lower()
    if mime_l.startswith("image/"):
        return False
    if mime_l.startswith("video/"):
        if "video_file_ref_ingest" in caps:
            return size >= FILE_REF_DEFAULT_BYTES
        return size >= FILE_REF_VIDEO_FALLBACK_BYTES
    return size >= FILE_REF_DEFAULT_BYTES


def _normalize_media_path(path: str) -> tuple[str, str] | None:
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("sandbox://"):
        remote = path[len("sandbox://"):]
        uri = path
    elif path.startswith("workspace/"):
        remote = path
        uri = "sandbox://" + path
    else:
        return None
    if remote.startswith("workspace/user/"):
        return None
    return uri, remote


def _media_candidates(value: Any, output: list[dict]) -> None:
    """Retain generated sandbox resources from presentation/resources envelopes."""
    if isinstance(value, dict):
        meta = value.get("metadata")
        if isinstance(meta, str) and meta.strip().startswith("{"):
            try:
                parsed = json.loads(meta)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict):
                value = {**value, "metadata": parsed}
                _media_candidates(parsed, output)

        path = (
            value.get("path")
            or value.get("uri")
            or value.get("image_path")
            or value.get("video_path")
        )
        normalized = _normalize_media_path(path) if isinstance(path, str) else None
        if normalized is None and isinstance(value.get("metadata"), dict):
            meta = value["metadata"]
            path = meta.get("path") or meta.get("uri")
            normalized = _normalize_media_path(path) if isinstance(path, str) else None
            if normalized is not None:
                value = {
                    **value,
                    "path": normalized[0],
                    "mime": value.get("mime") or value.get("mime_type") or meta.get("mime_type"),
                    "mime_type": value.get("mime_type") or meta.get("mime_type"),
                    "width": value.get("width") or meta.get("width"),
                    "height": value.get("height") or meta.get("height"),
                    "byte_len": value.get("byte_len") or meta.get("byte_len") or meta.get("size_bytes"),
                }
        if normalized is not None:
            uri, _remote = normalized
            resource = dict(value)
            resource["path"] = uri
            if not any(item["resource"].get("path") == uri for item in output):
                output.append({"resource": resource})
        for key, child in value.items():
            if key not in ("request", "data_base64"):
                _media_candidates(child, output)
    elif isinstance(value, list):
        for child in value:
            _media_candidates(child, output)


def _resource_kind(resource: dict) -> str:
    mime = str(resource.get("mime") or resource.get("mime_type") or "").lower()
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    path = str(
        resource.get("path")
        or resource.get("uri")
        or resource.get("image_path")
        or resource.get("video_path")
        or ""
    )
    suffix = Path(path).suffix.lower()
    return "video" if suffix in (".mp4", ".webm", ".mov") else "image"


def _payload_device_id(payload: dict) -> str | None:
    ctx = payload.get("chat_context")
    if isinstance(ctx, dict):
        device = ctx.get("device_id")
        if device:
            return str(device)
    return None


class ProtocolEngine:
    """Noise + chat.stream; start() deliberately never launches Chromium."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._semaphores: dict[str, threading.Semaphore] = {}

    def start(self) -> None:
        self.cfg.ensure_dirs()

    def _normalize_image(self, value: str) -> tuple[str, str]:
        # Reuse the existing SSRF, MIME, size and video-duration policy only.
        # This is a method call, not a CDP engine instance and starts no browser.
        return MuseEngine._normalize_image(self, value)

    def _text_concurrency(self) -> int:
        try:
            from app.config import get_settings
            value = int(getattr(get_settings(), "protocol_account_text_concurrency", 1) or 1)
        except Exception:
            value = 1
        return max(1, min(value, 2))

    @contextmanager
    def _account_lock(
        self,
        account_id: str | None,
        *,
        parallel_ok: bool = False,
        expect: str | None = "image",
    ):
        key = str(account_id or "default")
        # Image/video stay serial until separately validated.
        if expect in ("image", "video") or not parallel_ok:
            with self._locks_guard:
                lock = self._locks.setdefault(key, threading.Lock())
            with lock:
                yield
            return
        limit = self._text_concurrency()
        with self._locks_guard:
            sem = self._semaphores.setdefault(key + ":text", threading.Semaphore(limit))
        if not sem.acquire(timeout=120):
            raise MuseGenerationError("同账号文本并发位已满，请稍后重试")
        try:
            yield
        finally:
            sem.release()

    async def _stream(
        self,
        cookies: dict,
        prompt: str,
        *,
        expect: str | None,
        timeout: int,
        reference_images: list[str] | None,
        proxy: dict | None,
        on_progress=None,
        on_log=None,
        account_id: str | None = None,
        use_pool: bool = True,
        marker: str | None = None,
        timeline: list | None = None,
    ):
        from app.muse_engine.protocol_pool import get_worker

        deadline = time.monotonic() + max(10, int(timeout))
        worker = None
        listener = None
        pooled = bool(use_pool and account_id)
        wall0 = time.perf_counter()

        def log(message: str) -> None:
            if on_log:
                try:
                    on_log(message)
                except Exception:
                    pass

        def note(event: str, **extra) -> None:
            if timeline is not None:
                timeline.append({"t_ms": round((time.perf_counter() - wall0) * 1000, 1), "event": event, **extra})

        try:
            if pooled:
                worker = get_worker(str(account_id))
                client = await worker.acquire(cookies, proxy)
                reused = "reuse_hit" in client.timings
                log("复用纯协议 Noise 会话" if reused else "建立纯协议 Noise 会话（池化）")
                note("client_ready", reused=reused)
                node_id = client.new_task_node()
                listener = await worker.register_listener(node_id)
                subscription = {"payload": {"capabilities": []}}
            else:
                client = HatchProtocolClient(cookies, proxy=proxy)
                log("建立纯协议 Noise 会话")
                client._mark("connect_start")
                await client.connect()
                local_sub_id, subscription = await client.subscribe()
                node_id = client.node_id
                note("client_ready", reused=False)
            items: list[dict[str, Any]] = []
            references = list(reference_images or [])
            if len(references) > MAX_REFERENCE_IMAGES:
                raise MuseGenerationError(f"参考素材最多 {MAX_REFERENCE_IMAGES} 个")
            capabilities = (subscription.get("payload") or {}).get("capabilities") or []
            for index, reference in enumerate(references):
                try:
                    encoded, mime = self._normalize_image(reference)
                    data = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise MuseGenerationError(f"参考素材 {index + 1} 编码无效") from exc
                filename = f"reference-{index + 1}{mimetypes.guess_extension(mime) or '.bin'}"
                if should_use_file_ref(len(data), mime, capabilities):
                    items.append(await client.upload_file_ref(data, mime, filename))
                else:
                    items.append({
                        "type": "image" if mime.startswith("image/") else "file",
                        "mime_type": mime,
                        "data_base64": encoded,
                        "filename": filename,
                    })
            items.append({"type": "text", "text": prompt})
            client._mark("stream_start")
            body = {"items": items, "node_id": node_id, "capabilities": {}}
            if marker:
                body["metadata"] = {"client_marker": marker}
            log(f"提交 chat.stream（参考素材 {len(references)} 个）")
            note("stream_send_start", node_id=node_id, marker=marker)
            sent = await client.request("POST", "/chat/stream", body, 60)
            if sent.status != 200:
                raise MuseGenerationError(f"chat.stream HTTP {sent.status}")
            try:
                ack_payload = sent.json()
            except Exception:
                ack_payload = {}
            if not isinstance(ack_payload, dict):
                ack_payload = {}
            note(
                "stream_ack",
                node_id=node_id,
                message_id=str(ack_payload.get("message_id") or ""),
                session_id=str(ack_payload.get("session_id") or ""),
            )
            if on_progress:
                on_progress(15)
            seen: set[Any] = set()
            text_parts: list[str] = []
            pending_deltas: list[str] = []
            candidates: list[dict] = []
            done: dict | None = None
            done_at: float | None = None
            presentation_ready = False
            scan_offset = 0
            if pooled:
                local_sub_id = None

            while time.monotonic() < deadline:
                events = []
                if pooled:
                    while True:
                        try:
                            events.append(listener.queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                    if not events:
                        try:
                            events.append(await asyncio.wait_for(listener.queue.get(), timeout=0.5))
                        except asyncio.TimeoutError:
                            events = []
                else:
                    raw = bytes(client.responses[local_sub_id].body)
                    complete = raw.rfind(b"\n", scan_offset)
                    if complete >= scan_offset:
                        for line in raw[scan_offset:complete + 1].splitlines():
                            if not line.strip():
                                continue
                            try:
                                events.append(json.loads(line))
                            except (ValueError, UnicodeDecodeError):
                                continue
                        scan_offset = complete + 1
                    else:
                        try:
                            await client.wait_update(local_sub_id, 0.5)
                        except asyncio.TimeoutError:
                            pass

                for event in events:
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "pool_error":
                        raise MuseGenerationError("Noise 连接已断开，请重试")
                    sequence = event.get("seq")
                    if sequence is not None:
                        if sequence in seen:
                            continue
                        seen.add(sequence)
                    name = event.get("event") or event.get("type")
                    payload = event.get("payload")
                    if name == "res" or not isinstance(payload, dict):
                        continue
                    device_id = _payload_device_id(payload)
                    ours = device_id == node_id
                    if name == "delta.text_append":
                        if not ours:
                            continue
                        if "first_event" not in client.timings:
                            client._mark("first_event")
                            note("first_event", node_id=node_id)
                        delta = str(payload.get("text") or "")
                        if delta:
                            text_parts.append(delta)
                            if expect is None:
                                pending_deltas.append(delta)
                    if name in (
                        "delta.presentation",
                        "delta.message_done",
                        "delta.tool_done",
                        "delta.placeholder",
                    ):
                        before = len(candidates)
                        _media_candidates(payload, candidates)
                        if name == "delta.presentation" and len(candidates) > before:
                            presentation_ready = True
                            if "first_event" not in client.timings:
                                client._mark("first_event")
                                note("first_event", node_id=node_id, kind="presentation")
                    if name == "delta.message_done":
                        status = payload.get("status")
                        if ours and status not in (None, "completed"):
                            raise MuseGenerationError(f"Muse generation failed: {status}")
                        if ours or (expect is not None and (presentation_ready or candidates)):
                            if status in (None, "completed") or not ours:
                                done = payload
                                done_at = time.monotonic()
                                note("message_done", node_id=node_id, ours=ours)
                                if on_progress:
                                    on_progress(85)
                    if name == "delta.message_removed" and ours:
                        raise MuseGenerationError("Muse removed the generation before completion")

                if pending_deltas and expect is None:
                    for delta in pending_deltas:
                        yield {"type": "text", "text": delta}
                    pending_deltas.clear()
                if done is not None and expect is None:
                    text = "".join(text_parts)
                    if not text:
                        transcript = done.get("transcript") or {}
                        content = []
                        for message in transcript.get("messages") or []:
                            for part in message.get("content") or []:
                                if part.get("type") == "text":
                                    content.append(str(part.get("text") or ""))
                        text = "".join(content)
                    client._mark("generation_done")
                    note("generation_done", node_id=node_id, text_len=len(text))
                    yield {
                        "type": "result",
                        "text": text,
                        "timings": dict(client.timings),
                        "node_id": node_id,
                        "marker": marker,
                        "timeline": list(timeline or []),
                    }
                    return
                if expect is not None:
                    matching = [
                        c["resource"]
                        for c in candidates
                        if _resource_kind(c["resource"]) == expect
                    ]
                    if matching and (presentation_ready or done is not None):
                        resource = matching[-1]
                        log("检测到成品，通过 fs.read 取回")
                        client._mark("generation_done")
                        data = await client.download_bytes(resource)
                        note("fs_read_done", bytes=len(data))
                        yield {
                            "type": "result",
                            "data": data,
                            "resource": resource,
                            "text": "".join(text_parts),
                            "timings": dict(client.timings),
                            "download_via": "protocol.noise.fs.read",
                            "node_id": node_id,
                            "marker": marker,
                            "timeline": list(timeline or []),
                        }
                        return
                    if done is not None and done_at is not None and time.monotonic() - done_at >= 12:
                        raise MuseGenerationError(f"生成完成但未收到 {expect} 成品")
            raise MuseGenerationError("纯协议生成超时，未收到完整结果")
        except asyncio.TimeoutError:
            raise MuseGenerationError("纯协议上游请求超时") from None
        finally:
            if listener is not None and worker is not None:
                try:
                    await worker.unregister_listener(listener.node_id)
                except Exception:
                    pass
                try:
                    await worker.release()
                except Exception:
                    pass
            elif not pooled:
                try:
                    await client.close()
                except Exception:
                    pass

    def chat_stream(
        self,
        cookies: dict,
        prompt: str,
        expires: dict | None = None,
        timeout: int | None = None,
        account_id: str | None = None,
        proxy: dict | None = None,
        *,
        parallel_ok: bool = False,
        marker: str | None = None,
        timeline: list | None = None,
        use_pool: bool = True,
    ) -> Iterator[str]:
        timeout = int(timeout or getattr(self.cfg, "chat_timeout", 300))
        timeline = timeline if timeline is not None else []
        with self._account_lock(account_id, parallel_ok=parallel_ok, expect=None):
            async def collect():
                emitted = []
                result_meta = {}
                async for event in self._stream(
                    cookies,
                    prompt,
                    expect=None,
                    timeout=timeout,
                    reference_images=None,
                    proxy=proxy,
                    account_id=account_id,
                    use_pool=use_pool and bool(account_id),
                    marker=marker,
                    timeline=timeline,
                ):
                    if event.get("type") == "text":
                        emitted.append(event["text"])
                    elif event.get("type") == "result":
                        result_meta = event
                        if event.get("text") and not emitted:
                            emitted.append(event["text"])
                return emitted, result_meta

            if account_id and use_pool:
                from app.muse_engine.protocol_pool import get_worker
                texts, meta = get_worker(str(account_id)).call(collect(), timeout=timeout + 60)
            else:
                texts, meta = asyncio.run(collect())
            if meta.get("timings") is not None:
                timeline.append({"timings_ms": timing_deltas(meta.get("timings") or {})})
            for text in texts:
                yield text

    def generate(
        self,
        cookies: dict,
        prompt: str,
        expect: str = "image",
        timeout: int = 240,
        expires: dict | None = None,
        account_id: str | None = None,
        on_progress=None,
        reference_image: str | None = None,
        reference_images: list[str] | None = None,
        stop_event=None,
        on_log=None,
        fingerprint: dict | None = None,
        proxy: dict | None = None,
        *,
        parallel_ok: bool = False,
        marker: str | None = None,
        use_pool: bool = True,
    ) -> dict:
        if expect not in ("image", "video"):
            raise MuseGenerationError(f"Unsupported generation type: {expect}")
        references = list(reference_images or ([] if not reference_image else [reference_image]))
        timeline: list = []
        with self._account_lock(account_id, parallel_ok=False, expect=expect):
            async def collect() -> dict:
                result = None
                async for event in self._stream(
                    cookies,
                    prompt,
                    expect=expect,
                    timeout=timeout,
                    reference_images=references,
                    proxy=proxy,
                    on_progress=on_progress,
                    on_log=on_log,
                    account_id=account_id,
                    use_pool=use_pool and bool(account_id),
                    marker=marker,
                    timeline=timeline,
                ):
                    if event.get("type") == "result":
                        result = event
                if result is None:
                    raise MuseGenerationError("纯协议会话未返回生成结果")
                return result

            try:
                if account_id and use_pool:
                    from app.muse_engine.protocol_pool import get_worker
                    result = get_worker(str(account_id)).call(collect(), timeout=int(timeout) + 120)
                else:
                    result = asyncio.run(collect())
            except MuseGenerationError:
                raise
            except Exception:
                raise MuseGenerationError("纯协议生成失败") from None
            data = result["data"]
            resource = result["resource"]
            mime = str(resource.get("mime") or resource.get("mime_type") or "")
            remote = str(resource.get("path") or resource.get("uri") or "")
            source_mime = image_magic_mime(data) or mime
            converted_from = None
            marks = dict(result.get("timings") or {})
            if expect == "image":
                if not data.startswith(PNG_MAGIC):
                    marks["png_convert_start"] = time.perf_counter()
                    try:
                        data = to_png_bytes(data)
                    except Exception as exc:
                        raise MuseGenerationError(
                            f"无法将上游图片转换为 PNG ({type(exc).__name__})"
                        ) from None
                    marks["png_convert"] = time.perf_counter()
                    converted_from = source_mime or "unknown"
                mime = "image/png"
                extension = ".png"
            else:
                extension = (
                    Path(remote).suffix.lower()
                    or mimetypes.guess_extension(mime)
                    or ".mp4"
                )
                if extension == ".jpeg":
                    extension = ".jpg"
            if expect == "image" and not data.startswith(PNG_MAGIC):
                raise MuseGenerationError("PNG 交付校验失败：文件头不是 PNG magic")
            os.makedirs(self.cfg.media_dir, exist_ok=True)
            filename = uuid.uuid4().hex + extension
            destination = os.path.join(self.cfg.media_dir, filename)
            with open(destination, "wb") as handle:
                handle.write(data)
            if on_progress:
                on_progress(100)
            out = {
                "path": destination,
                "filename": filename,
                "size": len(data),
                "ext": extension,
                "mime": mime or mimetypes.guess_type(filename)[0] or "",
                "kind": expect,
                "via": "protocol.chat.stream+fs.read",
                "download_via": result.get("download_via") or "protocol.noise.fs.read",
                "w": resource.get("width"),
                "h": resource.get("height"),
                "sha256": hashlib.sha256(data).hexdigest(),
                "timings_ms": timing_deltas(marks),
                "timeline": result.get("timeline") or timeline,
                "node_id": result.get("node_id"),
                "marker": result.get("marker") or marker,
            }
            if converted_from:
                out["converted_from"] = converted_from
                out["source_mime"] = converted_from
            return out

