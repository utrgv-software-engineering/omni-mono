"""Tests for offline session summarization: seam, orchestration, prompt."""

import asyncio
import json

import pytest

from omniagents.core.session import history_db
from omniagents.core.session.summarize import (
    DEFAULT_SESSION_ENVELOPE,
    DEFAULT_SESSION_SUMMARY_INSTRUCTIONS,
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


def test_default_session_summary_instructions_shape():
    text = DEFAULT_SESSION_SUMMARY_INSTRUCTIONS
    # JSON-only structured summary of a stored session.
    assert text.startswith("Return ONLY valid JSON")
    assert "Session metadata" in text
    # The schema advertises exactly the neutral envelope keys.
    for key in DEFAULT_SESSION_ENVELOPE:
        assert f'"{key}"' in text
    for key in ("session_id", "project", "agent", "created_at", "message_count"):
        assert f'"{key}"' in text
    # Domain-neutralized in the lift: no coding-flavored keys or wording.
    assert "key_files" not in text
    assert '"commands"' not in text
    assert '"tests"' not in text
    assert "coding" not in text.lower()
    assert "file path" not in text.lower()
