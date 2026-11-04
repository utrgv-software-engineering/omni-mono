"""Public communication is durable, explicit, isolated, and does not wake agents."""

import uuid
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from omniagents_workplace.chat_messages import ChatMessages
from omniagents_workplace.store import Store
from omniagents_workplace.server import create_app
from test_personal import personal, signin


def test_durable_ordered_idempotent_messages(tmp_path):
    path = tmp_path / "workplace.db"
    store = Store(path)
    feed = ChatMessages(store)
    a = feed.publish(
        "a", "one", "assistant", "agent", "[worker message] literal public text"
    )
    assert (
        feed.publish(
            "a", "one", "assistant", "agent", "[worker message] literal public text"
        )
        == a
    )
    with pytest.raises(HTTPException) as exc:
        feed.publish("a", "one", "assistant", "agent", "different")
    assert exc.value.status_code == 409
    feed.publish("b", "two", "assistant", "agent", "Other chat")
    feed.publish("a", "three", "user", "alice", "Hello")
    first = feed.page("a", limit=1)
    assert first["has_more"] and first["messages"][0]["text"].startswith(
        "[worker message]"
    )
    assert feed.page("a", after=first["cursor"])["messages"][0]["text"] == "Hello"
    store.close()
    reopened = Store(path)
    assert len(ChatMessages(reopened).page("a")["messages"]) == 2
    reopened.close()


def test_publication_auth_and_no_runtime_side_effect(personal, tmp_path):
    w, client, calls, states = personal
    url = "/library/api/chat/messages"
    feed = "/workplace/api/personal/chats/factory-personal/feed"
    body = {
        "conversation": "factory-personal",
        "text": "Published answer",
        "message_id": "answer",
    }
    assert client.get(feed).status_code == 401
    assert client.post(url, json=body).status_code == 401
    signin(w, client)
    app = w.apps.by_username("initech-factory")
    token = w.apps.env(app)["CANVAS_MCP_TOKEN"]
    headers = {"Authorization": "Bearer " + token}
    before = len(calls)
    for _ in range(2):
        assert client.post(url, json=body, headers=headers).status_code == 200
    assert len(calls) == before  # no enqueue, wakeup, or runtime read
    messages = client.get(feed).json()["messages"]
    assert len(messages) == 1 and messages[0]["text"] == "Published answer"
    assert (
        client.post(url, json={**body, "role": "user"}, headers=headers).status_code
        == 422
    )
    assert (
        client.post(
            url, json={**body, "conversation": "worker-internal"}, headers=headers
        ).status_code
        == 404
    )
    assert (
        client.post(url, json={**body, "text": "changed"}, headers=headers).status_code
        == 409
    )
    second = TestClient(create_app(w, static_dir=tmp_path))
    signin(w, second)
    assert second.get(feed).json()["messages"] == messages


def test_runtime_output_is_never_public(personal):
    w, client, calls, states = personal
    signin(w, client)
    client.get("/workplace/api/personal/chats/factory-personal")
    states["factory-personal"]["snapshot"]["items"] = [
        {"kind": "agent_message", "content": {"text": "Internal final response"}},
        {
            "kind": "user_message",
            "content": {"text": "[worker message] internal handoff"},
        },
    ]
    states.push("factory-personal")
    assert client.get("/workplace/api/personal/chats/factory-personal").json()["items"]
    assert (
        client.get("/workplace/api/personal/chats/factory-personal/feed").json()[
            "messages"
        ]
        == []
    )
    body = {"text": "Actual user message", "submission_id": str(uuid.uuid4())}
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/messages", json=body
        ).status_code
        == 200
    )
    assert [
        m["text"]
        for m in client.get(
            "/workplace/api/personal/chats/factory-personal/feed"
        ).json()["messages"]
    ] == ["Actual user message"]


def test_automation_chat_is_registered_on_explicit_publication(personal, monkeypatch):
    from omniagents_workplace.factory_models import FactoryRpc

    w, client, calls, _ = personal
    signin(w, client)
    token = w.apps.env(w.apps.by_username("initech-factory"))["CANVAS_MCP_TOKEN"]

    async def runtime_http(self, method, path, **kwargs):
        assert path == "/integrations/conversations?session_id=scheduled-work"
        return {
            "conversations": [
                {
                    "id": "scheduled-work",
                    "agent": "coordinator",
                    "title": "Nightly report",
                    "created_at": 1,
                }
            ]
        }

    monkeypatch.setattr(FactoryRpc, "http", runtime_http)
    result = client.post(
        "/library/api/chat/messages",
        headers={"Authorization": "Bearer " + token},
        json={
            "conversation": "scheduled-work",
            "text": "Report ready",
            "message_id": "report",
        },
    )
    assert result.status_code == 200
    registered = dict(w.store.docs("personal.chat"))["scheduled-work"]
    assert registered["agent"] == "coordinator"
    assert not calls  # Publication does not enqueue or start an agent.
    assert (
        client.get("/workplace/api/personal/chats/scheduled-work/feed").json()[
            "messages"
        ][0]["text"]
        == "Report ready"
    )
