"""Server-side JSON-RPC implementation for FastAPI."""

from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .base import RpcMethodsBase


class JsonRpcChannel:
    """Simple helper used by the server to send notifications."""

    def __init__(self, transport: "Transport") -> None:
        self.transport = transport

    async def async_call(self, method: str, args: Optional[Dict[str, Any]] = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": args or {}}
        await self.transport.send_json(payload)

    async def send_result(self, call_id: Any, result: Any) -> None:
        payload = {"jsonrpc": "2.0", "id": call_id, "result": result}
        await self.transport.send_json(payload)
    
    # Backward compatibility property
    @property 
    def websocket(self) -> Any:
        """Backward compatibility for code that accesses channel.websocket."""
        # Return the underlying websocket if it's a ServerWebSocketTransport
        if hasattr(self.transport, 'websocket'):
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
                    if method:
                        func = getattr(self.methods, method)
                        result = await func(**params)
                        if call_id is not None:
                            await channel.send_result(call_id, result)
            finally:
                await transport.disconnect()
                
        app.add_api_websocket_route(path, handler, dependencies=dependencies)