import builtins
import logging
import sys
import types

import pytest

from omniagents.core.tracing import setup_tracing


def test_setup_tracing_default_provider_logs(caplog):
    caplog.set_level(logging.DEBUG)
    setup_tracing({"provider": "default"})
    assert "Using default OpenAI tracing" in caplog.text


def test_setup_tracing_studio_initializes_stub(monkeypatch, caplog):
    calls = []
    stub_tracing = types.ModuleType("omniagents.studio.tracing")

    def fake_init(**kwargs):
        calls.append(kwargs)

    stub_tracing.init = fake_init
    stub_studio = types.ModuleType("omniagents.studio")
    stub_studio.tracing = stub_tracing
    monkeypatch.setitem(sys.modules, "omniagents.studio", stub_studio)
    monkeypatch.setitem(sys.modules, "omniagents.studio.tracing", stub_tracing)
    caplog.set_level(logging.INFO)
    setup_tracing({"provider": "studio", "project": "proj", "name": "trace"})
    assert calls
    assert calls[0]["project"] == "proj"
    assert "Studio tracing enabled" in caplog.text


def test_setup_tracing_agentio_fallback(monkeypatch):
    calls = []
    stub_agentio = types.ModuleType("agentio")

    def legacy_init(**kwargs):
        calls.append(kwargs)

    stub_agentio.init = legacy_init
    monkeypatch.setitem(sys.modules, "agentio", stub_agentio)
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "omniagents.studio":
            raise ImportError("missing studio")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    setup_tracing({"provider": "agentio", "project": "legacy"})
    assert calls
    assert calls[0]["project"] == "legacy"


def test_setup_tracing_warns_when_studio_missing(monkeypatch, caplog, capsys):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "omniagents.studio":
            raise ImportError("missing studio")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    caplog.set_level(logging.WARNING)
    setup_tracing({"provider": "studio"})
    captured = capsys.readouterr()
    assert "Studio tracing not available" in caplog.text
    assert "OmniAgents Studio tracing is not installed" in captured.out


def test_setup_tracing_none_provider_disables(monkeypatch, caplog, capsys):
    calls = []
    stub_agents = types.ModuleType("agents")

    def set_trace_processors(value):
        calls.append(value)

    stub_agents.set_trace_processors = set_trace_processors
    monkeypatch.setitem(sys.modules, "agents", stub_agents)
    caplog.set_level(logging.INFO)
    setup_tracing({"provider": "none"})
    captured = capsys.readouterr()
    assert calls == [[]]
    assert "Tracing disabled" in caplog.text
    assert "Tracing disabled" in captured.out


def test_setup_tracing_unknown_provider_warns(caplog, capsys):
    caplog.set_level(logging.WARNING)
    setup_tracing({"provider": "mystery"})
    captured = capsys.readouterr()
    assert "Unknown tracing provider" in caplog.text
    assert "Unknown tracing provider" in captured.out
