"""Evaluation environment base class for managing external state during evaluations.

This module provides the EvalEnvironment abstract base class that enables:
- Fixture loading before scenarios
- State cleanup after scenarios
- Context injection into tool calls
- Database/API verification for measures
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ["EvalEnvironment", "EnvironmentContext", "NoOpEnvironment"]


@dataclass
class EnvironmentContext:
    """Context returned by environment setup, passed to measures and teardown."""

    session_id: str
    """Unique identifier for this evaluation session."""

    data: Dict[str, Any] = field(default_factory=dict)
    """Arbitrary data from setup (e.g., resource IDs, API keys, etc.)."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the context data."""
        return self.data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data


class EvalEnvironment(ABC):
    """Base class for evaluation environments with external state.

    Subclass this to create environments that:
    - Load database fixtures before scenarios
    - Provide verification APIs for measures
    - Clean up state after scenarios

    Example:
        class HotelEvalEnvironment(EvalEnvironment):
            async def setup(self, scenario):
                result = await self.api.load_fixture(scenario.get("fixture"))
                return EnvironmentContext(
                    session_id=result["session_id"],
                    data={"hotel_id": result["hotel_id"]}
                )

            async def teardown(self, context):
                await self.api.cleanup(context.session_id)

            async def verify(self, context, expected):
                return await self.api.verify(context.session_id, expected)
    """

    @abstractmethod
    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        """Initialize environment before a scenario runs.

        Args:
            scenario: The scenario configuration dict, including fixture name,
                     user_persona, expected outcomes, etc.

        Returns:
            EnvironmentContext with session_id and any data needed by
            tools, measures, or teardown.
        """
        pass

    @abstractmethod
    async def teardown(self, context: EnvironmentContext) -> None:
        """Clean up environment after a scenario completes.

        Args:
            context: The context returned by setup().
        """
        pass

    async def verify(
        self, context: EnvironmentContext, expected: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify expected state against actual state.

        Override this to provide database/API verification for measures.

        Args:
            context: The context returned by setup().
            expected: Expected state specification from scenario.

        Returns:
            Dict with verification results, typically:
            {"success": bool, "results": {...details...}}
        """
        return {"success": True, "results": {}}

    def get_tool_context(self, context: EnvironmentContext) -> Dict[str, Any]:
        """Get context values to inject into tool calls.

        Override this if tools need values from the environment context
        (e.g., resource IDs, session tokens, etc.)

        Args:
            context: The context returned by setup().

        Returns:
            Dict of values to merge into tool call arguments.
        """
        return dict(context.data)

    def get_env_vars(self, context: EnvironmentContext) -> Dict[str, str]:
        """Get environment variables to set during scenario execution.

        Override this to specify env vars that tools may read directly.
        These are set before the scenario runs and should be cleaned up
        in teardown if needed.

        Args:
            context: The context returned by setup().

        Returns:
            Dict mapping env var names to values.
        """
        return {}


class NoOpEnvironment(EvalEnvironment):
    """A no-op environment for scenarios without external state."""

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        import uuid

        return EnvironmentContext(session_id=str(uuid.uuid4()))

    async def teardown(self, context: EnvironmentContext) -> None:
        pass
