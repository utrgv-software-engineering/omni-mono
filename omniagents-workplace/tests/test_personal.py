"""Personal UI ownership, durable identity, and narrowly scoped mutations."""

import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.personal import PersonalConnection, PersonalConversations
from omniagents_workplace.server import create_app
from test_factory_runtime import _workplace


@pytest.fixture
def personal(tmp_path, monkeypatch):
    workplace = _workplace(tmp_path, "http://runtime.invalid:8000")
    # The chat list creates each chat agent's Main Chat; tests address it directly.
    PersonalConversations(workplace.store).ensure_mains(
        ["generalist", "foreman"], "generalist"
    )
    calls = []
    connections = []

    class States(dict):
        def push(self, sid):
            """What the factory does after any change: push it."""
            for connection in connections:
                if connection.session_id == sid:
                    connection._changed({"method": "queue_changed", "params": {}})

    states = States()
    opened = PersonalConnection.__init__

    def tracked(self, *args, **kwargs):
        opened(self, *args, **kwargs)
        connections.append(self)

    monkeypatch.setattr(PersonalConnection, "__init__", tracked)

    async def call(self, method, params):
        calls.append((self.session_id, method, copy.deepcopy(params)))
        state = states.setdefault(
            self.session_id,
            {
                "active_run_id": "run-" + self.session_id,
                "run_active": True,
                "depth": 0,
                "snapshot": {"items": []},
            },
        )
        if method == "queue_status":
            assert params == {
                "session_id": self.session_id,
                "include_snapshot": True,
                "snapshot_limit": 200,
            }
            return copy.deepcopy(state)
        if method == "enqueue_message":
            state["depth"] += 1
            # The factory pushes the change to every connection.
            self._changed({"method": "queue_changed", "params": {}})
            return {"accepted": True}
        if method == "list_items":
            return {"items": [], "has_more": False, "next_cursor": None, "total": 0}
        return True

    from omniagents_workplace.factory_models import FactoryRpc

    async def runtime_http(self, method, path, **kwargs):
        if path.startswith("/integrations/conversations"):
            return {"conversations": []}
        return {
            "routing_version": 1,
            "agents": ["generalist", "foreman"],
            "chat_agent": "generalist",
        }

    monkeypatch.setattr(FactoryRpc, "http", runtime_http)
    monkeypatch.setattr(PersonalConnection, "call", call)
    client = TestClient(create_app(workplace, static_dir=tmp_path))
    yield workplace, client, calls, states
    workplace.close()


def signin(workplace, client, account=None):
    account = account or workplace.accounts.by_username("bill")
    client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
    return account


def test_requires_signed_in_admin_and_same_origin(personal):
    w, client, calls, _ = personal
    assert (
        client.get("/workplace/api/personal/chats/factory-personal").status_code == 401
    )
    member = w.add_person(username="member", name="Member", email="m@example.test")
    signin(w, client, member)
    assert (
        client.get("/workplace/api/personal/chats/factory-personal").status_code == 403
    )
    assert not calls
    signin(w, client)
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/messages",
            headers={"origin": "https://evil.test"},
            json={"text": "hi", "submission_id": str(uuid.uuid4())},
        ).status_code
        == 403
    )
    assert not calls


def test_shared_session_survives_new_app_for_all_admins(personal, tmp_path):
    w, client, calls, _ = personal
    first = signin(w, client)
    sid = client.get("/workplace/api/personal/chats/factory-personal").json()[
        "session_id"
    ]
    other = w.add_person(
        username="other", name="Other", email="o@example.test", admin=True
    )
    signin(w, client, other)
    other_sid = client.get("/workplace/api/personal/chats/factory-personal").json()[
        "session_id"
    ]
    assert sid == other_sid == "factory-personal"
    second_client = TestClient(create_app(w, static_dir=tmp_path))
    signin(w, second_client, first)
    assert (
        second_client.get("/workplace/api/personal/chats/factory-personal").json()[
            "session_id"
        ]
        == sid
    )
    assert all(method == "queue_status" for _, method, _ in calls)


