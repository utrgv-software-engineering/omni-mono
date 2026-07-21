"""omni-code's product wiring for the framework worker tools.

The worker lifecycle tools, the ``worktree`` isolation mechanics (branch
naming, multi-source layout, transactional rollback), and the
project-backed spec-loading recipe all live in
``omniagents.core.workers``; this module supplies only omni-code's parts
— the project/model/orchestrator providers and the published-sources
mapper — and installs them. ``install()`` is idempotent and runs at
import time of ``omni_code/agents/omni/context.py``, so the wiring
provably lands before the first spawn. Tests import this module directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from omniagents.core.workers import (
    MultiSourceWorktreeIsolation,
    WorkerDefaults,
    WorkerSource,
    make_project_worker_spec_loader,
    register_isolation_strategy,
    set_worker_defaults,
)

_FALLBACK_PROJECT_CONTEXT: Any = None


def _resolve_project_context() -> Any:
    """The running server's published ProjectContext, else a cached
    ``load_project`` of the packaged project.yml (tests, direct runs)."""
    from omni_code.agent_workspace_state import get_project_context

    ctx = get_project_context()
    if ctx is not None:
        return ctx

    global _FALLBACK_PROJECT_CONTEXT
    if _FALLBACK_PROJECT_CONTEXT is None:
        from omniagents.core.project.runtime import load_project

        project_yml = Path(__file__).resolve().parent / "project.yml"
        _FALLBACK_PROJECT_CONTEXT = load_project(str(project_yml))
    return _FALLBACK_PROJECT_CONTEXT


def _resolve_worker_model_runtime(model: Optional[str] = None) -> Any:
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    return resolve_model_for_runtime(model) if model else resolve_model_for_runtime()


def _orchestrator_spec() -> Any:
    from omni_code.agent_workspace_state import get_orchestrator_spec

    return get_orchestrator_spec()


def _worker_sources() -> list[WorkerSource]:
    """Published workspace sources → framework source descriptors.

    ``local-git``/``git-remote`` isolate via git worktree, ``local`` via
    plain copy; other kinds are skipped. A source's host path is its
    published ``path`` or its mount under the manifest root. Empty when
    serve published no sources (single-repo behavior applies then).
    """
    from omni_code.agent_workspace_state import get_manifest_root, get_sources

    manifest_root = get_manifest_root()
    out: list[WorkerSource] = []
    for source in get_sources() or []:
        kind = source.get("kind")
        mount_name = source.get("mountName")
        if kind not in ("local", "local-git", "git-remote"):
            continue
        if not isinstance(mount_name, str) or not mount_name.strip():
            continue
        path = source.get("path")
        if not (isinstance(path, str) and path.strip()):
            path = os.path.join(manifest_root, mount_name) if manifest_root else None
        if path:
            out.append(
                WorkerSource(
                    name=mount_name,
                    host_path=path,
                    git=kind in ("local-git", "git-remote"),
                )
            )
    return out


_installed = False


def install() -> None:
    """Install omni-code's worker wiring (idempotent)."""
    global _installed
    if _installed:
        return
    register_isolation_strategy(
        MultiSourceWorktreeIsolation(_worker_sources, name="worktree")
    )
    set_worker_defaults(
        WorkerDefaults(
            spec_loader=make_project_worker_spec_loader(
                project_provider=_resolve_project_context,
                agent_key="worker",
                project_slug="omni_code",
                model_resolver=_resolve_worker_model_runtime,
                orchestrator_spec_provider=_orchestrator_spec,
            ),
            default_isolation="none",
        )
    )
    _installed = True
