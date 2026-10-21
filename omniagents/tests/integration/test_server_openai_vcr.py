import asyncio
import os
import socket
import json
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
import vcr

from omniagents.backends.server import app as server_app
from omniagents.rpc import JsonRpcClient, RpcMethodsBase

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE_PATH = CASSETTE_DIR / "server_mode_openai.yaml"
CLIENT_STATUS_CASSETTE = CASSETTE_DIR / "server_mode_client_status.yaml"
SAFE_AGENT_CASSETTE = CASSETTE_DIR / "server_mode_tool_approval.yaml"
_PROMPT_TEXT = "Please add 5 and 7 for me."


def _get_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _determine_vcr_mode(target: Path | None = None) -> str | None:
    cassette = target or CASSETTE_PATH
    if cassette.exists():
        return "playback"
    if os.getenv("OPENAI_API_KEY"):
        return "record"
    return None


class RecordingMethods(RpcMethodsBase):
    def __init__(self) -> None:
        super().__init__()
        self._queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self.events: list[tuple[str, dict]] = []

    async def _record(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))
        await self._queue.put((name, payload))

    async def run_started(self, **payload) -> None:
        await self._record("run_started", payload)

    async def message_output(self, **payload) -> None:
        await self._record("message_output", payload)

    async def run_end(self, **payload) -> None:
        await self._record("run_end", payload)

    async def token(self, **payload) -> None:
        await self._record("token", payload)

    async def client_request(self, **payload) -> None:
        await self._record("client_request", payload)

    async def tool_approval_requested(self, **payload) -> None:
        await self._record("tool_approval_requested", payload)

    async def tool_approval_resolved(self, **payload) -> None:
        await self._record("tool_approval_resolved", payload)

    def set_client(self, client: JsonRpcClient) -> None:
        self._client = client

    async def tool_called(self, **payload) -> None:
        await self._record("tool_called", payload)

    async def tool_result(self, **payload) -> None:
        await self._record("tool_result", payload)

    async def wait_for(self, name: str, timeout: float = 60.0) -> dict:
        while True:
            event, payload = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            if event == name:
                return payload


@pytest_asyncio.fixture
async def rpc_server_url(server_rpc_agent_spec):
    app = server_app.build_app(
        spec=server_rpc_agent_spec, default_host_workspace_root="/tmp"
    )
    port = _get_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        server.should_exit = True
        await task


@pytest_asyncio.fixture
async def safe_rpc_server_url(safe_server_rpc_agent_spec):
    app = server_app.build_app(
        spec=safe_server_rpc_agent_spec, default_host_workspace_root="/tmp"
    )
    port = _get_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"ws://127.0.0.1:{port}/ws"
    finally:
        server.should_exit = True
        await task


@pytest.mark.asyncio
async def test_server_mode_streaming_with_openai(
    monkeypatch, rpc_server_url, server_rpc_agent_spec
):
    mode = _determine_vcr_mode(CASSETTE_PATH)
    if mode is None:
        pytest.skip("No OpenAI cassette available and OPENAI_API_KEY not set.")

    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "playback":
        monkeypatch.setenv(
            "OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "sk-test-placeholder")
        )
        record_mode = "none"
    else:
        monkeypatch.setenv("OPENAI_API_KEY", os.environ["OPENAI_API_KEY"])
        record_mode = "once"

    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        filter_headers=[("authorization", "DUMMY")],
        ignore_localhost=True,
        decode_compressed_response=True,
    )

    methods = RecordingMethods()

    with recorder.use_cassette(CASSETTE_PATH.name):
        async with JsonRpcClient.from_url(rpc_server_url, methods=methods) as client:
            result = await client.call(
                "start_run",
                prompt=_PROMPT_TEXT,
                environment_selection={"mode": "none"},
            )
            run_id = result["run_id"]
            session_id = result["session_id"]

            run_started = await methods.wait_for("run_started")
            assert run_started["run_id"] == run_id

            tool_called = await methods.wait_for("tool_called")
            assert tool_called["run_id"] == run_id
            assert tool_called["tool"] == "add_integers_tool"
            tool_args = json.loads(tool_called["input"])
            assert tool_args == {"a": 5, "b": 7}
            tool_call_id = tool_called.get("call_id")

            tool_result = await methods.wait_for("tool_result")
            assert tool_result["run_id"] == run_id
            assert tool_result.get("tool") in {"add_integers_tool", "<tool>"}
            if tool_call_id:
                assert tool_result.get("call_id") == tool_call_id
            assert "12" in str(tool_result["output"])

            message_event = await methods.wait_for("message_output")
            assert message_event["run_id"] == run_id
            normalized_content = message_event["content"].strip().lower()
            assert normalized_content.startswith("ack")
            assert "12" in normalized_content

            run_end = await methods.wait_for("run_end")
            assert run_end["run_id"] == run_id
            assert run_end["end_reason"] in {"completed", "max_turns"}
            assert "usage" in run_end and isinstance(run_end["usage"], dict)

            token_events = [event for event, _ in methods.events if event == "token"]
            assert token_events, "Expected at least one token usage notification"

            history = await client.call("get_session_history", session_id=session_id)

    assistant_messages = []
    for entry in history:
        if entry.get("type") != "message" or entry.get("role") != "assistant":
            continue
        content = entry.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(str(item))
            content = " ".join(parts)
        assistant_messages.append(str(content))

    assert any("ack" in msg.lower() and "12" in msg for msg in assistant_messages)
    tool_names = {tool.name for tool in server_rpc_agent_spec.available_tools}
    assert {"echo_text_tool", "add_integers_tool"} <= tool_names

    function_calls = [
        entry for entry in history if entry.get("type") == "function_call"
    ]
    assert function_calls
    target_call = next(
        (entry for entry in function_calls if entry.get("name") == "add_integers_tool"),
        None,
    )
    assert target_call is not None
    call_args = target_call.get("arguments")
    if isinstance(call_args, str):
        call_args = json.loads(call_args)
    assert call_args == {"a": 5, "b": 7}

    function_outputs = [
        entry for entry in history if entry.get("type") == "function_call_output"
    ]
    assert function_outputs
    assert any("12" in str(item.get("output", "")) for item in function_outputs)


