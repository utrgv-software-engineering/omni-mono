"""Server-side JSON-RPC implementation for FastAPI.

Adds debug logging for all calls and notifications when OMNIAGENTS_SERVER_DEBUG
is set or omniagents.core.debug.Debug is enabled.
"""

from typing import Any, Dict, Optional
import inspect
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .base import RpcMethodsBase
from omniagents.core.debug import Debug

DEBUG_SERVER = os.getenv("OMNIAGENTS_SERVER_DEBUG", "0").lower() in ("1", "true")


class JsonRpcChannel:
    """Simple helper used by the server to send notifications."""

    def __init__(self, transport: "Transport") -> None:
        self.transport = transport

    async def async_call(
        self, method: str, args: Optional[Dict[str, Any]] = None
    ) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": args or {}}
        if DEBUG_SERVER or Debug.enabled():
            try:
                print(f"[srv]   → {method} payload={args or {}}")
            except Exception:
                pass
        await self.transport.send_json(payload)

    async def send_result(self, call_id: Any, result: Any) -> None:
        payload = {"jsonrpc": "2.0", "id": call_id, "result": result}
        if DEBUG_SERVER or Debug.enabled():
            try:
                print(f"[srv]   ← result id={call_id} result={result}")
            except Exception:
                pass
        await self.transport.send_json(payload)

    async def send_error(
        self,
        call_id: Any,
        code: int,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        msg_text = str(message) if message is not None else ""
        if not msg_text:
            msg_text = ""
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": call_id,
            "error": {"code": int(code), "message": msg_text},
        }
        if data is not None:
            payload["error"]["data"] = data
        if DEBUG_SERVER or Debug.enabled():
            try:
                print(
                    f"[srv]   ← error id={call_id} code={code} message={message} data={data}"
                )
            except Exception:
                pass
        await self.transport.send_json(payload)

    # Backward compatibility property
    @property
    def websocket(self) -> Any:
        """Backward compatibility for code that accesses channel.websocket."""
        # Return the underlying websocket if it's a ServerWebSocketTransport
        if hasattr(self.transport, "websocket"):
            return self.transport.websocket
        return None


class JsonRpcEndpoint:
    """Minimal JSON-RPC 2.0 WebSocket endpoint."""

    def __init__(self, methods: RpcMethodsBase) -> None:
        self.methods = methods

    def register_route(self, app: FastAPI, path: str, dependencies=None) -> None:
        dependencies = dependencies or []

        async def handler(websocket: WebSocket):
            from .transport.websocket import ServerWebSocketTransport
            from .transport import (
                TransportClosedError,
                TransportDecodeError,
                TransportError,
            )

            transport = ServerWebSocketTransport(websocket)
            await transport.connect()

            channel = JsonRpcChannel(transport)
            self.methods.channel = channel

            try:
                while transport.is_connected():
                    try:
                        data = await transport.receive_json()
                    except TransportClosedError:
                        break
                    except WebSocketDisconnect:
                        break
                    except TransportDecodeError as exc:
                        await self._handle_parse_error(channel, exc)
                        continue
                    except TransportError:
                        break
                    except Exception as exc:
                        await self._handle_internal_error(channel, None, exc)
                        continue

                    await self._process_message(channel, data)
            finally:
                try:
                    if getattr(self.methods, "channel", None) is channel:
                        self.methods.channel = None
                except AttributeError:
                    pass
                await transport.disconnect()

        app.add_api_websocket_route(path, handler, dependencies=dependencies)

    async def _handle_parse_error(
        self, channel: JsonRpcChannel, exc: Exception
    ) -> None:
        await channel.send_error(
            None,
            -32700,
            "Parse error",
            {"message": str(exc)},
        )

    async def _handle_internal_error(
        self,
        channel: JsonRpcChannel,
        call_id: Any,
        exc: Exception,
        method: Optional[str] = None,
    ) -> None:
        if call_id is None:
            return
        data: Dict[str, Any] = {"type": exc.__class__.__name__, "message": str(exc)}
        if method is not None:
            data["method"] = method
        message_text = str(exc).strip()
        if not message_text:
            message_text = "Internal error"
        await channel.send_error(
            call_id,
            -32603,
            message_text,
            data,
        )

    async def _process_message(self, channel: JsonRpcChannel, data: Any) -> None:
        if not isinstance(data, dict):
            await channel.send_error(
                None,
                -32600,
                "Invalid Request",
                {"message": "Request must be an object"},
            )
            return

        call_id = data.get("id")
        version = data.get("jsonrpc")
        method = data.get("method")
        params = data.get("params") if "params" in data else data.get("args")

        if DEBUG_SERVER or Debug.enabled():
            try:
                if method:
                    print(f"[srv] ⇐ call id={call_id} method={method} params={params}")
                else:
                    print(f"[srv] ⇐ message id={call_id} data={data}")
            except Exception:
                pass

        if version != "2.0":
            await channel.send_error(
                call_id, -32600, "Invalid Request", {"message": "jsonrpc must be '2.0'"}
            )
            return

        if not isinstance(method, str) or not method:
            await channel.send_error(
                call_id,
                -32600,
                "Invalid Request",
                {"message": "method must be a string"},
            )
            return

        if params is None:
            params = {}

        if not isinstance(params, dict):
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32602,
                    "Invalid params",
                    {"message": "params must be an object"},
                )
            return

        func = getattr(self.methods, method, None)
        if not callable(func):
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32601,
                    "Method not found",
                    {"method": method},
                )
            return

        try:
            signature = inspect.signature(func)
            signature.bind(**params)
        except (TypeError, ValueError) as exc:
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32602,
                    "Invalid params",
                    {"message": str(exc), "method": method},
                )
            return

        try:
            try:
                self.methods.channel = channel
            except AttributeError:
                pass
            result = func(**params)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            await self._handle_internal_error(channel, call_id, exc, method)
            return

        if call_id is not None:
            await channel.send_result(call_id, result)
