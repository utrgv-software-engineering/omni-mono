"""Tests for the builtin ``terminal.create`` / ``terminal.close``."""

import asyncio
import textwrap
from pathlib import Path
from unittest.mock import Mock

import pytest

from omniagents.builtin.server_functions import terminal as terminal_mod
from omniagents.builtin.server_functions.terminal import (
    terminal_close,
    terminal_create,
)
from omniagents.execution import (
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    OperationExecutionDescriptor,
    RequestContext,
    TerminalExecutionDefaults,
    bind_request_context,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope
from omniagents.workspace.base import Workspace


@pytest.fixture(autouse=True)
def _routed_terminal_request():
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="env-1",
        workspace_id="workspace-1",
        kind=EnvironmentKind.HOST,
        state=EnvironmentState.READY,
        generation=2,
        capabilities=frozenset({EnvironmentCapability.PTY}),
    )
    workspace = Mock(spec=Workspace)
    workspace.root = "/leased/workspace"
    lease = EnvironmentLease(descriptor, workspace)
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="terminal.create",
                resource_scope=ResourceScope.THREAD,
                execution_requirements=ExecutionRequirement.PTY,
            ),
            principal=None,
            environment=descriptor,
            environment_lease=lease,
        )
    )
    yield
    reset_request_context(token)


class _FakeSession:
    def __init__(self, context=None):
        self.id = "sess-1"
        self.context = context


class _FakeManager:
    def __init__(self):
        self.create_calls = []
        self.close_calls = []

    async def create_terminal(
        self,
        session_id,
        *,
        shell=None,
        cwd=None,
        cols=None,
        rows=None,
        user=None,
        owner_user_id=None,
        backend=None,
        environment_id=None,
        environment_generation=None,
    ):
        self.create_calls.append(
            {
                "session_id": session_id,
                "shell": shell,
                "cwd": cwd,
                "cols": cols,
                "rows": rows,
                "user": user,
                "environment_id": environment_id,
                "environment_generation": environment_generation,
            }
        )
        return {
            "terminal_id": "t-1",
            "token": "tok-1",
            "shell": shell or "/bin/bash",
            "cwd": cwd,
            "created_at": "2026-01-01T00:00:00",
            "cols": cols or 80,
            "rows": rows or 24,
        }

    async def close_terminal(self, session_id, terminal_id, *, environment_id=None):
        self.close_calls.append((session_id, terminal_id, environment_id))


class _Service:
    pass


def test_terminal_uses_leased_workspace_not_session_context():
    svc = _Service()
    manager = _FakeManager()
    svc.terminal_manager = manager
    session = _FakeSession(context={"workspace_root": "/host/project"})

    data = asyncio.run(terminal_create(svc, session))

    assert manager.create_calls == [
        {
            "session_id": "sess-1",
            "shell": None,
            "cwd": "/leased/workspace",
            "cols": None,
            "rows": None,
            "user": None,
            "environment_id": "env-1",
            "environment_generation": 2,
        }
    ]
    assert data["path"] == "/ws/terminal"
    assert data["terminal_token"] == "tok-1"
    assert data["session_id"] == "sess-1"


def test_terminal_defaults_are_owned_by_environment_lease():
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id="env-profiled",
        workspace_id="workspace-profiled",
        kind=EnvironmentKind.HOST,
        state=EnvironmentState.READY,
        generation=1,
        capabilities=frozenset({EnvironmentCapability.PTY}),
    )
    workspace = Mock(spec=Workspace)
    workspace.root = "/workspace-root"
    lease = EnvironmentLease(
        descriptor,
        workspace,
        terminal_defaults=TerminalExecutionDefaults(
            shell="/bin/zsh",
            cwd="/profile/cwd",
            user="profile-user",
        ),
    )
    token = bind_request_context(
        RequestContext(
            operation=OperationExecutionDescriptor(
                name="terminal.create",
                resource_scope=ResourceScope.THREAD,
                execution_requirements=ExecutionRequirement.PTY,
            ),
            principal=None,
            environment=descriptor,
            environment_lease=lease,
        )
    )
    try:
        svc = _Service()
        svc.terminal_manager = _FakeManager()
        asyncio.run(terminal_create(svc, _FakeSession()))
        call = svc.terminal_manager.create_calls[0]
        assert call["shell"] == "/bin/zsh"
        assert call["cwd"] == "/profile/cwd"
        assert call["user"] == "profile-user"
    finally:
        reset_request_context(token)


def test_embedder_manager_honors_explicit_cwd_and_size():
    svc = _Service()
    manager = _FakeManager()
    svc.terminal_manager = manager

    data = asyncio.run(
        terminal_create(svc, _FakeSession(), cwd="/workspace/sub", cols=120, rows=40)
    )

    call = manager.create_calls[0]
    assert call["cwd"] == "/workspace/sub"
    assert call["cols"] == 120 and call["rows"] == 40
    assert data["cwd"] == "/workspace/sub"


def test_manager_registry_is_lazy_but_execution_backend_is_leased(monkeypatch):
    svc = _Service()
    manager = _FakeManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: manager)
    session = _FakeSession(context={"workspace_root": "/host/project"})

    data = asyncio.run(terminal_create(svc, session, cols=0, rows=-3))

    assert svc.terminal_manager is manager
    call = manager.create_calls[0]
    assert call["cwd"] == "/leased/workspace"
    # Non-positive sizes are dropped, not forwarded.
    assert call["cols"] is None and call["rows"] is None
    assert data["cwd"] == "/leased/workspace"
    assert data["terminal_token"] == "tok-1"


def test_host_fallback_explicit_cwd_beats_session(monkeypatch):
    svc = _Service()
    manager = _FakeManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: manager)
    session = _FakeSession(context={"workspace_root": "/host/project"})

    asyncio.run(terminal_create(svc, session, cwd="/elsewhere"))

    assert manager.create_calls[0]["cwd"] == "/elsewhere"


def test_close_uses_installed_manager():
    svc = _Service()
    manager = _FakeManager()
    svc.terminal_manager = manager

    result = asyncio.run(terminal_close(svc, _FakeSession(), "t-1"))

    assert result == {"ok": True}
    assert manager.close_calls == [("sess-1", "t-1", "env-1")]


def test_close_falls_back_and_installs_manager(monkeypatch):
    svc = _Service()
    manager = _FakeManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: manager)

    result = asyncio.run(terminal_close(svc, _FakeSession(), "t-9"))

    assert result == {"ok": True}
    assert svc.terminal_manager is manager
    assert manager.close_calls == [("sess-1", "t-9", "env-1")]


# ----------------------------- capability pack -------------------------- #


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def test_terminal_pack_exposes_exact_rpc_surface(tmp_path):
    """terminal.create / terminal.close are the contract the /ws/terminal
    socket and web UI are built against — verbatim names."""
    from omniagents.core.project.runtime import load_project

    _write(tmp_path / "project.yml", "name: test\n")
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        """,
    )

    proj = load_project(str(tmp_path))

    assert "terminal" in proj.capability_registry
    cap = proj.capability_registry["terminal"]
    names = [m["name"] for m in cap.server_functions()]
    assert names == ["terminal.create", "terminal.close"]
    assert cap.tools() == []
    assert "terminal.create" in proj.builtin_server_function_names
    assert "terminal.close" in proj.builtin_server_function_names
