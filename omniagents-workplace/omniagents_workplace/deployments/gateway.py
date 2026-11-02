"""Dedicated Coolify origins with live Workplace membership enforcement."""

import asyncio
import contextlib
import re
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
import websockets
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.websockets import WebSocket

COOKIE = "omni_deployment_access"
STATE = "omni_deployment_state"
HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


class DeploymentGateway:
    def __init__(self, app, installation):
        self.app, self.installation = app, installation

    def factory(self, scope):
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode().lower()
        match = re.match(r"^coolify-([a-f0-9]{32})\.", host)
        if not match:
            return None
        factory_id = match[1]
        expected = urlsplit(
            self.installation.deployments.url(factory_id)
        ).netloc.lower()
        internal = urlsplit(
            self.installation.deployments.backchannel(factory_id)
        ).netloc
        if host == internal and scope.get("path", "").startswith("/workplace-auth/"):
            return factory_id
        return factory_id if host == expected else ""

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if (
            scope["type"] == "http"
            and scope.get("path") == "/workplace/deployments/allow-host"
        ):
            request = Request(scope, receive)
            domain = request.query_params.get("domain", "").lower()
            allowed = any(
                (record.get("deployment") or {}).get("enabled")
                and domain == urlsplit(self.installation.deployments.url(key)).hostname
                for key, record in self.installation.records.items()
            )
            allowed = allowed or any(
                key == domain
                for key, _ in self.installation.store.docs("extension.origin")
            )
            allowed = allowed or any(
                self.installation.deployments.preview_host(host)
                for _, record in self.installation.store.docs("deployment.preview")
                for host in record.get("hosts", [])
                if urlsplit("http://" + host).hostname == domain
            )
            return await Response(status_code=200 if allowed else 403)(
                scope, receive, send
            )
        request_host = dict(scope.get("headers", [])).get(b"host", b"").decode().lower()
        if request_host.startswith("preview-"):
            factory_id = self.installation.deployments.preview_host(request_host)
            if not factory_id:
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                return await Response("Preview unavailable", status_code=404)(
                    scope, receive, send
                )
            try:
                if scope["type"] == "websocket":
                    return await self.socket(
                        scope, receive, send, factory_id, None, preview=True
                    )
                response = await self.proxy(
                    Request(scope, receive), factory_id, preview=True
                )
                return await response(scope, receive, send)
            except (httpx.HTTPError, OSError):
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1011})
                return await Response(
                    "Preview is starting or unavailable", status_code=503
                )(scope, receive, send)
        factory_id = self.factory(scope)
        if factory_id is None:
            return await self.app(scope, receive, send)
        try:
            if not factory_id:
                raise HTTPException(404, "Unknown deployment host")
            d = self.installation.deployments
            d.identity.client(factory_id)
            request = Request({**scope, "type": "http"}, receive)
            grant = d.identity.read("access", request.cookies.get(COOKIE))
            if scope["type"] == "http":
                response = await self.route(request, factory_id, grant)
                if response is not None:
                    return await response(scope, receive, send)
            d.identity.account(grant, factory_id)
            if not d.view(factory_id)["ready"]:
                raise HTTPException(503, "Coolify is starting")
            if scope["type"] == "websocket":
                return await self.socket(scope, receive, send, factory_id, grant)
            response = await self.proxy(request, factory_id)
            return await response(scope, receive, send)
        except HTTPException as exc:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(
                    scope, receive, send
                )
        except (httpx.HTTPError, OSError):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1011})
            else:
                await JSONResponse(
                    {"detail": "Coolify is unavailable"}, status_code=503
                )(scope, receive, send)

    async def route(self, request, factory_id, grant):
        d = self.installation.deployments
        path = request.url.path
        if path == "/__workplace/start" and request.method == "GET":
            state = secrets.token_urlsafe(32)
            response = RedirectResponse(
                self.installation.base_url
                + "/workplace/deployments/open/"
                + factory_id
                + "?"
                + urlencode({"state": state}),
                303,
            )
            response.set_cookie(
                STATE,
                state,
                max_age=120,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            return response
        if path == "/__workplace/callback" and request.method == "GET":
            ticket = d.identity.read(
                "entry", request.query_params.get("code"), consume=True
            )
            d.identity.account(ticket, factory_id)
            if not secrets.compare_digest(
                ticket.get("state", ""), request.cookies.get(STATE, "")
            ) or not request.cookies.get(STATE):
                raise HTTPException(403, "Invalid sign-in state")
            access = d.identity.issue(
                "access", ticket, self.installation.accounts.session_seconds
            )
            response = RedirectResponse("/auth/authentik/redirect", 303)
            response.delete_cookie(STATE, secure=request.url.scheme == "https")
            # A different Workplace user may be using this browser now. Force
            # Coolify to establish the newly authorized user's native session.
            for name in request.cookies:
                if name not in {COOKIE, STATE}:
                    response.delete_cookie(name, secure=request.url.scheme == "https")
            response.set_cookie(
                COOKIE,
                access,
                max_age=self.installation.accounts.session_seconds,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            return response
        match = re.fullmatch(
            r"/workplace-auth/application/o/(authorize|token|userinfo)/", path
        )
        if match:
            return await d.identity.oauth(request, factory_id, match[1], grant)
        if not grant and request.method == "GET":
            return RedirectResponse("/__workplace/start", 303)
        return None

    async def proxy(self, request, factory_id, *, preview=False):
        d = self.installation.deployments
        # Never accept a browser mutation from another origin, including a
        # sibling factory. Coolify retains its own CSRF validation as well.
        origin = request.headers.get("origin")
        if (
            not preview
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin
            and origin != d.url(factory_id)
        ):
            raise HTTPException(403, "Wrong deployment origin")
        path = request.url.path
        offset = (
            1
            if path.startswith("/app/")
            else 2 if path.startswith("/terminal/ws") else 0
        )
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in HOP and not k.lower().startswith("x-forwarded-")
        }
        headers["cookie"] = "; ".join(
            f"{k}={v}"
            for k, v in request.cookies.items()
            if k not in {COOKIE, STATE, "workplace_session"}
        )
        public = urlsplit(str(request.url)) if preview else urlsplit(d.url(factory_id))
        headers.update(
            {
                "host": public.netloc,
                "x-forwarded-host": public.netloc,
                "x-forwarded-proto": public.scheme,
            }
        )
        client = httpx.AsyncClient(timeout=120, trust_env=False)
        url = (
            (
                d.preview_upstream(factory_id)
                if preview
                else d.upstream(factory_id, offset)
            )
            + path
            + ("?" + request.url.query if request.url.query else "")
        )
        try:
            upstream = await client.send(
                client.build_request(
                    request.method, url, headers=headers, content=request.stream()
                ),
                stream=True,
            )
        except BaseException:
            await client.aclose()
            raise

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        response = StreamingResponse(body(), status_code=upstream.status_code)
        response.raw_headers = [
            (k.lower(), v)
            for k, v in upstream.headers.raw
            if k.decode().lower() not in HOP
        ]
        if preview:
            # Preview applications cannot set cookies on Workplace or sibling factories.
            from http.cookies import SimpleCookie

            safe = []
            for key, value in response.raw_headers:
                if key == b"set-cookie":
                    cookie = SimpleCookie()
                    cookie.load(value.decode("latin1"))
                    for name, morsel in cookie.items():
                        if name.removeprefix("__Host-") in {
                            COOKIE,
                            STATE,
                            "workplace_session",
                        }:
                            continue
                        morsel["domain"] = ""
                        safe.append((key, morsel.OutputString().encode("latin1")))
                else:
                    safe.append((key, value))
            response.raw_headers = safe
        location = response.headers.get("location")
        internal = d.backchannel(factory_id) + "/workplace-auth/"
        if location and location.startswith(internal):
            response.headers["location"] = (
                d.url(factory_id) + location[len(d.backchannel(factory_id)) :]
            )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    async def socket(self, scope, receive, send, factory_id, grant, *, preview=False):
        d = self.installation.deployments
        ws = WebSocket(scope, receive, send)
        if not preview and ws.headers.get("origin") != d.url(factory_id):
            raise HTTPException(403, "Wrong deployment origin")
        path = scope["path"]
        offset = (
            1
            if path.startswith("/app/")
            else 2 if path.startswith("/terminal/ws") else None
        )
        if offset is None and not preview:
            raise HTTPException(404, "Unknown socket")
        query = scope.get("query_string", b"").decode()
        uri = (
            d.upstream(factory_id, offset or 0).replace("http:", "ws:")
            + path
            + ("?" + query if query else "")
        )
        options = {}
        if preview:
            target = urlsplit(d.preview_upstream(factory_id))
            uri = "ws://" + ws.headers["host"] + path + ("?" + query if query else "")
            options = {
                "host": target.hostname,
                "port": target.port,
                "additional_headers": {
                    "Cookie": "; ".join(
                        f"{k}={v}"
                        for k, v in ws.cookies.items()
                        if k not in {COOKIE, STATE, "workplace_session"}
                    )
                },
            }
        async with websockets.connect(
            uri,
            origin=ws.headers.get("origin") if preview else d.url(factory_id),
            max_size=16 * 1024 * 1024,
            **options,
        ) as upstream:
            await ws.accept()

            async def browser():
                while True:
                    event = await ws.receive()
                    if event["type"] == "websocket.disconnect":
                        return
                    await upstream.send(
                        event.get("text")
                        if event.get("text") is not None
                        else event["bytes"]
                    )

            async def server():
                async for value in upstream:
                    if isinstance(value, str):
                        await ws.send_text(value)
                    else:
                        await ws.send_bytes(value)

            async def access():
                while True:
                    await asyncio.sleep(1)
                    if preview:
                        if d.preview_host(ws.headers["host"]) != factory_id:
                            raise HTTPException(403, "Preview closed")
                    else:
                        d.identity.client(factory_id)
                        d.identity.account(grant, factory_id)

            tasks = [asyncio.create_task(fn()) for fn in (browser, server, access)]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with contextlib.suppress(Exception):
                    await ws.close(code=1008)
