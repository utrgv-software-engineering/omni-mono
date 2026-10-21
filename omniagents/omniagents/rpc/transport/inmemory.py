"""In-memory transport implementation."""

import asyncio
import json
from typing import Dict, Any, Optional

from . import (
    BaseTransport,
    BoundedTransportQueue,
    QueueClosedError,
    TransportClosedError,
    TransportError,
    TransportOverloadedError,
)


class InMemoryTransport(BaseTransport):
    """
    In-memory transport for local agent execution.

    This transport uses asyncio queues to simulate bidirectional
    communication without network overhead. It must be used in pairs
    created by the create_pair() class method.
    """

    def __init__(
        self,
        *,
        max_messages: int = 256,
        max_bytes: int = 4 * 1024 * 1024,
    ):
        super().__init__()
        self._receive_queue = BoundedTransportQueue[Dict[str, Any]](
            resource="inmemory_transport",
            max_messages=max_messages,
            max_bytes=max_bytes,
        )
        self._send_queue: Optional[BoundedTransportQueue[Dict[str, Any]]] = None
        self._pair: Optional["InMemoryTransport"] = None

    @classmethod
    def create_pair(
        cls,
        *,
        max_messages: int = 256,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> tuple["InMemoryTransport", "InMemoryTransport"]:
        """
        Create a connected pair of in-memory transports.

        Returns:
            Tuple of (client_transport, server_transport) that are connected
            to each other.
        """
        client = cls(max_messages=max_messages, max_bytes=max_bytes)
        server = cls(max_messages=max_messages, max_bytes=max_bytes)

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
        if self._send_queue:
            await self._send_queue.close(
                TransportClosedError("Paired transport disconnected")
            )

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data to paired transport."""
        self._ensure_connected()
        if not self._send_queue:
            raise TransportError("No send queue configured")

        try:
            encoded_size = len(
                json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
            )
            await self._send_queue.put(data, size=encoded_size)
        except Exception as e:
            if isinstance(
                e, (TransportError, QueueClosedError, TransportOverloadedError)
            ):
                raise
            raise TransportError(f"Failed to send data: {e}")

    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from paired transport."""
        self._ensure_connected()

        try:
            return await self._receive_queue.get()
        except asyncio.CancelledError:
            raise
        except TransportClosedError:
            self._connected = False
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
