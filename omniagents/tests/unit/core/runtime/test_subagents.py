"""The subagent observability bus: agent-tool observer, end signal,
snapshot merging, and the unified event envelope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from omniagents.core.runtime import subagents
from omniagents.core.session.helpers import (
    reset_current_session_id,
    set_current_session_id,
)


class FakeService:
    def __init__(self):
        self.calls = []
        self.session = SimpleNamespace(id="sess-1")

    def get_session(self, session_id):
        return self.session if session_id == self.session.id else None

    async def request_client_function(self, function, args, **kwargs):
        self.calls.append((function, args))


@pytest.fixture(autouse=True)
def clean_bus(monkeypatch):
    monkeypatch.setattr(subagents, "_AGENT_TOOL_RUNS", {})
    monkeypatch.setattr(subagents, "_SNAPSHOT_PROVIDERS", [])
    yield


@pytest.fixture
def service(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr(subagents, "_current_service", lambda: svc)
    return svc


def tool_call(call_id="call-1", arguments='{"question": "how?"}'):
    return SimpleNamespace(call_id=call_id, arguments=arguments)


def run_item_event(item):
    return SimpleNamespace(type="run_item_stream_event", item=item)


class FakeToolCallItem:
    type = "tool_call_item"

    def to_input_item(self):
        return {"name": "grep_files", "call_id": "inner-1", "arguments": "{}"}


@pytest.mark.asyncio
async def test_observer_registers_run_and_relays_mapped_events(service):
    token = set_current_session_id("sess-1")
    try:
        await subagents.agent_tool_stream_observer(
            {
                "event": run_item_event(FakeToolCallItem()),
                "agent": SimpleNamespace(name="explorer"),
                "tool_call": tool_call(),
            }
        )
    finally:
        reset_current_session_id(token)

    functions = [f for f, _ in service.calls]
    # run_started event, snapshot, then the mapped tool_called event.
    assert functions == [
        "ui.subagent.event",
        "ui.subagents.update",
        "ui.subagent.event",
    ]
    started = service.calls[0][1]
    assert started["kind"] == "agent_tool"
    assert started["subagent_id"] == "call-1"
    assert started["method"] == "run_started"
    mapped = service.calls[2][1]
    assert mapped["method"] == "tool_called"
    assert mapped["params"]["tool"] == "grep_files"
    run = subagents._AGENT_TOOL_RUNS["call-1"]
    assert run.agent == "explorer"
    assert run.status == "running"


@pytest.mark.asyncio
async def test_observer_is_inert_outside_a_framework_run(service):
    await subagents.agent_tool_stream_observer(
        {
            "event": run_item_event(FakeToolCallItem()),
            "agent": SimpleNamespace(name="explorer"),
            "tool_call": tool_call(),
        }
    )
    assert service.calls == []
    assert subagents._AGENT_TOOL_RUNS == {}


@pytest.mark.asyncio
async def test_parent_tool_result_settles_the_run(service):
    subagents._AGENT_TOOL_RUNS["call-1"] = subagents.SubagentRun(
        subagent_id="call-1",
        agent="explorer",
        task="how?",
        parent_session_id="sess-1",
    )
    await subagents.note_parent_tool_result(
        "call-1", {"metadata": {"success": False}, "output": "it broke"}
    )
    run = subagents._AGENT_TOOL_RUNS["call-1"]
    assert run.status == "error"
    assert run.error == "it broke"
    assert run.snapshot()["error"] == "it broke"
    assert run.finished_at is not None
    methods = [
        (f, a.get("method")) for f, a in service.calls if f == "ui.subagent.event"
    ]
    assert methods == [("ui.subagent.event", "run_end")]
    # Ordinary tools (unknown call ids) are a no-op.
    await subagents.note_parent_tool_result("not-a-subagent", {})


@pytest.mark.asyncio
async def test_snapshot_merges_providers_and_agent_tool_runs():
    subagents.register_snapshot_provider(
        lambda sid: [{"subagent_id": "w1", "kind": "worker", "status": "running"}]
        if sid == "sess-1"
        else []
    )
    subagents._AGENT_TOOL_RUNS["call-1"] = subagents.SubagentRun(
        subagent_id="call-1",
        agent="explorer",
        task="how?",
        parent_session_id="sess-1",
    )
    snapshot = subagents.subagents_snapshot("sess-1")
    kinds = {(e["subagent_id"], e["kind"]) for e in snapshot}
    assert kinds == {("w1", "worker"), ("call-1", "agent_tool")}
    assert subagents.subagents_snapshot("other") == []


@pytest.mark.asyncio
async def test_worker_relay_uses_unified_envelope(monkeypatch, service):
    from omniagents.builtin.tools.worker_tools import _relay_worker_event

    handle = SimpleNamespace(parent_session_id="sess-1", worker_id="zeph")
    await _relay_worker_event(handle, "message_output", {"content": "hi"})
    assert service.calls == [
        (
            "ui.subagent.event",
            {
                "subagent_id": "zeph",
                "kind": "worker",
                "method": "message_output",
                "params": {"content": "hi"},
            },
        )
    ]


def test_task_from_arguments_unwraps_the_json_envelope():
    from omniagents.core.runtime.subagents import _task_from_arguments

    assert (
        _task_from_arguments('{"input": "Explore /app and explain it"}')
        == "Explore /app and explain it"
    )
    # A single string field counts even without the canonical name.
    assert _task_from_arguments('{"question": "how?"}') == "how?"
    # Multiple string fields are ambiguous — show the raw JSON.
    assert (
        _task_from_arguments('{"a": "x", "b": "y"}') == '{"a": "x", "b": "y"}'
    )
    assert _task_from_arguments("not json") == "not json"
    assert _task_from_arguments("") == ""


@pytest.mark.asyncio
async def test_parent_tool_result_carries_the_answer(service):
    subagents._AGENT_TOOL_RUNS["call-2"] = subagents.SubagentRun(
        subagent_id="call-2",
        agent="explorer",
        task="how?",
        parent_session_id="sess-1",
    )
    await subagents.note_parent_tool_result(
        "call-2", {"output": "This project is a small web app."}
    )
    run = subagents._AGENT_TOOL_RUNS["call-2"]
    assert run.status == "completed"
    assert run.result == "This project is a small web app."
    assert run.snapshot()["result"] == "This project is a small web app."
