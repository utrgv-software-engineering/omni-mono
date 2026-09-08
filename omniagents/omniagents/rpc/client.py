"""Client-side JSON-RPC implementation."""

import contextlib
import asyncio
from importlib.metadata import version
import platform
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from pydantic import ValidationError

from .base import RpcMethodsBase
from .version import supports_gui_version
from .lifecycle import (
    DEFAULT_LIFECYCLE_POLICY,
    LifecyclePolicy,
    RpcConnectionClosedError,
    RpcTimeoutError,
)


class _Unset:
    """Sentinel distinguishing 'use policy default' from an explicit None."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<unset>"


_UNSET = _Unset()


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
    from .generated.gui_v2 import InitializeResult
    from .transport import Transport


DEFAULT_CLIENT_CAPABILITIES: Dict[str, Any] = {
    "realtime": False,
    "mcp_apps": False,
    "client_functions": False,
    "approvals": False,
    "artifacts": False,
    "replay": False,
    "terminal": False,
    "experimental_operations": [],
    "disabled_notifications": [],
}


class _ClientProxy:
    def __init__(self, client: "JsonRpcClient") -> None:
        self._client = client

    def __getattr__(self, name: str) -> Callable[..., asyncio.Future]:
        async def _call(**kwargs):
            return await self._client.call(name, **kwargs)

        return _call


class JsonRpcClient:
    """Lightweight JSON-RPC 2.0 client with pluggable transport.

    Lifecycle semantics follow :class:`~omniagents.rpc.lifecycle.LifecyclePolicy`:

    - ``__aenter__`` bounds transport connect + ``initialize`` handshake by
      ``policy.connect_timeout``.
    - Every call carries a deadline (``policy.rpc_timeout`` by default,
      overridable per call via :meth:`request`) and raises
      :class:`RpcTimeoutError` when exceeded.
    - Cancelling a task that awaits a call removes its pending entry — no
      orphaned futures.
    - When the connection closes (gracefully or not), **all** pending calls
      are rejected with :class:`RpcConnectionClosedError` and registered
      ``on_disconnect`` callbacks fire exactly once.
    """

    def __init__(
        self,
        transport: "Transport",
        methods: Optional[RpcMethodsBase] = None,
        *,
        gui_handshake: bool = True,
        client_info: Optional[Dict[str, str]] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY,
    ) -> None:
        self._transport = transport
        self._methods = methods
        self._gui_handshake = gui_handshake
        self._client_info = client_info or {
            "name": "omniagents-python",
            "version": version("omniagents"),
        }
        self._capabilities = capabilities or dict(DEFAULT_CLIENT_CAPABILITIES)
        self._policy = policy
        self._recv_task: Optional[asyncio.Task] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._id = 0
        self._closed = False
        self._disconnect_callbacks: List[Callable[[RpcConnectionClosedError], None]] = (
            []
        )
        self.initialize_result: Optional["InitializeResult"] = None
        self.other = _ClientProxy(self)

    @classmethod
    def from_url(
        cls,
        url: str,
        methods: Optional[RpcMethodsBase] = None,
        *,
        gui_handshake: bool = True,
        client_info: Optional[Dict[str, str]] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY,
    ) -> "JsonRpcClient":
        """Create a JsonRpcClient with WebSocket transport (backward compatibility).

        *token* is sent as an ``Authorization: Bearer`` header — never as a
        URL query parameter.
        """
        from .transport.websocket import ClientWebSocketTransport

        transport = ClientWebSocketTransport(url, token=token, policy=policy)
        return cls(
            transport,
            methods,
            gui_handshake=gui_handshake,
            client_info=client_info,
            capabilities=capabilities,
            policy=policy,
        )

    @property
    def policy(self) -> LifecyclePolicy:
        return self._policy

    def on_disconnect(
        self, callback: Callable[[RpcConnectionClosedError], None]
    ) -> Callable[[], None]:
        """Register a callback fired once when the connection closes.

        The callback receives the :class:`RpcConnectionClosedError` used to
        reject pending calls. Returns an unsubscribe function. This is also
        the seam where resume/replay logic (event cursors) plugs in.
        """
        self._disconnect_callbacks.append(callback)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._disconnect_callbacks.remove(callback)

        return _unsubscribe

    async def __aenter__(self) -> "JsonRpcClient":
        try:
            await asyncio.wait_for(
                self._connect_and_handshake(), timeout=self._policy.connect_timeout
            )
        except asyncio.TimeoutError:
            await self._teardown_failed_connect()
            raise RpcTimeoutError("connect", self._policy.connect_timeout) from None
        except BaseException:
            await self._teardown_failed_connect()
            raise
        return self

    async def _teardown_failed_connect(self) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
            self._recv_task = None
        with contextlib.suppress(Exception):
            await self._transport.disconnect()

    async def _connect_and_handshake(self) -> None:
        self.initialize_result = None
        await self._transport.connect()
        self._recv_task = asyncio.create_task(self._recv_loop())
        if self._gui_handshake:
            from .generated.gui_v2 import (
                GUI_PROTOCOL_VERSION,
                InitializeParams,
                InitializeResult,
            )

            initialize = InitializeParams.model_validate(
                {
                    "protocol_version": GUI_PROTOCOL_VERSION,
                    "identity": self._client_info,
                    "platform": {
                        "os": platform.system().lower(),
                        "arch": platform.machine().lower(),
                    },
                    "capabilities": self._capabilities,
                },
                strict=True,
            )
            # The overall connect_timeout budget (enforced by __aenter__)
            # covers the handshake; disable the per-call deadline here.
            result = await self.request(
                "initialize", initialize.model_dump(), timeout=None
            )
            server_version = (
                result.get("protocol_version") if isinstance(result, dict) else None
            )
            if not supports_gui_version(server_version, GUI_PROTOCOL_VERSION):
                raise RpcError(
                    -32012,
                    "Incompatible GUI protocol. Update the client and server together.",
                    {
                        "kind": "protocol_version_mismatch",
                        "client_version": GUI_PROTOCOL_VERSION,
                        "server_version": server_version,
                    },
                )
            try:
                self.initialize_result = InitializeResult.model_validate(
                    result, strict=True, extra="ignore"
                )
            except ValidationError as exc:
                raise RpcError(
                    -32602,
                    "Invalid GUI initialize response. Update the client and server together.",
                    {"kind": "invalid_initialize_result"},
                ) from exc
            await self.notify("initialized")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._recv_task:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
        await self._transport.disconnect()
        self._handle_closed(
            RpcConnectionClosedError("Connection closed by client", permanent=True)
        )

    def _handle_closed(self, error: RpcConnectionClosedError) -> None:
        """Reject all pending calls and fire disconnect callbacks (once)."""
        if self._closed:
            return
        self._closed = True
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(error)
        callbacks = list(self._disconnect_callbacks)
        for callback in callbacks:
            with contextlib.suppress(Exception):
                callback(error)

    async def _recv_loop(self) -> None:
        from .transport import TransportClosedError, TransportDecodeError

        close_error = RpcConnectionClosedError("Connection lost")
        cancelled = False
        try:
            while self._transport.is_connected():
                try:
                    data = await self._transport.receive_json()
                except TransportClosedError as exc:
                    close_error = RpcConnectionClosedError(
                        str(exc) or "Connection lost"
                    )
                    break
                except TransportDecodeError:
                    continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not self._transport.is_connected():
                        close_error = RpcConnectionClosedError(
                            str(exc) or "Connection lost"
                        )
                        break
                    continue

                if "method" in data:
                    if self._gui_handshake and self.initialize_result is None:
                        continue
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
        except asyncio.CancelledError:
            # __aexit__ rejects pending calls after transport teardown.
            cancelled = True
            raise
        except Exception as exc:
            close_error = RpcConnectionClosedError(str(exc) or "Connection lost")
        finally:
            if not cancelled:
                self._handle_closed(close_error)

    async def call(self, method: str, **params) -> Any:
        """Issue a JSON-RPC call with the policy's default deadline."""
        return await self.request(method, params)

    async def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: float | None | _Unset = _UNSET,
    ) -> Any:
        """Issue a JSON-RPC call with an explicit deadline.

        Args:
            method: JSON-RPC method name.
            params: Named parameters (``{}`` when omitted).
            timeout: Deadline in seconds. Omit for the policy default;
                pass ``None`` to wait indefinitely (long-running RPCs the
                caller cancels via task cancellation instead).
        """
        if isinstance(timeout, _Unset):
            timeout = self._policy.rpc_timeout
        if self._closed or not self._transport.is_connected():
            raise RpcConnectionClosedError("Transport is not connected")
        self._id += 1
        call_id = str(self._id)
        payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": method,
            "params": params or {},
        }
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut
        try:
            await self._transport.send_json(payload)
            if timeout is None:
                return await fut
            try:
                return await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                raise RpcTimeoutError(method, timeout) from None
        finally:
            # Timeout, caller cancellation, or send failure: drop the
            # pending entry so nothing is left dangling.
            self._pending.pop(call_id, None)

    async def notify(self, method: str, **params) -> None:
        if self._closed or not self._transport.is_connected():
            raise RpcConnectionClosedError("Transport is not connected")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._transport.send_json(payload)
