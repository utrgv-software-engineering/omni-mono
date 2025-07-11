"""Backend interface for OmniAgents UI implementations."""

from typing import Protocol, runtime_checkable
from omniagents.core.specs import AgentSpec


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