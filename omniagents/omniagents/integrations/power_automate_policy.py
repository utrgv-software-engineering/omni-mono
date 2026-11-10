from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


_FACTORY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_MARKER = re.compile(r"@[A-Za-z0-9_-]{1,64}")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def parse_timestamp(value: Any) -> datetime:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or _TIMESTAMP.fullmatch(value) is None
    ):
        raise ValueError("timestamp must be a timezone-aware ISO datetime string")
    try:
        parsed = datetime.fromisoformat(
            value.replace("z", "+00:00").replace("Z", "+00:00")
        )
        if parsed.utcoffset() is None:
            raise ValueError("timestamp requires an explicit timezone")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("invalid timezone-aware ISO datetime") from exc


def _actor_ids(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(actor, str) and bool(actor.strip()) and "\x00" not in actor
        for actor in value
    )


def validate_source_policy(policy: dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise ValueError("invalid source policy")
    senders = policy.get("senders", "allowlist")
    if senders not in ("allowlist", "channel_humans"):
        raise ValueError("senders must be allowlist or channel_humans")
    if senders == "allowlist":
        if not _actor_ids(policy.get("users")) or not policy["users"]:
            raise ValueError("Teams sources require an explicit user allowlist")
    elif "users" in policy and not _actor_ids(policy["users"]):
        raise ValueError("users must be a list of nonempty actor IDs")
    if not _actor_ids(policy.get("ignored_actor_ids", [])):
        raise ValueError("ignored_actor_ids must be a list of nonempty actor IDs")
    if "thread_activation" not in policy:
        if senders == "channel_humans":
            raise ValueError("channel_humans requires thread_activation")
        return
    activation = policy["thread_activation"]
    if not isinstance(activation, dict):
        raise ValueError("thread_activation must be an object")
    factory_id = activation.get("factory_id")
    if not isinstance(factory_id, str) or _FACTORY_ID.fullmatch(factory_id) is None:
        raise ValueError("factory_id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    marker = activation.get("marker")
    if not isinstance(marker, str) or _MARKER.fullmatch(marker) is None:
        raise ValueError("marker must match @[A-Za-z0-9_-]{1,64}")
    parse_timestamp(activation.get("not_before"))


def actor_authorized(policy: dict[str, Any], payload: dict[str, Any]) -> bool:
    try:
        validate_source_policy(policy)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    actor = payload.get("actor_id")
    if not isinstance(actor, str) or not actor.strip() or "\x00" in actor:
        return False
    if payload.get("application_id") or payload.get("bridge_generated"):
        return False
    if actor in policy.get("ignored_actor_ids", []):
        return False
    strict_human = (
        policy.get("senders") == "channel_humans" or "thread_activation" in policy
    )
    if strict_human or "actor_type" in payload:
        if payload.get("actor_type") != "human":
            return False
    return policy.get("senders") == "channel_humans" or actor in policy["users"]


def has_marker(text: str, marker: str) -> bool:
    if not isinstance(marker, str) or _MARKER.fullmatch(marker) is None:
        raise ValueError("marker must match @[A-Za-z0-9_-]{1,64}")
    if not isinstance(text, str):
        return False
    return (
        re.search(
            r"(?<![\w@.+-])" + re.escape(marker.lower()) + r"(?![\w@-])",
            text.lower(),
        )
        is not None
    )


def activation_scope(source: str, policy: dict[str, Any], root_message_id: str) -> str:
    validate_source_policy(policy)
    activation = policy.get("thread_activation")
    if activation is None:
        raise ValueError("activation_scope requires thread_activation")
    scope = [
        source,
        activation["factory_id"],
        policy["tenant_id"],
        policy["team_id"],
        policy["channel_id"],
        root_message_id,
        activation.get("identity_marker", activation["marker"]).lower(),
        parse_timestamp(activation["not_before"]).isoformat(),
    ]
    return hashlib.sha256(
        json.dumps(
            scope, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
