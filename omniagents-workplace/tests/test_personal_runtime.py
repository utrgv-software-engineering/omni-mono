from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from omniagents_workplace.personal_runtime import PersonalRpcPolicy


@pytest.fixture
def policy():
    return PersonalRpcPolicy(
        SimpleNamespace(
            session_id="factory-personal",
            stop=AsyncMock(),
            snapshot=AsyncMock(return_value={"attention": []}),
            decide=AsyncMock(),
        ),
        "alice",
    )


async def request(policy, method, **params):
    return await policy({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,params",
    [
        ("get_thread", {"thread_id": "other"}),
        ("resume_session", {"session_id": "other"}),
        ("set_session_approvals", {"session_id": "factory-personal"}),
        (
            "start_run",
            {
                "session_id": "factory-personal",
                "prompt": "hi",
                "variables": {"approvals": "off"},
            },
        ),
        (
            "start_run",
            {
                "session_id": "factory-personal",
                "prompt": "hi",
                "environment_selection": {"mode": "new"},
            },
        ),
        ("tool_approval_response", {"call_id": "foreign", "decision": "approve"}),
        (
            "tool_approval_response",
            {"call_id": "foreign", "decision": "approve", "always_approve": True},
        ),
        (
            "server_call",
            {"session_id": "factory-personal", "function": "workers.kill", "args": {}},
        ),
        ("initialize", {"capabilities": []}),
    ],
)
async def test_rejects_unscoped_or_policy_changing_requests(policy, method, params):
    forward, reply = await request(policy, method, **params)
    assert forward is None and "error" in reply


@pytest.mark.asyncio
async def test_native_start_and_queue_preserve_attribution(policy):
    forward, reply = await request(
        policy,
        "start_run",
        session_id="factory-personal",
        prompt="hello",
        content=[{"type": "input_text", "text": "hello"}],
        submission_id="receipt",
    )
    assert reply is None
    assert forward["params"]["prompt"] == "[alice]\nhello"
    assert forward["params"]["content"][0]["text"] == "[alice]"
    assert forward["params"]["environment_selection"] == {"mode": "inherit"}
    forward, _ = await request(
        policy,
        "enqueue_message",
        session_id="factory-personal",
        content="next",
        source="spoof",
    )
    assert forward["params"]["source"] == "workplace:personal"
    assert forward["params"]["content"] == "[alice]\nnext"


@pytest.mark.asyncio
async def test_negotiation_and_local_slash_list(policy):
    forward, _ = await request(
        policy,
        "initialize",
        capabilities={
            "artifacts": True,
            "experimental_operations": ["queue_status", "set_session_approvals"],
        },
    )
    assert forward["params"]["capabilities"]["experimental_operations"] == [
        "queue_status"
    ]
    assert forward["params"]["capabilities"]["client_functions"] is False
    assert forward["params"]["capabilities"]["artifacts"] is True
    forward, reply = await request(
        policy, "server_call", function="slash.list", args={}
    )
    assert forward is None and reply["result"] == {"commands": []}


@pytest.mark.asyncio
async def test_decisions_use_authoritative_pending_item(policy):
    policy.connection.snapshot.return_value = {
        "attention": [{"item_id": "item", "content": {"call_id": "call"}}]
    }
    forward, _ = await request(
        policy, "tool_approval_response", call_id="call", decision="approve"
    )
    assert forward is None
    policy.connection.decide.assert_awaited_once_with("item", "approve", None)


@pytest.mark.parametrize(
    "endpoint",
    [
        "/workplace/api/personal/runtime/ws",
        "/workplace/api/personal/runtime/ws/realtime",
    ],
)
@pytest.mark.parametrize(
    "browser_origin",
    ["http://127.0.0.1:7300", "http://127.0.0.1:7395", "http://localhost:7395"],
)
def test_websocket_requires_admin_origin_and_live_session(
    tmp_path, monkeypatch, browser_origin, endpoint
):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    from test_factory_runtime import _workplace
    from omniagents_workplace.accounts import SESSION_COOKIE
    from omniagents_workplace.personal import PersonalConnection
    from omniagents_workplace.server import create_app
    import omniagents_workplace.factory_runtime as runtime

    monkeypatch.setenv("FACTORY_ARCHITECTURE", "personal")
    monkeypatch.setenv(
        "WORKPLACE_ALLOWED_ORIGINS", "http://127.0.0.1:7395,http://localhost:7395"
    )
    monkeypatch.setattr(PersonalConnection, "connect", AsyncMock())

    async def relay(ws, url, token, authorized, policy):
        assert token == "t" and policy.connection.session_id == "factory-personal"
        await ws.send_json({"authorized": authorized()})
        await ws.receive_text()
        assert not authorized()
        await ws.close(code=1008)

    monkeypatch.setattr(runtime, "relay", relay)
    w = _workplace(tmp_path, "http://runtime.invalid")
    try:
        client = TestClient(create_app(w, static_dir=tmp_path))
        path = endpoint
        origin = {"origin": browser_origin}

        def refused(headers):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path, headers=headers) as ws:
                    ws.receive_json()

        refused(origin)
        member = w.add_person(username="member", name="Member", email="m@example.test")
        client.cookies.set(SESSION_COOKIE, w.accounts.start_session(member))
        refused(origin)
        client.cookies.set(
            SESSION_COOKIE, w.accounts.start_session(w.accounts.by_username("bill"))
        )
        refused({"origin": "https://evil.test"})
        with client.websocket_connect(path, headers=origin) as ws:
            assert ws.receive_json() == {"authorized": True}
            w.accounts.sign_out(client.cookies[SESSION_COOKIE])
            ws.send_text("revoked")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    finally:
        w.close()


