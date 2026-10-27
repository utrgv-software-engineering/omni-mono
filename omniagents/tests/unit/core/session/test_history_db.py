import concurrent.futures
import pytest
import importlib
import json
import sqlite3
import uuid

from omniagents.core.paths import get_sessions_db_path
from omniagents.core.session import Session
import omniagents.core.session.history_db as history_db_module


def test_local_session_persistence(tmp_path):
    # env and module reload handled by fixture
    sess = Session("test_project", "test_agent")
    msg = {"role": "user", "content": "hi"}
    sess.append_message(msg)

    # Reload to simulate new process
    importlib.reload(history_db_module)
    loaded = Session("test_project", "test_agent", sess.id)
    assert loaded.history == [msg]


def test_history_db_concurrent_appends_preserve_entries():
    project = "project"
    agent = "agent"
    session_id = f"session-{uuid.uuid4()}"
    history_db_module.register_session(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )
    messages = [{"role": "user", "content": f"message-{idx}"} for idx in range(40)]

    def persist(message):
        history_db_module.append_message(
            session_id,
            message,
            project_slug=project,
            agent_slug=agent,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(persist, messages))

    stored = history_db_module.load_history(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )
    assert len(stored) == len(messages)
    assert {entry["content"] for entry in stored} == {
        entry["content"] for entry in messages
    }


def test_history_db_schema_upgrade_adds_missing_columns():
    project = "legacy"
    agent = "legacy"
    db_path = get_sessions_db_path(project, agent)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, msg_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, archived INTEGER DEFAULT 0)"
        )
        conn.commit()
    finally:
        conn.close()

    history_db_module.load_history(
        "missing",
        project_slug=project,
        agent_slug=agent,
    )

    check_conn = sqlite3.connect(db_path)
    try:
        history_columns = [
            row[1] for row in check_conn.execute("PRAGMA table_info(history)")
        ]
        session_columns = [
            row[1] for row in check_conn.execute("PRAGMA table_info(sessions)")
        ]
    finally:
        check_conn.close()

    assert "created_at" in history_columns
    assert "created_at" in session_columns


def test_history_db_get_user_messages_skips_invalid_payloads():
    project = "project"
    agent = "agent"
    session_id = "session-invalid"
    history_db_module.register_session(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )
    db_path = get_sessions_db_path(project, agent)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO history(session_id, msg_json) VALUES (?, ?)",
            (session_id, "not json"),
        )
        conn.execute(
            "INSERT INTO history(session_id, msg_json) VALUES (?, ?)",
            (session_id, json.dumps(["array"])),
        )
        conn.execute(
            "INSERT INTO history(session_id, msg_json) VALUES (?, ?)",
            (session_id, json.dumps({"role": "assistant", "content": "skip"})),
        )
        conn.execute(
            "INSERT INTO history(session_id, msg_json) VALUES (?, ?)",
            (session_id, json.dumps({"role": "user", "content": "   "})),
        )
        conn.commit()
    finally:
        conn.close()

    valid = {"role": "user", "content": "keep"}
    history_db_module.append_message(
        session_id,
        valid,
        project_slug=project,
        agent_slug=agent,
    )

    messages = history_db_module.get_user_messages(
        limit=10,
        include_archived=True,
        project_slug=project,
        agent_slug=agent,
    )
    assert len(messages) == 1
    assert messages[0]["content"] == "keep"
    assert messages[0]["session_id"] == session_id


@pytest.mark.timeout(300)  # thousands of durable commits; slow on Windows
def test_history_db_get_user_messages_paginates_and_limits_results():
    project = "project"
    agent = "agent"
    session_id = "session-paginate"
    history_db_module.register_session(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )
    for idx in range(1203):
        history_db_module.append_message(
            session_id,
            {"role": "user", "content": f"message-{idx}"},
            project_slug=project,
            agent_slug=agent,
        )

    messages = history_db_module.get_user_messages(
        limit=50,
        include_archived=True,
        project_slug=project,
        agent_slug=agent,
    )
    assert len(messages) == 50
    assert messages[0]["content"] == "message-1153"
    assert messages[-1]["content"] == "message-1202"


def test_session_metadata_roundtrip():
    project = "project"
    agent = "agent"
    session_id = "session-meta"
    history_db_module.register_session(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )

    history_db_module.update_session_metadata(
        session_id,
        context={"workspace_root": "/repo"},
        variables={"workspace_root": "/repo"},
        project_slug=project,
        agent_slug=agent,
    )

    ctx, vars_data = history_db_module.get_session_metadata(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )

    assert ctx == {"workspace_root": "/repo"}
    assert vars_data == {"workspace_root": "/repo"}


def test_session_loads_metadata():
    from omniagents.core.session.manager import Session

    project = "project"
    agent = "agent"
    session_id = "session-meta-load"
    history_db_module.register_session(
        session_id,
        project_slug=project,
        agent_slug=agent,
    )
    history_db_module.update_session_metadata(
        session_id,
        context={"workspace_root": "/repo"},
        variables={"workspace_root": "/repo"},
        project_slug=project,
        agent_slug=agent,
    )

    loaded = Session(project, agent, session_id)
    assert loaded.context == {"workspace_root": "/repo"}
    assert loaded.variables == {"workspace_root": "/repo"}
