"""The terminal domain's training environment.

This is the ONLY environment code the domain needs: a thin subclass of the
framework's general ``SandboxTrainingEnvironment`` that adds the agent's tool(s)
as methods (TRL turns each public method into a tool). The base class handles
the per-rollout Docker session, the workspace binding, task setup, and the
verifier. Swapping domains means swapping the tool method(s) here.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make sibling packages (tools/) importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniagents.core.training.sandbox_env import SandboxTrainingEnvironment
from tools.terminal_tools import run_bash


class TerminalSandboxEnvironment(SandboxTrainingEnvironment):
    """Per-rollout terminal sandbox exposing a single `bash` tool."""

    async def bash(self, command: str, timeout: int = 30) -> str:
        """Run a shell command in the terminal and return its output.

        Args:
            command: The shell command to execute.
            timeout: Maximum number of seconds to wait for the command.
        """
        # async so TRL gathers bash calls ACROSS rollouts -> their container
        # execs run concurrently (the work happens on the shared pool loop).
        return await asyncio.wrap_future(
            self._pool.submit(self._abash(command, timeout))
        )

    async def _abash(self, command: str, timeout: int) -> str:
        self._bind_workspace()  # route omniagents.workspace.* into this container
        return await run_bash(command, timeout)
