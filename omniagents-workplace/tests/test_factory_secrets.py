from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.backup import backup, restore
from omniagents_workplace.factory_process import FactoryProcess
from omniagents_workplace.factory_secrets import KEY, FactorySecrets
from omniagents_workplace.server import Workplace, create_app
from test_factory_runtime import _workplace


@pytest.fixture
def setup(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")
    owner = w.accounts.by_username("bill")
    client = TestClient(create_app(w, static_dir=tmp_path))
    client.cookies.set(SESSION_COOKIE, w.accounts.start_session(owner))
    yield w, client
    w.close()


URL = "/workplace/api/factory/secrets"


def save(client, value):
    return client.put(URL, json={"values": {"SERPAPI_API_KEY": value}})


def test_encrypted_scoped_environment_and_removal(setup, tmp_path):
    w, client = setup
    config = w.factory_config.raw()
    config["env_file"] = str(tmp_path / "runtime.env")
    w.factory_config.save(w, config)
    assert save(client, "test-search-key").json() == {"configured": ["SERPAPI_API_KEY"]}
    assert "test-search-key" not in w.store.meta(KEY)
    assert "test-search-key" not in client.get(URL).text
    assert w.factory_secrets.key_path.stat().st_mode & 0o777 == 0o600
    assert FactorySecrets(w).environment()["SERPAPI_API_KEY"] == "test-search-key"
    app = w.apps.by_username("initech-factory")
    assert w.apps.env(app)["SERPAPI_API_KEY"] == "test-search-key"
    member_app = w.apps.register(
        w.accounts.by_username("bill"), name="Other app", username="other-app"
    )
    assert "SERPAPI_API_KEY" not in w.apps.env(member_app)
    process = FactoryProcess(tmp_path / "runtime", tmp_path / "project")
    env = process.environment(w, "personal", 8765, "t", "control", Path("profile.yml"))
    assert env["SERPAPI_API_KEY"] == "test-search-key"
    assert "SERPAPI_API_KEY=test-search-key" in (tmp_path / "runtime.env").read_text()
    other = Workplace(tmp_path / "other", base_url="http://other.test")
    try:
        assert other.factory_secrets.environment()["SERPAPI_API_KEY"] == ""
    finally:
        other.close()
    assert save(client, None).json() == {"configured": []}
    assert w.apps.env(app)["SERPAPI_API_KEY"] == ""
    assert "test-search-key" not in (tmp_path / "runtime.env").read_text()


def test_admin_csrf_and_no_arbitrary_environment(setup):
    w, client = setup
    assert client.put(URL, json={"values": {"PATH": "evil"}}).status_code == 422
    assert save(client, "bad\nkey").status_code == 422
    assert save(client, "").status_code == 422
    assert (
        client.put(
            URL,
            json={"values": {"SERPAPI_API_KEY": "key"}},
            headers={"origin": "https://evil.test"},
        ).status_code
        == 403
    )
    member = w.add_person(username="member", name="Member", email="member@example.test")
    client.cookies.set(SESSION_COOKIE, w.accounts.start_session(member))
    assert client.get(URL).status_code == 403
    assert save(client, "key").status_code == 403


def test_backup_restores_production_but_clears_staging(setup, tmp_path):
    w, client = setup
    assert save(client, "test-search-key").status_code == 200
    saved = backup(w.data_dir, tmp_path / "backups")
    for mode in ("production", "staging"):
        destination = tmp_path / mode
        restore(saved, destination, mode=mode)
        restored = Workplace(destination, base_url="http://restored.test", resume=False)
        try:
            expected = "test-search-key" if mode == "production" else ""
            assert restored.factory_secrets.environment()["SERPAPI_API_KEY"] == expected
            assert restored.factory_secrets.key_path.exists() == (mode == "production")
        finally:
            restored.close()
