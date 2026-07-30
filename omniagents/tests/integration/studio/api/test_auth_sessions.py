from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from omniagents.studio.server import api as studio_api


def _auth_header():
    return {"Authorization": "Bearer secret"}


def test_http_routes_require_token(studio_api_env):
    response = studio_api_env.client.get("/api/projects")
    assert response.status_code == 401
    response = studio_api_env.client.get(
        "/api/projects", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401
    # Query-string tokens are no longer a credential (unified auth model);
    # OMNIAGENTS_LEGACY_QUERY_AUTH=1 is the documented migration escape.
    response = studio_api_env.client.get("/api/projects", params={"token": "secret"})
    assert response.status_code == 401
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
    # Query-string tokens are no longer a credential — even the right one.
    with pytest.raises(WebSocketDisconnect):
        with studio_api_env.client.websocket_connect(
            f"/api/playground/ws?session_id={session_id}&token=secret"
        ):
            pass
    # Authorization header authenticates.
    with studio_api_env.client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}",
        headers=_auth_header(),
    ) as websocket:
        websocket.close()
    # One-time connect ticket (browser flow) authenticates.
    ticket = studio_api_env.client.post(
        "/api/auth/ws-ticket", headers=_auth_header()
    ).json()["ticket"]
    with studio_api_env.client.websocket_connect(
        f"/api/playground/ws?session_id={session_id}&ticket={ticket}"
    ) as websocket:
        websocket.close()
