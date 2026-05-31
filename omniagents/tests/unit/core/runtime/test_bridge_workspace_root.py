"""Verify ``stream_agent`` installs a HostWorkspace rooted at the agent's
``workspace_root`` so omniagents.workspace.* calls inside tool bodies
resolve relative to the configured root rather than the server cwd.
"""

from __future__ import annotations

import types

import pytest

import omniagents.core.runtime.bridge as bridge
from omniagents import workspace as ws


class DummyChannel:
    def __init__(self) -> None:
        self.calls = []

    async def async_call(self, method, args):
        self.calls.append((method, args))


class DummySession:
    def __init__(self) -> None:
        self.channels = []
        self.history = []
        self.pending_events = []
        self.id = "ws-session"

    def append_message(self, message):
        self.history.append(message)


@pytest.mark.asyncio
async def test_stream_agent_installs_workspace_root_from_context(monkeypatch, tmp_path):
    """When ``stream_agent`` is called with ``context={'workspace_root': X}``,
    the workspace contextvar should resolve to a HostWorkspace rooted at X
    for the duration of the run."""

    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    captured_root: dict = {}

    class FakeRunResult:
        async def stream_events(self):
            # Snapshot the workspace root visible to tool bodies at the
            # moment the SDK begins streaming events.
            captured_root["root"] = ws.workspace_root()
            if False:
                yield None  # make this an async generator

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult()

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.Runner", FakeRunner)
    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    agent = types.SimpleNamespace(
        name="Agent",
        instructions="",
        tools=[],
        model="model-x",
        model_settings=types.SimpleNamespace(max_tokens=None, truncation=None),
    )

    session = DummySession()
    channel = DummyChannel()

    before_root = ws.workspace_root()
    workspace_root = str(tmp_path)

    await bridge.stream_agent(
        agent,
        "prompt",
        session,
        rpc_channel=channel,
        max_turns=1,
        context={"workspace_root": workspace_root},
    )

    assert captured_root["root"] == workspace_root
    # Token should have been reset; the workspace returns to its prior state.
    assert ws.workspace_root() == before_root


@pytest.mark.asyncio
async def test_stream_agent_without_workspace_root_leaves_contextvar_untouched(
    monkeypatch,
):
    """When ``context`` carries no workspace_root, the contextvar should
    not be overwritten — falls back to whatever was set previously (or the
    lazy default)."""

    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    captured_root: dict = {}

    class FakeRunResult:
        async def stream_events(self):
            captured_root["root"] = ws.workspace_root()
            if False:
                yield None

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult()

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.Runner", FakeRunner)
    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    agent = types.SimpleNamespace(
        name="Agent",
        instructions="",
        tools=[],
        model="model-x",
        model_settings=types.SimpleNamespace(max_tokens=None, truncation=None),
    )

    session = DummySession()
    expected_root = ws.workspace_root()

    await bridge.stream_agent(
        agent,
        "prompt",
        session,
        rpc_channel=DummyChannel(),
        max_turns=1,
        context={"some": "other"},
    )

    assert captured_root["root"] == expected_root


@pytest.mark.asyncio
async def test_stream_agent_preserves_unconfined_host_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    captured = {}

    class FakeRunResult:
        async def stream_events(self):
            active = ws.current_workspace()
            captured["root"] = ws.workspace_root()
            captured["confine"] = getattr(active, "confine", None)
            captured["sibling"] = active.resolve(str(tmp_path.parent))
            if False:
                yield None

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult()

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.Runner", FakeRunner)
    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    agent = types.SimpleNamespace(
        name="Agent",
        instructions="",
        tools=[],
        model="model-x",
        model_settings=types.SimpleNamespace(max_tokens=None, truncation=None),
    )

    workspace = ws.HostWorkspace(root=str(tmp_path), confine=False)
    token = ws.set_workspace(workspace)
    try:
        await bridge.stream_agent(
            agent,
            "prompt",
            DummySession(),
            rpc_channel=DummyChannel(),
            max_turns=1,
            context={"workspace_root": str(tmp_path)},
        )
    finally:
        ws.reset_workspace(token)

    assert captured["root"] == str(tmp_path)
    assert captured["confine"] is False
    assert captured["sibling"] == str(tmp_path.parent)
