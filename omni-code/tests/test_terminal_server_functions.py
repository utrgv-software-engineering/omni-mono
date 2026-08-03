"""Behavioral checks for the framework's builtin terminal server functions.

The omni agent's ``terminal`` capability pack activates these functions. Their
shell, cwd, backend, and ownership all come from the dispatcher-bound
EnvironmentLease; session context is not an execution-routing fallback.
"""

import pytest
from omniagents.builtin.server_functions import terminal as terminal_mod


class FakeTerminalManager:
    def __init__(self):
        self.created = []
        self.closed = []

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
        self.created.append(
            {
                "session_id": session_id,
                "shell": shell,
                "cwd": cwd,
                "cols": cols,
                "rows": rows,
                "user": user,
                "owner_user_id": owner_user_id,
                "backend": backend,
                "environment_id": environment_id,
                "environment_generation": environment_generation,
            }
        )
        return {
            "terminal_id": "term-1",
            "token": "tok-1",
            "shell": shell or "bash",
            "created_at": "now",
            "cols": cols or 80,
            "rows": rows or 24,
        }

    async def close_terminal(self, session_id, terminal_id, *, environment_id=None):
        self.closed.append(
            {
                "session_id": session_id,
                "terminal_id": terminal_id,
                "environment_id": environment_id,
            }
        )


class FakeSession:
    def __init__(self):
        self.id = "sess-1"
        self.context = {"workspace_root": "/tmp/workspace"}
        self.variables = {}


@pytest.mark.asyncio
async def test_terminal_create_attaches_manager_and_returns_ws_path(monkeypatch):
    fake_manager = FakeTerminalManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: fake_manager)

    handler = getattr(terminal_mod.terminal_create, "_server_function_on_invoke")

    class FakeService:
        pass

    svc = FakeService()
    sess = FakeSession()

    result = await handler(
        svc,
        sess,
        {
            "shell": "bash",
            "cols": 120,
            "rows": 40,
        },
    )

    assert getattr(svc, "terminal_manager", None) is fake_manager
    assert len(fake_manager.created) == 1
    created = fake_manager.created[0]
    assert created["session_id"] == "sess-1"
    assert created["shell"] == "bash"
    assert created["cwd"] == "/"
    assert created["cols"] == 120
    assert created["rows"] == 40
    assert created["user"] is None
    assert created["owner_user_id"] is None
    assert created["backend"].__class__.__name__ == "HostPtyBackend"
    assert created["environment_id"] == "test-host-environment"
    assert created["environment_generation"] == 1
    assert result["terminal_id"] == "term-1"
    assert result["token"] == "tok-1"
    assert result["terminal_token"] == "tok-1"
    assert result["path"] == "/ws/terminal"
    assert result["session_id"] == "sess-1"
    assert result["cwd"] == "/"
    assert result["environment_id"] == "test-host-environment"
    assert result["environment_generation"] == 1


@pytest.mark.asyncio
async def test_terminal_close_uses_attached_manager(monkeypatch):
    fake_manager = FakeTerminalManager()
    monkeypatch.setattr(terminal_mod, "_get_terminal_manager", lambda: fake_manager)

    handler = getattr(terminal_mod.terminal_close, "_server_function_on_invoke")

    class FakeService:
        terminal_manager = fake_manager

    svc = FakeService()
    sess = FakeSession()

    result = await handler(svc, sess, {"terminal_id": "term-1"})

    assert result == {"ok": True}
    assert fake_manager.closed == [
        {
            "session_id": "sess-1",
            "terminal_id": "term-1",
            "environment_id": "test-host-environment",
        }
    ]
