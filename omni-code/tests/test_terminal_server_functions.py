"""Behavioral checks against the framework's builtin terminal server
functions (``omniagents.builtin.server_functions.terminal``) — the module
the omni agent's ``terminal`` capability pack activates. The standalone
fallback path (no embedder-installed manager) must keep resolving the cwd
from the session context, since omni-code relies on it for the host
execution target."""

import pytest
from omniagents.builtin.server_functions import terminal as terminal_mod


class FakeTerminalManager:
    def __init__(self):
        self.created = []
        self.closed = []

    async def create_terminal(
        self, session_id, *, shell=None, cwd=None, cols=None, rows=None
    ):
        self.created.append(
            {
                "session_id": session_id,
                "shell": shell,
                "cwd": cwd,
                "cols": cols,
                "rows": rows,
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

    async def close_terminal(self, session_id, terminal_id):
        self.closed.append({"session_id": session_id, "terminal_id": terminal_id})


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
    assert fake_manager.created == [
        {
            "session_id": "sess-1",
            "shell": "bash",
            "cwd": "/tmp/workspace",
            "cols": 120,
            "rows": 40,
        }
    ]
    assert result["terminal_id"] == "term-1"
    assert result["token"] == "tok-1"
    assert result["terminal_token"] == "tok-1"
    assert result["path"] == "/ws/terminal"
    assert result["session_id"] == "sess-1"
    assert result["cwd"] == "/tmp/workspace"


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
    assert fake_manager.closed == [{"session_id": "sess-1", "terminal_id": "term-1"}]
