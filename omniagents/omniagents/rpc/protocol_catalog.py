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
    # Provider account & usage surface (tkt_unKKDF-5OOgc)
    "account_status",
    "account_login_start",
    "account_login_complete",
    "account_login_cancel",
    "account_logout",
    "account_refresh",
    "account_usage",
    "account_select",
    # Structured user elicitation (tkt_F9tu2m9PjdpY)
    "elicitation_response",
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
    # Workspace filesystem watch & chunked transfer (tkt_B5fXOZHf08K1).
    # Experimental on entry per the governance default for brand-new
    # surfaces: clients opt in via initialize.capabilities
    # .experimental_operations until the shape has proven itself in the
    # desktop client.
    "fs_watch",
    "fs_unwatch",
    "fs_list",
    "fs_stat",
    "fs_download_open",
    "fs_download_read",
    "fs_download_close",
    "fs_upload_open",
    "fs_upload_chunk",
    "fs_upload_commit",
    "fs_upload_abort",
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
    "account_changed",
    # Structured user elicitation (tkt_F9tu2m9PjdpY)
    "elicitation_requested",
    "elicitation_resolved",
)

STABLE_CLIENT_NOTIFICATIONS = ("initialized",)

#: Server→client notifications that expect a correlated client→server
#: request in response. The mapping drives the ``x-omni-correlation``
#: metadata in the generated OpenRPC document.
CORRELATED_NOTIFICATIONS = {
    "client_request": {
        "requestField": "request_id",
        "responseMethod": "client_response",
    },
    "elicitation_requested": {
        "requestField": "elicitation_id",
        "responseMethod": "elicitation_response",
    },
}

EXPERIMENTAL_NOTIFICATIONS = (
    "session_forked",
    "session_variables_changed",
    # Filesystem watch/transfer events (tkt_B5fXOZHf08K1). Per-connection
    # and never journaled: after a reconnect clients re-subscribe
    # (fs_watch) and rescan (fs_list) instead of replaying.
    "fs_events",
    "fs_rescan_required",
    "fs_transfer_progress",
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
            (
                "correlated_notification"
                if name in CORRELATED_NOTIFICATIONS
                else "notification"
            ),
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
