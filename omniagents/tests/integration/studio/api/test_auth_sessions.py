from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.studio.server import api as studio_api
from omniagents.studio.server.api import create_api_app
from omniagents.studio.tracing.storage import SQLiteStorage


@pytest.fixture
def studio_api_env(tmp_path: Path):
    fixtures_root = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "fixtures"
        / "projects"
        / "studio_playground"
    )
    project_dir = tmp_path / "project"
    shutil.copytree(fixtures_root, project_dir)
    storage_path = tmp_path / "storage" / "demo.db"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(str(storage_path))
    app_resources = {}
    manifest_path = project_dir / "project.yml"
    ctx = load_project(str(manifest_path))
    base_spec = build_spec_from_project(ctx)
    app_resources["manifest_path"] = str(manifest_path)
    app_resources["project_name"] = ctx.raw.get("name")
    app_resources["base_spec"] = base_spec
    app_resources["agent_specs"] = {ctx.entrypoint: base_spec}
    app_resources["entry_agent_key"] = ctx.entrypoint
    app_resources["tool_registry"] = ctx.tool_registry
    app_resources["guardrails_registry"] = ctx.guardrails_registry
    app_resources["default_model_settings"] = base_spec.model_settings or {}
    app_resources["default_safe_agent"] = bool(
        getattr(base_spec, "use_safe_agent", False)
    )
    app_resources["default_safe_agent_options"] = (
        asdict(base_spec.safe_agent_options) if base_spec.safe_agent_options else {}
    )
    app_resources["default_guardrails"] = {
        "input": [],
        "output": [],
    }
    app, _ = create_api_app(
        storage=storage,
        default_project="demo",
        playground_resources=app_resources,
        auth_token="secret",
    )
    client_ctx = TestClient(app)
    client = client_ctx.__enter__()
    try:
        yield SimpleNamespace(app=app, client=client, portal=client.portal)
    finally:
        client_ctx.__exit__(None, None, None)


def _auth_header():
    return {"Authorization": "Bearer secret"}


def test_http_routes_require_token(studio_api_env):
    response = studio_api_env.client.get("/api/projects")
    assert response.status_code == 401
    response = studio_api_env.client.get(
        "/api/projects", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401
    response = studio_api_env.client.get("/api/projects", params={"token": "secret"})
    assert response.status_code == 200
    response = studio_api_env.client.get("/api/projects", headers=_auth_header())
    assert response.status_code == 200


def test_playground_session_requires_auth(studio_api_env):
    payload = {
        "model": "gpt-4o",
        "instructions": "hello",
    }
    response = studio_api_env.client.post("/api/playground/session", json=payload)
    assert response.status_code == 401
    response = studio_api_env.client.post(
        "/api/playground/session", json=payload, headers=_auth_header()
    )
    assert response.status_code == 200
    assert "session_id" in response.json()


def test_session_updated_at_is_timezone_aware(monkeypatch, studio_api_env):
    timestamps = ["2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"]

    def _fake_now():
        return timestamps.pop(0) if timestamps else "2024-01-01T00:00:01Z"

    monkeypatch.setattr(studio_api, "_utc_now", _fake_now)
    payload = {"model": "gpt-4o"}
    response = studio_api_env.client.post(
        "/api/playground/session",
        json=payload,
        headers=_auth_header(),
    )
    session_id = response.json()["session_id"]
    store = studio_api_env.app.state.playground_session_store
    stored = asyncio.run(store.get(session_id))
    assert stored["updated_at"].endswith("Z")
    dt = datetime.fromisoformat(stored["updated_at"].replace("Z", "+00:00"))
    assert dt.tzinfo == timezone.utc
    payload["session_id"] = session_id
    studio_api_env.client.post(
        "/api/playground/session",
        json=payload,
        headers=_auth_header(),
    )
    refreshed = asyncio.run(store.get(session_id))
    assert refreshed["updated_at"] != stored["updated_at"]


def test_playground_websocket_enforces_token(studio_api_env):
    payload = {"model": "gpt-4o"}
    response = studio_api_env.client.post(
        "/api/playground/session",
        json=payload,
        headers=_auth_header(),
    )
    session_id = response.json()["session_id"]
    with pytest.raises(WebSocketDisconnect):
        with studio_api_env.client.websocket_connect(
            f"/api/playground/ws?session_id={session_id}"
        ):
            pass
    with pytest.raises(WebSocketDisconnect):
        with studio_api_env.client.websocket_connect(
            f"/api/playground/ws?session_id={session_id}&token=wrong"
        ):
            pass
    with studio_api_env.client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}&token=secret"
    ) as websocket:
        websocket.close()
