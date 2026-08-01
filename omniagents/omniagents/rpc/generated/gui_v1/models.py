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
    cascade: bool | None = None


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


class ListModelsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_hidden: bool | None = None
    modality: str | None = None
    session_id: str | None = None


class ListModelsResult(RootModel[dict[str, Any]]):
    pass


class GetModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str


class GetModelResult(RootModel[dict[str, Any]]):
    pass


class ListProvidersParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class ListProvidersResult(RootModel[dict[str, Any]]):
    pass


class SetSessionModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    model: str


class SetSessionModelResult(RootModel[dict[str, Any]]):
    pass


class SetSessionReasoningParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    effort: str


class SetSessionReasoningResult(RootModel[dict[str, Any]]):
    pass


class SetVoiceModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str


class SetVoiceModelResult(RootModel[dict[str, Any]]):
    pass


class AccountStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class AccountStatusResult(RootModel[dict[str, Any]]):
    pass


class AccountLoginStartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    mode: str
    api_key: str | None = None
    redirect_uri: str | None = None


class AccountLoginStartResult(RootModel[dict[str, Any]]):
    pass


class AccountLoginCompleteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str
    code: str | None = None


class AccountLoginCompleteResult(RootModel[dict[str, Any]]):
    pass


class AccountLoginCancelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_id: str


class AccountLoginCancelResult(RootModel[bool]):
    pass


class AccountLogoutParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


class AccountLogoutResult(RootModel[dict[str, Any]]):
    pass


class AccountRefreshParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


class AccountRefreshResult(RootModel[dict[str, Any]]):
    pass


class AccountUsageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str | None = None


class AccountUsageResult(RootModel[dict[str, Any]]):
    pass


class AccountSelectParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str


class AccountSelectResult(RootModel[dict[str, Any]]):
    pass


class ElicitationResponseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    elicitation_id: str
    action: str
    value: dict[str, Any] | None = None
    reason: str | None = None


class ElicitationResponseResult(RootModel[dict[str, Any]]):
    pass


class McpListServersParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = None


class McpListServersResult(RootModel[dict[str, Any]]):
    pass


class McpGetServerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    refresh: bool | None = None


class McpGetServerResult(RootModel[dict[str, Any]]):
    pass


class McpCreateServerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    type: str
    params: dict[str, Any]
    server_options: dict[str, Any] | None = None


class McpCreateServerResult(RootModel[dict[str, Any]]):
    pass


class McpUpdateServerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    type: str | None = None
    params: dict[str, Any] | None = None
    server_options: dict[str, Any] | None = None


class McpUpdateServerResult(RootModel[dict[str, Any]]):
    pass


class McpDeleteServerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str


class McpDeleteServerResult(RootModel[dict[str, Any]]):
    pass


class McpReloadServerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str | None = None


class McpReloadServerResult(RootModel[dict[str, Any]]):
    pass


class McpAuthStartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    redirect_uri: str | None = None
    session_id: str | None = None


class McpAuthStartResult(RootModel[dict[str, Any]]):
    pass


class McpAuthCompleteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auth_id: str
    code: str


class McpAuthCompleteResult(RootModel[dict[str, Any]]):
    pass


class McpAuthCancelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auth_id: str


class McpAuthCancelResult(RootModel[bool]):
    pass


class GetThreadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str


class GetThreadResult(RootModel[dict[str, Any]]):
    pass


class ListTurnsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    limit: int | None = None
    cursor: str | None = None
    order: str | None = None


class ListTurnsResult(RootModel[dict[str, Any]]):
    pass


class ListItemsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    turn_id: str | None = None
    kinds: list[Any] | None = None
    limit: int | None = None
    cursor: str | None = None
    order: str | None = None


class ListItemsResult(RootModel[dict[str, Any]]):
    pass


class GetItemParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    item_id: str


class GetItemResult(RootModel[dict[str, Any]]):
    pass


class ForkSessionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    new_session_id: str | None = None
    from_item_id: str | None = None
    from_turn_id: str | None = None


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


class GetConfigParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass


class GetConfigResult(RootModel[dict[str, Any]]):
    pass


class ValidateConfigParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: dict[str, Any]


class ValidateConfigResult(RootModel[dict[str, Any]]):
    pass


class WriteConfigParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: dict[str, Any]


class WriteConfigResult(RootModel[dict[str, Any]]):
    pass


class McpReadResourceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    uri: str
    session_id: str | None = None


