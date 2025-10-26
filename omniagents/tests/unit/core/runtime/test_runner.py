import argparse
import types

import pytest

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.runner import Runner


def make_spec(**overrides):
    base = {
        "name": overrides.pop("name", "Agent"),
        "header_title": "Header",
        "page_title": "Page",
        "welcome_text": "Welcome",
        "client_storage_prefix": "prefix",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


def test_from_agent_configures_safe_options_and_server_functions(monkeypatch):
    processed = object()

    def fake_process(instructions):
        return processed

    captured_server_functions = {}

    def fake_registrar(functions):
        captured_server_functions["functions"] = functions
        return "registrar"

    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        fake_process,
    )
    monkeypatch.setattr(
        "omniagents.core.server_functions.discovery.build_server_functions_registrar",
        fake_registrar,
    )

    class DummyAgent:
        def __init__(self):
            self.name = "Helper Bot"
            self.instructions = "hello"
            self.tools = ["tool"]
            self.model = "gpt"

    agent = DummyAgent()
    variables = {"user": "sam"}
    built_context = {"built": True}

    def build_context(data):
        assert data is variables
        return built_context

    runner = Runner.from_agent(
        agent,
        welcome_text="Hi",
        safe=True,
        skip_approvals=True,
        halt_on_rejection=False,
        safe_tool_names=["t"],
        safe_tool_patterns=["tool-.*"],
        variables=variables,
        build_context=build_context,
        max_turns=5,
        server_functions=["fn"],
    )

    spec = runner.spec
    assert agent.instructions is processed
    assert spec.use_safe_agent
    options = spec.safe_agent_options
    assert options.skip_approvals
    assert not options.halt_on_rejection
    assert options.safe_tool_names == ["t"]
    assert options.safe_tool_patterns == ["tool-.*"]
    assert spec.context is built_context
    assert spec.variables is variables
    assert spec.build_context is build_context
    assert spec.max_turns == 5
    assert spec.client_storage_prefix == "helper_bot."
    assert captured_server_functions["functions"] == ["fn"]
    assert spec.register_server_functions == "registrar"


def test_from_agent_without_build_context_uses_variables_as_context(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        lambda value: value,
    )

    class DummyAgent:
        def __init__(self):
            self.name = "Solo"
            self.instructions = "text"
            self.tools = []
            self.model = "model"

    agent = DummyAgent()
    variables = {"x": 1}
    runner = Runner.from_agent(agent, variables=variables)

    spec = runner.spec
    assert spec.context is variables
    assert spec.variables is None


def test_from_remote_uses_env_token(monkeypatch):
    monkeypatch.setenv("REMOTE_TOKEN", "secret")
    runner = Runner.from_remote(
        "ws://localhost/ws",
        name="Remote",
        token=None,
        token_env_var="REMOTE_TOKEN",
    )
    spec = runner.spec
    assert spec.remote_ws_url == "ws://localhost/ws"
    assert spec.remote_auth_token_key == "REMOTE_TOKEN"
    assert spec.client_storage_prefix == "remote."
    assert spec.welcome_text.startswith("Connected to Remote")


def test_run_invokes_backend_with_spec(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    captured = {}

    class DummyBackend:
        def run(self, spec, **kwargs):
            captured["spec"] = spec
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_backend", lambda mode: DummyBackend
    )

    runner.run("cli", extra=True)
    assert captured["spec"] is spec
    assert captured["kwargs"] == {"extra": True}


def test_run_unknown_backend_raises(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_backend", lambda mode: None
    )

    with pytest.raises(ValueError):
        runner.run("ghost")


def test_main_cli_mode_invokes_run_and_debug(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="cli",
        debug=True,
        resume=False,
        session_id="sess",
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
    )
    called = {}

    def fake_parse_args(self):
        return args

    def fake_run(self, mode, **kwargs):
        called["mode"] = mode
        called["kwargs"] = kwargs

    def fake_debug(cli_flag):
        called["debug"] = cli_flag

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse_args)
    monkeypatch.setattr(Runner, "run", fake_run)
    monkeypatch.setattr("omniagents.core.runtime.runner.Debug.init", fake_debug)

    runner.main()
    assert called["mode"] == "cli"
    assert called["kwargs"]["session_id"] == "sess"
    assert called["kwargs"]["args"] is args
    assert called["debug"]


def test_main_resume_invalid_mode_exits(monkeypatch, capsys):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="cli",
        debug=False,
        resume=True,
        session_id=None,
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
    )

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )

    with pytest.raises(SystemExit) as exc:
        runner.main()

    assert exc.value.code == 2
    output = capsys.readouterr().out
    assert "--resume is only supported" in output


def test_main_resume_tui_mode_sets_flag(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="bubbletea",
        debug=False,
        resume=True,
        session_id=None,
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
    )
    called = {}

    def fake_run(self, mode, **kwargs):
        called["mode"] = mode
        called["kwargs"] = kwargs

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(Runner, "run", fake_run)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )

    runner.main()
    assert called["mode"] == "bubbletea"
    assert called["kwargs"]["resume"]
    assert called["kwargs"]["args"] is args
