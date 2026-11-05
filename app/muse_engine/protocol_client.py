"""Browser-free Muse Hatch transport: Noise XX + logical HTTP over WebSocket.

Only pool-session cookies are consumed. No evidence files, request payloads,
access tokens, or cookie values are logged by this module.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import posixpath
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

import requests
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.muse_engine import hatch_pb2 as pb
from app.muse_engine.browser_proxy import (
    HTTPProxyRelay,
    normalize_proxy_dict,
    request_proxy_context,
)
from app.muse_engine.engine import MuseAuthError, MuseGenerationError, MuseProxyError

PROTOCOL = b"Noise_XX_25519_AESGCM_SHA256"
MAX_PLAINTEXT_FRAME = 65489
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)


def _sha256(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _hkdf(chaining_key: bytes, ikm: bytes, outputs: int = 2) -> list[bytes]:
    prk = hmac.new(chaining_key, ikm, hashlib.sha256).digest()
    result, last = [], b""
    for index in range(1, outputs + 1):
        last = hmac.new(prk, last + bytes([index]), hashlib.sha256).digest()
        result.append(last)
    return result


def _dh(private: X25519PrivateKey, public_bytes: bytes) -> bytes:
    result = private.exchange(X25519PublicKey.from_public_bytes(public_bytes))
    if result == b"\0" * 32:
        raise ValueError("all-zero X25519 DH")
    return result


class Cipher:
    def __init__(self, key: bytes | None = None):
        self.key, self.n = key, 0

    def enc(self, ad: bytes, plain: bytes) -> bytes:
        if self.key is None:
            return plain
        nonce = b"\0" * 4 + self.n.to_bytes(8, "big")
        self.n += 1
        return AESGCM(self.key).encrypt(nonce, plain, ad)

    def dec(self, ad: bytes, cipher: bytes) -> bytes:
        if self.key is None:
            return cipher
        nonce = b"\0" * 4 + self.n.to_bytes(8, "big")
        self.n += 1
        return AESGCM(self.key).decrypt(nonce, cipher, ad)


class Symmetric:
    def __init__(self):
        self.ck = PROTOCOL.ljust(32, b"\0")
        self.h = self.ck
        self.c = Cipher()
        self.mix_hash(b"")

    def mix_hash(self, value: bytes) -> None:
        self.h = _sha256(self.h + value)

    def mix_key(self, value: bytes) -> None:
        self.ck, key = _hkdf(self.ck, value, 2)
        self.c = Cipher(key)

    def encrypt_hash(self, plain: bytes) -> bytes:
        cipher = self.c.enc(self.h, plain)
        self.mix_hash(cipher)
        return cipher

    def decrypt_hash(self, cipher: bytes) -> bytes:
        plain = self.c.dec(self.h, cipher)
        self.mix_hash(cipher)
        return plain

    def split(self) -> tuple[Cipher, Cipher]:
        first, second = _hkdf(self.ck, b"", 2)
        return Cipher(first), Cipher(second)


class NoiseXX:
    def __init__(self):
        self.s = Symmetric()
        self.client_nonce = secrets.token_bytes(32)

    def msg1(self) -> bytes:
        self.e = X25519PrivateKey.generate()
        ephemeral = self.e.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.s.mix_hash(ephemeral)
        # Primary VM msg1 carries protobuf field 1 = 32-byte client nonce.
        return ephemeral + self.s.encrypt_hash(b"\x0a\x20" + self.client_nonce)

    def read_msg2(self, message: bytes) -> bytes:
        if len(message) < 96:
            raise ValueError("short Noise message 2")
        self.re = message[:32]
        self.s.mix_hash(self.re)
        self.s.mix_key(_dh(self.e, self.re))
        self.rs = self.s.decrypt_hash(message[32:80])
        self.s.mix_key(_dh(self.e, self.rs))
        return self.s.decrypt_hash(message[80:])

    def msg3(self, payload: bytes = b"") -> bytes:
        self.st = X25519PrivateKey.generate()
        static = self.st.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        encrypted_static = self.s.encrypt_hash(static)
        self.s.mix_key(_dh(self.st, self.re))
        return encrypted_static + self.s.encrypt_hash(payload)

    def split(self) -> tuple[Cipher, Cipher]:
        return self.s.split()


class Reassembler:
    def __init__(self):
        self.parts: dict[int, dict] = {}

    def push(self, frame: pb.NoiseTransportFrame) -> bytes | None:
        if not 1 <= frame.total_chunks <= 256:
            raise ValueError("invalid chunks")
        if frame.chunk_index >= frame.total_chunks:
            raise ValueError("invalid chunk index")
        if len(frame.payload) > MAX_PLAINTEXT_FRAME:
            raise ValueError("chunk too large")
        entry = self.parts.setdefault(
            frame.chunk_id, {"total": frame.total_chunks, "chunks": {}}
        )
        if entry["total"] != frame.total_chunks:
            raise ValueError("inconsistent chunks")
        if frame.chunk_index in entry["chunks"]:
            raise ValueError("duplicate chunk")
        entry["chunks"][frame.chunk_index] = bytes(frame.payload)
        if len(entry["chunks"]) != entry["total"]:
            return None
        del self.parts[frame.chunk_id]
        return b"".join(entry["chunks"][i] for i in range(entry["total"]))


@dataclass
class Response:
    status: int | None = None
    headers: list = field(default_factory=list)
    body: bytearray = field(default_factory=bytearray)
    ended: bool = False
    reset: dict | None = None

    def json(self):
        return json.loads(bytes(self.body))


class HatchProtocolClient:
    """Noise transport client. May be pooled per account; send path is lock-serialised."""

    def __init__(self, cookies: dict[str, str], *, proxy: dict | None = None):
        self.cookies = dict(cookies)
        self.proxy = normalize_proxy_dict(proxy)
        self.relay: HTTPProxyRelay | None = None
        self.ws = None
        self.send_cipher: Cipher | None = None
        self.recv_cipher: Cipher | None = None
        self.reassembler = Reassembler()
        self.next_stream = 1
        self.responses: dict[int, Response] = {}
        self.waiters: dict[int, set[asyncio.Future]] = {}
        self.receiver_task: asyncio.Task | None = None
        self.heartbeat_task: asyncio.Task | None = None
        self.node_id = str(uuid.uuid4())
        self.closing = False
        self.timings: dict[str, float] = {}
        self.timing_meta: dict[str, object] = {}
        self._http_session = requests.Session()
        self._send_lock: asyncio.Lock | None = None

    def _mark(self, name: str) -> None:
        self.timings[name] = time.perf_counter()

    def _http(self, method: str, url: str, body: dict | None = None) -> dict:
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Origin": "https://muse.ai",
            "Referer": "https://muse.ai/thread/new",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        try:
            with request_proxy_context(self.proxy) as options:
                # Prefer Session so warm TLS to muse.ai can be reused within this client.
                response = self._http_session.request(
                    method, url, headers=headers, json=body, timeout=30,
                    allow_redirects=False, **options,
                )
        except requests.RequestException as exc:
            error = MuseProxyError if self.proxy else MuseGenerationError
            raise error(f"Muse HTTP request failed ({type(exc).__name__})") from None
        if response.status_code == 401:
            raise MuseAuthError("Muse session authentication failed (HTTP 401)")
        if self.proxy and response.status_code == 407:
            raise MuseProxyError("Account proxy authentication failed (HTTP 407)")
        if response.status_code != 200:
            raise MuseGenerationError(f"Muse HTTP {response.status_code} for {url.split('?')[0]}")
        try:
            value = response.json() if response.content else {}
        except ValueError:
            raise MuseGenerationError("Muse returned a non-JSON protocol bootstrap response") from None
        if not isinstance(value, dict):
            raise MuseGenerationError("Muse returned an invalid protocol bootstrap response")
        return value

    async def connect(self) -> None:
        self._mark("session_start")
        session = self._http("GET", "https://muse.ai/api/session?vm_state=live")
        self._mark("session")
        vm_id = session.get("vm_id")
        vm_state = str(session.get("vm_state") or "").upper()
        if session.get("status") != "assigned" or not session.get("endpoint_url") or vm_state not in ("", "RUNNING"):
            if not vm_id:
                raise MuseGenerationError("Muse workspace is not assigned")
            # Wake is best-effort; readiness still requires a fresh session lookup.
            try:
                self._mark("wake_start")
                self._http(
                    "POST",
                    "https://muse.ai/api/hatch/vm/wake",
                    {
                        "vm_id": vm_id,
                        "retry_count": 0,
                        "connect_attempt_id": str(uuid.uuid4()),
                    },
                )
                self._mark("wake")
            except MuseGenerationError:
                pass
            session = self._http("GET", "https://muse.ai/api/session?vm_state=live")
            self._mark("session")
            vm_id = session.get("vm_id")
            vm_state = str(session.get("vm_state") or "").upper()
        if session.get("status") != "assigned":
            raise MuseGenerationError("Muse workspace is not assigned")
        endpoint = session.get("endpoint_url")
        if not vm_id or not endpoint:
            raise MuseGenerationError("Muse session lacks VM connection information")
        if vm_state and vm_state != "RUNNING":
            raise MuseGenerationError(f"Muse VM is not RUNNING (state={vm_state})")
        vm_type = str(session.get("vm_type") or "").lower()
        self._mark("token_start")
        token = self._http(
            "POST", "https://muse.ai/api/hatch/token",
            {"vmAddress": endpoint, **({"vmName": session["vm_name"]} if session.get("vm_name") else {})},
        )
        auth_token = token.get("token")
        self._mark("token")
        if not auth_token:
            raise MuseGenerationError("Muse hatch/token returned no token")
        notary = token.get("notary_token")
        if isinstance(notary, str) and notary.startswith("delegation."):
            raise MuseGenerationError(
                "Confidential-VM delegation endorsement is not implemented; "
                "protocol engine supports standard hatch_vm only (no CDP fallback)"
            )
        if "confidential" in vm_type:
            raise MuseGenerationError(
                f"Unsupported VM type {session.get('vm_type')!r}; "
                "protocol engine supports standard hatch_vm only (no CDP fallback)"
            )
        cvm = session.get("cvm_eligibility") or session.get("cvmEligibility") or {}
        if isinstance(cvm, dict):
            state = str(cvm.get("state") or cvm.get("status") or "").upper()
            if state and state not in ("NOT_ELIGIBLE", "INELIGIBLE", "NONE", "DISABLED", "N/A"):
                if "ELIGIB" in state or state in ("REQUIRED", "ENROLLED", "ENABLED"):
                    raise MuseGenerationError(
                        f"Unsupported confidential VM eligibility state={state!r}; "
                        "protocol engine supports standard hatch_vm only (no CDP fallback)"
                    )
        query = {
            "vm_id": vm_id, "auth_token": auth_token, "app_id": "hatch-web",
            "request_id": str(uuid.uuid4()),
        }
        if notary:
            query["notary_token"] = notary
        url = "wss://hatch.metaaivm.com/v1/noise?" + urlencode(query)
        try:
            if self.proxy:
                # The existing relay supports authenticated HTTP and SOCKS5 and
                # never falls back to a direct connection on proxy failure.
                self.relay = HTTPProxyRelay(self.proxy)
            self.ws = await websockets.connect(
                url, max_size=4 * 1024 * 1024, max_queue=64,
                open_timeout=30, close_timeout=3,
                origin="https://muse.ai", user_agent_header=USER_AGENT,
                additional_headers={
                    "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8",
                    "Cache-Control": "no-cache", "Pragma": "no-cache",
                },
                proxy=self.relay.url if self.relay else None,
            )
            self._mark("noise_start")
            noise = NoiseXX()
            await self.ws.send(noise.msg1())
            message2 = await asyncio.wait_for(self.ws.recv(), 30)
            if not isinstance(message2, bytes):
                raise TypeError("text WebSocket frame during Noise handshake")
            # Standard hatch_vm may still return an RV challenge blob in msg2;
            # the supported path sends an empty msg3. Confidential VMs that
            # require attestation/recovery proofs are rejected earlier via
            # vm_type / delegation notary — never silently fall back to CDP.
            challenge = noise.read_msg2(message2)
            if challenge and "confidential" in str(session.get("vm_type") or "").lower():
                raise MuseGenerationError(
                    "Confidential VM attestation challenge received; "
                    "recovery/attestation proof is not implemented "
                    "(no CDP fallback)"
                )
            await self.ws.send(noise.msg3())  # empty payload for standard VM
            self.send_cipher, self.recv_cipher = noise.split()
            self._mark("noise")
        except (MuseAuthError, MuseProxyError, MuseGenerationError):
            raise
        except Exception as exc:
            if self.relay:
                self.relay.close()
                self.relay = None
            error = MuseProxyError if self.proxy else MuseGenerationError
            raise error(f"Noise WebSocket connection failed ({type(exc).__name__})") from None
        self.receiver_task = asyncio.create_task(self._receiver())
        self.heartbeat_task = asyncio.create_task(self._heartbeat())

    def _notify(self, stream_id: int) -> None:
        for waiter in self.waiters.get(stream_id, set()):
            if not waiter.done():
                waiter.set_result(True)

    def _fail_waiters(self, exc: Exception) -> None:
        for waiters in self.waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(exc)

    async def _receiver(self) -> None:
        try:
            async for message in self.ws:
                if not isinstance(message, bytes):
                    raise TypeError("text WebSocket frame")
                frame = pb.NoiseTransportFrame.FromString(self.recv_cipher.dec(b"", message))
                raw = self.reassembler.push(frame)
                if raw is None:
                    continue
                service_response = pb.ServiceResponse.FromString(raw)
                service_frame = pb.ServiceFrame.FromString(service_response.payload)
                kind = service_frame.WhichOneof("kind")
                stream_id = service_frame.stream_id
                response = self.responses.setdefault(stream_id, Response())
                if kind == "response":
                    response.status = service_frame.response.status
                    response.headers = [
                        (h.key, h.value) for h in service_frame.response.headers
                    ]
                    response.body.extend(service_frame.response.body)
                    response.ended = service_frame.response.end_body
                elif kind == "body_chunk":
                    response.body.extend(service_frame.body_chunk.data)
                    response.ended = service_frame.body_chunk.end_body
                elif kind == "reset":
                    response.reset = {
                        "code": service_frame.reset.code,
                        "reason": service_frame.reset.reason,
                    }
                    response.ended = True
                self._notify(stream_id)
            if not self.closing:
                self._fail_waiters(MuseGenerationError("Noise connection closed"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_waiters(
                MuseGenerationError(f"Noise receive failed ({type(exc).__name__})")
            )

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                await self.request("POST", "/api/ping", {"presence": "active"}, 15)
        except asyncio.CancelledError:
            raise

    async def wait_update(self, stream_id: int, timeout: float = 30) -> None:
        waiter = asyncio.get_running_loop().create_future()
        self.waiters.setdefault(stream_id, set()).add(waiter)
        try:
            await asyncio.wait_for(waiter, timeout)
        finally:
            self.waiters.get(stream_id, set()).discard(waiter)

    async def wait_headers(self, stream_id: int, timeout: float = 30) -> Response:
        deadline = time.monotonic() + timeout
        while True:
            response = self.responses[stream_id]
            if response.status is not None or response.reset or response.ended:
                return response
            await self.wait_update(stream_id, max(0.1, deadline - time.monotonic()))

    async def wait_end(self, stream_id: int, timeout: float = 30) -> Response:
        deadline = time.monotonic() + timeout
        while not self.responses[stream_id].ended:
            await self.wait_update(stream_id, max(0.1, deadline - time.monotonic()))
        return self.responses[stream_id]

    def _pack(self, message) -> list[bytes]:
        raw = message.SerializeToString()
        chunk_id = int.from_bytes(secrets.token_bytes(8), "big", signed=True)
        chunks = [raw[i:i + MAX_PLAINTEXT_FRAME] for i in range(0, len(raw), MAX_PLAINTEXT_FRAME)] or [b""]
        result = []
        for index, chunk in enumerate(chunks):
            frame = pb.NoiseTransportFrame(
                chunk_id=chunk_id, chunk_index=index,
                total_chunks=len(chunks), payload=chunk,
            )
            result.append(self.send_cipher.enc(b"", frame.SerializeToString()))
        return result

    async def send_request(
        self, method: str, path: str, body: dict | None = None, *, stream: bool = False
    ) -> int:
        if self._send_lock is None:
            self._send_lock = asyncio.Lock()
        async with self._send_lock:
            stream_id = self.next_stream
            self.next_stream += 1
            self.responses[stream_id] = Response()
            headers = [
                pb.Header(key="x-request-id", value=str(uuid.uuid4())),
                pb.Header(key="x-app-id", value="hatch-web"),
                pb.Header(key="Accept-Language", value="en-US"),
                pb.Header(key="Accept-Encoding", value="identity"),
            ]
            if body is not None:
                headers.append(pb.Header(key="Content-Type", value="application/json"))
            request = pb.ApplicationRequest(
                verb=method, path=path, headers=headers,
                body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                if body is not None else b"",
                end_body=not stream,
            )
            frame = pb.ServiceFrame(stream_id=stream_id, request=request)
            service = pb.ServiceRequest(service=pb.SERVICE_DAEMON, payload=frame.SerializeToString())
            for encrypted in self._pack(service):
                await self.ws.send(encrypted)
            return stream_id

    async def request(
        self, method: str, path: str, body: dict | None = None, timeout: float = 40
    ) -> Response:
        stream_id = await self.send_request(method, path, body)
        response = await self.wait_end(stream_id, timeout)
        if response.reset:
            raise MuseGenerationError(f"Hatch stream reset for {path}")
        return response

    async def subscribe(self) -> tuple[int, dict]:
        self._mark("subscribe_start")
        stream_id = await self.send_request(
            "POST", "/chat/subscribe",
            {"after_stream_seq": 0, "after_chat_event_seq": 0, "capabilities": []},
        )
        response = await self.wait_headers(stream_id, 30)
        if response.status != 200:
            raise MuseGenerationError(f"chat.subscribe HTTP {response.status}")
        deadline = time.monotonic() + 30
        while b"\n" not in response.body:
            await self.wait_update(stream_id, max(0.1, deadline - time.monotonic()))
        ack = json.loads(bytes(response.body).split(b"\n", 1)[0])
        if ack.get("status") != "ok":
            raise MuseGenerationError("chat.subscribe was not accepted")

        self._mark("subscribe")
        return stream_id, ack

    async def download_bytes(self, resource: dict) -> bytes:
        self._mark("fs_read_start")
        path = str(resource.get("path") or "").removeprefix("sandbox://")
        if not path:
            raise MuseGenerationError("Generated resource has no sandbox path")
        output = bytearray()
        offset = 0
        while True:
            response = await self.request(
                "POST", "/fs/read",
                {"path": path, "offset": offset, "len": 1048576}, 60,
            )
            if response.status != 200:
                raise MuseGenerationError(f"fs.read HTTP {response.status}")
            body = response.json()
            result = body.get("result", body)
            chunk = base64.b64decode(result["data_base64"])
            if len(chunk) != result.get("len", len(chunk)):
                raise MuseGenerationError("fs.read length mismatch")
            output.extend(chunk)
            if result.get("eof"):
                break
            if not chunk:
                raise MuseGenerationError("fs.read returned a zero-length non-EOF chunk")
            offset += len(chunk)
        self._mark("fs_read")
        return bytes(output)

    async def upload_file_ref(self, data: bytes, mime: str, filename: str) -> dict:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or "reference.bin"
        suffix = posixpath.splitext(safe)[1].lower() or mimetypes.guess_extension(mime) or ".bin"
        stem = posixpath.splitext(safe)[0]
        final = f"workspace/user/files/{stem}_0_{uuid.uuid4().hex[:8]}{suffix}"
        staging = f"workspace/user/files/.upload-{uuid.uuid4()}.part"
        try:
            for index, offset in enumerate(range(0, len(data), 524288)):
                chunk = data[offset:offset + 524288]
                response = await self.request("POST", "/fs/write", {
                    "path": staging, "data_base64": base64.b64encode(chunk).decode(),
                    "overwrite": index == 0, "append": index != 0,
                    "create_parent": index == 0,
                }, 90)
                if response.status != 200:
                    raise MuseGenerationError(f"fs.write HTTP {response.status}")
            if not data:
                response = await self.request("POST", "/fs/write", {
                    "path": staging, "data_base64": "", "overwrite": True,
                    "append": False, "create_parent": True,
                }, 90)
                if response.status != 200:
                    raise MuseGenerationError("Empty fs.write failed")
            response = await self.request("POST", "/fs/stat", {"path": staging}, 40)
            stat = response.json().get("result", response.json())
            if response.status != 200 or stat.get("kind") != "file" or stat.get("size") != len(data):
                raise MuseGenerationError("Uploaded reference failed size verification")
            response = await self.request("POST", "/fs/rename", {
                "source": staging, "destination": final,
                "create_parent": True, "overwrite": True,
            }, 60)
            if response.status != 200:
                raise MuseGenerationError(f"fs.rename HTTP {response.status}")
            return {"type": "file_ref", "mime_type": mime, "path": final, "filename": filename}
        except Exception:
            try:
                await self.request("POST", "/fs/delete", {"path": staging, "recursive": False}, 30)
            except Exception:
                pass
            raise

    async def close(self) -> None:
        self.closing = True
        tasks = [t for t in (self.receiver_task, self.heartbeat_task) if t]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        if self.relay:
            self.relay.close()
            self.relay = None
        try:
            self._http_session.close()
        except Exception:
            pass

    def new_task_node(self) -> str:
        """Allocate a stable-for-this-task node_id without reconnecting."""
        self.node_id = str(uuid.uuid4())
        return self.node_id

    def reset_timings(self) -> None:
        self.timings = {}
        self.timing_meta = {}
