"""Product configuration for worker sub-agents.

Following the provider-seam pattern (see ``omniagents.core.skills.roots``
and the autonomy prompt builders): the framework owns the worker
lifecycle tools; the product installs how a worker's AgentSpec is built,
how its history is loaded on resume/status, which isolation strategy is
the default, and (optionally) how workers are named.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class WorkerDefaults:
    """Product-supplied wiring for the worker tools.

    ``spec_loader(model)`` returns ``(AgentSpec, model_runtime)`` for a new
    worker — the same callable shape ``WorkerManager`` expects. It is the
    only required field: without it workers cannot be spawned.

    ``history_loader(entry, tail)`` returns the last ``tail`` history items
    for a worker (used by ``get_worker_status`` and resume flows). None
    uses the framework's generic session-history loader.

    ``default_isolation`` names the strategy used when ``spawn_worker`` is
    called without an ``isolation`` argument. ``"none"`` (the default)
    shares the parent workspace.

    ``namer(existing)`` returns a fresh display name not present in the
    ``existing`` set. None uses the framework's whimsical name generator.
    """

    spec_loader: Optional[Callable[..., Any]] = None
    history_loader: Optional[Callable[..., Any]] = None
    default_isolation: str = "none"
    namer: Optional[Callable[[set], str]] = None
    recipe_id: str = "project-worker-v1"


_defaults = WorkerDefaults()


def set_worker_defaults(defaults: WorkerDefaults) -> None:
    """Install the product's worker wiring (process-global)."""
    global _defaults
    _defaults = defaults


def worker_defaults() -> WorkerDefaults:
    return _defaults
