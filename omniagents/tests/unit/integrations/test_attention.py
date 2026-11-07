import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from omniagents.integrations.attention import (
    FactoryAttention,
    QuestionResponse,
    mount_attention,
)
from omniagents.integrations.events import IncomingEvent
from omniagents.integrations.store import EventStore


def dispatch(store, session="session", provider="schedule"):
    event = store.record(
        IncomingEvent(provider=provider, event="test", delivery_id=session, payload={})
    )
    return store.start_dispatch(event, "nightly-review", "improver", session)


def test_failure_cursor_is_completion_order_not_start_order(tmp_path):
    store = EventStore(tmp_path / "events.db")
    slow = dispatch(store, "slow")
    fast = dispatch(store, "fast")
    assert store.attention_events() == {"events": [], "cursor": 0}
    store.finish_dispatch(fast, "failed", "secret exception")
    first = store.attention_events(0)
    store.finish_dispatch(slow, "failed")
    store.finish_dispatch(slow, "failed")
    second = store.attention_events(first["cursor"])
    assert [row["run_id"] for row in first["events"]] == [fast]
    assert [row["run_id"] for row in second["events"]] == [slow]
    assert "secret" not in str(first)
    assert store.attention_events()["cursor"] == second["cursor"]
    store.close()
    restored = EventStore(tmp_path / "events.db")
    assert restored.attention_events(first["cursor"]) == second
    restored.close()


@pytest.mark.parametrize(
    "status,provider",
    [
        ("completed", "schedule"),
        ("stopped", "schedule"),
        ("interrupted", "schedule"),
        ("failed", "personal"),
        ("failed", "hand_off"),
    ],
)
def test_no_notifications_for_nonfailures_or_private_chats(tmp_path, status, provider):
    store = EventStore(tmp_path / "events.db")
    store.finish_dispatch(dispatch(store, provider=provider), status)
    assert store.attention_events(0)["events"] == []
    store.close()


@pytest.mark.asyncio
async def test_pending_question_scope_and_original_client_response(tmp_path):
    store = EventStore(tmp_path / "events.db")
    run_id = dispatch(store)
    store.start_dispatch(1, "nightly-review", "improver", "session")
    future = asyncio.get_running_loop().create_future()
    session = SimpleNamespace(
        id="session",
        pending_client_requests={
            "q1": {"function": "escalate", "args": {"message": "secret"}}
        },
    )
    service = SimpleNamespace(
        _pending_client_requests={"q1": future},
        _client_request_sessions={"q1": session},
        client_response=AsyncMock(return_value=True),
    )
    attention = FactoryAttention(
        store,
        SimpleNamespace(keyed_services=lambda: [("improver", service)]),
        lambda *_: [],
    )
    listing = attention.listing()
    assert len(listing["questions"]) == 1
    assert listing["questions"][0]["run_id"] == run_id
    assert "secret" not in str(listing)
    body = QuestionResponse(
        run_id=run_id,
        agent="improver",
        session_id="session",
        method="client_response",
        params={"request_id": "q1", "ok": True, "result": {"reply": "yes"}},
        actor="admin",
    )
    for update in [
        {"run_id": run_id + 1},
        {"session_id": "private"},
        {"agent": "other"},
        {"method": "run"},
    ]:
        with pytest.raises(HTTPException):
            await attention.respond(body.model_copy(update=update))
    assert await attention.respond(body) is True
    service.client_response.assert_awaited_once_with(**body.params)
    future.set_result(None)
    assert attention.listing()["questions"] == []
    with pytest.raises(HTTPException):
        await attention.respond(body)
    store.close()


@pytest.mark.asyncio
async def test_elicitation_child_session_and_control_token(tmp_path):
    store = EventStore(tmp_path / "events.db")
    run_id = dispatch(store)
    future = asyncio.get_running_loop().create_future()
    service = SimpleNamespace(
        _pending_elicitations={"q2": (future, SimpleNamespace(id="child"))},
        elicitation_response=AsyncMock(return_value={"status": "accepted"}),
    )
    attention = FactoryAttention(
        store,
        SimpleNamespace(keyed_services=lambda: [("improver", service)]),
        lambda *_: [("worker", "child", [])],
    )
    assert attention.listing()["questions"][0]["run_id"] == run_id
    body = QuestionResponse(
        run_id=run_id,
        agent="improver",
        session_id="child",
        method="elicitation_response",
        params={
            "elicitation_id": "q2",
            "action": "accept",
            "value": {"text": "answer"},
        },
        actor="admin",
    )
    assert (await attention.respond(body))["status"] == "accepted"
    app = FastAPI()
    mount_attention(
        app,
        attention,
        lambda request: request.headers.get("authorization") == "Bearer control",
    )
    client = TestClient(app)
    assert client.get("/integrations/attention").status_code == 403
    assert (
        client.post(
            "/integrations/attention/respond", json=body.model_dump()
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/integrations/attention", headers={"Authorization": "Bearer control"}
        ).status_code
        == 200
    )
    future.cancel()
    store.close()
