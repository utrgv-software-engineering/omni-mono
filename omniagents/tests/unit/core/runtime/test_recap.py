"""Tests for the session recap orchestration and summarizer seam."""

import asyncio
from textwrap import dedent

import pytest

from omniagents.core.product import bootstrap_product, reset_product_info
from omniagents.core.runtime.recap import (
    generate_and_broadcast_recap,
    generate_recap,
    recap_summarizer,
    set_recap_summarizer,
)


@pytest.fixture(autouse=True)
def _reset_seam():
    set_recap_summarizer(None)
    reset_product_info()
    yield
    set_recap_summarizer(None)
    reset_product_info()


def install_summarizer_product(tmp_path, *, agent_key=None, slug="conv_proj"):
    """Bootstrap a product whose project optionally ships *agent_key*."""
    if agent_key:
        agent_yaml = tmp_path / "agents" / agent_key / "agent.yml"
        agent_yaml.parent.mkdir(parents=True, exist_ok=True)
        agent_yaml.write_text(
            dedent(
                f"""
                name: {agent_key}
                instructions: "Project-owned summarizer prompt."
                tools: []
                """
            ),
            encoding="utf-8",
        )
    manifest = tmp_path / "project.yml"
    manifest.write_text(
        f"name: {slug}\nproduct:\n  slug: {slug}\n", encoding="utf-8"
    )
    bootstrap_product(manifest)
    return manifest


class _FakeSession:
    def __init__(self, history=None):
        self.id = "sess-1"
        self.history = history or []


class _FakeService:
    def __init__(self):
        self.client_calls = []

    async def request_client_function(self, function, args=None, **kwargs):
        self.client_calls.append((function, args))


def test_empty_session_short_circuits():
    set_recap_summarizer(None)
    text = asyncio.run(generate_recap(_FakeService(), _FakeSession()))
    assert "Nothing to recap yet" in text


def test_missing_summarizer_reports_unavailable():
    set_recap_summarizer(None)
    session = _FakeSession([{"role": "user", "content": "hi"}])
    text = asyncio.run(generate_recap(_FakeService(), session))
    assert "no recap summarizer" in text


def test_summarizer_receives_transcript_and_broadcasts():
    captured = {}

    async def summarizer(prompt, *, service, session):
        captured["prompt"] = prompt
        captured["session"] = session
        return "  - all done\n"

    set_recap_summarizer(summarizer)
    session = _FakeSession([{"role": "user", "content": "fix the bug"}])
    svc = _FakeService()

    text = asyncio.run(generate_and_broadcast_recap(svc, session))

    assert text == "- all done"
    assert "fix the bug" in captured["prompt"]
    assert captured["session"] is session
    assert svc.client_calls == [("ui.recap", {"text": "- all done"})]


# ---------------------------------------------------------------------------
# Conventional summarizer (project ships a `recapper` agent)
# ---------------------------------------------------------------------------


class _ConvSession:
    def __init__(self):
        self.id = "sess-c"
        self.history = [{"role": "user", "content": "hi"}]
        self.active_model = None
        self.model_config = {"provider": "openai", "model": "gpt-conv", "api_key": "k"}


class _ConvService:
    spec = None


def test_conventional_summarizer_runs_recapper_under_its_own_instructions(
    monkeypatch, tmp_path
):
    install_summarizer_product(tmp_path, agent_key="recapper")

    captured = {}

    async def fake_run_project_agent_once(agent_key, prompt, **kwargs):
        captured["agent_key"] = agent_key
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "recap text"

    monkeypatch.setattr(
        "omniagents.core.runtime.ephemeral.run_project_agent_once",
        fake_run_project_agent_once,
    )

    summarizer = recap_summarizer()
    assert summarizer is not None
    session = _ConvSession()
    text = asyncio.run(summarizer("transcript", service=_ConvService(), session=session))

    assert text == "recap text"
    assert captured["agent_key"] == "recapper"
    assert captured["prompt"] == "transcript"
    # The agent's own instructions.md is the prompt — never overridden.
    assert "instructions" not in captured
    # Model resolved through the utility-run preference order.
    assert captured["model"] == "gpt-conv"
    assert captured["runtime"]["provider"] == "openai"
    assert captured["session"] is session


def test_explicit_install_wins_over_convention(tmp_path):
    install_summarizer_product(tmp_path, agent_key="recapper")

    async def summarizer(prompt, *, service, session):
        return "explicit"

    set_recap_summarizer(summarizer)
    assert recap_summarizer() is summarizer
    # Uninstalling restores the conventional fallback.
    set_recap_summarizer(None)
    assert recap_summarizer() is not None
    assert recap_summarizer() is not summarizer


def test_no_conventional_agent_behaves_as_uninstalled(tmp_path):
    install_summarizer_product(tmp_path, agent_key=None)

    assert recap_summarizer() is None
    session = _FakeSession([{"role": "user", "content": "hi"}])
    text = asyncio.run(generate_recap(_FakeService(), session))
    assert "no recap summarizer" in text
