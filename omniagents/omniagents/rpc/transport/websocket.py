"""WebSocket transport implementations."""

import json
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import websockets

from fastapi import WebSocket, WebSocketDisconnect

from . import BaseTransport, TransportError, TransportClosedError, TransportDecodeError
from ..lifecycle import DEFAULT_LIFECYCLE_POLICY, LifecyclePolicy


class ClientWebSocketTransport(BaseTransport):
    """WebSocket transport for client-side connections using websockets library.

    Credentials travel in headers (``Authorization: Bearer …`` via *token*
    or explicit *headers*), never in the URL — see ``omniagents.rpc.auth``.

    Liveness (ping/pong cadence) and the open timeout come from the shared
    :class:`~omniagents.rpc.lifecycle.LifecyclePolicy` so every Python
    client probes the link identically.
    """

    def __init__(
        self,
        url: str,
        *,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY,
    ):
        super().__init__()
        self.url = url
        self._headers: Dict[str, str] = dict(headers or {})
        if token:
            self._headers.setdefault("Authorization", f"Bearer {token}")
        self._policy = policy
        self._ws: Optional["websockets.WebSocketClientProtocol"] = None

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        if self._connected:
            return

        try:
            import websockets

            headers = self._headers or None
            lifecycle_kwargs = {
                "open_timeout": self._policy.connect_timeout,
                "ping_interval": self._policy.ping_interval,
                "ping_timeout": self._policy.ping_timeout,
            }
            try:
                self._ws = await websockets.connect(
                    self.url, additional_headers=headers, **lifecycle_kwargs
                )
            except TypeError:
                # Older websockets releases use ``extra_headers``.
                self._ws = await websockets.connect(
                    self.url, extra_headers=headers, **lifecycle_kwargs
                )
            self._connected = True
        except Exception as e:
            raise TransportError(f"Failed to connect to {self.url}: {e}")

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._ws and self._connected:
            await self._ws.close()
            self._ws = None
            self._connected = False

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data through WebSocket."""
        self._ensure_connected()
        if not self._ws:
            raise TransportClosedError("WebSocket is None")

        try:
            await self._ws.send(json.dumps(data))
        except Exception as e:
            self._connected = False
            raise TransportError(f"Failed to send data: {e}")

    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from WebSocket."""
        self._ensure_connected()
        if not self._ws:
            raise TransportClosedError("WebSocket is None")

        try:
            msg = await self._ws.recv()
            return json.loads(msg)
        except json.JSONDecodeError as e:
            raise TransportDecodeError(msg, e)
        except Exception as e:
            self._connected = False
            raise TransportError(f"Failed to receive data: {e}")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        # The websockets library doesn't have an 'open' attribute
        # Instead, check if the websocket exists and is not closed
        if not super().is_connected() or self._ws is None:
            return False
        # Check if websocket is in a connected state
        try:
            # The websockets library will raise an exception if the connection is closed
            return not self._ws.closed
        except AttributeError:
            # Fallback for different websockets versions
            return True


class ServerWebSocketTransport(BaseTransport):
    """WebSocket transport wrapping FastAPI WebSocket for server-side."""

    def __init__(self, websocket: WebSocket):
        super().__init__()
        self.websocket = websocket

    async def connect(self) -> None:
        """Accept WebSocket connection."""
        if self._connected:
            return

        try:
            await self.websocket.accept()
            self._connected = True
        except Exception as e:
            raise TransportError(f"Failed to accept WebSocket: {e}")

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self._connected:
            try:
                await self.websocket.close()
            except Exception:
                # Ignore errors during close
                pass
            finally:
                self._connected = False

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data through WebSocket."""
        self._ensure_connected()

        try:
            await self.websocket.send_json(data)
        except Exception as e:
            self._connected = False
            raise TransportError(f"Failed to send data: {e}")

    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from WebSocket."""
        self._ensure_connected()

        try:
            msg = await self.websocket.receive_text()
        except WebSocketDisconnect:
            self._connected = False
            raise TransportClosedError("WebSocket disconnected")
        except Exception as e:
            self._connected = False
            raise TransportError(f"Failed to receive data: {e}")

        try:
            return json.loads(msg)
        except json.JSONDecodeError as e:
            self._connected = False
            raise TransportDecodeError(msg, e)
        except Exception as e:
            self._connected = False
            raise TransportError(f"Failed to receive data: {e}")
