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


@pytest.mark.asyncio
async def test_external_backend_does_not_compact_unrelated_transcript():
    from types import SimpleNamespace

    svc = _FakeService()
    svc.spec = SimpleNamespace(session_backend=object())
    session = _FakeSession([{"role": "user", "content": "keep this"}])
    with pytest.raises(RuntimeError, match="external SDK session backend"):
        await compact(svc, session)
    assert svc.status_calls == []
    assert session.history == [{"role": "user", "content": "keep this"}]


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


@pytest.mark.asyncio
async def test_timeout_clears_status_preserves_history_and_allows_retry(monkeypatch):
    import importlib

    module = importlib.import_module("omniagents.builtin.server_functions.compact")
    monkeypatch.setattr(module, "COMPACTION_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    set_compaction_summarizer(stalled)
    session = _FakeSession([{"role": "user", "content": "keep this"}])
    svc = _FakeService()
    with pytest.raises(RuntimeError, match="compaction timed out"):
        await compact(svc, session)
    assert cancelled.is_set()
    assert session.history == [{"role": "user", "content": "keep this"}]
    assert svc.status_calls[-1] == ("", False)
    assert not session._checkpoint_compaction_in_progress

    async def success(*args, **kwargs):
        return "summary"

    set_compaction_summarizer(success)
    monkeypatch.setattr(module, "COMPACTION_TIMEOUT_SECONDS", 5)
    assert await compact(svc, session) == "Context compacted."


@pytest.mark.asyncio
async def test_manual_compact_cannot_overlap_automatic_attempt():
    entered, release = asyncio.Event(), asyncio.Event()

    async def summarize(*args, **kwargs):
        entered.set()
        await release.wait()
        return "summary"

    set_compaction_summarizer(summarize)
    session = _FakeSession([{"role": "user", "content": "keep this"}])
    svc = _FakeService()
    first = asyncio.create_task(compact(svc, session))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(RuntimeError, match="already in progress"):
            await compact(svc, session)
        assert svc.status_calls == [("Compacting...", True)]
    finally:
        release.set()
        await first


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["tool", "native", "model"])
async def test_compact_refuses_unsafe_history_snapshot(busy):
    from types import SimpleNamespace

    session = _FakeSession([{"role": "user", "content": "keep this"}])
    if busy == "tool":
        session.history.append({"type": "function_call", "call_id": "pending"})
    elif busy == "native":
        session._sdk_compaction_in_progress = True
    else:
        session.current_stream_result = SimpleNamespace(is_complete=False)
    svc = _FakeService()
    with pytest.raises(RuntimeError):
        await compact(svc, session)
    assert svc.status_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "overflow"])
@pytest.mark.parametrize("failure", [False, True])
async def test_native_compaction_always_wins_over_agent(trigger, failure):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from omniagents.core.runtime.compaction import make_auto_compact_error_hook
    from omniagents.core.session.compaction import wrap_with_compaction
    from omniagents.core.session.manager import Session

    session = Session("native", "routing")
    original = {"role": "user", "content": "preserve original transcript"}
    session.append_message(original)
    replacement = [{"role": "user", "content": "native compacted context"}]
    endpoint = AsyncMock(return_value=SimpleNamespace(output=replacement))
    if failure:
        endpoint.side_effect = RuntimeError("native endpoint failed")
    native = wrap_with_compaction(
        session,
        {
            "enabled": True,
            "model": "gpt-6-astra",
            "client": SimpleNamespace(responses=SimpleNamespace(compact=endpoint)),
        },
    )
    assert native is not session
    session._sdk_compaction_session = native
    summarizer = AsyncMock(return_value="must not be used")
    set_compaction_summarizer(summarizer)
    service = _FakeService()

    async def server_call(*args, **kwargs):
        return await compact(service, session)

    service.server_call = server_call

    async def invoke():
        if trigger == "manual":
            return await compact(service, session)
        hook = make_auto_compact_error_hook(state_key="test")
        return await hook(
            service=service,
            session=session,
            run_id="run",
            event="error",
            params={
                "error": RuntimeError("context overflow"),
                "is_context_length_exceeded": True,
            },
        )

    if failure:
        with pytest.raises(RuntimeError, match="native endpoint failed"):
            await invoke()
        assert await session.get_items() == [original]
    else:
        result = await invoke()
        assert result == (
            "Context compacted." if trigger == "manual" else {"retry": True}
        )
        assert await session.get_items() == replacement
    endpoint.assert_awaited_once_with(model="gpt-6-astra", input=[original])
    summarizer.assert_not_awaited()
    assert session.history == [original]
    assert not session._compact_command_in_progress
    assert not session._sdk_compaction_in_progress
    assert service.status_calls[-1] == ("", False)


@pytest.mark.parametrize("setup_fails", [False, True])
def test_native_resolution_on_reopened_session(monkeypatch, setup_fails):
    from types import SimpleNamespace
    import omniagents.core.models as models
    import omniagents.core.session.compaction as compaction

    runtime = {"model": "gpt-6-astra", "use_responses": True}
    session = SimpleNamespace(model_config={"max_input_tokens": 272000})
    service = SimpleNamespace(
        spec=SimpleNamespace(session_compaction_config={"enabled": True})
    )
    provider = object()
    wrapper = object()
    monkeypatch.setattr(
        models, "resolve_service_model_runtime", lambda *a: ("azure/astra", runtime)
    )
    monkeypatch.setattr(models, "provider_from_runtime", lambda *a: (provider, {}))

    def wrap(*args, **kwargs):
        assert kwargs["model_provider"] is provider
        assert kwargs["max_input_tokens"] == 272000
        return session if setup_fails else wrapper

    monkeypatch.setattr(compaction, "wrap_with_compaction", wrap)
    if setup_fails:
        with pytest.raises(RuntimeError, match="could not be initialized"):
            compaction.native_compaction_for_session(service, session)
    else:
        assert compaction.native_compaction_for_session(service, session) is wrapper
        assert session._sdk_compaction_session is wrapper


@pytest.mark.asyncio
async def test_native_command_does_not_inherit_checkpoint_timeout(monkeypatch):
    import importlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    module = importlib.import_module("omniagents.builtin.server_functions.compact")
    monkeypatch.setattr(module, "COMPACTION_TIMEOUT_SECONDS", 0.001)

    async def native_compact(args):
        await asyncio.sleep(0.02)

    native = SimpleNamespace(run_compaction=AsyncMock(side_effect=native_compact))
    session = _FakeSession([{"role": "user", "content": "history"}])
    session._sdk_compaction_session = native
    assert await compact(_FakeService(), session) == "Context compacted."
    native.run_compaction.assert_awaited_once()
