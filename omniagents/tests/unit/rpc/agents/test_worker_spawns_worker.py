"""A worker spawns a worker of its own through real worker services; only
model sampling is stubbed."""

import asyncio
import json

import pytest
from agents import Agent, RunContextWrapper

from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.agents.workers import WorkerManager


@pytest.mark.asyncio
async def test_a_worker_spawns_one_level_of_workers(monkeypatch):
    import omniagents.builtin.tools.worker_tools as wt
    from omniagents.core.agents.service import current_service
    from omniagents.core.workers.config import WorkerDefaults

    loads = []
    spawned = []

    async def build(settings, mcp, app):
        return Agent(name="worker", model="gpt-4.1", instructions="Test worker")

    def loader(model, **kwargs):
        loads.append(kwargs)
        return (
            AgentSpec(
                name="worker",
                welcome_text="",
                get_agent_instructions=lambda: "",
                build_agent=build,
                project_slug="nested",
                agent_slug="gym",
            ),
            {},
        )

    manager = WorkerManager(spec_loader=loader)
    monkeypatch.setattr(wt, "manager", lambda: manager)
    monkeypatch.setattr(
        wt, "worker_defaults", lambda: WorkerDefaults(spec_loader=loader)
    )
    monkeypatch.setattr(wt, "_workspace_root_from_ctx", lambda ctx: "/workspace")

    async def stream(agent, prompt, session, channel, **kwargs):
        service = current_service()
        if getattr(service, "_worker_depth", 0) == 1:
            ctx = RunContextWrapper(context={"session_id": session.id})
            out = await wt.spawn_worker._original_func(ctx, "sub task")
            spawned.append(json.loads(out.llm_output))
        session.append_message({"role": "assistant", "content": "done"})
        return {"usage": {}, "model": "stub"}

    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", stream)

    try:
        async with asyncio.timeout(20):
            first = await manager.spawn(
                worker_id="lead",
                task_description="task",
                project_slug="nested",
                agent_slug="gym",
            )
            await first.done_event.wait()
            assert first.status == "completed", first.error
            assert spawned and spawned[0]["status"] == "spawned", spawned
            child = next(h for h in manager._workers.values() if h is not first)
            await child.done_event.wait()
            assert child.status == "completed", child.error
            assert child.depth == 2
            assert child.parent_session_id == first.session_id
    finally:
        for handle in list(manager._workers.values()):
            if handle.agent is not None:
                await handle.agent.cleanup()
            if handle.monitor_task and not handle.monitor_task.done():
                handle.monitor_task.cancel()
    assert loads == [
        {"spawner": "gym", "depth": 1},
        {"spawner": "gym", "depth": 2},
    ]
