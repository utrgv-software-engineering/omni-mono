from dataclasses import dataclass
from typing import Literal

Direction = Literal["client_to_server", "server_to_client"]
Kind = Literal["request", "notification", "correlated_notification"]
Stability = Literal["stable", "experimental"]


@dataclass(frozen=True)
class ProtocolOperation:
    name: str
    direction: Direction
    kind: Kind
    stability: Stability


STABLE_REQUESTS = (
    "initialize",
    "start_run",
    "stop_run",
    "send_user_message",
    "get_session_history",
    "list_sessions",
    "archive_session",
    "delete_session",
    "get_user_history",
    "get_agent_info",
    "tool_approval_response",
    "mcp_approval_response",
    "client_functions",
    "client_response",
    "list_server_functions",
    "server_call",
    "enqueue_message",
    "list_queue",
    "cancel_queued_message",
    "resume_session",
    "ack_events",
    # Model & capability catalog (tkt_5A4tPEYUN85M)
    "list_models",
    "get_model",
    "list_providers",
    "set_session_model",
    "set_session_reasoning",
    "set_voice_model",
)

EXPERIMENTAL_REQUESTS = (
    "fork_session",
    "set_session_hold",
    "queue_status",
    "enqueue_notification",
    "update_session_variables",
    "set_client_status",
    "disable_tool",
    "disable_mcp_server",
    "report_incident",
    "export_session",
    "get_config",
    "validate_config",
    "write_config",
)

STABLE_NOTIFICATIONS = (
    "run_started",
    "run_status",
    "tool_called",
    "tool_result",
    "message_output",
    "token",
    "run_end",
    "tool_approval_requested",
    "tool_approval_resolved",
    "mcp_approval_requested",
    "mcp_approval_resolved",
    "client_request",
    "client_request_resolved",
    "queue_changed",
)

STABLE_CLIENT_NOTIFICATIONS = ("initialized",)

EXPERIMENTAL_NOTIFICATIONS = (
    "session_forked",
    "session_variables_changed",
)

OPERATIONS = tuple(
    [
        ProtocolOperation(name, "client_to_server", "request", "stable")
        for name in STABLE_REQUESTS
    ]
    + [
        ProtocolOperation(name, "client_to_server", "request", "experimental")
        for name in EXPERIMENTAL_REQUESTS
    ]
    + [
        ProtocolOperation(name, "client_to_server", "notification", "stable")
        for name in STABLE_CLIENT_NOTIFICATIONS
    ]
    + [
        ProtocolOperation(
            name,
            "server_to_client",
            "correlated_notification" if name == "client_request" else "notification",
            "stable",
        )
        for name in STABLE_NOTIFICATIONS
    ]
    + [
        ProtocolOperation(name, "server_to_client", "notification", "experimental")
        for name in EXPERIMENTAL_NOTIFICATIONS
    ]
)

OPERATION_BY_NAME = {operation.name: operation for operation in OPERATIONS}