from test_factory_runtime import upstream


@pytest.mark.parametrize("side", [False, True])
def test_native_messages_reach_runtime_but_foreign_sessions_do_not(
    tmp_path, monkeypatch, upstream, side
):
    from fastapi.testclient import TestClient
    from test_factory_runtime import _workplace
    from omniagents_workplace.accounts import SESSION_COOKIE
    from omniagents_workplace.personal import PersonalConnection
    from omniagents_workplace.server import create_app

    monkeypatch.setenv("FACTORY_ARCHITECTURE", "personal")
    monkeypatch.setattr(PersonalConnection, "connect", AsyncMock())
    url, seen = upstream
    w = _workplace(tmp_path, url)
    try:
        client = TestClient(create_app(w, static_dir=tmp_path))
        client.cookies.set(
            SESSION_COOKIE, w.accounts.start_session(w.accounts.by_username("bill"))
        )
        sid = (
            client.post("/workplace/api/personal/chats", json={"title": "Side"}).json()[
                "id"
            ]
            if side
            else "factory-personal"
        )
        path = (
            f"/workplace/api/personal/chats/{sid}/runtime/ws"
            if side
            else "/workplace/api/personal/runtime/ws"
        )
        with client.websocket_connect(path, headers={"origin": w.base_url}) as ws:
            ws.send_json(
                {"id": 1, "method": "get_thread", "params": {"thread_id": "other"}}
            )
            assert "error" in ws.receive_json()
            for method, params in [
                ("start_run", {"prompt": "hello", "submission_id": "receipt"}),
                (
                    "enqueue_message",
                    {"content": "next", "submission_id": "next-receipt"},
                ),
            ]:
                ws.send_json(
                    {"id": 2, "method": method, "params": {"session_id": sid, **params}}
                )
                assert ws.receive_json()["result"]["method"] == method
        assert seen["paths"] == ["/agents/generalist/ws"]
    finally:
        w.close()


@pytest.mark.asyncio
async def test_voice_policy_scopes_and_attributes_requests(policy):
    from omniagents_workplace.personal_runtime import PersonalVoicePolicy

    voice = PersonalVoicePolicy(policy.connection, "alice")
    for method in ["start_session", "stop_session", "delegate", "sync_context"]:
        forward, reply = await request(voice, method, session_id="other")
        assert forward is None and "error" in reply
    forward, _ = await request(
        voice,
        "delegate",
        session_id="factory-personal",
        delegation_id="d1",
        text="work",
    )
    assert forward["params"]["text"] == "[alice]\nwork"
    forward, _ = await request(
        voice,
        "sync_context",
        session_id="factory-personal",
        turns=[{"id": "1", "role": "user", "text": "hi"}],
    )
    assert forward["params"]["turns"][0]["text"] == "[alice]\nhi"
    forward, reply = await request(
        voice, "start_run", session_id="factory-personal", prompt="bypass"
    )
    assert forward is None and "error" in reply


@pytest.mark.asyncio
async def test_side_chat_policy_binds_text_and_voice_to_selected_session():
    from types import SimpleNamespace
    from omniagents_workplace.personal_runtime import (
        PersonalRpcPolicy,
        PersonalVoicePolicy,
    )

    connection = SimpleNamespace(session_id="personal-side")
    for policy, method, content in [
        (PersonalRpcPolicy(connection, "emm"), "enqueue_message", {"content": "hi"}),
        (
            PersonalVoicePolicy(connection, "emm"),
            "delegate",
            {"text": "hi", "delegation_id": "d"},
        ),
    ]:
        forward, error = await policy(
            {
                "id": 1,
                "method": method,
                "params": {"session_id": "factory-personal", **content},
            }
        )
        assert forward is None and "error" in error
        forward, error = await policy(
            {
                "id": 2,
                "method": method,
                "params": {"session_id": "personal-side", **content},
            }
        )
        assert error is None and forward["params"]["session_id"] == "personal-side"
