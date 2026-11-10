from types import SimpleNamespace

import pytest

from omniagents.integrations.power_automate import normalize_event
from omniagents.integrations.power_automate_actions import (
    PowerAutomateActions,
    validate_action,
    validate_access_policy,
)
from omniagents.integrations.store import EventStore
from .test_power_automate import config, message


def rules():
    return {
        "operations": [
            "teams.reply",
            "sharepoint.get_file",
            "sharepoint.update_item",
            "outlook.send_email",
        ],
        "download_attachments": True,
        "sharepoint_sites": {
            "site": {"libraries": ["library"], "lists": {"list": ["Status"]}}
        },
        "mailboxes": ["factory@example.test"],
        "recipients": ["human@example.test"],
    }


def bridge_context(tmp_path, payloads=None):
    configuration = config()
    configuration.sources["intake"]["agent_access"] = {"assistant": rules()}
    store = EventStore(tmp_path / "events.db")
    for index, payload in enumerate(payloads if payloads is not None else [{}]):
        event = normalize_event("intake", str(index), message(), configuration)
        event.payload.update(payload)
        identity = store.record(event)
        store.start_dispatch(identity, "teams", "assistant", "session")
    source = SimpleNamespace(config=configuration)
    return PowerAutomateActions(source, store)


def test_policy_rejects_unknown_operations():
    with pytest.raises(ValueError):
        validate_access_policy(
            {"agent_access": {"assistant": {"operations": ["execute_sql"]}}}
        )


def test_reply_destination_is_not_chosen_by_agent():
    payload = validate_action("teams.reply", {"text": "answer"}, rules(), message())
    assert payload["channel_id"] == "channel"
    assert payload["root_message_id"] == "root"
    with pytest.raises(ValueError):
        validate_action(
            "teams.reply", {"text": "answer", "channel_id": "other"}, rules(), message()
        )


@pytest.mark.parametrize(
    "operation,payload",
    [
        (
            "sharepoint.get_file",
            {"site_id": "site", "library_id": "other", "file_id": "file"},
        ),
        (
            "sharepoint.get_file",
            {"site_id": "other", "library_id": "library", "file_id": "file"},
        ),
        (
            "sharepoint.update_item",
            {
                "site_id": "site",
                "list_id": "list",
                "item_id": "item",
                "etag": "*",
                "fields": {"Status": "done"},
            },
        ),
        (
            "sharepoint.update_item",
            {
                "site_id": "site",
                "list_id": "list",
                "item_id": "item",
                "etag": "v1",
                "fields": {"Permissions": "public"},
            },
        ),
        (
            "outlook.send_email",
            {
                "mailbox": "factory@example.test",
                "to": ["other@example.test"],
                "subject": "subject",
                "body": "body",
            },
        ),
        (
            "outlook.send_email",
            {
                "mailbox": "other@example.test",
                "to": ["human@example.test"],
                "subject": "subject",
                "body": "body",
            },
        ),
        (
            "outlook.send_email",
            {
                "mailbox": "factory@example.test",
                "to": ["human@example.test"],
                "subject": "subject",
                "body": "body",
                "bcc": ["other@example.test"],
            },
        ),
    ],
)
def test_rejects_unauthorized_actions(operation, payload):
    with pytest.raises(ValueError):
        validate_action(operation, payload, rules(), message())


def test_authorized_email_is_plain_text():
    payload = {
        "mailbox": "factory@example.test",
        "to": ["human@example.test"],
        "subject": "subject",
        "body": "<b>literal</b>",
    }
    assert (
        validate_action("outlook.send_email", payload, rules(), message())[
            "body_format"
        ]
        == "text"
    )


def test_actions_disabled_by_default():
    with pytest.raises(ValueError):
        validate_action("teams.reply", {"text": "hello"}, {}, message())


def test_context_is_bound_to_agent_and_conversation(tmp_path):
    actions = bridge_context(tmp_path)
    try:
        assert actions.context("assistant", "session")[0] == "intake"
        with pytest.raises(ValueError):
            actions.context("other-agent", "session")
        with pytest.raises(ValueError):
            actions.context("assistant", "other-session")
        actions.source.config.sources["intake"]["users"] = []
        with pytest.raises(ValueError):
            actions.context("assistant", "session")
    finally:
        actions.event_store.close()