def test_messages_cannot_choose_identity_or_weaken_policy(personal):
    w, client, calls, _ = personal
    signin(w, client)
    sid = client.get("/workplace/api/personal/chats/factory-personal").json()[
        "session_id"
    ]
    body = {"text": "Check my project", "submission_id": str(uuid.uuid4())}
    for key, value in [
        ("session_id", "victim"),
        ("role", "system"),
        ("variables", {}),
        ("safe_tool_overrides", {"all": True}),
        ("agent", "foreman"),
    ]:
        assert (
            client.post(
                "/workplace/api/personal/chats/factory-personal/messages",
                json={**body, key: value},
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/messages", json=body
        ).status_code
        == 200
    )
    assert calls[-1] == (
        sid,
        "enqueue_message",
        {
            "session_id": sid,
            # The agent is told who wrote it; the public feed keeps the text.
            "content": "[From Bill (@bill)]\n\n" + body["text"],
            "variables": {"workplace_sender": "bill", "workplace_conversation": sid},
            "submission_id": body["submission_id"],
            "role": "user",
            "source": "workplace:personal",
        },
    )
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/messages",
            json={**body, "text": " "},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/messages",
            json={**body, "text": "x" * 20001},
        ).status_code
        == 422
    )


def test_stop_only_current_owners_run(personal):
    w, client, calls, states = personal
    signin(w, client)
    sid = client.get("/workplace/api/personal/chats/factory-personal").json()[
        "session_id"
    ]
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/stop",
            json={"run_id": "foreign"},
        ).status_code
        == 409
    )
    assert not any(m == "stop_run" for _, m, _ in calls)
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/stop",
            json={"run_id": "run-" + sid},
        ).status_code
        == 200
    )
    assert calls[-1] == (sid, "stop_run", {"run_id": "run-" + sid})
    states[sid]["active_run_id"] = None
    states.push(sid)
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/stop",
            json={"run_id": "run-" + sid},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "kind,content,action,method,args",
    [
        (
            "approval",
            {"call_id": "token", "approval_kind": "tool"},
            "approve",
            "tool_approval_response",
            {"call_id": "token", "decision": "approve", "always_approve": False},
        ),
        (
            "approval",
            {"request_id": "token", "approval_kind": "mcp"},
            "reject",
            "mcp_approval_response",
            {"request_id": "token", "decision": "reject"},
        ),
        (
            "elicitation",
            {"elicitation_id": "question"},
            "accept",
            "elicitation_response",
            {
                "elicitation_id": "question",
                "action": "accept",
                "value": {"text": "answer"},
            },
        ),
    ],
)
def test_only_pending_controls_in_owners_snapshot_can_be_answered(
    personal, kind, content, action, method, args
):
    w, client, calls, states = personal
    signin(w, client)
    sid = client.get("/workplace/api/personal/chats/factory-personal").json()[
        "session_id"
    ]
    item = {
        "item_id": "own-item",
        "kind": kind,
        "status": "started",
        "content": content,
    }
    states[sid]["snapshot"]["items"] = [item]
    states.push(sid)
    body = {"item_id": "foreign-item", "action": action, "value": {"text": "answer"}}
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/decisions", json=body
        ).status_code
        == 409
    )
    assert not any(m == method for _, m, _ in calls)
    body["item_id"] = "own-item"
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/decisions",
            json={**body, "always_approve": True},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/decisions", json=body
        ).status_code
        == 200
    )
    assert calls[-1] == (sid, method, args)
    item["status"] = "completed"
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/decisions", json=body
        ).status_code
        == 409
    )


def test_disabled_account_loses_access(personal):
    w, client, calls, _ = personal
    account = signin(w, client)
    assert (
        client.get("/workplace/api/personal/chats/factory-personal").status_code == 200
    )
    account.disabled = True
    calls.clear()
    assert (
        client.get("/workplace/api/personal/chats/factory-personal").status_code == 401
    )
    assert not calls


@pytest.mark.asyncio
async def test_identity_persists_when_store_is_reopened(tmp_path, monkeypatch):
    from omniagents_workplace.store import Store
    from types import SimpleNamespace

    account = SimpleNamespace(id="a", admin=True, disabled=False)
    store = Store(tmp_path / "state.db")
    PersonalConversations(store).ensure_mains(["generalist"], "generalist")
    first = await PersonalConversations(store).for_account(
        account, "http://runtime", "secret"
    )
    store.close()
    store = Store(tmp_path / "state.db")
    second = await PersonalConversations(store).for_account(
        account, "http://runtime", "secret"
    )
    assert first.session_id == second.session_id
    store.close()


