from dataclasses import dataclass
from enum import IntFlag, StrEnum, auto
from typing import Literal

Direction = Literal["client_to_server", "server_to_client"]
Kind = Literal["request", "notification", "correlated_notification"]
Stability = Literal["stable", "experimental"]


class ResourceScope(StrEnum):
    """Resource whose identity and ownership govern an operation."""

    CONNECTION = "connection"
    PRINCIPAL = "principal"
    AGENT_HOST = "agent_host"
    AGENT = "agent"
    THREAD = "thread"
    RUN = "run"
    WORKSPACE = "workspace"
    ENVIRONMENT = "environment"
    EXTENSION = "extension"


class ExecutionRequirement(IntFlag):
    """Capabilities or deferred planning required before an operation runs."""

    NONE = 0
    RUN_PLAN = auto()
    WORKSPACE_READ = auto()
    WORKSPACE_WRITE = auto()
    PROCESS = auto()
    NETWORK = auto()
    PTY = auto()
    ENVIRONMENT_LIFECYCLE = auto()
    EXTENSION_DECLARED = auto()
    OPTIONAL_WORKSPACE_READ = auto()
    """Acquire a workspace-read lease when an environment is explicitly sent.

    Unlike :attr:`WORKSPACE_READ`, the operation remains valid without an
    environment. This is for control-plane functions with an optional
    workspace-local override, not for filesystem APIs that require a target.
    """


@dataclass(frozen=True)
class ProtocolOperation:
    name: str
    direction: Direction
    kind: Kind
    stability: Stability
    resource_scope: ResourceScope
    execution_requirements: ExecutionRequirement


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
    # Canonical conversation domain objects (tkt_a-QF2Oc4OAsD). Stable on
    # entry — Desktop renders transcripts from this surface, and the
    # deterministic-rendering guarantee is worthless if clients have to
    # opt in via experimental_operations. Results are open records: item
    # ``content`` shape follows the item kind and grows with the tools.
    "get_thread",
    "list_turns",
    "list_items",
    "get_item",
)

EXPERIMENTAL_REQUESTS = (
    # Approval reviewer ("Approve for me"): per-session routing of
    # tool/MCP approvals to the guardian reviewer instead of the human.
    "set_session_approvals",
    # Workflow completion reviewer: per-session routing of plan-step
    # completions with exit criteria to the plan-review guardian, the
    # human, or nobody.
    "set_session_workflow",
    # AgentHost provisioning control plane. These are introduced together so
    # clients never have to smuggle Workspace/profile identity through a Run.
    "agent_host_register_workspace",
    "agent_host_register_profile",
    "agent_host_bind_thread",
    "agent_host_list_resources",
    "agent_host_materialize_environment",
    "agent_host_stop_environment",
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
    # Workspace file management (delete/rename/mkdir) for the desktop
    # Files tree. Experimental on entry, same posture as the transfer
    # operations above: mutations run the identical effective-writability
    # policy as fs_upload_commit.
    "fs_delete",
    "fs_rename",
    "fs_mkdir",
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
    # Server-side repository-status watch: the server polls porcelain
    # status (git applies ignore rules, so this stays bounded where a
    # recursive fs watch of a worktree would not) and pushes a coalesced
    # git_status_changed digest. Channel-bound like fs_watch.
    "git_status_watch",
    "git_status_unwatch",
    # Conversation organization, search & branching (tkt_eZldW-Gje6kn).
    # Experimental on entry, following the fs_*/git_* precedent for a
    # brand-new surface: the filter set and the two cursor shapes (keyset
    # for the listing, rank-offset for search) have not yet been proven by
    # a shipped client, and unlike the tkt_a-QF2Oc4OAsD READS there is no
    # deterministic-rendering guarantee that opting in would undermine.
    # Clients opt in via initialize.capabilities.experimental_operations.
    "list_threads",
    "search_threads",
    "update_thread",
    "list_thread_descendants",
    "export_thread",
    "purge_threads",
    # Durable structured plans & aggregated run diffs (tkt_FqUypyxF2tzC).
    # Experimental on entry, matching the fs_*/git_* precedent: the Omni
    # Desktop plan and diff components are an explicit follow-up, so no
    # shipped consumer has exercised these payload shapes yet. Clients opt
    # in via initialize.capabilities.experimental_operations. Both name a
    # ``thread_id`` (== session_id) and are session-owned.
    "get_plan",
    "get_run_diff",
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
    # Reviewer/sandbox-resolved approval decisions that never reached the
    # client as prompts. Journaled: transcripts and replay must show them.
    "tool_approval_reviewed",
    # Plan-step completion review outcomes that need surfacing: rejections,
    # unverified accepts, and breaker/human escalations. Clean verifications
    # and waived completions are deliberately silent — the plan snapshot
    # already carries the verified state.
    "plan_completion_reviewed",
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
    # Repository status moved (git_status_watch). Carries only a digest —
    # never repository content — and is per-connection and derivable, so
    # like the fs_* events it is not journaled: a reconnecting client
    # re-subscribes and refetches git_status.
    "git_status_changed",
    # Thread metadata changed (tkt_eZldW-Gje6kn). Connection-wide, so a
    # rename or an archive reaches the sidebar of every open column and not
    # just the one already displaying the thread. Not journaled: this is
    # current state, not conversation history, and replaying a stale rename
    # on reconnect would be wrong — a reconnecting client refetches
    # list_threads. Delivered only to channels whose principal owns the
    # thread.
    "thread_updated",
    # A conversation item changed after it was first delivered
    # (tkt_FqUypyxF2tzC). Deliberately kind-agnostic: today plans and run
    # diffs revise many times inside a run with no other event marking the
    # change, and approvals/elicitations can adopt the same channel later
    # without a second notification. Journaled and coalesced per item_id —
    # unlike the fs_*/git_* progress events this carries authoritative
    # state, so a reconnecting client must receive the latest revision.
    "item_updated",
)


