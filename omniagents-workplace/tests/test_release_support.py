import copy
import sqlite3
import shutil
from types import SimpleNamespace

import pytest

from omniagents_workplace.github.workflow_settings import WorkflowSettings
from omniagents_workplace.deployments.releases import verify
from omniagents_workplace.backup import backup, restore
from omniagents_workplace.store import Store


def test_restore_legacy_snapshot_preserves_committed_wal(tmp_path):
    live = Store(tmp_path / "live/workplace.db")
    try:
        live.set_meta("proof", "committed WAL data")
        source = tmp_path / "legacy"
        source.mkdir()
        for name in ["workplace.db", "workplace.db-wal"]:
            shutil.copyfile(live.path.parent / name, source / name)
        restore(source, tmp_path / "restored", mode="production")
        restored = Store(tmp_path / "restored/workplace.db")
        try:
            assert restored.meta("proof") == "committed WAL data"
        finally:
            restored.close()
    finally:
        live.close()


def test_additional_sqlite_wal_and_non_sqlite_db(tmp_path):
    source = tmp_path / "source"
    Store(source / "workplace.db").close()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "opaque.db").write_bytes(b"not a SQLite database")
    with sqlite3.connect(runtime / "events.sqlite3") as live:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE events (value TEXT)")
        live.execute("INSERT INTO events VALUES ('durable')")
        live.commit()
        saved = backup(source, tmp_path / "backups", include={"runtime": runtime})
        with sqlite3.connect(saved / "additional/runtime/events.sqlite3") as restored:
            assert restored.execute("SELECT value FROM events").fetchone() == (
                "durable",
            )
            assert restored.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert not list(saved.rglob("*-wal"))
        assert not list(saved.rglob("*-shm"))
    assert (
        saved / "additional/runtime/opaque.db"
    ).read_bytes() == b"not a SQLite database"


@pytest.fixture
def configured(tmp_path):
    repo = SimpleNamespace(
        full_name="course/demo",
        default_branch="main",
        protected=["main"],
        bare=SimpleNamespace(
            blob=lambda *_: ("blob", b"on: push\njobs: {}"), ref_sha=lambda *_: "a" * 40
        ),
    )
    settings = WorkflowSettings(tmp_path)
    settings.configure(
        repo,
        variables={"REGISTRY": "registry.test"},
        credentials={"DEPLOY_KEY": "private-value"},
        approve_workflows=[".github/workflows/ci.yml"],
    )
    run = SimpleNamespace(
        event="push",
        head_branch="main",
        head_sha="a" * 40,
        path=".github/workflows/ci.yml",
    )
    return settings, repo, run


def test_credential_gates(configured):
    settings, repo, run = configured
    assert "private-value" not in settings.path.read_text()
    assert settings.path.stat().st_mode & 0o777 == 0o600
    assert settings.execution(repo, run)[1] == {"DEPLOY_KEY": "private-value"}
    assert settings.public(repo.full_name)["secret_names"] == ["DEPLOY_KEY"]
    for field, value in [("event", "pull_request"), ("head_branch", "feature")]:
        changed = copy.copy(run)
        setattr(changed, field, value)
        assert settings.execution(repo, changed)[1] == {}
    for field, value in [
        ("head_sha", "b" * 40),
        ("path", ".github/workflows/changed.yml"),
    ]:
        changed = copy.copy(run)
        setattr(changed, field, value)
        with pytest.raises(ValueError):
            settings.execution(repo, changed)
    repo.protected = []
    with pytest.raises(ValueError):
        settings.execution(repo, run)


def test_workflow_edit_requires_reapproval(configured):
    settings, repo, run = configured
    repo.bare.blob = lambda *_: ("new", b"changed workflow")
    with pytest.raises(ValueError, match="administrator"):
        settings.execution(repo, run)
    settings.configure(repo, approve_workflows=[run.path])
    assert settings.execution(repo, run)[1]["DEPLOY_KEY"] == "private-value"


def test_read_token_scope_and_revocation(configured):
    settings, repo, _ = configured
    old = settings.rotate_release_token(repo.full_name)
    assert settings.permits_release_read(repo.full_name, old)
    assert not settings.permits_release_read("course/other", old)
    new = settings.rotate_release_token(repo.full_name)
    assert not settings.permits_release_read(repo.full_name, old)
    assert settings.permits_release_read(repo.full_name, new)


