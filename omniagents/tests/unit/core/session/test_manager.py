import asyncio
import uuid

import pytest

from omniagents.core.session.manager import (
    SessionManager,
    filter_history_for_api,
    strip_internal_keys_for_api,
)


def make_manager():
    return SessionManager("proj", "agent")


def test_get_or_create_reuses_existing_session():
    manager = make_manager()
    session = manager.get_or_create()
    same = manager.get_or_create(session.id)
    assert same is session
    assert same.id == session.id


def test_get_or_create_loads_history_and_archived_state():
    manager = make_manager()
    session = manager.get_or_create()
    message = {"role": "user", "content": "hi"}
    session.append_message(message)
    manager.archive_session(session.id)
    reloaded = make_manager().get_or_create(session.id)
    assert reloaded.history == [message]
    assert reloaded.archived is True


@pytest.mark.asyncio
async def test_bind_task_tracks_and_cancel_clears_session():
    manager = make_manager()
    session = manager.get_or_create()
    event = asyncio.Event()
    task = asyncio.create_task(event.wait(), name="run-bind")
    manager.bind_task(task, session)
    assert manager.get_by_run_id("run-bind") is session
    assert session.current_task is task
    cancelled = await manager.cancel("run-bind")
    assert cancelled is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.current_task is None


@pytest.mark.asyncio
async def test_cancel_handles_missing_tasks():
    manager = make_manager()
    assert await manager.cancel("missing") is False
    session = manager.get_or_create()
    manager._task_map["orphaned"] = session
    assert await manager.cancel("orphaned") is False


def test_archive_session_persists_flag():
    manager = make_manager()
    session = manager.get_or_create()
    ok = manager.archive_session(session.id)
    assert ok is True
    assert session.archived is True
    reloaded = make_manager().get_or_create(session.id)
    assert reloaded.archived is True


def test_list_sessions_includes_timestamped_messages():
    manager = make_manager()
    session = manager.get_or_create()
    session.append_message({"role": "user", "content": "first"})
    session.append_message({"role": "assistant", "content": "second"})
    info = manager.list_sessions()
    assert len(info) == 1
    entry = info[0]
    assert entry["message_count"] == 2
    first = entry["first_message"]
    last = entry["last_message"]
    assert first["content"] == "first"
    assert isinstance(first["timestamp"], str)
    assert last["content"] == "second"
    assert isinstance(last["timestamp"], str)


def test_list_sessions_limit_and_offset_page_newest_first():
    manager = make_manager()
    for i in range(3):
        session = manager.get_or_create()
        session.append_message({"role": "user", "content": f"m{i}"})

    everything = manager.list_sessions()
    assert len(everything) == 3

    page1 = manager.list_sessions(limit=2)
    page2 = manager.list_sessions(limit=2, offset=2)
    assert [e["id"] for e in page1] == [e["id"] for e in everything[:2]]
    assert [e["id"] for e in page2] == [e["id"] for e in everything[2:]]

    # Paging past the end is empty, not an error.
    assert manager.list_sessions(limit=2, offset=10) == []


def test_get_user_messages_filters_archived_sessions():
    manager = make_manager()
    archived = manager.get_or_create()
    archived.append_message({"role": "user", "content": " archived "})
    manager.archive_session(archived.id)
    active = manager.get_or_create()
    active.append_message({"role": "user", "content": "active message"})
    filtered = manager.get_user_messages(limit=10, include_archived=False)
    assert [item["session_id"] for item in filtered] == [active.id]
    assert filtered[0]["content"] == "active message"
    all_messages = manager.get_user_messages(limit=10, include_archived=True)
    assert [item["session_id"] for item in all_messages] == [archived.id, active.id]


def test_archive_session_returns_false_for_missing_session():
    manager = make_manager()
    assert manager.archive_session("missing") is False


