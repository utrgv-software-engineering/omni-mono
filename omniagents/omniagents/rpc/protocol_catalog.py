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
    # MCP management, status & OAuth (tkt_kVK7F-iaL-OA). Stable on
    # entry: Desktop connector settings and runtime status consume this
    # surface. Results are open records (additive-friendly payloads).
    "mcp_list_servers",
    "mcp_get_server",
    "mcp_create_server",
    "mcp_update_server",
    "mcp_delete_server",
    "mcp_reload_server",
    "mcp_auth_start",
    "mcp_auth_complete",
    "mcp_auth_cancel",
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
    # Session/OOB MCP operations (tkt_kVK7F-iaL-OA): promoted from the
    # ``mcp.*`` server functions; experimental until a client depends on
    # the RPC form.
    "mcp_read_resource",
    "mcp_call_tool",
    "mcp_get_prompt",
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
    # Typed git operations & interactive diff (tkt_l_l0lUi4A5V2).
    # Experimental on entry, matching the fs_* precedent: the Omni
    # Desktop interactive diff UI is a follow-up, so the hunk model has
    # not yet been proven by a shipped consumer. Clients opt in via
    # initialize.capabilities.experimental_operations.
    "git_list_repositories",
    "git_status",
    "git_diff",
    "git_log",
    "git_list_branches",
    "git_list_worktrees",
    "git_conflicts",
    "git_stage",
    "git_unstage",
    "git_discard",
    "git_commit",
    "git_checkout",
    "git_reset",
    "git_fetch",
    "git_pull",
    "git_push",
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
    # MCP startup/auth transitions (tkt_kVK7F-iaL-OA). Broadcast, not
    # journaled: server status is process-global (the account_changed
    # precedent) — clients refetch via mcp_list_servers on reconnect.
    "mcp_server_status_changed",
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
    # Lifecycle progress for long-running git operations
    # (tkt_l_l0lUi4A5V2). The phases are the server's own state machine,
    # never a reading of git's human-facing progress output. Not
    # journaled: like the fs_* events these are per-connection and
    # derivable — a reconnecting client refetches git_status.
    "git_operation_progress",
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