@pytest.mark.parametrize("provider", ["github", "standin"])
def test_release_gate(provider):
    config = dict(
        provider=provider,
        repository="course/demo",
        branch="main",
        workflow=".github/workflows/ci.yml",
        workflow_sha256="approved",
        required_checks=["test", "image"],
    )
    observed = dict(
        provider=provider,
        repository="course/demo",
        run_id="12",
        revision="a" * 40,
        current_revision="a" * 40,
        branch="main",
        default_branch="main",
        event="push",
        path=config["workflow"],
        workflow_sha256="approved",
        protected=True,
        jobs=[
            dict(name=n, status="completed", conclusion="success")
            for n in ["test", "image"]
        ],
    )
    args = dict(revision="a" * 40, run_id=12, image_digest="sha256:" + "b" * 64)
    assert verify(config, observed, **args)["revision"] == "a" * 40
    original_tag = verify(config, observed, **args)["image_tag"]
    rebuilt_tag = verify(
        config, observed, **{**args, "image_digest": "sha256:" + "c" * 64}
    )["image_tag"]
    assert (
        original_tag != rebuilt_tag
    )  # Same-source rebuilds keep independent rollback artifacts.
    for field, value in [
        ("current_revision", "c" * 40),
        ("event", "pull_request"),
        ("workflow_sha256", "changed"),
        ("protected", False),
        ("repository", "other/repo"),
    ]:
        wrong = copy.deepcopy(observed)
        wrong[field] = value
        with pytest.raises(ValueError):
            verify(config, wrong, **args)
    for result in ["failure", "skipped", None]:
        wrong = copy.deepcopy(observed)
        wrong["jobs"][0]["conclusion"] = result
        with pytest.raises(ValueError):
            verify(config, wrong, **args)
    wrong = copy.deepcopy(observed)
    wrong["jobs"] = []
    with pytest.raises(ValueError):
        verify(config, wrong, **args)


