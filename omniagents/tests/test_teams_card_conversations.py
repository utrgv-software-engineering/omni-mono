import json
from unittest.mock import AsyncMock

import pytest

from omniagents.integrations.power_automate import PowerAutomateSource
from omniagents.integrations.power_automate_policy import activation_scope
from omniagents.integrations.store import EventStore
from omniagents.integrations.power_automate_transport import Delivery
from .test_power_automate_threads import channel_config, delivery


def card_event(token, identity="card-event", human=False):
    incoming = delivery(
        identity,
        message_id="answer" if human else "card-root",
        root_message_id="card-root",
        text="yes" if human else "",
        actor_type="human" if human else "application",
        application_id="" if human else "flow-bot",
        created_at="2026-11-08T12:03:00Z" if human else "2026-11-08T12:02:00Z",
    )
    root = incoming.payload["thread_history"]["root"]
    root.update(id="card-root", replyToId=None, createdDateTime="2026-11-08T12:02:00Z")
    root["from"] = {"application": {"id": "flow-bot"}}
    root["body"] = {"content": "<attachment></attachment>"}
    root["attachments"] = [
        {
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": json.dumps(
                {
                    "body": [
                        {"type": "TextBlock", "text": f"[omni-conversation:{token}]"}
                    ]
                }
            ),
        }
    ]
    if not human:
        incoming.payload["thread_history"]["replies"] = []
    return incoming


@pytest.mark.asyncio
async def test_card_event_binds_without_running_and_human_reply_continues(tmp_path):
    store = EventStore(tmp_path / "events.db")
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db", store)
    try:
        token = source.register_notification(
            "improver", "original-session", "review-42"
        )
        assert (
            source.register_notification("improver", "original-session", "review-42")
            == token
        )
        await source.accept_delivery(card_event(token))
        accept.assert_not_awaited()
        scope = activation_scope("intake", source.config.sources["intake"], "card-root")
        assert source.threads.active(scope)
        await source.accept_delivery(card_event(token, "human-answer", human=True))
        event = accept.call_args.args[0]
        assert (
            store.thread_session("teams", event.thread_key, "improver")
            == "original-session"
        )
        assert event.payload["factory_conversation"] == {
            "agent": "improver",
            "session_id": "original-session",
        }
    finally:
        await source.stop()
        store.close()


@pytest.mark.asyncio
async def test_reply_before_card_delivery_and_restart(tmp_path):
    store = EventStore(tmp_path / "events.db")
    path = tmp_path / "spool.db"
    source = PowerAutomateSource(channel_config(), AsyncMock(), path, store)
    token = source.register_notification("improver", "original-session", "review-42")
    await source.stop()
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, path, store)
    try:
        await source.accept_delivery(card_event(token, "human-answer", human=True))
        assert accept.await_count == 1
        assert (
            accept.call_args.args[0].payload["factory_conversation"]["session_id"]
            == "original-session"
        )
    finally:
        await source.stop()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["unknown", "human-root", "other-channel", "replay-root"]
)
async def test_untrusted_cards_do_not_activate(tmp_path, mutation):
    store = EventStore(tmp_path / "events.db")
    accept = AsyncMock()
    source = PowerAutomateSource(channel_config(), accept, tmp_path / "spool.db", store)
    try:
        token = source.register_notification(
            "improver", "original-session", "review-42"
        )
        incoming = card_event(token, human=True)
        if mutation == "unknown":
            incoming = card_event("0" * 64, human=True)
        elif mutation == "human-root":
            incoming.payload["thread_history"]["root"]["from"] = {
                "user": {"id": "human"}
            }
        elif mutation == "other-channel":
            incoming.payload["channel_id"] = "other"
        elif mutation == "replay-root":
            await source.accept_delivery(card_event(token))
            incoming.payload["root_message_id"] = "copied-root"
            incoming.payload["thread_history"]["root"]["id"] = "copied-root"
            incoming.payload["thread_history"]["replies"][0][
                "replyToId"
            ] = "copied-root"
        try:
            await source.accept_delivery(incoming)
        except ValueError:
            assert mutation == "other-channel"
        accept.assert_not_awaited()
    finally:
        await source.stop()
        store.close()


@pytest.mark.asyncio
async def test_card_reply_runs_originating_agent_not_channel_default(tmp_path):
    from omniagents.integrations.intake import EventIntake, RunResult

    path = tmp_path / "automations" / "teams-channel" / "automation.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nagent: dispatcher\nconversation: new\ntriggers:\n  - provider: teams\n    event: message\n---\nHandle the message.\n"
    )
    store = EventStore(tmp_path / "events.db")
    calls = []

    async def run(agent, session_id, prompt, progress=None):
        calls.append((agent, session_id))
        return RunResult(output="answer")

    intake = EventIntake(
        project_root=tmp_path, entrypoint="dispatcher", store=store, run=run
    )
    source = PowerAutomateSource(
        channel_config(), intake.accept, tmp_path / "spool.db", store
    )
    intake.after_run["teams"] = source.finished
    try:
        token = source.register_notification(
            "improver", "original-session", "review-42"
        )
        await source.accept_delivery(card_event(token, "human-answer", human=True))
        await intake.drain()
        assert calls == [("improver", "original-session")]
        assert len(source.spool.pending()) == 1
        assert (
            json.loads(source.spool.pending()[0][2])["root_message_id"] == "card-root"
        )
    finally:
        await source.stop()
        store.close()
