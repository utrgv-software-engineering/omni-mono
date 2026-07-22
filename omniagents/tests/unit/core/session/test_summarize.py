"""Tests for offline session summarization: seam, orchestration, prompt."""

import asyncio
import json

import pytest

from omniagents.core.session import history_db
from omniagents.core.session.summarize import (
    DEFAULT_SESSION_ENVELOPE,
    build_session_summary_prompt,
    session_summarizer,
    set_session_summarizer,
    summarize_session,
    trim_history_tail,
)


@pytest.fixture(autouse=True)
def _reset_seam():
    original = session_summarizer()
    yield
    set_session_summarizer(original)


def _seed_session(session_id, contents):
    history_db.register_session(session_id, project_slug=None, agent_slug=None)
    for content in contents:
        history_db.append_message(
            session_id,
            {"role": "user", "content": content},
            project_slug=None,
            agent_slug=None,
        )


# ---------------------------------------------------------------------------
# Summarizer seam
# ---------------------------------------------------------------------------


def test_session_summarizer_seam_roundtrip():
    async def summarizer(prompt, *, model, project, agent, session_id):
        return "{}"

    set_session_summarizer(summarizer)
    assert session_summarizer() is summarizer
    set_session_summarizer(None)
    assert session_summarizer() is None


def test_summarize_session_without_summarizer_raises():
    set_session_summarizer(None)
    with pytest.raises(RuntimeError, match="set_session_summarizer"):
        asyncio.run(summarize_session("whatever"))


def test_summarize_session_missing_session_raises():
    async def summarizer(prompt, **kwargs):
        return "{}"

    set_session_summarizer(summarizer)
    with pytest.raises(LookupError, match="Session not found: ghost"):
        asyncio.run(summarize_session("ghost"))


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def test_summarize_session_envelope_and_identity_keys():
    _seed_session("sum-1", ["first message", "second message"])
    seen = {}

    async def summarizer(prompt, *, model, project, agent, session_id):
        seen["prompt"] = prompt
        seen["kwargs"] = {"model": model, "project": project, "agent": agent}
        return json.dumps({"summary": "done", "extra": "kept"})

    set_session_summarizer(summarizer)

    out = asyncio.run(summarize_session("sum-1", model="ref-1"))

    assert out["summary"] == "done"
    assert out["extra"] == "kept"
    assert out["session_id"] == "sum-1"
    assert out["message_count"] == 2
    for key, default in DEFAULT_SESSION_ENVELOPE.items():
        if key != "summary":  # summarizer already filled summary
            assert out[key] == default
    assert seen["kwargs"]["model"] == "ref-1"
    assert "first message" in seen["prompt"]
    assert "second message" in seen["prompt"]


def test_summarize_session_envelope_defaults_are_copies():
    _seed_session("sum-2", ["hello"])

    async def summarizer(prompt, **kwargs):
        return "{}"

    set_session_summarizer(summarizer)

    out = asyncio.run(summarize_session("sum-2"))
    out["decisions"].append("mutate")

    assert DEFAULT_SESSION_ENVELOPE["decisions"] == []


def test_summarize_session_non_json_output_wrapped_as_raw():
    _seed_session("sum-3", ["hello"])

    async def summarizer(prompt, **kwargs):
        return "not json at all"

    set_session_summarizer(summarizer)

    out = asyncio.run(summarize_session("sum-3"))

    assert out["raw"] == "not json at all"
    assert out["session_id"] == "sum-3"
    assert out["decisions"] == []


def test_summarize_session_tail_trims_window():
    _seed_session("sum-4", [f"msg-{i}" for i in range(5)])

    async def summarizer(prompt, **kwargs):
        return json.dumps({"summary": prompt})

    set_session_summarizer(summarizer)

    out = asyncio.run(summarize_session("sum-4", tail=2))

    # Metadata still mentions first/last messages; only the transcript
    # section reflects the trimmed window.
    transcript = out["summary"].split("Transcript (chronological raw JSON items):")[1]
    assert "msg-4" in transcript
    assert "msg-3" in transcript
    assert "msg-0" not in transcript


def test_trim_history_tail_semantics():
    items = [{"i": i} for i in range(4)]
    assert trim_history_tail(items, tail=None) == items
    assert trim_history_tail(items, tail=10) == items
    assert trim_history_tail(items, tail=2) == items[-2:]
    assert trim_history_tail(items, tail=0) == []
    assert trim_history_tail([], tail=3) == []


# ---------------------------------------------------------------------------
# Prompt shape
# ---------------------------------------------------------------------------


def test_build_session_summary_prompt_shape():
    prompt = build_session_summary_prompt(
        [{"role": "user", "content": "hi"}], session_meta={"id": "s1"}
    )
    assert prompt.startswith("Session metadata:")
    assert '"id": "s1"' in prompt
    assert "Transcript (chronological raw JSON items):" in prompt
    assert '"content": "hi"' in prompt


# ---------------------------------------------------------------------------
# Conventional summarizer + manifest envelope
# ---------------------------------------------------------------------------


