"""Builtin ``terminal.create`` / ``terminal.close`` server functions.

The RPC surface the web UI's terminal panel and ``/ws/terminal`` socket
are built against (see ``omniagents/rpc/terminal_backends.py`` and the
``terminal:`` block in ``omniagents/core/sandbox/profiles.py``). Activated
by the ``terminal`` capability pack; lifted from omni-code.

Two binding modes:

* The embedder (a sandboxing launcher like ``omni serve``) installs a
  configured :class:`~omniagents.rpc.terminal.TerminalManager` on the
  service (``service.terminal_manager``) whose backend runs the shell
  inside the user's sandbox. The manager then owns the terminal defaults
  (shell/cwd/user from the sandbox profile).
* Standalone (no manager installed), a host-pty
  :class:`~omniagents.rpc.terminal.TerminalManager` is created lazily and
  the cwd resolves from the agent's session context.

``TerminalManager`` is imported lazily: pty support is Unix-only, and
builtin server-function modules load during every project discovery pass.
"""

from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.core.server_functions.helpers import session_workspace_root
from omniagents.core.session import Session

_terminal_manager = None
_terminal_import_error: Optional[Exception] = None


def _get_terminal_manager():
    global _terminal_manager, _terminal_import_error
    if _terminal_manager is not None:
        return _terminal_manager
    if _terminal_import_error is not None:
        raise RuntimeError(
            "Terminal support is unavailable on this platform"
        ) from _terminal_import_error
    try:
        from omniagents.rpc.terminal import TerminalManager
    except ImportError as exc:  # pragma: no cover - platform dependent
        _terminal_import_error = exc
        raise RuntimeError("Terminal support is unavailable on this platform") from exc
    _terminal_manager = TerminalManager()
    return _terminal_manager


def attach_terminal_manager(service) -> bool:
    """Install the shared host-pty manager on *service*. Returns ``False``
    when terminal support is unavailable on this platform."""
    try:
        manager = _get_terminal_manager()
    except RuntimeError:
        return False
    setattr(service, "terminal_manager", manager)
    return True


def terminal_supported() -> bool:
    try:
        _get_terminal_manager()
        return True
    except RuntimeError:
        return False


def _coerce_positive_int(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    if ivalue <= 0:
        return None
    return ivalue


def _resolve_workspace_root(session: Session, override: Optional[str]) -> Optional[str]:
    if override and override.strip():
        return override
    return session_workspace_root(session)


@server_function(
    name_override="terminal.create",
    description="Start an interactive shell for this session",
    params_schema={
        "type": "object",
        "properties": {
            "shell": {"type": "string"},
            "cwd": {"type": "string"},
            "cols": {"type": "integer", "minimum": 10},
            "rows": {"type": "integer", "minimum": 5},
        },
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {
            "terminal_id": {"type": "string"},
            "token": {"type": "string"},
            "shell": {"type": "string"},
            "cwd": {"type": ["string", "null"]},
            "created_at": {"type": "string"},
            "cols": {"type": "integer"},
            "rows": {"type": "integer"},
        },
        "required": ["terminal_id", "token"],
    },
    strict=True,
)
async def terminal_create(
    service,
    session: Session,
    shell: Optional[str] = None,
    cwd: Optional[str] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
) -> dict:
    width = _coerce_positive_int(cols)
    height = _coerce_positive_int(rows)

    # Two binding modes. When the embedder installs a
    # ``service.terminal_manager``, it owns the terminal's defaults
    # (shell/cwd/user from the sandbox profile). We must NOT consult
    # ``session.context['workspace_root']`` here — that's a *host* path
    # which doesn't exist inside the sandbox. Only the caller's explicit
    # cwd is honored; otherwise the manager's ``default_cwd`` wins.
    #
    # When no manager is installed (standalone omniagents server, no
    # sandbox), we fall back to the host-pty manager and resolve cwd
    # from the agent's session context.
    manager = getattr(service, "terminal_manager", None)
    if manager is not None:
        data = await manager.create_terminal(
            session.id,
            shell=shell,
            cwd=cwd,
            cols=width,
            rows=height,
            owner_user_id=getattr(session, "user_id", None),
        )
        data["cwd"] = data.get("cwd")
    else:
        manager = _get_terminal_manager()
        try:
            setattr(service, "terminal_manager", manager)
        except Exception:
            pass
        resolved_cwd = _resolve_workspace_root(session, cwd)
        data = await manager.create_terminal(
            session.id,
            shell=shell,
            cwd=resolved_cwd,
            cols=width,
            rows=height,
            owner_user_id=getattr(session, "user_id", None),
        )
        data["cwd"] = resolved_cwd

    data["path"] = "/ws/terminal"
    data["terminal_token"] = data.get("token")
    data["session_id"] = session.id
    return data


@server_function(
    name_override="terminal.close",
    description="Stop a running terminal for this session",
    params_schema={
        "type": "object",
        "properties": {"terminal_id": {"type": "string"}},
        "required": ["terminal_id"],
        "additionalProperties": False,
    },
    result_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
    strict=True,
)
async def terminal_close(service, session: Session, terminal_id: str) -> dict:
    manager = getattr(service, "terminal_manager", None) or _get_terminal_manager()
    try:
        if getattr(service, "terminal_manager", None) is None:
            setattr(service, "terminal_manager", manager)
    except Exception:
        pass
    await manager.close_terminal(session.id, terminal_id)
    return {"ok": True}


TERMINAL_SERVER_FUNCTIONS = [terminal_create, terminal_close]
