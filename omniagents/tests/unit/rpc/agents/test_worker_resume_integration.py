"""Real local RPC, session persistence, and ownership; only model sampling is stubbed."""

import asyncio

import pytest
from agents import Agent

from omniagents.core.agents.specs import AgentSpec
from omniagents.rpc.agents.workers import WorkerManager


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_archived", [False, True])
async def test_worker_can_resume_with_history_and_remain_out_of_chat_lists(
    monkeypatch, legacy_archived
):
    observed = []
    from unittest.mock import AsyncMock
    from omniagents.core.agents.guardian import GuardianAssessment
    from omniagents.core.agents.service import current_service

    review = AsyncMock(
        return_value=GuardianAssessment("low", "explicit", "allow", "test")
    )
    monkeypatch.setattr("omniagents.core.agents.guardian.review_approval", review)
    from types import SimpleNamespace
    from omniagents.core.agents.specs import SafeAgentOptions

    parent = SimpleNamespace(
        spec=SimpleNamespace(
            safe_agent_options=SafeAgentOptions(skip_approvals=False),
            security_config={},
        )
    )

    async def stream(agent, prompt, session, channel, **kwargs):
        observed.append((session.id, prompt, list(session.history)))
        decision = await current_service().request_tool_approval(
            session=session, call_id="test-call", tool_name="write_file", arguments="{}"
        )
        assert decision["decision"] == "approve"
        session.append_message({"role": "assistant", "content": "saved worker result"})
        return {"usage": {}, "model": "stub"}

    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", stream)

    async def build(settings, mcp, app):
        return Agent(name="worker", model="gpt-4.1", instructions="Test worker")

    def loader(model):
        return (
            AgentSpec(
                name="worker",
                welcome_text="",
                get_agent_instructions=lambda: "",
                build_agent=build,
                project_slug="worker-resume",
                agent_slug="omni",
            ),
            {},
        )

    manager = WorkerManager(spec_loader=loader)
    try:
        async with asyncio.timeout(15):
            first = await manager.spawn(
                worker_id="reviewer",
                task_description="first task",
                parent_session_id="parent",
                parent_service=parent,
                project_slug="worker-resume",
                agent_slug="omni",
            )
            await first.done_event.wait()
            assert first.status == "completed", first.error
            service = first.agent._agent_service
            assert service._worker_approval_context.parent_service is parent
            assert service._worker_approval_context.auto_approve is False
            assert first.agent.spec.safe_agent_options.skip_approvals is False
            thread = await service.get_thread(first.session_id)
            assert thread["status"] == "active"
            assert thread["source"] == "worker"
            assert not (await service.list_threads())["threads"]
            assert not await service.list_sessions()
            assert len((await service.list_threads(source="worker"))["threads"]) == 1
            if legacy_archived:
                await service.archive_session(first.session_id)
                assert (await service.get_thread(first.session_id))[
                    "status"
                ] == "archived"
            resumed = await manager.resume("reviewer", "follow-up task")
            await resumed.done_event.wait()
            assert resumed.status == "completed", resumed.error
            assert resumed.session_id == first.session_id
            assert (
                resumed.agent._agent_service._worker_approval_context.parent_service
                is parent
            )
            assert resumed.agent.spec.safe_agent_options.skip_approvals is False
            assert len(observed) == 2
            assert review.await_count == 2
            assert any(
                m.get("content") == "saved worker result" for m in observed[1][2]
            )
            assert (await resumed.agent._agent_service.get_thread(first.session_id))[
                "status"
            ] == "active"
    finally:
        for handle in manager.workers.values():
            await handle.agent.cleanup()
            if handle.monitor_task and not handle.monitor_task.done():
                handle.monitor_task.cancel()