def activate_channel(actions):
    policy = actions.source.config.sources["intake"]
    policy.pop("users", None)
    policy.update(
        senders="channel_humans",
        thread_activation={
            "factory_id": "factory-1",
            "marker": "@factory",
            "not_before": "2026-11-08T10:00:00Z",
        },
    )
    return policy


def test_channel_humans_context_accepts_new_participant(tmp_path):
    actions = bridge_context(
        tmp_path, [{"actor_id": "new-human", "actor_type": "human"}]
    )
    try:
        activate_channel(actions)
        source, access, context, _ = actions.context("assistant", "session")
        assert source == "intake"
        assert access == rules()
        assert context["actor_id"] == "new-human"
        assert (
            validate_action("teams.reply", {"text": "answer"}, access, context)[
                "channel_id"
            ]
            == "channel"
        )
    finally:
        actions.event_store.close()


@pytest.mark.parametrize("key", ["tenant_id", "team_id", "channel_id"])
def test_channel_humans_context_retains_exact_destination_checks(tmp_path, key):
    actions = bridge_context(tmp_path, [{"actor_type": "human", key: "other"}])
    try:
        activate_channel(actions)
        with pytest.raises(ValueError, match="conversation_no_longer_authorized"):
            actions.context("assistant", "session")
    finally:
        actions.event_store.close()


@pytest.mark.parametrize(
    "bad_actor",
    [
        {"actor_type": "unknown"},
        {"actor_type": "bot"},
        {"actor_type": "application"},
        {"actor_type": None},
        {},
        {"actor_type": "human", "bridge_generated": True},
        {"actor_type": "human", "application_id": "app"},
        {"actor_type": "human", "actor_id": ""},
    ],
)
def test_channel_humans_context_rechecks_every_dispatch(tmp_path, bad_actor):
    actions = bridge_context(tmp_path, [bad_actor, {"actor_type": "human"}])
    try:
        activate_channel(actions)
        with pytest.raises(ValueError, match="conversation_no_longer_authorized"):
            actions.context("assistant", "session")
    finally:
        actions.event_store.close()


@pytest.mark.parametrize(
    "revocation", ["ignore_actor", "allowlist", "agent_access", "activation"]
)
def test_channel_humans_context_observes_live_policy_revocation(tmp_path, revocation):
    actions = bridge_context(
        tmp_path,
        [{"actor_id": "new-human", "actor_type": "human"}, {"actor_type": "human"}],
    )
    try:
        policy = activate_channel(actions)
        assert actions.context("assistant", "session")[0] == "intake"
        if revocation == "ignore_actor":
            policy["ignored_actor_ids"] = ["new-human"]
        elif revocation == "allowlist":
            policy.update(senders="allowlist", users=["human"])
        elif revocation == "agent_access":
            policy.pop("agent_access")
        else:
            policy.pop("thread_activation")
        with pytest.raises(ValueError):
            actions.context("assistant", "session")
    finally:
        actions.event_store.close()


@pytest.mark.parametrize(
    "flags",
    [
        {"actor_type": "application"},
        {"actor_type": "bot"},
        {"bridge_generated": True},
        {"application_id": "app"},
    ],
)
def test_legacy_context_also_suppresses_bot_and_self_messages(tmp_path, flags):
    actions = bridge_context(tmp_path, [flags])
    try:
        with pytest.raises(ValueError, match="conversation_no_longer_authorized"):
            actions.context("assistant", "session")
    finally:
        actions.event_store.close()


def test_activation_allowlist_context_requires_explicit_human(tmp_path):
    actions = bridge_context(tmp_path)
    try:
        policy = activate_channel(actions)
        policy.update(senders="allowlist", users=["human"])
        with pytest.raises(ValueError, match="conversation_no_longer_authorized"):
            actions.context("assistant", "session")
    finally:
        actions.event_store.close()
