import errno
import os
import sys
import types

import pytest

import omniagents.backends.bubbletea as bubbletea_module
from omniagents.backends.bubbletea import BubbleTeaBackend
from omniagents.core.agents.specs import AgentSpec


@pytest.fixture
def base_spec() -> AgentSpec:
    return AgentSpec(
        name="Test",
        header_title="Header",
        page_title="Page",
        welcome_text="Welcome",
        client_storage_prefix="test.",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )


@pytest.fixture
def runtime_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    backend = BubbleTeaBackend()
    binary_path = tmp_path / backend._binary_filename()
    binary_path.write_text("")
    state = {
        "backend": backend,
        "binary_path": binary_path,
        "commands": [],
        "popen_side_effects": [],
        "wait_results": [0, 0, 0],
        "cache_dir": tmp_path,
    }

    def fake_apply(spec, args):
        return None

    class DummySocket:
        port = 43210

        def __init__(self, *_, **__):
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

        def bind(self, *_):
            pass

        def getsockname(self):
            return ("127.0.0.1", self.port)

        def settimeout(self, *_):
            pass

        def connect(self, *_):
            pass

        def close(self):
            self.closed = True

    class StubPopen:
        def __init__(self, cmd, **_):
            state["commands"].append(cmd)
            if state["popen_side_effects"]:
                effect = state["popen_side_effects"].pop(0)
                if isinstance(effect, Exception):
                    raise effect
            if state["wait_results"]:
                self._return = state["wait_results"].pop(0)
            else:
                self._return = 0

        def wait(self):
            return self._return

    fake_server_app = types.ModuleType("omniagents.backends.server.app")
    fake_server_app.build_app = lambda **kwargs: object()
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None

    monkeypatch.setattr(BubbleTeaBackend, "_user_cache_dir", lambda self: tmp_path)
    monkeypatch.setattr(BubbleTeaBackend, "_compute_source_hash", lambda self: None)
    monkeypatch.setattr(BubbleTeaBackend, "_read_cached_hash", lambda self, _: None)
    monkeypatch.setattr(
        BubbleTeaBackend,
        "_build_binary",
        lambda self, debug=False: state["binary_path"],
    )
    monkeypatch.setattr(
        BubbleTeaBackend, "_find_binary", lambda self: state["binary_path"]
    )
    monkeypatch.setattr(bubbletea_module, "apply_cli_overrides", fake_apply)
    monkeypatch.setattr(
        bubbletea_module.socket, "socket", lambda *args, **kwargs: DummySocket()
    )
    monkeypatch.setattr(bubbletea_module.subprocess, "Popen", StubPopen)
    monkeypatch.setitem(sys.modules, "omniagents.backends.server.app", fake_server_app)
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setenv("OMNI_TUI_SILENT", "1")
    monkeypatch.delenv("OMNI_TUI_AUTO_BUILD", raising=False)

    return state


def test_run_includes_initial_message_flag(runtime_env, base_spec):
    backend = runtime_env["backend"]
    backend.run(base_spec, initial_prompt="hello there", args=types.SimpleNamespace())
    assert runtime_env["commands"]
    cmd = runtime_env["commands"][0]
    assert "--initial-message" in cmd
    assert "hello there" in cmd


def test_run_triggers_auto_build_when_missing(
    runtime_env, base_spec, monkeypatch, tmp_path
):
    backend = runtime_env["backend"]
    built_path = tmp_path / "rebuilt" / backend._binary_filename()
    built_path.parent.mkdir(parents=True, exist_ok=True)
    built_path.write_text("rebuilt")
    calls = []

    monkeypatch.setattr(BubbleTeaBackend, "_find_binary", lambda self: None)

    def fake_build(self, debug=False):
        calls.append(debug)
        return built_path

    monkeypatch.setattr(BubbleTeaBackend, "_build_binary", fake_build)
    backend.run(base_spec, args=types.SimpleNamespace())
    assert calls
    assert runtime_env["commands"][0][0] == str(built_path)


def test_run_rebuilds_cached_binary_on_hash_mismatch(
    runtime_env, base_spec, monkeypatch
):
    backend = runtime_env["backend"]
    binary_path = runtime_env["binary_path"]
    binary_path.write_text("stale")
    calls = []

    monkeypatch.setattr(BubbleTeaBackend, "_find_binary", lambda self: binary_path)
    monkeypatch.setattr(BubbleTeaBackend, "_compute_source_hash", lambda self: "new")
    monkeypatch.setattr(BubbleTeaBackend, "_read_cached_hash", lambda self, _: "old")

    def fake_build(self, debug=False):
        calls.append(debug)
        binary_path.write_text("fresh")
        return binary_path

    monkeypatch.setattr(BubbleTeaBackend, "_build_binary", fake_build)
    backend.run(base_spec, args=types.SimpleNamespace())
    assert calls
    assert binary_path.read_text() == "fresh"


@pytest.mark.parametrize(
    "error_factory",
    [lambda: FileNotFoundError(), lambda: OSError(errno.ENOEXEC, "bad")],
)
def test_run_retries_build_when_launch_fails(
    runtime_env, base_spec, monkeypatch, error_factory
):
    backend = runtime_env["backend"]
    binary_path = runtime_env["binary_path"]
    binary_path.write_text("old")
    runtime_env["popen_side_effects"].append(error_factory())
    runtime_env["wait_results"] = [0, 0, 0]
    calls = []

    def fake_build(self, debug=False):
        calls.append(debug)
        return binary_path

    monkeypatch.setattr(BubbleTeaBackend, "_build_binary", fake_build)
    backend.run(base_spec, args=types.SimpleNamespace())
    assert calls
    assert len(runtime_env["commands"]) >= 2


def test_run_writes_log_and_optional_flags(runtime_env, base_spec, monkeypatch):
    backend = runtime_env["backend"]
    spec = base_spec
    spec.use_safe_agent = True
    monkeypatch.setenv("OMNI_TUI_SILENT", "0")
    backend.run(
        spec,
        args=types.SimpleNamespace(),
        debug=True,
        auth_token="secret",
        session_id="session",
        resume=True,
    )
    cmd = runtime_env["commands"][0]
    assert "--token" in cmd
    assert "secret" in cmd
    assert "--safe-mode" in cmd
    assert "--resume" in cmd
    assert "--debug" in cmd
    log_file = runtime_env["cache_dir"] / "tui-server.log"
    assert log_file.exists()


def test_run_exits_when_auto_build_disabled(runtime_env, base_spec, monkeypatch):
    backend = runtime_env["backend"]

    def fail_build(self, debug=False):
        raise AssertionError

    monkeypatch.setattr(BubbleTeaBackend, "_find_binary", lambda self: None)
    monkeypatch.setattr(BubbleTeaBackend, "_build_binary", fail_build)
    monkeypatch.setenv("OMNI_TUI_AUTO_BUILD", "0")
    with pytest.raises(SystemExit) as exc:
        backend.run(base_spec, args=types.SimpleNamespace())
    assert exc.value.code == 1
    assert runtime_env["commands"] == []
