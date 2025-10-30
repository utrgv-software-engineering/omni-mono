import sys
import types

import pytest

from omniagents.cli import main as cli_main


class DummyRunner:
    def __init__(self, spec=None, config_path=None):
        self.spec = spec
        self.config_path = config_path
        self.calls = []

    def run(self, mode, **kwargs):
        self.calls.append((mode, kwargs))


@pytest.fixture
def stub_runtime(monkeypatch):
    module = types.ModuleType("omniagents.core.project.runtime")

    def _load_project(path):
        return {"path": path}

    def _build_spec_from_project(project, agent_key=None):
        return {"project": project, "agent_key": agent_key}

    module.load_project = _load_project
    module.build_spec_from_project = _build_spec_from_project
    monkeypatch.setitem(sys.modules, "omniagents.core.project.runtime", module)


def test_run_command_forwards_initial_prompt(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "tui",
        "hello",
        "world",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "tui"
    assert kwargs["initial_prompt"] == "hello world"
    assert kwargs["args"].prompt == ["hello", "world"]


def test_run_command_without_prompt_skips_initial_prompt(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "tui",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "tui"
    assert "initial_prompt" not in kwargs
