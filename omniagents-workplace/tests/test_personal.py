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
    monkeypatch.setenv("FACTORY_ARCHITECTURE", "personal")
    workplace = _workplace(tmp_path, "http://runtime.invalid:8000")
    calls, states = [], {}

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
            assert params == {"session_id": self.session_id, "include_snapshot": True}
            return copy.deepcopy(state)
        if method == "enqueue_message":
            state["depth"] += 1
            return {"accepted": True}
        return True

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
    assert client.get("/workplace/api/personal").status_code == 401
    member = w.add_person(username="member", name="Member", email="m@example.test")
    signin(w, client, member)
    assert client.get("/workplace/api/personal").status_code == 403
    assert not calls
    signin(w, client)
    assert (
        client.post(
            "/workplace/api/personal/messages",
            headers={"origin": "https://evil.test"},
            json={"text": "hi", "submission_id": str(uuid.uuid4())},
        ).status_code
        == 403
    )
    assert not calls


def test_shared_session_survives_new_app_for_all_admins(personal, tmp_path):
    w, client, calls, _ = personal
    first = signin(w, client)
    sid = client.get("/workplace/api/personal").json()["session_id"]
    other = w.add_person(
        username="other", name="Other", email="o@example.test", admin=True
    )
    signin(w, client, other)
    other_sid = client.get("/workplace/api/personal").json()["session_id"]
    assert sid == other_sid == "factory-personal"
    second_client = TestClient(create_app(w, static_dir=tmp_path))
    signin(w, second_client, first)
    assert second_client.get("/workplace/api/personal").json()["session_id"] == sid
    assert all(method == "queue_status" for _, method, _ in calls)


def test_messages_cannot_choose_identity_or_weaken_policy(personal):
    w, client, calls, _ = personal
    signin(w, client)
    sid = client.get("/workplace/api/personal").json()["session_id"]
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
                "/workplace/api/personal/messages", json={**body, key: value}
            ).status_code
            == 422
        )
    assert client.post("/workplace/api/personal/messages", json=body).status_code == 200
    assert calls[-1] == (
        sid,
        "enqueue_message",
        {
            "session_id": sid,
            "content": "[bill]\n" + body["text"],
            "submission_id": body["submission_id"],
            "role": "user",
            "source": "workplace:personal",
        },
    )
    assert (
        client.post(
            "/workplace/api/personal/messages", json={**body, "text": " "}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/workplace/api/personal/messages", json={**body, "text": "x" * 20001}
        ).status_code
        == 422
    )


def test_stop_only_current_owners_run(personal):
    w, client, calls, states = personal
    signin(w, client)
    sid = client.get("/workplace/api/personal").json()["session_id"]
    assert (
        client.post(
            "/workplace/api/personal/stop", json={"run_id": "foreign"}
        ).status_code
        == 409
    )
    assert not any(m == "stop_run" for _, m, _ in calls)
    assert (
        client.post(
            "/workplace/api/personal/stop", json={"run_id": "run-" + sid}
        ).status_code
        == 200
    )
    assert calls[-1] == (sid, "stop_run", {"run_id": "run-" + sid})
    states[sid]["active_run_id"] = None
    assert (
        client.post(
            "/workplace/api/personal/stop", json={"run_id": "run-" + sid}
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
    sid = client.get("/workplace/api/personal").json()["session_id"]
    item = {
        "item_id": "own-item",
        "kind": kind,
        "status": "started",
        "content": content,
    }
    states[sid]["snapshot"]["items"] = [item]
    body = {"item_id": "foreign-item", "action": action, "value": {"text": "answer"}}
    assert (
        client.post("/workplace/api/personal/decisions", json=body).status_code == 409
    )
    assert not any(m == method for _, m, _ in calls)
    body["item_id"] = "own-item"
    assert (
        client.post(
            "/workplace/api/personal/decisions", json={**body, "always_approve": True}
        ).status_code
        == 422
    )
    assert (
        client.post("/workplace/api/personal/decisions", json=body).status_code == 200
    )
    assert calls[-1] == (sid, method, args)
    item["status"] = "completed"
    assert (
        client.post("/workplace/api/personal/decisions", json=body).status_code == 409
    )


def test_disabled_account_loses_access(personal):
    w, client, calls, _ = personal
    account = signin(w, client)
    assert client.get("/workplace/api/personal").status_code == 200
    account.disabled = True
    calls.clear()
    assert client.get("/workplace/api/personal").status_code == 401
    assert not calls


@pytest.mark.asyncio
async def test_identity_persists_when_store_is_reopened(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_ARCHITECTURE", "personal")
    from omniagents_workplace.store import Store
    from types import SimpleNamespace

    account = SimpleNamespace(id="a", admin=True, disabled=False)
    store = Store(tmp_path / "state.db")
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

    async def architecture(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"architecture": "personal", "session_id": "owner"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", architecture)
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


def test_specialist_mode_disables_personal(personal, monkeypatch, tmp_path):
    w, _, calls, _ = personal
    monkeypatch.setenv("FACTORY_ARCHITECTURE", "specialist")
    client = TestClient(create_app(w, static_dir=tmp_path))
    signin(w, client)
    assert client.get("/workplace/api/session").json()["architecture"] == "specialist"
    assert client.get("/workplace/api/personal").status_code == 409
    assert (
        client.post(
            "/workplace/api/personal/messages",
            json={"text": "hi", "submission_id": str(uuid.uuid4())},
        ).status_code
        == 409
    )
    assert not calls


@pytest.mark.asyncio
async def test_runtime_mode_mismatch_never_opens_socket(monkeypatch):
    import httpx
    from omniagents_workplace.factory_models import FactoryRpcError

    async def architecture(self, url, **kwargs):
        return httpx.Response(
            200,
            json={"architecture": "specialist", "session_id": None},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", architecture)
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
        client.get("/workplace/api/personal").json()["session_id"] == "factory-personal"
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
    assert second.get(base).json()["chats"][1]["archived"]
    assert (
        client.patch(base + "/factory-personal", json={"title": "Oops"}).status_code
        == 409
    )
    assert client.delete(base + "/factory-personal").status_code == 409
    assert client.delete(base + "/" + sid).status_code == 409
    states[sid].update(run_active=False, depth=0, active_run_id=None)
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
    states[sid]["snapshot"]["items"] = [
        {
            "item_id": "msg",
            "kind": "user_message",
            "status": "completed",
            "content": {"text": "[bill]\nPlan the next event"},
        }
    ]
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
    client.patch(base + "/" + sid, json={"title": "My title"})
    row = next(c for c in client.get(base).json()["chats"] if c["id"] == sid)
    assert row["waiting"] and row["revision"] != revision
    assert row["title"] == "My title"
    assert client.get(base).json()["chats"][0]["title"] == "Main Chat"
