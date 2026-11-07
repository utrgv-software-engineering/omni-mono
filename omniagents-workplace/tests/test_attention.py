from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from omniagents_workplace.attention import (
    AttentionNotifications,
    CURSOR_KEY,
    QuestionResponsePolicy,
)
from test_factory_runtime import _workplace


def test_run_socket_ticket_scope():
    from omniagents_workplace.teams_auth import SOCKETS

    assert SOCKETS.fullmatch("/workplace/api/factory/runs/9/runtime/agents/improver/ws")
    assert not SOCKETS.fullmatch(
        "/workplace/api/factory/runs/../runtime/agents/improver/ws"
    )
    assert not SOCKETS.fullmatch(
        "/workplace/api/factory/runs/9/runtime/agents/improver/ws/realtime"
    )


@pytest.fixture
def workplace(tmp_path):
    workplace = _workplace(tmp_path, "http://runtime.invalid")
    workplace.approvals.endpoint = lambda: ("http://runtime.invalid", "control")
    workplace.notifier.url = "https://workflow.invalid/hook?sig=secret"
    workplace.notifier.deliver = AsyncMock(return_value="accepted")
    yield workplace
    workplace.close()


@pytest.mark.asyncio
async def test_poll_no_browser_duplicate_failed_retry_and_safe_content(workplace):
    feed = {
        "cursor": 1,
        "events": [{"id": 1, "run_id": 9}],
        "questions": [
            {"id": "q1", "kind": "escalate", "run_id": 8, "session_id": "child"}
        ],
    }
    calls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer control"
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=feed)

    worker = AttentionNotifications(workplace)
    workplace.store.set_meta(CURSOR_KEY, 0)
    workplace.notifier.deliver.side_effect = ["accepted", "failed", "accepted"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await worker.announce(client)
        assert workplace.store.meta(CURSOR_KEY) == 0
        await worker.announce(client)
        assert workplace.store.meta(CURSOR_KEY) == 1
        await worker.announce(client)
    assert workplace.notifier.deliver.await_count == 3
    assert calls[-1] == {"after": "1"}
    assert (
        workplace.notifier.deliver.call_args_list[0]
        .kwargs["url"]
        .endswith("/factory/runs/8?session=child")
    )


@pytest.mark.asyncio
async def test_first_enable_baseline_and_unconfigured_does_not_advance(workplace):
    worker = AttentionNotifications(workplace)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"cursor": 20, "events": [], "questions": []}
            )
        )
    ) as client:
        workplace.notifier.url = None
        await worker.announce(client)
        assert workplace.store.meta(CURSOR_KEY) is None
        workplace.notifier.url = "https://workflow.invalid"
        await worker.announce(client)
    assert workplace.store.meta(CURSOR_KEY) == 20
    workplace.notifier.deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_question_policy_never_forwards_writes_and_uses_server_actor(
    workplace, monkeypatch
):
    account = SimpleNamespace(username="admin")
    policy = QuestionResponsePolicy(workplace, account, "improver", 8)
    for method in [
        "run",
        "edit_item",
        "server_call",
        "enqueue_message",
        "client_functions",
    ]:
        forward, reply = await policy({"id": 1, "method": method, "params": {}})
        assert forward is None and "error" in reply
    request = {
        "id": 2,
        "method": "client_response",
        "params": {
            "request_id": "q1",
            "ok": True,
            "result": {"reply": "sensitive answer"},
        },
    }
    assert "error" in (await policy(request))[1]
    await policy(
        {"id": 3, "method": "resume_session", "params": {"session_id": "child"}}
    )
    post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"result": True},
            request=httpx.Request("POST", "http://runtime.invalid"),
        )
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    forward, reply = await policy(request)
    assert forward is None and reply["result"] is True
    body = post.call_args.kwargs["json"]
    assert (
        body["actor"] == "admin"
        and body["run_id"] == 8
        and body["session_id"] == "child"
    )
    assert "sensitive answer" not in str(
        workplace.audit_log.events(event="factory.question_answered")
    )
