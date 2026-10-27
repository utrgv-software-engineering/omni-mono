import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from omniagents_workplace.config import ConfigError, apply, load_config, write_env
from omniagents_workplace.server import Workplace, create_app

from conftest import assert_persisted

CONFIG = {
    "organization": {"name": "Initech", "slug": "initech", "tz": "America/New_York"},
    "admin": {
        "name": "Bill Lumbergh",
        "username": "bill",
        "email": "bill@initech.test",
    },
    "app": {
        "name": "Initech Factory",
        "username": "initech-factory",
        "description": "Software factory",
        "slack_delivery": "socket",
        "github_webhook": "http://127.0.0.1:8765/integrations/github/events",
        "linear_webhook": "http://127.0.0.1:8765/integrations/linear/events",
    },
    "env_file": ".env",
    "slack": {"channels": [{"name": "factory-prs", "purpose": "Every pull request"}]},
    "github": {
        "repositories": [
            {
                "name": "tps",
                "description": "TPS reports",
                "from": "tps",
                "labels": [{"name": "factory:Initech"}],
            },
            {"name": "web"},
        ]
    },
    "linear": {"teams": [{"key": "ENG", "name": "Engineering", "labels": ["Bug"]}]},
    "canvases": [{"title": "Studio", "from": "studio"}, {"title": "Blank"}],
}


@pytest.fixture
def config_dir(tmp_path):
    root = tmp_path / "factory"
    (root / "tps").mkdir(parents=True)
    (root / "tps" / "README.md").write_text("# TPS\n")
    (root / "workplace.yml").write_text(yaml.safe_dump(CONFIG))
    import shutil

    shutil.copytree(
        Path(__file__).parent / "fixtures" / "canvas" / "studio-redesign",
        root / "studio",
    )
    return root


def test_up_makes_what_a_factory_needs_and_its_admin_chooses_a_password(
    tmp_path, config_dir
):
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    applied = apply(
        workplace, load_config(config_dir / "workplace.yml"), config_dir=config_dir
    )
    ws, gh, li = workplace.slack, workplace.github, workplace.linear

    assert (ws.name, ws.domain, ws.tz) == ("Initech", "initech", "America/New_York")
    assert gh.organization().login == "initech" and li.url_key == "initech"
    bill = workplace.accounts.by_username("bill")
    assert bill.admin and bill.password is None and workplace.unclaimed() is bill

    app = applied.app
    assert app.username == "initech-factory" and not app.owner_id
    env = workplace.apps.env(app)
    assert (
        env["GITHUB_WEBHOOK_SECRET"]
        and env["LINEAR_WEBHOOK_SECRET"]
        and env["SLACK_APP_TOKEN"]
    )

    prs = ws.channel_by_name("factory-prs")
    assert prs.is_default and prs.purpose == "Every pull request"
    assert {bill.slack_id, app.slack_user_id} <= set(prs.members)
    assert bill.slack_id in ws.general().members

    tps = gh.repos["initech/tps"]
    assert (
        tps.bare.blob("main", "README.md")[1] == b"# TPS\n"
        and "factory:Initech" in tps.labels
    )
    assert (
        gh.repos["initech/web"].bare.blob("main", "README.md")[1].startswith(b"# web")
    )
    team = li.find_team("ENG")
    assert li.find_label("Bug", team.id) is not None
    assert sorted(c.title for c in workplace.canvas.canvases.values()) == [
        "Blank",
        "OmniAgents Studio redesign",
    ]
    assert env["CANVAS_MCP_TOKEN"].startswith("cnv_") and env[
        "CANVAS_MCP_URL"
    ].endswith("/canvas/mcp")

    # Again: nothing new, the same app.
    again = apply(
        workplace, load_config(config_dir / "workplace.yml"), config_dir=config_dir
    )
    assert again.made == [] and again.app.id == app.id

    # Someone added later joins the default channels.
    priya = workplace.add_person(
        username="priya", name="Priya Shah", email="priya@initech.test"
    )
    assert priya.slack_id in prs.members

    write_env(config_dir / ".env", env)
    assert (os.stat(config_dir / ".env").st_mode & 0o777) == 0o600

    app_ = create_app(workplace, static_dir=tmp_path)
    elsewhere, here = TestClient(app_), TestClient(app_, client=("127.0.0.1", 50000))
    assert elsewhere.get("/workplace/api/session").json()["claim_here"] is None
    assert (
        elsewhere.post(
            "/workplace/api/claim", json={"password": "tps-reports"}
        ).status_code
        == 403
    )
    assert here.get("/workplace/api/session").json()["claim_here"]["username"] == "bill"
    assert (
        here.post("/workplace/api/claim", json={"password": "short"}).status_code == 422
    )
    claimed = here.post(
        "/workplace/api/claim",
        json={"password": "tps-reports", "tz": "America/Chicago"},
    )
    assert (
        claimed.status_code == 200 and claimed.json()["account"]["username"] == "bill"
    )
    assert here.get("/workplace/api/session").json()["account"]["username"] == "bill"
    # Once chosen, it is signing in.
    assert (
        here.post("/workplace/api/claim", json={"password": "another-one"}).status_code
        == 403
    )
    assert workplace.unclaimed() is None
    assert_persisted(workplace)
    workplace.close()


