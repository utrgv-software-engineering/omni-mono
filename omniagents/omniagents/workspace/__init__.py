"""``omniagents.workspace`` — sandbox-aware filesystem and shell API.

Tool authors that touch workspace state import top-level functions from
this module. The functions resolve to either :class:`HostWorkspace` or
:class:`SandboxWorkspace` via a contextvar, which the SDK sandbox runtime
swaps when a ``SandboxAgent`` run starts. Non-sandbox runs default to
``HostWorkspace(cwd)`` so the API works without any setup::

    from omniagents.workspace import read_text, write_text, exec

    @rich_function_tool(running_summary="Reading file...")
    async def read_file(path: str) -> str:
        return await read_text(path)

For tools that don't touch workspace state (web requests, model calls,
in-memory bookkeeping), don't import from here — keep using the stdlib.

The contract:

* All operations are ``async``. Host-side wraps blocking stdlib calls in
  :func:`asyncio.to_thread`; sandbox-side awaits the SDK session.
* Path arguments are interpreted relative to ``workspace_root()``. Absolute
  paths and ``..`` traversal that escape the root raise
  :class:`WorkspaceEscapeError` (host); the sandbox session enforces the
  same boundary via its manifest path policy.
* ``exec(..., user="alice")`` maps to sandbox ``run_as`` and is ignored
  host-side (we don't change identity on the host).
* ``spawn(..., pty=True)`` raises :class:`WorkspaceFeatureUnavailable`
  if the active backend can't support PTY.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence

from ._runtime import (
    clamp_within,
    current_workspace,
    get_requested_cwd,
    reset_workspace,
    set_default_workspace,
    set_requested_cwd,
    set_workspace,
)
from .base import CommandLike, ProcessHandle, Workspace
from .host import HostWorkspace
from .sandbox import SandboxWorkspace
from .types import (
    ExecResult,
    FileEntry,
    GrepMatch,
    WorkspaceError,
    WorkspaceEscapeError,
    WorkspaceFeatureUnavailable,
)

# Pyodide backend is optional — importing the module is cheap (no Node
# subprocess yet), but we still re-export through __all__ only when the
# import succeeds so environments without it can `from omniagents.workspace
# import *` cleanly.
try:
    from .pyodide import PyodideWorkspace, pyodide_worker_available

    _HAS_PYODIDE_WORKSPACE = True
except Exception:  # pragma: no cover - defensive; module has no hard deps
    PyodideWorkspace = None  # type: ignore[assignment]
    pyodide_worker_available = None  # type: ignore[assignment]
    _HAS_PYODIDE_WORKSPACE = False

__all__ = [
    # Public API surface
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "exists",
    "is_file",
    "is_dir",
    "mkdir",
    "remove",
    "list_dir",
    "glob",
    "grep",
    "exec",
    "spawn",
    "workspace_root",
    "scratch_root",
    "scratch_dir",
    "resolve",
    "relative_to_workspace",
    # Types
    "ExecResult",
    "FileEntry",
    "GrepMatch",
    "ProcessHandle",
    "Workspace",
    "WorkspaceError",
    "WorkspaceEscapeError",
    "WorkspaceFeatureUnavailable",
    # Backends + routing (advanced use)
    "HostWorkspace",
    "SandboxWorkspace",
    "PyodideWorkspace",
    "pyodide_worker_available",
    "use_pyodide_workspace",
    "current_workspace",
    "set_workspace",
    "set_default_workspace",
    "set_requested_cwd",
    "get_requested_cwd",
    "clamp_within",
    "reset_workspace",
]


# ---------------------- pyodide convenience wiring ----------------------- #


@asynccontextmanager
async def use_pyodide_workspace(
    host_workspace_dir: "str | None" = None,
    *,
    packages: "Sequence[str]" = (),
    snapshot_path: "str | None" = None,
    node_path: str = "node",
) -> "AsyncIterator[PyodideWorkspace]":
    """Install a :class:`PyodideWorkspace` as the active workspace.

    Equivalent to ``set_workspace(PyodideWorkspace(...))`` plus subprocess
    lifecycle management. The worker is started on enter and shut down on
    exit; the contextvar is restored regardless of how the block ends.

    Example::

        async with use_pyodide_workspace("/tmp/agent-workspace") as ws:
            # All builtin tools (read_file, grep_files, apply_patch, ...)
            # now route through pyodide's FS instead of the host disk.
            await runner.run_streamed(agent, input=...)

    Args:
        host_workspace_dir: Host directory to NODEFS-mount at ``/workspace``.
            ``None`` → ephemeral MEMFS root.
        packages: Pyodide-built packages to pre-load.
        snapshot_path: Optional pyodide heap snapshot path.
        node_path: ``node`` binary to invoke.
    """
    if PyodideWorkspace is None:
        raise RuntimeError(
            "PyodideWorkspace is unavailable. The pyodide backend module "
            "failed to import — check omniagents.workspace.pyodide for details."
        )
    ws = PyodideWorkspace(
        host_workspace_dir=host_workspace_dir,
        node_path=node_path,
        packages=packages,
        snapshot_path=snapshot_path,
    )
    await ws.start()
    token = set_workspace(ws)
    try:
        yield ws
    finally:
        try:
            reset_workspace(token)
        finally:
            await ws.aclose()


# --------------------------- file I/O ------------------------------------ #


async def read_text(path: str, *, encoding: str = "utf-8") -> str:
    return await current_workspace().read_text(path, encoding=encoding)


async def read_bytes(path: str) -> bytes:
    return await current_workspace().read_bytes(path)


async def write_text(
    path: str,
    content: str,
    *,
    encoding: str = "utf-8",
    append: bool = False,
    create_dirs: bool = True,
) -> None:
    await current_workspace().write_text(
        path,
        content,
        encoding=encoding,
        append=append,
        create_dirs=create_dirs,
    )


async def write_bytes(
    path: str,
    data: bytes,
    *,
    append: bool = False,
    create_dirs: bool = True,
) -> None:
    await current_workspace().write_bytes(
        path, data, append=append, create_dirs=create_dirs
    )


async def exists(path: str) -> bool:
    return await current_workspace().exists(path)


async def is_file(path: str) -> bool:
    return await current_workspace().is_file(path)


async def is_dir(path: str) -> bool:
    return await current_workspace().is_dir(path)


async def mkdir(path: str, *, parents: bool = True, exist_ok: bool = True) -> None:
    await current_workspace().mkdir(path, parents=parents, exist_ok=exist_ok)


async def remove(path: str, *, recursive: bool = False) -> None:
    await current_workspace().remove(path, recursive=recursive)


# --------------------------- discovery ----------------------------------- #


async def list_dir(
    path: str = ".",
    *,
    include_hidden: bool = False,
    respect_gitignore: bool = True,
    sort_by: Literal["name", "mtime", "size"] = "name",
) -> List[FileEntry]:
    return await current_workspace().list_dir(
        path,
        include_hidden=include_hidden,
        respect_gitignore=respect_gitignore,
        sort_by=sort_by,
    )


async def glob(
    pattern: str,
    *,
    root: str = ".",
    recursive: bool = True,
    include_hidden: bool = False,
) -> List[str]:
    return await current_workspace().glob(
        pattern, root=root, recursive=recursive, include_hidden=include_hidden
    )


async def grep(
    pattern: str,
    *,
    root: str = ".",
    file_pattern: str = "*",
    case_sensitive: bool = True,
    context_lines: int = 0,
) -> List[GrepMatch]:
    return await current_workspace().grep(
        pattern,
        root=root,
        file_pattern=file_pattern,
        case_sensitive=case_sensitive,
        context_lines=context_lines,
    )


# ----------------------- shell execution --------------------------------- #


async def exec(  # noqa: A001 — shadowing builtin is intentional, ergonomic
    command: CommandLike,
    *,
    cwd: Optional[str] = None,
    timeout: Optional[float] = 30.0,
    env: Optional[Dict[str, str]] = None,
    shell: bool = True,
    user: Optional[str] = None,
    max_output_bytes: Optional[int] = None,
) -> ExecResult:
    return await current_workspace().exec(
        command,
        cwd=cwd,
        timeout=timeout,
        env=env,
        shell=shell,
        user=user,
        max_output_bytes=max_output_bytes,
    )


async def spawn(
    command: CommandLike,
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = True,
    user: Optional[str] = None,
    pty: bool = False,
) -> ProcessHandle:
    return await current_workspace().spawn(
        command, cwd=cwd, env=env, shell=shell, user=user, pty=pty
    )


# ------------------------- path utilities -------------------------------- #


def workspace_root() -> str:
    """Absolute path of the workspace root in the active backend."""
    return current_workspace().root


def scratch_root() -> str:
    """Absolute path of the framework-owned scratch directory.

    Tool-internal state (worktrees, spawn logs, etc.) lives under here,
    deliberately outside :func:`workspace_root` so the user's
    ``glob_files``/``grep_files``/git workflows don't trip over it.
    """
    return current_workspace().scratch_root


async def scratch_dir(purpose: str) -> str:
    """Absolute path of a purpose-scoped subdirectory of :func:`scratch_root`.

    Created if missing. Use this instead of inventing dot-directories
    inside the user's workspace for tool-internal state — e.g. background
    job logs live under ``scratch_dir("jobs")``, worker worktrees under
    ``scratch_dir("worktrees")``.

    Creation goes through :func:`exec` rather than :func:`mkdir` because
    sandbox ``scratch_root`` lives outside the manifest mount — the typed
    filesystem methods are path-policy checked and may refuse it, while
    ``exec`` can write anywhere the shell can. The host backend evaluates
    the same shell command locally, so behavior is consistent.
    """
    import os as _os
    import shlex as _shlex

    path = _os.path.join(scratch_root(), purpose)
    result = await exec(f"mkdir -p {_shlex.quote(path)}", shell=True, timeout=10.0)
    if result.exit_code != 0:
        raise WorkspaceError(
            f"could not create scratch dir {path!r}: {result.stderr or result.stdout}"
        )
    return path


def resolve(path: str) -> str:
    """Resolve *path* against ``workspace_root()`` into an absolute path."""
    return current_workspace().resolve(path)


def relative_to_workspace(path: str) -> str:
    """Return *path* relative to ``workspace_root()``."""
    return current_workspace().relative_to_workspace(path)
