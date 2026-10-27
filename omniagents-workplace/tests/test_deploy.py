"""What a deployed workplace relies on: backups while it runs, sign-in throttling,
config from the environment, a sandbox-only port and a health check."""

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from omniagents_workplace.backup import backup
from omniagents_workplace.config import ConfigError, substitute
from omniagents_workplace.server import Workplace, create_app, load_seed


def test_a_backup_copies_the_database_and_repositories_while_it_runs(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        workplace.slack.post_message("U0ACME0PRI", "C0ACME0GEN", "Before the backup")
        made = [
            backup(tmp_path / "data", tmp_path / "backups", keep=2) for _ in range(3)
        ]
        kept = sorted(p.name for p in (tmp_path / "backups").iterdir())
        assert len(kept) <= 2 and made[-1].name in kept
        with sqlite3.connect(made[-1] / "workplace.db") as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM docs WHERE kind = 'slack.message'"
                ).fetchone()[0]
                > 0
            )
        assert (made[-1] / "git" / "acme" / "api-service.git" / "HEAD").is_file()
        restored = Workplace(made[-1], base_url="http://w")
        try:
            assert any(
                m.text == "Before the backup"
                for m in restored.slack.messages("C0ACME0GEN")
            )
        finally:
            restored.close()
    finally:
        workplace.close()


def test_too_many_wrong_passwords_wait(workplace_server):
    workplace, base_url = workplace_server
    for _ in range(10):
        assert (
            httpx.post(
                f"{base_url}/workplace/api/signin",
                json={"username": "priya", "password": "nope"},
            ).status_code
            == 401
        )
    waited = httpx.post(
        f"{base_url}/workplace/api/signin",
        json={"username": "priya", "password": "acme-priya"},
    )
    assert waited.status_code == 429 and "try again" in waited.json()["detail"]
    # Others are not held up.
    assert (
        httpx.post(
            f"{base_url}/workplace/api/signin",
            json={"username": "marcus", "password": "acme-marcus"},
        ).status_code
        == 200
    )


def test_a_config_takes_values_from_the_environment():
    env = {"ORG": "Acme", "EMPTY": ""}
    assert substitute({"a": "${ORG}", "b": ["x-${MISSING:-d}"], "c": 3}, env) == {
        "a": "Acme",
        "b": ["x-d"],
        "c": 3,
    }
    with pytest.raises(ConfigError, match="give the org"):
        substitute("${EMPTY:?give the org}", env)
    with pytest.raises(ConfigError, match="no NOPE"):
        substitute("${NOPE}", env)


def test_the_sandbox_port_serves_git_only_and_health_answers(tmp_path):
    workplace = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        app = create_app(workplace, static_dir=tmp_path, sandbox_ports=[7301])
        on_sandbox = TestClient(app, base_url="http://workplace:7301")
        on_public = TestClient(app, base_url="http://workplace:7300")
        assert (
            on_public.get("/healthz").json()
            == on_public.get("/up").json()
            == {"ok": True}
        )
        assert on_sandbox.get("/workplace/api/session").status_code == 403
        assert on_sandbox.post("/slack/mcp", json={}).status_code == 403
        refs = on_sandbox.get(
            "/github/acme/api-service.git/info/refs",
            params={"service": "git-upload-pack"},
        )
        assert refs.status_code != 403
        assert on_public.get("/workplace/api/session").status_code == 200
    finally:
        workplace.close()


def test_a_backup_restores_into_another_workplace(tmp_path):
    from omniagents_workplace.backup import restore

    workplace = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        workplace.slack.post_message("U0ACME0PRI", "C0ACME0GEN", "Production's message")
        backup(tmp_path / "data", tmp_path / "backups")
    finally:
        workplace.close()
    staging = Workplace(
        tmp_path / "staging", base_url="http://s"
    )  # a workplace of its own first
    staging.close()
    assert (
        restore(tmp_path / "backups", tmp_path / "staging").parent
        == tmp_path / "backups"
    )
    restored = Workplace(tmp_path / "staging", base_url="http://s")
    try:
        assert any(
            m.text == "Production's message"
            for m in restored.slack.messages("C0ACME0GEN")
        )
        assert (tmp_path / "staging" / "git" / "acme" / "api-service.git").is_dir()
    finally:
        restored.close()


