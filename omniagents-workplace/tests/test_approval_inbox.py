"""The approval inbox: list, decide with the approver audited, announce once."""

import json

import httpx
import pytest
import yaml

from omniagents_workplace.approval_inbox import ApprovalDecision
from test_factory_runtime import _workplace

PENDING = {
    "call_id": "apr_1",
    "agent": "builder",
    "session_id": "s1",
    "tool": "send_email",
    "arguments": '{"to": "patient@example.test"}',
    "requested_at": 1.0,
    "status": "pending",
}


@pytest.fixture
def inbox(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")
    raw = w.factory_config.raw()
    raw["factory"]["admin_auth_token"] = "control"
    w.factory_config.path.write_text(yaml.safe_dump(raw))
    yield w
    w.close()


@pytest.mark.asyncio
async def test_approvers_list_and_decide_and_the_approver_is_audited(inbox):
    w = inbox
    calls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer control"
        calls.append((request.method, request.url.path, request.content))
        if request.method == "GET":
            return httpx.Response(200, json={"approvals": [PENDING]})
        body = json.loads(request.content)
        return httpx.Response(
            200, json={**PENDING, "status": "approved", "decided_by": body["approver"]}
        )

    account = w.accounts.by_username("bill")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await w.approvals.listing(client) == [PENDING]
        row = await w.approvals.decide(
            account, "apr_1", ApprovalDecision(decision="approve"), client
        )
    assert row["decided_by"] == "bill"
    assert json.loads(calls[-1][2]) == {
        "decision": "approve",
        "approver": "bill",
        "reason": "",
    }
    event = w.audit_log.events(event="approval.decided")[0]
    assert (event["actor"], event["tool"], event["decision"]) == (
        "bill",
        "send_email",
        "approve",
    )


@pytest.mark.asyncio
async def test_new_approvals_are_announced_once_without_their_arguments(
    inbox, monkeypatch
):
    w = inbox
    sent = []

    async def send(title, text, facts=(), client=None):
        sent.append((title, text, dict(facts)))
        return True

    monkeypatch.setattr(w.notifier, "send", send)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"approvals": [PENDING]})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert await w.approvals.announce(client) == 1
        assert await w.approvals.announce(client) == 0
    assert sent[0][0] == "Approval needed"
    assert "patient@example.test" not in json.dumps(sent)
