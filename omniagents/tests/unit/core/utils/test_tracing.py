import logging
import sys
import types

import pytest

from omniagents.core.tracing import local, setup_tracing


@pytest.fixture
def init_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(local, "init", lambda **kwargs: calls.append(kwargs))
    return calls


def test_setup_tracing_no_config_disables(init_calls, monkeypatch, caplog):
    """No tracing config means no tracing: nothing recorded, nothing exported."""
    calls = []
    stub_agents = types.ModuleType("agents")
    stub_agents.set_trace_processors = calls.append
    monkeypatch.setitem(sys.modules, "agents", stub_agents)
    caplog.set_level(logging.INFO)
    setup_tracing(None)
    setup_tracing({"project": "proj", "name": "trace"})
    assert init_calls == []
    assert calls == [[], []]


def test_setup_tracing_openai_provider_enables(monkeypatch, caplog):
    """OpenAI provider enables OpenAI tracing (explicit opt-in)."""
    calls = []
    stub_agents = types.ModuleType("agents")

    def set_trace_processors(value):
        calls.append(value)

    stub_agents.set_trace_processors = set_trace_processors
    monkeypatch.setitem(sys.modules, "agents", stub_agents)
    caplog.set_level(logging.INFO)
    setup_tracing({"provider": "openai"})
    # OpenAI provider should NOT call set_trace_processors (leave SDK default enabled)
    assert calls == []
    assert "OpenAI tracing enabled" in caplog.text


def test_setup_tracing_studio_passes_project_and_name(init_calls, caplog):
    caplog.set_level(logging.INFO)
    setup_tracing({"provider": "studio", "project": "proj", "name": "trace"})
    assert init_calls[0]["project"] == "proj"
    assert init_calls[0]["name"] == "trace"
    assert "Local tracing enabled" in caplog.text


def test_setup_tracing_init_failure_disables(monkeypatch, caplog, capsys):
    """A failing local init disables tracing instead of falling back to OpenAI."""
    calls = []
    stub_agents = types.ModuleType("agents")
    stub_agents.set_trace_processors = calls.append
    monkeypatch.setitem(sys.modules, "agents", stub_agents)

    def failing_init(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(local, "init", failing_init)
    setup_tracing({"provider": "studio", "project": "proj"})
    captured = capsys.readouterr()
    assert calls == [[]]
    assert "Error initializing local tracing: boom" in captured.out
    assert "no data sent to OpenAI" in captured.out


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
    assert captured.out == ""


def test_setup_tracing_unknown_provider_warns(caplog, capsys):
    caplog.set_level(logging.WARNING)
    setup_tracing({"provider": "mystery"})
    captured = capsys.readouterr()
    assert "Unknown tracing provider" in caplog.text
    assert "Unknown tracing provider" in captured.out
