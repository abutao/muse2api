"""Per-account HTTP proxies and a loopback authentication relay for Chromium."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ipaddress
import select
import socket
import socketserver
import threading
from urllib.parse import quote, unquote, urlunsplit, urlsplit

_PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
_PROXY_PREFIXES = tuple(f"{name}://" for name in _PROXY_SCHEMES)
_SCHEME_ALIAS = {
    "http": "http",
    "https": "http",  # CONNECT-style HTTP proxy
    "socks4": "socks5",
    "socks4a": "socks5h",
    "socks5": "socks5",
    "socks5h": "socks5h",
}


def _is_ipv6_literal(host: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(host.strip("[]")), ipaddress.IPv6Address)
    except ValueError:
        return False


def format_proxy_host(host: str) -> str:
    """Return a host suitable for URLs; IPv6 literals are bracketed once."""
    raw = (host or "").strip()
    if not raw:
        raise ValueError("Proxy host is required")
    if raw.startswith("["):
        if not raw.endswith("]") or len(raw) < 4:
            raise ValueError("IPv6 proxy host must look like [2001:db8::1]")
        inner = raw[1:-1]
        if not _is_ipv6_literal(inner):
            raise ValueError("Invalid IPv6 proxy host in brackets")
        return f"[{ipaddress.IPv6Address(inner).compressed}]"
    if _is_ipv6_literal(raw):
        return f"[{ipaddress.IPv6Address(raw).compressed}]"
    if ":" in raw:
        raise ValueError(
            "IPv6 proxy hosts must be bracketed, e.g. [2001:db8::1]:8080:user:pass"
        )
    if any(c in raw for c in "/\\@%"):
        raise ValueError("Proxy host contains invalid characters")
    try:
        return raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Proxy host is not a valid hostname") from exc


def _validate_port(port: str | int) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Proxy port must be an integer") from exc
    if not 1 <= value <= 65535:
        raise ValueError("Proxy port must be between 1 and 65535")
    return value


def _split_colon_proxy(raw: str) -> tuple[str, str, str, str, str]:
    """Parse host:port[:user[:pass]] with at most 3 splits so passwords may contain ':'."""
    text = raw.strip()
    scheme = "http"
    # Classic 5-field form without ':' inside the password: host:port:user:pass:scheme
    exact = text.split(":")
    if (
        len(exact) == 5
        and exact[1].isdigit()
        and exact[4].strip().lower() in _PROXY_SCHEMES
        and not exact[0].startswith("[")
    ):
        host, port, user, password, scheme = (p.strip() for p in exact)
        return scheme, host, port, user, password

    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError("IPv6 proxy host is missing a closing ']'")
        host = text[: end + 1]
        rest = text[end + 1 :]
        if not rest.startswith(":"):
            raise ValueError("IPv6 proxy must look like [address]:port:user:pass")
        exact_tail = rest[1:].split(":")
        # Exact [addr]:port:user:pass:scheme when password has no ':'
        if (
            len(exact_tail) == 4
            and exact_tail[0].isdigit()
            and exact_tail[3].lower() in _PROXY_SCHEMES
        ):
            port, user, password, scheme = exact_tail[0], exact_tail[1], exact_tail[2], exact_tail[3]
            return scheme, host, port, user, password
        pieces = rest[1:].split(":", 2)
        if len(pieces) == 1:
            port, user, password = pieces[0], "", ""
        elif len(pieces) == 2:
            port, user, password = pieces[0], pieces[1], ""
        else:
            port, user, password = pieces[0], pieces[1], pieces[2]
        return scheme, host, port, user, password

    parts = text.split(":", 3)
    if len(parts) == 2:
        host, port = parts
        return scheme, host, port, "", ""
    if len(parts) == 3:
        host, port, user = parts
        return scheme, host, port, user, ""
    if len(parts) == 4:
        host, port, user, password = parts
        return scheme, host, port, user, password
    raise ValueError(
        "Unsupported proxy format; use http://host:port, user:pass@host:port, "
        "or host:port:user:pass (password may contain ':')"
    )


def parse_proxy_string(entry: str | None) -> dict | None:
    """Normalize one proxy line into {server, username?, password?} without embedding userinfo.

    Accepted forms:
      * http(s)/socks URL (userinfo optional; decoded once into separate fields)
      * user:pass@host:port
      * host:port
      * host:port:user:pass   (default scheme http; password may contain ':')
      * host:port:user:pass:socks5  (exact 5 fields when password has no ':')
      * [ipv6]:port:user:pass
    """
    raw = (entry or "").strip()
    if not raw:
        return None
    if any(c.isspace() or ord(c) < 32 for c in raw):
        raise ValueError("Proxy value contains whitespace or control characters")

    low = raw.lower()
    if low.startswith(_PROXY_PREFIXES):
        parsed = urlsplit(raw)
        scheme = _SCHEME_ALIAS.get(parsed.scheme)
        if not scheme:
            raise ValueError(f"Unsupported proxy scheme: {parsed.scheme}")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Proxy URL must not include path, query, or fragment")
        host = parsed.hostname
        if not host:
            raise ValueError("Proxy URL is missing a host")
        port = parsed.port
        if port is None:
            port = 1080 if scheme.startswith("socks") else 80
        host_fmt = format_proxy_host(host)
        out = {"server": f"{scheme}://{host_fmt}:{_validate_port(port)}"}
        # Decode userinfo once; callers must not quote again into server.
        if parsed.username is not None:
            out["username"] = unquote(parsed.username)
        if parsed.password is not None:
            out["password"] = unquote(parsed.password)
        return out

    # Prefer host:port:user:pass before user:pass@host:port so passwords may contain '@'.
    colon_candidate = False
    if raw.startswith("["):
        colon_candidate = True
    else:
        head = raw.split(":", 2)
        if len(head) >= 2 and head[1].isdigit() and "@" not in head[0]:
            colon_candidate = True

    if colon_candidate:
        scheme, host, port, user, password = _split_colon_proxy(raw)
        scheme = _SCHEME_ALIAS.get(scheme.strip().lower(), "http")
        host_fmt = format_proxy_host(host)
        out = {"server": f"{scheme}://{host_fmt}:{_validate_port(port)}"}
        if user:
            out["username"] = user
        if password:
            out["password"] = password
        return out

    if "@" in raw:
        userinfo, _, hostport = raw.rpartition("@")
        if not userinfo or not hostport:
            raise ValueError("Proxy userinfo must look like user:pass@host:port")
        if hostport.startswith("["):
            scheme, host, port, _u, _p = _split_colon_proxy(hostport)
        else:
            hp = hostport.split(":", 1)
            if len(hp) != 2:
                raise ValueError("Proxy hostport must include a port")
            host, port = hp
            scheme = "http"
        if ":" in userinfo:
            user, password = userinfo.split(":", 1)
        else:
            user, password = userinfo, ""
        host_fmt = format_proxy_host(host)
        scheme = _SCHEME_ALIAS.get(scheme, "http")
        out = {"server": f"{scheme}://{host_fmt}:{_validate_port(port)}"}
        if user:
            out["username"] = user
        if password:
            out["password"] = password
        return out

    raise ValueError(
        "Unsupported proxy format; use http://host:port, user:pass@host:port, "
        "or host:port:user:pass (password may contain ':' or '@')"
    )


def normalize_server(value: str) -> str:
    """Accept HTTP or SOCKS5 proxy origins; credentials use separate fields."""
    try:
        parsed_dict = parse_proxy_string(value)
        if not parsed_dict:
            raise ValueError("empty")
        if parsed_dict.get("username") or parsed_dict.get("password"):
            # Origins used by Chromium relay must not carry userinfo.
            return parsed_dict["server"]
        return parsed_dict["server"]
    except ValueError as exc:
        raise ValueError(
            "Use http://host:port, socks5://host:port, host:port:user:pass, "
            "or [ipv6]:port:user:pass; enter username/password separately when possible"
        ) from exc


def public_proxy(proxy: dict | None) -> dict | None:
    if not proxy:
        return None
    normalized = normalize_proxy_dict(proxy) or {}
    return {
        "server": normalized.get("server", ""),
        "username": normalized.get("username", ""),
        "has_password": bool(normalized.get("password")),
    }


def normalize_proxy_dict(proxy: dict | None) -> dict | None:
    """Normalize proxy dicts and colon/URL strings into server + separate credentials."""
    if not proxy:
        return proxy
    if isinstance(proxy, str):
        return parse_proxy_string(proxy)

    server = str(proxy.get("server") or "").strip()
    user = proxy.get("username")
    pwd = proxy.get("password")
    if not server:
        return None

    # If server itself is a compact provider line / URL, parse it first.
    try:
        parsed = parse_proxy_string(server)
    except ValueError:
        parsed = None
    if parsed:
        out = {"server": parsed["server"]}
        # Explicit fields win; otherwise take decoded userinfo once.
        if user:
            out["username"] = str(user)
        elif parsed.get("username"):
            out["username"] = parsed["username"]
        if pwd:
            out["password"] = str(pwd)
        elif parsed.get("password"):
            out["password"] = parsed["password"]
        return out

    # Already a clean scheme://host:port with separate auth fields.
    try:
        clean = parse_proxy_string(
            server if "://" in server else f"http://{server}"
        )
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    out = {"server": clean["server"]}
    if user:
        out["username"] = str(user)
    if pwd:
        out["password"] = str(pwd)
    return out


def request_proxies(proxy: dict | None) -> dict:
    proxy = normalize_proxy_dict(proxy)
    if not proxy:
        return {}
    parsed = urlsplit(proxy["server"])
    host = parsed.hostname or ""
    port = parsed.port
    host_fmt = format_proxy_host(host)
    netloc = f"{host_fmt}:{port}" if port else host_fmt
    auth = ""
    if proxy.get("username") is not None or proxy.get("password") is not None:
        # Quote once here for requests; server field itself never embeds userinfo.
        auth = (
            quote(str(proxy.get("username") or ""), safe="")
            + ":"
            + quote(str(proxy.get("password") or ""), safe="")
            + "@"
        )
    # requests expects an HTTP proxy URL for both http/https targets.
    url = f"http://{auth}{netloc}"
    return {"proxies": {"http": url, "https": url}}


@contextmanager
def request_proxy_context(proxy: dict | None):
    """requests uses its native HTTP path or a short-lived local SOCKS adapter."""
    proxy = normalize_proxy_dict(proxy)
    if proxy and urlsplit(proxy["server"]).scheme in ("socks5", "socks5h"):
        relay = HTTPProxyRelay(proxy)
        try:
            yield {"proxies": {"http": relay.url, "https": relay.url}}
        finally:
            relay.close()
    else:
        yield request_proxies(proxy)


class _RelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        # Socket errors must never print headers or credentials.
        pass


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        relay = self.server.relay
        client = self.request
        upstream = None
        try:
            if not relay.track(client):
                return
            client.settimeout(15)
            # Read exactly through the header boundary: TLS bytes after CONNECT
            # must remain available for the bidirectional tunnel.
            head = bytearray()
            while not head.endswith(b"\r\n\r\n"):
                byte = client.recv(1)
                if not byte:
                    return
                head.extend(byte)
                if len(head) > 65536:
                    return
            lines = bytes(head[:-4]).split(b"\r\n")
            method, target, version = lines[0].split(b" ", 2)
            if method != b"CONNECT" and urlsplit(target.decode("ascii")).scheme != "http":
                return
            headers = [line for line in lines[1:]
                       if line.split(b":", 1)[0].lower() not in
                       (b"proxy-authorization", b"proxy-connection", b"connection")]
            if relay.scheme in ("socks5", "socks5h"):
                parsed = urlsplit(("//" if method == b"CONNECT" else "") + target.decode("ascii"))
                host = parsed.hostname
                port = parsed.port or (443 if method == b"CONNECT" else 80)
                if not host or parsed.username is not None or not 1 <= port <= 65535:
                    return
                upstream = relay.connect_socks(host, port)
                if not relay.track(upstream):
                    return
                if method == b"CONNECT":
                    client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    request_line = b" ".join((method, path.encode("ascii"), version))
                    headers.append(b"Connection: close")
                    upstream.sendall(b"\r\n".join([request_line, *headers]) + b"\r\n\r\n")
            else:
                upstream = socket.create_connection(relay.address, timeout=15)
                if not relay.track(upstream):
                    return
                if relay.authorization:
                    headers.append(b"Proxy-Authorization: " + relay.authorization)
                if method != b"CONNECT":
                    headers.append(b"Connection: close")
                upstream.sendall(b"\r\n".join([lines[0], *headers]) + b"\r\n\r\n")
            client.settimeout(30)
            upstream.settimeout(30)
            while not relay.closed:
                readable, _, _ = select.select([client, upstream], [], [], 1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except (OSError, ValueError, UnicodeError):
            # A failed proxy must never trigger a direct connection.
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
        finally:
            for sock in (client, upstream):
                if sock:
                    relay.untrack(sock)
                    sock.close()


class HTTPProxyRelay:
    """Forward Chrome's requests to one authenticated HTTP proxy, never direct."""
    def __init__(self, proxy: dict):
        parsed = urlsplit(normalize_server(proxy["server"]))
        self.address = (parsed.hostname, parsed.port)
        self.scheme = parsed.scheme
        self.username = proxy.get("username", "").encode("latin-1")
        self.password = proxy.get("password", "").encode("latin-1")
        if self.scheme in ("socks5", "socks5h") and (len(self.username) > 255 or len(self.password) > 255):
            raise ValueError("SOCKS5 credentials must not exceed 255 bytes")
        self.authorization = None
        if proxy.get("username") or proxy.get("password"):
            credentials = (proxy.get("username", "") + ":" + proxy.get("password", "")).encode("latin-1")
            self.authorization = b"Basic " + base64.b64encode(credentials)
        self.closed = False
        self._sockets = set()
        self._lock = threading.Lock()
        self.server = _RelayServer(("127.0.0.1", 0), _RelayHandler)
        self.server.relay = self
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    @staticmethod
    def _read_exact(sock, size):
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                raise OSError("SOCKS5 proxy closed the connection")
            data.extend(chunk)
        return bytes(data)

    def connect_socks(self, host, port):
        sock = socket.create_connection(self.address, timeout=15)
        try:
            method = 2 if self.username or self.password else 0
            sock.sendall(bytes((5, 1, method)))
            if self._read_exact(sock, 2) != bytes((5, method)):
                raise OSError("SOCKS5 authentication method rejected")
            if method == 2:
                sock.sendall(bytes((1, len(self.username))) + self.username
                             + bytes((len(self.password),)) + self.password)
                if self._read_exact(sock, 2) != bytes((1, 0)):
                    raise OSError("SOCKS5 authentication failed")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                domain = host.encode("idna")
                if not 1 <= len(domain) <= 255:
                    raise OSError("SOCKS5 hostname is invalid")
                destination = bytes((3, len(domain))) + domain
            else:
                destination = bytes((1 if address.version == 4 else 4,)) + address.packed
            sock.sendall(bytes((5, 1, 0)) + destination + int(port).to_bytes(2, "big"))
            reply = self._read_exact(sock, 4)
            if reply[:3] != bytes((5, 0, 0)):
                raise OSError("SOCKS5 connection rejected")
            length = {1: 4, 4: 16}.get(reply[3])
            if reply[3] == 3:
                length = self._read_exact(sock, 1)[0]
            if length is None:
                raise OSError("SOCKS5 response is invalid")
            self._read_exact(sock, length + 2)
            return sock
        except Exception:
            sock.close()
            raise

    def track(self, sock):
        with self._lock:
            if self.closed:
                sock.close()
                return False
            self._sockets.add(sock)
            return True

    def untrack(self, sock):
        with self._lock:
            self._sockets.discard(sock)

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            for sock in self._sockets:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            self._sockets.clear()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
