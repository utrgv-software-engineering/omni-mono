"""Builtin filesystem-info server functions for picker UIs.

The web backend's workspace picker feature-detects these by name
(``backends/web/ui/src/App.tsx`` requires ``fs_list_dir`` +
``fs_get_workspace_root``; ``WorkspacePicker.tsx`` also calls
``fs_get_home``), so the names are a fixed RPC surface — do not rename.
Activated by the ``workspace_info`` capability pack; lifted from
omni-code.

Sandbox-aware: when the embedder publishes a live sandbox session via
:mod:`omniagents.core.runtime.workspace_info`, ``fs_list_dir`` browses the
*container* filesystem (what the agent's tools actually see) through
:class:`omniagents.workspace.SandboxWorkspace`, clamped to the manifest
root. Standalone (no providers installed) everything falls back to the
server's own host filesystem.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional

from omniagents import server_function
from omniagents.core.runtime.workspace_info import (
    get_manifest_root,
    get_sandbox_session,
    get_workspace_sources,
)
from omniagents.core.server_functions.helpers import session_workspace_root
from omniagents.core.session import Session


def _is_hidden(path: str) -> bool:
    try:
        parts = os.path.normpath(path).split(os.sep)
        for p in parts:
            if p.startswith(".") and p not in (".", ".."):
                return True
        return False
    except Exception:
        return False


def _entry(path: str) -> Dict:
    st = None
    try:
        st = os.stat(path)
    except Exception:
        pass
    return {
        "name": os.path.basename(path) or path,
        "path": os.path.abspath(path),
        "is_dir": os.path.isdir(path),
        "size": int(st.st_size) if st else None,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat() if st else None,
    }


@server_function(description="Get current working directory", strict=True)
async def fs_get_cwd() -> dict:
    return {"path": os.getcwd()}


@server_function(
    description=(
        "Get workspace root for current session (falls back to manifest "
        "root, then cwd)"
    ),
    strict=True,
)
async def fs_get_workspace_root(session: Session) -> dict:
    wr = session_workspace_root(session)
    if wr:
        return {"path": os.path.abspath(wr)}
    # When the session has no stored workspace_root yet, prefer the
    # sandbox manifest root over the server's host cwd — they're the same
    # for unix_local but differ for clients that mount under /workspace.
    manifest_root = get_manifest_root()
    if manifest_root:
        return {"path": manifest_root}
    return {"path": os.getcwd()}


@server_function(description="Get user home directory", strict=True)
async def fs_get_home() -> dict:
    return {"path": os.path.expanduser("~")}


@server_function(
    description=(
        "List the sources that seeded the current workspace. Each entry "
        "describes one directory or repo attached to this project: 'kind' "
        "is local | local-git | git-remote, 'mountName' is the slug under "
        "the manifest root, 'path' (for local kinds) is the host directory "
        "the agent can operate on directly under host-mode, and 'repoUrl' "
        "(+ optional 'ref') describes git-remote sources. Returns an "
        "empty list when no sources were declared."
    ),
    strict=True,
)
async def fs_get_sources() -> dict:
    return {"sources": get_workspace_sources()}


async def _fs_list_dir_in_sandbox(
    session: object,
    path: Optional[str],
    include_files: bool,
    ignore_hidden: bool,
) -> dict:
    """List a directory INSIDE the sandbox container, for the picker.

    The picker browses the filesystem the agent's tools actually see — the
    container — not the server's host. Defaults to the manifest root. Paths
    are absolute container paths so the picker can navigate by them.
    """
    from pathlib import PurePosixPath

    from omniagents.workspace import clamp_within
    from omniagents.workspace.sandbox import SandboxWorkspace

    manifest_root = get_manifest_root() or "/workspace"
    # Clamp the requested path into the manifest root. A stray absolute/host
    # path (e.g. the launcher's local project dir leaking through) would not
    # exist in the container and the SDK would reject it; clamping lists the
    # manifest root instead — the same boundary the agent's tools enforce.
    base = clamp_within(path, manifest_root)
    base = str(PurePosixPath(base))
    ws = SandboxWorkspace(session, root=base)
    try:
        file_entries = await ws.list_dir(".", include_hidden=not ignore_hidden)
    except Exception as e:
        raise ValueError(f"Cannot list {base!r} in sandbox: {e}")
    entries: List[Dict] = []
    for fe in file_entries:
        if not include_files and not fe.is_dir:
            continue
        entries.append(
            {
                "name": fe.name,
                "path": str(PurePosixPath(base) / fe.name),
                "is_dir": fe.is_dir,
                "size": fe.size,
                "modified": fe.mtime.isoformat() if fe.mtime else None,
            }
        )
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = str(PurePosixPath(base).parent)
    # Only expose "up" while it stays within the manifest root — the picker
    # can't navigate above the workspace boundary.
    up = parent if (parent != base and clamp_within(parent, manifest_root) == parent) else None
    return {"path": base, "parent": up, "entries": entries}


@server_function(description="List a directory for picker UIs", strict=True)
async def fs_list_dir(
    path: Optional[str] = None,
    include_files: Optional[bool] = False,
    ignore_hidden: Optional[bool] = True,
) -> dict:
    # When a sandbox session is live, browse the CONTAINER filesystem (what
    # the agent's tools see). With no session (the host target) fall through
    # to the host filesystem below.
    session = get_sandbox_session()
    if session is not None:
        return await _fs_list_dir_in_sandbox(
            session, path, bool(include_files), bool(ignore_hidden)
        )

    base = path or os.getcwd()
    base = os.path.abspath(base)
    if not os.path.exists(base):
        raise ValueError(f"Path does not exist: {base}")
    if not os.path.isdir(base):
        raise ValueError(f"Not a directory: {base}")

    try:
        entries: List[Dict] = []
        with os.scandir(base) as it:
            for e in it:
                p = e.path
                if ignore_hidden and _is_hidden(p):
                    continue
                if e.is_dir():
                    entries.append(_entry(p))
                elif include_files:
                    entries.append(_entry(p))
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        parent = os.path.dirname(base)
        up = parent if parent != base else None
        return {"path": base, "parent": up, "entries": entries}
    except Exception as e:
        raise ValueError(str(e))


# Intentionally no ``session_ensure`` here: omni-code's ``session_ensure``
# was a proxy for the framework's own builtin ``session.ensure``
# (registered in ``omniagents.core.agents.service``), which clients should
# call directly. The omni-code shim dies downstream.
