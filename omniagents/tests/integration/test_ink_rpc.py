"""Exercise the shipping Ink client against the Python WebSocket/RPC stack.

Only model generation is substituted. Session storage, approvals, snapshots,
replay, capability negotiation, and transport all use production code.
"""

import os
import asyncio
import inspect
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn
from agents import Agent

from omniagents.backends.server.app import build_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents import service as service_module
from omniagents.core.tools import discovery
from omniagents.rpc.transport.websocket import ServerWebSocketTransport


@pytest.mark.parametrize(
    "scenario,reconnect",
    [
        (scenario, reconnect)
        for scenario in ["completed", "approval", "mcp", "elicitation"]
        for reconnect in ["manual", "automatic"]
    ]
    + [
        (scenario, "automatic")
        for scenario in [
            "lost_start",
            "lost_start_switch",
            "lost_queue",
            "lost_approval",
            "approval_other",
            "soak",
            "workspace",
            "screens",
        ]
    ],
)
def test_ink_reconnect(monkeypatch, tmp_path, scenario, reconnect):
    root = Path(__file__).resolve().parents[2]
    tui = root / "omniagents/backends/ink/tui"
    tsx = tui / "node_modules/tsx/dist/cli.mjs"
    node = shutil.which("node")
    if not node or not tsx.exists():
        pytest.skip("Ink integration requires Node and installed TUI dependencies")

    finished = asyncio.Event()
    sessions = []
    services = []
    fault = {"method": None}
    requests = []
    transports = set()
    receive_json = ServerWebSocketTransport.receive_json
    send_json = ServerWebSocketTransport.send_json

    async def receive(transport):
        data = await receive_json(transport)
        transports.add(transport)
        requests.append(data)
        if data.get("method") == fault["method"]:
            transport.lost_response_id = data["id"]
            fault["method"] = None
        return data

    async def send(transport, data):
        if "result" in data and data.get("id") == getattr(
            transport, "lost_response_id", None
        ):
            transport.lost_response_id = None
            async with transport._send_lock:
                await transport.websocket.close(code=1012)
            return
        await send_json(transport, data)

    monkeypatch.setattr(ServerWebSocketTransport, "receive_json", receive)
    monkeypatch.setattr(ServerWebSocketTransport, "send_json", send)

    async def build_agent(settings, mcp, app):
        return Agent(name="integration", instructions="", tools=[], model="gpt")

    async def stream(agent, prompt, session, channel, *, service, **kwargs):
        sessions.append(session)
        services.append(service)
        session.approvals_reviewer = "user"
        if prompt == "approval":
            await service.request_tool_approval(
                session=session,
                call_id="provider-call",
                tool_name="demo",
                arguments='{"path":"example.txt"}',
                run_id=session.active_run_id,
            )
        elif prompt == "mcp":
            await service.request_mcp_approval(
                session=session,
                request_id="provider-request",
                server_label="demo-server",
                tool_name="demo",
                arguments='{"path":"example.txt"}',
                run_id=session.active_run_id,
            )
        elif prompt == "elicitation":
            await service.request_elicitation(
                session=session,
                kind="question",
                message="Which path?",
                title="Choose path",
                run_id=session.active_run_id,
                timeout_ms=60_000,
            )
        else:
            await finished.wait()

    monkeypatch.setattr(service_module, "stream_agent", stream)

    def metadata(path):
        return {"display_type": "diff", "path": path, "diff": "+new text"}

    monkeypatch.setitem(
        discovery._approval_metadata_registry,
        "demo",
        (metadata, inspect.signature(metadata)),
    )
    if scenario in {"workspace", "screens"}:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "config",
                "user.email",
                "test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True
        )
        (tmp_path / "patch.txt").write_text("before\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "--", "patch.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-qm", "initial"], check=True
        )
        (tmp_path / "patch.txt").write_text("after\n")

    app = build_app(
        spec=AgentSpec(
            name="Ink integration",
            welcome_text="",
            get_agent_instructions=lambda: "",
            build_agent=build_agent,
        ),
        default_host_workspace_root=str(tmp_path),
    )

    @app.post("/test/pulse/{index}")
    async def pulse(index: int):
        session = sessions[0]
        service = services[0]
        worker = {
            "subagent_id": "worker",
            "kind": "worker",
            "status": "running",
            "task": "screen soak worker",
            "session_id": session.id,
            "parent_session_id": session.id,
            "run_id": session.active_run_id,
            "started_at": time.time(),
            "result": None,
            "error": None,
        }
        for function, args in [
            ("ui.subagents.update", {"snapshot": [worker]}),
            (
                "ui.subagent.event",
                {
                    "subagent_id": "worker",
                    "method": "message_output",
                    "params": {"content": f"WORKER_PULSE_{index}"},
                },
            ),
            ("ui.recap", {"text": f"TRANSCRIPT_PULSE_{index}"}),
        ]:
            await service.request_client_function(function, args, session=session)
        return {}

    @app.post("/test/finish")
    async def finish():
        finished.set()
        return {}

    @app.get("/test/state")
    async def state():
        return {
            "started": bool(sessions),
            "runs": len(sessions),
            "requests": requests,
            "active": any(
                s.current_task is not None and not s.current_task.done()
                for s in sessions
            ),
        }

    @app.post("/test/lose/{method}")
    async def lose(method: str):
        fault["method"] = method
        return {}

    @app.post("/test/drop")
    async def drop():
        for transport in list(transports):
            if transport._connected:
                await transport.websocket.close(code=1012)
            transports.discard(transport)
        if scenario == "completed":
            finished.set()
            for session in sessions:
                if session.current_task:
                    await session.current_task
        return {}

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and thread.is_alive():
                assert time.monotonic() < deadline, "Python RPC server did not start"
                time.sleep(0.01)
            assert server.started
            result = subprocess.run(
                [
                    node,
                    str(tsx),
                    str(tui / "src/rpc/fixtures/python-server-client.ts"),
                    f"http://127.0.0.1:{listener.getsockname()[1]}",
                    scenario,
                    reconnect,
                ],
                cwd=root,
                # The fixtures drive an emulated interactive terminal. Ink
                # only paints live frames outside CI (is-in-ci reads CI and
                # CONTINUOUS_INTEGRATION), so CI runners must not leak them.
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"CI", "CONTINUOUS_INTEGRATION"}
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Python RPC server failed to stop"
