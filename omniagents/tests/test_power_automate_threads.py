import json
from unittest.mock import AsyncMock

import pytest

from omniagents.integrations.power_automate import (
    PowerAutomateConfig,
    PowerAutomateSource,
)
from omniagents.integrations.power_automate_handlers import (
    TeamsHandler,
    validate_teams_history,
)
from omniagents.integrations.power_automate_policy import activation_scope
from omniagents.integrations.power_automate_transport import Delivery
from omniagents.integrations.store import EventStore
from .test_power_automate import message


def channel_config(**updates):
    return PowerAutomateConfig.parse(
        {
            "sources": {
                "intake": {
                    "handler": "teams",
                    "tenant_id": "tenant",
                    "team_id": "team",
                    "channel_id": "channel",
                    "senders": "channel_humans",
                    "ignored_actor_ids": ["posting-user"],
                    "thread_activation": {
                        "factory_id": "factory-one",
                        "marker": "@factory",
                        "not_before": "2026-11-08T12:00:00Z",
                        **updates,
                    },
                }
            }
        }
    )


def trigger(**updates):
    return message(
        **{
            "message_id": "mention",
            "root_message_id": "old-root",
            "actor_type": "human",
            "created_at": "2026-11-08T12:01:00Z",
            "text": "@factory explain",
            **updates,
        }
    )


def delivery(identity="first", **updates):
    payload = trigger(**updates)
    root = trigger(
        message_id="old-root",
        text="full discussion before deployment",
        created_at="2020-01-01T00:00:00Z",
    )

    def raw(message):
        return {
            "id": message["message_id"],
            "replyToId": (
                None
                if message["message_id"] == message["root_message_id"]
                else message["root_message_id"]
            ),
            "channelIdentity": {
                "teamId": message["team_id"],
                "channelId": message["channel_id"],
            },
            "from": {"user": {"id": message["actor_id"]}},
            "body": {"content": message["text"]},
            "createdDateTime": message["created_at"],
        }

    history = {"root": raw(root), "replies": [raw(payload)]}
    return Delivery(identity, "intake", {**payload, "thread_history": history})


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [None, "", "old-root"])
async def test_connector_root_parent_forms_preserve_raw_history(tmp_path, parent):
    incoming = delivery()
    history = incoming.payload["thread_history"]
    history["root"]["replyToId"] = parent
    history["replies"][0]["body"] = {
        "contentType": "html",
        "content": "<p>@factory explain</p>",
    }
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    try:
        await source.accept_delivery(incoming)
        accept.assert_awaited_once()
        event = accept.call_args.args[0]
        assert event.payload["thread_history"] == history
        assert "<p>@factory explain</p>" in event.context
        assert source.threads.active(
            activation_scope("intake", source.config.sources["intake"], "old-root")
        )
    finally:
        await source.stop()


def test_connector_root_cannot_reference_another_thread():
    incoming = delivery()
    history = incoming.payload["thread_history"]
    history["root"]["replyToId"] = "different-root"
    with pytest.raises(ValueError, match="invalid Teams history root"):
        validate_teams_history(history, incoming.payload)


@pytest.mark.asyncio
async def test_first_marker_in_old_thread_receives_complete_context(tmp_path):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    await source.accept_delivery(delivery())
    event = accept.call_args.args[0]
    assert "full discussion before deployment" in event.context
    assert "untrusted reference" in event.context
    assert event.payload["message_id"] == "mention"
    assert accept.await_count == 1
    await source.stop()


@pytest.mark.asyncio
async def test_followups_and_stable_message_dedup_survive_restart(tmp_path):
    accept = AsyncMock()
    path = tmp_path / "spool.db"
    source = PowerAutomateSource(channel_config(), accept, path)
    await source.accept_delivery(delivery())
    first = accept.call_args.args[0]
    await source.stop()
    source = PowerAutomateSource(channel_config(), accept, path)
    await source.accept_delivery(delivery("duplicate-event-row"))
    await source.accept_delivery(
        delivery(
            "next",
            message_id="next",
            actor_id="another-human",
            text="follow up",
            created_at="2026-11-08T12:02:00Z",
        )
    )
    assert accept.await_count == 2
    assert accept.call_args.args[0].thread_key == first.thread_key
    await source.stop()


@pytest.mark.asyncio
async def test_only_trigger_marker_activates_not_history(tmp_path):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    incoming = delivery(text="ordinary reply")
    incoming.payload["thread_history"]["root"]["body"][
        "content"
    ] = "@factory historical request"
    await source.accept_delivery(incoming)
    accept.assert_not_awaited()
    await source.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates,raises",
    [
        ({"created_at": "2026-11-08T11:00:00Z"}, False),
        ({"actor_id": "posting-user"}, False),
        ({"actor_type": "application", "application_id": "bot"}, False),
        ({"channel_id": "other"}, True),
        ({"actor_type": "unknown"}, True),
    ],
)
async def test_cutoff_and_authorization(tmp_path, updates, raises):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    if raises:
        with pytest.raises(ValueError):
            await source.accept_delivery(delivery(**updates))
    else:
        await source.accept_delivery(delivery(**updates))
    accept.assert_not_awaited()
    await source.stop()


