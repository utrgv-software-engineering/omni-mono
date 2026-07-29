import pytest

from omniagents.rpc.transport.policies import classify_event
from omniagents.rpc.transport.queue import QueuePolicy


@pytest.mark.parametrize(
    "method",
    [
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
    ],
)
def test_critical_events_are_never_droppable(method):
    assert classify_event(method).policy is QueuePolicy.CRITICAL


def test_token_key_preserves_run_and_message_correlation():
    policy = classify_event(
        "token",
        {"session_id": "session", "run_id": "run", "message_id": "message"},
    )

    assert policy.policy is QueuePolicy.COALESCE_APPEND
    assert policy.key == ("token", "session", "run", "message")


@pytest.mark.parametrize(
    "method", ["run_status", "queue_changed", "session_variables_changed"]
)
def test_replaceable_state_coalesces_latest(method):
    assert classify_event(method).policy is QueuePolicy.COALESCE_LATEST


@pytest.mark.parametrize(
    "method", ["realtime_audio_delta", "voice_audio_delta", "heartbeat", "keepalive"]
)
def test_only_ephemeral_events_drop_oldest(method):
    assert classify_event(method).policy is QueuePolicy.DROP_OLDEST


def test_unknown_events_fail_closed_as_critical():
    policy = classify_event("future_event")

    assert policy.policy is QueuePolicy.CRITICAL
    assert policy.event_class == "unclassified_critical"