class McpReadResourceResult(RootModel[dict[str, Any]]):
    pass


class McpCallToolParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    tool_name: str
    session_id: str
    args: dict[str, Any] | None = None


class McpCallToolResult(RootModel[dict[str, Any]]):
    pass


class McpGetPromptParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    prompt_name: str
    args: dict[str, Any] | None = None
    session_id: str | None = None


class McpGetPromptResult(RootModel[dict[str, Any]]):
    pass


class FsWatchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str
    recursive: bool | None = None
    poll_interval_ms: int | None = None


class FsWatchResult(RootModel[dict[str, Any]]):
    pass


class FsUnwatchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    watch_id: str


class FsUnwatchResult(RootModel[bool]):
    pass


class FsListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str
    recursive: bool | None = None


class FsListResult(RootModel[dict[str, Any]]):
    pass


class FsStatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str


class FsStatResult(RootModel[dict[str, Any]]):
    pass


class FsDownloadOpenParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str


class FsDownloadOpenResult(RootModel[dict[str, Any]]):
    pass


class FsDownloadReadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    transfer_id: str
    offset: int | None = None
    length: int | None = None


class FsDownloadReadResult(RootModel[dict[str, Any]]):
    pass


class FsDownloadCloseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    transfer_id: str


class FsDownloadCloseResult(RootModel[bool]):
    pass


class FsUploadOpenParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str
    size: int
    sha256: str | None = None
    expected_sha256: str | None = None
    overwrite: bool | None = None


class FsUploadOpenResult(RootModel[dict[str, Any]]):
    pass


class FsUploadChunkParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    transfer_id: str
    offset: int
    data: str


class FsUploadChunkResult(RootModel[dict[str, Any]]):
    pass


class FsUploadCommitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    transfer_id: str


class FsUploadCommitResult(RootModel[dict[str, Any]]):
    pass


class FsUploadAbortParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    transfer_id: str


class FsUploadAbortResult(RootModel[bool]):
    pass


class GitListRepositoriesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    path: str | None = None
    max_depth: int | None = None


class GitListRepositoriesResult(RootModel[dict[str, Any]]):
    pass


class GitStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    include_untracked: bool | None = None
    include_ignored: bool | None = None
    paths: list[Any] | None = None


class GitStatusResult(RootModel[dict[str, Any]]):
    pass


class GitDiffParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    mode: str | None = None
    paths: list[Any] | None = None
    context_lines: int | None = None
    from_rev: str | None = None
    to_rev: str | None = None


class GitDiffResult(RootModel[dict[str, Any]]):
    pass


class GitLogParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    rev: str | None = None
    max_count: int | None = None
    skip: int | None = None
    paths: list[Any] | None = None


class GitLogResult(RootModel[dict[str, Any]]):
    pass


class GitListBranchesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    include_remote: bool | None = None


class GitListBranchesResult(RootModel[dict[str, Any]]):
    pass


class GitListWorktreesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str


class GitListWorktreesResult(RootModel[dict[str, Any]]):
    pass


class GitConflictsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    paths: list[Any] | None = None


class GitConflictsResult(RootModel[dict[str, Any]]):
    pass


class GitStageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    paths: list[Any] | None = None
    hunks: list[Any] | None = None
    context_lines: int | None = None
    mode: str | None = None


class GitStageResult(RootModel[dict[str, Any]]):
    pass


class GitUnstageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    paths: list[Any] | None = None
    hunks: list[Any] | None = None
    context_lines: int | None = None


class GitUnstageResult(RootModel[dict[str, Any]]):
    pass


class GitDiscardParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    paths: list[Any] | None = None
    hunks: list[Any] | None = None
    context_lines: int | None = None
    confirmation_token: str | None = None


class GitDiscardResult(RootModel[dict[str, Any]]):
    pass


class GitCommitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    message: str
    amend: bool | None = None
    allow_empty: bool | None = None
    author: str | None = None
    confirmation_token: str | None = None


class GitCommitResult(RootModel[dict[str, Any]]):
    pass


class GitCheckoutParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    branch: str
    create: bool | None = None
    start_point: str | None = None
    detach: bool | None = None
    discard_changes: bool | None = None
    confirmation_token: str | None = None


class GitCheckoutResult(RootModel[dict[str, Any]]):
    pass


class GitResetParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    mode: str | None = None
    rev: str | None = None
    paths: list[Any] | None = None
    confirmation_token: str | None = None


class GitResetResult(RootModel[dict[str, Any]]):
    pass


class GitFetchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    remote: str | None = None
    refspec: str | None = None
    prune: bool | None = None


class GitFetchResult(RootModel[dict[str, Any]]):
    pass


class GitPullParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    remote: str | None = None
    refspec: str | None = None
    rebase: bool | None = None


class GitPullResult(RootModel[dict[str, Any]]):
    pass


class GitPushParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    repo: str
    remote: str | None = None
    refspec: str | None = None
    force: bool | None = None
    force_with_lease: bool | None = None
    set_upstream: bool | None = None
    confirmation_token: str | None = None


class GitPushResult(RootModel[dict[str, Any]]):
    pass


class ListThreadsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str | None = None
    pinned: bool | None = None
    source: str | None = None
    model: str | None = None
    parent_thread_id: str | None = None
    created_after: float | None = None
    created_before: float | None = None
    updated_after: float | None = None
    updated_before: float | None = None
    limit: int | None = None
    cursor: str | None = None
    order: str | None = None


class ListThreadsResult(RootModel[dict[str, Any]]):
    pass


class SearchThreadsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    status: str | None = None
    pinned: bool | None = None
    source: str | None = None
    model: str | None = None
    parent_thread_id: str | None = None
    created_after: float | None = None
    created_before: float | None = None
    updated_after: float | None = None
    updated_before: float | None = None
    limit: int | None = None
    cursor: str | None = None


class SearchThreadsResult(RootModel[dict[str, Any]]):
    pass


class UpdateThreadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    title: str | None = None
    pinned: bool | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None


class UpdateThreadResult(RootModel[dict[str, Any]]):
    pass


class ListThreadDescendantsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    max_depth: int | None = None
    limit: int | None = None


class ListThreadDescendantsResult(RootModel[dict[str, Any]]):
    pass


class ExportThreadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    limit: int | None = None
    cursor: str | None = None
    include_descendants: bool | None = None


class ExportThreadResult(RootModel[dict[str, Any]]):
    pass


class PurgeThreadsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retention_days: int
    dry_run: bool | None = None


class PurgeThreadsResult(RootModel[dict[str, Any]]):
    pass


class GetPlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    scope: str | None = None


class GetPlanResult(RootModel[dict[str, Any]]):
    pass


class GetRunDiffParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    turn_id: str | None = None


class GetRunDiffResult(RootModel[dict[str, Any]]):
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
    is_final: bool | None = None
    message_id: str | None = None
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
    model_ref: str | None = None
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
    model_ref: str | None = None
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


class AccountChangedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    reason: str
    account: dict[str, Any] | None = None
    seq: int | None = None
    stream_id: str | None = None


class ElicitationRequestedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    elicitation_id: str
    kind: str
    message: str
    title: str | None = None
    input_schema: dict[str, Any] | None = None
    options: list[Any] | None = None
    url: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    item_id: str | None = None
    source: str | None = None
    timeout_ms: int | None = None
    expires_at: str | None = None
    persist_response: bool | None = None
    seq: int | None = None
    stream_id: str | None = None


class ElicitationResolvedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    elicitation_id: str
    status: str
    session_id: str | None = None
    run_id: str | None = None
    action: str | None = None
    value: dict[str, Any] | None = None
    reason: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class McpServerStatusChangedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str
    status: str
    previous_status: str | None = None
    reason_code: str | None = None
    reason: str | None = None
    auth_state: str | None = None
    at: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class SessionForkedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    new_session_id: str
    from_item_id: str | None = None
    seq: int | None = None
    stream_id: str | None = None


class SessionVariablesChangedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    changed: list[Any]
    variables: dict[str, Any]
    seq: int | None = None
    stream_id: str | None = None


class FsEventsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    watch_id: str
    events: list[Any]
    seq: int | None = None
    stream_id: str | None = None


class FsRescanRequiredParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    watch_id: str
    reason: str
    seq: int | None = None
    stream_id: str | None = None


class FsTransferProgressParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    transfer_id: str
    direction: str
    transferred: int
    total: int
    seq: int | None = None
    stream_id: str | None = None


class GitOperationProgressParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    operation_id: str
    repo: str
    operation: str
    phase: str
    detail: dict[str, Any] | None = None
    seq: int | None = None
    stream_id: str | None = None


class ThreadUpdatedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str
    changed: list[Any]
    thread: dict[str, Any]
    cascaded_thread_ids: list[Any] | None = None
    seq: int | None = None
    stream_id: str | None = None


class ItemUpdatedParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    thread_id: str
    item_id: str
    kind: str
    status: str
    revision: int
    item_seq: int
    content: dict[str, Any]
    turn_id: str | None = None
    updated_at: float | None = None
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
    "list_models": ListModelsParams,
    "get_model": GetModelParams,
    "list_providers": ListProvidersParams,
    "set_session_model": SetSessionModelParams,
    "set_session_reasoning": SetSessionReasoningParams,
    "set_voice_model": SetVoiceModelParams,
    "account_status": AccountStatusParams,
    "account_login_start": AccountLoginStartParams,
    "account_login_complete": AccountLoginCompleteParams,
    "account_login_cancel": AccountLoginCancelParams,
    "account_logout": AccountLogoutParams,
    "account_refresh": AccountRefreshParams,
    "account_usage": AccountUsageParams,
    "account_select": AccountSelectParams,
    "elicitation_response": ElicitationResponseParams,
    "mcp_list_servers": McpListServersParams,
    "mcp_get_server": McpGetServerParams,
    "mcp_create_server": McpCreateServerParams,
    "mcp_update_server": McpUpdateServerParams,
    "mcp_delete_server": McpDeleteServerParams,
    "mcp_reload_server": McpReloadServerParams,
    "mcp_auth_start": McpAuthStartParams,
    "mcp_auth_complete": McpAuthCompleteParams,
    "mcp_auth_cancel": McpAuthCancelParams,
    "get_thread": GetThreadParams,
    "list_turns": ListTurnsParams,
    "list_items": ListItemsParams,
    "get_item": GetItemParams,
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
    "get_config": GetConfigParams,
    "validate_config": ValidateConfigParams,
    "write_config": WriteConfigParams,
    "mcp_read_resource": McpReadResourceParams,
    "mcp_call_tool": McpCallToolParams,
    "mcp_get_prompt": McpGetPromptParams,
    "fs_watch": FsWatchParams,
    "fs_unwatch": FsUnwatchParams,
    "fs_list": FsListParams,
    "fs_stat": FsStatParams,
    "fs_download_open": FsDownloadOpenParams,
    "fs_download_read": FsDownloadReadParams,
    "fs_download_close": FsDownloadCloseParams,
    "fs_upload_open": FsUploadOpenParams,
    "fs_upload_chunk": FsUploadChunkParams,
    "fs_upload_commit": FsUploadCommitParams,
    "fs_upload_abort": FsUploadAbortParams,
    "git_list_repositories": GitListRepositoriesParams,
    "git_status": GitStatusParams,
    "git_diff": GitDiffParams,
    "git_log": GitLogParams,
    "git_list_branches": GitListBranchesParams,
    "git_list_worktrees": GitListWorktreesParams,
    "git_conflicts": GitConflictsParams,
    "git_stage": GitStageParams,
    "git_unstage": GitUnstageParams,
    "git_discard": GitDiscardParams,
    "git_commit": GitCommitParams,
    "git_checkout": GitCheckoutParams,
    "git_reset": GitResetParams,
    "git_fetch": GitFetchParams,
    "git_pull": GitPullParams,
    "git_push": GitPushParams,
    "list_threads": ListThreadsParams,
    "search_threads": SearchThreadsParams,
    "update_thread": UpdateThreadParams,
    "list_thread_descendants": ListThreadDescendantsParams,
    "export_thread": ExportThreadParams,
    "purge_threads": PurgeThreadsParams,
    "get_plan": GetPlanParams,
    "get_run_diff": GetRunDiffParams,
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
    "list_models": ListModelsResult,
    "get_model": GetModelResult,
    "list_providers": ListProvidersResult,
    "set_session_model": SetSessionModelResult,
    "set_session_reasoning": SetSessionReasoningResult,
    "set_voice_model": SetVoiceModelResult,
    "account_status": AccountStatusResult,
    "account_login_start": AccountLoginStartResult,
    "account_login_complete": AccountLoginCompleteResult,
    "account_login_cancel": AccountLoginCancelResult,
    "account_logout": AccountLogoutResult,
    "account_refresh": AccountRefreshResult,
    "account_usage": AccountUsageResult,
    "account_select": AccountSelectResult,
    "elicitation_response": ElicitationResponseResult,
    "mcp_list_servers": McpListServersResult,
    "mcp_get_server": McpGetServerResult,
    "mcp_create_server": McpCreateServerResult,
    "mcp_update_server": McpUpdateServerResult,
    "mcp_delete_server": McpDeleteServerResult,
    "mcp_reload_server": McpReloadServerResult,
    "mcp_auth_start": McpAuthStartResult,
    "mcp_auth_complete": McpAuthCompleteResult,
    "mcp_auth_cancel": McpAuthCancelResult,
    "get_thread": GetThreadResult,
    "list_turns": ListTurnsResult,
    "list_items": ListItemsResult,
    "get_item": GetItemResult,
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
    "get_config": GetConfigResult,
    "validate_config": ValidateConfigResult,
    "write_config": WriteConfigResult,
    "mcp_read_resource": McpReadResourceResult,
    "mcp_call_tool": McpCallToolResult,
    "mcp_get_prompt": McpGetPromptResult,
    "fs_watch": FsWatchResult,
    "fs_unwatch": FsUnwatchResult,
    "fs_list": FsListResult,
    "fs_stat": FsStatResult,
    "fs_download_open": FsDownloadOpenResult,
    "fs_download_read": FsDownloadReadResult,
    "fs_download_close": FsDownloadCloseResult,
    "fs_upload_open": FsUploadOpenResult,
    "fs_upload_chunk": FsUploadChunkResult,
    "fs_upload_commit": FsUploadCommitResult,
    "fs_upload_abort": FsUploadAbortResult,
    "git_list_repositories": GitListRepositoriesResult,
    "git_status": GitStatusResult,
    "git_diff": GitDiffResult,
    "git_log": GitLogResult,
    "git_list_branches": GitListBranchesResult,
    "git_list_worktrees": GitListWorktreesResult,
    "git_conflicts": GitConflictsResult,
    "git_stage": GitStageResult,
    "git_unstage": GitUnstageResult,
    "git_discard": GitDiscardResult,
    "git_commit": GitCommitResult,
    "git_checkout": GitCheckoutResult,
    "git_reset": GitResetResult,
    "git_fetch": GitFetchResult,
    "git_pull": GitPullResult,
    "git_push": GitPushResult,
    "list_threads": ListThreadsResult,
    "search_threads": SearchThreadsResult,
    "update_thread": UpdateThreadResult,
    "list_thread_descendants": ListThreadDescendantsResult,
    "export_thread": ExportThreadResult,
    "purge_threads": PurgeThreadsResult,
    "get_plan": GetPlanResult,
    "get_run_diff": GetRunDiffResult,
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
    "account_changed": AccountChangedParams,
    "elicitation_requested": ElicitationRequestedParams,
    "elicitation_resolved": ElicitationResolvedParams,
    "mcp_server_status_changed": McpServerStatusChangedParams,
    "session_forked": SessionForkedParams,
    "session_variables_changed": SessionVariablesChangedParams,
    "fs_events": FsEventsParams,
    "fs_rescan_required": FsRescanRequiredParams,
    "fs_transfer_progress": FsTransferProgressParams,
    "git_operation_progress": GitOperationProgressParams,
    "thread_updated": ThreadUpdatedParams,
    "item_updated": ItemUpdatedParams,
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
        "list_models",
        "get_model",
        "list_providers",
        "set_session_model",
        "set_session_reasoning",
        "set_voice_model",
        "account_status",
        "account_login_start",
        "account_login_complete",
        "account_login_cancel",
        "account_logout",
        "account_refresh",
        "account_usage",
        "account_select",
        "elicitation_response",
        "mcp_list_servers",
        "mcp_get_server",
        "mcp_create_server",
        "mcp_update_server",
        "mcp_delete_server",
        "mcp_reload_server",
        "mcp_auth_start",
        "mcp_auth_complete",
        "mcp_auth_cancel",
        "get_thread",
        "list_turns",
        "list_items",
        "get_item",
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
        "get_config",
        "validate_config",
        "write_config",
        "mcp_read_resource",
        "mcp_call_tool",
        "mcp_get_prompt",
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
        "list_threads",
        "search_threads",
        "update_thread",
        "list_thread_descendants",
        "export_thread",
        "purge_threads",
        "get_plan",
        "get_run_diff",
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
        "account_changed",
        "elicitation_requested",
        "elicitation_resolved",
        "mcp_server_status_changed",
    ]
)
EXPERIMENTAL_NOTIFICATIONS = frozenset(
    [
        "session_forked",
        "session_variables_changed",
        "fs_events",
        "fs_rescan_required",
        "fs_transfer_progress",
        "git_operation_progress",
        "thread_updated",
        "item_updated",
    ]
)