@pytest.mark.asyncio
async def test_complete_transport_missing_domain_data_rejected(tmp_path):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    incoming = delivery()
    incoming.payload.pop("thread_history")
    with pytest.raises(ValueError, match="missing Teams"):
        await source.accept_delivery(incoming)
    accept.assert_not_awaited()
    scope = activation_scope("intake", source.config.sources["intake"], "old-root")
    assert not source.threads.active(scope)
    assert source.threads.pending(scope) is None
    await source.stop()


@pytest.mark.asyncio
async def test_failed_local_intake_does_not_activate(tmp_path):
    accept = AsyncMock(side_effect=OSError("intake unavailable"))
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    with pytest.raises(OSError):
        await source.accept_delivery(delivery())
    scope = activation_scope("intake", source.config.sources["intake"], "old-root")
    assert not source.threads.active(scope)
    accept.side_effect = None
    await source.accept_delivery(delivery())
    assert source.threads.active(scope)
    await source.stop()


@pytest.mark.asyncio
async def test_crash_after_intake_before_receipt_preserves_reply_recovery(
    tmp_path, monkeypatch
):
    store = EventStore(tmp_path / "events.db")

    async def accept(event):
        event_id = store.record(event)
        if event_id is not None:
            dispatch = store.start_dispatch(event_id, "teams", "assistant", "session")
            store.finish_dispatch(dispatch, "completed", output="answer")

    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db", store)
    original = source.threads.accepted

    def fail(*args):
        raise OSError("lost receipt")

    monkeypatch.setattr(source.threads, "accepted", fail)
    with pytest.raises(OSError):
        await source.accept_delivery(delivery())
    source.recover_replies()
    assert len(store.pending_bridge_replies()) == 1
    assert not source.spool.pending()
    monkeypatch.setattr(source.threads, "accepted", original)
    await source.accept_delivery(delivery())
    source.recover_replies()
    assert len(source.spool.pending()) == 1
    assert not store.pending_bridge_replies()
    await source.stop()
    store.close()


@pytest.mark.asyncio
async def test_out_of_order_followup_is_parked_for_requeue_after_activation(tmp_path):
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db")
    await source.accept_delivery(
        delivery(
            "later-first",
            message_id="next",
            text="question",
            created_at="2026-11-08T12:02:00Z",
        )
    )
    await source.accept_delivery(
        delivery(
            "earlier",
            message_id="old",
            text="earlier",
            created_at="2026-11-08T12:00:30Z",
        )
    )
    accept.assert_not_awaited()
    await source.accept_delivery(delivery())
    assert source.threads.ready_parked() == [("later-first", "intake")]
    await source.stop()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda history: history.pop("root"),
        lambda history: history["replies"].clear(),
        lambda history: history["replies"].append(history["replies"][0]),
        lambda history: history["root"]["channelIdentity"].update(channelId="other"),
    ],
)
def test_teams_handler_validates_history_identity(mutation):
    incoming = delivery()
    history = incoming.payload["thread_history"]
    mutation(history)
    with pytest.raises(ValueError):
        validate_teams_history(history, incoming.payload)


def test_history_has_no_reply_count_cap_and_preserves_raw_fields():
    incoming = delivery()
    history = incoming.payload["thread_history"]
    for number in range(75):
        history["replies"].append(
            {**history["replies"][0], "id": str(number), "extra": {"any": [1, 2]}}
        )
    result = validate_teams_history(history, incoming.payload)
    assert len(result) == 77
    assert result[-1]["extra"] == {"any": [1, 2]}


@pytest.mark.asyncio
@pytest.mark.parametrize("ignored", [False, True])
async def test_suppressed_parked_replay_is_removed(tmp_path, ignored):
    source = PowerAutomateSource(channel_config(), AsyncMock(), tmp_path / "spool.db")
    parked = delivery(
        "parked",
        message_id="followup",
        text="question",
        created_at="2026-11-08T12:02:00Z",
    )
    try:
        await source.accept_delivery(parked)
        await source.accept_delivery(delivery())
        assert source.threads.ready_parked() == [("parked", "intake")]
        if ignored:
            source.config.sources["intake"]["ignored_actor_ids"].append("human")
        else:
            duplicate = Delivery("other-id", parked.source, parked.payload)
            await source.accept_delivery(duplicate)
        await source.accept_delivery(parked)
        assert source.threads.ready_parked() == []
    finally:
        await source.stop()
