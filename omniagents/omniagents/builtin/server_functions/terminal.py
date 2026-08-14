"""Builtin ``terminal.create`` / ``terminal.close`` server functions.

The RPC surface the web UI's terminal panel and ``/ws/terminal`` socket
are built against (see ``omniagents/rpc/terminal_backends.py`` and the
``terminal:`` block in ``omniagents/core/sandbox/profiles.py``). Activated
by the ``terminal`` capability pack; lifted from omni-code.

The operation router binds an immutable EnvironmentLease before invoking a
handler.  The terminal backend is selected from that lease; a missing binding
never falls back to a host shell.

``TerminalManager`` is imported lazily: pty support is Unix-only, and
builtin server-function modules load during every project discovery pass.
"""

from __future__ import annotations

from typing import Optional

from omniagents import server_function
from omniagents.core.session import Session
from omniagents.execution import require_environment_lease
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope

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


def _backend_for_lease(lease):
    # Keyed on the lease's sandbox runtime, NOT descriptor.kind — kind is
    # the placement family (a unix_local environment is host-placed, but
    # its shell must ride the sandbox session exactly like its tools do).
    # No sandbox session → the shell runs as a child of serve on the host.
    if lease.sandbox_runtime is None:
        from omniagents.rpc.terminal_backends import HostPtyBackend

        return HostPtyBackend()

    from omniagents.core.sandbox.pty_stream import coerce_pty_streaming
    from omniagents.rpc.terminal_backends import SessionPtyBackend
    from omniagents.workspace.sandbox import SandboxWorkspace

    workspace = lease.workspace
    if not isinstance(workspace, SandboxWorkspace):
        raise RuntimeError(f"environment {lease.environment_id!r} has no PTY backend")
    inner = coerce_pty_streaming(workspace.session)
    if inner is None:
        raise RuntimeError(
            f"environment {lease.environment_id!r} does not support interactive PTYs"
        )
    return SessionPtyBackend(inner)


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
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.PTY,
)
async def terminal_create(
    service,
    session: Session,
    shell: Optional[str] = None,
    cwd: Optional[str] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
) -> dict:
    lease = require_environment_lease()
    width = _coerce_positive_int(cols)
    height = _coerce_positive_int(rows)
    manager = getattr(service, "terminal_manager", None) or _get_terminal_manager()
    if getattr(service, "terminal_manager", None) is None:
        setattr(service, "terminal_manager", manager)
    defaults = lease.terminal_defaults
    resolved_shell = shell if shell and shell.strip() else defaults.shell
    resolved_cwd = cwd if cwd and cwd.strip() else defaults.cwd or lease.workspace.root
    data = await manager.create_terminal(
        session.id,
        shell=resolved_shell,
        cwd=resolved_cwd,
        cols=width,
        rows=height,
        user=defaults.user,
        owner_user_id=getattr(session, "user_id", None),
        backend=_backend_for_lease(lease),
        environment_id=lease.environment_id,
        environment_generation=lease.generation,
    )
    data["cwd"] = resolved_cwd

    data["path"] = "/ws/terminal"
    data["terminal_token"] = data.get("token")
    data["session_id"] = session.id
    data["environment_id"] = lease.environment_id
    data["environment_generation"] = lease.generation
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
    resource_scope=ResourceScope.THREAD,
    execution_requirements=ExecutionRequirement.PTY,
)
async def terminal_close(service, session: Session, terminal_id: str) -> dict:
    lease = require_environment_lease()
    manager = getattr(service, "terminal_manager", None) or _get_terminal_manager()
    try:
        if getattr(service, "terminal_manager", None) is None:
            setattr(service, "terminal_manager", manager)
    except Exception:
        pass
    await manager.close_terminal(
        session.id, terminal_id, environment_id=lease.environment_id
    )
    return {"ok": True}


TERMINAL_SERVER_FUNCTIONS = [terminal_create, terminal_close]
