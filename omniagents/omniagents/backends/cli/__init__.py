"""CLI backend for OmniAgents."""

import asyncio
from omniagents.core.backend import Backend
from omniagents.core.specs import AgentSpec
from .runner import run_cli


class CLIBackend:
    """CLI backend implementation."""
    
    def run(self, spec: AgentSpec, **kwargs) -> None:
        """Run the CLI backend with the given agent specification.
        
        Args:
            spec: The agent specification
            **kwargs: Additional backend-specific arguments (e.g., args from argparse)
        """
        # Extract CLI-specific kwargs
        config_path = kwargs.get('config_path')
        args = kwargs.get('args')
        
        # Run the async CLI loop
        asyncio.run(run_cli(
            config_path=config_path,
            args=args,
            spec=spec
        ))


__all__ = ['CLIBackend']