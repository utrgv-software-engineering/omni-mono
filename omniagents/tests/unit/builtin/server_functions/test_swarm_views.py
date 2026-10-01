"""Read API and live updates expose only the owning session's swarm data."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.run_context import RunContextWrapper

from omniagents.builtin.server_functions.workers import _workers_list_on_invoke
from omniagents.builtin.tools import swarm_tools as sw
from omniagents.core.runtime import subagents


@pytest.fixture
def setup(monkeypatch):
    session = SimpleNamespace(id="parent")
    service = SimpleNamespace(
        get_session=lambda sid: session if sid == "parent" else None,
        request_client_function=AsyncMock(),
    )
    monkeypatch.setattr(sw, "_SWARMS", {})
    monkeypatch.setattr(sw.workers, "worker_state_dict", lambda _: None)
    monkeypatch.setattr(subagents, "_SNAPSHOT_PROVIDERS", [])
    monkeypatch.setattr(subagents, "_AGENT_TOOL_RUNS", {})
    return service, session


@pytest.mark.asyncio
async def test_read_api_is_scoped_and_keeps_worker_snapshot(setup):
    service, session = setup
    sw._SWARMS["mine"] = sw.Swarm("mine", "parent", "My work")
    sw._SWARMS["other"] = sw.Swarm("other", "other-session", "Private work")
    result = await _workers_list_on_invoke(service, session)
    assert "snapshot" in result and "subagents" in result
    assert [s["swarm_id"] for s in result["swarms"]] == ["mine"]
    assert (await _workers_list_on_invoke(service, None))["swarms"] == []


@pytest.mark.asyncio
async def test_member_messages_publish_through_parent_service(setup, monkeypatch):
    service, session = setup
    swarm = sw.Swarm("mine", "parent", "My work", service=service)
    swarm.members["reviewer"] = sw.Member("reviewer", "Review", "worker-reviewer")
    sw._SWARMS["mine"] = swarm
    # A member executes in its own service; publication must still reach owner.
    monkeypatch.setattr(subagents, "_current_service", lambda: None)
    await sw._message(swarm, "builder", "reviewer", "Check compatibility")
    call = service.request_client_function.call_args
    assert call.args[0] == "ui.subagents.update"
    assert call.kwargs["session"] is session
    assert call.kwargs["expect_result"] is False
    assert call.args[1]["swarms"][0]["messages"][0]["message"] == "Check compatibility"


@pytest.mark.asyncio
async def test_member_addition_and_close_publish_snapshots(setup):
    service, _ = setup
    sw._SWARMS["mine"] = sw.Swarm("mine", "parent", "My work", service=service)
    ctx = RunContextWrapper(context={"session_id": "parent"})
    await sw.add_swarm_member._original_func(ctx, "mine", "reviewer", "Review")
    assert (
        service.request_client_function.call_args.args[1]["swarms"][0]["members"][0][
            "name"
        ]
        == "reviewer"
    )
    await sw.close_swarm._original_func(ctx, "mine", "Integrated")
    assert (
        service.request_client_function.call_args.args[1]["swarms"][0]["status"]
        == "closed"
    )
