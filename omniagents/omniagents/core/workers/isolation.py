"""Pluggable isolation for worker sub-agents.

A worker can run directly in the parent's workspace (no isolation) or in
an isolated copy of it. *How* that copy is made is a product decision —
a coding product uses git worktrees, others may want a plain directory
copy or nothing at all — so the mechanics live behind a small strategy
protocol and a process-global registry.

The framework ships two strategies:

* ``none`` — the worker shares the parent workspace root.
* ``copy`` — the worker gets a ``cp -a`` copy of the parent workspace
  under the framework scratch area. Works for any directory workspace.

Products register richer strategies (e.g. git worktrees) with
:func:`register_isolation_strategy` and select a default via
``WorkerDefaults.default_isolation``.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable


class IsolationError(Exception):
    """Isolation setup failed. ``str(exc)`` is shown to the model."""


@dataclass
class WorkerWorkspace:
    """The workspace a worker runs in, as produced by a strategy.

    ``metadata`` is strategy-private but is merged into worker tool
    payloads so the orchestrating agent can see e.g. the worktree path
    and branch it must merge back.
    """

    root: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class IsolationStrategy(Protocol):
    name: str
    #: Message returned to the model when :meth:`ready` is False.
    unavailable_message: str

    async def ready(self, parent_workspace_root: str) -> bool:
        """Can this strategy isolate ``parent_workspace_root`` at all?"""
        ...

    async def prepare(
        self, *, worker_id: str, parent_workspace_root: str
    ) -> WorkerWorkspace:
        """Create the isolated workspace. Raises :class:`IsolationError`
        with a model-facing message on failure."""
        ...

    async def is_parent_dirty(self, parent_workspace_root: str) -> bool:
        """True when the parent has un-captured changes the worker would
        not see (drives the ``allow_dirty`` gate)."""
        ...

    async def cleanup(self, worker_id: str, workspace: WorkerWorkspace) -> bool:
        """Remove the isolated workspace. Returns True on success."""
        ...

    def task_preamble(self, workspace: WorkerWorkspace) -> Optional[str]:
        """Text prepended to the worker's task so it knows where it is
        working and what to do with its changes. None for no preamble."""
        ...


class NoneIsolation:
    """The worker shares the parent workspace root."""

    name = "none"
    unavailable_message = ""

    async def ready(self, parent_workspace_root: str) -> bool:
        return True

    async def prepare(
        self, *, worker_id: str, parent_workspace_root: str
    ) -> WorkerWorkspace:
        return WorkerWorkspace(root=parent_workspace_root)

    async def is_parent_dirty(self, parent_workspace_root: str) -> bool:
        return False

    async def cleanup(self, worker_id: str, workspace: WorkerWorkspace) -> bool:
        return False

    def task_preamble(self, workspace: WorkerWorkspace) -> Optional[str]:
        return None


class CopyIsolation:
    """Plain-directory isolation: ``cp -a`` the parent workspace into the
    framework scratch area. Sandbox-safe (all operations go through
    ``workspace.exec``); no version control required or involved —
    merging results back is the orchestrator's problem, which is why the
    preamble tells the worker to leave its changes in place."""

    name = "copy"
    unavailable_message = "Cannot isolate: parent workspace is not accessible."

    async def ready(self, parent_workspace_root: str) -> bool:
        from omniagents import workspace as _ws

        probe = await _ws.exec(
            f"test -d {shlex.quote(parent_workspace_root)}", shell=True, timeout=5.0
        )
        return probe.exit_code == 0

    async def prepare(
        self, *, worker_id: str, parent_workspace_root: str
    ) -> WorkerWorkspace:
        from omniagents import workspace as _ws
        from omniagents.workspace import scratch_dir

        parent_name = Path(parent_workspace_root).name or "workspace"
        base = await scratch_dir("workers")
        dest = os.path.join(base, f"{parent_name}-worker-{worker_id}")

        async def _exists(p: str) -> bool:
            probe = await _ws.exec(f"test -e {shlex.quote(p)}", shell=True, timeout=5.0)
            return probe.exit_code == 0

        if await _exists(dest):
            n = 2
            while await _exists(f"{dest}-{n}"):
                n += 1
            dest = f"{dest}-{n}"

        result = await _ws.exec(
            f"cp -a {shlex.quote(parent_workspace_root)} {shlex.quote(dest)}",
            shell=True,
            timeout=120.0,
        )
        if result.exit_code != 0:
            raise IsolationError(
                f"Failed to copy workspace: {(result.stderr or result.stdout or '').strip()}"
            )
        return WorkerWorkspace(
            root=dest,
            metadata={
                "workspace_path": dest,
                "parent_workspace": parent_workspace_root,
            },
        )

    async def is_parent_dirty(self, parent_workspace_root: str) -> bool:
        return False

    async def cleanup(self, worker_id: str, workspace: WorkerWorkspace) -> bool:
        from omniagents import workspace as _ws

        try:
            result = await _ws.exec(
                f"rm -rf {shlex.quote(workspace.root)}", shell=True, timeout=30.0
            )
            return result.exit_code == 0
        except Exception:
            return False

    def task_preamble(self, workspace: WorkerWorkspace) -> Optional[str]:
        return (
            f"[You are working in an isolated copy of the workspace at "
            f"{workspace.root}. Make your changes there and leave them in "
            f"place when done; the orchestrator integrates them.]"
        )


_STRATEGIES: Dict[str, IsolationStrategy] = {}


def register_isolation_strategy(strategy: IsolationStrategy) -> None:
    """Register (or replace) a strategy under ``strategy.name``."""
    _STRATEGIES[strategy.name] = strategy


def get_isolation_strategy(name: str) -> IsolationStrategy:
    """Look up a strategy by name; raises KeyError listing what exists."""
    try:
        return _STRATEGIES[name]
    except KeyError:
        raise KeyError(
            f"Unknown isolation strategy {name!r}. "
            f"Registered: {sorted(_STRATEGIES)}"
        ) from None


def isolation_strategy_names() -> list[str]:
    return sorted(_STRATEGIES)


register_isolation_strategy(NoneIsolation())
register_isolation_strategy(CopyIsolation())
