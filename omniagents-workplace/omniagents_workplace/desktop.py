"""Authenticated Workplace proxy for the factory's VM desktop."""

import asyncio
from urllib.parse import urlsplit, urlunsplit

import httpx
import websockets
from fastapi import HTTPException, Request, WebSocket

from .factory_runtime import trusted_origin


def mount_desktop(app, workplace, admin, endpoint, cookie_value, cookie_name):
    def upstream():
        url, token = endpoint()
        if not url:
            raise HTTPException(503, "Factory is not connected")
        return url.rstrip("/"), {"Authorization": f"Bearer {token}"} if token else {}

    @app.get("/workplace/api/desktop")
    async def status(request: Request):
        admin(request)
        url, headers = upstream()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    url + "/integrations/desktop", headers=headers
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Factory desktop unavailable") from exc

    @app.websocket("/workplace/api/desktop/ws")
    async def connect(socket: WebSocket):
        cookie = cookie_value(socket.headers.get("cookie", ""), cookie_name)

        def authorized():
            account = workplace.accounts.signed_in(cookie)
            return bool(account and account.admin and not account.disabled)

        if not authorized() or not trusted_origin(
            socket.headers.get("origin"),
            workplace.base_url,
            workplace.additional_origins,
        ):
            await socket.close(code=1008)
            return
        tasks = []
        try:
            url, headers = upstream()
            parts = urlsplit(url + "/integrations/desktop/ws")
            url = urlunsplit(
                parts._replace(scheme="wss" if parts.scheme == "https" else "ws")
            )
            async with websockets.connect(
                url,
                additional_headers=headers,
                max_size=16 * 1024 * 1024,
                open_timeout=10,
            ) as remote:
                await socket.accept()

                async def send():
                    while True:
                        await remote.send(await socket.receive_bytes())

                async def receive():
                    async for data in remote:
                        await socket.send_bytes(data)

                async def check_access():
                    while authorized():
                        await asyncio.sleep(2)

                tasks = [
                    asyncio.create_task(fn()) for fn in (send, receive, check_access)
                ]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (HTTPException, OSError, websockets.exceptions.WebSocketException):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await socket.close()
            except RuntimeError:
                pass
