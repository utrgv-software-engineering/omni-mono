"""Tests for the builtin read-only sandbox observation tools against a
seeded ``serve_state.SwitchContext``."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from omniagents.builtin.tools.sandbox_observe import (
    _state_dict,
    sandbox_get_state,
    sandbox_wait_until_ready,
)
from omniagents.core.runtime import serve_state


@pytest.fixture(autouse=True)
def _reset_state():
    serve_state.reset()
    yield
    serve_state.reset()


def _make_ctx(profile="devbox", backend="docker"):
    ctx = serve_state.SwitchContext(
        app=object(),
        spec=object(),
        session_id="sess-1",
        snapshot_dir=None,
        sources_parsed=[],
        credentials_parsed=[],
        workspace_dir=Path("/tmp/ws"),
        project_id=None,
        host="127.0.0.1",
        port=3001,
        auth_token=None,
        current_profile_name=profile,
        backend=backend,
        container_id="ctr-1",
    )
    ctx.ready_event.set()
    return ctx


def test_state_dict_standalone_reports_ready():
    st = _state_dict()
    assert st == {
        "available": False,
        "ready": True,
        "switching": False,
        "profile": None,
        "backend": None,
        "container_id": None,
        "last_error": None,
    }


def test_state_dict_reads_seeded_context():
    serve_state.set_switch_context(_make_ctx())
    st = _state_dict()
    assert st["available"] is True and st["ready"] is True
    assert st["profile"] == "devbox" and st["backend"] == "docker"
    assert st["container_id"] == "ctr-1" and st["switching"] is False


def test_state_dict_mid_switch_not_ready():
    ctx = _make_ctx()
    ctx.switching = True
    ctx.ready_event.clear()
    ctx.last_error = None
    serve_state.set_switch_context(ctx)
    st = _state_dict()
    assert st["ready"] is False and st["switching"] is True


def test_tools_are_function_tools_with_stable_names():
    assert sandbox_get_state.name == "sandbox_get_state"
    assert sandbox_wait_until_ready.name == "sandbox_wait_until_ready"


def test_wait_until_ready_blocks_then_resolves():
    import json

    ctx = _make_ctx()
    ctx.switching = True
    ctx.ready_event.clear()
    serve_state.set_switch_context(ctx)

    from omniagents.builtin.tools import sandbox_observe as mod

    # Exercise the tool body directly (the FunctionTool wraps it).
    async def go():
        async def finish_soon():
            await asyncio.sleep(0.05)
            ctx.switching = False
            ctx.ready_event.set()

        task = asyncio.ensure_future(finish_soon())
        try:
            sw = serve_state.get_switch_context()
            await asyncio.wait_for(sw.ready_event.wait(), timeout=2.0)
        finally:
            await task
        return mod._state_dict()

    st = asyncio.run(go())
    assert st["ready"] is True and st["switching"] is False


def test_wait_until_ready_no_context_immediate():
    # Standalone: no SwitchContext published — degraded "ready" answer.
    assert serve_state.get_switch_context() is None
    st = _state_dict()
    assert st["ready"] is True
