"""Tests for the builtin ``terminal.create`` / ``terminal.close``."""

import asyncio
import textwrap
from pathlib import Path

from omniagents.builtin.server_functions import terminal as terminal_mod
from omniagents.builtin.server_functions.terminal import (
    terminal_close,
    terminal_create,
)


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
        owner_user_id=None,
    ):
        self.create_calls.append(
            {
                "session_id": session_id,
                "shell": shell,
                "cwd": cwd,
                "cols": cols,
                "rows": rows,
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

    async def close_terminal(self, session_id, terminal_id):
        self.close_calls.append((session_id, terminal_id))


class _Service:
    pass


def test_embedder_manager_owns_defaults():
    """With a service-installed manager (sandbox mode), the session's
    workspace_root — a HOST path — must NOT leak into cwd; the manager's
    own default_cwd wins when the caller passes none."""
    svc = _Service()
    manager = _FakeManager()
    svc.terminal_manager = manager
    session = _FakeSession(context={"workspace_root": "/host/project"})

    data = asyncio.run(terminal_create(svc, session))

    assert manager.create_calls == [
        {"session_id": "sess-1", "shell": None, "cwd": None, "cols": None, "rows": None}
    ]
    assert data["path"] == "/ws/terminal"
    assert data["terminal_token"] == "tok-1"
    assert data["session_id"] == "sess-1"


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


def test_host_fallback_resolves_cwd_from_session(monkeypatch):
    """No embedder manager: the host-pty manager is created lazily,
    installed on the service, and cwd resolves from the session context."""
    svc = _Service()
    manager = _FakeManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: manager)
    session = _FakeSession(context={"workspace_root": "/host/project"})

    data = asyncio.run(terminal_create(svc, session, cols=0, rows=-3))

    assert svc.terminal_manager is manager
    call = manager.create_calls[0]
    assert call["cwd"] == "/host/project"
    # Non-positive sizes are dropped, not forwarded.
    assert call["cols"] is None and call["rows"] is None
    assert data["cwd"] == "/host/project"
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
    assert manager.close_calls == [("sess-1", "t-1")]


def test_close_falls_back_and_installs_manager(monkeypatch):
    svc = _Service()
    manager = _FakeManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: manager)

    result = asyncio.run(terminal_close(svc, _FakeSession(), "t-9"))

    assert result == {"ok": True}
    assert svc.terminal_manager is manager
    assert manager.close_calls == [("sess-1", "t-9")]


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
