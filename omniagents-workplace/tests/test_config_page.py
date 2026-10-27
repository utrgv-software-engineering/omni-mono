"""The configuration page (development's, as Jumpstart's /jumpstart): workplace.yml edited from the UI."""

import yaml
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.config import FactoryConfig
from omniagents_workplace.credentials import Credentials
from omniagents_workplace.server import Workplace, create_app

CONFIG = {
    "organization": {"name": "Initech", "slug": "initech", "tz": "America/New_York"},
    "admin": "${credentials.admin}",
    "app": {
        "name": "Initech Factory",
        "username": "initech-factory",
        "github_webhook": "${FACTORY_URL:-http://127.0.0.1:8765}/integrations/github/events",
    },
    "env_file": ".env",
    "slack": {"channels": [{"name": "eng", "purpose": "Engineering"}]},
}


def _served(tmp_path, monkeypatch, environment="development"):
    monkeypatch.delenv("WORKPLACE_MASTER_KEY", raising=False)
    root = tmp_path / "factory"
    root.mkdir(parents=True)
    (root / "workplace.yml").write_text(
        "# Initech's workplace.\n\n" + yaml.safe_dump(CONFIG, sort_keys=False)
    )
    credentials = Credentials(root, environment)
    credentials.create()
    credentials.write(
        credentials.read()
        .replace('name: ""', 'name: "Bill Lumbergh"', 1)
        .replace('username: ""', 'username: "bill"', 1)
        .replace('email: ""', 'email: "bill@initech.test"', 1)
    )
    workplace = Workplace(
        tmp_path / "data", base_url="http://127.0.0.1:7300", environment=environment
    )
    workplace.factory_config = FactoryConfig(
        root / "workplace.yml", Credentials(root, environment)
    )
    workplace.factory_config.apply(workplace)
    return workplace, root


def _as(workplace, username):
    client = TestClient(create_app(workplace, static_dir=workplace.data_dir))
    client.cookies.set(
        SESSION_COOKIE,
        workplace.accounts.start_session(workplace.accounts.by_username(username)),
    )
    return client


def test_an_admin_edits_the_factorys_config_and_it_is_applied(tmp_path, monkeypatch):
    workplace, root = _served(tmp_path, monkeypatch)
    try:
        bill = _as(workplace, "bill")
        session = bill.get("/workplace/api/session").json()
        assert session["configurable"] and session["environment"] == "development"
        got = bill.get("/workplace/api/config").json()
        assert (
            got["config"]["admin"] == "${credentials.admin}" and got["credentials"]
        )  # as written, unread
        edited = got["config"]
        edited["slack"]["channels"].append({"name": "launch", "purpose": "The launch"})
        edited["github"] = {
            "repositories": [
                {"name": "tps", "labels": [{"name": "bug", "color": "d73a4a"}]}
            ]
        }
        saved = bill.put("/workplace/api/config", json={"config": edited})
        assert saved.status_code == 200, saved.text
        assert (
            "#launch" in saved.json()["made"]
            and "the repository initech/tps" in saved.json()["made"]
        )
        assert (
            workplace.slack.channel_by_name("launch")
            and "bug" in workplace.github.repos["initech/tps"].labels
        )
        assert (
            (root / "workplace.yml")
            .read_text()
            .startswith("# Initech's workplace.\n\n")
        )
        written = yaml.safe_load((root / "workplace.yml").read_text())
        assert (
            written["admin"] == "${credentials.admin}"
            and written["slack"]["channels"][-1]["name"] == "launch"
        )

        # Another organization does not apply here, and the file stays as it was.
        broken = {
            **edited,
            "organization": {
                "name": "Hooli",
                "slug": "hooli",
                "tz": "America/New_York",
            },
        }
        assert (
            bill.put("/workplace/api/config", json={"config": broken}).status_code
            == 422
        )
        assert (
            yaml.safe_load((root / "workplace.yml").read_text())["organization"]["slug"]
            == "initech"
        )

        made = bill.post(
            "/workplace/api/config/admins",
            json={
                "name": "Milton",
                "username": "milton",
                "email": "milton@initech.test",
                "password": "red-stapler",
            },
        )
        assert (
            made.status_code == 200
            and workplace.accounts.sign_in("milton", "red-stapler")[0].admin
        )
        assert _as(workplace, "milton").get("/workplace/api/config").status_code == 200
    finally:
        workplace.close()


def test_the_configuration_page_is_developments_and_admins(tmp_path, monkeypatch):
    workplace, _ = _served(tmp_path, monkeypatch, environment="production")
    try:
        bill = _as(workplace, "bill")
        assert not bill.get("/workplace/api/session").json()["configurable"]
        assert bill.get("/workplace/api/config").status_code == 404
    finally:
        workplace.close()
    workplace, _ = _served(tmp_path / "dev", monkeypatch)
    try:
        workplace.add_person(username="peter", name="Peter", email="peter@initech.test")
        assert _as(workplace, "peter").get("/workplace/api/config").status_code == 403
    finally:
        workplace.close()
