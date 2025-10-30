import sys
import types

import pytest

import omniagents.backends.bubbletea as bubbletea_module
from omniagents.backends.bubbletea import BubbleTeaBackend
from omniagents.core.agents.specs import AgentSpec


@pytest.fixture
def base_spec():
    return AgentSpec(
        name="Test",
        header_title="Header",
        page_title="Page",
        welcome_text="Welcome",
        client_storage_prefix="test.",
        get_agent_instructions=lambda: "",
        build_agent=None,
    )


def test_run_includes_initial_message_flag(monkeypatch, tmp_path, base_spec):
    backend = BubbleTeaBackend()

    binary_path = tmp_path / "omni-tui"
    binary_path.write_text("")

    monkeypatch.setattr(
        BubbleTeaBackend,
        "_find_binary",
        lambda self: binary_path,
    )
    monkeypatch.setattr(
        BubbleTeaBackend,
        "_user_cache_dir",
        lambda self: tmp_path,
    )
    monkeypatch.setattr(
        BubbleTeaBackend,
        "_compute_source_hash",
        lambda self: None,
    )
    monkeypatch.setattr(
        BubbleTeaBackend,
        "_read_cached_hash",
        lambda self, _: None,
    )
    monkeypatch.setattr(
        BubbleTeaBackend,
        "_build_binary",
        lambda self, debug=False: binary_path,
    )
    monkeypatch.setattr(
        bubbletea_module,
        "apply_cli_overrides",
        lambda spec, args: None,
    )

    fake_server_app = types.ModuleType("omniagents.backends.server.app")
    fake_server_app.build_app = lambda **kwargs: object()
    monkeypatch.setitem(
        sys.modules,
        "omniagents.backends.server.app",
        fake_server_app,
    )

    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    class DummySocket:
        _port = 43210

        def __init__(self, *_, **__):
            self._closed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

        def bind(self, *_):
            pass

        def getsockname(self):
            return ("127.0.0.1", self._port)

        def settimeout(self, *_):
            pass

        def connect(self, *_):
            pass

        def close(self):
            self._closed = True

    monkeypatch.setattr(
        bubbletea_module.socket,
        "socket",
        lambda *args, **kwargs: DummySocket(),
    )

    commands = []

    class StubPopen:
        def __init__(self, cmd, **_):
            commands.append(cmd)

        def wait(self):
            return 0

    monkeypatch.setattr(
        bubbletea_module.subprocess,
        "Popen",
        StubPopen,
    )

    monkeypatch.setenv("OMNI_TUI_SILENT", "1")

    backend.run(
        base_spec,
        initial_prompt="hello there",
        args=types.SimpleNamespace(),
    )

    assert commands
    cmd = commands[0]
    assert "--initial-message" in cmd
    assert "hello there" in cmd
