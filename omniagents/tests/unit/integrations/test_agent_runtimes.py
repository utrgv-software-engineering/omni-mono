"""Each of the project's agents, served for reading its sessions at ``/agents/<key>/``."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.websockets import WebSocketDisconnect

from omniagents.integrations.agent_runtimes import AGENTS_PATH, AgentRuntimes


@pytest.fixture
def built(monkeypatch):
    """``build_app`` per agent: an app answering with where it was reached."""
    calls = []

    def build_spec_from_project(project, key):
        return SimpleNamespace(key=key)

    def build_app(*, spec, auth_token, integrations):
        calls.append((spec.key, auth_token, integrations))
        runtime = FastAPI()

        @runtime.get("/rest/{name}")
        async def rest(name: str, request: Request):
            return {
                "agent": spec.key,
                "path": request.url.path,
                "root_path": request.scope["root_path"],
            }

        @runtime.websocket("/ws")
        async def ws(websocket: WebSocket):
            await websocket.accept()
            await websocket.send_text(spec.key)
            await websocket.close()

        return runtime

    import omniagents.backends.server.app as server_app
    import omniagents.core.project.runtime as project_runtime

    monkeypatch.setattr(server_app, "build_app", build_app)
    monkeypatch.setattr(
        project_runtime, "build_spec_from_project", build_spec_from_project
    )
    return calls


def test_each_agent_is_served_once_built_and_others_are_not_found(built):
    project = SimpleNamespace(agents_map={"foreman": object(), "reviewer": object()})
    app = FastAPI()
    app.mount(AGENTS_PATH, AgentRuntimes(project, auth_token="t"))
    with TestClient(app) as client:
        got = client.get("/agents/reviewer/rest/x").json()
        assert got["agent"] == "reviewer" and got["root_path"].endswith(
            "/agents/reviewer"
        )
        client.get("/agents/reviewer/rest/y")
        with client.websocket_connect("/agents/foreman/ws") as ws:
            assert ws.receive_text() == "foreman"

        missing = client.get("/agents/nobody/rest/x")
        assert missing.status_code == 404 and "nobody" in missing.json()["error"]
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/agents/nobody/ws") as ws:
                ws.receive_text()
        assert closed.value.code == 4404
    # Built on first use, once each, behind the server's token, taking no events.
    assert built == [("reviewer", "t", False), ("foreman", "t", False)]


@pytest.mark.asyncio
async def test_pipelined_runtimes_share_factory_environment(monkeypatch):
    import omniagents.backends.server.app as server_app
    import omniagents.core.project.runtime as project_runtime

    resources = object()
    seen = []
    monkeypatch.setattr(
        project_runtime, "build_spec_from_project", lambda project, key: key
    )

    def build_app(**kwargs):
        seen.append(kwargs)
        return object()

    monkeypatch.setattr(server_app, "build_app", build_app)
    runtime = AgentRuntimes(
        SimpleNamespace(agents_map={"implementer": object(), "reviewer": object()}),
        auth_token="t",
        architecture="specialist",
        host_resources=resources,
    )
    await runtime.app("implementer")
    await runtime.app("reviewer")
    assert len(seen) == 2
    assert all(call["agent_host_resources"] is resources for call in seen)
    assert all(call["integrations"] is False for call in seen)
