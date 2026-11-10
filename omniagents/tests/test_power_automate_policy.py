from copy import deepcopy
from datetime import datetime, timezone

import pytest

from omniagents.integrations.power_automate_policy import (
    activation_scope,
    actor_authorized,
    has_marker,
    parse_timestamp,
    validate_source_policy,
)


def policy(**updates):
    return {
        "tenant_id": "tenant",
        "team_id": "team",
        "channel_id": "channel",
        "users": ["human"],
        **updates,
    }


def activation(**updates):
    return {
        "factory_id": "factory-1",
        "marker": "@factory",
        "not_before": "2026-11-08T10:00:00Z",
        **updates,
    }


def channel_policy(**updates):
    return policy(senders="channel_humans", thread_activation=activation(), **updates)


def test_named_allowlist_remains_supported_by_default():
    source = policy()
    assert validate_source_policy(source) is None
    assert actor_authorized(source, {"actor_id": "human"})
    assert actor_authorized(source, {"actor_id": "human", "actor_type": "human"})
    assert not actor_authorized(source, {"actor_id": "other"})
    source["senders"] = "allowlist"
    assert actor_authorized(source, {"actor_id": "human"})


def test_channel_humans_does_not_require_or_use_users():
    source = channel_policy()
    source.pop("users")
    assert validate_source_policy(source) is None
    assert actor_authorized(source, {"actor_id": "new-human", "actor_type": "human"})
    source["users"] = []
    assert actor_authorized(source, {"actor_id": "new-human", "actor_type": "human"})


@pytest.mark.parametrize(
    "actor_type", [None, "", "unknown", "bot", "application", "Human", 1, []]
)
@pytest.mark.parametrize(
    "source", [policy(), channel_policy(), policy(thread_activation=activation())]
)
def test_explicit_nonhuman_metadata_is_denied(source, actor_type):
    assert not actor_authorized(source, {"actor_id": "human", "actor_type": actor_type})


@pytest.mark.parametrize(
    "source", [channel_policy(), policy(thread_activation=activation())]
)
def test_activation_requires_explicit_human_metadata(source):
    assert not actor_authorized(source, {"actor_id": "human"})
    assert actor_authorized(source, {"actor_id": "human", "actor_type": "human"})


@pytest.mark.parametrize("actor_id", [None, "", " ", "\x00", 0, True, [], {}])
@pytest.mark.parametrize("source", [policy(), channel_policy()])
def test_actor_id_must_be_nonempty_text(source, actor_id):
    assert not actor_authorized(source, {"actor_id": actor_id, "actor_type": "human"})


@pytest.mark.parametrize("source", [policy(), channel_policy()])
@pytest.mark.parametrize(
    "flags",
    [{"application_id": "app"}, {"bridge_generated": True}, {"actor_type": "bot"}],
)
def test_self_and_application_events_are_denied(source, flags):
    assert not actor_authorized(
        source, {"actor_id": "human", "actor_type": "human", **flags}
    )


@pytest.mark.parametrize("source", [policy(), channel_policy()])
def test_ignored_human_posting_identity_is_denied(source):
    source = deepcopy(source)
    source["ignored_actor_ids"] = ["human"]
    assert not actor_authorized(source, {"actor_id": "human", "actor_type": "human"})


def test_revocation_is_evaluated_on_each_authorization():
    source = channel_policy()
    payload = {"actor_id": "new-human", "actor_type": "human"}
    assert actor_authorized(source, payload)
    source["ignored_actor_ids"] = ["new-human"]
    assert not actor_authorized(source, payload)
    source.pop("ignored_actor_ids")
    source["senders"] = "allowlist"
    assert not actor_authorized(source, payload)
    source["users"].append("new-human")
    assert actor_authorized(source, payload)
    source["users"].remove("new-human")
    assert not actor_authorized(source, payload)


@pytest.mark.parametrize(
    "updates",
    [
        {"senders": "everyone"},
        {"senders": None},
        {"senders": []},
        {"users": []},
        {"users": None},
        {"users": "human"},
        {"users": [""]},
        {"users": [" "]},
        {"users": [1]},
        {"ignored_actor_ids": None},
        {"ignored_actor_ids": "service"},
        {"ignored_actor_ids": [""]},
        {"ignored_actor_ids": [False]},
        {"senders": "channel_humans"},
        {"thread_activation": None},
        {"thread_activation": []},
        {"thread_activation": {}},
        {
            "senders": "channel_humans",
            "thread_activation": activation(),
            "users": "human",
        },
    ],
)
def test_invalid_source_configuration_fails_closed(updates):
    source = policy(**updates)
    with pytest.raises(ValueError):
        validate_source_policy(source)
    assert not actor_authorized(source, {"actor_id": "human", "actor_type": "human"})


@pytest.mark.parametrize("source", [None, [], "policy"])
def test_nondictionary_policy_fails_closed(source):
    with pytest.raises(ValueError):
        validate_source_policy(source)
    assert not actor_authorized(source, {"actor_id": "human"})


@pytest.mark.parametrize("key", ["factory_id", "marker", "not_before"])
def test_activation_requires_all_fields(key):
    settings = activation()
    settings.pop(key)
    with pytest.raises(ValueError):
        validate_source_policy(policy(thread_activation=settings))


