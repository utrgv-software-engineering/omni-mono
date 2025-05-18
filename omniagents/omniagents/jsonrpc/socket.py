from __future__ import annotations

from typing import Any, Dict

try:
    from fastapi_websocket_rpc.simplewebsocket import JsonSerializingWebSocket
except Exception:  # pragma: no cover - test stub lacks this
    class JsonSerializingWebSocket:
        """Minimal JSON websocket used during tests when library is stubbed."""

        def __init__(self, websocket):
            self._websocket = websocket

        async def connect(self, uri: str, **kwargs):  # pragma: no cover - tests stub
            await self._websocket.connect(uri, **kwargs)

        async def send(self, msg):
            await self._websocket.send_json(msg)

        async def recv(self):
            return await self._websocket.receive_json()

        async def close(self, code: int = 1000):
            await self._websocket.close(code)


class JsonRpcWebSocket(JsonSerializingWebSocket):
    """Adapter translating between JSON-RPC 2.0 messages and RpcMessage objects."""

    async def send(self, msg: Any):
        if isinstance(msg, dict) and ("request" in msg or "response" in msg):
            if msg.get("request") is not None:
                req = msg["request"]
                payload: Dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "method": req.get("method"),
                    "params": req.get("arguments", {}),
                }
                if req.get("call_id") is not None:
                    payload["id"] = req["call_id"]
                await super().send(payload)
                return
            if msg.get("response") is not None:
                resp = msg["response"]
                payload: Dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "id": resp.get("call_id"),
                    "result": resp.get("result"),
                }
                await super().send(payload)
                return
        await super().send(msg)

    async def recv(self) -> Any:
        msg = await super().recv()
        if isinstance(msg, dict) and msg.get("jsonrpc") == "2.0":
            if "method" in msg:
                return {
                    "request": {
                        "method": msg.get("method"),
                        "arguments": msg.get("params", {}),
                        "call_id": msg.get("id"),
                    }
                }
            if "result" in msg or "error" in msg:
                return {
                    "response": {
                        "call_id": msg.get("id"),
                        "result": msg.get("result"),
                        "result_type": type(msg.get("result")).__name__,
                    }
                }
        return msg

