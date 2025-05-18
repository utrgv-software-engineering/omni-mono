import contextlib
import asyncio
import json
from typing import Any, Dict, Optional, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

__all__ = ["RpcMethodsBase", "JsonRpcEndpoint", "JsonRpcClient"]


class RpcMethodsBase:
    """Base class for JSON-RPC method containers."""

    def __init__(self) -> None:
        self.channel: Optional[JsonRpcChannel] = None


class JsonRpcChannel:
    """Simple helper used by the server to send notifications."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def async_call(self, method: str, args: Optional[Dict[str, Any]] = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": args or {}}
        await self.websocket.send_json(payload)

    async def send_result(self, call_id: Any, result: Any) -> None:
        payload = {"jsonrpc": "2.0", "id": call_id, "result": result}
        await self.websocket.send_json(payload)


class JsonRpcEndpoint:
    """Minimal JSON-RPC 2.0 WebSocket endpoint."""

    def __init__(self, methods: RpcMethodsBase) -> None:
        self.methods = methods

    def register_route(self, app: FastAPI, path: str, dependencies=None) -> None:
        dependencies = dependencies or []

        async def handler(websocket: WebSocket):
            await websocket.accept()
            channel = JsonRpcChannel(websocket)
            self.methods.channel = channel
            while True:
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    break
                call_id = data.get("id")
                method = data.get("method")
                params = data.get("params") or data.get("args") or {}
                if method:
                    func = getattr(self.methods, method)
                    result = await func(**params)
                    if call_id is not None:
                        await channel.send_result(call_id, result)
        app.add_api_websocket_route(path, handler, dependencies=dependencies)


class _ClientProxy:
    def __init__(self, client: "JsonRpcClient") -> None:
        self._client = client

    def __getattr__(self, name: str) -> Callable[..., asyncio.Future]:
        async def _call(**kwargs):
            return await self._client.call(name, **kwargs)

        return _call


class JsonRpcClient:
    """Lightweight JSON-RPC 2.0 client over WebSocket."""

    def __init__(self, url: str, methods: Optional[RpcMethodsBase] = None) -> None:
        self._url = url
        self._methods = methods
        self._ws: Optional["websockets.WebSocketClientProtocol"] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._id = 0
        self.other = _ClientProxy(self)

    async def __aenter__(self) -> "JsonRpcClient":
        import websockets

        self._ws = await websockets.connect(self._url)
        self._recv_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(Exception):
                await self._recv_task
        if self._ws:
            await self._ws.close()
        self._ws = None

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            try:
                data = json.loads(msg)
            except Exception:
                continue
            if "method" in data:
                func = getattr(self._methods, data["method"], None)
                if func:
                    params = data.get("params") or {}
                    if asyncio.iscoroutinefunction(func):
                        await func(**params)
                    else:
                        func(**params)
            if "id" in data and ("result" in data or "error" in data):
                call_id = str(data.get("id"))
                fut = self._pending.pop(call_id, None)
                if fut and not fut.done():
                    fut.set_result(data.get("result"))

    async def call(self, method: str, **params) -> Any:
        assert self._ws is not None
        self._id += 1
        call_id = str(self._id)
        payload = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut
        await self._ws.send(json.dumps(payload))
        return await fut
