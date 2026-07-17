import os
from datetime import datetime
from typing import Optional, List, Dict

from omniagents import server_function
from omniagents.core.session import Session


def _is_hidden(path: str) -> bool:
    try:
        parts = os.path.normpath(path).split(os.sep)
        for p in parts:
            if p.startswith('.') and p not in ('.', '..'):
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


@server_function(description="Get workspace root for current session (falls back to manifest root, then cwd)", strict=True)
async def fs_get_workspace_root(session: Session) -> dict:
    from omni_code.utility_runs import session_workspace_root

    wr = session_workspace_root(session)
    if wr:
        return {"path": os.path.abspath(wr)}
    # When the session has no stored workspace_root yet, prefer the
    # sandbox manifest root over omni serve's host cwd — they're the same
    # for unix_local but differ for clients that mount under /workspace.
    try:
        from omni_code.agent_workspace_state import get_manifest_root

        manifest_root = get_manifest_root()
        if isinstance(manifest_root, str) and manifest_root.strip():
            return {"path": manifest_root}
    except Exception:
        pass
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
    try:
        from omni_code.agent_workspace_state import get_sources

        return {"sources": get_sources()}
    except Exception:
        return {"sources": []}


async def _fs_list_dir_in_sandbox(
    session: object,
    path: Optional[str],
    include_files: bool,
    ignore_hidden: bool,
) -> dict:
    """List a directory INSIDE the sandbox container, for the picker.

    The picker browses the filesystem the agent's tools actually see — the
    container — not omni serve's host. Defaults to the manifest root. Paths
    are absolute container paths so the picker can navigate by them.
    """
    from pathlib import PurePosixPath

    from omniagents.workspace import clamp_within
    from omniagents.workspace.sandbox import SandboxWorkspace

    from omni_code.agent_workspace_state import get_manifest_root

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
async def fs_list_dir(path: Optional[str] = None, include_files: Optional[bool] = False, ignore_hidden: Optional[bool] = True) -> dict:
    # When a sandbox session is live, browse the CONTAINER filesystem (what
    # the agent's tools see). With no session (the host target) fall through
    # to the host filesystem below.
    from omni_code.agent_workspace_state import get_sandbox_session

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


# Intentionally no server-side state for workspace.


@server_function(
    description="Ensure a session exists, optionally persisting workspace metadata",
    strict=True,
)
async def session_ensure(
    workspace_root: Optional[str] = None,
    context: Optional[Dict] = None,
    variables: Optional[Dict] = None,
) -> dict:
    return {
        "workspace_root": workspace_root,
        "context": context,
        "variables": variables,
    }


async def _session_ensure_on_invoke(service, session, args=None):
    sess = session
    if service is not None and (sess is None or not getattr(sess, "id", None)):
        sess = service.get_or_create_session(None)

    call_args = {}
    payload = args or {}
    for key in ("workspace_root", "context", "variables"):
        if key in payload and payload[key] is not None:
            call_args[key] = payload[key]

    if sess is None:
        sess_id = None
    else:
        sess_id = getattr(sess, "id", None)

    # Proxy to the builtin omniagents session.ensure so persisted metadata
    # (workspace root, context, variables) survives restarts.
    return await service.server_call(
        "session.ensure",
        call_args or None,
        session_id=sess_id,
    )


setattr(session_ensure, "_server_function_on_invoke", _session_ensure_on_invoke)
