"""Pluggable isolation for worker sub-agents.

A worker can run directly in the parent's workspace (no isolation) or in
an isolated copy of it. *How* that copy is made is a product decision —
a coding product uses git worktrees, others may want a plain directory
copy or nothing at all — so the mechanics live behind a small strategy
protocol and a process-global registry.

The framework ships three strategies:

* ``none`` — the worker shares the parent workspace root.
* ``copy`` — the worker gets a ``cp -a`` copy of the parent workspace
  under the framework scratch area. Works for any directory workspace.
* ``worktree`` — the worker gets a git worktree on its own branch,
  checked out from HEAD, under the framework scratch area. Requires the
  parent workspace to be a git repository.

:class:`MultiSourceWorktreeIsolation` extends ``worktree`` to
workspaces assembled from several sources (one worktree or copy per
source). It needs a product-supplied :data:`SourcesProvider`, so it is
not auto-registered — products instantiate it (typically under
``name="worktree"`` to replace the single-repo builtin) and register it
with :func:`register_isolation_strategy`, selecting a default via
``WorkerDefaults.default_isolation``.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)


from contextvars import ContextVar

preparation_observer: ContextVar[Any] = ContextVar("worker_preparation", default=None)


def note_preparation(root, **metadata):
    observer = preparation_observer.get()
    if observer is not None:
        observer({"root": root, **metadata})


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

        # Workspace path syntax (POSIX in sandboxes on any host OS).
        pm = _ws.current_workspace().path_module
        parent_name = pm.basename(pm.normpath(parent_workspace_root)) or "workspace"
        base = await scratch_dir("workers")
        dest = pm.join(base, f"{parent_name}-worker-{worker_id}")

        async def _exists(p: str) -> bool:
            probe = await _ws.exec(f"test -e {shlex.quote(p)}", shell=True, timeout=5.0)
            return probe.exit_code == 0

        if await _exists(dest):
            n = 2
            while await _exists(f"{dest}-{n}"):
                n += 1
            dest = f"{dest}-{n}"

        note_preparation(dest, parent_workspace=parent_workspace_root, kind="copy")
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


async def is_git_repo(workspace_root: str) -> bool:
    """True when *workspace_root* is inside a git work tree.

    Routed through ``workspace.exec`` so the same body works against a
    SandboxWorkspace (where the git binary lives inside the sandbox).
    """
    from omniagents import workspace as _ws

    try:
        result = await _ws.exec(
            f"git -C {shlex.quote(workspace_root)} rev-parse --is-inside-work-tree",
            timeout=5.0,
            shell=True,
        )
        return result.exit_code == 0
    except Exception:
        return False


async def is_git_dirty(workspace_root: str) -> bool:
    """True when the repository at *workspace_root* has uncommitted changes."""
    from omniagents import workspace as _ws

    try:
        result = await _ws.exec(
            f"git -C {shlex.quote(workspace_root)} status --porcelain",
            timeout=5.0,
            shell=True,
        )
        if result.exit_code != 0:
            return False
        return bool((result.stdout or "").strip())
    except Exception:
        return False


class WorktreeIsolation:
    """Git-worktree isolation: the worker gets a worktree on its own
    ``worker/<parent>-worker-<id>`` branch checked out from HEAD, under
    the framework scratch area. Works whether the parent is a regular
    repo or itself a worktree — ``git worktree add`` operates on the
    shared repository in both cases. Sandbox-safe (all operations go
    through ``workspace.exec``).

    Stateless: everything cleanup needs is carried in the
    :class:`WorkerWorkspace` metadata produced by :meth:`prepare`.
    """

    name = "worktree"
    unavailable_message = (
        "Cannot create worktree: the workspace is not a git repository."
    )

    async def ready(self, parent_workspace_root: str) -> bool:
        return await is_git_repo(parent_workspace_root)

    async def prepare(
        self, *, worker_id: str, parent_workspace_root: str
    ) -> WorkerWorkspace:
        from omniagents import workspace as _ws
        from omniagents.workspace import scratch_root

        # Derive a worktree name from the workspace directory name + worker
        # id, e.g. supervisor in ~/projects/my-app → "my-app-worker-pip".
        # Workspace path syntax (POSIX in sandboxes on any host OS).
        pm = _ws.current_workspace().path_module
        parent_name = pm.basename(pm.normpath(parent_workspace_root)) or "workspace"
        worktree_name = f"{parent_name}-worker-{worker_id}"
        worktree_branch = f"worker/{worktree_name}"
        worktrees_dir = pm.join(scratch_root(), "worktrees")
        worktree_path = pm.join(worktrees_dir, worktree_name)

        # Existence check + mkdir via exec rather than the workspace's typed
        # methods: sandbox scratch lives outside the manifest mount, so the
        # path-policy-checked typed methods may refuse it while exec can
        # operate anywhere the shell has permission.
        async def _scratch_exists(p: str) -> bool:
            probe = await _ws.exec(f"test -e {shlex.quote(p)}", shell=True, timeout=5.0)
            return probe.exit_code == 0

        if await _scratch_exists(worktree_path):
            n = 2
            while await _scratch_exists(f"{worktree_path}-{n}"):
                n += 1
            worktree_name = f"{worktree_name}-{n}"
            worktree_branch = f"worker/{worktree_name}"
            worktree_path = pm.join(worktrees_dir, worktree_name)

        note_preparation(
            worktree_path,
            parent_workspace=parent_workspace_root,
            worktree_branch=worktree_branch,
            kind="worktree",
        )
        mkdir_result = await _ws.exec(
            f"mkdir -p {shlex.quote(worktrees_dir)}", shell=True, timeout=10.0
        )
        if mkdir_result.exit_code != 0:
            raise IsolationError(
                f"Failed to create worktrees scratch dir {worktrees_dir!r}: "
                f"{(mkdir_result.stderr or mkdir_result.stdout or '').strip()}"
            )

        result = await _ws.exec(
            f"git -C {shlex.quote(parent_workspace_root)} worktree add "
            f"-b {shlex.quote(worktree_branch)} {shlex.quote(worktree_path)} HEAD",
            timeout=30.0,
            shell=True,
        )
        if result.exit_code != 0:
            raise IsolationError(
                "Failed to create worktree: "
                f"{(result.stderr or result.stdout or '').strip()}"
            )

        return WorkerWorkspace(
            root=worktree_path,
            metadata={
                "worktree_path": worktree_path,
                "worktree_branch": worktree_branch,
                "worktree_name": worktree_name,
                "parent_workspace": parent_workspace_root,
            },
        )

    async def is_parent_dirty(self, parent_workspace_root: str) -> bool:
        return await is_git_dirty(parent_workspace_root)

    async def cleanup(self, worker_id: str, workspace: WorkerWorkspace) -> bool:
        from omniagents import workspace as _ws

        meta = workspace.metadata or {}
        parent = meta.get("parent_workspace")
        wt_path = meta.get("worktree_path") or workspace.root
        branch = meta.get("worktree_branch", "")
        if not parent:
            return False

        try:
            await _ws.exec(
                f"git -C {shlex.quote(parent)} worktree remove --force "
                f"{shlex.quote(wt_path)}",
                timeout=10.0,
                shell=True,
            )
        except Exception:
            pass

        if branch:
            try:
                await _ws.exec(
                    f"git -C {shlex.quote(parent)} branch -D {shlex.quote(branch)}",
                    timeout=5.0,
                    shell=True,
                )
            except Exception:
                pass
        return True

    def task_preamble(self, workspace: WorkerWorkspace) -> Optional[str]:
        branch = (workspace.metadata or {}).get("worktree_branch", "")
        return (
            f"[You are working in an isolated git worktree at {workspace.root}"
            + (f" on branch {branch}" if branch else "")
            + ". Commit your changes when done so they can be merged back "
            "into the parent repository.]"
        )


@dataclass(frozen=True)
class WorkerSource:
    """One workspace source to isolate for a worker.

    Multi-source workspaces (e.g. a serve session assembled from several
    ``--source`` mounts) are described to the framework as a flat list of
    these descriptors — the product's manifest/mount semantics stay on
    the product side of the seam.

    ``name`` is the directory name the source is mounted under inside the
    shared worker root (and the last branch-path segment for git
    sources). ``host_path`` is the absolute path of the source in the
    parent workspace, as seen by the active ``omniagents.workspace``.
    ``git`` selects the mechanism: True → git worktree on its own
    ``worker/<parent>-worker-<id>/<name>`` branch; False → ``cp -a``
    copy.
    """

    name: str
    host_path: str
    git: bool = True


#: Returns the current workspace sources, or an empty/None result when the
#: workspace is not multi-source (single-repo behavior applies then).
SourcesProvider = Callable[[], Optional[Sequence[WorkerSource]]]


class MultiSourceWorktreeIsolation(WorktreeIsolation):
    """Worktree isolation for workspaces assembled from several sources.

    Each git-backed source gets its own worktree on a
    ``worker/<parent>-worker-<id>/<name>`` branch; non-git sources get a
    plain ``cp -a`` copy. All of them are mounted by name under one
    shared worker root in the framework scratch area, so the worker sees
    the same layout as the parent workspace. Creation is transactional:
    if any source fails to isolate, everything created so far (worktrees,
    branches, the worker root) is rolled back before the error surfaces.

    The strategy is parameterized by a :data:`SourcesProvider` so it
    carries no product state. When the provider yields no sources it
    degrades to the plain single-repo :class:`WorktreeIsolation`
    behavior, which makes it a drop-in replacement for the builtin
    ``worktree`` strategy (register it under ``name="worktree"``).

    Stateless like its base: everything cleanup needs is carried in the
    :class:`WorkerWorkspace` metadata produced by :meth:`prepare`.
    Sandbox-safe (all operations go through ``workspace.exec``).
    """

    name = "multi-source-worktree"
    unavailable_message = (
        "Cannot create worktree: no git-backed workspace sources are available."
    )

    def __init__(
        self,
        sources_provider: SourcesProvider,
        *,
        name: Optional[str] = None,
        unavailable_message: Optional[str] = None,
    ) -> None:
        self._sources_provider = sources_provider
        if name is not None:
            self.name = name
        if unavailable_message is not None:
            self.unavailable_message = unavailable_message

    def sources(self) -> list[WorkerSource]:
        """Current usable sources; empty means single-repo behavior."""
        try:
            raw = self._sources_provider()
        except Exception:
            return []
        out: list[WorkerSource] = []
        for source in raw or []:
            if source.name.strip() and source.host_path.strip():
                out.append(source)
        return out

    async def ready(self, parent_workspace_root: str) -> bool:
        sources = self.sources()
        if not sources:
            return await super().ready(parent_workspace_root)
        git_paths = [s.host_path for s in sources if s.git]
        if not git_paths:
            return False
        for path in git_paths:
            if not await is_git_repo(path):
                return False
        return True

    async def is_parent_dirty(self, parent_workspace_root: str) -> bool:
        sources = self.sources()
        if not sources:
            return await super().is_parent_dirty(parent_workspace_root)
        for source in sources:
            if source.git and await is_git_dirty(source.host_path):
                return True
        return False

    async def prepare(
        self, *, worker_id: str, parent_workspace_root: str
    ) -> WorkerWorkspace:
        sources = self.sources()
        if not sources:
            return await super().prepare(
                worker_id=worker_id, parent_workspace_root=parent_workspace_root
            )

        from omniagents import workspace as _ws
        from omniagents.workspace import scratch_root

        # Workspace path syntax (POSIX in sandboxes on any host OS).
        pm = _ws.current_workspace().path_module
        parent_name = pm.basename(pm.normpath(parent_workspace_root)) or "workspace"
        worker_root_name = f"{parent_name}-worker-{worker_id}"
        worktrees_dir = pm.join(scratch_root(), "worktrees")
        worker_root = pm.join(worktrees_dir, worker_root_name)

        async def _scratch_exists(p: str) -> bool:
            probe = await _ws.exec(f"test -e {shlex.quote(p)}", shell=True, timeout=5.0)
            return probe.exit_code == 0

        if await _scratch_exists(worker_root):
            n = 2
            while await _scratch_exists(f"{worker_root}-{n}"):
                n += 1
            worker_root_name = f"{worker_root_name}-{n}"
            worker_root = pm.join(worktrees_dir, worker_root_name)

        note_preparation(
            worker_root,
            parent_workspace=parent_workspace_root,
            kind="multi-worktree",
            sources=[
                {"name": source.name, "path": source.host_path, "git": source.git}
                for source in sources
            ],
        )
        mkdir_result = await _ws.exec(
            f"mkdir -p {shlex.quote(worker_root)}", shell=True, timeout=10.0
        )
        if mkdir_result.exit_code != 0:
            raise IsolationError(
                f"Failed to create worker workspace {worker_root!r}: "
                f"{(mkdir_result.stderr or mkdir_result.stdout or '').strip()}"
            )

        source_infos: list[dict[str, Any]] = []
        created_branches: list[tuple[str, str]] = []
        try:
            for source in sources:
                target_path = pm.join(worker_root, source.name)
                if source.git:
                    branch = f"worker/{worker_root_name}/{source.name}"
                    result = await _ws.exec(
                        f"git -C {shlex.quote(source.host_path)} worktree add "
                        f"-b {shlex.quote(branch)} {shlex.quote(target_path)} HEAD",
                        timeout=30.0,
                        shell=True,
                    )
                    if result.exit_code != 0:
                        raise IsolationError(
                            "Failed to create worktree: "
                            f"{(result.stderr or result.stdout or '').strip()}"
                        )
                    created_branches.append((source.host_path, branch))
                    source_infos.append(
                        {
                            "name": source.name,
                            "git": True,
                            "source_path": source.host_path,
                            "worktree_path": target_path,
                            "worktree_branch": branch,
                        }
                    )
                else:
                    result = await _ws.exec(
                        f"cp -a {shlex.quote(source.host_path)} "
                        f"{shlex.quote(target_path)}",
                        timeout=120.0,
                        shell=True,
                    )
                    if result.exit_code != 0:
                        raise IsolationError(
                            "Failed to copy source: "
                            f"{(result.stderr or result.stdout or '').strip()}"
                        )
                    source_infos.append(
                        {
                            "name": source.name,
                            "git": False,
                            "source_path": source.host_path,
                            "worktree_path": target_path,
                        }
                    )
        except Exception:
            # Roll back everything created so far: worktree registrations,
            # their branches, then the shared worker root (which also
            # removes any plain copies).
            for info in source_infos:
                if not info.get("git"):
                    continue
                try:
                    await _ws.exec(
                        f"git -C {shlex.quote(info['source_path'])} "
                        f"worktree remove --force {shlex.quote(info['worktree_path'])}",
                        timeout=10.0,
                        shell=True,
                    )
                except Exception:
                    pass
            for source_path, branch in created_branches:
                try:
                    await _ws.exec(
                        f"git -C {shlex.quote(source_path)} branch -D "
                        f"{shlex.quote(branch)}",
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
            await _ws.exec(
                f"rm -rf {shlex.quote(worker_root)}", shell=True, timeout=10.0
            )
            raise IsolationError(
                "No project sources could be isolated for worker worktree."
            )

        primary_branch = next(
            (
                info["worktree_branch"]
                for info in source_infos
                if "worktree_branch" in info
            ),
            "",
        )
        return WorkerWorkspace(
            root=worker_root,
            metadata={
                "worktree_path": worker_root,
                "worktree_branch": primary_branch,
                "worktree_name": worker_root_name,
                "parent_workspace": parent_workspace_root,
                "sources": source_infos,
            },
        )

    async def cleanup(self, worker_id: str, workspace: WorkerWorkspace) -> bool:
        meta = workspace.metadata or {}
        source_infos = meta.get("sources") or []
        if not source_infos:
            return await super().cleanup(worker_id, workspace)

        from omniagents import workspace as _ws

        wt_path = meta.get("worktree_path") or workspace.root
        for info in source_infos:
            source_path = info.get("source_path")
            source_wt = info.get("worktree_path")
            source_branch = info.get("worktree_branch")
            if source_path and source_wt and source_branch:
                try:
                    await _ws.exec(
                        f"git -C {shlex.quote(source_path)} worktree remove "
                        f"--force {shlex.quote(source_wt)}",
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

    def task_preamble(self, workspace: WorkerWorkspace) -> Optional[str]:
        if not (workspace.metadata or {}).get("sources"):
            return super().task_preamble(workspace)
        return (
            f"[You are working in an isolated worker workspace at {workspace.root}. "
            f"Project sources are mounted under this root by name. "
            f"Git-backed sources each have their own worker branch; commit changes "
            f"inside each modified source when done so they can be merged.]"
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
register_isolation_strategy(WorktreeIsolation())
