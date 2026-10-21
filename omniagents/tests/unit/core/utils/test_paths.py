import os
from pathlib import Path

import pytest

from omniagents.core import paths


@pytest.fixture(autouse=True)
def clear_path_env(monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_HOME", raising=False)
    monkeypatch.delenv("OMNIAGENTS_HISTORY_DB", raising=False)


def test_get_home_dir_uses_env_override(tmp_path, monkeypatch):
    target = tmp_path / "custom"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(target))
    result = paths.get_home_dir()
    assert result == target.resolve()


def test_get_home_dir_defaults_to_dot_omniagents(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda _: tmp_path))
    expected = (tmp_path / ".omniagents").resolve()
    result = paths.get_home_dir()
    assert result == expected


def test_get_traces_and_sessions_dirs_created(tmp_path, monkeypatch):
    base = tmp_path / "base"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(base))
    traces_dir = paths.get_traces_dir()
    sessions_dir = paths.get_sessions_dir()
    assert traces_dir.is_dir()
    assert sessions_dir.is_dir()
    assert traces_dir.parent == base.resolve()
    assert sessions_dir.parent == base.resolve()


def test_get_sessions_db_path_uses_override(monkeypatch, tmp_path):
    target = tmp_path / "override.db"
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(target))
    result = paths.get_sessions_db_path("project", "agent")
    assert result == target.resolve()


def test_get_sessions_db_path_normalizes_slugs(tmp_path, monkeypatch):
    base = tmp_path / "home"
    monkeypatch.setenv("OMNIAGENTS_HOME", str(base))
    path = paths.get_sessions_db_path(" My Project ", "Agent@Name!")
    expected = base.resolve() / "sessions" / "my_project" / "agent_name" / "sessions.db"
    assert path == expected
