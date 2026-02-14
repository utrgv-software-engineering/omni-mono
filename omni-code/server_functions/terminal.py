from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.core.session import Session


_terminal_manager = None
_terminal_import_error: Optional[Exception] = None


def _get_terminal_manager():
    global _terminal_manager, _terminal_import_error
    if _terminal_manager is not None:
        return _terminal_manager
    if _terminal_import_error is not None:
        raise RuntimeError("Terminal support is unavailable on this platform") from _terminal_import_error
    try:
        from omniagents.rpc.terminal import TerminalManager
    except ImportError as exc:  # pragma: no cover - platform dependent
        _terminal_import_error = exc
        raise RuntimeError("Terminal support is unavailable on this platform") from exc
    _terminal_manager = TerminalManager()
    return _terminal_manager


def attach_terminal_manager(service) -> bool:
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
    ctx = getattr(session, "context", None)
    if isinstance(ctx, dict):
        value = ctx.get("workspace_root")
        if isinstance(value, str) and value.strip():
            return value
    if ctx is not None:
        value = getattr(ctx, "workspace_root", None)
        if isinstance(value, str) and value.strip():
            return value
    variables = getattr(session, "variables", None)
    if isinstance(variables, dict):
        value = variables.get("workspace_root")
        if isinstance(value, str) and value.strip():
            return value
    return None


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
    manager = _get_terminal_manager()
    try:
        if getattr(service, "terminal_manager", None) is None:
            setattr(service, "terminal_manager", manager)
    except Exception:
        pass
    width = _coerce_positive_int(cols)
    height = _coerce_positive_int(rows)
    resolved_cwd = _resolve_workspace_root(session, cwd)
    data = await manager.create_terminal(
        session.id,
        shell=shell,
        cwd=resolved_cwd,
        cols=width,
        rows=height,
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
