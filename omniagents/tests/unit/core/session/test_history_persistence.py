import importlib

import omniagents.core.session.history_db as history_db_module
import omniagents.core.session.manager as session_manager_module


def test_session_history_persistence_and_archival_flags(temp_history_db):
    manager = session_manager_module.SessionManager("project", "agent")
    session = manager.get_or_create()
    first_message = {"role": "user", "content": "hello"}
    second_message = {"role": "assistant", "content": "hi"}
    session.append_message(first_message)
    session.append_message(second_message)

    archived = manager.archive_session(session.id)
    assert archived is True

    expected_history = [dict(first_message), dict(second_message)]

    importlib.reload(history_db_module)
    importlib.reload(session_manager_module)

    reloaded_manager = session_manager_module.SessionManager("project", "agent")
    reloaded_session = reloaded_manager.get_or_create(session.id)

    assert reloaded_session.history == expected_history
    assert reloaded_session.archived is True

    user_messages = reloaded_manager.get_user_messages(10, True)
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "hello"
    assert user_messages[0]["session_id"] == session.id

    filtered_messages = reloaded_manager.get_user_messages(10, False)
    assert filtered_messages == []

    stored_history = history_db_module.load_history(
        session.id, project_slug="project", agent_slug="agent"
    )
    assert stored_history == expected_history
