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
    try:
        wr = None
        ctx = getattr(session, "context", None)
        if ctx is not None:
            if isinstance(ctx, dict):
                wr = ctx.get("workspace_root")
            else:
                wr = getattr(ctx, "workspace_root", None)
        if not wr:
            vars_ = getattr(session, "variables", None)
            if isinstance(vars_, dict):
                wr = vars_.get("workspace_root")
        if isinstance(wr, str) and wr.strip():
            return {"path": os.path.abspath(wr)}
    except Exception:
        pass
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


@server_function(description="List a directory for picker UIs", strict=True)
async def fs_list_dir(path: Optional[str] = None, include_files: Optional[bool] = False, ignore_hidden: Optional[bool] = True) -> dict:
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
    manager = getattr(service, "_session_manager", None)
    sess = session
    if manager is not None and (sess is None or not getattr(sess, "id", None)):
        sess = manager.get_or_create(None)

    call_args = {}
    payload = args or {}
    for key in ("workspace_root", "context", "variables"):
        if key in payload and payload[key] is not None:
            call_args[key] = payload[key]

    if sess is None:
        sess_id = None
    else:
        sess_id = getattr(sess, "id", None)

    try:
        # Proxy to the built-in omniagents session.ensure if present so persisted
        # metadata (workspace root, context, variables) survives restarts.
        return await service.server_call(
            "session.ensure",
            call_args or None,
            session_id=sess_id,
        )
    except ValueError:
        # Older omniagents builds don't register session.ensure. Fall back to
        # the legacy behavior (best-effort, in-memory only).
        result = {"session_id": sess_id}
        if sess is None:
            return result

        if "workspace_root" in call_args:
            wr_value = call_args["workspace_root"]
            ctx = sess.context if isinstance(getattr(sess, "context", None), dict) else {}
            ctx["workspace_root"] = wr_value
            sess.context = ctx
            vars_dict = sess.variables if isinstance(getattr(sess, "variables", None), dict) else {}
            vars_dict["workspace_root"] = wr_value
            sess.variables = vars_dict
            result["workspace_root"] = wr_value

        if "context" in call_args:
            sess.context = call_args["context"]
        if "variables" in call_args:
            sess.variables = call_args["variables"]

        result["context"] = getattr(sess, "context", None)
        result["variables"] = getattr(sess, "variables", None)
        return result


setattr(session_ensure, "_server_function_on_invoke", _session_ensure_on_invoke)