def test_an_invite_from_the_command_line_lets_someone_choose_a_new_password(tmp_path):
    from omniagents_workplace.cli import invite_link

    workplace = Workplace(
        tmp_path / "data", base_url="https://w.example", seed=load_seed()
    )
    workplace.store.set_meta("workplace.served_at", {"base_url": "https://w.example"})
    workplace.close()
    link = invite_link(tmp_path / "data", "priya")
    assert link.startswith("https://w.example/invite/")
    again = Workplace(tmp_path / "data", base_url="https://w.example")
    try:
        account, _ = again.accounts.accept_invite(
            link.rsplit("/", 1)[1], "a-new-password"
        )
        assert account.username == "priya"
    finally:
        again.close()


def test_a_copy_of_a_workplace_sends_its_webhooks_to_its_own_factory(tmp_path):
    from omniagents_workplace.cli import repoint_webhooks

    workplace = Workplace(
        tmp_path / "data", base_url="https://w.example", seed=load_seed()
    )
    app = workplace.apps.by_username("acme-factory")
    workplace.apps.configure(
        app,
        github_webhook_url="http://factory:8765/integrations/github/events",
        linear_webhook_url="http://factory:8765/integrations/linear/events",
    )
    workplace.close()
    changed = repoint_webhooks(tmp_path / "data", "http://127.0.0.1:8775")
    assert (
        "acme-factory: GitHub webhooks go to http://127.0.0.1:8775/integrations/github/events"
        in changed
    )
    copy = Workplace(tmp_path / "data", base_url="https://w.example", resume=False)
    try:
        hooks = copy.apps.settings(copy.apps.by_username("acme-factory"))
        assert (
            hooks["github"]["webhook_url"]
            == "http://127.0.0.1:8775/integrations/github/events"
        )
        assert (
            hooks["linear"]["webhook_url"]
            == "http://127.0.0.1:8775/integrations/linear/events"
        )
    finally:
        copy.close()
    with pytest.raises(SystemExit):
        repoint_webhooks(tmp_path / "data", "factory:8775")


def test_a_backup_leaves_out_what_a_push_has_in_progress_and_restore_skips_unfinished_ones(
    tmp_path,
):
    from omniagents_workplace.backup import backup, restore

    workplace = Workplace(
        tmp_path / "data", base_url="https://w.example", seed=load_seed()
    )
    workplace.close()
    repo = tmp_path / "data" / "git" / "acme" / "api-service.git"
    incoming = repo / "objects" / "tmp_objdir-incoming-x" / "d1"
    incoming.mkdir(parents=True)
    (incoming / "abc").write_text("half a push")
    (repo / "refs" / "heads" / "main.lock").write_text("locked")
    made = backup(tmp_path / "data", tmp_path / "backups")
    copied = made / "git" / "acme" / "api-service.git"
    assert (copied / "HEAD").is_file() and (copied / "objects").is_dir()
    assert not (copied / "objects" / "tmp_objdir-incoming-x").exists()
    assert not (copied / "refs" / "heads" / "main.lock").exists()
    # A later backup that never finished is not the latest one to restore.
    unfinished = tmp_path / "backups" / "workplace-29991231T000000000Z.partial"
    unfinished.mkdir()
    (unfinished / "workplace.db").write_text("")
    assert restore(tmp_path / "backups", tmp_path / "staging") == made
    # The copy is a workplace of its own.
    original = Workplace(tmp_path / "data", base_url="https://w.example", resume=False)
    copy = Workplace(tmp_path / "staging", base_url="https://w.example", resume=False)
    try:
        assert (
            original.instance and copy.instance and original.instance != copy.instance
        )
    finally:
        original.close()
        copy.close()
