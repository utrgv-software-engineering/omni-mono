"""AgentHost-derived worker defaults.

Every product that serves a project and wants worker sub-agents repeats
the same wiring: map the current Run's Workspace sources to
:class:`WorkerSource` entries for multi-source worktree isolation, build
worker specs from the current AgentDefinition's ``ProjectContext`` (falling
back to the installed product manifest), and resolve worker models over the
installed model store. Runtime placement stays on the Run/environment path.

Lifted from omni-code's ``omni_code/workers_runtime.py``.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any, Callable, Optional

from .config import WorkerDefaults, set_worker_defaults
from .isolation import (
    MultiSourceWorktreeIsolation,
    WorkerSource,
    register_isolation_strategy,
)
from .spec_loader import make_project_worker_spec_loader

__all__ = [
    "serve_worker_sources",
    "default_worker_project_context",
    "default_worker_model_resolver",
    "install_project_worker_defaults",
]


def serve_worker_sources() -> list[WorkerSource]:
    """Current Run Workspace sources → worker source descriptors.

    Local-kind sources (``local``/``local-git``) and git-remote sources
    become :class:`WorkerSource` entries mounted under their mount name;
    git-backed kinds (``local-git``/``git-remote``) isolate via git worktree,
    plain ``local`` via copy; any other kind is skipped. Host environments use
    a local source's declared host path. Non-host environments always use the
    source mount beneath the leased workspace root.
    """
    from omniagents.execution import EnvironmentKind, current_request_context

    context = current_request_context()
    if (
        context is None
        or context.environment_lease is None
        or context.workspace is None
    ):
        return []
    lease = context.environment_lease
    workspace_root = lease.workspace.root
    host_environment = lease.descriptor.kind is EnvironmentKind.HOST
    out: list[WorkerSource] = []
    for source in context.workspace.sources:
        kind = source.get("kind")
        mount_name = source.get("mountName")
        # local ∪ local-git ∪ git-remote, exactly the kinds the
        # descriptor protocol defines placement for.
        if kind not in {"local", "local-git", "git-remote"}:
            continue
        if not isinstance(mount_name, str) or not mount_name.strip():
            continue
        raw_path = source.get("path")
        path = (
            raw_path.strip()
            if host_environment
            and kind in {"local", "local-git"}
            and isinstance(raw_path, str)
            and raw_path.strip()
            # Sandbox placement is POSIX on any host OS.
            else (os.path if host_environment else posixpath).join(
                workspace_root, mount_name
            )
        )
        if path:
            out.append(
                WorkerSource(
                    name=mount_name,
                    host_path=path,
                    git=kind in {"local-git", "git-remote"},
                )
            )
    return out


_fallback_project_context: Any = None


def default_worker_project_context() -> Any:
    """The current AgentDefinition's ``ProjectContext``, else a cached
    ``load_project`` of the installed product's manifest (tests, direct
    runs). Raises ``RuntimeError`` when neither exists — a worker spec
    cannot be built without a project."""
    from omniagents.core.agents.service import current_service

    service = current_service()
    ctx = getattr(getattr(service, "spec", None), "project_context", None)
    if ctx is not None:
        return ctx

    global _fallback_project_context
    if _fallback_project_context is None:
        from omniagents.core.product import product_info
        from omniagents.core.project.runtime import load_project

        manifest = product_info().manifest_path
        if manifest is None:
            raise RuntimeError(
                "Cannot build a worker spec: the current AgentDefinition has "
                "no ProjectContext and the installed product identity has no "
                "manifest_path to load a project from."
            )
        _fallback_project_context = load_project(str(manifest))
    return _fallback_project_context


def default_worker_model_resolver(model: Optional[str] = None) -> Any:
    """Resolve a worker model over the installed process-wide model store
    (:func:`omniagents.core.models.set_model_store`). ``None`` when no
    store is installed — the worker spec is then left as built."""
    from omniagents.core.models import model_store

    store = model_store()
    if store is None:
        return None
    return store.resolve_model_for_runtime(model)


_installed = False


def install_project_worker_defaults(
    *,
    model_resolver: Optional[Callable[[Optional[str]], Any]] = None,
    agent_key: str = "worker",
    project_slug: Optional[str] = None,
    default_isolation: str = "none",
) -> None:
    """Install the AgentHost-derived worker wiring (idempotent).

    Registers :class:`MultiSourceWorktreeIsolation` over
    :func:`serve_worker_sources` as the ``worktree`` strategy (replacing
    the single-repo builtin) and installs a
    :func:`make_project_worker_spec_loader` whose inputs derive from the
    current AgentDefinition's project context (product-manifest fallback), the
    installed product slug, and the installed model store.

    Args:
        model_resolver: Override for the model resolution step;
            defaults to :func:`default_worker_model_resolver` (the
            installed model store). A product with its own resolution
            policy passes its resolver here — the one remaining hook.
        agent_key: The project agent key of the worker agent.
        project_slug: Pinned onto worker specs so sessions/traces stay
            grouped; defaults to the installed product's slug (resolved
            at install time, so install after product bootstrap).
        default_isolation: Strategy used when ``spawn_worker`` names
            none.
    """
    global _installed
    if _installed:
        return
    register_isolation_strategy(
        MultiSourceWorktreeIsolation(serve_worker_sources, name="worktree")
    )
    if project_slug is None:
        from omniagents.core.product import product_info

        project_slug = product_info().slug
    set_worker_defaults(
        WorkerDefaults(
            spec_loader=make_project_worker_spec_loader(
                project_provider=default_worker_project_context,
                agent_key=agent_key,
                project_slug=project_slug,
                model_resolver=model_resolver or default_worker_model_resolver,
            ),
            default_isolation=default_isolation,
            recipe_id=f"{project_slug}:{agent_key}:v1",
        )
    )
    _installed = True
