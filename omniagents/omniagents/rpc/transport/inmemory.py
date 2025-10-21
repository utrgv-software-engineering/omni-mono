"""In-memory transport implementation."""

import asyncio
from typing import Dict, Any, Optional

from . import BaseTransport, TransportError, TransportClosedError


class InMemoryTransport(BaseTransport):
    """
    In-memory transport for local agent execution.

    This transport uses asyncio queues to simulate bidirectional
    communication without network overhead. It must be used in pairs
    created by the create_pair() class method.
    """

    def __init__(self):
        super().__init__()
        # Each transport has its own receive queue
        self._receive_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        # Reference to paired transport's receive queue (set by create_pair)
        self._send_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None
        self._pair: Optional["InMemoryTransport"] = None

    @classmethod
    def create_pair(cls) -> tuple["InMemoryTransport", "InMemoryTransport"]:
        """
        Create a connected pair of in-memory transports.

        Returns:
            Tuple of (client_transport, server_transport) that are connected
            to each other.
        """
        client = cls()
        server = cls()

        # Cross-wire the transports
        client._send_queue = server._receive_queue
        server._send_queue = client._receive_queue
        client._pair = server
        server._pair = client

        return client, server

    async def connect(self) -> None:
        """Mark transport as connected."""
        if not self._send_queue or not self._pair:
            raise TransportError("InMemoryTransport must be created with create_pair()")
        self._connected = True

    async def disconnect(self) -> None:
        """Mark transport as disconnected and notify pair."""
        self._connected = False
        # Put a sentinel value to unblock any waiting receive on the PAIRED transport
        if self._send_queue:
            try:
                await self._send_queue.put({"__disconnect__": True})
            except Exception:
                pass

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data to paired transport."""
        self._ensure_connected()
        if not self._send_queue:
            raise TransportError("No send queue configured")

        try:
            await self._send_queue.put(data)
        except Exception as e:
            raise TransportError(f"Failed to send data: {e}")

    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from paired transport."""
        self._ensure_connected()

        try:
            data = await self._receive_queue.get()
            # Check for disconnect sentinel
            if isinstance(data, dict) and data.get("__disconnect__"):
                self._connected = False
                raise TransportClosedError("Paired transport disconnected")
            return data
        except asyncio.CancelledError:
            raise
        except TransportClosedError:
            raise
        except Exception as e:
            raise TransportError(f"Failed to receive data: {e}")

    def is_connected(self) -> bool:
        """Check if transport is connected and pair is available."""
        return (
            super().is_connected()
            and self._pair is not None
            and self._send_queue is not None
        )