def test_get_or_create_registers_external_session_identifier():
    manager = make_manager()
    external_id = str(uuid.uuid4())
    session = manager.get_or_create(external_id)
    assert session.id == external_id
    sessions = manager.list_sessions()
    assert len(sessions) == 1
    entry = sessions[0]
    assert entry["id"] == external_id
    assert entry["message_count"] == 0
    assert "first_message" not in entry
    assert "last_message" not in entry


def test_get_or_create_repairs_missing_tool_outputs_after_crash(temp_history_db):
    manager = make_manager()
    session = manager.get_or_create()
    session.append_message(
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "alpha",
            "arguments": "{}",
            "status": "completed",
        }
    )

    reloaded = make_manager().get_or_create(session.id)
    assert any(
        item.get("type") == "function_call_output"
        and item.get("call_id") == "call_1"
        and "Missing tool output" in (item.get("output") or "")
        for item in reloaded.history
    )


def test_get_or_create_repairs_missing_chat_completions_tool_outputs(temp_history_db):
    manager = make_manager()
    session = manager.get_or_create()
    session.append_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "alpha", "arguments": "{}"},
                }
            ],
        }
    )

    reloaded = make_manager().get_or_create(session.id)
    assert any(
        item.get("role") == "tool"
        and item.get("tool_call_id") == "call_1"
        and "Missing tool output" in (item.get("content") or "")
        for item in reloaded.history
    )


# ---------------------------------------------------------------------
# Tests for ``filter_history_for_api`` and ``strip_internal_keys_for_api``
# (moved here from ``tests/unit/core/runtime/test_bridge.py`` when these
# helpers became part of the OmniAgents Session and run inside
# ``Session.get_items()``).
# ---------------------------------------------------------------------


def test_filter_history_for_api_handles_parallel_function_calls_after_reasoning():
    """Test that reasoning followed by multiple parallel function calls is reordered correctly."""
    history = [
        {"role": "user", "content": "investigate codebase"},
        {"type": "reasoning", "id": "rs_123", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "fc_1", "name": "list_dir"},
        {"type": "function_call", "id": "fc_2", "call_id": "fc_2", "name": "glob"},
        {"type": "function_call", "id": "fc_3", "call_id": "fc_3", "name": "grep"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Scanning repo"}],
        },
        {"type": "function_call_output", "call_id": "fc_1", "output": "result1"},
        {"type": "function_call_output", "call_id": "fc_2", "output": "result2"},
        {"type": "function_call_output", "call_id": "fc_3", "output": "result3"},
    ]

    result = filter_history_for_api(history)

    # Calls come DIRECTLY after their reasoning; the message trails the
    # group (live API probe 2026-07-07: msg-between-rs-and-calls is 400'd).
    # Expected: user, reasoning, fc_1, fc_2, fc_3, out1, out2, out3, message
    assert len(result) == 9
    assert result[0]["role"] == "user"
    assert result[1]["type"] == "reasoning"
    assert result[1]["id"] == "rs_123"
    assert result[2]["type"] == "function_call"
    assert result[2]["id"] == "fc_1"
    assert result[3]["type"] == "function_call"
    assert result[3]["id"] == "fc_2"
    assert result[4]["type"] == "function_call"
    assert result[4]["id"] == "fc_3"
    assert result[5]["type"] == "function_call_output"
    assert result[5]["call_id"] == "fc_1"
    assert result[6]["type"] == "function_call_output"
    assert result[6]["call_id"] == "fc_2"
    assert result[7]["type"] == "function_call_output"
    assert result[7]["call_id"] == "fc_3"
    assert result[8]["type"] == "message"
    assert result[8]["id"] == "msg_1"