def _classification_map(
    groups: tuple[tuple[ResourceScope, ExecutionRequirement, tuple[str, ...]], ...],
) -> dict[str, tuple[ResourceScope, ExecutionRequirement]]:
    result: dict[str, tuple[ResourceScope, ExecutionRequirement]] = {}
    for resource_scope, execution_requirements, names in groups:
        for name in names:
            if name in result:
                raise RuntimeError(f"duplicate protocol classification for {name}")
            result[name] = (resource_scope, execution_requirements)
    return result


_REQUEST_CLASSIFICATIONS = _classification_map(
    (
        (
            ResourceScope.CONNECTION,
            ExecutionRequirement.NONE,
            ("initialize", "client_functions", "set_client_status"),
        ),
        (
            ResourceScope.THREAD,
            ExecutionRequirement.RUN_PLAN,
            ("start_run",),
        ),
        (
            ResourceScope.RUN,
            ExecutionRequirement.NONE,
            (
                "stop_run",
                "send_user_message",
                "tool_approval_response",
                "mcp_approval_response",
                "client_response",
                "elicitation_response",
            ),
        ),
        (
            ResourceScope.THREAD,
            ExecutionRequirement.NONE,
            (
                "get_session_history",
                "archive_session",
                "delete_session",
                "enqueue_message",
                "list_queue",
                "cancel_queued_message",
                "resume_session",
                "ack_events",
                "set_session_model",
                "set_session_reasoning",
                "set_session_approvals",
                "set_session_workflow",
                "get_thread",
                "list_turns",
                "list_items",
                "get_item",
                "fork_session",
                "set_session_hold",
                "queue_status",
                "enqueue_notification",
                "update_session_variables",
                "report_incident",
                "export_session",
                "mcp_call_tool",
                "update_thread",
                "list_thread_descendants",
                "export_thread",
                "get_plan",
                "get_run_diff",
                "agent_host_bind_thread",
            ),
        ),
        (
            ResourceScope.PRINCIPAL,
            ExecutionRequirement.NONE,
            (
                "list_sessions",
                "get_user_history",
                "account_status",
                "account_login_start",
                "account_login_complete",
                "account_login_cancel",
                "account_logout",
                "account_refresh",
                "account_usage",
                "account_select",
                "list_threads",
                "search_threads",
            ),
        ),
        (
            ResourceScope.AGENT,
            ExecutionRequirement.NONE,
            (
                "get_agent_info",
                "list_server_functions",
                "list_models",
                "get_model",
                "list_providers",
                "mcp_list_servers",
                "mcp_get_server",
                "mcp_create_server",
                "mcp_update_server",
                "mcp_delete_server",
                "mcp_reload_server",
                "mcp_auth_start",
                "mcp_auth_complete",
                "mcp_auth_cancel",
                "disable_tool",
                "disable_mcp_server",
                "mcp_read_resource",
                "mcp_get_prompt",
            ),
        ),
        (
            ResourceScope.AGENT_HOST,
            ExecutionRequirement.NONE,
            (
                "set_voice_model",
                "get_config",
                "validate_config",
                "write_config",
                "purge_threads",
                "agent_host_register_workspace",
                "agent_host_register_profile",
                "agent_host_list_resources",
            ),
        ),
        (
            ResourceScope.ENVIRONMENT,
            ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
            ("agent_host_stop_environment",),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.NONE,
            ("agent_host_materialize_environment",),
        ),
        (
            ResourceScope.EXTENSION,
            ExecutionRequirement.EXTENSION_DECLARED,
            ("server_call",),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.WORKSPACE_READ,
            (
                "fs_watch",
                "fs_unwatch",
                "fs_list",
                "fs_stat",
                "fs_download_open",
                "fs_download_read",
                "fs_download_close",
            ),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.WORKSPACE_WRITE,
            (
                "fs_upload_open",
                "fs_upload_chunk",
                "fs_upload_commit",
                "fs_upload_abort",
                "fs_delete",
                "fs_rename",
                "fs_mkdir",
            ),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.WORKSPACE_READ | ExecutionRequirement.PROCESS,
            (
                "git_list_repositories",
                "git_status",
                "git_diff",
                "git_log",
                "git_list_branches",
                "git_list_worktrees",
                "git_conflicts",
                "git_status_watch",
                "git_status_unwatch",
            ),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.WORKSPACE_WRITE | ExecutionRequirement.PROCESS,
            (
                "git_stage",
                "git_unstage",
                "git_discard",
                "git_commit",
                "git_checkout",
                "git_reset",
            ),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.WORKSPACE_WRITE
            | ExecutionRequirement.PROCESS
            | ExecutionRequirement.NETWORK,
            ("git_fetch", "git_pull", "git_push"),
        ),
    )
)