def test_up_refuses_a_workplace_of_another_organization(tmp_path, config_dir):
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    workplace.setup(
        organization="Hooli",
        slug="hooli",
        username="gavin",
        name="Gavin Belson",
        email="gavin@hooli.test",
        password="nucleus-1",
        tz="America/Los_Angeles",
    )
    with pytest.raises(ConfigError, match="hooli"):
        apply(
            workplace, load_config(config_dir / "workplace.yml"), config_dir=config_dir
        )
    workplace.close()


def test_the_factorys_own_repository_is_made_from_its_checkout(tmp_path):
    import subprocess

    factory = tmp_path / "factory"
    factory.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "a@b.c"],
        ["config", "user.name", "A"],
    ):
        subprocess.run(["git", *args], cwd=factory, check=True)
    (factory / "project.yml").write_text("name: factory\n")
    (factory / ".gitignore").write_text(".env\n")
    (factory / ".env").write_text("SECRET=1\n")
    config = {
        "organization": {
            "name": "Initech",
            "slug": "initech",
            "tz": "America/New_York",
        },
        "admin": {"name": "Bill", "username": "bill", "email": "bill@initech.test"},
        "factory": {
            "repository": {
                "name": "factory",
                "from": ".",
                "required_checks": ["evaluate"],
            }
        },
        "app": {"name": "Initech Factory", "username": "initech-factory"},
    }
    # Before its first commit: the files git would track (never .env).
    workplace = Workplace(tmp_path / "data", base_url="http://w")
    try:
        applied = apply(workplace, config, config_dir=factory)
        assert "the factory's repository initech/factory" in applied.made
        repo = workplace.github.repos["initech/factory"]
        assert (
            repo.bare.blob("main", "project.yml")[1] == b"name: factory\n"
            and "main" in repo.protected
        )
        assert repo.self_hosted  # its evaluations run on the self-hosted runner
        assert repo.required_checks == {"main": ["evaluate"]}  # and must pass to merge
        assert sorted(repo.bare.files("main")) == [".gitignore", "project.yml"]
    finally:
        workplace.close()
    # With history: its history.
    subprocess.run(["git", "add", "-A"], cwd=factory, check=True)
    subprocess.run(["git", "commit", "-qm", "The factory"], cwd=factory, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=factory, capture_output=True, text=True
    ).stdout.strip()
    workplace = Workplace(tmp_path / "data2", base_url="http://w")
    try:
        apply(workplace, config, config_dir=factory)
        assert workplace.github.repos["initech/factory"].bare.branches()["main"] == head
    finally:
        workplace.close()


def test_a_relative_data_directory_works(tmp_path, monkeypatch):
    """bin/dev serves `--data .workplace`: repositories made from files still import."""
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "app.py").write_text("print('hi')\n")
    monkeypatch.chdir(tmp_path)
    config = {
        "organization": {
            "name": "Initech",
            "slug": "initech",
            "tz": "America/New_York",
        },
        "admin": {"name": "Bill", "username": "bill", "email": "bill@initech.test"},
        "github": {"repositories": [{"name": "svc", "from": "svc"}]},
        "app": {"name": "Initech Factory", "username": "initech-factory"},
    }
    workplace = Workplace(Path(".workplace"), base_url="http://w")
    try:
        apply(workplace, config, config_dir=tmp_path)
        assert (
            workplace.github.repos["initech/svc"].bare.blob("main", "app.py")[1]
            == b"print('hi')\n"
        )
    finally:
        workplace.close()


def test_with_no_admin_the_setup_page_makes_them_and_then_the_rest(
    tmp_path, config_dir
):
    """An empty admin: is no one (not whoever runs `up`): the organization is made,
    and the rest waits for the setup page, which asks only for the admin."""
    from omniagents_workplace.config import FactoryConfig

    config = dict(
        CONFIG, admin={"name": "", "username": "", "email": "", "password": "unused-1"}
    )
    (config_dir / "workplace.yml").write_text(yaml.safe_dump(config))
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    workplace.factory_config = FactoryConfig(config_dir / "workplace.yml", None)
    applied = workplace.factory_config.apply(workplace)
    assert applied.waiting and applied.app is None and workplace.accounts.needs_setup()
    assert (
        workplace.slack.name == "Initech"
        and "initech/tps" not in workplace.github.repos
    )
    assert not (config_dir / ".env").exists()

    here = TestClient(
        create_app(workplace, static_dir=tmp_path), client=("127.0.0.1", 50000)
    )
    session = here.get("/workplace/api/session").json()
    assert session["setup_here"] and session["organization"] == "Initech"
    made = here.post(
        "/workplace/api/setup",
        json={
            "token": "",
            "organization": "",
            "slug": "",
            "username": "peter",
            "name": "Peter Gibbons",
            "email": "peter@initech.test",
            "password": "tps-reports",
            "tz": "America/Chicago",
        },
    )
    assert made.status_code == 200 and made.json()["account"]["username"] == "peter"
    assert "initech/tps" in workplace.github.repos and (config_dir / ".env").is_file()
    assert workplace.accounts.by_username("peter").admin
    workplace.close()


def test_an_admin_named_in_part_is_an_error(tmp_path, config_dir):
    config = dict(CONFIG, admin={"name": "Bill Lumbergh", "username": "", "email": ""})
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    with pytest.raises(ConfigError, match="username, email"):
        apply(workplace, config, config_dir=config_dir)
    workplace.close()


def test_new_credentials_name_no_admin():
    from omniagents_workplace.credentials import template

    admin = yaml.safe_load(template("development"))["admin"]
    assert (admin["name"], admin["username"], admin["email"]) == ("", "", "") and len(
        admin["password"]
    ) > 16
