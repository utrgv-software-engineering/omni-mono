"""Backend interface for OmniAgents UI implementations."""

from typing import Protocol, runtime_checkable, Optional, Dict, Any, Callable, List
from abc import ABC, abstractmethod
from omniagents.core.agents.specs import AgentSpec


@runtime_checkable
class Backend(Protocol):
    """Protocol defining the minimal interface for a UI backend."""

    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the backend with the given agent specification.

        Args:
            spec: The agent specification
            **kwargs: Additional backend-specific arguments
        """
        ...


class UIBackend(ABC):
    """Abstract base class for UI backends with full functionality."""

    @abstractmethod
    async def initialize(self, spec: AgentSpec) -> None:
        """Initialize the UI backend with the agent specification."""
        pass

    @abstractmethod
    async def run(self) -> None:
        """Start the UI backend (blocking call)."""
        pass

    @abstractmethod
    async def display_user_message(self, message: str) -> None:
        """Display a user message in the UI."""
        pass

    @abstractmethod
    async def display_assistant_message(
        self, message: str, streaming: bool = False
    ) -> None:
        """Display an assistant message (may be streamed)."""
        pass

    @abstractmethod
    async def display_tool_call(
        self, tool_name: str, args: str, call_id: Optional[str] = None
    ) -> None:
        """Display a tool call in the UI."""
        pass

    @abstractmethod
    async def display_tool_result(
        self, result: str, call_id: Optional[str] = None
    ) -> None:
        """Display a tool result in the UI."""
        pass

    @abstractmethod
    async def request_tool_approval(
        self, tool_name: str, args: str
    ) -> tuple[bool, bool, Optional[str]]:
        """Request user approval for a tool call.

        Returns:
            Tuple of (approved, always_approve, error_msg)
        """
        pass

    @abstractmethod
    async def get_user_input(self) -> str:
        """Get input from the user (blocking until input is available)."""
        pass

    @abstractmethod
    async def display_error(self, error: str) -> None:
        """Display an error message."""
        pass

    @abstractmethod
    async def display_artifact(
        self, title: str, content: str, mode: str = "markdown"
    ) -> None:
        """Display an artifact (code, document, etc.)."""
        pass

    @abstractmethod
    async def update_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Show settings UI and return updated settings."""
        pass

    @abstractmethod
    async def store_setting(self, key: str, value: Any) -> None:
        """Store a setting persistently."""
        pass

    @abstractmethod
    async def get_setting(self, key: str, default: Any = None) -> Any:
        """Retrieve a stored setting."""
        pass


class BackendFactory:
    """Factory for creating backend instances."""

    _backends: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, backend_class: type) -> None:
        """Register a backend class."""
        cls._backends[name] = backend_class

    @classmethod
    def create(cls, name: str, spec: AgentSpec, **kwargs) -> Backend:
        """Create a backend instance."""
        if name not in cls._backends:
            raise ValueError(f"Unknown backend: {name}")
        backend_class = cls._backends[name]
        return backend_class(spec, **kwargs)
