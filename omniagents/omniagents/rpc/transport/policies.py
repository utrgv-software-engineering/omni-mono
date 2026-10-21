from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Hashable, Mapping

from .queue import QueuePolicy


@dataclass(frozen=True)
class EventQueuePolicy:
    policy: QueuePolicy
    key: Hashable | None
    event_class: str


_CRITICAL_EVENTS = frozenset(
    {
        "run_started",
        "message_output",
        "run_end",
        "tool_approval_requested",
        "tool_approval_resolved",
        "mcp_approval_requested",
        "mcp_approval_resolved",
        "client_request",
        "client_request_resolved",
        "realtime_error",
        # Filesystem watch frames are already coalesced and bounded by
        # FilesystemManager (overflow becomes one fs_rescan_required), so
        # what does get emitted must not be silently dropped — losing a
        # diff or a rescan signal breaks incremental sync invisibly.
        "fs_events",
        "fs_rescan_required",
    }
)

_LATEST_EVENTS = frozenset(
    {
        "run_status",
        "queue_changed",
        "session_variables_changed",
    }
)

_DROP_OLDEST_EVENTS = frozenset(
    {
        "realtime_audio_delta",
        "voice_audio_delta",
        "heartbeat",
        "keepalive",
    }
)


def classify_event(
    method: str, params: Mapping[str, Any] | None = None
) -> EventQueuePolicy:
    payload = params or {}
    if method == "token":
        return EventQueuePolicy(
            policy=QueuePolicy.COALESCE_APPEND,
            key=(
                method,
                payload.get("session_id"),
                payload.get("run_id"),
                payload.get("message_id"),
            ),
            event_class="text_delta",
        )
    if method == "fs_transfer_progress":
        # Progress is replaceable state per transfer; key on the transfer
        # so two concurrent transfers never coalesce into each other.
        return EventQueuePolicy(
            policy=QueuePolicy.COALESCE_LATEST,
            key=(method, payload.get("session_id"), payload.get("transfer_id")),
            event_class="replacement_state",
        )
    if method in _LATEST_EVENTS:
        return EventQueuePolicy(
            policy=QueuePolicy.COALESCE_LATEST,
            key=(method, payload.get("session_id"), payload.get("run_id")),
            event_class="replacement_state",
        )
    if method in _DROP_OLDEST_EVENTS:
        return EventQueuePolicy(
            policy=QueuePolicy.DROP_OLDEST,
            key=None,
            event_class="ephemeral_media",
        )
    if method in _CRITICAL_EVENTS:
        return EventQueuePolicy(
            policy=QueuePolicy.CRITICAL,
            key=None,
            event_class="critical",
        )
    return EventQueuePolicy(
        policy=QueuePolicy.CRITICAL,
        key=None,
        event_class="unclassified_critical",
    )
