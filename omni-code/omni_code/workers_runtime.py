"""omni-code's product wiring for the framework worker tools.

The worker lifecycle tools (spawn/wait/status/message/close/resume/
cleanup) live in ``omniagents.builtin.tools.worker_tools``; this module
supplies everything product-specific and installs it via
``omniagents.core.workers.set_worker_defaults`` + the isolation-strategy
registry:

- the worker AgentSpec loader (project registries via
  ``build_spec_from_project``, model resolution via ``omni_code.models``,
  sandbox forwarding from the running orchestrator),
- the session-history loader with omni-code slugs,
- the ``worktree`` isolation strategy: one git worktree per git-backed
  workspace source (or a single worktree in the plain single-repo case),
  branch naming ``worker/<parent>-worker-<id>``.

``install()`` is idempotent and is called at import time of
``server_functions/workers.py`` — server-function discovery imports that
module in every omni-code server process, so the wiring provably lands
before the first spawn. Tests import this module directly.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

from omniagents.core.workers import (
    IsolationError,
    WorkerDefaults,
    WorkerWorkspace,
    WorktreeIsolation as FrameworkWorktreeIsolation,
    is_git_dirty,
    is_git_repo,
    register_isolation_strategy,
    set_worker_defaults,
)


def _worktrees_dir() -> str:
    """Resolve the worktree parent directory for the active workspace.

    Lives under the workspace abstraction's framework-owned scratch root
    so it stays outside the user's project files (host: ``~/.omniagents/
    scratch/worktrees``; sandbox: ``/tmp/omniagents/scratch/worktrees``).
    """
    from omniagents import workspace as _ws

    return os.path.join(_ws.scratch_root(), "worktrees")


# ---------------------------------------------------------------------------
# Worker spec loading (project registries + model resolution + sandbox)
# ---------------------------------------------------------------------------


_FALLBACK_PROJECT_CONTEXT: Any = None


def _resolve_project_context() -> Any:
    """Return the ProjectContext to load the worker spec against.

    Prefer the one published by ``omni serve`` (``set_project_context``)
    so the worker shares the running server's resolved registries. Fall
    back to discovering ``project.yml`` relative to this file — that path
    is used by tests and direct invocations where serve_cli never ran.
    The fallback is cached because ``load_project`` walks the agents/
    tools/capabilities directories and is too expensive to repeat per
    spawn.
    """
    from omni_code.agent_workspace_state import get_project_context

    ctx = get_project_context()
    if ctx is not None:
        return ctx

    global _FALLBACK_PROJECT_CONTEXT
    if _FALLBACK_PROJECT_CONTEXT is None:
        from omniagents.core.project.runtime import load_project

        project_yml = Path(__file__).resolve().parent.parent / "project.yml"
        _FALLBACK_PROJECT_CONTEXT = load_project(str(project_yml))
    return _FALLBACK_PROJECT_CONTEXT


def _load_and_configure_spec(model: Optional[str] = None):
    """Build the worker spec.

    Routes through ``build_spec_from_project`` — the same path the
    orchestrator uses (omni_code/serve_cli.py:_load_spec) — so the worker
    inherits the project's full capability_registry, tool_registry,
    guardrails_registry, server_functions_registry, agents_map, and
    project_defaults. Loading the worker YAML directly via
    ``load_agent_spec_from_yaml`` would pass capability_registry=None and
    silently skip every entry in the worker's ``capabilities:`` list,
    leaving the worker with zero tools.

    On top of that, forward the running orchestrator's sandbox attachment
    (sandbox_use_sandbox_agent / sandbox_run_as / sandbox_run_config) so
    worker tools route through the same SandboxSession in sandbox mode.
    """
    from omniagents.core.project.runtime import build_spec_from_project
    from omni_code.agent_workspace_state import get_orchestrator_spec
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()
    model_runtime = (
        resolve_model_for_runtime(model) if model else resolve_model_for_runtime()
    )

    proj = _resolve_project_context()
    spec = build_spec_from_project(proj, agent_key="worker")

    # build_spec_from_project sets project_slug from project.name — which
    # is "omni_code" — but pin it explicitly so worker sessions/traces
    # stay grouped even if the project name ever drifts.
    spec.project_slug = "omni_code"

    # Forward sandbox state from the running orchestrator. Skips silently
    # when the orchestrator hasn't published (tests, direct invocation)
    # or when the orchestrator isn't running in sandbox mode.
    orch = get_orchestrator_spec()
    if orch is not None:
        if getattr(orch, "sandbox_use_sandbox_agent", False):
            spec.sandbox_use_sandbox_agent = True
        run_as = getattr(orch, "sandbox_run_as", None)
        if run_as:
            spec.sandbox_run_as = run_as
        run_cfg = getattr(orch, "sandbox_run_config", None)
        if run_cfg is not None:
            spec.sandbox_run_config = run_cfg

    resolved_model = (
        model_runtime.get("model") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model, str) and resolved_model.strip():
        spec.model_name = resolved_model

    resolved_model_settings = (
        model_runtime.get("model_settings") if isinstance(model_runtime, dict) else None
    )
    if isinstance(resolved_model_settings, dict) and resolved_model_settings:
        spec.model_settings = resolved_model_settings

    spec.resolve_default_model_config = lambda: (
        model_runtime if isinstance(model_runtime, dict) else None
    )

    return spec, model_runtime


# ---------------------------------------------------------------------------
# Worktree helpers
# ---------------------------------------------------------------------------


def _published_sources() -> list[dict[str, Any]]:
    try:
        from omni_code.agent_workspace_state import get_sources

        return get_sources()
    except Exception:
        return []


def _source_git_paths(workspace_root: str) -> list[str]:
    sources = _published_sources()
    if not sources:
        return [workspace_root]
    try:
        from omni_code.agent_workspace_state import get_manifest_root

        manifest_root = get_manifest_root() or workspace_root
    except Exception:
        manifest_root = workspace_root
    paths: list[str] = []
    for source in sources:
        if source.get("kind") not in ("local-git", "git-remote"):
            continue
        path = _source_workspace_path(source, manifest_root)
        if path:
            paths.append(path)
    return paths


async def _is_worker_worktree_ready(workspace_root: str) -> bool:
    for path in _source_git_paths(workspace_root):
        if not await is_git_repo(path):
            return False
    return bool(_source_git_paths(workspace_root))


async def _is_worker_worktree_dirty(workspace_root: str) -> bool:
    for path in _source_git_paths(workspace_root):
        if await is_git_dirty(path):
            return True
    return False


def _source_workspace_path(
    source: dict[str, Any], manifest_root: str | None
) -> str | None:
    kind = source.get("kind")
    if kind in ("local", "local-git"):
        path = source.get("path")
        if isinstance(path, str) and path.strip():
            return path
    mount_name = source.get("mountName")
    if isinstance(mount_name, str) and mount_name.strip() and manifest_root:
        return os.path.join(manifest_root, mount_name)
    return None


async def _create_worker_worktree_for_sources(
    workspace_root: str, worker_id: str
) -> dict[str, Any]:
    """Create the multi-source worker workspace: one git worktree (or
    plain copy) per published workspace source, mounted by mountName
    under a shared worker root. Callers guarantee sources are published
    (the single-repo case is the framework strategy's job)."""
    from omniagents import workspace as _ws
    from omni_code.agent_workspace_state import get_manifest_root, get_sources

    sources = get_sources()
    manifest_root = get_manifest_root() or workspace_root
    parent_name = Path(workspace_root).name or "workspace"
    worker_root_name = f"{parent_name}-worker-{worker_id}"
    worker_root = os.path.join(_worktrees_dir(), worker_root_name)

    async def _scratch_exists(path: str) -> bool:
        probe = await _ws.exec(f"test -e {shlex.quote(path)}", shell=True, timeout=5.0)
        return probe.exit_code == 0

    if await _scratch_exists(worker_root):
        n = 2
        while await _scratch_exists(f"{worker_root}-{n}"):
            n += 1
        worker_root_name = f"{worker_root_name}-{n}"
        worker_root = os.path.join(_worktrees_dir(), worker_root_name)

    mkdir_result = await _ws.exec(
        f"mkdir -p {shlex.quote(worker_root)}", shell=True, timeout=10.0
    )
    if mkdir_result.exit_code != 0:
        raise OSError(
            f"Failed to create worker workspace {worker_root!r}: "
            f"{mkdir_result.stderr or mkdir_result.stdout}"
        )

    source_infos: list[dict[str, str]] = []
    created_branches: list[tuple[str, str]] = []
    try:
        for source in sources:
            mount_name = source.get("mountName")
            if not isinstance(mount_name, str) or not mount_name.strip():
                continue
            source_path = _source_workspace_path(source, manifest_root)
            if not source_path:
                continue
            target_path = os.path.join(worker_root, mount_name)
            kind = source.get("kind")
            if kind in ("local-git", "git-remote"):
                branch = f"worker/{worker_root_name}/{mount_name}"
                result = await _ws.exec(
                    f"git -C {shlex.quote(source_path)} worktree add "
                    f"-b {shlex.quote(branch)} {shlex.quote(target_path)} HEAD",
                    timeout=30.0,
                    shell=True,
                )
                if result.exit_code != 0:
                    raise subprocess.CalledProcessError(
                        result.exit_code,
                        ["git", "worktree", "add"],
                        output=(result.stdout or "").encode("utf-8"),
                        stderr=(result.stderr or "").encode("utf-8"),
                    )
                created_branches.append((source_path, branch))
                source_infos.append(
                    {
                        "mountName": mount_name,
                        "kind": str(kind),
                        "source_path": source_path,
                        "worktree_path": target_path,
                        "worktree_branch": branch,
                    }
                )
            elif kind == "local":
                result = await _ws.exec(
                    f"cp -a {shlex.quote(source_path)} {shlex.quote(target_path)}",
                    timeout=30.0,
                    shell=True,
                )
                if result.exit_code != 0:
                    raise subprocess.CalledProcessError(
                        result.exit_code,
                        ["cp", "-a"],
                        output=(result.stdout or "").encode("utf-8"),
                        stderr=(result.stderr or "").encode("utf-8"),
                    )
                source_infos.append(
                    {
                        "mountName": mount_name,
                        "kind": "local",
                        "source_path": source_path,
                        "worktree_path": target_path,
                    }
                )
    except Exception:
        for info in source_infos:
            path = info.get("worktree_path")
            if path:
                try:
                    await _ws.exec(
                        f"git -C {shlex.quote(info.get('source_path', workspace_root))} "
                        f"worktree remove --force {shlex.quote(path)}",
                        timeout=10.0,
                        shell=True,
                    )
                except Exception:
                    pass
        for source_path, branch in created_branches:
            try:
                await _ws.exec(
                    f"git -C {shlex.quote(source_path)} branch -D {shlex.quote(branch)}",
                    timeout=5.0,
                    shell=True,
                )
            except Exception:
                pass
        try:
            await _ws.exec(
                f"rm -rf {shlex.quote(worker_root)}", shell=True, timeout=10.0
            )
        except Exception:
            pass
        raise

    if not source_infos:
        await _ws.exec(f"rm -rf {shlex.quote(worker_root)}", shell=True, timeout=10.0)
        raise OSError("No project sources could be isolated for worker worktree.")

    primary_branch = next(
        (info["worktree_branch"] for info in source_infos if "worktree_branch" in info),
        "",
    )
    return {
        "worktree_path": worker_root,
        "worktree_branch": primary_branch,
        "worktree_name": worker_root_name,
        "parent_workspace": workspace_root,
        "sources": source_infos,
    }


async def _remove_sources_worker_workspace(workspace: WorkerWorkspace) -> bool:
    """Remove a multi-source worker workspace: each source worktree +
    branch, then the shared worker root. Everything needed is carried in
    the workspace metadata produced by ``prepare``."""
    from omniagents import workspace as _ws

    meta = workspace.metadata or {}
    wt_path = meta.get("worktree_path") or workspace.root
    source_infos = meta.get("sources") or []

    for source_info in source_infos:
        source_path = source_info.get("source_path")
        source_wt = source_info.get("worktree_path")
        source_branch = source_info.get("worktree_branch")
        if source_path and source_wt and source_branch:
            try:
                await _ws.exec(
                    f"git -C {shlex.quote(source_path)} worktree remove --force "
                    f"{shlex.quote(source_wt)}",
                    timeout=10.0,
                    shell=True,
                )
            except Exception:
                pass
            try:
                await _ws.exec(
                    f"git -C {shlex.quote(source_path)} branch -D "
                    f"{shlex.quote(source_branch)}",
                    timeout=5.0,
                    shell=True,
                )
            except Exception:
                pass
    try:
        await _ws.exec(f"rm -rf {shlex.quote(wt_path)}", shell=True, timeout=10.0)
    except Exception:
        pass
    return True


class WorktreeIsolation(FrameworkWorktreeIsolation):
    """omni-code's worktree strategy: extends the framework's single-repo
    ``worktree`` isolation with the multi-source serve case — one worktree
    (or plain copy) per published workspace source, each git source on its
    own ``worker/<parent>-worker-<id>/<mount>`` branch. With no published
    sources it behaves exactly like the framework strategy.
    """

    unavailable_message = (
        "Cannot create worktree: no git-backed workspace sources are available."
    )

    async def ready(self, parent_workspace_root: str) -> bool:
        return await _is_worker_worktree_ready(parent_workspace_root)

    async def prepare(
        self, *, worker_id: str, parent_workspace_root: str
    ) -> WorkerWorkspace:
        if not _published_sources():
            return await super().prepare(
                worker_id=worker_id, parent_workspace_root=parent_workspace_root
            )
        try:
            info = await _create_worker_worktree_for_sources(
                parent_workspace_root, worker_id
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)
            raise IsolationError(
                f"Failed to create worktree: {stderr.strip()}"
            ) from e
        except OSError as e:
            raise IsolationError(str(e)) from e
        metadata: dict[str, Any] = {
            "worktree_path": info["worktree_path"],
            "worktree_branch": info["worktree_branch"],
            "worktree_name": info["worktree_name"],
            "parent_workspace": info["parent_workspace"],
            "sources": info["sources"],
        }
        return WorkerWorkspace(root=info["worktree_path"], metadata=metadata)

    async def is_parent_dirty(self, parent_workspace_root: str) -> bool:
        return await _is_worker_worktree_dirty(parent_workspace_root)

    async def cleanup(self, worker_id: str, workspace: WorkerWorkspace) -> bool:
        if (workspace.metadata or {}).get("sources"):
            return await _remove_sources_worker_workspace(workspace)
        return await super().cleanup(worker_id, workspace)

    def task_preamble(self, workspace: WorkerWorkspace) -> Optional[str]:
        if not (workspace.metadata or {}).get("sources"):
            return super().task_preamble(workspace)
        return (
            f"[You are working in an isolated worker workspace at {workspace.root}. "
            f"Project sources are mounted under this root by mountName. "
            f"Git-backed sources each have their own worker branch; commit changes "
            f"inside each modified source when done so they can be merged.]"
        )


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


_installed = False


def install() -> None:
    """Install omni-code's worker wiring (idempotent)."""
    global _installed
    if _installed:
        return
    register_isolation_strategy(WorktreeIsolation())
    set_worker_defaults(
        WorkerDefaults(
            spec_loader=_load_and_configure_spec,
            default_isolation="none",
        )
    )
    _installed = True
