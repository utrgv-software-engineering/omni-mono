import asyncio
import shutil
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.studio.server.api import _guardrail_keys, create_api_app
from omniagents.core.runtime import bridge


@pytest.fixture
def studio_project(tmp_path):
    fixture_root = (
        Path(__file__).resolve().parent.parent / "fixtures" / "studio" / "basic_project"
    )
    destination = tmp_path / "project"
    shutil.copytree(fixture_root, destination)
    return destination


@pytest.fixture
def studio_app(studio_project):
    manifest_path = studio_project / "project.yml"
    context = load_project(str(manifest_path))
    agent_specs = {
        key: build_spec_from_project(context, key) for key in context.agents_map.keys()
    }
    base_spec = agent_specs[context.entrypoint]
    resources = {
        "project_name": context.product.slug,
        "tool_registry": context.tool_registry,
        "guardrails_registry": context.guardrails_registry,
        "agent_specs": agent_specs,
        "entry_agent_key": context.entrypoint,
        "manifest_path": str(manifest_path),
        "base_spec": base_spec,
        "default_model_settings": base_spec.model_settings or {},
        "default_safe_agent": bool(getattr(base_spec, "use_safe_agent", False)),
        "default_safe_agent_options": (
            asdict(base_spec.safe_agent_options) if base_spec.safe_agent_options else {}
        ),
        "default_guardrails": {
            "input": _guardrail_keys(
                getattr(base_spec, "input_guardrails", None),
                context.guardrails_registry.get("input", {}),
            ),
            "output": _guardrail_keys(
                getattr(base_spec, "output_guardrails", None),
                context.guardrails_registry.get("output", {}),
            ),
        },
    }
    app, _ = create_api_app(
        default_project=context.product.slug, playground_resources=resources
    )
    return app


@pytest.fixture
def studio_client(studio_app):
    client = TestClient(studio_app)
    try:
        yield client
    finally:
        client.close()


def _default_payload():
    return {
        "model": "gpt-4o",
        "instructions": "Stream integration test",
        "tools": ["greet_tool"],
        "model_settings": {"temperature": 0.2},
        "use_safe_agent": False,
        "safe_agent_options": {
            "skip_approvals": False,
            "halt_on_rejection": True,
            "safe_tool_names": ["greet_tool"],
            "safe_tool_patterns": [],
        },
        "guardrails": {
            "input": [],
            "output": [],
        },
        "output_type": "dict",
        "output_type_strict": True,
        "output_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "count": {"type": "integer"},
            },
        },
    }


def create_session(studio_client, payload=None):
    body = _default_payload()
    if payload:
        body.update(payload)
    response = studio_client.post("/api/playground/session", json=body)
    assert response.status_code == 200
    return response.json()["session_id"]


