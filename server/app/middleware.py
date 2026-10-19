"""ASGI middleware: request-body size cap and local-origin protection.

The server holds vendor API keys and is reached from a browser, so any web
page the user visits could try to drive it (CSRF via a text/plain POST, or
DNS rebinding to read responses). Two cheap guards close that:

- Host allowlist: the Host header must be a loopback name (or one listed in
  ODYSSEUS_EDIT_ALLOWED_HOSTS). Blocks DNS rebinding.
- Origin check: a state-changing request that carries an Origin header must
  come from the same host:port it is addressed to. Same-origin fetches and
  non-browser clients (curl, no Origin) pass.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_MAX_BODY_BYTES = 50 * 1024 * 1024
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def _send_json(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject bodies above `max_bytes` with 413, by Content-Length or while streaming."""

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        detail = f"Request body too large (limit {self.max_bytes // (1024 * 1024)} MB)"
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await _send_json(send, 413, detail)
                        return
                except ValueError:
                    await _send_json(send, 400, "Invalid Content-Length")
                    return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await _send_json(send, 413, detail)


def _split_host(value: str) -> tuple[str, str]:
    """('host', 'port') from a Host header value; IPv6 brackets removed."""
    value = value.strip().lower()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, rest.lstrip(":")
    host, _, port = value.partition(":")
    return host, port


class LocalOriginMiddleware:
    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str] = LOOPBACK_HOSTS) -> None:
        self.app = app
        hosts = {h.strip().lower().strip("[]") for h in allowed_hosts if h.strip()}
        self.allow_any_host = "*" in hosts
        self.allowed_hosts = hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host_header = headers.get("host", "")
        host, port = _split_host(host_header)
        if not self.allow_any_host and host not in self.allowed_hosts:
            await _send_json(send, 400, "Invalid host header")
            return

        origin = headers.get("origin")
        if origin and scope.get("method", "GET").upper() in _UNSAFE_METHODS:
            parts = urlsplit(origin)
            origin_host = (parts.hostname or "").lower()
            default_port = {"http": "80", "https": "443"}.get(parts.scheme, "")
            origin_port = str(parts.port) if parts.port else default_port
            request_port = port or {"http": "80", "https": "443"}.get(scope.get("scheme", "http"), "")
            if origin == "null" or origin_host != host or origin_port != request_port:
                await _send_json(send, 403, "Cross-origin request rejected")
                return
        await self.app(scope, receive, send)
