import argparse
import asyncio
import types

import pytest

from omniagents.core.agents.specs import AgentSpec
from omniagents.core.runtime.runner import Runner


def make_spec(**overrides):
    base = {
        "name": overrides.pop("name", "Agent"),
        "welcome_text": "Welcome",
        "get_agent_instructions": overrides.pop("get_agent_instructions", lambda: ""),
        "build_agent": overrides.pop("build_agent", None),
    }
    base.update(overrides)
    return AgentSpec(**base)


def test_from_agent_configures_safe_options_and_server_functions(monkeypatch):
    processed = object()

    def fake_process(instructions):
        assert instructions == "hello"
        return processed

    captured = {}

    def fake_registrar(functions):
        captured["functions"] = functions

        def registrar(target):
            captured["target"] = target

        return registrar

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
    assert spec.use_safe_agent is True
    options = spec.safe_agent_options
    assert options.skip_approvals is True
    assert options.halt_on_rejection is False
    assert options.safe_tool_names == ["t"]
    assert options.safe_tool_patterns == ["tool-.*"]
    assert spec.context is built_context
    assert spec.variables is variables
    assert spec.build_context is build_context
    assert spec.max_turns == 5
    assert spec.agent_slug == "helper_bot"
    assert captured["functions"] == ["fn"]
    assert spec.register_server_functions is not None

    spec.register_server_functions("service")
    assert captured["target"] == "service"

    built_agent = asyncio.run(spec.build_agent({}, None, spec))
    assert built_agent is agent


def test_from_agent_with_voice_agent_sets_voice_spec(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        lambda value: value,
    )

    class DummyAgent:
        def __init__(self, name):
            self.name = name
            self.instructions = f"{name}-instructions"
            self.tools = [f"{name}-tool"]
            self.model = "gpt"

    main_agent = DummyAgent("Main Agent")
    voice_agent = DummyAgent("Voice Agent")

    runner = Runner.from_agent(
        main_agent,
        voice_agent=voice_agent,
        safe=True,
        safe_tool_names=["common"],
    )

    spec = runner.spec
    voice_spec = spec.voice_spec
    assert voice_spec is not None
    assert voice_spec is not spec
    assert voice_spec.name == "Voice Agent"
    assert voice_spec.safe_agent_options.safe_tool_names == ["common"]
    assert voice_spec.available_tools == ["Voice Agent-tool"]
    assert voice_spec.context is spec.context


def test_from_agent_without_build_context_uses_variables_as_context(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        lambda value: value,
    )

    class DummyAgent:
        def __init__(self):
            self.name = "Solo"
            self.instructions = "instructions"
            self.tools = []
            self.model = "model"

    agent = DummyAgent()
    variables = {"x": 1}

    runner = Runner.from_agent(agent, variables=variables)
    spec = runner.spec

    assert spec.context is variables
    assert spec.variables is None
    assert spec.build_context is None


def test_run_notebook_enables_realtime_for_voice_spec(monkeypatch):
    class DummyIFrame:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class DummyHTML:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    display_calls = []

    ipy_display = types.SimpleNamespace(
        IFrame=DummyIFrame,
        HTML=DummyHTML,
        display=lambda x: display_calls.append(x),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "IPython.display",
        ipy_display,
    )

    class DummyStaticFiles:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setitem(__import__("sys").modules, "fastapi.staticfiles", types.SimpleNamespace(StaticFiles=DummyStaticFiles))
    monkeypatch.setitem(__import__("sys").modules, "uvicorn", types.SimpleNamespace(run=lambda **kwargs: None))

    class DummyApp:
        def add_middleware(self, *args, **kwargs):
            return None

        def mount(self, *args, **kwargs):
            return None

    monkeypatch.setattr("omniagents.backends.server.app.build_app", lambda **kwargs: DummyApp())
    monkeypatch.setattr(Runner, "_resolve_web_ui_dist", lambda self: None)
    monkeypatch.setattr(Runner, "_find_available_port", lambda *args, **kwargs: 8123)

    class DummySocket:
        def __init__(self, *args, **kwargs):
            self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def settimeout(self, *args, **kwargs):
            return None

        def connect(self, *args, **kwargs):
            return None

        def bind(self, *args, **kwargs):
            return None

    monkeypatch.setattr("socket.socket", lambda *args, **kwargs: DummySocket())
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)

    class DummyThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr("omniagents.core.runtime.runner.Thread", DummyThread)

    spec = make_spec()
    spec.voice_spec = make_spec(name="Voice")
    assert spec.voice_spec.realtime_mode is False

    runner = Runner(spec=spec)
    runner.run_notebook()

    assert runner.spec.voice_spec.realtime_mode is True
    assert runner.spec.voice_spec.realtime_settings is not None
    assert "audio" in (runner.spec.voice_spec.realtime_settings.modalities or [])
    assert len(display_calls) == 2


