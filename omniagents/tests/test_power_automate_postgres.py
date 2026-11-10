import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from omniagents.integrations.power_automate import (
    PowerAutomateConfig,
    PowerAutomateSource,
)
from omniagents.integrations.power_automate_files import process_one
from .test_power_automate import config, message
from .test_power_automate_actions import bridge_context

DSN = os.environ.get("OMNI_BRIDGE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="requires a disposable PostgreSQL database"
)


@pytest.fixture(scope="module", autouse=True)
def schema():
    if not DSN:
        return
    root = Path(__file__).parents[1] / "omniagents/integrations"
    with psycopg.connect(DSN, autocommit=True) as connection:
        if os.environ.get("OMNI_BRIDGE_TEST_RESET") == "1":
            connection.execute("DROP SCHEMA IF EXISTS omni_bridge CASCADE")
        connection.execute((root / "power_automate.sql").read_text())
        roles_sql = (root / "power_automate_roles.sql").read_text()
        for role in ("omni_bridge_worker", "omni_bridge_flow"):
            if connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone():
                roles_sql = roles_sql.replace(f"CREATE ROLE {role} NOLOGIN;", "")
        connection.execute(roles_sql)
        if not connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='intake'"
        ).fetchone():
            connection.execute("CREATE ROLE intake LOGIN")
        connection.execute("GRANT omni_bridge_flow TO intake")


@pytest.fixture
def connection():
    with psycopg.connect(DSN, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE omni_bridge.inbound_events, omni_bridge.outbound_actions, "
            "omni_bridge.attachment_chunks, omni_bridge.attachments CASCADE"
        )
        yield connection


def insert_event(connection, identity, payload, source="intake"):
    connection.execute(
        "INSERT INTO omni_bridge.inbound_events (id,source,payload) VALUES (%s,%s,%s)",
        (identity, source, json.dumps(payload)),
    )


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "uncertain"])
def test_connector_required_action_fields(connection, terminal):
    connection.execute(
        "INSERT INTO omni_bridge.outbound_actions (id,source,operation,payload) "
        "VALUES ('connector','intake','teams.reply','{}')"
    )
    connection.execute("SET ROLE intake")
    try:
        for state in ("sending", terminal):
            row = connection.execute(
                "SELECT source,operation,payload,created_at,state_changed_at "
                "FROM omni_bridge.outbound_actions WHERE id='connector'"
            ).fetchone()
            updated = connection.execute(
                "UPDATE omni_bridge.outbound_actions SET source=%s,operation=%s,"
                "payload=%s,created_at=%s,state_changed_at=%s,state=%s "
                "WHERE id='connector' RETURNING state,state_changed_at",
                (*row, state),
            ).fetchone()
            assert updated[0] == state
            assert updated[1] > row[4]
    finally:
        connection.execute("RESET ROLE")


@pytest.mark.parametrize(
    "assignment",
    [
        "source='other'",
        "operation='outlook.send_email'",
        "payload='{\"changed\":true}'",
        "created_at=created_at-interval '1 day'",
        "state_changed_at=state_changed_at-interval '1 day'",
        "created_at=date_trunc('milliseconds',created_at),"
        "state_changed_at=date_trunc('milliseconds',state_changed_at)",
        "source=NULL,operation=NULL,payload=NULL,created_at=NULL,state_changed_at=NULL",
    ],
)
def test_connector_action_metadata_immutable(connection, assignment):
    connection.execute(
        "INSERT INTO omni_bridge.outbound_actions (id,source,operation,payload) "
        "VALUES ('protected','intake','teams.reply','{}')"
    )
    connection.execute("SET ROLE intake")
    try:
        before = connection.execute(
            "SELECT id,source,operation,payload,agent,session_id,created_at "
            "FROM omni_bridge.outbound_actions WHERE id='protected'"
        ).fetchone()
        connection.execute(
            "UPDATE omni_bridge.outbound_actions SET state='sending',"
            + assignment
            + " WHERE id='protected'"
        )
        after = connection.execute(
            "SELECT id,source,operation,payload,agent,session_id,created_at "
            "FROM omni_bridge.outbound_actions WHERE id='protected'"
        ).fetchone()
        assert after == before
        state, changed_at = connection.execute(
            "SELECT state,state_changed_at FROM omni_bridge.outbound_actions "
            "WHERE id='protected'"
        ).fetchone()
        assert state == "sending"
        assert changed_at >= before[-1]
    finally:
        connection.execute("RESET ROLE")


