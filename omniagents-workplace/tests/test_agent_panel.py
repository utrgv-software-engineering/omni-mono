"""Panel reads structured tool records and enforces conversation/store boundaries."""

from test_personal import personal, signin
from omniagents_workplace.personal import PersonalConnection


def test_activity_and_worker_ownership(personal, monkeypatch):
    w, client, _, _ = personal
    signin(w, client)
    calls = []

    async def call(self, method, params):
        calls.append((method, params))
        if method == "server_call":
            return {
                "snapshot": [
                    {
                        "worker_id": "own",
                        "parent_session_id": self.session_id,
                        "session_id": "worker-own",
                    },
                    {
                        "worker_id": "other",
                        "parent_session_id": "other-chat",
                        "session_id": "worker-other",
                    },
                    {"worker_id": "untagged", "session_id": "worker-untagged"},
                ]
            }
        if method == "list_items":
            return {
                "items": [{"kind": "tool_call", "item_id": "tool-one"}],
                "next_cursor": "older",
            }
        return {
            "snapshot": {
                "items": [
                    {"kind": "message", "content": {"text": "private final"}},
                    {"kind": "approval", "status": "started", "item_id": "pending"},
                ]
            }
        }

    monkeypatch.setattr(PersonalConnection, "call", call)
    base = "/workplace/api/personal/chats/factory-personal"
    assert [a["worker_id"] for a in client.get(base + "/agents").json()["agents"]] == [
        "own"
    ]
    response = client.get(base + "/activity?cursor=older")
    assert response.status_code == 200
    assert "private final" not in response.text
    assert response.json()["attention"][0]["item_id"] == "pending"
    assert (
        "list_items",
        {
            "thread_id": "factory-personal",
            "kinds": ["tool_call"],
            "limit": 50,
            "order": "desc",
            "cursor": "older",
        },
    ) in calls
    assert client.get(base + "/activity?worker=own").status_code == 200
    for worker in ["other", "untagged", "missing"]:
        assert client.get(base + "/activity?worker=" + worker).status_code == 404
    assert (
        client.get("/workplace/api/personal/chats/missing/activity").status_code == 404
    )


def test_agent_memory_reads_and_writes_only_its_store(personal):
    w, client, _, _ = personal
    signin(w, client)
    app = w.apps.by_username("initech-factory")
    own = f"agent:{app.id}/generalist"
    other = f"agent:{app.id}/reviewer"
    fact = w.memory.remember(app.id, own, "Generalist fact")
    secret = w.memory.remember(app.id, other, "Reviewer fact")
    base = "/workplace/api/memory/"
    assert [s["id"] for s in client.get(base + "stores?scope=agent").json()] == [own]
    result = client.post(
        base + "search?scope=agent", json={"query": "", "stores": [other]}
    )
    assert [m["id"] for m in result.json()] == [fact["id"]]
    for operation, args in [
        ("get", {"id": secret["id"]}),
        ("update", {"id": secret["id"], "text": "changed"}),
        ("forget", {"id": secret["id"], "reason": "changed"}),
        ("supersede", {"old_ids": [secret["id"]], "text": "changed"}),
        ("remember", {"store": other, "text": "changed"}),
    ]:
        assert (
            client.post(base + operation + "?scope=agent", json=args).status_code == 403
        )
    assert (
        client.post(
            base + "update?scope=agent", json={"id": fact["id"], "text": "Updated fact"}
        ).status_code
        == 200
    )
    assert (
        len(
            client.post(base + "get?scope=agent", json={"id": fact["id"]}).json()[
                "history"
            ]
        )
        == 2
    )
    member = w.add_person(username="member", name="Member", email="member@example.test")
    signin(w, client, member)
    assert client.get(base + "stores?scope=agent").status_code == 403


def test_memory_sections_pagination_and_permissions(personal):
    w, client, _, _ = personal
    signin(w, client)
    app = w.apps.by_username("initech-factory")
    own = f"agent:{app.id}/generalist"
    ids = set()
    for i in range(105):
        ids.add(w.memory.remember(app.id, own, f"Pagination lesson {i}")["id"])
    seen = set()
    for offset in range(0, 105, 10):
        response = client.get(
            "/workplace/api/memory/page", params={"store": own, "offset": offset}
        )
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 105
        assert page["offset"] == offset
        assert len(page["items"]) <= 10
        batch = {m["id"] for m in page["items"]}
        assert not (seen & batch)
        seen |= batch
    assert seen == ids
    assert (
        client.get(
            "/workplace/api/memory/page", params={"store": own, "query": "missingterm"}
        ).json()["total"]
        == 0
    )
    empty = w.memory.provision("empty", "agent")
    assert (
        client.get("/workplace/api/memory/page", params={"store": empty}).json()[
            "items"
        ]
        == []
    )
    assert (
        client.get(
            "/workplace/api/memory/page", params={"store": own, "offset": 999}
        ).json()["offset"]
        == 100
    )
    assert (
        client.get(
            "/workplace/api/memory/page", params={"store": own, "offset": -1}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/workplace/api/memory/page", params={"store": own, "limit": 51}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/workplace/api/memory/page", params={"store": empty, "scope": "agent"}
        ).status_code
        == 403
    )
    member = w.add_person(username="reader", name="Reader", email="reader@example.test")
    signin(w, client, member)
    assert (
        client.get("/workplace/api/memory/page", params={"store": own}).status_code
        == 403
    )
