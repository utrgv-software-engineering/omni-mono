import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from omniagents.integrations.factory_settings import (
    FactorySettings,
    mount_factory_settings,
)
from omniagents.integrations.power_automate import PowerAutomateSource
from omniagents.integrations.power_automate_policy import activation_scope
from .test_power_automate_threads import channel_config, delivery


def changed(settings, marker="@research"):
    value = settings.snapshot()
    value["revision"] += 1
    value["display_name"] = "Research Factory"
    for item in value["sources"]:
        item["marker"] = marker
    return value


@pytest.mark.asyncio
async def test_marker_change_preserves_threads_replies_and_restart(tmp_path):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    await source.accept_delivery(delivery())
    event = accept.call_args.args[0]
    scope = activation_scope("intake", source.config.sources["intake"], "old-root")
    settings = FactorySettings(tmp_path / "settings.db", source, "Factory")
    await settings.update(changed(settings))
    assert (
        activation_scope("intake", source.config.sources["intake"], "old-root") == scope
    )
    assert source.reply_authorized(event)
    await source.finished(
        event, SimpleNamespace(output="Done", error=None, stopped=False)
    )
    assert len(source.spool.pending()) == 1
    await source.accept_delivery(
        delivery("followup", message_id="followup", text="continue")
    )
    assert accept.await_count == 2
    assert accept.call_args.args[0].thread_key == event.thread_key
    await source.accept_delivery(delivery())
    assert accept.await_count == 2
    settings.database.close()
    await source.stop()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    settings = FactorySettings(tmp_path / "settings.db", source, "Factory")
    assert settings.snapshot()["revision"] == 1
    assert settings.snapshot()["display_name"] == "Research Factory"
    assert source.config.sources["intake"]["thread_activation"]["marker"] == "@research"
    assert source.reply_authorized(event)
    assert len(source.spool.pending()) == 1
    await source.accept_delivery(
        delivery("new-followup", message_id="new-followup", text="continue again")
    )
    assert accept.await_count == 3
    settings.database.close()
    await source.stop()


@pytest.mark.asyncio
async def test_new_threads_use_only_current_marker_and_history_is_not_replayed(
    tmp_path,
):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    settings = FactorySettings(tmp_path / "settings.db", source, "Factory")
    await settings.update(changed(settings))
    await source.accept_delivery(delivery("old", message_id="old", text="@factory go"))
    accept.assert_not_awaited()
    await source.accept_delivery(
        delivery(
            "new",
            message_id="new",
            text="@research go",
            created_at="2026-11-08T12:02:00Z",
        )
    )
    accept.assert_awaited_once()
    assert "full discussion before deployment" in accept.call_args.args[0].context
    await source.accept_delivery(delivery("old", message_id="old", text="@factory go"))
    accept.assert_awaited_once()
    settings.database.close()
    await source.stop()


@pytest.mark.asyncio
async def test_legacy_activation_pending_parked_and_deliveries_keep_their_namespace(
    tmp_path,
):
    config = channel_config()
    legacy = activation_scope("intake", config.sources["intake"], "old-root")
    source = PowerAutomateSource(config, AsyncMock(), tmp_path / "spool.db")
    source.threads.begin(legacy, "inflight")
    source.threads.accepted(legacy, "accepted", "2026-11-08T12:01:00Z", "queue")
    source.threads.begin(legacy, "inflight")
    with source.spool.db:
        source.spool.db.execute(
            "INSERT INTO bridge_thread_parked VALUES (?, ?, ?, ?)",
            ("parked", "intake", legacy, "2026-11-08T12:03:00Z"),
        )
    settings = FactorySettings(tmp_path / "settings.db", source, "Factory")
    await settings.update(changed(settings))
    assert (
        activation_scope("intake", source.config.sources["intake"], "old-root")
        == legacy
    )
    assert source.threads.active(legacy)
    assert source.threads.pending(legacy) == "inflight"
    assert source.threads.delivered(legacy, "accepted")
    assert source.threads.ready_parked() == [("parked", "intake")]
    settings.database.close()
    await source.stop()


@pytest.mark.asyncio
async def test_settings_wait_for_batch_and_reject_conflicting_revisions(tmp_path):
    source = PowerAutomateSource(channel_config(), AsyncMock(), tmp_path / "spool.db")
    settings = FactorySettings(tmp_path / "settings.db", source, "Factory")
    value = changed(settings)
    async with source.delivery_lock:
        update = asyncio.create_task(settings.update(value))
        await asyncio.sleep(0)
        assert not update.done()
        assert settings.snapshot()["revision"] == 0
    assert await update == value
    assert await settings.update(value) == value
    conflict = copy.deepcopy(value)
    conflict["display_name"] = "Another"
    with pytest.raises(RuntimeError):
        await settings.update(conflict)
    settings.database.close()
    await source.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"display_name": ""},
        {"display_name": "a\nname"},
        {"revision": True},
        {"sources": []},
        {"database_password": "not allowed"},
        {"sources": [{"source": "intake", "identity": "wrong", "marker": "@factory"}]},
    ],
)
async def test_invalid_settings_do_not_mutate_runtime_or_storage(tmp_path, change):
    source = PowerAutomateSource(channel_config(), AsyncMock(), tmp_path / "spool.db")
    settings = FactorySettings(tmp_path / "settings.db", source, "Factory")
    before = settings.snapshot()
    with pytest.raises(ValueError):
        await settings.update({**changed(settings), **change})
    assert settings.snapshot() == before
    assert source.config.sources["intake"]["thread_activation"]["marker"] == "@factory"
    settings.database.close()
    await source.stop()


@pytest.mark.asyncio
async def test_control_only_route_and_factory_without_teams(tmp_path):
    settings = FactorySettings(tmp_path / "settings.db", None, "Factory")
    app = FastAPI()
    mount_factory_settings(
        app,
        settings,
        lambda request: request.headers.get("authorization") == "Bearer control",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://factory"
    ) as client:
        assert (await client.get("/integrations/settings")).status_code == 403
        assert (
            await client.put("/integrations/settings", json=changed(settings))
        ).status_code == 403
        headers = {"Authorization": "Bearer control"}
        result = await client.put(
            "/integrations/settings", json=changed(settings), headers=headers
        )
        assert result.status_code == 200
        assert result.json()["sources"] == []
        assert (
            await client.get("/integrations/settings", headers=headers)
        ).json() == result.json()
        assert (
            await client.put("/integrations/settings", content="{", headers=headers)
        ).status_code == 422
    settings.database.close()