def test_filter_history_for_api_never_pairs_injected_assistant_with_reasoning():
    """An id-less assistant item (a notification/wakeup appended by
    ``start_run(prompt_role="assistant")``) that follows a tool-call turn
    with NO model message must not be reordered into the reasoning group:
    that moved it between the reasoning and its function_call and the API
    rejected the call ("function_call ... without its required 'reasoning'
    item"). The group is treated as orphaned instead (reasoning dropped,
    synthetic call ids) and the injected item stays in place."""
    injected = {
        "type": "message",
        "role": "assistant",
        "content": "[wakeup] It is 08:30. Your watering is done.",
    }
    history = [
        {"role": "user", "content": "start the day"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "c_1", "name": "harvest"},
        {"type": "function_call_output", "call_id": "c_1", "output": "done"},
        injected,
    ]

    result = filter_history_for_api(history)

    types = [item.get("type", "message") for item in result]
    assert "reasoning" not in types  # orphaned group: reasoning dropped
    fc = next(item for item in result if item.get("type") == "function_call")
    assert fc["id"].startswith("fc_synth_")  # no dangling rs linkage
    # the injected item was not consumed into the group and keeps its place
    assert result[-1] == injected


def test_filter_history_for_api_handles_reasoning_with_single_function_call():
    history = [
        {"type": "reasoning", "id": "rs_456", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "fc_1", "name": "search"},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": []},
        {"type": "function_call_output", "call_id": "fc_1", "output": "result"},
    ]

    result = filter_history_for_api(history)

    assert len(result) == 4
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "function_call"
    assert result[2]["type"] == "function_call_output"
    assert result[3]["type"] == "message"


def test_filter_history_for_api_handles_reasoning_without_function_calls():
    history = [
        {"type": "reasoning", "id": "rs_789", "summary": []},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": []},
    ]

    result = filter_history_for_api(history)

    assert len(result) == 2
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "message"


def test_filter_history_for_api_preserves_non_reasoning_items():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"type": "function_call_output", "call_id": "fc_x", "output": "old result"},
    ]

    result = filter_history_for_api(history)

    # Plain messages pass through untouched; the output whose call exists
    # nowhere in history is dropped by the repair pass (the API rejects the
    # whole input over an orphaned output, bricking the session).
    assert result == history[:2]


def test_filter_history_for_api_handles_multiple_reasoning_groups():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "tool1"},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": []},
        {"type": "function_call_output", "call_id": "call_1", "output": "r1"},
        {"type": "reasoning", "id": "rs_2", "summary": []},
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "tool2"},
        {"type": "function_call_output", "call_id": "call_2", "output": "r2"},
    ]

    result = filter_history_for_api(history)

    # rs_1 paired with msg_1 (message trails the calls); rs_2 orphaned
    # (no message) -> synthesised.
    assert len(result) == 6
    assert result[0]["id"] == "rs_1"
    assert result[1]["id"] == "fc_1"
    assert result[2]["call_id"] == "call_1"
    assert result[3]["id"] == "msg_1"
    assert result[4]["type"] == "function_call"
    assert result[4]["id"].startswith("fc_synth_")
    assert result[4]["name"] == "tool2"
    assert result[5]["type"] == "function_call_output"
    assert result[5]["call_id"].startswith("call_synth_")
    assert result[5]["output"] == "r2"


def test_filter_history_for_api_finds_outputs_appended_later():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "tool1",
            "arguments": "{}",
        },
        {"role": "assistant", "content": "partial"},
        {"role": "user", "content": "next"},
        {"type": "function_call_output", "call_id": "call_1", "output": "cancelled"},
    ]

    result = filter_history_for_api(history)

    assert any(
        item.get("type") == "function_call_output"
        and isinstance(item.get("call_id"), str)
        and item.get("call_id", "").startswith("call_synth_")
        and item.get("output") == "cancelled"
        for item in result
    )