@pytest.mark.asyncio
async def test_real_transport_reconnects_without_stopping_background_run(monkeypatch):
    import httpx

    async def architecture(self, method, url, **kwargs):
        return httpx.Response(
            200,
            json={
                "routing_version": 1,
                "agents": ["generalist"],
                "chat_agent": "generalist",
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", architecture)
    import asyncio
    import json
    import websockets

    connections, methods, sockets = [], [], []
    state = {
        "active_run_id": "background-run",
        "run_active": True,
        "depth": 0,
        "snapshot": {
            "items": [
                {
                    "item_id": "saved",
                    "kind": "agent_message",
                    "status": "completed",
                    "content": {"text": "Saved progress"},
                }
            ]
        },
    }

    async def runtime(socket):
        ensured = False
        connections.append(socket.request.path)
        sockets.append(socket)
        assert socket.request.headers["Authorization"] == "Bearer server-only"
        try:
            async for raw in socket:
                message = json.loads(raw)
                methods.append(message["method"])
                if message["method"] == "initialize":
                    assert (
                        "queue_status"
                        in message["params"]["capabilities"]["experimental_operations"]
                    )
                if "id" not in message:
                    continue
                if message["method"] == "server_call":
                    assert message["params"] == {
                        "function": "session.ensure",
                        "args": {"session_id": "owner"},
                        "session_id": "owner",
                    }
                    ensured = True
                if message["method"] == "queue_status":
                    assert ensured
                result = state if message["method"] == "queue_status" else {"ok": True}
                await socket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": message["id"], "result": result}
                    )
                )
        except websockets.ConnectionClosed:
            pass

    async with websockets.serve(runtime, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = PersonalConnection(f"http://127.0.0.1:{port}", "server-only", "owner")
        try:
            assert (await client.snapshot())["run_id"] == "background-run"
            assert (await client.snapshot())["items"][0]["content"][
                "text"
            ] == "Saved progress"
            assert (
                len(connections) == 1
            )  # browser polling does not own the runtime connection
            await sockets[0].close()
            await asyncio.wait_for(client.reader, 2)
            assert (await client.snapshot())["running"]
            assert len(connections) == 2
        finally:
            await client.close()
        assert "stop_run" not in methods
        assert methods.count("server_call") == 2
        assert connections == ["/agents/generalist/ws", "/agents/generalist/ws"]


def test_bridge_commands_match_runtime_signatures():
    """Keep the facade on the checked-in runtime contract, without model calls."""
    import inspect
    from omniagents.core.agents.service import AgentService

    calls = {
        "server_call": {
            "function": "session.ensure",
            "args": {"session_id": "s"},
            "session_id": "s",
        },
        "queue_status": {"session_id": "s", "include_snapshot": True},
        "enqueue_message": {
            "session_id": "s",
            "content": "hi",
            "role": "user",
            "source": "workplace:personal",
            "submission_id": "id",
        },
        "stop_run": {"run_id": "r"},
        "tool_approval_response": {
            "call_id": "a",
            "decision": "approve",
            "always_approve": False,
        },
        "mcp_approval_response": {"request_id": "a", "decision": "reject"},
        "elicitation_response": {
            "elicitation_id": "q",
            "action": "accept",
            "value": {"text": "answer"},
        },
    }
    for name, args in calls.items():
        inspect.signature(getattr(AgentService, name)).bind(None, **args)


def test_chat_is_available_to_admins(personal, tmp_path):
    w, _, calls, _ = personal
    client = TestClient(create_app(w, static_dir=tmp_path))
    signin(w, client)
    assert "architecture" not in client.get("/workplace/api/session").json()
    assert (
        client.get("/workplace/api/personal/chats/factory-personal").status_code == 200
    )
    assert (
        client.post(
            "/workplace/api/personal/chats/factory-personal/messages",
            json={"text": "hi", "submission_id": str(uuid.uuid4())},
        ).status_code
        == 200
    )
    assert calls


@pytest.mark.asyncio
async def test_runtime_without_a_chat_agent_never_opens_socket(monkeypatch):
    import httpx
    from omniagents_workplace.factory_models import FactoryRpcError

    async def architecture(self, method, url, **kwargs):
        return httpx.Response(
            200,
            json={"session_id": None},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", architecture)
    connection = PersonalConnection(
        "http://runtime.invalid", "secret", "factory-personal"
    )
    with pytest.raises(FactoryRpcError):
        await connection.connect()
    assert connection.socket is None


def test_side_chat_lifecycle_and_shared_registry(personal, tmp_path):
    w, client, calls, states = personal
    signin(w, client)
    base = "/workplace/api/personal/chats"
    chat = client.post(base, json={"title": "Planning"}).json()
    sid = chat["id"]
    assert sid != "factory-personal"
    assert client.get(base + "/unknown").status_code == 404
    assert client.get(base + "/" + sid).json()["session_id"] == sid
    assert (
        client.post(
            base + "/" + sid + "/messages",
            json={"text": "hi", "submission_id": str(uuid.uuid4())},
        ).status_code
        == 200
    )
    assert calls[-1][0] == sid
    assert (
        client.get("/workplace/api/personal/chats/factory-personal").json()[
            "session_id"
        ]
        == "factory-personal"
    )
    other = w.add_person(
        username="sideadmin", name="Other", email="side@example.test", admin=True
    )
    second = TestClient(create_app(w, static_dir=tmp_path))
    signin(w, second, other)
    assert any(
        c["id"] == sid and c["title"] == chat["title"]
        for c in second.get(base).json()["chats"]
    )
    assert (
        client.patch(
            base + "/" + sid, json={"title": "Renamed", "archived": True}
        ).status_code
        == 200
    )
    assert next(c for c in second.get(base).json()["chats"] if c["id"] == sid)[
        "archived"
    ]
    # The original conversation is the default agent's Main Chat: pinned.
    for change in ({"title": "Existing conversation"}, {"archived": True}):
        assert client.patch(base + "/factory-personal", json=change).status_code == 409
    assert client.delete(base + "/factory-personal").status_code == 409
    assert client.delete(base + "/" + sid).status_code == 409
    states[sid].update(run_active=False, depth=0, active_run_id=None)
    states.push(sid)
    assert client.delete(base + "/" + sid).status_code == 200
    assert calls[-1] == (sid, "delete_session", {"session_id": sid})
    assert second.get(base + "/" + sid).status_code == 404


def test_chat_registry_requires_admin_and_rejects_invalid_titles(personal):
    w, client, calls, _ = personal
    base = "/workplace/api/personal/chats"
    assert client.get(base).status_code == 401
    member = w.add_person(
        username="side-member", name="Member", email="side-member@example.test"
    )
    signin(w, client, member)
    assert client.post(base, json={}).status_code == 403
    signin(w, client)
    assert client.post(base, json={"title": " "}).status_code == 422
    assert client.post(base, json={"title": "x", "id": "victim"}).status_code == 422
    assert (
        client.post(base, json={}, headers={"origin": "https://evil.test"}).status_code
        == 403
    )


def test_auto_title_and_activity_from_actual_conversation(personal):
    w, client, _, states = personal
    signin(w, client)
    base = "/workplace/api/personal/chats"
    chat = client.post(base, json={}).json()
    sid = chat["id"]
    client.get(base + "/" + sid)
    client.post(
        base + "/" + sid + "/messages",
        json={"text": "Plan the next event", "submission_id": str(uuid.uuid4())},
    )
    states[sid]["snapshot"]["items"] = [
        {
            "item_id": "msg",
            "kind": "user_message",
            "status": "completed",
            "content": {"text": "[bill]\nPlan the next event"},
        }
    ]
    states.push(sid)
    row = next(c for c in client.get(base).json()["chats"] if c["id"] == sid)
    assert row["title"] == "Plan the next event"
    assert row["running"] and not row["waiting"] and row["revision"]
    revision = row["revision"]
    states[sid]["snapshot"]["items"].append(
        {
            "item_id": "approval",
            "kind": "approval",
            "status": "started",
            "content": {"call_id": "pending"},
        }
    )
    states.push(sid)
    client.patch(base + "/" + sid, json={"title": "My title"})
    row = next(c for c in client.get(base).json()["chats"] if c["id"] == sid)
    assert row["waiting"] and row["revision"] != revision
    assert row["title"] == "My title"
    assert (
        next(
            c for c in client.get(base).json()["chats"] if c["id"] == "factory-personal"
        )["title"]
        == "Main Chat"
    )


def test_each_chat_agent_has_one_pinned_main_chat(personal):
    w, client, _, _ = personal
    signin(w, client)
    base = "/workplace/api/personal/chats"
    chats = client.get(base).json()["chats"]
    mains = {c["agent"]: c["id"] for c in chats if c["kind"] == "main"}
    # The default agent's Main Chat is the original shared conversation.
    assert mains == {"generalist": "factory-personal", "foreman": "main-foreman"}
    for chat_id in mains.values():
        assert (
            client.patch(base + "/" + chat_id, json={"archived": True}).status_code
            == 409
        )
        assert client.delete(base + "/" + chat_id).status_code == 409
    side = client.post(base, json={"agent": "foreman"}).json()
    assert side["kind"] == "side" and side["agent"] == "foreman"
    client.get(base)
    assert (
        len([c for c in client.get(base).json()["chats"] if c["kind"] == "main"]) == 2
    )


def test_chats_saved_before_kinds_are_migrated(tmp_path):
    from omniagents_workplace.store import Store

    store = Store(tmp_path / "state.db")
    store.put(
        "personal.chat",
        "factory-personal",
        {
            "id": "factory-personal",
            "title": "Event planning",
            "archived": False,
            "created_at": 0,
        },
    )
    store.put(
        "personal.chat",
        "auto",
        {
            "id": "auto",
            "title": "pr-opened",
            "agent": "code_reviewer",
            "automation": True,
            "created_at": 1,
        },
    )
    chats = PersonalConversations(store)
    chats.ensure_mains(["generalist"], "generalist")
    by_id = {c["id"]: c for c in chats.chats()}
    assert by_id["factory-personal"]["kind"] == "main"
    assert by_id["factory-personal"]["agent"] == "generalist"
    assert by_id["factory-personal"]["title"] == "Main Chat"
    assert by_id["auto"]["kind"] == "automation" and by_id["auto"]["automation"] is None
    store.close()


def test_a_deleted_original_is_not_adopted_again(tmp_path):
    from omniagents_workplace.store import Store

    store = Store(tmp_path / "state.db")
    store.put("chat.deleted", "factory-personal", {"deleted": True})
    chats = PersonalConversations(store)
    chats.ensure_mains(["generalist"], "generalist")
    assert [c["id"] for c in chats.chats()] == ["main-generalist"]
    store.close()


def test_chat_agent_cannot_change_mid_conversation(personal):
    w, client, _, _ = personal
    signin(w, client)
    base = "/workplace/api/personal/chats"
    created = client.post(base, json={"agent": "generalist"}).json()
    assert (
        client.patch(base + "/" + created["id"], json={"agent": "foreman"}).status_code
        == 422
    )


def test_automation_chats_are_titled_and_archive_once_when_their_item_closes(
    personal, monkeypatch
):
    from omniagents_workplace.factory_models import FactoryRpc

    w, client, _, _ = personal
    signin(w, client)
    conversation = {
        "id": "auto-1",
        "agent": "code_reviewer",
        "automation": "pr-opened",
        "title": "PR #12 · Add pagination",
        "created_at": 5,
        "source": "github",
        "thread_key": "acme/web#12",
        "closed": False,
    }

    async def runtime_http(self, method, path, **kwargs):
        if path.startswith("/integrations/conversations"):
            return {"conversations": [conversation]}
        return {
            "routing_version": 1,
            "agents": ["generalist", "code_reviewer"],
            "chat_agents": ["generalist"],
            "chat_agent": "generalist",
        }

    monkeypatch.setattr(FactoryRpc, "http", runtime_http)
    base = "/workplace/api/personal/chats"
    listing = client.get(base).json()
    row = next(c for c in listing["chats"] if c["id"] == "auto-1")
    assert row["kind"] == "automation" and row["title"] == "PR #12 · Add pagination"
    assert row["automation"] == "pr-opened" and not row["archived"]
    # An agent only automations use is not listed: its conversations open from
    # Automations. Side Chats people started with it would keep it listed.
    assert listing["agents"] == ["generalist"]
    conversation["closed"] = True
    row = next(c for c in client.get(base).json()["chats"] if c["id"] == "auto-1")
    assert row["archived"]
    # Restored by an admin, it stays restored.
    client.patch(base + "/auto-1", json={"archived": False})
    row = next(c for c in client.get(base).json()["chats"] if c["id"] == "auto-1")
    assert not row["archived"]


def test_unread_is_per_admin_and_working_for_names_the_sender(personal):
    w, client, _, states = personal
    signin(w, client)
    base = "/workplace/api/personal/chats"
    client.post(
        base + "/factory-personal/messages",
        json={"text": "Draft the landing page", "submission_id": str(uuid.uuid4())},
    )
    states["factory-personal"]["snapshot"]["items"] = [
        {"item_id": "m", "kind": "agent_message", "status": "completed"}
    ]
    states.push("factory-personal")
    row = next(
        c for c in client.get(base).json()["chats"] if c["id"] == "factory-personal"
    )
    assert row["unread"] and row["working_for"] == "Bill"
    assert (
        client.post(
            base + "/factory-personal/seen", json={"revision": row["revision"]}
        ).status_code
        == 200
    )
    row = next(
        c for c in client.get(base).json()["chats"] if c["id"] == "factory-personal"
    )
    assert not row["unread"]
    other = w.add_person(
        username="ann", name="Ann", email="ann@example.test", admin=True
    )
    signin(w, client, other)
    row = next(
        c for c in client.get(base).json()["chats"] if c["id"] == "factory-personal"
    )
    assert row["unread"]
    feed = client.get(base + "/factory-personal/feed").json()["messages"]
    assert feed[0]["author"] == "bill" and feed[0]["author_name"] == "Bill"
    assert feed[0]["text"] == "Draft the landing page"


def test_readiness_counts_messages_lessons_runs_and_grades_per_week(
    personal, monkeypatch
):
    import time as clock
    from omniagents_workplace.factory_models import FactoryRpc

    w, client, _, _ = personal
    signin(w, client)
    now = clock.time()
    runs = [
        {
            "agent": "generalist",
            "status": "completed",
            "started_at": now - 60,
            "grades": [
                {"judge": "task_compliance", "agent": "generalist", "passed": True}
            ],
        },
        {
            # Another agent's run that called the generalist: its grade counts.
            "agent": "foreman",
            "status": "failed",
            "started_at": now - 30,
            "grades": [
                {"judge": "task_compliance", "agent": "generalist", "passed": False},
                {"judge": "review_quality", "agent": "reviewer", "passed": True},
            ],
        },
    ]

    async def runtime_http(self, method, path, **kwargs):
        if path.startswith("/integrations/runs"):
            return {"runs": runs}
        if path.startswith("/integrations/conversations"):
            return {"conversations": []}
        return {
            "routing_version": 1,
            "agents": ["generalist"],
            "chat_agent": "generalist",
        }

    monkeypatch.setattr(FactoryRpc, "http", runtime_http)
    app = w.apps.active()[0]
    store = w.memory.provision(app.id, "agent") + "/generalist"
    w.memory._access(app.id, store, True)
    w.memory.remember(app.id, store, "Buttons use sharp corners", tags=["person:bill"])
    w.memory.remember(app.id, store, "Run make lint before a PR")
    w.memory.remember(app.id, store, "Working on PR 12", kind="note")
    client.post(
        "/workplace/api/personal/chats/factory-personal/messages",
        json={"text": "Sharper corners please", "submission_id": str(uuid.uuid4())},
    )
    data = client.get("/workplace/api/personal/agents/generalist/readiness").json()
    assert data["agent"] == "generalist" and data["factory_error"] is None
    assert len(data["weeks"]) >= 8
    this_week = data["weeks"][-1]
    assert this_week["messages"] == 1
    assert this_week["lessons"] == 2 and this_week["personal_lessons"] == 1
    assert this_week["runs"] == 1 and this_week["failed"] == 0
    rate = this_week["pass_rates"]["task_compliance"]
    assert rate["passed"] == 1 and rate["graded"] == 2
    assert (
        client.get("/workplace/api/personal/agents/Bad-Name/readiness").status_code
        == 422
    )


def test_each_chat_carries_its_latest_public_message(personal):
    w, client, _, _ = personal
    signin(w, client)
    base = "/workplace/api/personal/chats"
    client.post(
        base + "/factory-personal/messages",
        json={"text": "book the venue", "submission_id": str(uuid.uuid4())},
    )
    chats = {c["id"]: c for c in client.get(base).json()["chats"]}
    last = chats["factory-personal"]["last_message"]
    assert last["text"] == "book the venue" and last["author_name"] == "Bill"
    assert last["role"] == "user" and last["at"] > 0
    assert chats["main-foreman"]["last_message"] is None


def test_automations_view_marks_runs_whose_chat_can_be_opened(personal, monkeypatch):
    from omniagents_workplace.factory_models import FactoryRpc

    w, client, _, _ = personal
    signin(w, client)
    view = {
        "since": 0,
        "automations": [
            {
                "name": "pr-opened",
                "runs": [
                    {"id": 1, "session_id": "auto-1", "status": "completed"},
                    {"id": 2, "session_id": "gone", "status": "failed"},
                ],
                "skipped": [],
            }
        ],
    }

    async def runtime_http(self, method, path, **kwargs):
        if path.startswith("/integrations/automations"):
            assert "days=7" in path
            return view
        if path.startswith("/integrations/conversations"):
            return {
                "conversations": [
                    {"id": "auto-1", "agent": "code_reviewer", "title": "PR #1"}
                ]
            }
        return {
            "routing_version": 1,
            "agents": ["generalist"],
            "chat_agent": "generalist",
        }

    monkeypatch.setattr(FactoryRpc, "http", runtime_http)
    data = client.get("/workplace/api/factory/automations").json()
    runs = data["automations"][0]["runs"]
    assert [r["chat"] for r in runs] == [True, False]
    assert data["chat_agent"] == "generalist" and data["chat_agents"] == ["generalist"]
    member = w.add_person(username="viewer", name="Viewer", email="v@example.test")
    signin(w, client, member)
    assert client.get("/workplace/api/factory/automations").status_code == 403


def test_an_idle_chat_is_read_once_until_the_factory_pushes_a_change(personal):
    w, client, calls, states = personal
    signin(w, client)
    chat = "/workplace/api/personal/chats/factory-personal"
    client.get(chat)

    def reads(method):
        return sum(1 for _, called, _ in calls if called == method)

    client.get(chat + "/activity")
    before, pages = reads("queue_status"), reads("list_items")
    for _ in range(5):
        assert client.get(chat + "/activity").status_code == 200
        client.get(chat)
    assert reads("queue_status") == before
    assert reads("list_items") == pages
    states.push("factory-personal")
    client.get(chat + "/activity")
    client.get(chat + "/activity")
    assert reads("queue_status") == before + 1
    assert reads("list_items") == pages + 1


@pytest.mark.asyncio
async def test_the_worker_list_is_what_the_factory_last_pushed(monkeypatch):
    connection = PersonalConnection("http://runtime.invalid", None, "parent")
    listed = []

    async def call(self, method, params):
        listed.append(params["function"])
        return {"snapshot": [{"worker_id": "first"}]}

    monkeypatch.setattr(PersonalConnection, "call", call)
    assert await connection.workers() == [{"worker_id": "first"}]
    assert await connection.workers() == [{"worker_id": "first"}]
    connection._changed(
        {
            "method": "client_request",
            "params": {
                "session_id": "parent",
                "function": "ui.subagents.update",
                "args": {"snapshot": [{"worker_id": "second"}]},
            },
        }
    )
    assert await connection.workers() == [{"worker_id": "second"}]
    connection._changed({"method": "run_end", "params": {"session_id": "other"}})
    assert await connection.workers() == [{"worker_id": "second"}]
    assert listed == ["workers.list"]