def _install_session_product(tmp_path, *, with_agent=True, sessions_block=""):
    from textwrap import dedent

    from omniagents.core.product import bootstrap_product

    if with_agent:
        agent_yaml = tmp_path / "agents" / "session_summarizer" / "agent.yml"
        agent_yaml.parent.mkdir(parents=True, exist_ok=True)
        agent_yaml.write_text(
            dedent(
                """
                name: session_summarizer
                instructions: Emit the project JSON schema.
                tools: []
                """
            ),
            encoding="utf-8",
        )
    manifest = tmp_path / "project.yml"
    manifest.write_text(
        "name: sess_proj\nproduct:\n  slug: sess_proj\n  prog: sessprog\n"
        + sessions_block,
        encoding="utf-8",
    )
    bootstrap_product(manifest)
    return manifest


@pytest.fixture()
def _fresh_identity():
    from omniagents.core.product import reset_product_info

    reset_product_info()
    yield
    reset_product_info()


def test_conventional_session_summarizer_runs_named_agent(
    _fresh_identity, monkeypatch, tmp_path
):
    _install_session_product(tmp_path, with_agent=True)
    set_session_summarizer(None)

    captured = {}

    async def fake_run_project_agent_once(agent_key, prompt, **kwargs):
        captured["agent_key"] = agent_key
        captured["prompt"] = prompt
        captured.update(kwargs)
        return json.dumps({"summary": "conventional"})

    monkeypatch.setattr(
        "omniagents.core.runtime.ephemeral.run_project_agent_once",
        fake_run_project_agent_once,
    )
    monkeypatch.setattr(
        "omniagents.core.models.resolve_model_runtime",
        lambda model_ref=None, **_kw: (
            "gpt-conv",
            {"provider": "openai", "model": "gpt-conv", "api_key": "k"},
        ),
    )
    provider = object()
    monkeypatch.setattr(
        "omniagents.core.models.provider_from_runtime",
        lambda runtime: (provider, {}),
    )

    _seed_session("sum-conv", ["conventional needle"])
    out = asyncio.run(summarize_session("sum-conv"))

    assert out["summary"] == "conventional"
    assert captured["agent_key"] == "session_summarizer"
    assert "conventional needle" in captured["prompt"]
    assert captured["model"] == "gpt-conv"
    assert captured["runtime"]["provider"] == "openai"
    # The agent's own instructions.md is the prompt — never overridden.
    assert "instructions" not in captured


def test_conventional_session_summarizer_no_model_names_prog(
    _fresh_identity, monkeypatch, tmp_path
):
    _install_session_product(tmp_path, with_agent=True)
    set_session_summarizer(None)
    monkeypatch.setattr(
        "omniagents.core.models.resolve_model_runtime",
        lambda model_ref=None, **_kw: (None, {}),
    )

    summarizer = session_summarizer()
    assert summarizer is not None
    with pytest.raises(RuntimeError, match="Run: sessprog model setup"):
        asyncio.run(
            summarizer(
                "p", model=None, project=None, agent=None, session_id="x"
            )
        )


def test_missing_conventional_agent_behaves_as_uninstalled(
    _fresh_identity, tmp_path
):
    _install_session_product(tmp_path, with_agent=False)
    set_session_summarizer(None)
    assert session_summarizer() is None
    with pytest.raises(RuntimeError, match="set_session_summarizer"):
        asyncio.run(summarize_session("whatever"))


def test_explicit_session_summarizer_wins_over_convention(
    _fresh_identity, tmp_path
):
    _install_session_product(tmp_path, with_agent=True)

    async def explicit(prompt, **kwargs):
        return "{}"

    set_session_summarizer(explicit)
    assert session_summarizer() is explicit
    set_session_summarizer(None)
    assert session_summarizer() is not None
    assert session_summarizer() is not explicit


def test_manifest_session_envelope_reads_declaration(_fresh_identity, tmp_path):
    from omniagents.core.session.summarize import manifest_session_envelope

    _install_session_product(
        tmp_path,
        sessions_block=(
            "sessions:\n"
            "  summary_envelope:\n"
            '    summary: ""\n'
            "    key_files: []\n"
            "    tests: []\n"
        ),
    )
    assert manifest_session_envelope() == {
        "summary": "",
        "key_files": [],
        "tests": [],
    }


def test_manifest_session_envelope_absent_or_malformed(_fresh_identity, tmp_path):
    from omniagents.core.product import reset_product_info
    from omniagents.core.session.summarize import manifest_session_envelope

    # No sessions: section at all.
    _install_session_product(tmp_path, with_agent=False)
    assert manifest_session_envelope() is None
    # Malformed declaration (not a mapping).
    other = tmp_path / "bad"
    other.mkdir()
    reset_product_info()
    _install_session_product(
        other, with_agent=False, sessions_block="sessions:\n  summary_envelope: nope\n"
    )
    assert manifest_session_envelope() is None
    # No installed manifest at all.
    reset_product_info()
    assert manifest_session_envelope() is None
