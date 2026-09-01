"""Verify ``stream_agent`` binds only its immutable environment lease."""

from __future__ import annotations

import types

import pytest

import omniagents.core.runtime.bridge as bridge
from omniagents import workspace as ws
from omniagents.execution import (
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
)
from omniagents.workspace import HostWorkspace


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
async def test_stream_agent_installs_workspace_from_environment_lease(
    monkeypatch, tmp_path
):

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

    prior_workspace = HostWorkspace(str(tmp_path / "prior"))
    prior_token = ws.set_workspace(prior_workspace)
    workspace_root = str(tmp_path)
    lease = EnvironmentLease(
        ExecutionEnvironmentDescriptor(
            environment_id="environment-1",
            workspace_id="workspace-1",
            kind=EnvironmentKind.HOST,
            state=EnvironmentState.READY,
            generation=1,
        ),
        HostWorkspace(workspace_root),
    )

    try:
        await bridge.stream_agent(
            agent,
            "prompt",
            session,
            rpc_channel=channel,
            max_turns=1,
            context={"workspace_root": "/must/not/select/a/backend"},
            environment_lease=lease,
        )

        assert captured_root["root"] == workspace_root
        assert ws.current_workspace() is prior_workspace
    finally:
        ws.reset_workspace(prior_token)


@pytest.mark.asyncio
async def test_targetless_stream_clears_inherited_workspace_for_the_run(
    monkeypatch,
):
    """A targetless run cannot inherit a caller task's workspace binding."""

    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    captured_root: dict = {}

    class FakeRunResult:
        async def stream_events(self):
            try:
                ws.workspace_root()
            except ws.WorkspaceNotBoundError:
                captured_root["unbound"] = True
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
    inherited = ws.HostWorkspace(root="/tmp")
    token = ws.set_workspace(inherited)
    try:
        await bridge.stream_agent(
            agent,
            "prompt",
            session,
            rpc_channel=DummyChannel(),
            max_turns=1,
            context={"some": "other"},
        )
        assert captured_root == {"unbound": True}
        assert ws.current_workspace() is inherited
    finally:
        ws.reset_workspace(token)


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
            captured["confine"] = active.enforces_confinement
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

    workspace = ws.HostWorkspace(root=str(tmp_path))
    lease = EnvironmentLease(
        ExecutionEnvironmentDescriptor(
            environment_id="environment-unconfined",
            workspace_id="workspace-unconfined",
            kind=EnvironmentKind.HOST,
            state=EnvironmentState.READY,
            generation=1,
        ),
        workspace,
    )
    await bridge.stream_agent(
        agent,
        "prompt",
        DummySession(),
        rpc_channel=DummyChannel(),
        max_turns=1,
        context={"workspace_root": "/must/not/select/a/backend"},
        environment_lease=lease,
    )

    assert captured["root"] == str(tmp_path)
    assert captured["confine"] is False
    assert captured["sibling"] == str(tmp_path.parent)
