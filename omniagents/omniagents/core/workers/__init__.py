"""Worker sub-agent orchestration: manager, isolation, product wiring.

``WorkerManager`` (the run loop) still lives in ``omniagents.rpc.agents``
and is re-exported here; this package adds the pluggable pieces around
it — isolation strategies, default worker instructions, project-backed
spec loading, and product configuration.
"""

from omniagents.rpc.agents import WorkerManager

from .config import WorkerDefaults, set_worker_defaults, worker_defaults
from .instructions import (
    DEFAULT_WORKER_INSTRUCTIONS,
    apply_default_worker_instructions,
    default_worker_instructions_path,
)
from .isolation import (
    CopyIsolation,
    IsolationError,
    IsolationStrategy,
    MultiSourceWorktreeIsolation,
    NoneIsolation,
    SourcesProvider,
    WorkerSource,
    WorkerWorkspace,
    WorktreeIsolation,
    get_isolation_strategy,
    is_git_dirty,
    is_git_repo,
    isolation_strategy_names,
    register_isolation_strategy,
)
from .spec_loader import make_project_worker_spec_loader

__all__ = [
    "WorkerManager",
    "WorkerDefaults",
    "set_worker_defaults",
    "worker_defaults",
    "DEFAULT_WORKER_INSTRUCTIONS",
    "apply_default_worker_instructions",
    "default_worker_instructions_path",
    "IsolationError",
    "IsolationStrategy",
    "WorkerWorkspace",
    "WorkerSource",
    "SourcesProvider",
    "NoneIsolation",
    "CopyIsolation",
    "WorktreeIsolation",
    "MultiSourceWorktreeIsolation",
    "get_isolation_strategy",
    "is_git_dirty",
    "is_git_repo",
    "isolation_strategy_names",
    "register_isolation_strategy",
    "make_project_worker_spec_loader",
]
