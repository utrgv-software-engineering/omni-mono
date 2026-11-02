import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.server import create_app
from test_factory_runtime import _workplace


def test_desktop_proxy_requires_admin_and_origin(tmp_path):
    workplace = _workplace(tmp_path, "http://runtime.invalid:8000")
    try:
        client = TestClient(create_app(workplace, static_dir=tmp_path))
        assert client.get("/workplace/api/desktop").status_code == 401
        member = workplace.add_person(
            username="member", name="Member", email="member@example.test"
        )
        client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(member))
        assert client.get("/workplace/api/desktop").status_code == 403
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/workplace/api/desktop/ws", headers={"origin": workplace.base_url}
            ):
                pass
        admin = workplace.accounts.by_username("bill")
        client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(admin))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/workplace/api/desktop/ws", headers={"origin": "https://evil.test"}
            ):
                pass
    finally:
        workplace.close()
