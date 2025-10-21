"""Server-side JSON-RPC implementation for FastAPI.

Adds debug logging for all calls and notifications when OMNIAGENTS_SERVER_DEBUG
is set or omniagents.core.debug.Debug is enabled.
"""

from typing import Any, Dict, Optional
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
            from .transport import TransportClosedError

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

                    call_id = data.get("id")
                    method = data.get("method")
                    params = data.get("params") or data.get("args") or {}
                    if DEBUG_SERVER or Debug.enabled():
                        try:
                            if method:
                                print(
                                    f"[srv] ⇐ call id={call_id} method={method} params={params}"
                                )
                            else:
                                print(f"[srv] ⇐ message id={call_id} data={data}")
                        except Exception:
                            pass
                    if method:
                        # Ensure the RPC methods see the channel for this connection
                        try:
                            self.methods.channel = channel
                        except AttributeError:
                            pass
                        try:
                            func = getattr(self.methods, method)
                            result = await func(**params)
                            if call_id is not None:
                                await channel.send_result(call_id, result)
                        except Exception as e:
                            # Return JSON-RPC error response for method failures
                            if call_id is not None:
                                error_payload = {
                                    "jsonrpc": "2.0",
                                    "id": call_id,
                                    "error": {
                                        "code": -32603,  # Internal error
                                        "message": str(e),
                                        "data": {
                                            "type": e.__class__.__name__,
                                            "method": method,
                                        },
                                    },
                                }
                                if DEBUG_SERVER or Debug.enabled():
                                    try:
                                        print(
                                            f"[srv]   ← error id={call_id} error={error_payload['error']}"
                                        )
                                    except Exception:
                                        pass
                                await transport.send_json(error_payload)
                            # On notifications (no id), just swallow the error
            finally:
                await transport.disconnect()

        app.add_api_websocket_route(path, handler, dependencies=dependencies)
