"""Checks the deployment script as source; never starts containers or a factory."""

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture
def supervisor(monkeypatch, tmp_path):
    # main() deliberately updates the process env; restore that mutation too.
    monkeypatch.setenv("GITHUB_ACCOUNT", "")
    for key, value in {
        "FACTORY_REPOSITORY_URL": "http://workplace/github/org/factory.git",
        "FACTORY_BRANCH": "main",
        "FACTORY_LIVE": str(tmp_path / "live"),
        "PROMOTION_POLL_SECONDS": "30",
        "FACTORY_PORT": "8765",
        "FACTORY_AUTH_TOKEN": "test",
        "PROMOTION_GRACE_SECONDS": "60",
        "FACTORY_ENV_FILE": str(tmp_path / "factory.env"),
    }.items():
        monkeypatch.setenv(key, value)
    default = (
        Path(__file__).resolve().parents[3]
        / "omni-factory/deploy/factory-supervisor.py"
    )
    path = Path(os.environ.get("SUPERVISOR_UNDER_TEST", str(default)))
    spec = importlib.util.spec_from_file_location("tested_factory_supervisor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_environment_is_parsed_without_shell_execution(supervisor, tmp_path):
    from omniagents_workplace.config import write_env

    path = tmp_path / "factory.env"
    marker = tmp_path / "must-not-exist"
    values = {
        "A": "value with spaces",
        "SECRET": "quoted'password",
        "UNTRUSTED": f"$(touch {marker})",
        "EMPTY": "",
    }
    write_env(path, values)
    assert supervisor.read_environment() == values
    assert not marker.exists()
    before = path.stat().st_mtime_ns
    write_env(path, values)
    assert path.stat().st_mtime_ns == before


def test_promotion_requires_opt_in_and_valid_link(supervisor, monkeypatch):
    from omniagents.integrations import self_improvement

    current = {
        "enabled": False,
        "repository": "org/factory",
        "provider": "github",
        "branch": "release",
    }
    monkeypatch.setattr(self_improvement, "policy", lambda: current)
    assert supervisor.promotion_target() is None
    current["enabled"] = True
    assert supervisor.promotion_target() == (
        "https://github.com/org/factory.git",
        "release",
    )
    current["repository"] = "../factory"
    assert supervisor.promotion_target() is None


def test_git_credentials_are_scoped_to_repository(supervisor, monkeypatch):
    from types import SimpleNamespace

    seen = []
    monkeypatch.setenv("FACTORY_BOOTSTRAP_TOKEN", "standin-secret")
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "real-secret")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda cmd, **kw: seen.append(cmd)
        or SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    supervisor.git("ls-remote", supervisor.REPOSITORY)
    assert f"http.{supervisor.REPOSITORY}.extraHeader=" in seen[-1][2]
    assert "http.extraHeader=" not in str(seen)


def test_restart_with_loop_off_keeps_installed_commit(supervisor, monkeypatch):
    from types import SimpleNamespace

    (supervisor.LIVE / ".git").mkdir(parents=True)
    started = []
    monkeypatch.setattr(supervisor, "read_environment", lambda: {})
    monkeypatch.setattr(supervisor, "git", lambda *a, **kw: "installed-sha")
    monkeypatch.setattr(supervisor, "dependencies", lambda previous: None)
    monkeypatch.setattr(
        supervisor,
        "start",
        lambda sha: started.append(sha) or SimpleNamespace(poll=lambda: None),
    )
    monkeypatch.setattr(supervisor, "promotion_target", lambda: None)
    monkeypatch.setattr(
        supervisor,
        "remote_head",
        lambda: pytest.fail("disabled loop fetched a new version"),
    )
    monkeypatch.setattr(supervisor.signal, "signal", lambda *a: None)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise StopIteration

    monkeypatch.setattr(
        supervisor, "time", SimpleNamespace(sleep=sleep, monotonic=lambda: 0)
    )
    with pytest.raises(StopIteration):
        supervisor.main()
    assert started == ["installed-sha"]


def test_settings_reload_waits_until_idle_and_keeps_installed_commit(
    supervisor, monkeypatch
):
    from types import SimpleNamespace

    (supervisor.LIVE / ".git").mkdir(parents=True)
    started, stopped = [], []
    environments = iter(
        [
            {"GITHUB_ACCOUNT": "old"},
            {"GITHUB_ACCOUNT": "new"},
            {"GITHUB_ACCOUNT": "new"},
        ]
    )
    busy = iter([1, 0])
    monkeypatch.setattr(supervisor, "read_environment", lambda: next(environments))
    monkeypatch.setattr(supervisor, "git", lambda *a, **kw: "installed-sha")
    monkeypatch.setattr(supervisor, "dependencies", lambda previous: None)
    monkeypatch.setattr(
        supervisor,
        "start",
        lambda sha: started.append((sha, os.environ["GITHUB_ACCOUNT"]))
        or SimpleNamespace(poll=lambda: None),
    )
    monkeypatch.setattr(supervisor, "stop", lambda server: stopped.append(server))
    monkeypatch.setattr(supervisor, "running_runs", lambda: next(busy))
    monkeypatch.setattr(supervisor.signal, "signal", lambda *a: None)
    monkeypatch.setattr(
        supervisor,
        "time",
        SimpleNamespace(sleep=lambda seconds: None, monotonic=lambda: 0),
    )
    with pytest.raises(StopIteration):
        supervisor.main()
    assert started == [("installed-sha", "old"), ("installed-sha", "new")]
    assert len(stopped) == 1