def test_from_remote_uses_env_token(monkeypatch):
    monkeypatch.setenv("REMOTE_TOKEN", "token-value")

    runner = Runner.from_remote(
        "ws://example/ws",
        name="Remote Agent",
        token=None,
        token_env_var="REMOTE_TOKEN",
        welcome_text="Hello",
        context={"existing": 1},
        variables={"v": 2},
    )

    spec = runner.spec
    assert spec.remote_ws_url == "ws://example/ws"
    assert spec.remote_auth_token_key == "REMOTE_TOKEN"
    assert spec.agent_slug == "remote_agent"
    assert spec.context == {"existing": 1}
    assert spec.variables == {"v": 2}


def test_run_invokes_backend_with_spec(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    captured = {}

    class DummyBackend:
        def run(self, spec, **kwargs):
            captured["spec"] = spec
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_backend",
        lambda mode: DummyBackend,
    )

    runner.run("server", extra=True)

    assert captured["spec"] is spec
    assert captured["kwargs"] == {"extra": True}


def test_run_unknown_backend_raises(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)

    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_backend", lambda mode: None
    )

    with pytest.raises(ValueError):
        runner.run("missing")


def test_main_server_mode_invokes_run_and_debug(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="server",
        debug=True,
        resume=False,
        session_id="sess",
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
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

    assert called["mode"] == "server"
    # Check that args are passed through
    assert called["kwargs"]["args"] is args
    assert called["debug"] is True


def test_main_server_mode_adds_server_kwargs(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
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
    called = {}

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )
    monkeypatch.setattr(
        Runner,
        "run",
        lambda self, mode, **kwargs: called.setdefault("call", (mode, kwargs)),
    )

    runner.main()

    mode, kwargs = called["call"]
    assert mode == "server"
    assert kwargs == {
        "args": args,
        "host": "127.0.0.1",
        "port": 9000,
        "auth_token": "secret",
        "reload": True,
    }


def test_main_resume_with_web_mode_works(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="web",
        debug=False,
        resume=True,
        session_id="sess123",
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
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

    assert called["mode"] == "web"
    assert called["kwargs"]["resume"] is True
    assert called["kwargs"]["args"] is args


def test_main_resume_with_ink_mode_sets_flag(monkeypatch):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="ink",
        debug=False,
        resume=True,
        session_id="ink_sess",
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
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

    assert called["mode"] == "ink"
    assert called["kwargs"]["resume"] is True
    assert called["kwargs"]["args"] is args


def test_runner_requires_spec_or_config():
    with pytest.raises(ValueError):
        Runner()


def test_runner_loads_spec_from_config_and_sets_tracing(monkeypatch):
    spec = types.SimpleNamespace(tracing_config={"exporter": "noop"})
    captured = {}

    monkeypatch.setattr(
        "omniagents.core.runtime.runner.load_agent_spec_from_yaml",
        lambda path: spec,
    )
    monkeypatch.setattr(
        "omniagents.core.tracing.setup_tracing",
        lambda config: captured.setdefault("config", config),
    )

    runner = Runner(config_path="agent.yaml")

    assert runner.spec is spec
    assert captured["config"] == {"exporter": "noop"}


def test_from_agent_server_functions_failure(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.utils.jinja_instructions.process_instructions",
        lambda value: value,
    )
    captured = {}

    def failing_registrar(functions):
        captured["functions"] = functions
        raise RuntimeError("fail")

    monkeypatch.setattr(
        "omniagents.core.server_functions.discovery.build_server_functions_registrar",
        failing_registrar,
    )

    class DummyAgent:
        def __init__(self):
            self.name = "Worker"
            self.instructions = "do"
            self.tools = []
            self.model = None

    agent = DummyAgent()

    runner = Runner.from_agent(agent, server_functions=["fn"])

    assert captured["functions"] == ["fn"]
    assert getattr(runner.spec, "register_server_functions", None) is None


def test_from_remote_uses_direct_token(monkeypatch):
    # Mock setup_tracing to avoid side effects during test
    monkeypatch.setattr(
        "omniagents.core.tracing.setup_tracing",
        lambda config: None,
    )
    monkeypatch.setattr("os.getenv", lambda key: pytest.fail("should not read env"))

    runner = Runner.from_remote(
        "ws://example/ws",
        name="Remote Agent",
        token="direct",
        token_env_var="REMOTE_TOKEN",
    )

    assert runner.spec.remote_ws_url == "ws://example/ws"
    assert runner.spec.remote_auth_token_key == "REMOTE_TOKEN"


def test_runner_main_handles_generic_exception(monkeypatch, capsys):
    spec = make_spec()
    runner = Runner(spec=spec)
    args = types.SimpleNamespace(
        mode="server",
        debug=False,
        resume=False,
        session_id=None,
        host="0.0.0.0",
        port=8000,
        auth_token=None,
        reload=False,
        approvals=None,
        on_reject=None,
    )

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(
        "omniagents.core.runtime.runner.Debug.init", lambda cli_flag: None
    )

    def failing_run(self, mode, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(Runner, "run", failing_run)
    monkeypatch.setattr("traceback.print_exc", lambda: None)

    with pytest.raises(SystemExit) as exc:
        runner.main()

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "Error: boom" in output
