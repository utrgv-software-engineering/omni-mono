from omniagents.core.runtime.compaction import (
    build_resume_protocol_block,
    extract_tool_events,
    format_tool_events_block,
)


def test_extract_tool_events_and_format_block():
    history = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file"}}]},
        {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
    ]

    events = extract_tool_events(history)

    assert len(events) == 2
    rendered = format_tool_events_block(events)
    assert "read_file" in rendered
    assert '"tool_call_id": "call-1"' in rendered


def test_build_resume_protocol_block_lists_recent_files():
    history = [
        {"role": "user", "content": "fix the bug"},
        {"role": "user", "content": "continue"},
    ]
    tool_events = [{"name": "read_file", "file_path": "/tmp/demo.py"}]

    block = build_resume_protocol_block(history=history, tool_events=tool_events)

    assert "Resume Protocol (do this first):" in block
    assert "/tmp/demo.py" in block


# ---------------------------------------------------------------------------
# Checkpoint compaction
# ---------------------------------------------------------------------------

import asyncio

import pytest

from omniagents.core.runtime.compaction import (
    DEFAULT_CHECKPOINT_ENVELOPE,
    history_since_last_checkpoint,
    make_auto_compact_error_hook,
    make_auto_compact_token_hook,
    run_checkpoint_compaction,
    summarize_with_window_shrink,
)


def _checkpoint_item(text="old summary"):
    return {
        "role": "assistant",
        "content": text,
        "omniagents": {"kind": "context_summary", "version": 1},
    }


class _FakeSession:
    def __init__(self, history):
        self.id = "sess-1"
        self.history = list(history)
        self.variables = {}

    def append_message(self, item):
        self.history.append(item)


class ContextWindowExceededError(Exception):
    """Name-matched by is_context_length_exceeded."""


def test_history_since_last_checkpoint_identity_without_marker():
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert history_since_last_checkpoint(history) == history
    assert history_since_last_checkpoint([]) == []


def test_history_since_last_checkpoint_windows_after_marker():
    before = {"role": "user", "content": "before"}
    marker = _checkpoint_item()
    after = {"role": "user", "content": "after"}
    history = [before, marker, after]
    assert history_since_last_checkpoint(history) == [after]
    assert history_since_last_checkpoint(history, include_checkpoint=True) == [
        marker,
        after,
    ]


def test_run_checkpoint_compaction_appends_marker_item():
    session = _FakeSession(
        [
            {"role": "user", "content": "please fix the bug"},
            {"role": "assistant", "content": "working on it"},
        ]
    )
    seen_prompts = []

    async def summarize(prompt):
        seen_prompts.append(prompt)
        return "the summary body"

    item = asyncio.run(run_checkpoint_compaction(session, summarize))

    assert session.history[-1] is item
    assert item["omniagents"] == {"kind": "context_summary", "version": 1}
    assert item["content"].startswith(DEFAULT_CHECKPOINT_ENVELOPE)
    assert "the summary body" in item["content"]
    # Summarizer saw the user's messages.
    assert "please fix the bug" in seen_prompts[0]


def test_run_checkpoint_compaction_windows_since_previous_checkpoint():
    session = _FakeSession(
        [
            {"role": "user", "content": "OLD-WORK"},
            _checkpoint_item(),
            {"role": "user", "content": "NEW-WORK"},
        ]
    )

    async def summarize(prompt):
        # The transcript window must exclude items before (and including)
        # the previous checkpoint; the verbatim user-message block still
        # spans the full history by design.
        transcript = prompt.split("Conversation transcript since last checkpoint", 1)[1]
        assert "NEW-WORK" in transcript
        assert "OLD-WORK" not in transcript
        return "s"

    asyncio.run(run_checkpoint_compaction(session, summarize))


def test_run_checkpoint_compaction_custom_envelope():
    session = _FakeSession([{"role": "user", "content": "hi"}])

    async def summarize(prompt):
        return "s"

    item = asyncio.run(
        run_checkpoint_compaction(session, summarize, envelope="CUSTOM ENVELOPE. ")
    )
    assert item["content"].startswith("CUSTOM ENVELOPE. ")


def test_summarize_with_window_shrink_drops_oldest_on_context_error():
    window = [{"role": "user", "content": f"item-{i}"} for i in range(3)]
    prompts = []
    calls = {"n": 0}

    def build_prompt(w):
        return "|".join(i["content"] for i in w)

    async def summarize(prompt):
        prompts.append(prompt)
        calls["n"] += 1
        if calls["n"] < 3:
            raise ContextWindowExceededError("too big")
        return "ok"

    result = asyncio.run(
        summarize_with_window_shrink(
            window, build_prompt=build_prompt, summarize=summarize
        )
    )
    assert result == "ok"
    assert prompts == ["item-0|item-1|item-2", "item-1|item-2", "item-2"]
    # Caller's window is not mutated.
    assert len(window) == 3


