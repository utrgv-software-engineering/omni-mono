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

    async def _path_exists(self, context: EnvironmentContext, rel_path: str) -> bool:
        """Whether *rel_path* (relative to the workspace root) exists.

        Default: host filesystem under ``context.data["workspace_root"]``.
        Environments whose files live elsewhere (e.g. inside a container)
        override this and :meth:`_read_file`; the ``verify`` matching
        logic is shared.
        """
        import os

        root = context.get("workspace_root", "")
        return bool(root) and os.path.exists(os.path.join(root, rel_path))

    async def _read_file(
        self, context: EnvironmentContext, rel_path: str
    ) -> Optional[str]:
        """Content of *rel_path* relative to the workspace root, or None.

        Default: host filesystem read. See :meth:`_path_exists`.
        """
        import os

        root = context.get("workspace_root", "")
        path = os.path.join(root, rel_path)
        try:
            with open(path, errors="replace") as f:
                return f.read()
        except OSError:
            return None

    async def verify(
        self, context: EnvironmentContext, expected: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Verify expected filesystem state against the workspace.

        ``expected`` maps workspace-relative paths to expectation dicts::

            {
                "src/main.py": {
                    "exists": True,
                    "contains": ["def hello"],
                    "not_contains": ["import os"],
                },
                "secrets.txt": {"exists": False},
            }

        The matching logic is shared; environments that keep files
        somewhere other than the host workspace override just
        :meth:`_path_exists` / :meth:`_read_file`. Environments with no
        ``workspace_root`` in their context keep the historical no-op
        result. Override wholesale for non-filesystem verification
        (databases, APIs).

        Returns:
            Dict with verification results:
            {"success": bool, "results": {rel_path: {"matched": bool, ...}}}
        """
        if not context or not context.get("workspace_root"):
            return {"success": True, "results": {}}

        results: Dict[str, Any] = {}
        success = True

        for rel_path, expectations in (expected or {}).items():
            expect_exists = expectations.get("exists", True)
            exists = await self._path_exists(context, rel_path)

            if not expect_exists:
                if exists:
                    results[rel_path] = {
                        "matched": False,
                        "reason": "file should not exist",
                    }
                    success = False
                else:
                    results[rel_path] = {"matched": True}
                continue

            if not exists:
                results[rel_path] = {"matched": False, "reason": "file not found"}
                success = False
                continue

            content: Optional[str] = None
            if expectations.get("contains") or expectations.get("not_contains"):
                content = await self._read_file(context, rel_path)
            content = content or ""

            reason: Optional[str] = None
            for pattern in expectations.get("contains", []):
                if pattern not in content:
                    reason = f"missing pattern: {pattern!r}"
                    break
            if reason is None:
                for pattern in expectations.get("not_contains", []):
                    if pattern in content:
                        reason = f"forbidden pattern found: {pattern!r}"
                        break

            if reason is None:
                results[rel_path] = {"matched": True}
            else:
                results[rel_path] = {"matched": False, "reason": reason}
                success = False

        return {"success": success, "results": results}

    def run_command(
        self,
        context: EnvironmentContext,
        command: str,
        timeout: int = 120,
    ) -> tuple:
        """Run a shell command in the scenario's workspace.

        Returns ``(exit_code, stdout, stderr)``. The default runs the
        command as a host subprocess in ``context.data["workspace_root"]``;
        environments that execute elsewhere (e.g. inside a container)
        override this, so measures stay environment-agnostic — they just
        ask the environment to run a command and read the result.
        """
        import subprocess

        workspace = context.get("workspace_root") if context else None
        if not workspace:
            return (127, "", "no workspace_root in environment context")
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return (124, "", f"command timed out after {timeout}s")
        return (result.returncode, result.stdout or "", result.stderr or "")

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

    async def prepare_spec(
        self, spec: Any, context: Optional[EnvironmentContext]
    ) -> None:
        """Mutate the agent spec after load, before the agent is constructed.

        Called once per scenario by the runner, after ``setup()`` and after
        ``context.data`` has been merged into ``spec.context``, but before
        ``create_unified_agent(spec)``. This is the only hook that runs while
        the spec is still mutable, so it's where an environment wires runtime
        objects that cannot live in YAML — e.g. attaching a live
        ``SandboxSession`` to ``spec.sandbox_run_config`` and flipping
        ``spec.sandbox_use_sandbox_agent`` so the builder constructs a
        SandboxAgent whose tools exec inside the container.

        Default is a no-op, so environments that don't need it (and the
        ``NoOpEnvironment``) are unaffected.

        Args:
            spec: The loaded ``AgentSpec``, still mutable.
            context: The context returned by ``setup()`` (may be ``None``).
        """
        return None


class NoOpEnvironment(EvalEnvironment):
    """A no-op environment for scenarios without external state."""

    async def setup(self, scenario: Dict[str, Any]) -> EnvironmentContext:
        import uuid

        return EnvironmentContext(session_id=str(uuid.uuid4()))

    async def teardown(self, context: EnvironmentContext) -> None:
        pass
