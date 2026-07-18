"""Tests for the builtin ``/compact`` server function."""

import asyncio

import pytest

from omniagents.builtin.server_functions.compact import compact
from omniagents.core.runtime.compaction import (
    compaction_summarizer,
    is_checkpoint_item,
    set_compaction_summarizer,
)


@pytest.fixture(autouse=True)
def _reset_seam():
    original = compaction_summarizer()
    yield
    set_compaction_summarizer(original)


class _FakeSession:
    def __init__(self, history=None):
        self.id = "sess-1"
        self.active_run_id = "run-1"
        self.history = list(history or [])

    def append_message(self, item):
        self.history.append(item)


class _FakeService:
    def __init__(self):
        self.status_calls = []

    async def set_client_status(self, text, **kwargs):
        self.status_calls.append((text, kwargs.get("show_spinner")))


def test_empty_session_short_circuits():
    set_compaction_summarizer(None)
    svc = _FakeService()
    result = asyncio.run(compact(svc, _FakeSession()))
    assert "Nothing to compact yet" in result
    assert svc.status_calls == []


def test_missing_summarizer_reports_unavailable():
    set_compaction_summarizer(None)
    svc = _FakeService()
    session = _FakeSession([{"role": "user", "content": "hi"}])

    result = asyncio.run(compact(svc, session))

    assert "no compaction summarizer" in result
    # History untouched — no checkpoint appended without a summarizer.
    assert len(session.history) == 1


def test_compacts_with_installed_summarizer():
    captured = {}

    async def summarizer(prompt, *, service, session):
        captured["prompt"] = prompt
        captured["session"] = session
        return "the summary body"

    set_compaction_summarizer(summarizer)
    svc = _FakeService()
    session = _FakeSession([{"role": "user", "content": "fix the bug"}])

    result = asyncio.run(compact(svc, session))

    assert result == "Context compacted."
    assert "fix the bug" in captured["prompt"]
    assert captured["session"] is session
    # A checkpoint marker item was appended.
    assert is_checkpoint_item(session.history[-1])
    assert "the summary body" in session.history[-1]["content"]
    # Spinner shown, then cleared even on the happy path.
    assert svc.status_calls[0] == ("Compacting...", True)
    assert svc.status_calls[-1] == ("", False)