def test_filter_history_for_api_interleaved_calls_and_outputs():
    history = [
        {"role": "user", "content": "scan the codebase"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "spawn_worker",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "w1"},
        {
            "type": "function_call",
            "id": "fc_2",
            "call_id": "call_2",
            "name": "spawn_worker",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_2", "output": "w2"},
        {
            "type": "function_call",
            "id": "fc_3",
            "call_id": "call_3",
            "name": "spawn_worker",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_3", "output": "w3"},
        {"type": "reasoning", "id": "rs_2", "summary": []},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Got it. Scanning in parallel."}],
        },
    ]

    result = filter_history_for_api(history)

    # msg_1 belongs to rs_2 (every API response opens with its own
    # reasoning item). It must NOT be stolen backwards into rs_1's group:
    # doing so detaches it from rs_2 and wedges it between rs_1 and the
    # calls — the API rejects that with "function_call ... without its
    # required 'reasoning' item". rs_1's group has no message of its own,
    # so it takes the orphan path: reasoning dropped, synthetic call ids.
    fc_items = [r for r in result if r.get("type") == "function_call"]
    assert len(fc_items) == 3
    assert all(fc["id"].startswith("fc_synth_") for fc in fc_items)

    out_items = [r for r in result if r.get("type") == "function_call_output"]
    assert len(out_items) == 3
    assert {o["call_id"] for o in out_items} == {fc["call_id"] for fc in fc_items}

    rs2_idx = next(i for i, r in enumerate(result) if r.get("id") == "rs_2")
    msg_idx = next(i for i, r in enumerate(result) if r.get("id") == "msg_1")
    assert msg_idx == rs2_idx + 1  # rs_2 keeps ITS message adjacent
    last_fc_idx = max(
        i for i, r in enumerate(result) if r.get("type") == "function_call"
    )
    assert last_fc_idx < rs2_idx


def test_filter_history_for_api_interleaved_single_reasoning_group():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read_file",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "glob"},
        {"type": "function_call_output", "call_id": "call_2", "output": "matches"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Here's what I found."}],
        },
    ]

    result = filter_history_for_api(history)

    assert len(result) == 6
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "function_call"
    assert result[1]["call_id"] == "call_1"
    assert result[2]["type"] == "function_call"
    assert result[2]["call_id"] == "call_2"
    assert result[3]["type"] == "function_call_output"
    assert result[3]["call_id"] == "call_1"
    assert result[4]["type"] == "function_call_output"
    assert result[4]["call_id"] == "call_2"
    assert result[5]["type"] == "message"
    assert result[5]["id"] == "msg_1"


def test_filter_history_for_api_interleaved_multi_turn():
    history = [
        {"role": "user", "content": "investigate codebase"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "spawn_worker",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "w1"},
        {
            "type": "function_call",
            "id": "fc_2",
            "call_id": "call_2",
            "name": "spawn_worker",
        },
        {"type": "function_call_output", "call_id": "call_2", "output": "w2"},
        {
            "type": "function_call",
            "id": "fc_3",
            "call_id": "call_3",
            "name": "spawn_worker",
        },
        {"type": "function_call_output", "call_id": "call_3", "output": "w3"},
        {"type": "reasoning", "id": "rs_2", "summary": []},
        {
            "type": "message",
            "id": "msg_preamble",
            "role": "assistant",
            "content": [{"type": "text", "text": "Scanning in parallel."}],
        },
        {"role": "user", "content": "go ahead"},
        {"type": "reasoning", "id": "rs_3", "summary": []},
        {
            "type": "function_call",
            "id": "fc_4",
            "call_id": "call_4",
            "name": "wait_for_workers",
        },
        {
            "type": "message",
            "id": "msg_final",
            "role": "assistant",
            "content": [{"type": "text", "text": "Here are the results."}],
        },
        {
            "type": "function_call_output",
            "call_id": "call_4",
            "output": "completed",
        },
    ]

    result = filter_history_for_api(history)

    # msg_preamble belongs to rs_2, not to rs_1's spawn group — the group's
    # message search stops at the next reasoning item. The spawn calls lose
    # their (never-messaged) reasoning group and become synthetic; rs_2
    # keeps its preamble adjacent; rs_3's same-turn group stays intact.
    spawns = [
        (i, r)
        for i, r in enumerate(result)
        if r.get("type") == "function_call" and r.get("name") == "spawn_worker"
    ]
    assert len(spawns) == 3
    assert all(r["id"].startswith("fc_synth_") for _, r in spawns)

    rs2_idx = next(i for i, r in enumerate(result) if r.get("id") == "rs_2")
    preamble_idx = next(
        i for i, r in enumerate(result) if r.get("id") == "msg_preamble"
    )
    assert preamble_idx == rs2_idx + 1
    assert all(i < rs2_idx for i, _ in spawns)

    rs3_idx = next(i for i, r in enumerate(result) if r.get("id") == "rs_3")
    assert result[rs3_idx + 1].get("id") == "fc_4"  # calls right after reasoning
    assert result[rs3_idx + 2].get("call_id") == "call_4"
    assert result[rs3_idx + 3].get("id") == "msg_final"  # message trails


