import sqlite3

import pytest

from omniagents.integrations.power_automate import PowerAutomateSource, normalize_event
from omniagents.integrations.store import EventStore
from .test_power_automate import config, message


def completed(store, delivery="one", status="completed"):
    event = normalize_event("intake", delivery, message(), config())
    event_id = store.record(event)
    dispatch = store.start_dispatch(event_id, "teams", "assistant", "session")
    store.finish_dispatch(dispatch, status, output="answer")
    return dispatch


@pytest.mark.asyncio
async def test_handoff_callback_and_recovery_share_reply_identity(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    store = EventStore(tmp_path / "events.db")
    event = replace(
        normalize_event("intake", "one", message(), config()),
        delivery_id="hand-off-unique",
    )
    dispatch = store.start_dispatch(
        store.record(event), "teams", "assistant", "session"
    )
    store.finish_dispatch(dispatch, "completed", output="answer")
    source = PowerAutomateSource(config(), None, tmp_path / "replies.db", store)
    try:
        await source.finished(
            event, SimpleNamespace(output="answer", error=None, stopped=False)
        )
        source.recover_replies()
        assert len(source.spool.pending()) == 1
        assert not store.pending_bridge_replies()
    finally:
        await source.stop()
        store.close()


def test_completion_and_output_are_atomic(tmp_path):
    store = EventStore(tmp_path / "events.db")
    try:
        event = normalize_event("intake", "one", message(), config())
        dispatch = store.start_dispatch(
            store.record(event), "teams", "assistant", "session"
        )
        store._db.execute(
            "CREATE TRIGGER fail_output BEFORE INSERT ON run_outputs BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.finish_dispatch(dispatch, "completed", output="answer")
        assert (
            store._db.execute(
                "SELECT status FROM dispatches WHERE id=?", (dispatch,)
            ).fetchone()[0]
            == "running"
        )
        assert not store.pending_bridge_replies()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_recovery_survives_restart_and_skips_failed_runs(tmp_path):
    store = EventStore(tmp_path / "events.db")
    completed(store)
    completed(store, "failed", "failed")
    source = PowerAutomateSource(config(), None, tmp_path / "replies.db", store)
    source.recover_replies()
    assert len(source.spool.pending()) == 1
    await source.stop()
    source = PowerAutomateSource(config(), None, tmp_path / "replies.db", store)
    try:
        source.recover_replies()
        assert len(source.spool.pending()) == 1
        assert not store.pending_bridge_replies()
    finally:
        await source.stop()
        store.close()


@pytest.mark.asyncio
async def test_recovery_revalidates_revoked_policy(tmp_path):
    store = EventStore(tmp_path / "events.db")
    completed(store)
    configuration = config()
    configuration.sources["intake"]["users"] = []
    source = PowerAutomateSource(configuration, None, tmp_path / "replies.db", store)
    try:
        source.recover_replies()
        assert not source.spool.pending()
        assert (
            store._db.execute("SELECT outcome FROM bridge_reply_receipts").fetchone()[0]
            == "suppressed"
        )
    finally:
        await source.stop()
        store.close()


@pytest.mark.asyncio
async def test_spool_failure_does_not_acknowledge_recovery(tmp_path, monkeypatch):
    store = EventStore(tmp_path / "events.db")
    completed(store)
    source = PowerAutomateSource(config(), None, tmp_path / "replies.db", store)

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(source.spool, "enqueue", fail)
    try:
        with pytest.raises(OSError):
            source.recover_replies()
        assert len(store.pending_bridge_replies()) == 1
    finally:
        await source.stop()
        store.close()


@pytest.mark.parametrize(
    "key,value",
    [
        ("maintenance_seconds", 0),
        ("action_timeout_seconds", True),
        ("transfer_expiry_seconds", -1),
    ],
)
def test_maintenance_config_requires_bounded_integers(key, value):
    from omniagents.integrations.power_automate import PowerAutomateConfig

    with pytest.raises(ValueError):
        PowerAutomateConfig.parse({"sources": config().sources, key: value})


@pytest.mark.asyncio
async def test_poll_loop_runs_recovery_and_periodic_maintenance(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from omniagents.integrations import power_automate_maintenance

    source = PowerAutomateSource(config(), None, tmp_path / "replies.db")
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    source.connect = AsyncMock(return_value=connection)
    source.acquire_delivery_lease = AsyncMock()
    source.recover_replies = Mock()
    source.flush_replies = AsyncMock()
    source.deliver = AsyncMock()
    maintenance = AsyncMock()
    monkeypatch.setattr(power_automate_maintenance, "maintain", maintenance)
    sleeps = 0

    async def pause(delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", pause)
    try:
        with pytest.raises(asyncio.CancelledError):
            await source.run()
        assert source.recover_replies.call_count == 2
        assert source.deliver.await_count == 2
        maintenance.assert_awaited_once_with(connection, source.config)
    finally:
        await source.stop()
