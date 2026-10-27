"""Small transport limits and security events; no proxy or identity-provider assumptions."""

import json
import logging
import time
from collections import OrderedDict, deque

from starlette.websockets import WebSocketDisconnect

logger = logging.getLogger("omniagents_workplace.security")
logger.setLevel(logging.INFO)


def audit(event, **fields):
    # Callers pass identifiers/outcomes only, never passwords, cookies or tokens.
    logger.info("%s", json.dumps({"event": event, **fields}, sort_keys=True))


class RequestLimits:
    def __init__(
        self,
        app,
        auth_bytes=16384,
        body_bytes=64 * 1024 * 1024,
        websocket_bytes=1024 * 1024,
        attempts=30,
        window=60,
    ):
        self.app = app
        self.auth_bytes, self.body_bytes = auth_bytes, body_bytes
        self.websocket_bytes, self.attempts, self.window = (
            websocket_bytes,
            attempts,
            window,
        )
        self.sources = OrderedDict()

    async def __call__(self, scope, receive, send):
        async def refuse(status, detail):
            headers = [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ]
            if status == 429:
                headers.append((b"retry-after", str(self.window).encode()))
            await send(
                {"type": "http.response.start", "status": status, "headers": headers}
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps({"detail": detail}).encode(),
                }
            )

        if scope["type"] == "websocket":

            async def bounded_receive():
                message = await receive()
                size = len(message.get("bytes") or b"") + len(
                    (message.get("text") or "").encode()
                )
                if size > self.websocket_bytes:
                    await send({"type": "websocket.close", "code": 1009})
                    raise WebSocketDisconnect(1009)
                return message

            try:
                await self.app(scope, bounded_receive, send)
            except WebSocketDisconnect:
                pass
            return
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        auth = path in {
            "/workplace/api/signin",
            "/workplace/api/setup",
            "/workplace/api/claim",
            "/workplace/api/me/password",
        } or path.startswith("/workplace/api/invites/")
        if scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        if auth:
            # ASGI's client is resolved by Uvicorn's explicitly trusted proxies.
            # Never interpret forwarded headers ourselves.
            source = (scope.get("client") or ("unknown",))[0]
            now = time.monotonic()
            recent = self.sources.pop(source, deque())
            while recent and recent[0] <= now - self.window:
                recent.popleft()
            if len(self.sources) >= 4096:
                self.sources.popitem(last=False)
            self.sources[source] = recent
            if len(recent) >= self.attempts:
                audit("auth.source_limited", source=source)
                return await refuse(429, "Too many authentication requests")
            recent.append(now)
        limit = self.auth_bytes if auth else self.body_bytes
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await refuse(400, "Invalid Content-Length")
        if length < 0:
            return await refuse(400, "Invalid Content-Length")
        if length > limit:
            return await refuse(413, "Request body too large")
        # Check actual bytes too, including chunked requests, before parsers run.
        body = bytearray()
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                return await refuse(413, "Request body too large")
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def lock_data_directory(path):
    """Keep this handle open for the server lifetime; separate workers must fail closed."""
    import fcntl
    import os
    from pathlib import Path

    root = Path(path)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = os.fdopen(
        os.open(root / ".server.lock", os.O_CREAT | os.O_RDWR, 0o600), "w"
    )
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError(
            "Another workplace server is using this data directory"
        ) from exc
    return handle
