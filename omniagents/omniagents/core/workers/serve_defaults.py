"""Serve-state-derived worker defaults.

Every product that serves a project and wants worker sub-agents repeats
the same wiring: map the serve session's published ``--source``
descriptors to :class:`WorkerSource` entries for multi-source worktree
isolation, build worker specs from the published ``ProjectContext``
(falling back to the installed product's manifest), forward the running
orchestrator's sandbox attachment, and resolve worker models over the
installed model store. All of those inputs already live in framework
globals (``omniagents.core.runtime.serve_state``,
``omniagents.core.product``, ``omniagents.core.models``), so the wiring
itself can be a framework default: :func:`install_project_worker_defaults`
is the one call a product makes to enable the whole thing.

Lifted from omni-code's ``omni_code/workers_runtime.py``.
"""

from __future__ import annotations

import os
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
    """Published serve-session sources → worker source descriptors.

    Local-kind sources (``local``/``local-git``) and git-remote sources
    become :class:`WorkerSource` entries mounted under their mount name;
    git-backed kinds (``local-git``/``git-remote``) isolate via git
    worktree, plain ``local`` via copy; any other kind is skipped. A
    source's host path is its published ``path``, else its mount under
    the session's manifest root (the launcher relocates path-less
    sources there); sources with neither are skipped. Empty when serve
    published no sources — single-repo worktree behavior applies then.
    """
    from omniagents.core.runtime.serve_state import (
        get_manifest_root,
        get_source_descriptors,
    )

    manifest_root = get_manifest_root()
    out: list[WorkerSource] = []
    for source in get_source_descriptors():
        # local ∪ local-git ∪ git-remote, exactly the kinds the
        # descriptor protocol defines placement for.
        if not (source.is_local or source.is_git):
            continue
        if not source.mount_name:
            continue
        path = source.path
        if not path:
            path = (
                os.path.join(manifest_root, source.mount_name)
                if manifest_root
                else None
            )
        if path:
            out.append(
                WorkerSource(
                    name=source.mount_name,
                    host_path=path,
                    git=source.is_git,
                )
            )
    return out


_fallback_project_context: Any = None


def default_worker_project_context() -> Any:
    """The serve session's published ``ProjectContext``, else a cached
    ``load_project`` of the installed product's manifest (tests, direct
    runs). Raises ``RuntimeError`` when neither exists — a worker spec
    cannot be built without a project."""
    from omniagents.core.runtime.serve_state import get_project_context

    ctx = get_project_context()
    if ctx is not None:
        return ctx

    global _fallback_project_context
    if _fallback_project_context is None:
        from omniagents.core.product import product_info
        from omniagents.core.project.runtime import load_project

        manifest = product_info().manifest_path
        if manifest is None:
            raise RuntimeError(
                "Cannot build a worker spec: no ProjectContext is published "
                "(serve_state) and the installed product identity has no "
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


def _orchestrator_spec() -> Any:
    from omniagents.core.runtime.serve_state import get_orchestrator_spec

    return get_orchestrator_spec()


_installed = False


def install_project_worker_defaults(
    *,
    model_resolver: Optional[Callable[[Optional[str]], Any]] = None,
    agent_key: str = "worker",
    project_slug: Optional[str] = None,
    default_isolation: str = "none",
) -> None:
    """Install the serve-state-derived worker wiring (idempotent).

    Registers :class:`MultiSourceWorktreeIsolation` over
    :func:`serve_worker_sources` as the ``worktree`` strategy (replacing
    the single-repo builtin) and installs a
    :func:`make_project_worker_spec_loader` whose inputs all derive from
    framework state: the published project context (product-manifest
    fallback), the published orchestrator spec, the installed product
    slug, and the installed model store.

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
                orchestrator_spec_provider=_orchestrator_spec,
            ),
            default_isolation=default_isolation,
        )
    )
    _installed = True
