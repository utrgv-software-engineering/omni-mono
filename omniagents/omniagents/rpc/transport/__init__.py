"""
Transport abstraction for JSON-RPC communication.

This module provides a unified interface for different transport mechanisms
(WebSocket, in-memory, etc.) to enable both local and remote agent execution
with the same RPC interface.
"""

from typing import Protocol, Any, Dict, runtime_checkable
from abc import ABC, abstractmethod


class TransportError(Exception):
    """Base exception for transport-related errors."""
    pass


class TransportClosedError(TransportError):
    """Raised when attempting to use a closed transport."""
    pass


@runtime_checkable
class Transport(Protocol):
    """
    Abstract transport interface for JSON-RPC communication.
    
    This protocol defines the minimal interface that all transport
    implementations must provide to support bidirectional JSON-RPC
    communication.
    """
    
    async def connect(self) -> None:
        """Establish connection."""
        ...
    
    async def disconnect(self) -> None:
        """Close connection gracefully."""
        ...
    
    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data through transport."""
        ...
    
    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from transport."""
        ...
    
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        ...


class BaseTransport(ABC):
    """Base class for transport implementations with common functionality."""
    
    def __init__(self):
        self._connected = False
    
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        return self._connected
    
    def _ensure_connected(self) -> None:
        """Raise error if not connected."""
        if not self._connected:
            raise TransportClosedError("Transport is not connected")
    
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection gracefully."""
        pass
    
    @abstractmethod
    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON data through transport."""
        pass
    
    @abstractmethod
    async def receive_json(self) -> Dict[str, Any]:
        """Receive JSON data from transport."""
        pass