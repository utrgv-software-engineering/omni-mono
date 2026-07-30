import asyncio
from collections import defaultdict

import pytest

from omniagents.core.runtime import bridge


def _auth_header():
    return {"Authorization": "Bearer secret"}


class DummyTool:
    name = "greet_tool"
    description = "Greeting tool"
    params_json_schema = {"type": "object"}
    strict_json_schema = None


def _create_session(client, headers, payload):
    response = client.post("/api/playground/session", json=payload, headers=headers)
    assert response.status_code == 200
    return response.json()["session_id"]


def _start_run(
    ws, request_id, prompt, *, session_id=None, expect_request, response_payload
):
    params = {"prompt": prompt}
    if session_id is not None:
        params["session_id"] = session_id
    ws.send_json(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "start_run",
            "params": params,
        }
    )
    result = None
    request_payload = None
    completed = False
    while True:
        message = ws.receive_json()
        if message.get("id") == request_id and "result" in message:
            result = message["result"]
            if completed:
                break
            continue
        method = message.get("method")
        if method == "run_started":
            continue
        if method == "tool_approval_requested":
            if not expect_request:
                pytest.fail("unexpected tool approval request")
            request_payload = message["params"]
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id + 500,
                    "method": "tool_approval_response",
                    "params": {
                        "call_id": request_payload["call_id"],
                        **response_payload,
                    },
                }
            )
            continue
        if method == "run_end":
            completed = True
            if result is not None:
                break
            continue
    assert result is not None
    return request_payload, result


def test_configure_session_validation_and_success(studio_api_env):
    resources = studio_api_env.app.state.playground_resources
    resources["tool_registry"] = {}
    client = studio_api_env.client
    headers = _auth_header()
    response = client.post(
        "/api/playground/session",
        json={"model": "gpt-4o", "tools": ["missing"]},
        headers=headers,
    )
    assert response.status_code == 400
    assert "Unknown tools" in response.json()["detail"]
    response = client.post(
        "/api/playground/session",
        json={"model": "gpt-4o", "output_type": "list", "output_schema": {}},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "output_schema requires output_type 'dict'"
    resources["tool_registry"]["greet_tool"] = DummyTool()
    session_id = _create_session(
        client,
        headers,
        {
            "model": "gpt-4o",
            "tools": ["greet_tool"],
            "instructions": "hello",
            "safe_agent_options": {"safe_tool_names": ["greet_tool"]},
        },
    )
    store = studio_api_env.app.state.playground_session_store
    stored = asyncio.run(store.get(session_id))
    assert stored["model"] == "gpt-4o"
    assert stored["tools"] == ["greet_tool"]
    assert stored["safe_agent_options"]["safe_tool_names"] == ["greet_tool"]


def test_tool_approval_routing_is_session_scoped(monkeypatch, studio_api_env):
    resources = studio_api_env.app.state.playground_resources
    resources["tool_registry"]["greet_tool"] = DummyTool()
    client = studio_api_env.client
    headers = _auth_header()
    payload = {
        "model": "gpt-4o",
        "tools": ["greet_tool"],
        "safe_agent_options": {"safe_tool_names": ["greet_tool"]},
    }
    session_a = _create_session(client, headers, payload)
    session_b = _create_session(client, headers, payload)
    approvals = defaultdict(list)
    decisions = defaultdict(list)

    call_counter = iter(range(1, 100))

    async def fake_stream(agent, prompt, session, rpc_channel, **kwargs):
        # Approvals ride the SDK 0.16 interruption flow: the bridge asks the
        # service, which emits tool_approval_requested and awaits the
        # tool_approval_response RPC (always-allow lives on the session).
        service = kwargs["service"]
        allowed = getattr(session, "always_allow_tools", set())
        if "greet_tool" not in allowed:
            approvals[session.id].append("requested")
            decisions[session.id].append(
                await service.request_tool_approval(
                    session=session,
                    call_id=f"call-{next(call_counter)}",
                    tool_name="greet_tool",
                    arguments='{"input": "value"}',
                )
            )
        else:
            approvals[session.id].append("auto")
        return {}

    monkeypatch.setattr(bridge, "stream_agent", fake_stream)
    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", fake_stream)

    with client.websocket_connect(
        f"/api/playground/ws?session_id={session_a}",
        headers=_auth_header(),
    ) as ws_a:
        request_a, result_a = _start_run(
            ws_a,
            1,
            "first run",
            expect_request=True,
            response_payload={
                "decision": "approve",
                "always_approve": True,
            },
        )
        rpc_session_a = result_a["session_id"]
        assert request_a["session_id"] == rpc_session_a
        assert decisions[rpc_session_a] == [
            {
                "decision": "approve",
                "always_approve": True,
                "rejection_message": None,
            }
        ]
        second_request, second_result = _start_run(
            ws_a,
            3,
            "second run",
            session_id=rpc_session_a,
            expect_request=False,
            response_payload=None,
        )
        assert second_result["session_id"] == rpc_session_a
        assert second_request is None
    assert approvals[rpc_session_a] == ["requested", "auto"]

    with client.websocket_connect(
        f"/api/playground/ws?session_id={session_b}",
        headers=_auth_header(),
    ) as ws_b:
        request_b, result_b = _start_run(
            ws_b,
            5,
            "third run",
            expect_request=True,
            response_payload={
                "decision": "approve",
                "always_approve": False,
            },
        )
        rpc_session_b = result_b["session_id"]
        assert request_b["session_id"] == rpc_session_b
        assert rpc_session_b != rpc_session_a
        assert decisions[rpc_session_b] == [
            {
                "decision": "approve",
                "always_approve": False,
                "rejection_message": None,
            }
        ]

    assert approvals[rpc_session_b] == ["requested"]
    assert decisions[rpc_session_a] == [
        {
            "decision": "approve",
            "always_approve": True,
            "rejection_message": None,
        }
    ]
    assert decisions[rpc_session_b] == [
        {
            "decision": "approve",
            "always_approve": False,
            "rejection_message": None,
        }
    ]
