"""Worker sub-agent orchestration: manager, isolation, product wiring.

``WorkerManager`` (the run loop) still lives in ``omniagents.rpc.agents``
and is re-exported here; this package adds the pluggable pieces around
it — isolation strategies, project-backed spec loading, and product
configuration. Worker instructions are product content: the worker
agent's own ``instructions``/``instructions_file`` (``omniagents new``
scaffolds a starter ``agents/worker/``); a spec that declares neither
gets the YAML loader's generic fallback plus its warning.
"""

from omniagents.rpc.agents import WorkerManager

from .config import WorkerDefaults, set_worker_defaults, worker_defaults
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
from .serve_defaults import (
    default_worker_model_resolver,
    default_worker_project_context,
    install_project_worker_defaults,
    serve_worker_sources,
)
from .spec_loader import make_project_worker_spec_loader

__all__ = [
    "WorkerManager",
    "WorkerDefaults",
    "set_worker_defaults",
    "worker_defaults",
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
    "serve_worker_sources",
    "default_worker_project_context",
    "default_worker_model_resolver",
    "install_project_worker_defaults",
]
