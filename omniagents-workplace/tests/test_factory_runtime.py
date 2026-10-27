"""A run's sessions, read through the workplace: the proxy to the factory's agent runtimes."""

import asyncio
import json
import socket
import threading

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.config import FactoryConfig
from omniagents_workplace.factory_runtime import _refusal, allowed, same_origin
from omniagents_workplace.server import Workplace, create_app


def test_only_reading_goes_through():
    for method in (
        "initialize",
        "resume_session",
        "list_turns",
        "get_thread",
        "search_threads",
        "slash.list",
    ):
        assert allowed(method), method
    for method in (
        "start_run",
        "send_text",
        "stop_run",
        "ack_events",
        "fork_session",
        "tool_approval_response",
        "fs_delete",
        "git_pull",
        "set_session_model",
        None,
        3,
    ):
        assert not allowed(method), method
    assert allowed("server_call", {"function": "workers.list", "session_id": "s"})
    assert not allowed("server_call", {"function": "workers.kill"}) and not allowed(
        "server_call"
    )
    assert (
        _refusal({"jsonrpc": "2.0", "id": 1, "result": {}}) is None
    )  # a response passes
    assert (
        _refusal({"jsonrpc": "2.0", "method": "start_run"}) == ""
    )  # a notification is dropped
    refused = json.loads(_refusal({"jsonrpc": "2.0", "id": 7, "method": "start_run"}))
    assert refused["id"] == 7 and refused["error"]["code"] == -32601
    assert same_origin("http://workplace.test:7300", "workplace.test:7300")
    assert not same_origin(
        "http://evil.test", "workplace.test:7300"
    ) and not same_origin(None, "x")


@pytest.fixture
def upstream():
    """An agent runtime: answers each request with its method and the token it was given."""
    import websockets

    seen = {"paths": []}
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    stop = asyncio.Event()

    async def handle(ws):
        seen["paths"].append(ws.request.path)
        token = ws.request.headers.get("Authorization")
        async for text in ws:
            message = json.loads(text)
            await ws.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "result": {"method": message["method"], "auth": token},
                    }
                )
            )

    async def serve():
        async with websockets.serve(handle, "127.0.0.1", port):
            ready.set()
            await stop.wait()

    thread = threading.Thread(
        target=lambda: loop.run_until_complete(serve()), daemon=True
    )
    thread.start()
    ready.wait(5)
    yield f"http://127.0.0.1:{port}", seen
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)


def _workplace(tmp_path, factory_url):
    root = tmp_path / "factory"
    root.mkdir()
    (root / "workplace.yml").write_text(
        yaml.safe_dump(
            {
                "organization": {
                    "name": "Initech",
                    "slug": "initech",
                    "tz": "America/New_York",
                },
                "admin": {
                    "name": "Bill",
                    "username": "bill",
                    "email": "bill@initech.test",
                    "password": "tps-reports",
                },
                "factory": {
                    "url": factory_url,
                    "auth_token": "t",
                    "config_dir": str(tmp_path / "cfg"),
                },
                "app": {"name": "Initech Factory", "username": "initech-factory"},
            }
        )
    )
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    workplace.factory_config = FactoryConfig(root / "workplace.yml", None)
    workplace.factory_config.apply(workplace)
    return workplace


def test_a_signed_in_page_reads_an_agents_session(tmp_path, upstream):
    url, seen = upstream
    workplace = _workplace(tmp_path, url)
    try:
        client = TestClient(create_app(workplace, static_dir=tmp_path))
        path = "/workplace/api/factory/runtime/agents/foreman/ws"
        origin = {"origin": workplace.base_url}

        with pytest.raises(WebSocketDisconnect):  # not signed in
            with client.websocket_connect(path, headers=origin) as ws:
                ws.receive_text()
        client.cookies.set(
            SESSION_COOKIE,
            workplace.accounts.start_session(workplace.accounts.by_username("bill")),
        )
        with pytest.raises(WebSocketDisconnect):  # another site, with the cookie
            with client.websocket_connect(
                path, headers={"origin": "http://evil.test"}
            ) as ws:
                ws.receive_text()
        with pytest.raises(WebSocketDisconnect):  # not an agent's key
            with client.websocket_connect(
                "/workplace/api/factory/runtime/agents/Foo.bar/ws", headers=origin
            ) as ws:
                ws.receive_text()

        with client.websocket_connect(path, headers=origin) as ws:
            ws.send_text(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "start_run", "params": {}}
                )
            )
            refused = json.loads(ws.receive_text())
            assert refused["id"] == 1 and refused["error"]["code"] == -32601
            ws.send_text(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "resume_session",
                        "params": {"session_id": "s"},
                    }
                )
            )
            answered = json.loads(ws.receive_text())
            assert answered["id"] == 2 and answered["result"] == {
                "method": "resume_session",
                "auth": "Bearer t",
            }
            workplace.accounts.sign_out(client.cookies[SESSION_COOKIE])
            ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        assert seen["paths"] == [
            "/agents/foreman/ws"
        ]  # only the reading reached the factory, once
    finally:
        workplace.close()
