"""Worker sub-agent orchestration: manager, isolation, product wiring.

``WorkerManager`` (the run loop) still lives in ``omniagents.rpc.agents``
and is re-exported here; this package adds the pluggable pieces around
it — isolation strategies and product configuration.
"""

from omniagents.rpc.agents import WorkerManager

from .config import WorkerDefaults, set_worker_defaults, worker_defaults
from .isolation import (
    CopyIsolation,
    IsolationError,
    IsolationStrategy,
    NoneIsolation,
    WorkerWorkspace,
    get_isolation_strategy,
    isolation_strategy_names,
    register_isolation_strategy,
)

__all__ = [
    "WorkerManager",
    "WorkerDefaults",
    "set_worker_defaults",
    "worker_defaults",
    "IsolationError",
    "IsolationStrategy",
    "WorkerWorkspace",
    "NoneIsolation",
    "CopyIsolation",
    "get_isolation_strategy",
    "isolation_strategy_names",
    "register_isolation_strategy",
]