@pytest.mark.asyncio
async def test_server_mode_client_status_and_variables(
    monkeypatch, rpc_server_url, server_rpc_agent_spec
):
    mode = _determine_vcr_mode(CLIENT_STATUS_CASSETTE)
    if mode is None:
        pytest.skip("No OpenAI cassette available and OPENAI_API_KEY not set.")

    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "playback":
        monkeypatch.setenv(
            "OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "sk-test-placeholder")
        )
        record_mode = "none"
    else:
        monkeypatch.setenv("OPENAI_API_KEY", os.environ["OPENAI_API_KEY"])
        record_mode = "once"

    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        filter_headers=[("authorization", "DUMMY")],
        ignore_localhost=True,
        decode_compressed_response=True,
    )

    methods = RecordingMethods()

    with recorder.use_cassette(CLIENT_STATUS_CASSETTE.name):
        async with JsonRpcClient.from_url(rpc_server_url, methods=methods) as client:
            methods.set_client(client)
            result = await client.call(
                "start_run",
                prompt="Status check run.",
                variables={"favorite_number": 42},
                environment_selection={"mode": "none"},
            )
            run_id = result["run_id"]
            session_id = result["session_id"]

            run_started = await methods.wait_for("run_started")
            assert run_started["run_id"] == run_id

            await client.call(
                "server_call",
                function="test.trigger_status",
                args={"text": "Working", "run_id": run_id, "show_spinner": False},
                session_id=session_id,
            )

            status_event = await methods.wait_for("client_request")
            assert status_event["function"] == "ui.set_status"
            assert status_event["args"].get("text") == "Working"
            assert status_event.get("session_id") == session_id
            assert status_event.get("run_id") == run_id

            message_event = await methods.wait_for("message_output")
            assert message_event["run_id"] == run_id
            run_end = await methods.wait_for("run_end")
            assert run_end["run_id"] == run_id

            variables = await client.call(
                "server_call",
                function="session.get_variables",
                session_id=session_id,
            )
            assert variables == {"favorite_number": 42}

            sessions = await client.call("list_sessions")
            assert any(entry["id"] == session_id for entry in sessions)

            await client.call("archive_session", session_id=session_id)
            sessions_after = await client.call("list_sessions")
            archived_entry = next(
                entry for entry in sessions_after if entry["id"] == session_id
            )
            assert archived_entry.get("archived") is True

            await client.call("delete_session", session_id=session_id)
            final_sessions = await client.call("list_sessions")
            assert all(entry["id"] != session_id for entry in final_sessions)


@pytest.mark.asyncio
async def test_server_mode_tool_approval_flow(
    monkeypatch, safe_rpc_server_url, safe_server_rpc_agent_spec
):
    mode = _determine_vcr_mode(SAFE_AGENT_CASSETTE)
    if mode is None:
        pytest.skip("No OpenAI cassette available and OPENAI_API_KEY not set.")

    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)

    if mode == "playback":
        monkeypatch.setenv(
            "OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "sk-test-placeholder")
        )
        record_mode = "none"
    else:
        monkeypatch.setenv("OPENAI_API_KEY", os.environ["OPENAI_API_KEY"])
        record_mode = "once"

    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        filter_headers=[("authorization", "DUMMY")],
        ignore_localhost=True,
        decode_compressed_response=True,
    )

    methods = RecordingMethods()

    with recorder.use_cassette(SAFE_AGENT_CASSETTE.name):
        async with JsonRpcClient.from_url(
            safe_rpc_server_url, methods=methods
        ) as client:
            methods.set_client(client)
            result = await client.call(
                "start_run",
                prompt=_PROMPT_TEXT,
                environment_selection={"mode": "none"},
            )
            run_id = result["run_id"]
            session_id = result["session_id"]

            run_started = await methods.wait_for("run_started")
            assert run_started["run_id"] == run_id

            tool_called = await methods.wait_for("tool_called")
            assert tool_called["run_id"] == run_id
            assert tool_called["tool"] == "add_integers_tool"

            # Approvals ride the SDK 0.16 interruption flow: the service
            # emits tool_approval_requested and the client resolves it via
            # the tool_approval_response RPC.
            approval_request = await methods.wait_for("tool_approval_requested")
            assert approval_request["tool_name"] == "add_integers_tool"
            assert approval_request["session_id"] == session_id
            await client.call(
                "tool_approval_response",
                call_id=approval_request["call_id"],
                decision="approve",
                always_approve=False,
            )

            tool_result = await methods.wait_for("tool_result")
            assert "12" in str(tool_result["output"])

            message_event = await methods.wait_for("message_output")
            assert message_event["run_id"] == run_id
            assert "ack" in message_event["content"].lower()

            run_end = await methods.wait_for("run_end")
            assert run_end["run_id"] == run_id

            history = await client.call("get_session_history", session_id=session_id)
            assert any(entry.get("type") == "function_call" for entry in history)
