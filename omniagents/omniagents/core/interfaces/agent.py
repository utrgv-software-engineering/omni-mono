"""
Unified agent interface for both local and remote agents.

This module provides a protocol that defines the common interface
for all agents, whether they execute locally through RPC or remotely
via WebSocket.
"""

from typing import Protocol, Any, List, Dict, Optional, runtime_checkable
from abc import abstractmethod


@runtime_checkable
class UnifiedAgent(Protocol):
    """
    Unified interface for both local and remote agents.

    This protocol ensures that both local agents (running through in-memory RPC)
    and remote agents (running through WebSocket RPC) provide the same interface
    to the CLI and Flet runners.
    """

    # Required properties for agent compatibility
    name: str
    tools: List[Any]
    instructions: str
    model: str
    model_settings: Dict[str, Any]

    # Optional properties
    handoffs: List[Any]
    mcp_servers: Optional[Any]

    # Type hints for Runner compatibility
    input_type: type
    output_type: type

    def run_streamed(
        self, messages: List[Dict[str, str]], *, run_config: Optional[Any] = None
    ) -> Any:
        """
        Run agent with streaming output.

        Returns an object with a .stream_events() method that yields StreamEvent objects.
        This matches the interface expected by CLI and Flet runners.
        """
        ...

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List available sessions."""
        ...

    async def archive_session(self, session_id: str) -> None:
        """Archive a session."""
        ...

    def get_all_tools(self) -> List[Any]:
        """Get all available tools."""
        ...

    def reset_run_state(self) -> None:
        """Reset agent state (SafeAgent compatibility)."""
        ...


class UnifiedAgentBase:
    """Base class providing common functionality for unified agents."""

    # Default type hints for Runner compatibility
    input_type = str
    output_type = str

    # Default empty collections
    handoffs: List[Any] = []
    mcp_servers: Optional[Any] = None

    def __init__(self):
        # Initialize required properties with defaults
        self.name = "UnifiedAgent"
        self.tools = []
        self.instructions = ""
        self.model = "gpt-4"
        self.model_settings = {}

    def get_all_tools(self) -> List[Any]:
        """Default implementation returns tools list."""
        return self.tools

    def reset_run_state(self) -> None:
        """Default implementation does nothing."""
        pass