_CLIENT_NOTIFICATION_CLASSIFICATIONS = _classification_map(
    (
        (
            ResourceScope.CONNECTION,
            ExecutionRequirement.NONE,
            STABLE_CLIENT_NOTIFICATIONS,
        ),
    )
)

_SERVER_NOTIFICATION_CLASSIFICATIONS = _classification_map(
    (
        (
            ResourceScope.RUN,
            ExecutionRequirement.NONE,
            (
                "run_started",
                "run_status",
                "tool_called",
                "tool_result",
                "message_output",
                "token",
                "run_end",
                "tool_approval_requested",
                "tool_approval_resolved",
                "tool_approval_reviewed",
                "plan_completion_reviewed",
                "mcp_approval_requested",
                "mcp_approval_resolved",
                "client_request",
                "client_request_resolved",
                "elicitation_requested",
                "elicitation_resolved",
            ),
        ),
        (
            ResourceScope.THREAD,
            ExecutionRequirement.NONE,
            (
                "queue_changed",
                "session_forked",
                "session_variables_changed",
                "thread_updated",
                "item_updated",
            ),
        ),
        (
            ResourceScope.PRINCIPAL,
            ExecutionRequirement.NONE,
            ("account_changed",),
        ),
        (
            ResourceScope.AGENT,
            ExecutionRequirement.NONE,
            ("mcp_server_status_changed",),
        ),
        (
            ResourceScope.WORKSPACE,
            ExecutionRequirement.NONE,
            (
                "fs_events",
                "fs_rescan_required",
                "fs_transfer_progress",
                "git_operation_progress",
                "git_status_changed",
            ),
        ),
    )
)


def _operation(
    name: str,
    direction: Direction,
    kind: Kind,
    stability: Stability,
) -> ProtocolOperation:
    if kind == "request":
        resource_scope, execution_requirements = _REQUEST_CLASSIFICATIONS[name]
    elif direction == "client_to_server":
        resource_scope, execution_requirements = _CLIENT_NOTIFICATION_CLASSIFICATIONS[
            name
        ]
    else:
        resource_scope, execution_requirements = _SERVER_NOTIFICATION_CLASSIFICATIONS[
            name
        ]
    return ProtocolOperation(
        name=name,
        direction=direction,
        kind=kind,
        stability=stability,
        resource_scope=resource_scope,
        execution_requirements=execution_requirements,
    )


OPERATIONS = tuple(
    [
        _operation(name, "client_to_server", "request", "stable")
        for name in STABLE_REQUESTS
    ]
    + [
        _operation(name, "client_to_server", "request", "experimental")
        for name in EXPERIMENTAL_REQUESTS
    ]
    + [
        _operation(name, "client_to_server", "notification", "stable")
        for name in STABLE_CLIENT_NOTIFICATIONS
    ]
    + [
        _operation(
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
        _operation(name, "server_to_client", "notification", "experimental")
        for name in EXPERIMENTAL_NOTIFICATIONS
    ]
)

OPERATION_BY_NAME = {operation.name: operation for operation in OPERATIONS}