def test_summarize_with_window_shrink_propagates_other_errors():
    async def summarize(prompt):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(
            summarize_with_window_shrink(
                [{"role": "user", "content": "x"}],
                build_prompt=lambda w: "p",
                summarize=summarize,
            )
        )


def test_summarize_with_window_shrink_empty_window_propagates():
    async def summarize(prompt):
        raise ContextWindowExceededError("still too big")

    with pytest.raises(ContextWindowExceededError):
        asyncio.run(
            summarize_with_window_shrink(
                [], build_prompt=lambda w: "p", summarize=summarize
            )
        )


class _FakeService:
    def __init__(self):
        self.calls = []

    async def server_call(self, command, *, args, session_id):
        self.calls.append({"command": command, "args": args, "session_id": session_id})


def _run_hook(hook, service, session, *, is_context=True):
    return asyncio.run(
        hook(
            service=service,
            session=session,
            run_id="run-1",
            params={"error": RuntimeError("x"), "is_context_length_exceeded": is_context},
            event="error",
        )
    )


def test_auto_compact_hook_compacts_and_retries():
    hook = make_auto_compact_error_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])

    result = _run_hook(hook, service, session)

    assert result == {"retry": True}
    assert service.calls == [
        {"command": "compact", "args": {}, "session_id": "sess-1"}
    ]
    state = session.variables["prod"]
    assert state["needs_context_compression"] is True
    assert state["context_error_run_id"] == "run-1"
    assert state["auto_compact_in_progress"] is False


def test_auto_compact_hook_ignores_non_context_errors():
    hook = make_auto_compact_error_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    assert _run_hook(hook, service, session, is_context=False) is None
    assert service.calls == []
    assert session.variables == {}


def test_auto_compact_hook_honors_opt_out():
    hook = make_auto_compact_error_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    session.variables["prod"] = {"auto_compact": False}

    result = _run_hook(hook, service, session)

    assert result is None
    assert service.calls == []
    # Pressure flags are still recorded.
    assert session.variables["prod"]["needs_context_compression"] is True


def test_auto_compact_hook_reentry_guard():
    hook = make_auto_compact_error_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    session.variables["prod"] = {"auto_compact_in_progress": True}
    assert _run_hook(hook, service, session) is None
    assert service.calls == []


def test_auto_compact_hook_custom_command():
    hook = make_auto_compact_error_hook(state_key="prod", command="my.compact")
    service = _FakeService()
    session = _FakeSession([])
    _run_hook(hook, service, session)
    assert service.calls[0]["command"] == "my.compact"


def _run_token_hook(hook, service, session, *, total=95, max_input=100):
    return asyncio.run(
        hook(
            service=service,
            session=session,
            run_id="run-1",
            params={
                "max_input_tokens": max_input,
                "delta": {"total_tokens": total},
            },
            event="token",
        )
    )


def test_auto_compact_token_hook_compacts_above_threshold():
    hook = make_auto_compact_token_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])

    _run_token_hook(hook, service, session, total=95, max_input=100)

    assert service.calls == [
        {"command": "compact", "args": {}, "session_id": "sess-1"}
    ]
    state = session.variables["prod"]
    assert state["needs_context_compression"] is True
    assert state["context_utilization"] == 0.95
    assert state["context_utilization_run_id"] == "run-1"
    assert state["auto_compact_in_progress"] is False


def test_auto_compact_token_hook_ignores_below_threshold():
    hook = make_auto_compact_token_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    _run_token_hook(hook, service, session, total=50, max_input=100)
    assert service.calls == []
    assert session.variables == {}


def test_auto_compact_token_hook_ignores_missing_max_input():
    hook = make_auto_compact_token_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    _run_token_hook(hook, service, session, total=95, max_input=None)
    assert service.calls == []


def test_auto_compact_token_hook_honors_opt_out():
    hook = make_auto_compact_token_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    session.variables["prod"] = {"auto_compact": False}

    _run_token_hook(hook, service, session)

    assert service.calls == []
    # Pressure flags are still recorded.
    assert session.variables["prod"]["needs_context_compression"] is True


def test_auto_compact_token_hook_reentry_guard():
    hook = make_auto_compact_token_hook(state_key="prod")
    service = _FakeService()
    session = _FakeSession([])
    session.variables["prod"] = {"auto_compact_in_progress": True}
    _run_token_hook(hook, service, session)
    assert service.calls == []


def test_auto_compact_token_hook_custom_threshold():
    hook = make_auto_compact_token_hook(state_key="prod", threshold=0.5)
    service = _FakeService()
    session = _FakeSession([])
    _run_token_hook(hook, service, session, total=60, max_input=100)
    assert service.calls[0]["command"] == "compact"
