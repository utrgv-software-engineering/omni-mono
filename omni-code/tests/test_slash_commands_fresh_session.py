"""Every typeable slash command works as the first thing typed in a new session.

Drives the real omni-code server over its WebSocket the way every client
does: ``slash.list``, a fresh ``session.ensure``, then ``server_call`` with
no environment named. A new command is covered the moment it is declared.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from omniagents.backends.server.app import build_app
from omniagents.core.project.runtime import build_spec_from_project, load_project

import omni_code

MANIFEST = Path(omni_code.__file__).resolve().parent / "project.yml"


class _Rpc:
    def __init__(self, ws):
        self.ws, self.next_id = ws, 0

    def call(self, method, params):
        self.next_id += 1
        self.ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": self.next_id,
                    "method": method,
                    "params": params,
                }
            )
        )
        while True:
            message = json.loads(self.ws.receive_text())
            if message.get("id") == self.next_id:
                return message


@pytest.fixture
def rpc(tmp_path, monkeypatch):
    for name, sub in (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("OMNIAGENTS_HOME", "agents"),
    ):
        monkeypatch.setenv(name, str(tmp_path / sub))
    # Commands that reach a model fail fast instead of calling out.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = load_project(str(MANIFEST))
    spec = build_spec_from_project(project)
    spec.project_context = project
    spec.realtime_mode = False
    spec.voice_spec = None
    app = build_app(spec=spec, default_host_workspace_root=str(workspace))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        rpc = _Rpc(ws)
        handshake = rpc.call(
            "initialize",
            {
                "protocol_version": "2.0.0",
                "identity": {"name": "slash-test", "version": "0"},
                "platform": {"os": "linux", "arch": "x64"},
                "capabilities": {
                    "realtime": False,
                    "mcp_apps": False,
                    "client_functions": True,
                    "approvals": True,
                    "artifacts": False,
                    "replay": True,
                    "terminal": False,
                    "experimental_operations": [],
                    "disabled_notifications": [],
                },
            },
        )
        assert "error" not in handshake, handshake
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        )
        yield rpc


def test_every_slash_command_runs_in_a_fresh_session(rpc):
    catalog = rpc.call("server_call", {"function": "slash.list", "args": {}})["result"]
    commands = catalog["commands"]
    assert {"compact", "jobs", "goal", "model"} <= {row["name"] for row in commands}
    failures = {}
    for row in commands:
        ensured = rpc.call("server_call", {"function": "session.ensure", "args": {}})
        session_id = ensured["result"]["session_id"]
        reply = rpc.call(
            "server_call",
            {
                "function": row.get("function") or row["name"],
                "args": {},
                "session_id": session_id,
            },
        )
        if "error" in reply:
            failures[row["name"]] = reply["error"]["message"]
        elif not isinstance(reply["result"].get("message"), str):
            failures[row["name"]] = f"no message to show: {reply['result']}"
    assert failures == {}
