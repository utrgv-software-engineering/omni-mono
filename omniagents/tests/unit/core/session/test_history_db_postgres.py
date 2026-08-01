"""PG-backed history_db tests.

Skipped unless ``OMNIAGENTS_TEST_PG_URL`` is set. Each test runs under a
unique ``tenant_id`` so concurrent runs against a shared DB don't collide;
the fixture teardown deletes that tenant's rows.

Run locally:

    docker run --rm -d --name pg-test -p 55432:5432 \\
        -e POSTGRES_PASSWORD=test postgres:16
    OMNIAGENTS_TEST_PG_URL=postgresql://postgres:test@localhost:55432/postgres \\
        pytest tests/unit/core/session/test_history_db_postgres.py
"""

from __future__ import annotations

import importlib
import os
import uuid

import pytest

PG_URL = os.getenv("OMNIAGENTS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="OMNIAGENTS_TEST_PG_URL not set")


@pytest.fixture
def pg_history_db(monkeypatch):
    """Configure the PG backend for one test under a fresh tenant.

    Runs AFTER the autouse ``temp_history_db`` fixture in conftest.py, so
    it overrides that fixture's sqlite env before any backend method is
    called. The post-test reload + tenant-row delete keeps the DB clean
    across runs."""
    tenant = f"test-{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("OMNIAGENTS_HISTORY_BACKEND", "postgres")
    monkeypatch.setenv("OMNIAGENTS_HISTORY_URL", PG_URL)
    monkeypatch.setenv("OMNIAGENTS_TENANT_ID", tenant)

    import omniagents.core.session.history_db as history_db

    importlib.reload(history_db)

    yield history_db

    # Wipe the test tenant's data, then close the pool so the next test
    # gets a fresh backend instance.
    backend = history_db._resolve_backend()
    with backend._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audio_metadata WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM history WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM sessions WHERE tenant_id = %s", (tenant,))
        conn.commit()
    backend.close()


def test_register_and_load_history(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    history_db.append_message(
        sid, {"role": "user", "content": "hi"}, project_slug="p", agent_slug="a"
    )
    history_db.append_message(
        sid,
        {"role": "assistant", "content": "hello"},
        project_slug="p",
        agent_slug="a",
    )
    loaded = history_db.load_history(sid, project_slug="p", agent_slug="a")
    assert loaded == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_register_session_is_idempotent(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a", user_id="u1")
    # Second register with a different user_id should NOT overwrite —
    # matches sqlite's INSERT OR IGNORE behaviour.
    history_db.register_session(sid, project_slug="p", agent_slug="a", user_id="u2")
    history_db.update_session_user_id(sid, "u3", project_slug="p", agent_slug="a")
    sessions = history_db.list_sessions_for_user("u3", project_slug="p", agent_slug="a")
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid


def test_metadata_round_trip(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    history_db.update_session_metadata(
        sid,
        context={"goal": "ship it"},
        variables={"counter": 3},
        project_slug="p",
        agent_slug="a",
    )
    ctx, vars_ = history_db.get_session_metadata(sid, project_slug="p", agent_slug="a")
    assert ctx == {"goal": "ship it"}
    assert vars_ == {"counter": 3}


def test_archive_and_hold(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    assert history_db.get_archived(sid, project_slug="p", agent_slug="a") is False

    history_db.set_archived(sid, True, project_slug="p", agent_slug="a")
    assert history_db.get_archived(sid, project_slug="p", agent_slug="a") is True

    history_db.set_session_hold(sid, True, project_slug="p", agent_slug="a")
    assert history_db.get_session_hold(sid, project_slug="p", agent_slug="a") is True

    with pytest.raises(ValueError, match="legal hold"):
        history_db.delete_session(sid, project_slug="p", agent_slug="a")


def test_delete_session_cascades(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    history_db.append_message(
        sid, {"role": "user", "content": "x"}, project_slug="p", agent_slug="a"
    )
    history_db.save_audio_metadata(
        sid,
        "item-1",
        0,
        "f.pcm16",
        "pcm16",
        duration_ms=42,
        project_slug="p",
        agent_slug="a",
    )

    assert history_db.delete_session(sid, project_slug="p", agent_slug="a") is True
    assert history_db.load_history(sid, project_slug="p", agent_slug="a") == []
    assert history_db.get_audio_metadata(sid, project_slug="p", agent_slug="a") == []
    # Second delete returns False — session no longer exists.
    assert history_db.delete_session(sid, project_slug="p", agent_slug="a") is False


def test_list_sessions_shape(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    history_db.append_message(
        sid,
        {"role": "user", "content": "first thing"},
        project_slug="p",
        agent_slug="a",
    )
    history_db.append_message(
        sid,
        {"role": "assistant", "content": "last thing"},
        project_slug="p",
        agent_slug="a",
    )
    sessions = history_db.list_sessions(project_slug="p", agent_slug="a")
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == sid
    assert s["message_count"] == 2
    assert s["first_message"]["content"] == "first thing"
    assert s["last_message"]["content"] == "last thing"
    # created_at should be an ISO string (datetime → _iso conversion).
    assert isinstance(s["created_at"], str) and "T" in s["created_at"]


def test_get_user_messages_filters(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    history_db.append_message(
        sid, {"role": "user", "content": "alpha"}, project_slug="p", agent_slug="a"
    )
    history_db.append_message(
        sid,
        {"role": "assistant", "content": "skip me"},
        project_slug="p",
        agent_slug="a",
    )
    history_db.append_message(
        sid, {"role": "user", "content": "beta"}, project_slug="p", agent_slug="a"
    )
    history_db.append_message(
        sid,
        {"role": "user", "content": [{"type": "input_text", "text": "skip-list"}]},
        project_slug="p",
        agent_slug="a",
    )

    msgs = history_db.get_user_messages(limit=10, project_slug="p", agent_slug="a")
    # Only the two string-content user messages — assistant + list-content
    # should be filtered out. Results are reversed to oldest-first.
    assert [m["content"] for m in msgs] == ["alpha", "beta"]


def test_audio_metadata_round_trip(pg_history_db):
    history_db = pg_history_db
    sid = uuid.uuid4().hex
    history_db.register_session(sid, project_slug="p", agent_slug="a")
    row_id = history_db.save_audio_metadata(
        sid,
        "item-1",
        0,
        "audio.pcm16",
        "pcm16",
        duration_ms=100,
        project_slug="p",
        agent_slug="a",
    )
    assert row_id > 0

    rows = history_db.get_audio_metadata(sid, project_slug="p", agent_slug="a")
    assert len(rows) == 1
    assert rows[0]["item_id"] == "item-1"
    assert rows[0]["file_path"] == "audio.pcm16"

    by_item = history_db.get_audio_metadata_by_item(
        "item-1", 0, project_slug="p", agent_slug="a"
    )
    assert by_item is not None
    assert by_item["session_id"] == sid

    deleted = history_db.delete_audio_metadata(sid, project_slug="p", agent_slug="a")
    assert deleted == 1
    assert history_db.get_audio_metadata(sid, project_slug="p", agent_slug="a") == []


def test_purge_old_sessions_respects_hold(pg_history_db):
    """purge_old_sessions skips held sessions even when they're past the
    retention window. We can't easily backdate created_at without raw SQL,
    so use a 0-day window after writing a row + set_session_hold."""
    history_db = pg_history_db
    held = uuid.uuid4().hex
    purgeable = uuid.uuid4().hex
    history_db.register_session(held, project_slug="p", agent_slug="a")
    history_db.register_session(purgeable, project_slug="p", agent_slug="a")
    history_db.set_session_hold(held, True, project_slug="p", agent_slug="a")

    # Hand-roll the backdate via the backend's pool — no public API.
    backend = history_db._resolve_backend()
    with backend._conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET created_at = now() - interval '30 days' "
                "WHERE tenant_id = %s",
                (conn.tenant_id,),
            )
        conn.commit()

    purged = history_db.purge_old_sessions(7, project_slug="p", agent_slug="a")
    # The purged IDS, not a count: a caller told "1 session was purged"
    # cannot go and clear that session's domain rows and replay journal.
    assert purged == [purgeable]
    assert (
        history_db.get_archived(held, project_slug="p", agent_slug="a") is False
    )  # held survives
    assert history_db.get_session_hold(held, project_slug="p", agent_slug="a") is True
