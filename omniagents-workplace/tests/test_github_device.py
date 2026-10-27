import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.repository_settings import KEY
from omniagents_workplace.server import create_app
from test_repository_settings import setup, body

BASE = "/workplace/api/repositories/github/device"


@pytest.fixture
def device(setup, tmp_path, monkeypatch):
    w, client, owner = setup
    executable = tmp_path / "fake-gh"
    executable.write_text(f"""#!{sys.executable}
import os, sys, time
from pathlib import Path
assert 'GH_TOKEN' not in os.environ
assert 'GITHUB_TOKEN' not in os.environ
root=Path(os.environ['HOME'])
assert os.environ['GH_CONFIG_DIR']==str(root/'gh')
if sys.argv[1:3]==['auth','login']:
    print('! First copy your one-time code: ABCD-1234', flush=True)
    while not (root/'complete').exists(): time.sleep(.01)
    if (root/'complete').read_text()=='fail':sys.exit(1)
    (root/'credential').write_text('gho_fixture_secret')
elif sys.argv[1:3]==['auth','token']:
    print((root/'credential').read_text())
""")
    executable.chmod(0o700)
    monkeypatch.setattr(
        "omniagents_workplace.github_device.shutil.which", lambda _: str(executable)
    )
    monkeypatch.setenv("GH_TOKEN", "must_not_inherit")
    monkeypatch.setenv("GITHUB_TOKEN", "must_not_inherit")
    monkeypatch.setattr(
        w.repository_settings,
        "github",
        lambda path, **kwargs: {"login": "device-user"} if path == "/user" else [],
    )
    yield w, client, owner
    w.repository_settings.device.close()


def start(device):
    w, client, _ = device
    result = client.post(BASE, json={})
    assert result.status_code == 200
    attempt = result.json()["id"]
    for _ in range(100):
        result = client.post(f"{BASE}/{attempt}", json={})
        if result.json().get("user_code"):
            break
        time.sleep(0.01)
    assert result.json()["user_code"] == "ABCD-1234"
    assert result.json()["verification_uri"] == "https://github.com/login/device"
    return attempt


def complete(w, fail=False):
    pending = w.repository_settings.device.pending
    root = Path(pending["directory"].name)
    (root / "complete").write_text("fail" if fail else "ok")
    pending["process"].wait(timeout=5)
    return root


def test_success_is_encrypted_and_replay_protected(device):
    w, client, owner = device
    w.store.set_meta(
        KEY, body(factory_repository="initech/factory", self_improvement=True)
    )
    attempt = start(device)
    root = complete(w)
    response = client.post(f"{BASE}/{attempt}", json={})
    assert response.status_code == 200
    value = response.json()
    assert value["status"] == "connected"
    assert value["settings"]["auth_type"] == "device"
    assert value["settings"]["connected_as"] == "device-user"
    assert value["settings"]["repositories"] == []
    assert not value["settings"]["self_improvement"]
    assert value["settings"]["factory_repository"] is None
    assert "gho_fixture_secret" not in response.text
    assert "gho_fixture_secret" not in w.store.meta("repository.github.connection")
    assert w.repository_settings.token() == "gho_fixture_secret"
    assert (
        w.repository_settings.environment()["GITHUB_MCP_TOKEN"] == "gho_fixture_secret"
    )
    assert not root.exists()
    assert client.post(f"{BASE}/{attempt}", json={}).status_code == 410
    assert client.get("/workplace/api/repositories/available").json() == {
        "repositories": [],
        "page": 1,
        "has_more": False,
        "limited": False,
        "incomplete": False,
    }


def test_session_binding_admin_and_csrf(device):
    w, client, owner = device
    assert (
        client.post(BASE, json={}, headers={"origin": "https://evil.test"}).status_code
        == 403
    )
    attempt = start(device)
    other = TestClient(create_app(w))
    other.cookies.set(SESSION_COOKIE, w.accounts.start_session(owner))
    assert other.post(BASE, json={}).status_code == 409
    assert other.post(f"{BASE}/{attempt}", json={}).status_code == 403
    assert other.delete(f"{BASE}/{attempt}").status_code == 403
    member = w.add_person(
        username="device-member", name="Member", email="device@example.test"
    )
    other.cookies.set(SESSION_COOKIE, w.accounts.start_session(member))
    assert other.post(BASE, json={}).status_code == 403
    assert other.post(f"{BASE}/{attempt}", json={}).status_code == 403


@pytest.mark.parametrize(
    "action", ["cancel", "expire", "failure", "disconnect", "replace"]
)
def test_cleanup_preserves_old_connection_unless_explicitly_replaced(device, action):
    w, client, owner = device
    w.repository_settings._connection(
        {
            "access_token": "old_token",
            "login": "old",
            "account_id": owner.id,
            "auth_type": "pat",
        }
    )
    attempt = start(device)
    pending = w.repository_settings.device.pending
    root = Path(pending["directory"].name)
    assert w.repository_settings.token() == "old_token"
    if action == "cancel":
        assert client.delete(f"{BASE}/{attempt}").status_code == 200
        assert client.delete(f"{BASE}/{attempt}").status_code == 200
    elif action == "expire":
        pending["expires"] = 0
        assert client.post(f"{BASE}/{attempt}", json={}).status_code == 410
    elif action == "failure":
        complete(w, fail=True)
        assert client.post(f"{BASE}/{attempt}", json={}).status_code == 400
    elif action == "disconnect":
        assert (
            client.delete("/workplace/api/repositories/github/connection").status_code
            == 200
        )
    else:
        assert (
            client.put(
                "/workplace/api/repositories/github/token", json={"token": "new_token"}
            ).status_code
            == 200
        )
    if action not in ("disconnect", "replace"):
        assert w.repository_settings.token() == "old_token"
    assert pending["process"].poll() is not None
    assert not root.exists()
    assert w.repository_settings.device.pending is None
