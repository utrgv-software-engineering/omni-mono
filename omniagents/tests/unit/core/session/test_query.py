from omniagents.core.session import history_db
from omniagents.core.session.query import (
    count_sessions,
    parse_datetime_arg,
    populate_session_stats,
    query_sessions,
    search_session_messages,
)


def test_parse_datetime_arg_normalizes_zulu():
    assert parse_datetime_arg("2026-02-18T15:04:05Z") == "2026-02-18 15:04:05"


def test_query_sessions_and_stats_roundtrip():
    project = "query-project"
    agent = "query-agent"
    history_db.register_session("session-a", project_slug=project, agent_slug=agent)
    history_db.register_session("session-b", project_slug=project, agent_slug=agent)
    history_db.append_message(
        "session-a",
        {"role": "user", "content": "hello from a"},
        project_slug=project,
        agent_slug=agent,
    )
    history_db.append_message(
        "session-a",
        {"role": "assistant", "content": "reply a"},
        project_slug=project,
        agent_slug=agent,
    )
    history_db.append_message(
        "session-b",
        {"role": "user", "content": "hello from b"},
        project_slug=project,
        agent_slug=agent,
    )

    sessions = query_sessions(
        project=project,
        agent=agent,
        include_archived=True,
        after=None,
        before=None,
        limit=10,
        offset=0,
    )

    assert len(sessions) == 2
    populate_session_stats(project=project, agent=agent, sessions=sessions)
    indexed = {session["id"]: session for session in sessions}
    assert indexed["session-a"]["message_count"] == 2
    assert indexed["session-a"]["first_message"]["content"] == "hello from a"
    assert indexed["session-a"]["last_message"]["content"] == "reply a"
    assert (
        count_sessions(
            project=project,
            agent=agent,
            include_archived=True,
            after=None,
            before=None,
        )
        == 2
    )


def test_search_session_messages_filters_role_and_offset():
    project = "search-project"
    agent = "search-agent"
    history_db.register_session(
        "session-search", project_slug=project, agent_slug=agent
    )
    history_db.append_message(
        "session-search",
        {"role": "user", "content": "alpha one"},
        project_slug=project,
        agent_slug=agent,
    )
    history_db.append_message(
        "session-search",
        {"role": "assistant", "content": "alpha two"},
        project_slug=project,
        agent_slug=agent,
    )
    history_db.append_message(
        "session-search",
        {"role": "user", "content": "alpha three"},
        project_slug=project,
        agent_slug=agent,
    )

    results = search_session_messages(
        project=project,
        agent=agent,
        query="alpha",
        role="user",
        include_archived=True,
        limit=10,
        offset=1,
    )

    assert len(results) == 1
    assert results[0]["content"] == "alpha one"
