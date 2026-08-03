"""Tests for the temporary serve-process profile-switch composition state."""

import pytest

from omniagents.core.runtime import serve_state
from omniagents.core.runtime.serve_state import SwitchContext


@pytest.fixture(autouse=True)
def _reset():
    serve_state.reset()
    yield
    serve_state.reset()


def _switch_ctx() -> SwitchContext:
    return SwitchContext(
        app=object(),
        spec=object(),
        session_id="s1",
        environment_id="environment-1",
        workspace_id="workspace-1",
        snapshot_dir=None,
        sources_parsed=[],
        credentials_parsed=[],
        workspace_dir="/w",
        project_id=None,
        host="127.0.0.1",
        port=1234,
        auth_token=None,
    )


def test_switch_context_defaults_to_absent():
    assert serve_state.get_switch_context() is None


def test_switch_context_publish_reset_and_clear():
    ctx = _switch_ctx()
    serve_state.set_switch_context(ctx)
    assert serve_state.get_switch_context() is ctx
    assert ctx.switching is False
    assert ctx.ready_event.is_set() is False

    serve_state.reset()
    assert serve_state.get_switch_context() is None
