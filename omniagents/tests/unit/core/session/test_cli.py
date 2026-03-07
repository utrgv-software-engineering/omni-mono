import argparse

from omniagents.core.session import history_db
from omniagents.core.session.cli import handle_sessions_command


def test_handle_sessions_db_path(capsys):
    args = argparse.Namespace(
        command="sessions", sessions_cmd="db-path", project=None, agent=None
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert capsys.readouterr().out.strip().endswith("history.db")


def test_handle_sessions_list_json(capsys):
    history_db.register_session("session-1", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-1",
        {"role": "user", "content": "hello"},
        project_slug=None,
        agent_slug=None,
    )
    args = argparse.Namespace(
        command="sessions",
        sessions_cmd="list",
        project=None,
        agent=None,
        include_archived=True,
        after=None,
        before=None,
        offset=0,
        limit=20,
        all=False,
        stats=True,
        text=False,
        pretty=False,
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert "session-1" in capsys.readouterr().out


def test_handle_sessions_search_text(capsys):
    history_db.register_session("session-2", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-2",
        {"role": "user", "content": "search needle"},
        project_slug=None,
        agent_slug=None,
    )
    args = argparse.Namespace(
        command="sessions",
        sessions_cmd="search",
        query="needle",
        project=None,
        agent=None,
        role="user",
        include_archived=True,
        after=None,
        before=None,
        offset=0,
        limit=20,
        text=True,
        pretty=False,
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert "search needle" in capsys.readouterr().out


def test_handle_sessions_export_jsonl(capsys):
    history_db.register_session("session-3", project_slug=None, agent_slug=None)
    history_db.append_message(
        "session-3",
        {"role": "user", "content": "export me"},
        project_slug=None,
        agent_slug=None,
    )
    args = argparse.Namespace(
        command="sessions",
        sessions_cmd="export",
        session_id="session-3",
        project=None,
        agent=None,
        format="jsonl",
        pretty=False,
    )

    code = handle_sessions_command(args)

    assert code == 0
    assert '"content":"export me"' in capsys.readouterr().out
