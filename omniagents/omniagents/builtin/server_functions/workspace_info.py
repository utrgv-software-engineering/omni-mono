"""Builtin filesystem-info server functions for picker UIs.

The web backend's workspace picker feature-detects these by name
(``backends/web/ui/src/App.tsx`` requires ``fs_list_dir`` +
``fs_get_workspace_root``; ``WorkspacePicker.tsx`` also calls
``fs_get_home``), so the names are a fixed RPC surface — do not rename.
Activated by the ``workspace_info`` capability pack; lifted from
omni-code.

Every handler uses the Workspace adapter captured in the operation's immutable
EnvironmentLease. There is no process cwd, host-filesystem, or global sandbox
fallback.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from omniagents import server_function
from omniagents.execution import require_environment_lease, require_request_context
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


@server_function(
    description="Get current working directory",
    strict=True,
    resource_scope=ResourceScope.WORKSPACE,
    execution_requirements=ExecutionRequirement.WORKSPACE_READ,
)
async def fs_get_cwd() -> dict:
    return {"path": require_environment_lease().workspace.root}


@server_function(
    description=("Get the root of the selected execution environment's workspace"),
    strict=True,
    resource_scope=ResourceScope.WORKSPACE,
    execution_requirements=ExecutionRequirement.WORKSPACE_READ,
)
async def fs_get_workspace_root() -> dict:
    return {"path": require_environment_lease().workspace.root}


@server_function(
    description="Get the browseable home for the selected workspace",
    strict=True,
    resource_scope=ResourceScope.ENVIRONMENT,
    execution_requirements=ExecutionRequirement.PROCESS,
)
async def fs_get_home() -> dict:
    workspace = require_environment_lease().workspace
    result = await workspace.exec("printf '%s' \"$HOME\"", shell=True, timeout=5)
    if result.exit_code != 0:
        raise RuntimeError(
            result.stderr.strip() or "could not resolve environment home"
        )
    home = result.stdout.strip()
    try:
        # ``fs_get_home`` is consumed by the same picker as ``fs_list_dir``.
        # A sandbox home commonly lives outside the manifest root, so exposing
        # it would only make the next RPC fail its workspace boundary check.
        visible = workspace.logical_path(home)
    except Exception:
        visible = workspace.logical_path(".")
    return {"path": visible}


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
    resource_scope=ResourceScope.WORKSPACE,
    execution_requirements=ExecutionRequirement.WORKSPACE_READ,
)
async def fs_get_sources() -> dict:
    context = require_request_context()
    if context.workspace is None:
        raise RuntimeError("workspace source lookup requires a workspace resource")
    return {"sources": [dict(source) for source in context.workspace.sources]}


@server_function(
    description="List a directory for picker UIs",
    strict=True,
    resource_scope=ResourceScope.WORKSPACE,
    execution_requirements=ExecutionRequirement.WORKSPACE_READ,
)
async def fs_list_dir(
    path: Optional[str] = None,
    include_files: Optional[bool] = False,
    ignore_hidden: Optional[bool] = True,
) -> dict:
    workspace = require_environment_lease().workspace
    requested = path or "."
    try:
        # RPC paths are environment-visible logical paths.  A composite host
        # workspace resolves mounted sources to their physical backing trees
        # for I/O, but returning those paths here leaks host layout and makes
        # the next picker request escape the logical workspace boundary.
        base = workspace.logical_path(requested)
        file_entries = await workspace.list_dir(
            requested, include_hidden=not bool(ignore_hidden)
        )
        entries: List[Dict] = []
        for entry in file_entries:
            if not include_files and not entry.is_dir:
                continue
            entries.append(
                {
                    "name": entry.name,
                    "path": workspace.logical_path(entry.path),
                    "is_dir": entry.is_dir,
                    "size": entry.size,
                    "modified": entry.mtime.isoformat() if entry.mtime else None,
                }
            )
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        root = workspace.logical_path(".")
        if base == root:
            up = None
        else:
            relative = workspace.relative_path(workspace.resolve(base))
            if relative in (None, "."):
                up = None
            else:
                parent_relative = relative.rsplit("/", 1)[0] if "/" in relative else "."
                up = workspace.logical_path(parent_relative)
        return {"path": base, "parent": up, "entries": entries}
    except Exception as e:
        raise ValueError(str(e))


# Intentionally no ``session_ensure`` here: omni-code's ``session_ensure``
# was a proxy for the framework's own builtin ``session.ensure``
# (registered in ``omniagents.core.agents.service``), which clients should
# call directly. The omni-code shim dies downstream.
