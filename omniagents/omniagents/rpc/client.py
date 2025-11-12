"""Client-side JSON-RPC implementation."""

import contextlib
import asyncio
from typing import Any, Dict, Optional, Callable, TYPE_CHECKING

from .base import RpcMethodsBase


class RpcError(Exception):
    """Raised when a JSON-RPC call returns an error object.

    Attributes:
        code: JSON-RPC error code
        message: Human-readable message
        data: Optional error data
    """

    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


if TYPE_CHECKING:
    from .transport import Transport


class _ClientProxy:
    def __init__(self, client: "JsonRpcClient") -> None:
        self._client = client

    def __getattr__(self, name: str) -> Callable[..., asyncio.Future]:
        async def _call(**kwargs):
            return await self._client.call(name, **kwargs)

        return _call


class JsonRpcClient:
    """Lightweight JSON-RPC 2.0 client with pluggable transport."""

    def __init__(
        self, transport: "Transport", methods: Optional[RpcMethodsBase] = None
    ) -> None:
        self._transport = transport
        self._methods = methods
        self._recv_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._id = 0
        self.other = _ClientProxy(self)

    @classmethod
    def from_url(
        cls, url: str, methods: Optional[RpcMethodsBase] = None
    ) -> "JsonRpcClient":
        """Create a JsonRpcClient with WebSocket transport (backward compatibility)."""
        from .transport.websocket import ClientWebSocketTransport

        transport = ClientWebSocketTransport(url)
        return cls(transport, methods)

    async def __aenter__(self) -> "JsonRpcClient":
        await self._transport.connect()
        self._recv_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
        await self._transport.disconnect()

    async def _recv_loop(self) -> None:
        from .transport import TransportClosedError

        try:
            while self._transport.is_connected():
                try:
                    data = await self._transport.receive_json()
                except TransportClosedError:
                    break
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
                        if "error" in data and data["error"] is not None:
                            err = data["error"]
                            fut.set_exception(
                                RpcError(
                                    int(err.get("code", -32000)),
                                    str(err.get("message", "Unknown error")),
                                    err.get("data"),
                                )
                            )
                        else:
                            fut.set_result(data.get("result"))
        except Exception:
            # Ignore errors in receive loop
            pass

    async def call(self, method: str, **params) -> Any:
        if not self._transport.is_connected():
            raise RuntimeError("Transport is not connected")
        self._id += 1
        call_id = str(self._id)
        payload = {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut
        await self._transport.send_json(payload)
        return await fut

    async def notify(self, method: str, **params) -> None:
        if not self._transport.is_connected():
            raise RuntimeError("Transport is not connected")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._transport.send_json(payload)
