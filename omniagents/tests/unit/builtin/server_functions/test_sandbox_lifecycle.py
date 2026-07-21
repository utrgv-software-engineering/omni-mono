"""Tests for the builtin ``sandbox.*`` lifecycle server functions, the
switch-executor seam, and the builtin ``sandbox`` capability pack."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from omniagents.builtin.server_functions.sandbox_lifecycle import (
    SANDBOX_SERVER_FUNCTIONS,
    sandbox_discard_snapshot,
    sandbox_notify_activity,
    sandbox_pause,
    sandbox_switch,
    sandbox_unpause,
)
from omniagents.core.runtime import serve_state
from omniagents.core.runtime.sandbox_lifecycle import (
    set_switch_executor,
    switch_executor,
)


@pytest.fixture(autouse=True)
def _reset_state():
    serve_state.reset()
    set_switch_executor(None)
    yield
    serve_state.reset()
    set_switch_executor(None)


class _Inner:
    """Backend session with the docker-extended lifecycle surface."""

    def __init__(self, fail_pause=False):
        self._paused = False
        self.pings = 0
        self._fail_pause = fail_pause

    def set_dependencies(self, dependencies):
        pass

    async def pause(self):
        if self._fail_pause:
            raise RuntimeError("daemon down")
        self._paused = True

    async def unpause(self):
        self._paused = False

    async def paused(self):
        return self._paused

    async def notify_activity(self):
        self.pings += 1


class _NoLifecycle:
    def set_dependencies(self, dependencies):
        pass


def _publish(inner):
    from omniagents.core.sandbox.lifecycle_session import LifecycleSandboxSession

    session = LifecycleSandboxSession(inner)
    serve_state.set_sandbox_session(session)
    return session


# ----------------------------- pause / unpause -------------------------- #


def test_pause_unpause_happy_path():
    inner = _Inner()
    _publish(inner)

    res = asyncio.run(sandbox_pause(None))
    assert res == {"ok": True, "supported": True, "paused": True}
    res = asyncio.run(sandbox_unpause(None))
    assert res == {"ok": True, "supported": True, "paused": False}


def test_pause_without_session():
    res = asyncio.run(sandbox_pause(None))
    assert res["ok"] is False and res["supported"] is False
    assert "no live sandbox session" in res["reason"]


def test_pause_unsupported_backend():
    _publish(_NoLifecycle())
    res = asyncio.run(sandbox_pause(None))
    assert res == {
        "ok": False,
        "supported": False,
        "reason": "backend does not implement pause",
    }
    res = asyncio.run(sandbox_unpause(None))
    assert res["reason"] == "backend does not implement unpause"


def test_pause_backend_error_reports_supported():
    _publish(_Inner(fail_pause=True))
    res = asyncio.run(sandbox_pause(None))
    assert res == {"ok": False, "supported": True, "reason": "daemon down"}


def test_pause_works_on_bare_backend_session():
    # Duck-typed semantics: a session published without the SDK wrapper
    # (test fakes, custom embedders) still works.
    serve_state.set_sandbox_session(_Inner())
    assert asyncio.run(sandbox_pause(None))["ok"] is True


# ----------------------------- notify_activity -------------------------- #


def test_notify_activity():
    inner = _Inner()
    _publish(inner)
    assert asyncio.run(sandbox_notify_activity(None)) == {"ok": True}
    assert inner.pings == 1
    _publish(_NoLifecycle())
    assert asyncio.run(sandbox_notify_activity(None)) == {"ok": False}
    serve_state.set_sandbox_session(None)
    assert asyncio.run(sandbox_notify_activity(None)) == {"ok": False}


# ----------------------------- discard_snapshot ------------------------- #


def test_discard_snapshot_sets_flag():
    assert serve_state.get_discard_snapshot_on_exit() is False
    assert asyncio.run(sandbox_discard_snapshot(None)) == {"ok": True}
    assert serve_state.get_discard_snapshot_on_exit() is True


# ----------------------------- switch seam ------------------------------ #


def test_switch_without_executor_returns_clear_error():
    assert switch_executor() is None
    res = asyncio.run(sandbox_switch(None, "/profiles/devbox.yml"))
    assert res["ok"] is False
    assert "set_switch_executor" in res["reason"]


def test_switch_validates_profile():
    async def executor(profile):  # pragma: no cover - must not be reached
        raise AssertionError("called")

    set_switch_executor(executor)
    res = asyncio.run(sandbox_switch(None, "   "))
    assert res["ok"] is False and "non-empty" in res["reason"]


def test_switch_delegates_to_installed_executor():
    seen = []

    async def executor(profile):
        seen.append(profile)
        return {"ok": True, "profile": "devbox", "backend": "docker"}

    set_switch_executor(executor)
    res = asyncio.run(sandbox_switch(None, "/profiles/devbox.yml"))
    assert res == {"ok": True, "profile": "devbox", "backend": "docker"}
    assert seen == ["/profiles/devbox.yml"]
    # Seam is clearable.
    set_switch_executor(None)
    assert switch_executor() is None


# ----------------------- pack surface via load_project ------------------ #


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


def test_sandbox_pack_rpc_surface(tmp_path):
    from omniagents.core.project.runtime import load_project

    _write(tmp_path / "project.yml", "name: test\n")
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: be a test
        capabilities:
          - sandbox
        """,
    )

    proj = load_project(str(tmp_path))
    assert "sandbox" in proj.capability_registry
    cap = proj.capability_registry["sandbox"]

    tool_names = {t.name for t in cap.tools()}
    assert tool_names == {"sandbox_get_state", "sandbox_wait_until_ready"}

    sf_names = {m["name"] for m in cap.server_functions()}
    assert sf_names == {
        "sandbox.pause",
        "sandbox.unpause",
        "sandbox.notify_activity",
        "sandbox.discard_snapshot",
        "sandbox.switch",
    }

    instructions = asyncio.run(cap.instructions())
    assert "You CANNOT change" in instructions
    assert "sandbox_wait_until_ready" in instructions

    # The builtin server-function registry carries the RPC names.
    assert {"sandbox.pause", "sandbox.switch"}.issubset(
        set(proj.server_functions_registry.keys())
    )
    assert len(SANDBOX_SERVER_FUNCTIONS) == 5
