import asyncio
import uuid

import pytest

from omniagents.core.session.manager import SessionManager


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
