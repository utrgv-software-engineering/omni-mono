from __future__ import annotations

import hashlib
import json

from .events import IncomingEvent
from .power_automate_policy import (
    actor_authorized,
    parse_timestamp,
    validate_source_policy,
)
from .power_automate_threads import prepare_thread, with_thread_history


THREAD_FIELDS = ("tenant_id", "team_id", "channel_id", "root_message_id")


def validate_handler(policy):
    if not isinstance(policy, dict):
        raise ValueError("invalid handler policy")
    handler = policy.get(
        "handler", "teams" if policy.get("provider") == "teams" else None
    )
    if handler == "teams":
        if not all(
            isinstance(policy.get(key), str) and policy[key]
            for key in THREAD_FIELDS[:3]
        ):
            raise ValueError("Teams sources require tenant, team and channel IDs")
        validate_source_policy(policy)
    elif handler == "json":
        event = policy.get("event", "event")
        if not isinstance(event, str) or not event or len(event) > 128:
            raise ValueError("invalid configured event name")
        fields = policy.get("required_fields", [])
        if not isinstance(fields, list) or not all(
            isinstance(key, str) and key for key in fields
        ):
            raise ValueError("required_fields must contain payload field names")
    else:
        raise ValueError("source requires a json or teams handler")
    return handler


def normalize_teams(source, delivery_id, payload, policy):
    if policy is None or not isinstance(payload, dict):
        raise ValueError("unauthorized_source")
    for key in THREAD_FIELDS[:3]:
        if payload.get(key) != policy[key]:
            raise ValueError("unauthorized_destination")
    if (
        payload.get("application_id")
        or payload.get("bridge_generated")
        or payload.get("actor_type") in ("application", "bot", "deleted")
        or payload.get("actor_id") in policy.get("ignored_actor_ids", [])
    ):
        return None
    if not actor_authorized(policy, payload):
        raise ValueError("unauthorized_actor")
    for key in ("message_id", "root_message_id", "text"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError("invalid_message")
    thread_parts = [
        policy["tenant_id"],
        policy["team_id"],
        policy["channel_id"],
        payload["root_message_id"],
    ]
    activation = policy.get("thread_activation")
    if activation:
        parse_timestamp(payload.get("created_at"))
        thread_parts = [source, activation["factory_id"], *thread_parts]
        identity_parts = [*thread_parts, payload["message_id"]]
    else:
        identity_parts = [source, delivery_id]
    return IncomingEvent(
        provider="teams",
        event="message",
        delivery_id=hashlib.sha256(json.dumps(identity_parts).encode()).hexdigest(),
        thread_key=json.dumps(thread_parts, separators=(",", ":")),
        payload={**payload, "bridge_source": source, "bridge_event_id": delivery_id},
        filters={"users": [payload["actor_id"]], "channels": [policy["channel_id"]]},
    )


def validate_teams_history(document, payload):
    if not isinstance(document, dict):
        raise ValueError("missing Teams thread history")
    root = document.get("root")
    replies = document.get("replies")
    if not isinstance(root, dict) or not isinstance(replies, list):
        raise ValueError("Teams history requires root and replies")
    if root.get("id") != payload["root_message_id"]:
        raise ValueError("Teams history root mismatch")
    messages = []
    identities = set()
    for raw in [root, *replies]:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("id"), str)
            or not raw["id"]
        ):
            raise ValueError("invalid Teams history identity")
        if raw["id"] in identities:
            raise ValueError("duplicate Teams history message")
        identities.add(raw["id"])
        destination = raw.get("channelIdentity") or {}
        for key, raw_key in (("team_id", "teamId"), ("channel_id", "channelId")):
            if destination.get(raw_key) != payload[key]:
                raise ValueError("cross_thread_history")
        if raw is root:
            if raw.get("replyToId") not in (None, "", raw["id"]):
                raise ValueError("invalid Teams history root")
        elif raw.get("replyToId") != payload["root_message_id"]:
            raise ValueError("cross_thread_history")
        messages.append(raw)
    if payload["message_id"] not in identities:
        raise ValueError("Teams history missing trigger")
    return messages


class JsonHandler:
    def __init__(self, policy):
        self.policy = policy

    def prepare(self, delivery):
        if any(
            key not in delivery.payload
            for key in self.policy.get("required_fields", [])
        ):
            raise ValueError("missing required payload data")
        return IncomingEvent(
            provider="power-automate",
            event=self.policy.get("event", "event"),
            delivery_id=hashlib.sha256(
                json.dumps([delivery.source, delivery.id]).encode()
            ).hexdigest(),
            payload=delivery.payload,
            filters={"sources": [delivery.source]},
        )


class TeamsHandler:
    def __init__(self, policy, threads):
        self.policy = policy
        self.threads = threads

    def prepare(self, delivery):
        event = normalize_teams(
            delivery.source, delivery.id, delivery.payload, self.policy
        )
        if event is None:
            self.threads.forget_parked(delivery.id)
            return None, None
        scope = None
        if self.policy.get("thread_activation"):
            scope = prepare_thread(event, self.policy, self.threads)
            if scope is None:
                return None, None
        try:
            messages = validate_teams_history(
                delivery.payload.get("thread_history"), event.payload
            )
            event = with_thread_history(event, messages)
            return event, scope
        except ValueError:
            if scope is not None:
                self.threads.reject(scope, event.delivery_id)
            raise

    def accepted(self, event, scope):
        if scope is not None:
            self.threads.accepted(
                scope,
                event.delivery_id,
                event.payload["created_at"],
                event.payload["bridge_event_id"],
            )
