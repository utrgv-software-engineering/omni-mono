import os

import pytest

from omniagents.core.debug import Debug


@pytest.fixture(autouse=True)
def reset_debug_state(monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_DEBUG", raising=False)
    Debug.set_enabled(False)
    yield
    monkeypatch.delenv("OMNIAGENTS_DEBUG", raising=False)
    Debug.set_enabled(False)


def test_init_prioritizes_cli_flag_sets_env():
    Debug.init(cli_flag=True, yaml_config=False)
    assert Debug.enabled()
    assert os.getenv("OMNIAGENTS_DEBUG") == "1"


def test_init_uses_yaml_when_cli_disabled():
    Debug.init(cli_flag=False, yaml_config=True)
    assert Debug.enabled()
    assert os.getenv("OMNIAGENTS_DEBUG") is None


def test_init_reads_environment_variable(monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_DEBUG", "true")
    Debug.init(cli_flag=False, yaml_config=False)
    assert Debug.enabled()


def test_init_without_flags_leaves_debug_disabled():
    Debug.init(cli_flag=False, yaml_config=False)
    assert Debug.enabled() is False


def test_set_enabled_toggles_environment(monkeypatch):
    Debug.set_enabled(True)
    assert os.getenv("OMNIAGENTS_DEBUG") == "1"
    Debug.set_enabled(False)
    assert os.getenv("OMNIAGENTS_DEBUG") is None