def teams_payload(**updates):
    payload = message(**updates)
    raw = {
        "id": payload["message_id"],
        "replyToId": None,
        "channelIdentity": {
            "teamId": payload["team_id"],
            "channelId": payload["channel_id"],
        },
        "body": {"content": payload["text"]},
    }
    return {**payload, "thread_history": {"root": raw, "replies": []}}


@pytest.mark.asyncio
async def test_durable_accept_and_poison_rejection(connection, tmp_path):
    for identity, body in [
        ("good", teams_payload()),
        ("bad", teams_payload(actor_id="other")),
    ]:
        insert_event(connection, identity, body)
    accepted = []

    async def accept(event):
        accepted.append(event)

    source = PowerAutomateSource(config(), accept, tmp_path / "replies.db")
    async with await psycopg.AsyncConnection.connect(DSN) as database:
        await source.deliver(database)
        await source.deliver(database)
    assert len(accepted) == 1
    assert connection.execute(
        "SELECT accepted_at IS NOT NULL FROM omni_bridge.inbound_events WHERE id='good'"
    ).fetchone()[0]
    assert connection.execute(
        "SELECT rejected_at IS NOT NULL FROM omni_bridge.inbound_events WHERE id='bad'"
    ).fetchone()[0]
    await source.stop()


@pytest.mark.asyncio
async def test_failed_intake_keeps_event_pending(connection, tmp_path):
    insert_event(connection, "good", teams_payload())

    async def fail(event):
        raise OSError("intake unavailable")

    source = PowerAutomateSource(config(), fail, tmp_path / "replies.db")
    async with await psycopg.AsyncConnection.connect(DSN) as database:
        with pytest.raises(OSError):
            await source.deliver(database)
    assert connection.execute(
        "SELECT accepted_at,rejected_at,error_code "
        "FROM omni_bridge.inbound_events WHERE id='good'"
    ).fetchone() == (None, None, None)
    await source.stop()


def test_flow_cannot_spoof_source_or_acknowledge_events(connection):
    connection.execute("SET ROLE intake")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "INSERT INTO omni_bridge.inbound_events (id,source,payload) VALUES ('spoof','other','{}')"
            )
        connection.execute(
            "INSERT INTO omni_bridge.inbound_events (id,source,payload) VALUES ('own','intake','{}')"
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "UPDATE omni_bridge.inbound_events SET accepted_at=now() WHERE id='own'"
            )
    finally:
        connection.execute("RESET ROLE")


@pytest.mark.asyncio
async def test_atomic_json_without_manifest_tables(connection, tmp_path):
    payload = {
        "sensor": {"readings": list(range(100))},
        "thread_history": {"arbitrary": True},
    }
    insert_event(connection, "one", payload)
    insert_event(connection, "foreign", {}, source="other")
    accepted = []

    async def accept(event):
        accepted.append(event)

    source = PowerAutomateSource(
        PowerAutomateConfig.parse({"sources": {"intake": {"handler": "json"}}}),
        accept,
        tmp_path / "replies.db",
    )
    try:
        async with await psycopg.AsyncConnection.connect(DSN) as database:
            await source.deliver(database)
            await source.deliver(database)
        assert len(accepted) == 1
        assert accepted[0].payload == payload
        assert connection.execute(
            "SELECT accepted_at FROM omni_bridge.inbound_events WHERE id='foreign'"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT to_regclass('omni_bridge.delivery_manifests')"
        ).fetchone() == (None,)
    finally:
        await source.stop()