def test_filter_history_for_api_mixed_interleaved_and_grouped():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read_file",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "glob"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Looking..."}],
        },
        {"type": "function_call_output", "call_id": "call_2", "output": "matches"},
    ]

    result = filter_history_for_api(history)

    assert len(result) == 6
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "function_call"
    assert result[1]["call_id"] == "call_1"
    assert result[2]["type"] == "function_call"
    assert result[2]["call_id"] == "call_2"
    assert result[3]["type"] == "function_call_output"
    assert result[3]["call_id"] == "call_1"
    assert result[4]["type"] == "function_call_output"
    assert result[4]["call_id"] == "call_2"
    assert result[5]["type"] == "message"
    assert result[5]["id"] == "msg_1"


def test_filter_history_for_api_interleaved_orphaned_no_message():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "tool_a"},
        {"type": "function_call_output", "call_id": "call_1", "output": "result_a"},
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "tool_b"},
        {"type": "function_call_output", "call_id": "call_2", "output": "result_b"},
    ]

    result = filter_history_for_api(history)

    assert not any(r.get("type") == "reasoning" for r in result)

    fc_items = [r for r in result if r.get("type") == "function_call"]
    assert len(fc_items) == 2
    assert all(r["id"].startswith("fc_synth_") for r in fc_items)
    assert fc_items[0]["name"] == "tool_a"
    assert fc_items[1]["name"] == "tool_b"

    out_items = [r for r in result if r.get("type") == "function_call_output"]
    assert len(out_items) == 2
    assert all(r["call_id"].startswith("call_synth_") for r in out_items)
    for fc, out in zip(fc_items, out_items):
        assert fc["call_id"] == out["call_id"]


def test_filter_history_for_api_interleaved_stale_output_not_consumed():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read_file",
        },
        {
            "type": "function_call_output",
            "call_id": "call_stale",
            "output": "old stuff",
        },
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "glob"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Here we go."}],
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "file data"},
        {"type": "function_call_output", "call_id": "call_2", "output": "glob hits"},
    ]

    result = filter_history_for_api(history)

    fc_items = [r for r in result if r.get("type") == "function_call"]
    assert len(fc_items) == 2

    out_items = [r for r in result if r.get("type") == "function_call_output"]
    out_call_ids = [r["call_id"] for r in out_items]
    assert "call_1" in out_call_ids
    assert "call_2" in out_call_ids
    # The stale output must not be CONSUMED by the group — and since its
    # call exists nowhere in history, the final repair pass drops it: the
    # API rejects the whole input over an orphaned output ("No tool call
    # found for function call output"), which bricks the session.
    assert "call_stale" not in out_call_ids

    msg_idx = next(i for i, r in enumerate(result) if r.get("type") == "message")
    last_fc_idx = max(
        i for i, r in enumerate(result) if r.get("type") == "function_call"
    )
    assert msg_idx > last_fc_idx  # message trails its group's calls


