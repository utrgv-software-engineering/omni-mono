import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from agents.tool import FunctionTool

from omniagents.core.agents.approval import attach_tool_approval_predicates
from omniagents.core.agents.human_approval import (
    arguments_digest,
    consume_preapproval,
    preapprove,
    requires_person,
)
from omniagents.integrations.approvals import EXPIRY_SECONDS, PersonApprovals
from omniagents.integrations.store import EventStore


def test_named_tools_are_matched_by_name_pattern_or_mcp_identity():
    entries = ["send_email", "re:slack_.*", "ehr/*", "github/merge_pull_request"]
    assert requires_person(entries, "send_email")
    assert requires_person(entries, "slack_post_message")
    assert not requires_person(entries, "slackish")
    assert requires_person(entries, "anything", ("ehr", "update_record"))
    assert requires_person(entries, "x", ("github", "merge_pull_request"))
    assert not requires_person(entries, "x", ("github", "create_issue"))
    assert not requires_person([], "send_email")


def tool(name):
    async def invoke(ctx, args):
        return "done"

    return FunctionTool(
        name=name,
        description=name,
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=invoke,
    )


def test_nothing_else_lets_a_named_tool_through():
    session = SimpleNamespace(
        id="s1",
        human_approval_tools=["send_email"],
        always_allow_tools={"send_email", "read_file"},
    )
    send, read = attach_tool_approval_predicates(
        [tool("send_email"), tool("read_file")],
        session=session,
        safe_tool_patterns=[".*"],  # autopilot
        skip_approvals=True,
    )
    args = {"to": "a@example.test"}
    assert asyncio.run(send.needs_approval(None, args, "c1")) is True
    assert asyncio.run(read.needs_approval(None, {}, "c2")) is False
    # A person approved this exact call after its run ended: once, then asks again.
    preapprove("s1", "send_email", json.dumps(args))
    assert asyncio.run(send.needs_approval(None, args, "c3")) is False
    assert asyncio.run(send.needs_approval(None, args, "c4")) is True
    assert not consume_preapproval("s1", "send_email", {"to": "other@example.test"})
    assert arguments_digest("t", '{"b": 1, "a": 2}') == arguments_digest(
        "t", {"a": 2, "b": 1}
    )


def test_a_named_tool_is_routed_to_a_person_not_the_guardian():
    from omniagents.core.agents.service import AgentService

    session = SimpleNamespace(id="s1", human_approval_tools=["send_email"])
    resolved = asyncio.run(
        AgentService._resolve_approval_without_client(
            SimpleNamespace(),
            session=session,
            kind="tool",
            call_id="c1",
            tool_name="send_email",
            arguments="{}",
        )
    )
    assert resolved is None  # None: the ordinary prompt for a person


class FakeService:
    def __init__(self):
        self._pending_tool_approvals = {}
        self.responses = []
        self.enqueued = []

    def hold(self, call_id, session, payload):
        future = asyncio.get_event_loop().create_future()
        session.pending_tool_approvals[call_id] = payload
        self._pending_tool_approvals[call_id] = (future, session)
        return future

    async def _answer_tool_approval(
        self, call_id, decision, always, message, approver=None
    ):
        self.responses.append((call_id, decision, message, approver))
        future, _ = self._pending_tool_approvals.pop(call_id)
        future.set_result({"decision": decision})
        return True

    async def enqueue_message(self, session_id, content, role="user", source=None):
        self.enqueued.append((session_id, content, source))


@pytest.mark.asyncio
async def test_approvals_are_kept_answered_with_the_approver_and_survive_a_restart(
    tmp_path,
):
    store = EventStore(tmp_path / "events.db")
    service = FakeService()
    runtime = SimpleNamespace(state=SimpleNamespace(agent_service=service))

    async def app(agent):
        return runtime

    runtimes = SimpleNamespace(keyed_services=lambda: [("builder", service)], app=app)
    approvals = PersonApprovals(store, runtimes)
    session = SimpleNamespace(id="s1", pending_tool_approvals={})
    payload = {
        "tool_name": "send_email",
        "arguments": '{"to": "x"}',
        "requires_person": True,
    }
    service.hold("apr_1", session, payload)
    service.hold("apr_2", session, {"tool_name": "read_file", "requires_person": False})
    listed = approvals.listing()
    assert [(a["call_id"], a["agent"], a["tool"]) for a in listed] == [
        ("apr_1", "builder", "send_email")
    ]
    decided = await approvals.decide("apr_1", True, "eric")
    assert decided["status"] == "approved" and decided["decided_by"] == "eric"
    assert service.responses == [("apr_1", "approve", None, "eric")]
    with pytest.raises(LookupError):
        await approvals.decide("apr_1", True, "eric")

    # A restart: the runtime no longer holds it.
    service.hold("apr_3", session, payload)
    approvals.sync()
    service._pending_tool_approvals.clear()
    assert store.interrupt_approvals() == 1
    assert approvals.listing()[0]["status"] == "interrupted"
    await approvals.decide("apr_3", True, "eric")
    assert service.enqueued[0][0] == "s1" and "eric approved" in service.enqueued[0][1]
    assert consume_preapproval("s1", "send_email", '{"to": "x"}')

    # Left for seven days: declined.
    service.hold("apr_4", session, payload)
    approvals.sync()
    assert await approvals.expire(time.time() + EXPIRY_SECONDS + 1) == 1
    assert service.responses[-1][:2] == ("apr_4", "reject")
    assert store.approval("apr_4")["status"] == "expired"
    store.close()
