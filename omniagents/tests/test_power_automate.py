import json
from types import SimpleNamespace

import pytest

from omniagents.integrations.power_automate import (
    PowerAutomateConfig,
    ReplySpool,
    normalize_event,
)


def config():
    return PowerAutomateConfig.parse(
        {
            "sources": {
                "intake": {
                    "provider": "teams",
                    "tenant_id": "tenant",
                    "team_id": "team",
                    "channel_id": "channel",
                    "users": ["human"],
                }
            }
        }
    )


def message(**updates):
    return {
        "tenant_id": "tenant",
        "team_id": "team",
        "channel_id": "channel",
        "actor_id": "human",
        "message_id": "root",
        "root_message_id": "root",
        "text": "hello",
        **updates,
    }


def test_replies_share_thread_but_have_distinct_delivery_ids():
    root = normalize_event("intake", "root", message(), config())
    reply = normalize_event("intake", "reply", message(message_id="reply"), config())
    assert root.thread_key == reply.thread_key
    assert root.delivery_id != reply.delivery_id
    assert (
        root.delivery_id
        == normalize_event("intake", "root", message(), config()).delivery_id
    )
    assert root.provider == "teams"
    assert root.filters == {"users": ["human"], "channels": ["channel"]}


@pytest.mark.parametrize(
    "updates",
    [
        {"actor_id": "other"},
        {"tenant_id": "other"},
        {"team_id": "other"},
        {"channel_id": "other"},
        {"root_message_id": ""},
    ],
)
def test_rejects_untrusted_or_invalid_input(updates):
    with pytest.raises(ValueError):
        normalize_event("intake", "event", message(**updates), config())


def test_rejects_unknown_source():
    with pytest.raises(ValueError):
        normalize_event("other", "event", message(), config())


def test_ignores_bridge_messages():
    assert (
        normalize_event("intake", "event", message(bridge_generated=True), config())
        is None
    )


def test_explicit_policy_required():
    with pytest.raises(ValueError):
        PowerAutomateConfig.parse({})
    with pytest.raises(ValueError):
        PowerAutomateConfig.parse({"sources": {"intake": {"provider": "teams"}}})


def test_reply_spool_survives_restart_and_deduplicates(tmp_path):
    event = normalize_event("intake", "event", message(), config())
    result = SimpleNamespace(output="answer", error=None, stopped=False)
    path = tmp_path / "replies.db"
    spool = ReplySpool(path)
    spool.enqueue(event, result)
    spool.enqueue(event, result)
    spool.close()
    spool = ReplySpool(path)
    rows = spool.pending()
    assert len(rows) == 1
    assert json.loads(rows[0][2])["root_message_id"] == "root"
    assert json.loads(rows[0][2])["text"] == "answer"
    spool.acknowledge(rows[0][0])
    assert not spool.pending()
    spool.close()


def test_failed_run_does_not_publish_success(tmp_path):
    spool = ReplySpool(tmp_path / "replies.db")
    event = normalize_event("intake", "event", message(), config())
    spool.enqueue(
        event, SimpleNamespace(output="partial", error="failure", stopped=False)
    )
    assert not spool.pending()
    spool.close()


def test_runtime_parser_supports_bridge_and_respects_deployment_policy(monkeypatch):
    from omniagents.integrations.server import parse_integrations
    from omniagents.core.allowed_services import INTEGRATIONS_ENV

    entry = {"type": "power-automate", "sources": config().sources}
    monkeypatch.delenv(INTEGRATIONS_ENV, raising=False)
    assert isinstance(parse_integrations([entry])[0], PowerAutomateConfig)
    monkeypatch.setenv(INTEGRATIONS_ENV, "[]")
    assert parse_integrations([entry]) == []


def test_inbound_text_has_no_additional_handler_size_cap():
    text = "x" * 100000
    assert (
        normalize_event("intake", "event", message(text=text), config()).payload["text"]
        == text
    )
