from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, RootModel


class JsonRpcError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: int
    message: str
    data: Any | None = None


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


class Platform(BaseModel):
    model_config = ConfigDict(extra="forbid")
    os: str
    arch: str


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    realtime: bool
    mcp_apps: bool
    client_functions: bool
    approvals: bool
    artifacts: bool
    replay: bool
    terminal: bool
    experimental_operations: list[str]
    disabled_notifications: list[str]


class InitializeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: str
    identity: Identity
    platform: Platform
    capabilities: Capabilities


class InitializeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: str
    identity: Identity
    platform: Platform
    capabilities: Capabilities


class StartRunParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str
    session_id: str | None = None
    variables: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    content: str | None = None
    workflow_name: str | None = None
    group_id: str | None = None
    safe_tool_overrides: dict[str, Any] | None = None
    prompt_role: str | None = None


class StartRunResult(BaseModel):
    run_id: str
    session_id: str


class StopRunParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str


class StopRunResult(RootModel[bool]):
    pass


class SendUserMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    content: str


class SendUserMessageResult(RootModel[None]):
    pass


class GetSessionHistoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class GetSessionHistoryResult(RootModel[list[dict[str, Any]]]):
    pass


class ListSessionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int | None = None
    offset: int | None = None


class ListSessionsResult(RootModel[list[dict[str, Any]]]):
    pass


class ArchiveSessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class ArchiveSessionResult(RootModel[bool]):
    pass


class DeleteSessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class DeleteSessionResult(RootModel[bool]):
    pass


class GetUserHistoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int | None = None
    include_archived: bool | None = None


class GetUserHistoryResult(RootModel[list[dict[str, Any]]]):
    pass


class GetAgentInfoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class GetAgentInfoResult(RootModel[dict[str, Any]]):
    pass


class ToolApprovalResponseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    decision: str
    always_approve: bool | None = None
    rejection_message: str | None = None


class ToolApprovalResponseResult(RootModel[bool]):
    pass


class McpApprovalResponseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    decision: str
    rejection_message: str | None = None


class McpApprovalResponseResult(RootModel[bool]):
    pass


class ClientFunctionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    functions: list[Any]
    version: int | None = None


class ClientFunctionsResult(RootModel[bool]):
    pass


class ClientResponseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ClientResponseResult(RootModel[bool]):
    pass


class ListServerFunctionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class ListServerFunctionsResult(RootModel[list[dict[str, Any]]]):
    pass


class ServerCallParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    function: str
    args: dict[str, Any] | None = None
    session_id: str | None = None


class ServerCallResult(RootModel[dict[str, Any]]):
    pass


class EnqueueMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    content: str
    role: str | None = None
    trigger_run: bool | None = None
    variables: dict[str, Any] | None = None
    safe_tool_overrides: dict[str, Any] | None = None
    source: str | None = None


class EnqueueMessageResult(RootModel[dict[str, Any]]):
    pass


class ListQueueParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class ListQueueResult(RootModel[dict[str, Any]]):
    pass


class CancelQueuedMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    item_id: str


class CancelQueuedMessageResult(RootModel[dict[str, Any]]):
    pass


class ResumeSessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    stream_id: str | None = None
    after_seq: int | None = None


class ResumeSessionResult(RootModel[dict[str, Any]]):
    pass


class AckEventsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    stream_id: str
    seq: int


class AckEventsResult(RootModel[dict[str, Any]]):
    pass


class ForkSessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    new_session_id: str | None = None


class ForkSessionResult(RootModel[dict[str, Any]]):
    pass


class SetSessionHoldParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    hold: bool


class SetSessionHoldResult(RootModel[dict[str, Any]]):
    pass


class QueueStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class QueueStatusResult(RootModel[dict[str, Any]]):
    pass


class EnqueueNotificationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    content: str
    source: str | None = None
    safe_tool_overrides: dict[str, Any] | None = None


class EnqueueNotificationResult(RootModel[dict[str, Any]]):
    pass


class UpdateSessionVariablesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    updates: dict[str, Any]


class UpdateSessionVariablesResult(RootModel[dict[str, Any]]):
    pass


class SetClientStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class SetClientStatusResult(RootModel[None]):
    pass


class DisableToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str


class DisableToolResult(RootModel[None]):
    pass


class DisableMcpServerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str


class DisableMcpServerResult(RootModel[None]):
    pass


class ReportIncidentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    description: str
    disable_tools: list[Any] | None = None
    disable_mcp_servers: list[Any] | None = None


class ReportIncidentResult(RootModel[None]):
    pass


class ExportSessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    redact: bool | None = None


class ExportSessionResult(RootModel[dict[str, Any]]):
    pass


class InitializedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class RunStartedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    prompt: str | None = None
    prompt_role: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class RunStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    status: str
    message: str
    attempt: int | None = None
    total: int | None = None
    next_delay: int | None = None
    seq: int | None = None
    stream_id: str | None = None


class ToolCalledParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    tool: str
    input: str
    call_id: str
    seq: int | None = None
    stream_id: str | None = None


class ToolResultParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    tool: str
    output: str
    call_id: str
    metadata: dict[str, Any] | None = None
    seq: int | None = None
    stream_id: str | None = None


class MessageOutputParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    content: str
    seq: int | None = None
    stream_id: str | None = None


class TokenParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    delta: dict[str, Any]
    totals: dict[str, Any]
    turn: int | None = None
    response_id: str | None = None
    model: str | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    truncation: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class RunEndParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    session_id: str
    end_reason: str
    usage: dict[str, Any] | None = None
    model: str | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    truncation: str | None = None
    error: dict[str, Any] | None = None
    seq: int | None = None
    stream_id: str | None = None


class ToolApprovalRequestedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    tool_name: str
    arguments: str
    metadata: dict[str, Any] | None = None
    session_id: str | None = None
    run_id: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class ToolApprovalResolvedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    session_id: str | None = None
    reason: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class McpApprovalRequestedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    request_id: str
    server_label: str
    tool_name: str
    arguments: str
    session_id: str | None = None
    run_id: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class McpApprovalResolvedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    session_id: str | None = None
    reason: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class ClientRequestParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    function: str
    args: dict[str, Any]
    session_id: str | None = None
    idempotency_key: str | None = None
    run_id: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class ClientRequestResolvedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    session_id: str | None = None
    reason: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class QueueChangedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    depth: int
    items: list[Any]
    seq: int | None = None
    stream_id: str | None = None


class SessionForkedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    new_session_id: str
    seq: int | None = None
    stream_id: str | None = None


class SessionVariablesChangedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    changed: dict[str, Any]
    variables: dict[str, Any]
    seq: int | None = None
    stream_id: str | None = None


CLIENT_REQUEST_MODELS = {
    "initialize": InitializeParams,
    "start_run": StartRunParams,
    "stop_run": StopRunParams,
    "send_user_message": SendUserMessageParams,
    "get_session_history": GetSessionHistoryParams,
    "list_sessions": ListSessionsParams,
    "archive_session": ArchiveSessionParams,
    "delete_session": DeleteSessionParams,
    "get_user_history": GetUserHistoryParams,
    "get_agent_info": GetAgentInfoParams,
    "tool_approval_response": ToolApprovalResponseParams,
    "mcp_approval_response": McpApprovalResponseParams,
    "client_functions": ClientFunctionsParams,
    "client_response": ClientResponseParams,
    "list_server_functions": ListServerFunctionsParams,
    "server_call": ServerCallParams,
    "enqueue_message": EnqueueMessageParams,
    "list_queue": ListQueueParams,
    "cancel_queued_message": CancelQueuedMessageParams,
    "resume_session": ResumeSessionParams,
    "ack_events": AckEventsParams,
    "fork_session": ForkSessionParams,
    "set_session_hold": SetSessionHoldParams,
    "queue_status": QueueStatusParams,
    "enqueue_notification": EnqueueNotificationParams,
    "update_session_variables": UpdateSessionVariablesParams,
    "set_client_status": SetClientStatusParams,
    "disable_tool": DisableToolParams,
    "disable_mcp_server": DisableMcpServerParams,
    "report_incident": ReportIncidentParams,
    "export_session": ExportSessionParams,
}
CLIENT_RESULT_MODELS = {
    "initialize": InitializeResult,
    "start_run": StartRunResult,
    "stop_run": StopRunResult,
    "send_user_message": SendUserMessageResult,
    "get_session_history": GetSessionHistoryResult,
    "list_sessions": ListSessionsResult,
    "archive_session": ArchiveSessionResult,
    "delete_session": DeleteSessionResult,
    "get_user_history": GetUserHistoryResult,
    "get_agent_info": GetAgentInfoResult,
    "tool_approval_response": ToolApprovalResponseResult,
    "mcp_approval_response": McpApprovalResponseResult,
    "client_functions": ClientFunctionsResult,
    "client_response": ClientResponseResult,
    "list_server_functions": ListServerFunctionsResult,
    "server_call": ServerCallResult,
    "enqueue_message": EnqueueMessageResult,
    "list_queue": ListQueueResult,
    "cancel_queued_message": CancelQueuedMessageResult,
    "resume_session": ResumeSessionResult,
    "ack_events": AckEventsResult,
    "fork_session": ForkSessionResult,
    "set_session_hold": SetSessionHoldResult,
    "queue_status": QueueStatusResult,
    "enqueue_notification": EnqueueNotificationResult,
    "update_session_variables": UpdateSessionVariablesResult,
    "set_client_status": SetClientStatusResult,
    "disable_tool": DisableToolResult,
    "disable_mcp_server": DisableMcpServerResult,
    "report_incident": ReportIncidentResult,
    "export_session": ExportSessionResult,
}
CLIENT_NOTIFICATION_MODELS = {"initialized": InitializedParams}
SERVER_NOTIFICATION_MODELS = {
    "run_started": RunStartedParams,
    "run_status": RunStatusParams,
    "tool_called": ToolCalledParams,
    "tool_result": ToolResultParams,
    "message_output": MessageOutputParams,
    "token": TokenParams,
    "run_end": RunEndParams,
    "tool_approval_requested": ToolApprovalRequestedParams,
    "tool_approval_resolved": ToolApprovalResolvedParams,
    "mcp_approval_requested": McpApprovalRequestedParams,
    "mcp_approval_resolved": McpApprovalResolvedParams,
    "client_request": ClientRequestParams,
    "client_request_resolved": ClientRequestResolvedParams,
    "queue_changed": QueueChangedParams,
    "session_forked": SessionForkedParams,
    "session_variables_changed": SessionVariablesChangedParams,
}
STABLE_REQUESTS = frozenset(
    [
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
    ]
)
EXPERIMENTAL_REQUESTS = frozenset(
    [
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
    ]
)
STABLE_NOTIFICATIONS = frozenset(
    [
        "initialized",
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
    ]
)
EXPERIMENTAL_NOTIFICATIONS = frozenset(["session_forked", "session_variables_changed"])
