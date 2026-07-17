"""Tests for the session recap orchestration and summarizer seam."""

import asyncio

import pytest

from omniagents.core.runtime.recap import (
    generate_and_broadcast_recap,
    generate_recap,
    recap_summarizer,
    set_recap_summarizer,
)


@pytest.fixture(autouse=True)
def _reset_seam():
    original = recap_summarizer()
    yield
    set_recap_summarizer(original)


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
