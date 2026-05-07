"""Tool approval routing tests for the SDK 0.16+ event-based wire format.

Verifies that ``AgentService.request_tool_approval`` emits a
``tool_approval_requested`` notification on the right channels, that
``tool_approval_response`` resolves the awaiting future with the right
shape, and that the ``always_approve`` flag mutates the session's
always-allow set so subsequent invocations skip the prompt.
"""

import asyncio
import contextlib

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec


class FakeChannel:
    def __init__(self, name: str):
        self.name = name
        self.events: list[tuple[str, dict]] = []

    async def async_call(self, method: str, args: dict | None = None):
        self.events.append((method, dict(args or {})))


def _make_service() -> AgentService:
    spec = AgentSpec(
        name="Test",
        welcome_text="hi",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )
    return AgentService(spec, default_workspace_root="/tmp")


@pytest.mark.asyncio
async def test_request_emits_event_to_session_channels():
    service = _make_service()
    ch_other = FakeChannel("other")
    ch_target = FakeChannel("target")
    sess_target = service._session_manager.get_or_create(None)
    sess_other = service._session_manager.get_or_create(None)
    sess_target.channels.append(ch_target)
    sess_other.channels.append(ch_other)

    task = asyncio.create_task(
        service.request_tool_approval(
            session=sess_target,
            call_id="call_1",
            tool_name="delete_file",
            arguments='{"path": "/tmp/x"}',
            run_id="run_a",
        )
    )
    await asyncio.sleep(0)

    assert ch_target.events, "target session channel should receive approval event"
    method, payload = ch_target.events[0]
    assert method == "tool_approval_requested"
    assert payload["call_id"] == "call_1"
    assert payload["tool_name"] == "delete_file"
    assert payload["session_id"] == sess_target.id
    assert payload["run_id"] == "run_a"
    assert ch_other.events == [], "unrelated session must not receive prompt"

    await service.tool_approval_response(
        call_id="call_1", decision="approve", always_approve=False
    )
    response = await task
    assert response == {
        "decision": "approve",
        "always_approve": False,
        "rejection_message": None,
    }
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_response_with_always_approve_updates_session_allow_set():
    service = _make_service()
    ch = FakeChannel("c")
    sess = service._session_manager.get_or_create(None)
    sess.channels.append(ch)

    task = asyncio.create_task(
        service.request_tool_approval(
            session=sess,
            call_id="call_2",
            tool_name="run_shell",
            arguments=None,
        )
    )
    await asyncio.sleep(0)

    assert "run_shell" not in sess.always_allow_tools
    await service.tool_approval_response(
        call_id="call_2", decision="approve", always_approve=True
    )
    await task

    # Session-level allow list now carries the tool, so the per-tool
    # needs_approval predicate (configured by configure_tool_approval) will
    # short-circuit on the next invocation.
    assert "run_shell" in sess.always_allow_tools


@pytest.mark.asyncio
async def test_response_reject_propagates_message():
    service = _make_service()
    ch = FakeChannel("c")
    sess = service._session_manager.get_or_create(None)
    sess.channels.append(ch)

    task = asyncio.create_task(
        service.request_tool_approval(
            session=sess,
            call_id="call_3",
            tool_name="risky_op",
            arguments=None,
        )
    )
    await asyncio.sleep(0)

    await service.tool_approval_response(
        call_id="call_3",
        decision="reject",
        rejection_message="Too dangerous",
    )
    response = await task
    assert response["decision"] == "reject"
    assert response["rejection_message"] == "Too dangerous"


@pytest.mark.asyncio
async def test_response_broadcasts_resolved_event():
    service = _make_service()
    ch_a = FakeChannel("a")
    ch_b = FakeChannel("b")
    sess = service._session_manager.get_or_create(None)
    sess.channels.extend([ch_a, ch_b])

    task = asyncio.create_task(
        service.request_tool_approval(
            session=sess,
            call_id="call_4",
            tool_name="x",
            arguments=None,
        )
    )
    await asyncio.sleep(0)
    await service.tool_approval_response(call_id="call_4", decision="approve")
    await task

    # Both channels saw the request and the resolution.
    assert any(m == "tool_approval_requested" for m, _ in ch_a.events)
    assert any(m == "tool_approval_requested" for m, _ in ch_b.events)
    assert any(m == "tool_approval_resolved" for m, _ in ch_a.events)
    assert any(m == "tool_approval_resolved" for m, _ in ch_b.events)


@pytest.mark.asyncio
async def test_response_for_unknown_call_id_returns_false():
    service = _make_service()
    ok = await service.tool_approval_response(call_id="never_seen", decision="approve")
    assert ok is False


@pytest.mark.asyncio
async def test_request_queued_when_no_channel_attached():
    """If no client is connected at the time of request, the event must be
    queued so it can be replayed when a channel attaches later."""
    service = _make_service()
    sess = service._session_manager.get_or_create(None)
    # No channels attached.

    task = asyncio.create_task(
        service.request_tool_approval(
            session=sess,
            call_id="call_5",
            tool_name="t",
            arguments=None,
        )
    )
    await asyncio.sleep(0)

    # Pending tool approvals are surfaced via session.pending_tool_approvals
    # so a reconnecting client can replay them.
    assert "call_5" in sess.pending_tool_approvals
    payload = sess.pending_tool_approvals["call_5"]
    assert payload["tool_name"] == "t"

    # Resolve to clean up the awaiting future.
    await service.tool_approval_response(call_id="call_5", decision="reject")
    await task
    assert "call_5" not in sess.pending_tool_approvals
