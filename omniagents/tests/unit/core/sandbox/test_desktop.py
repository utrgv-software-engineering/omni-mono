from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from omniagents.core.sandbox.desktop import SandboxDesktop
from omniagents.integrations.desktop import mount_desktop
from omniagents.rpc.auth import AuthenticationFailed
from starlette.websockets import WebSocketDisconnect


@pytest.mark.asyncio
async def test_desktop_pixel_mapping_and_no_host_fallback():
    vm = SimpleNamespace(
        is_running=True,
        query_display_size=AsyncMock(return_value=(1024, 768)),
        mouse_move=AsyncMock(),
        mouse_click=AsyncMock(),
        type_text=AsyncMock(),
    )
    session = SimpleNamespace(
        state=SimpleNamespace(options=SimpleNamespace(enable_display=True)), vm=vm
    )
    desktop = SandboxDesktop(session)
    await desktop.click(1023, 767, "right", True)
    vm.mouse_move.assert_awaited_once_with(32767, 32767)
    vm.mouse_click.assert_awaited_once_with("right", double=True)
    with pytest.raises(ValueError):
        await desktop.move(1024, 768)
    with pytest.raises(ValueError):
        await desktop.type_text("hello 世界")
    vm.type_text.assert_not_awaited()
    with pytest.raises(RuntimeError, match="no enabled VM"):
        await SandboxDesktop(None).screenshot()


@pytest.mark.asyncio
async def test_cua_uses_only_its_vm_forward_and_validates_before_input(monkeypatch):
    import sys

    client = SimpleNamespace(
        video=SimpleNamespace(width=1280, height=1024),
        mouse=SimpleNamespace(move=Mock(), click=Mock()),
        keyboard=SimpleNamespace(press=Mock()),
        drain=AsyncMock(),
        screenshot=AsyncMock(),
    )
    connections = []

    @asynccontextmanager
    async def connect(host, port):
        connections.append((host, port))
        yield client

    monkeypatch.setitem(
        sys.modules,
        "asyncvnc",
        SimpleNamespace(
            connect=connect,
            key_codes={"Ctrl": 1, "l": 2, "a": 3, "Return": 4},
        ),
    )
    session = SimpleNamespace(
        state=SimpleNamespace(
            options=SimpleNamespace(
                enable_display=False,
                desktop_vnc_port=5901,
                port_forwards={5901: 15901},
            )
        ),
        vm=SimpleNamespace(is_running=True, vnc_port=None),
    )
    desktop = SandboxDesktop(session)
    assert desktop.vnc_port == 15901
    await desktop.click(1279, 1023, "right")
    client.mouse.move.assert_called_once_with(1279, 1023)
    client.mouse.click.assert_called_once_with(2)
    client.screenshot.assert_awaited_once()
    with pytest.raises(ValueError):
        await desktop.click(1280, 1023)
    client.mouse.click.assert_called_once()
    with pytest.raises(ValueError):
        await desktop.type_text("a☃")
    client.keyboard.press.assert_not_called()
    await desktop.press_key(["CTRL", "L"])
    client.keyboard.press.assert_called_once_with("Ctrl", "l")
    assert set(connections) == {("127.0.0.1", 15901)}
    session.vm.is_running = False
    before = len(connections)
    with pytest.raises(RuntimeError, match="not running"):
        await desktop.move(0, 0)
    assert len(connections) == before


def test_desktop_requires_workspace_owner():
    owner = SimpleNamespace(user_id="owner", roles=[])

    class Auth:
        enforces_authorization = True

        async def authenticate_http(self, request):
            token = request.headers.get("authorization")
            if not token:
                raise AuthenticationFailed("Missing token")
            return (
                owner
                if token == "Bearer owner"
                else SimpleNamespace(user_id="other", roles=[])
            )

    app = FastAPI()
    app.state.connection_authenticator = Auth()
    mount_desktop(app, lambda: None, AsyncMock(return_value=owner))
    client = TestClient(app)
    for headers in ({}, {"authorization": "Bearer other"}):
        assert client.get("/integrations/desktop", headers=headers).status_code == 403
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/integrations/desktop/ws", headers=headers):
                pass
    assert client.get(
        "/integrations/desktop", headers={"authorization": "Bearer owner"}
    ).json() == {"available": False}
