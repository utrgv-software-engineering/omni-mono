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
    client.get("/workplace/api/personal")
    states["factory-personal"]["snapshot"]["items"] = [
        {"kind": "agent_message", "content": {"text": "Internal final response"}},
        {
            "kind": "user_message",
            "content": {"text": "[worker message] internal handoff"},
        },
    ]
    assert client.get("/workplace/api/personal").json()["items"]
    assert (
        client.get("/workplace/api/personal/chats/factory-personal/feed").json()[
            "messages"
        ]
        == []
    )
    body = {"text": "Actual user message", "submission_id": str(uuid.uuid4())}
    assert client.post("/workplace/api/personal/messages", json=body).status_code == 200
    assert [
        m["text"]
        for m in client.get(
            "/workplace/api/personal/chats/factory-personal/feed"
        ).json()["messages"]
    ] == ["Actual user message"]