@pytest.mark.asyncio
async def test_reply_forwarding_is_idempotent(connection, tmp_path):
    from omniagents.integrations.power_automate import normalize_event

    source = PowerAutomateSource(config(), None, tmp_path / "replies.db")
    event = normalize_event("intake", "one", message(), config())
    result = SimpleNamespace(output="answer", error=None, stopped=False)
    async with await psycopg.AsyncConnection.connect(DSN) as database:
        source.spool.enqueue(event, result)
        await source.flush_replies(database)
        source.spool.enqueue(event, result)
        await source.flush_replies(database)
    assert not source.spool.pending()
    assert (
        connection.execute(
            "SELECT count(*) FROM omni_bridge.outbound_actions"
        ).fetchone()[0]
        == 1
    )
    await source.stop()


def test_attachment_upload_and_chunk_cleanup(connection):
    identity = uuid4()
    connection.execute("SET ROLE intake")
    try:
        connection.execute(
            "INSERT INTO omni_bridge.attachments (id,source,filename,content_type,byte_size,chunk_count) VALUES (%s,'intake','file.txt','text/plain',3,1)",
            (identity,),
        )
        connection.execute(
            "INSERT INTO omni_bridge.attachment_chunks VALUES (%s,%s,0,%s)",
            (str(identity) + ":0", identity, base64.b64encode(b"abc").decode()),
        )
        connection.execute(
            "UPDATE omni_bridge.attachments SET state='complete' WHERE id=%s",
            (identity,),
        )
        with pytest.raises(psycopg.Error):
            connection.execute(
                "INSERT INTO omni_bridge.attachment_chunks VALUES (%s,%s,1,%s)",
                (str(identity) + ":1", identity, "YWJj"),
            )
        with pytest.raises(psycopg.Error):
            connection.execute(
                "UPDATE omni_bridge.attachments SET state='ready' WHERE id=%s",
                (identity,),
            )
    finally:
        connection.execute("RESET ROLE")
    uploads = []

    def upload(stream, **kwargs):
        uploads.append((stream.read(), kwargs))

    container = SimpleNamespace(
        get_blob_client=lambda name: SimpleNamespace(upload_blob=upload)
    )
    assert process_one(connection, container)
    assert uploads[0][0] == b"abc"
    assert (
        connection.execute(
            "SELECT state FROM omni_bridge.attachments WHERE id=%s", (identity,)
        ).fetchone()[0]
        == "ready"
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM omni_bridge.attachment_chunks"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.asyncio
async def test_generic_actions_are_durable_scoped_and_idempotent(connection, tmp_path):
    actions = bridge_context(tmp_path)

    async def connect():
        return await psycopg.AsyncConnection.connect(DSN)

    actions.source.connect = connect
    try:
        payload = {"site_id": "site", "library_id": "library", "file_id": "file"}
        first = await actions.request(
            "assistant", "session", "sharepoint.get_file", payload, "request-one"
        )
        assert first == await actions.request(
            "assistant", "session", "sharepoint.get_file", payload, "request-one"
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM omni_bridge.outbound_actions"
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(ValueError, match="idempotency_conflict"):
            await actions.request(
                "assistant",
                "session",
                "sharepoint.get_file",
                {**payload, "file_id": "different"},
                "request-one",
            )
        assert (await actions.status("assistant", "session", first["action_id"]))[
            "state"
        ] == "pending"
        with pytest.raises(ValueError):
            await actions.status("assistant", "other-session", first["action_id"])
        attachment_id = str(uuid4())
        with pytest.raises(ValueError, match="attachment_not_in_conversation"):
            await actions.authorize_attachment("assistant", "session", attachment_id)
        connection.execute(
            "UPDATE omni_bridge.outbound_actions SET state='sending' WHERE id=%s",
            (first["action_id"],),
        )
        connection.execute(
            "UPDATE omni_bridge.outbound_actions SET state='succeeded', result='retrieved', result_attachment_id=%s WHERE id=%s",
            (attachment_id, first["action_id"]),
        )
        assert await actions.authorize_attachment(
            "assistant", "session", attachment_id
        ) == ("intake", attachment_id)
        assert (await actions.status("assistant", "session", first["action_id"]))[
            "attachment_id"
        ] == attachment_id
        assert (await actions.status("assistant", "session", first["action_id"]))[
            "result"
        ] == "retrieved"
    finally:
        actions.event_store.close()


@pytest.mark.asyncio
async def test_reply_recovery_after_lost_receipt_and_restart(
    connection, tmp_path, monkeypatch
):
    from omniagents.integrations.store import EventStore
    from .test_power_automate_recovery import completed

    store = EventStore(tmp_path / "events.db")
    completed(store)
    source = PowerAutomateSource(config(), None, tmp_path / "replies.db", store)
    original = store.acknowledge_bridge_reply

    def fail(*args):
        raise OSError("crash after spool commit")

    monkeypatch.setattr(store, "acknowledge_bridge_reply", fail)
    with pytest.raises(OSError):
        source.recover_replies()
    async with await psycopg.AsyncConnection.connect(DSN) as database:
        await source.flush_replies(database)
    await source.stop()
    monkeypatch.setattr(store, "acknowledge_bridge_reply", original)
    source = PowerAutomateSource(config(), None, tmp_path / "replies.db", store)
    try:
        source.recover_replies()
        async with await psycopg.AsyncConnection.connect(DSN) as database:
            await source.flush_replies(database)
        assert (
            connection.execute(
                "SELECT count(*) FROM omni_bridge.outbound_actions"
            ).fetchone()[0]
            == 1
        )
        assert not store.pending_bridge_replies()
    finally:
        await source.stop()
        store.close()


@pytest.mark.asyncio
async def test_maintenance_quarantines_only_stale_authorized_actions(connection):
    from omniagents.integrations.power_automate_maintenance import maintain

    for identity, source in [
        ("stale", "intake"),
        ("fresh", "intake"),
        ("other", "other"),
        ("pending", "intake"),
        ("done", "intake"),
    ]:
        connection.execute(
            "INSERT INTO omni_bridge.outbound_actions (id,source,operation,payload) VALUES (%s,%s,'teams.reply','{}')",
            (identity, source),
        )
    connection.execute(
        "UPDATE omni_bridge.outbound_actions SET state='sending' WHERE id != 'pending'"
    )
    connection.execute(
        "UPDATE omni_bridge.outbound_actions SET state='succeeded' WHERE id='done'"
    )
    connection.execute(
        "UPDATE omni_bridge.outbound_actions SET state_changed_at=now()-interval '2 hours' WHERE id != 'fresh'"
    )
    async with await psycopg.AsyncConnection.connect(DSN) as database:
        counts = await maintain(database, config())
        assert counts["uncertain_actions"] == 1
        assert (await maintain(database, config()))["uncertain_actions"] == 0
    states = dict(
        connection.execute("SELECT id,state FROM omni_bridge.outbound_actions")
    )
    assert states == {
        "stale": "uncertain",
        "fresh": "sending",
        "other": "sending",
        "pending": "pending",
        "done": "succeeded",
    }
    connection.execute("SET ROLE intake")
    try:
        for statement in [
            "UPDATE omni_bridge.outbound_actions SET state='pending' WHERE id='stale'",
            "UPDATE omni_bridge.outbound_actions SET state='succeeded' WHERE id='stale'",
            "UPDATE omni_bridge.outbound_actions SET state='sending' WHERE id='fresh'",
            "UPDATE omni_bridge.outbound_actions SET state_changed_at=now() WHERE id='fresh'",
            "UPDATE omni_bridge.outbound_actions SET state='succeeded' WHERE id='pending'",
        ]:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)
        connection.execute(
            "UPDATE omni_bridge.outbound_actions SET state='sending' WHERE id='pending'"
        )
        assert connection.execute(
            "SELECT state_changed_at > now()-interval '1 minute' FROM omni_bridge.outbound_actions WHERE id='pending'"
        ).fetchone()[0]
        connection.execute(
            "UPDATE omni_bridge.outbound_actions SET state='succeeded',result='sent' WHERE id='pending'"
        )
    finally:
        connection.execute("RESET ROLE")
    connection.execute(
        "UPDATE omni_bridge.outbound_actions SET state='succeeded',result='operator verified delivery' WHERE id='stale'"
    )


@pytest.mark.asyncio
async def test_transfer_cleanup_preserves_fresh_ready_foreign_and_locked(connection):
    from omniagents.integrations.power_automate_maintenance import maintain

    identities = {}
    for label in [
        "expired",
        "complete",
        "fresh",
        "ready",
        "other",
        "locked",
        "rejected",
    ]:
        identity = uuid4()
        identities[label] = identity
        connection.execute(
            "INSERT INTO omni_bridge.attachments (id,source,filename,content_type,byte_size,chunk_count) VALUES (%s,%s,'file','text/plain',3,1)",
            (identity, "other" if label == "other" else "intake"),
        )
        connection.execute(
            "INSERT INTO omni_bridge.attachment_chunks VALUES (%s,%s,0,'YWJj')",
            (str(identity), identity),
        )
        if label in ("complete", "ready", "rejected"):
            connection.execute(
                "UPDATE omni_bridge.attachments SET state=%s WHERE id=%s",
                (label, identity),
            )
        if label != "fresh":
            connection.execute(
                "UPDATE omni_bridge.attachments SET state_changed_at=now()-interval '8 days' WHERE id=%s",
                (identity,),
            )
    with psycopg.connect(DSN) as locker:
        locker.execute(
            "SELECT id FROM omni_bridge.attachments WHERE id=%s FOR UPDATE",
            (identities["locked"],),
        )
        async with await psycopg.AsyncConnection.connect(DSN) as database:
            counts = await maintain(database, config())
            assert counts["cleaned_transfers"] == 3
    remaining = {
        row[0]
        for row in connection.execute(
            "SELECT attachment_id FROM omni_bridge.attachment_chunks"
        )
    }
    assert remaining == {
        identities[label] for label in ("fresh", "ready", "other", "locked")
    }
    assert (
        connection.execute("SELECT count(*) FROM omni_bridge.attachments").fetchone()[0]
        == 7
    )
    assert connection.execute(
        "SELECT state,error_code FROM omni_bridge.attachments WHERE id=%s",
        (identities["expired"],),
    ).fetchone() == ("rejected", "transfer_expired")
    connection.execute("SET ROLE intake")
    try:
        with pytest.raises(psycopg.Error):
            connection.execute(
                "INSERT INTO omni_bridge.attachment_chunks VALUES ('late',%s,0,'YWJj')",
                (identities["expired"],),
            )
    finally:
        connection.execute("RESET ROLE")


@pytest.mark.asyncio
async def test_rollout_cutoff_leaves_old_rows_untouched(connection, tmp_path):
    insert_event(connection, "old", {})
    connection.execute(
        "UPDATE omni_bridge.inbound_events SET created_at='2020-01-01' WHERE id='old'"
    )
    insert_event(connection, "fresh", {"ready": True})
    accepted = []

    async def accept(event):
        accepted.append(event)

    settings = PowerAutomateConfig.parse(
        {
            "not_before": "2026-01-01T00:00:00Z",
            "sources": {"intake": {"handler": "json"}},
        }
    )
    source = PowerAutomateSource(settings, accept, tmp_path / "cutoff.db")
    try:
        async with await psycopg.AsyncConnection.connect(DSN) as database:
            await source.deliver(database)
        assert len(accepted) == 1
        assert accepted[0].payload == {"ready": True}
        assert connection.execute(
            "SELECT accepted_at,rejected_at FROM omni_bridge.inbound_events WHERE id='old'"
        ).fetchone() == (None, None)
    finally:
        await source.stop()
