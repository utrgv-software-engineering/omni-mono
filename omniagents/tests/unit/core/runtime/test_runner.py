import argparse
import types

import pytest

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.runner import Runner


def make_spec(**overrides):
    base = {
        "name": overrides.pop("name", "Base"),
        "header_title": "Header",
        "page_title": "Page",
        "welcome_text": "Welcome",
        "client_storage_prefix": "client",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


def test_from_agent_configures_safe_options_and_server_functions(monkeypatch):
    agent = types.SimpleNamespace(
        name="Test Agent",
        instructions="original",
        tools=[object()],
        model="model",
    )
    processed = object()

    def fake_process(value):
        assert value == "original"
        return processed

    registrar_calls = []

    def fake_registrar(functions):
        registrar_calls.append(functions)

        def registrar(target):
            registrar_calls.append(target)

        return registrar

    def build_context(values):
        return {"context": values}

    server_fn = object()

    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        fake_process,
    )
    monkeypatch.setattr(
        "omniagents.core.server_functions.discovery.build_server_functions_registrar",
        fake_registrar,
    )

    runner = Runner.from_agent(
        agent,
        safe=True,
        skip_approvals=True,
        halt_on_rejection=False,
        safe_tool_names=["tool-a"],
        safe_tool_patterns=["pattern-.*"],
        variables={"user": "Ada"},
        build_context=build_context,
        server_functions=[server_fn],
        welcome_text="Hi",
        context_window=200,
        max_turns=5,
    )

    spec = runner.spec

    assert spec.use_safe_agent is True
    assert spec.safe_agent_options.skip_approvals is True
    assert spec.safe_agent_options.halt_on_rejection is False
    assert spec.safe_agent_options.safe_tool_names == ["tool-a"]
    assert spec.safe_agent_options.safe_tool_patterns == ["pattern-.*"]
    assert spec.context == {"context": {"user": "Ada"}}
    assert spec.variables == {"user": "Ada"}
    assert spec.build_context is build_context
    assert spec.register_server_functions is not None

    spec.register_server_functions("service")

    assert registrar_calls == [[server_fn], "service"]
    assert spec.client_storage_prefix == "test_agent."
    assert spec.agent_slug == "test_agent"
    assert agent.instructions is processed


def test_from_agent_uses_variables_as_context_without_builder(monkeypatch):
    agent = types.SimpleNamespace(
        name="Solo",
        instructions="raw",
        tools=[],
        model="m",
    )
    processed = object()

    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        lambda value: processed,
    )

    runner = Runner.from_agent(agent, variables={"v": 1})

    spec = runner.spec

    assert spec.context == {"v": 1}
    assert spec.variables is None
    assert spec.build_context is None
    assert agent.instructions is processed


def test_from_remote_uses_env_token_lookup(monkeypatch):
    monkeypatch.setenv("REMOTE_TOKEN", "token-value")

    runner = Runner.from_remote(
        "ws://example/ws",
        name="Remote Agent",
        token_env_var="REMOTE_TOKEN",
        welcome_text="Hello",
        context={"existing": 1},
        variables={"v": 2},
    )

    spec = runner.spec

    assert spec.remote_ws_url == "ws://example/ws"
    assert spec.remote_auth_token_key == "REMOTE_TOKEN"
    assert spec.client_storage_prefix == "remote_agent."
    assert spec.context == {"existing": 1}
    assert spec.variables == {"v": 2}


def test_run_raises_for_unknown_backend(monkeypatch):
    runner = Runner(spec=make_spec())

    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_backend", lambda mode: None
    )

    with pytest.raises(ValueError):
        runner.run("missing")


def test_run_invokes_backend_with_kwargs(monkeypatch):
    runner = Runner(spec=make_spec())
    calls = []

    class Backend:
        def run(self, spec, **kwargs):
            calls.append((spec, kwargs))

    backend = Backend()

    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_backend",
        lambda mode: (lambda: backend) if mode == "cli" else None,
    )

    runner.run("cli", extra="value")

    assert calls == [(runner.spec, {"extra": "value"})]


def test_main_cli_mode_invokes_run_with_cli_options(monkeypatch):
    runner = Runner(spec=make_spec())

    parsed = types.SimpleNamespace(
        mode="cli",
        debug=True,
        resume=False,
        session_id="session-1",
        host="",
        port=0,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
    )

    debug_flags = []
    calls = []

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: parsed)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init",
        lambda cli_flag: debug_flags.append(cli_flag),
    )
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, mode, **kwargs: calls.append((mode, kwargs)),
    )

    runner.main()

    assert debug_flags == [True]
    assert calls == [("cli", {"args": parsed, "session_id": "session-1"})]


def test_main_server_mode_adds_server_kwargs(monkeypatch):
    runner = Runner(spec=make_spec())

    parsed = types.SimpleNamespace(
        mode="server",
        debug=False,
        resume=False,
        session_id=None,
        host="127.0.0.1",
        port=9000,
        auth_token="secret",
        reload=True,
        approvals=None,
        on_reject=None,
    )

    calls = []

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: parsed)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, mode, **kwargs: calls.append((mode, kwargs)),
    )

    runner.main()

    assert calls == [
        (
            "server",
            {
                "args": parsed,
                "host": "127.0.0.1",
                "port": 9000,
                "auth_token": "secret",
                "reload": True,
            },
        )
    ]


def test_main_allows_resume_for_bubbletea(monkeypatch):
    runner = Runner(spec=make_spec())

    parsed = types.SimpleNamespace(
        mode="bubbletea",
        debug=False,
        resume=True,
        session_id=None,
        host="",
        port=0,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
    )

    calls = []

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: parsed)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, mode, **kwargs: calls.append((mode, kwargs)),
    )

    runner.main()

    assert calls == [("bubbletea", {"args": parsed, "resume": True})]


def test_main_rejects_resume_for_unsupported_mode(monkeypatch):
    runner = Runner(spec=make_spec())

    parsed = types.SimpleNamespace(
        mode="cli",
        debug=False,
        resume=True,
        session_id=None,
        host="",
        port=0,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
    )

    monkeypatch.setattr("argparse.ArgumentParser.parse_args", lambda self: parsed)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, mode, **kwargs: pytest.fail("run should not be called"),
    )

    with pytest.raises(SystemExit) as exc:
        runner.main()

    assert exc.value.code == 2
