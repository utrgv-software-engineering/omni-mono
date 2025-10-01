import os
from datetime import datetime
from typing import Optional, List, Dict

from omniagents import server_function
from omniagents.core.session import Session, sm


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


@server_function(description="Get workspace root for current session (fallback to cwd)", strict=True)
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


@server_function(description="Ensure a session exists and return its id", strict=True)
async def session_ensure() -> dict:
    s = sm.get_or_create(None)
    return {"session_id": s.id}