def test_playground_ws_streams_happy_path(monkeypatch, studio_client):
    session_id = create_session(studio_client)

    async def fake_stream(agent, prompt, session, rpc_channel, **kwargs):
        run_id = asyncio.current_task().get_name()
        token_delta = {
            "requests": 1,
            "input_tokens": 12,
            "output_tokens": 5,
            "total_tokens": 17,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
        token_totals = dict(token_delta)
        await rpc_channel.async_call(
            "token",
            {
                "run_id": run_id,
                "turn": 1,
                "delta": token_delta,
                "totals": token_totals,
            },
        )
        await rpc_channel.async_call(
            "message_output",
            {
                "run_id": run_id,
                "content": "chunk-one",
                "is_final": False,
            },
        )
        token_delta_two = {
            "requests": 2,
            "input_tokens": 12,
            "output_tokens": 9,
            "total_tokens": 21,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
        token_totals_two = dict(token_delta_two)
        await rpc_channel.async_call(
            "token",
            {
                "run_id": run_id,
                "turn": 2,
                "delta": token_delta_two,
                "totals": token_totals_two,
            },
        )
        await rpc_channel.async_call(
            "message_output",
            {
                "run_id": run_id,
                "content": "final-response",
                "is_final": True,
            },
        )
        session.append_message({"role": "assistant", "content": "final-response"})
        return {
            "usage": {
                "requests": 2,
                "input_tokens": 12,
                "output_tokens": 9,
                "total_tokens": 21,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
            "model": "gpt-4o-mini",
            "max_input_tokens": 8192,
            "max_output_tokens": 1024,
            "truncation": "auto",
        }

    monkeypatch.setattr(bridge, "stream_agent", fake_stream)
    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", fake_stream)

    with studio_client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}"
    ) as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "Hello"},
            }
        )
        result = None
        run_end = None
        token_turns = []
        outputs = []
        run_started = None
        while not (result and run_end):
            message = ws.receive_json()
            if message.get("id") == 1 and "result" in message:
                result = message
                continue
            method = message.get("method")
            if method == "run_started":
                run_started = message
            elif method == "token":
                token_turns.append(message["params"]["turn"])
            elif method == "message_output":
                outputs.append(message["params"])
            elif method == "run_end":
                run_end = message

    assert run_started is not None
    assert result is not None
    assert run_end is not None
    run_id = result["result"]["run_id"]
    assert run_started["params"]["run_id"] == run_id
    assert run_started["params"]["session_id"] == result["result"]["session_id"]
    assert token_turns == [1, 2]
    assert outputs[0]["is_final"] is False
    assert outputs[1]["is_final"] is True
    assert outputs[1]["content"] == "final-response"
    assert run_end["params"]["run_id"] == run_id
    usage = run_end["params"]["usage"]
    assert usage["total_tokens"] == 21
    assert run_end["params"]["model"] == "gpt-4o-mini"


def test_playground_ws_tool_approval_flow(monkeypatch, studio_client):
    session_id = create_session(studio_client)
    session_runs = defaultdict(int)
    approval_results = []
    allow_flags = []

    async def fake_stream(agent, prompt, session, rpc_channel, **kwargs):
        run_id = asyncio.current_task().get_name()
        session_runs[session.id] += 1
        index = session_runs[session.id]
        await rpc_channel.async_call(
            "message_output",
            {
                "run_id": run_id,
                "content": f"preface-{index}",
                "is_final": False,
            },
        )
        if index == 1:
            result = await session.approval_callback(
                "greet_tool",
                '{"input": "value"}',
                metadata={"label": "Greet"},
            )
            approval_results.append(result)
        else:
            allow_flags.append("greet_tool" in session.always_allow_tools)
        await rpc_channel.async_call(
            "message_output",
            {
                "run_id": run_id,
                "content": f"complete-{index}",
                "is_final": True,
            },
        )
        return {}

    monkeypatch.setattr(bridge, "stream_agent", fake_stream)
    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", fake_stream)

    with studio_client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}"
    ) as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "Needs approval"},
            }
        )
        first_result = None
        first_run_end = None
        approval_request = None
        while not (first_result and first_run_end):
            message = ws.receive_json()
            if message.get("id") == 1 and "result" in message:
                first_result = message
                continue
            method = message.get("method")
            if method == "client_request":
                approval_request = message["params"]
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "client_response",
                        "params": {
                            "request_id": approval_request["request_id"],
                            "ok": True,
                            "result": {
                                "approved": True,
                                "always_approve": True,
                                "error_message": None,
                            },
                            "error": None,
                        },
                    }
                )
            elif method == "run_end":
                first_run_end = message

        assert first_run_end is not None
        assert approval_request is not None, (first_run_end, approval_results)
        assert approval_results == [(True, True, None)]
        rpc_session_id = first_result["result"]["session_id"]

        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "start_run",
                "params": {"prompt": "Follow up", "session_id": rpc_session_id},
            }
        )
        second_result = None
        second_run_end = None
        second_requests = 0
        while not (second_result and second_run_end):
            message = ws.receive_json()
            if message.get("id") == 3 and "result" in message:
                second_result = message
                continue
            method = message.get("method")
            if method == "client_request":
                second_requests += 1
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "client_response",
                        "params": {
                            "request_id": message["params"]["request_id"],
                            "ok": True,
                            "result": {
                                "approved": True,
                                "always_approve": True,
                                "error_message": None,
                            },
                            "error": None,
                        },
                    }
                )
            if method == "run_end":
                second_run_end = message

    assert allow_flags == [True]
    assert second_requests <= 1
    assert session_runs[rpc_session_id] == 2


