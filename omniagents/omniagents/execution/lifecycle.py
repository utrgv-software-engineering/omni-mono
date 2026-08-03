"""Environment-scoped lifecycle control surface.

The controller belongs to one logical ExecutionEnvironment and is captured in
each operation lease.  Implementations may replace the environment backend,
but handlers never discover one through process-global serve state.
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import EnvironmentLease


class EnvironmentLifecycleController(Protocol):
    """Control-plane operations supported by an environment host."""

    async def pause(self, lease: EnvironmentLease) -> dict[str, Any]: ...

    async def unpause(self, lease: EnvironmentLease) -> dict[str, Any]: ...

    async def notify_activity(self, lease: EnvironmentLease) -> dict[str, Any]: ...

    async def discard_snapshot(self, lease: EnvironmentLease) -> dict[str, Any]: ...

    async def close(self, lease: EnvironmentLease) -> None: ...
