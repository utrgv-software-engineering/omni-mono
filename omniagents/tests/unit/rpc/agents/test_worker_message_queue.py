"""Real worker services must process messages serially across multiple runs."""

import asyncio
from types import SimpleNamespace

import pytest
from agents import Agent

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.conversation.recorder import recorder_for_session
from omniagents.core.session.manager import Session
from omniagents.core.workers.config import worker_defaults
from omniagents.core.workers.durable import WorkerJournal
from omniagents.rpc.agents.workers import WorkerManager


@pytest.mark.asyncio
@pytest.mark.parametrize("close_busy", [False, True])
async def test_messages_wake_idle_workers_serialize_busy_workers_and_survive_reattach(
    monkeypatch, tmp_path, close_busy
):
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "history.db"))
    parent = Session("queue-worker", "omni", "parent")
    parent.user_id = "alice"
    recorder = recorder_for_session(parent)
    recorder.ensure_thread(parent.id, user_id="alice")
    journal = WorkerJournal(recorder.store, parent.id, "alice")
    parent_service = SimpleNamespace(
        get_session=lambda sid: parent if sid == parent.id else None,
        _queue_store=lambda session: journal.store,
        _refresh_queue=lambda session: None,
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    seen = []
    active = 0

    async def stream(agent, prompt, session, channel, **kwargs):
        nonlocal active
        active += 1
        assert active == 1
        seen.append((session.id, prompt))
        try:
            if prompt == "initial":
                entered.set()
                await release.wait()
            session.append_message({"role": "assistant", "content": f"reply:{prompt}"})
            return {"usage": {}, "model": "stub"}
        finally:
            active -= 1

    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", stream)

    async def build(settings, mcp, app):
        return Agent(name="worker", model="gpt-4.1", instructions="Test")

    def loader(model):
        return (
            AgentSpec(
                name="worker",
                welcome_text="",
                get_agent_instructions=lambda: "",
                build_agent=build,
                project_slug="queue-worker",
                agent_slug="omni",
            ),
            {},
        )

    manager = WorkerManager(spec_loader=loader)
    row = journal.admit(
        "worker",
        "spawn",
        "initial",
        {
            "kind": "project",
            "version": 1,
            "definition": worker_defaults().recipe_id,
            "messaging": "explicit",
        },
        {"kind": "none", "prepared": True},
        manager.owner,
    )
    handle = await manager.spawn(
        worker_id="worker",
        task_description="initial",
        parent_session_id=parent.id,
        parent_service=parent_service,
        project_slug="queue-worker",
        agent_slug="omni",
        journal=journal,
        record=row,
    )

    async def settled(count):
        async with asyncio.timeout(15):
            while (
                len(seen) < count or not manager._lookup("worker").done_event.is_set()
            ):
                await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(entered.wait(), 10)
        await manager.send_message("worker", "second", operation_id="second")
        await manager.send_message("worker", "third", operation_id="third")
        assert len(seen) == 1
        if close_busy:
            assert await manager.close("worker") == "closed"
            with pytest.raises(ValueError, match="closed"):
                await manager.send_message("worker", "too late")
            assert len(seen) == 1
            return
        release.set()
        await settled(3)
        assert [prompt for _, prompt in seen] == ["initial", "second", "third"]
        await manager.send_message("worker", "fourth", operation_id="fourth")
        await settled(4)
        await manager.send_message("worker", "fourth", operation_id="fourth")
        await asyncio.sleep(0.05)
        assert len(seen) == 4
        assert len({sid for sid, _ in seen}) == 1
        assert journal.get("worker")["generation"] == 4

        handle.monitor_task.cancel()
        await handle.agent.cleanup()
        manager = WorkerManager(spec_loader=loader)
        manager.restore(journal, parent_service, "queue-worker", "omni")
        await manager.send_message("worker", "after restart", operation_id="restart")
        await settled(5)
        assert seen[-1] == (row["session_id"], "after restart")
        assert await manager.close("worker") == "closed"
        with pytest.raises(ValueError, match="closed"):
            await manager.send_message("worker", "too late")
        assert journal.get("worker")["closed"]
    finally:
        release.set()
        for worker in manager.workers.values():
            if worker.monitor_task:
                worker.monitor_task.cancel()
            if worker.agent:
                await worker.agent.cleanup()