def test_playground_ws_surfaces_stream_errors(monkeypatch, studio_client):
    session_id = create_session(studio_client)

    async def failing_stream(agent, prompt, session, rpc_channel, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(bridge, "stream_agent", failing_stream)
    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", failing_stream)

    with studio_client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}"
    ) as ws:
        ws.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "start_run",
                "params": {"prompt": "Will fail"},
            }
        )
        result = None
        run_end = None
        while not (result and run_end):
            message = ws.receive_json()
            if message.get("id") == 1 and "result" in message:
                result = message
                continue
            if message.get("method") == "run_end":
                run_end = message

    assert result is not None
    assert run_end is not None
    assert run_end["params"]["end_reason"] == "error"
    error = run_end["params"].get("error")
    assert error["message"] == "forced failure"


def test_playground_ws_rejects_missing_session(studio_client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with studio_client.websocket_connect("/api/playground/ws?session_id=missing"):
            pass
    assert exc.value.code == 4404


def test_playground_ws_passes_per_session_workspace_root_to_stream(
    monkeypatch, studio_client
):
    """Strict-mode regression: studio playground client doesn't send
    workspace_root, but the AgentService for each playground session is
    constructed with default_workspace_root=<per-session tempdir> from
    PlaygroundSessionStore. Verify the runtime_context the agent sees
    matches that tempdir.

    Two sessions get distinct tempdirs (no cross-contamination).
    """
    import os as _os

    captured: list = []

    async def capturing_stream(agent, prompt, session, rpc_channel, **kwargs):
        captured.append(kwargs.get("context"))
        run_id = asyncio.current_task().get_name()
        await rpc_channel.async_call(
            "message_output",
            {"run_id": run_id, "content": "ok", "is_final": True},
        )
        return {
            "usage": {
                "requests": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
            "model": "gpt-4o-mini",
            "max_input_tokens": 8192,
            "max_output_tokens": 1024,
            "truncation": "auto",
        }

    monkeypatch.setattr(bridge, "stream_agent", capturing_stream)
    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", capturing_stream)

    def _drive(session_id: str) -> None:
        with studio_client.websocket_connect(
            f"/api/playground/ws?session_id={session_id}"
        ) as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "start_run",
                    "params": {"prompt": "hi"},
                }
            )
            result = None
            run_end = None
            while not (result and run_end):
                msg = ws.receive_json()
                if msg.get("id") == 1 and "result" in msg:
                    result = msg
                if msg.get("method") == "run_end":
                    run_end = msg

    s1 = create_session(studio_client)
    s2 = create_session(studio_client)

    _drive(s1)
    _drive(s2)

    # Each playground session got its own sandbox tempdir.
    assert len(captured) == 2
    ctx1, ctx2 = captured
    assert isinstance(ctx1, dict) and isinstance(ctx2, dict)
    ws1 = ctx1.get("workspace_root")
    ws2 = ctx2.get("workspace_root")
    assert isinstance(ws1, str) and ws1
    assert isinstance(ws2, str) and ws2
    assert ws1 != ws2, "two sessions must not share a sandbox tempdir"
    # The tempdirs follow the studio playground naming convention and
    # are NOT the studio process cwd (the strict-mode regression).
    assert "omniagents_studio_playground_" in ws1
    assert "omniagents_studio_playground_" in ws2
    assert ws1 != _os.getcwd()
    assert ws2 != _os.getcwd()