@pytest.mark.parametrize(
    "factory_id", [None, "", " ", "a/b", "../factory", "a\n", "é", "a" * 129, 1]
)
def test_factory_id_is_safe_and_bounded(factory_id):
    with pytest.raises(ValueError):
        validate_source_policy(
            policy(thread_activation=activation(factory_id=factory_id))
        )


@pytest.mark.parametrize(
    "marker",
    [
        None,
        "",
        "factory",
        "@",
        "@@factory",
        "@factory.*",
        "@factory!",
        "@f actory",
        "@é",
        "@" + "f" * 65,
        1,
    ],
)
def test_marker_is_explicit_ascii_literal(marker):
    with pytest.raises(ValueError):
        validate_source_policy(policy(thread_activation=activation(marker=marker)))
    with pytest.raises(ValueError):
        has_marker("@factory", marker)


def test_identifier_bounds_are_inclusive():
    validate_source_policy(
        policy(
            thread_activation=activation(factory_id="f" * 128, marker="@" + "f" * 64)
        )
    )
    validate_source_policy(
        policy(thread_activation=activation(factory_id="f", marker="@f"))
    )


@pytest.mark.parametrize(
    "text",
    [
        "@factory",
        "@FACTORY",
        "hello @factory!",
        "(@factory)",
        "[@factory]",
        "\n@factory\n",
        "@factory, hello",
        "ask: @factory?",
        "@factory.",
        "'@factory'",
        "@factory;",
    ],
)
def test_literal_marker_has_token_boundaries(text):
    assert has_marker(text, "@factory")


@pytest.mark.parametrize(
    "text",
    [
        "factory",
        "human@factory",
        "human.name@factory.test",
        "human+@factory",
        "x-@factory",
        "@factory2",
        "@factory_extra",
        "@factory-other",
        "@@factory",
        "@factory@other",
        "é@factory",
        "@factoryé",
        "@factoryK",
        "@factoryſ",
        "historic mention exists elsewhere",
        "",
        None,
    ],
)
def test_marker_does_not_match_emails_or_longer_tokens(text):
    assert not has_marker(text, "@factory")


def test_literal_marker_supports_hyphen_and_underscore():
    assert has_marker("(@FACTORY_1-TEST)", "@factory_1-test")
    assert not has_marker("@factory_1-test2", "@factory_1-test")


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-08T10:00:00Z",
        "2026-11-08T11:00:00+01:00",
        "2026-11-08T05:00:00-05:00",
        "2026-11-08T10:00+00:00",
    ],
)
def test_timestamp_is_explicit_and_canonical_utc(value):
    assert parse_timestamp(value) == datetime(2026, 11, 8, 10, tzinfo=timezone.utc)
    assert parse_timestamp(value).tzinfo is timezone.utc


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        1.5,
        [],
        {},
        datetime(2026, 11, 8),
        "",
        "2026-11-08",
        "2026-11-08T10:00:00",
        "2026-11-08 10:00:00Z",
        "2026-02-30T10:00:00Z",
        "2026-11-08T25:00:00Z",
        "2026-11-08T10:00:00+24:00",
        "2026-11-08T10:00:00+01:99",
        "not-a-date",
        "2026-11-08T10:00:00Z\n",
    ],
)
def test_invalid_timestamps_and_activation_cutoffs_are_rejected(value):
    with pytest.raises(ValueError):
        parse_timestamp(value)
    with pytest.raises(ValueError):
        validate_source_policy(policy(thread_activation=activation(not_before=value)))


def test_cutoff_comparison_uses_event_time_not_historic_mentions():
    cutoff = parse_timestamp(activation()["not_before"])
    assert parse_timestamp("2026-11-08T09:59:59.999999Z") < cutoff
    assert parse_timestamp("2026-11-08T11:00:00+01:00") == cutoff
    assert parse_timestamp("2026-11-08T10:00:00.000001Z") > cutoff
    assert not has_marker("a new reply with no marker", "@factory")


def test_scope_is_stable_and_excludes_participant_permissions():
    source = channel_policy()
    initial = activation_scope("intake", source, "root")
    assert len(initial) == 64
    assert int(initial, 16) >= 0
    source["users"] = ["other"]
    source["ignored_actor_ids"] = ["human"]
    source["agent_access"] = {"assistant": {"operations": []}}
    source["thread_activation"]["marker"] = "@FACTORY"
    source["thread_activation"]["not_before"] = "2026-11-08T11:00:00+01:00"
    assert activation_scope("intake", source, "root") == initial
    assert activation_scope("other", source, "root") != initial
    assert activation_scope("intake", source, "other") != initial


@pytest.mark.parametrize(
    "key", ["factory_id", "marker", "not_before", "tenant_id", "team_id", "channel_id"]
)
def test_scope_changes_with_activation_identity(key):
    source = channel_policy()
    initial = activation_scope("intake", source, "root")
    if key in source["thread_activation"]:
        source["thread_activation"][key] = {
            "factory_id": "factory-2",
            "marker": "@other",
            "not_before": "2026-11-09T10:00:00Z",
        }[key]
    else:
        source[key] = "other"
    assert activation_scope("intake", source, "root") != initial


def test_scope_requires_activation():
    with pytest.raises(ValueError):
        activation_scope("intake", policy(), "root")
