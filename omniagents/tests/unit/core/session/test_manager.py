import asyncio

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
    cancelled = manager.cancel("run-bind")
    assert cancelled is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.current_task is None


@pytest.mark.asyncio
async def test_cancel_handles_missing_tasks():
    manager = make_manager()
    assert manager.cancel("missing") is False
    session = manager.get_or_create()
    manager._task_map["orphaned"] = session
    assert manager.cancel("orphaned") is False


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