def test_filter_history_for_api_drops_output_whose_call_is_gone():
    """The 2026-07-24 resident brick: history persisted a function_call_output
    whose function_call never made it to the DB (run killed between the two
    writes). Replaying it 400s the ENTIRE input on every wakeup — the repair
    pass must drop the orphan while leaving intact pairs alone."""
    history = [
        {"type": "message", "id": "msg_0", "role": "user", "content": "hi"},
        {
            "type": "function_call_output",
            "call_id": "call_orphan",
            "output": "output with no surviving call",
        },
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "ls"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "done"}],
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "files"},
    ]

    result = filter_history_for_api(history)

    out_call_ids = [
        r["call_id"] for r in result if r.get("type") == "function_call_output"
    ]
    assert "call_orphan" not in out_call_ids
    assert "call_1" in out_call_ids
    # The intact pair and both messages survive untouched.
    assert any(r.get("id") == "fc_1" for r in result)
    assert sum(1 for r in result if r.get("type") == "message") == 2


def test_filter_history_for_api_placeholder_for_call_without_output():
    """The mirror orphan — a call whose output was never persisted — also
     400s the input ("No tool output found for function call"). The repair
    pass closes the pair with a placeholder output instead of dropping
    real context."""
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "ls"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "working on it"}],
        },
    ]

    result = filter_history_for_api(history)

    outs = [r for r in result if r.get("type") == "function_call_output"]
    assert len(outs) == 1
    assert outs[0]["call_id"] == "call_1"
    assert "lost" in outs[0]["output"]
    # Placeholder lands directly after its call.
    fc_idx = next(i for i, r in enumerate(result) if r.get("id") == "fc_1")
    assert result[fc_idx + 1]["type"] == "function_call_output"


def test_filter_history_for_api_trailing_reasoning_without_message():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "tool_x"},
        {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        {"type": "reasoning", "id": "rs_2", "summary": []},
    ]

    result = filter_history_for_api(history)

    fc_items = [r for r in result if r.get("type") == "function_call"]
    assert len(fc_items) == 1
    assert fc_items[0]["id"].startswith("fc_synth_")
    assert fc_items[0]["name"] == "tool_x"

    out_items = [r for r in result if r.get("type") == "function_call_output"]
    assert len(out_items) == 1
    assert out_items[0]["call_id"] == fc_items[0]["call_id"]

    assert not any(r.get("type") == "reasoning" for r in result)


def test_filter_history_for_api_single_interleaved_call():
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "search"},
        {"type": "function_call_output", "call_id": "call_1", "output": "found it"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Searching..."}],
        },
    ]

    result = filter_history_for_api(history)

    assert len(result) == 4
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "function_call"
    assert result[2]["type"] == "function_call_output"
    assert result[3]["type"] == "message"


def test_filter_history_for_api_reasoning_message_before_function_call():
    history = [
        {"role": "user", "content": "what ticket have you been assigned"},
        {"type": "reasoning", "id": "rs_abc", "summary": []},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "I'm assigned to ticket X"}],
        },
        {
            "type": "function_call",
            "id": "fc_abc",
            "call_id": "call_1",
            "name": "get_ticket",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": '{"id": "X"}'},
        {"role": "user", "content": "read the related tickets"},
    ]

    result = filter_history_for_api(history)

    # Even when the message was PERSISTED before the calls, replay must put
    # the calls directly after the reasoning (live API probe: a message
    # between rs and fc is rejected).
    assert len(result) == 6
    assert result[0]["role"] == "user"
    assert result[1]["type"] == "reasoning"
    assert result[2]["type"] == "function_call"
    assert result[2]["id"] == "fc_abc"
    assert result[3]["type"] == "function_call_output"
    assert result[4]["type"] == "message"
    assert result[5]["role"] == "user"


@pytest.mark.asyncio
async def test_add_items_normalizes_assistant_input_text_parts_in_place():
    """Write-side twin of the strip normalization: the SDK compaction
    wrapper caches the very dicts passed to add_items as compaction
    candidates, so the fix must mutate them in place or responses.compact
    still receives input_text parts on assistant messages."""
    from omniagents.core.session.manager import Session

    sess = Session(project_slug=None, agent_slug=None)
    item = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "input_text", "text": "wakeup"}],
    }
    await sess.add_items([item])
    assert item["content"][0]["type"] == "output_text"  # caller's dict fixed
    assert sess.history[-1]["content"][0]["type"] == "output_text"