def test_production_and_staging_restore(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for directory in [source, source / "installation/factories/id/workplace"]:
        store = Store(directory / "workplace.db")
        store.set_meta("workplace.instance", "existing")
        store.set_meta("repository.github.connection", "sealed")
        store.close()
        for name, value in [
            ("github-connection.key", "private-key"),
            ("workflow-settings.key", "workflow-key"),
            ("workflow-settings.enc", "sealed-config"),
        ]:
            (directory / name).write_text(value)
    registry = Store(source / "installation/installation.db")
    original_factory = {
        "id": "id",
        "autostart": True,
        "project": "/production",
        "runtime_container": "production-factory",
    }
    registry.put("factory", "id", original_factory)
    registry.close()
    saved = backup(source, tmp_path / "backups")
    with pytest.raises(ValueError, match="outside"):
        restore(saved, saved / "restored")
    for mode in ["production", "staging"]:
        target = tmp_path / mode
        restore(saved, target, mode=mode)
        registry_path = target / "installation/installation.db"
        assert registry_path.exists() == (mode == "production")
        inspected = Store(
            registry_path
            if mode == "production"
            else registry_path.with_name("installation.snapshot.db")
        )
        assert inspected.docs("factory") == [("id", original_factory)]
        inspected.close()
        if mode == "staging":
            fresh_host = Store(registry_path)
            assert fresh_host.docs("factory") == []
            fresh_host.close()
        for directory in [target, target / "installation/factories/id/workplace"]:
            store = Store(directory / "workplace.db")
            assert bool(store.meta("repository.github.connection")) == (
                mode == "production"
            )
            assert (directory / "github-connection.key").exists() == (
                mode == "production"
            )
            assert (directory / "workflow-settings.enc").exists() == (
                mode == "production"
            )
            store.close()
    with pytest.raises(ValueError):
        restore(saved, saved, mode="production")
    assert (saved / "workplace.db").exists()


def test_workflow_admin_api_and_scoped_evidence(tmp_path):
    from fastapi.testclient import TestClient
    from omniagents_workplace.server import Workplace, create_app, load_seed
    from omniagents_workplace.accounts import SESSION_COOKIE

    w = Workplace(tmp_path / "data", base_url="http://testserver", seed=load_seed())
    try:
        client = TestClient(create_app(w, static_dir=tmp_path))
        repo = w.github.repos["acme/api-service"]
        path = "/workplace/api/github/repos/acme/api-service/workflow-settings"
        assert client.get(path).status_code == 401
        client.cookies.set(
            SESSION_COOKIE, w.accounts.start_session(w.accounts.by_username("admin"))
        )
        result = client.put(
            path,
            json={
                "secrets": {"DEPLOY_KEY": "never-return-me"},
                "variables": {"TARGET": "test"},
            },
        )
        assert result.status_code == 200 and "never-return-me" not in result.text
        token = client.post(path + "/release-token", json={}).json()["token"]
        repo.runs.append(
            SimpleNamespace(
                id=991,
                head_sha=repo.bare.ref_sha(repo.default_branch),
                head_branch=repo.default_branch,
                event="push",
                path=".github/workflows/ci.yml",
                status="in_progress",
                jobs=[],
            )
        )
        url = "/github/release-evidence/acme/api-service/991"
        client.cookies.clear()
        assert client.get(url).status_code == 403
        response = client.get(url, headers={"Authorization": "Bearer " + token})
        assert (
            response.status_code == 200
            and response.json()["repository"] == "acme/api-service"
        )
        assert (
            client.get(
                "/github/release-evidence/acme/other/991",
                headers={"Authorization": "Bearer " + token},
            ).status_code
            == 403
        )
        member = w.add_person(
            username="member", name="Member", email="member@example.test"
        )
        client.cookies.set(SESSION_COOKIE, w.accounts.start_session(member))
        assert client.put(path, json={"secrets": {"X": "oops"}}).status_code == 403
        assert client.post(path + "/release-token", json={}).status_code == 403
    finally:
        w.close()


def test_additional_state_must_be_restored_explicitly_and_live_engine_is_excluded(
    tmp_path,
):
    source = tmp_path / "source"
    Store(source / "workplace.db").close()
    runtime = tmp_path / "runtime"
    store = Store(runtime / "sessions.db")
    store.set_meta("memory", "durable")
    store.close()
    engine = runtime / "deployments/coolify/engine"
    engine.mkdir(parents=True)
    (engine / "live-layer").write_text("not a consistent backup")
    saved = backup(source, tmp_path / "backups", include={"runtime": runtime})
    assert not (saved / "additional/runtime/deployments/coolify/engine").exists()
    with pytest.raises(ValueError, match="destinations"):
        restore(saved, tmp_path / "new-data", mode="production")
    assert not (tmp_path / "new-data").exists()
    restore(
        saved,
        tmp_path / "new-data",
        mode="production",
        include={"runtime": tmp_path / "new-runtime"},
    )
    store = Store(tmp_path / "new-runtime/sessions.db")
    assert store.meta("memory") == "durable"
    store.close()


def test_runtime_exclusions_preserve_durable_state(tmp_path):
    from omniagents_workplace.backup import _backup_bytes

    source = tmp_path / "source"
    Store(source / "workplace.db").close()
    runtime = tmp_path / "runtime"
    for name in ("quicksand", "recovery-old", "sessions", "integrations"):
        (runtime / name).mkdir(parents=True)
        (runtime / name / "state").write_text(name)
    excludes = ("runtime/quicksand", "runtime/recovery-*")
    assert _backup_bytes(runtime, exclude=excludes, relative="runtime") == len(
        "sessionsintegrations"
    )
    saved = backup(
        source, tmp_path / "backups", include={"runtime": runtime}, exclude=excludes
    )
    assert {p.name for p in (saved / "additional/runtime").iterdir()} == {
        "sessions",
        "integrations",
    }
    restore(
        saved,
        tmp_path / "restored",
        mode="production",
        include={"runtime": tmp_path / "restored-runtime"},
    )
    assert (tmp_path / "restored-runtime/sessions/state").read_text() == "sessions"
    for pattern in ("runtime", "workplace.db", "runtime/../sessions"):
        with pytest.raises(ValueError):
            backup(
                source,
                tmp_path / "backups",
                include={"runtime": runtime},
                exclude=(pattern,),
            )