def test_strip_internal_keys_for_api_normalizes_assistant_input_text_parts():
    """An assistant message re-persisted in converted input form (string
    content expanded to input_text parts after a cancelled run re-flushed
    its buffer) must replay as output_text — the API rejects input_text
    parts on assistant messages."""
    items = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "input_text", "text": "## TIME\nDay 1"}],
        }
    ]

    result = strip_internal_keys_for_api(items)
    part = result[0]["content"][0]
    assert part["type"] == "output_text"
    assert part["annotations"] == []
    assert part["text"] == "## TIME\nDay 1"


def test_strip_internal_keys_for_api_drops_phase_and_omniagents():
    items = [
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "phase": "final",
            "omniagents": {"kind": "context_summary"},
            "status": "completed",
        }
    ]

    result = strip_internal_keys_for_api(items)
    assert "phase" not in result[0]
    assert "omniagents" not in result[0]
    assert "status" not in result[0]


def test_strip_internal_keys_for_api_strips_id_for_orphaned_message():
    # Message with msg_X id but no rs_X reasoning -> id stripped.
    items = [
        {
            "type": "message",
            "id": "msg_orphan",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
        }
    ]
    result = strip_internal_keys_for_api(items)
    assert "id" not in result[0]


def test_strip_internal_keys_for_api_keeps_id_for_paired_message():
    items = [
        {"type": "reasoning", "id": "rs_x", "summary": []},
        {
            "type": "message",
            "id": "msg_x",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
        },
    ]
    result = strip_internal_keys_for_api(items)
    assert result[1]["id"] == "msg_x"


def test_strip_internal_keys_for_api_ensures_reasoning_summary():
    items = [{"type": "reasoning", "id": "rs_y"}]
    result = strip_internal_keys_for_api(items)
    assert result[0]["summary"] == []


@pytest.mark.asyncio
async def test_session_get_items_applies_filter_and_strip(monkeypatch):
    """Session.get_items() must reorder for the API and strip internal fields."""
    from omniagents.core.session.manager import Session

    sess = Session(project_slug=None, agent_slug=None)
    sess.history = [
        {"role": "user", "content": "hi"},
        # Streaming order persisted mid-group; filter keeps calls directly
        # after the reasoning and trails the message after the outputs.
        {"type": "reasoning", "id": "rs_1", "summary": [], "status": "completed"},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "search",
            "status": "completed",
        },
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "phase": "final",
            "omniagents": {"x": 1},
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "result",
            "status": "completed",
        },
    ]

    items = await sess.get_items()
    types_in_order = [it.get("type", it.get("role")) for it in items]
    assert types_in_order == [
        "user",
        "reasoning",
        "function_call",
        "function_call_output",
        "message",
    ]
    msg = items[4]
    assert "phase" not in msg
    assert "omniagents" not in msg
    assert "status" not in msg


@pytest.mark.asyncio
async def test_session_get_items_honors_legacy_compaction_marker():
    """Items before a legacy ``context_summary`` assistant marker are dropped."""
    from omniagents.core.session.manager import Session

    sess = Session(project_slug=None, agent_slug=None)
    sess.history = [
        {"role": "user", "content": "old1"},
        {"role": "user", "content": "old2"},
        {
            "role": "assistant",
            "content": "summary text",
            "omniagents": {"kind": "context_summary"},
        },
        {"role": "user", "content": "fresh"},
    ]
    items = await sess.get_items()
    assert items[0]["role"] == "assistant"
    # The omniagents key has been stripped.
    assert "omniagents" not in items[0]
    assert items[-1]["role"] == "user"
    assert items[-1]["content"] == "fresh"
