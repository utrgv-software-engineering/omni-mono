export type HttpsOmniagentsDevProtocolGuiV1SchemaJson =
  | Initialize
  | StartRun
  | StopRun
  | SendUserMessage
  | GetSessionHistory
  | ListSessions
  | ArchiveSession
  | DeleteSession
  | GetUserHistory
  | GetAgentInfo
  | ToolApprovalResponse
  | McpApprovalResponse
  | ClientFunctions
  | ClientResponse
  | ListServerFunctions
  | ServerCall
  | EnqueueMessage
  | ListQueue
  | CancelQueuedMessage
  | ResumeSession
  | AckEvents
  | ListModels
  | GetModel
  | ListProviders
  | SetSessionModel
  | SetSessionReasoning
  | SetVoiceModel
  | AccountStatus
  | AccountLoginStart
  | AccountLoginComplete
  | AccountLoginCancel
  | AccountLogout
  | AccountRefresh
  | AccountUsage
  | AccountSelect
  | ElicitationResponse
  | McpListServers
  | McpGetServer
  | McpCreateServer
  | McpUpdateServer
  | McpDeleteServer
  | McpReloadServer
  | McpAuthStart
  | McpAuthComplete
  | McpAuthCancel
  | GetThread
  | ListTurns
  | ListItems
  | GetItem
  | AgentHostRegisterWorkspace
  | AgentHostRegisterProfile
  | AgentHostBindThread
  | AgentHostListResources
  | AgentHostMaterializeEnvironment
  | AgentHostStopEnvironment
  | ForkSession
  | SetSessionHold
  | QueueStatus
  | EnqueueNotification
  | UpdateSessionVariables
  | SetClientStatus
  | DisableTool
  | DisableMcpServer
  | ReportIncident
  | ExportSession
  | GetConfig
  | ValidateConfig
  | WriteConfig
  | McpReadResource
  | McpCallTool
  | McpGetPrompt
  | FsWatch
  | FsUnwatch
  | FsList
  | FsStat
  | FsDownloadOpen
  | FsDownloadRead
  | FsDownloadClose
  | FsUploadOpen
  | FsUploadChunk
  | FsUploadCommit
  | FsUploadAbort
  | GitListRepositories
  | GitStatus
  | GitDiff
  | GitLog
  | GitListBranches
  | GitListWorktrees
  | GitConflicts
  | GitStage
  | GitUnstage
  | GitDiscard
  | GitCommit
  | GitCheckout
  | GitReset
  | GitFetch
  | GitPull
  | GitPush
  | ListThreads
  | SearchThreads
  | UpdateThread
  | ListThreadDescendants
  | ExportThread
  | PurgeThreads
  | GetPlan
  | GetRunDiff
  | Initialized
  | RunStarted
  | RunStatus
  | ToolCalled
  | ToolResult
  | MessageOutput
  | Token
  | RunEnd
  | ToolApprovalRequested
  | ToolApprovalResolved
  | McpApprovalRequested
  | McpApprovalResolved
  | ClientRequest
  | ClientRequestResolved
  | QueueChanged
  | AccountChanged
  | ElicitationRequested
  | ElicitationResolved
  | McpServerStatusChanged
  | SessionForked
  | SessionVariablesChanged
  | FsEvents
  | FsRescanRequired
  | FsTransferProgress
  | GitOperationProgress
  | ThreadUpdated
  | ItemUpdated;

export interface Initialize {
  capabilities: Capabilities;
  identity: Identity;
  platform: Platform;
  protocol_version: string;
}
export interface Capabilities {
  approvals: boolean;
  artifacts: boolean;
  client_functions: boolean;
  disabled_notifications: string[];
  experimental_operations: string[];
  mcp_apps: boolean;
  realtime: boolean;
  replay: boolean;
  terminal: boolean;
}
export interface Identity {
  name: string;
  version: string;
}
export interface Platform {
  arch: string;
  os: string;
}
export interface StartRun {
  content?: string;
  context?: {
    [k: string]: unknown;
  };
  environment_selection:
    | {
        mode: "inherit";
      }
    | {
        mode: "none";
      }
    | {
        environment_generation?: number;
        environment_id: string;
        mode: "explicit";
      };
  group_id?: string;
  prompt: string;
  prompt_role?: string;
  safe_tool_overrides?: {
    [k: string]: unknown;
  };
  session_id?: string;
  variables?: {
    [k: string]: unknown;
  };
  workflow_name?: string;
}
export interface StopRun {
  run_id: string;
}
export interface SendUserMessage {
  content: string;
  run_id: string;
}
export interface GetSessionHistory {
  session_id: string;
}
export interface ListSessions {
  limit?: number;
  offset?: number;
}
export interface ArchiveSession {
  session_id: string;
}
export interface DeleteSession {
  cascade?: boolean;
  session_id: string;
}
export interface GetUserHistory {
  include_archived?: boolean;
  limit?: number;
}
export interface GetAgentInfo {}
export interface ToolApprovalResponse {
  always_approve?: boolean;
  call_id: string;
  decision: string;
  rejection_message?: string;
}
export interface McpApprovalResponse {
  decision: string;
  rejection_message?: string;
  request_id: string;
}
export interface ClientFunctions {
  functions: unknown[];
  version?: number;
}
export interface ClientResponse {
  error?: {
    [k: string]: unknown;
  };
  ok: boolean;
  request_id: string;
  result?: {
    [k: string]: unknown;
  };
}
export interface ListServerFunctions {}
export interface ServerCall {
  args?: {
    [k: string]: unknown;
  };
  environment_generation?: number;
  environment_id?: string;
  function: string;
  session_id?: string;
  workspace_id?: string;
}
export interface EnqueueMessage {
  content: string;
  role?: string;
  safe_tool_overrides?: {
    [k: string]: unknown;
  };
  session_id: string;
  source?: string;
  trigger_run?: boolean;
  variables?: {
    [k: string]: unknown;
  };
}
export interface ListQueue {
  session_id: string;
}
export interface CancelQueuedMessage {
  item_id: string;
  session_id: string;
}
export interface ResumeSession {
  after_seq?: number;
  session_id: string;
  stream_id?: string;
}
export interface AckEvents {
  seq: number;
  session_id: string;
  stream_id: string;
}
export interface ListModels {
  include_hidden?: boolean;
  modality?: string;
  session_id?: string;
}
export interface GetModel {
  model: string;
}
export interface ListProviders {}
export interface SetSessionModel {
  model: string;
  session_id: string;
}
export interface SetSessionReasoning {
  effort: string;
  session_id: string;
}
export interface SetVoiceModel {
  model: string;
}
export interface AccountStatus {}
export interface AccountLoginStart {
  api_key?: string;
  mode: string;
  provider: string;
  redirect_uri?: string;
}
export interface AccountLoginComplete {
  code?: string;
  login_id: string;
}
export interface AccountLoginCancel {
  login_id: string;
}
export interface AccountLogout {
  provider: string;
}
export interface AccountRefresh {
  provider: string;
}
export interface AccountUsage {
  provider?: string;
}
export interface AccountSelect {
  provider: string;
}
export interface ElicitationResponse {
  action: string;
  elicitation_id: string;
  reason?: string;
  value?: {
    [k: string]: unknown;
  };
}
export interface McpListServers {
  session_id?: string;
}
export interface McpGetServer {
  refresh?: boolean;
  server_name: string;
}
export interface McpCreateServer {
  params: {
    [k: string]: unknown;
  };
  server_name: string;
  server_options?: {
    [k: string]: unknown;
  };
  type: string;
}
export interface McpUpdateServer {
  params?: {
    [k: string]: unknown;
  };
  server_name: string;
  server_options?: {
    [k: string]: unknown;
  };
  type?: string;
}
export interface McpDeleteServer {
  server_name: string;
}
export interface McpReloadServer {
  server_name?: string;
}
export interface McpAuthStart {
  redirect_uri?: string;
  server_name: string;
  session_id?: string;
}
export interface McpAuthComplete {
  auth_id: string;
  code: string;
}
export interface McpAuthCancel {
  auth_id: string;
}
export interface GetThread {
  thread_id: string;
}
export interface ListTurns {
  cursor?: string;
  limit?: number;
  order?: string;
  thread_id: string;
}
export interface ListItems {
  cursor?: string;
  kinds?: unknown[];
  limit?: number;
  order?: string;
  thread_id: string;
  turn_id?: string;
}
export interface GetItem {
  item_id: string;
  thread_id: string;
}
export interface AgentHostRegisterWorkspace {
  materialization_path: string;
  owner_user_id?: string;
  snapshot_ref?: string;
  sources?: {
    [k: string]: unknown;
  }[];
  workspace_id: string;
}
export interface AgentHostRegisterProfile {
  definition:
    | {
        kind: "default";
        project_id?: string;
      }
    | {
        kind: "path";
        path: string;
      };
  owner_user_id?: string;
  profile_id: string;
}
export interface AgentHostBindThread {
  binding: {
    environment_selection:
      | {
          mode: "none";
        }
      | {
          mode: "automatic";
          profile_id: string;
        }
      | {
          environment_id: string;
          mode: "existing";
        };
    workspace_id: string | null;
  };
  thread_id: string;
}
export interface AgentHostListResources {}
export interface AgentHostMaterializeEnvironment {
  profile_id: string;
  workspace_id: string;
}
export interface AgentHostStopEnvironment {
  environment_id: string;
}
export interface ForkSession {
  from_item_id?: string;
  from_turn_id?: string;
  new_session_id?: string;
  session_id: string;
}
export interface SetSessionHold {
  hold: boolean;
  session_id: string;
}
export interface QueueStatus {
  session_id: string;
}
export interface EnqueueNotification {
  content: string;
  safe_tool_overrides?: {
    [k: string]: unknown;
  };
  session_id: string;
  source?: string;
}
export interface UpdateSessionVariables {
  session_id: string;
  updates: {
    [k: string]: unknown;
  };
}
export interface SetClientStatus {
  text: string;
}
export interface DisableTool {
  tool_name: string;
}
export interface DisableMcpServer {
  server_name: string;
}
export interface ReportIncident {
  description: string;
  disable_mcp_servers?: unknown[];
  disable_tools?: unknown[];
  session_id: string;
}
export interface ExportSession {
  redact?: boolean;
  session_id: string;
}
export interface GetConfig {}
export interface ValidateConfig {
  updates: {
    [k: string]: unknown;
  };
}
export interface WriteConfig {
  updates: {
    [k: string]: unknown;
  };
}
export interface McpReadResource {
  server_name: string;
  session_id?: string;
  uri: string;
}
export interface McpCallTool {
  args?: {
    [k: string]: unknown;
  };
  server_name: string;
  session_id: string;
  tool_name: string;
}
export interface McpGetPrompt {
  args?: {
    [k: string]: unknown;
  };
  prompt_name: string;
  server_name: string;
  session_id?: string;
}
export interface FsWatch {
  environment_generation?: number;
  environment_id: string;
  path: string;
  poll_interval_ms?: number;
  recursive?: boolean;
  workspace_id?: string;
}
export interface FsUnwatch {
  environment_generation?: number;
  environment_id: string;
  watch_id: string;
  workspace_id?: string;
}
export interface FsList {
  environment_generation?: number;
  environment_id: string;
  path: string;
  recursive?: boolean;
  workspace_id?: string;
}
export interface FsStat {
  environment_generation?: number;
  environment_id: string;
  path: string;
  workspace_id?: string;
}
export interface FsDownloadOpen {
  environment_generation?: number;
  environment_id: string;
  path: string;
  workspace_id?: string;
}
export interface FsDownloadRead {
  environment_generation?: number;
  environment_id: string;
  length?: number;
  offset?: number;
  transfer_id: string;
  workspace_id?: string;
}
export interface FsDownloadClose {
  environment_generation?: number;
  environment_id: string;
  transfer_id: string;
  workspace_id?: string;
}
export interface FsUploadOpen {
  environment_generation?: number;
  environment_id: string;
  expected_sha256?: string;
  overwrite?: boolean;
  path: string;
  sha256?: string;
  size: number;
  workspace_id?: string;
}
export interface FsUploadChunk {
  data: string;
  environment_generation?: number;
  environment_id: string;
  offset: number;
  transfer_id: string;
  workspace_id?: string;
}
export interface FsUploadCommit {
  environment_generation?: number;
  environment_id: string;
  transfer_id: string;
  workspace_id?: string;
}
export interface FsUploadAbort {
  environment_generation?: number;
  environment_id: string;
  transfer_id: string;
  workspace_id?: string;
}
export interface GitListRepositories {
  environment_generation?: number;
  environment_id: string;
  max_depth?: number;
  path?: string;
  workspace_id?: string;
}
export interface GitStatus {
  environment_generation?: number;
  environment_id: string;
  include_ignored?: boolean;
  include_untracked?: boolean;
  paths?: unknown[];
  repo: string;
  workspace_id?: string;
}
export interface GitDiff {
  context_lines?: number;
  environment_generation?: number;
  environment_id: string;
  from_rev?: string;
  include_untracked?: boolean;
  mode?: string;
  paths?: unknown[];
  repo: string;
  to_rev?: string;
  workspace_id?: string;
}
export interface GitLog {
  environment_generation?: number;
  environment_id: string;
  max_count?: number;
  paths?: unknown[];
  repo: string;
  rev?: string;
  skip?: number;
  workspace_id?: string;
}
export interface GitListBranches {
  environment_generation?: number;
  environment_id: string;
  include_remote?: boolean;
  repo: string;
  workspace_id?: string;
}
export interface GitListWorktrees {
  environment_generation?: number;
  environment_id: string;
  repo: string;
  workspace_id?: string;
}
export interface GitConflicts {
  environment_generation?: number;
  environment_id: string;
  paths?: unknown[];
  repo: string;
  workspace_id?: string;
}
export interface GitStage {
  context_lines?: number;
  environment_generation?: number;
  environment_id: string;
  hunks?: unknown[];
  mode?: string;
  paths?: unknown[];
  repo: string;
  workspace_id?: string;
}
export interface GitUnstage {
  context_lines?: number;
  environment_generation?: number;
  environment_id: string;
  hunks?: unknown[];
  paths?: unknown[];
  repo: string;
  workspace_id?: string;
}
export interface GitDiscard {
  confirmation_token?: string;
  context_lines?: number;
  environment_generation?: number;
  environment_id: string;
  hunks?: unknown[];
  paths?: unknown[];
  repo: string;
  workspace_id?: string;
}
export interface GitCommit {
  allow_empty?: boolean;
  amend?: boolean;
  author?: string;
  confirmation_token?: string;
  environment_generation?: number;
  environment_id: string;
  message: string;
  repo: string;
  workspace_id?: string;
}
export interface GitCheckout {
  branch: string;
  confirmation_token?: string;
  create?: boolean;
  detach?: boolean;
  discard_changes?: boolean;
  environment_generation?: number;
  environment_id: string;
  repo: string;
  start_point?: string;
  workspace_id?: string;
}
export interface GitReset {
  confirmation_token?: string;
  environment_generation?: number;
  environment_id: string;
  mode?: string;
  paths?: unknown[];
  repo: string;
  rev?: string;
  workspace_id?: string;
}
export interface GitFetch {
  environment_generation?: number;
  environment_id: string;
  prune?: boolean;
  refspec?: string;
  remote?: string;
  repo: string;
  workspace_id?: string;
}
export interface GitPull {
  environment_generation?: number;
  environment_id: string;
  rebase?: boolean;
  refspec?: string;
  remote?: string;
  repo: string;
  workspace_id?: string;
}
export interface GitPush {
  confirmation_token?: string;
  environment_generation?: number;
  environment_id: string;
  force?: boolean;
  force_with_lease?: boolean;
  refspec?: string;
  remote?: string;
  repo: string;
  set_upstream?: boolean;
  workspace_id?: string;
}
export interface ListThreads {
  created_after?: number;
  created_before?: number;
  cursor?: string;
  limit?: number;
  model?: string;
  order?: string;
  parent_thread_id?: string;
  pinned?: boolean;
  source?: string;
  status?: string;
  updated_after?: number;
  updated_before?: number;
}
export interface SearchThreads {
  created_after?: number;
  created_before?: number;
  cursor?: string;
  limit?: number;
  model?: string;
  parent_thread_id?: string;
  pinned?: boolean;
  query: string;
  source?: string;
  status?: string;
  updated_after?: number;
  updated_before?: number;
}
export interface UpdateThread {
  metadata?: {
    [k: string]: unknown;
  };
  pinned?: boolean;
  status?: string;
  thread_id: string;
  title?: string | null;
}
export interface ListThreadDescendants {
  limit?: number;
  max_depth?: number;
  thread_id: string;
}
export interface ExportThread {
  cursor?: string;
  include_descendants?: boolean;
  limit?: number;
  thread_id: string;
}
export interface PurgeThreads {
  dry_run?: boolean;
  retention_days: number;
}
export interface GetPlan {
  scope?: string;
  thread_id: string;
}
export interface GetRunDiff {
  thread_id: string;
  turn_id?: string;
}
export interface Initialized {}
export interface RunStarted {
  prompt?: string;
  prompt_role?: string;
  run_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
}
export interface RunStatus {
  attempt?: number;
  message: string;
  next_delay?: number;
  run_id: string;
  seq?: number;
  session_id: string;
  status: string;
  stream_id?: string;
  total?: number;
}
export interface ToolCalled {
  call_id: string;
  input: string;
  run_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
  tool: string;
}
export interface ToolResult {
  call_id: string;
  metadata?: {
    [k: string]: unknown;
  };
  output: string;
  run_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
  tool: string;
}
export interface MessageOutput {
  content: string;
  is_final?: boolean;
  message_id?: string;
  run_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
}
export interface Token {
  delta: {
    [k: string]: unknown;
  };
  max_input_tokens?: number;
  max_output_tokens?: number;
  model?: string;
  model_ref?: string;
  response_id?: string;
  run_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
  totals: {
    [k: string]: unknown;
  };
  truncation?: string;
  turn?: number;
}
export interface RunEnd {
  end_reason: string;
  error?: {
    [k: string]: unknown;
  };
  max_input_tokens?: number;
  max_output_tokens?: number;
  model?: string;
  model_ref?: string;
  run_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
  truncation?: string;
  usage?: {
    [k: string]: unknown;
  };
}
export interface ToolApprovalRequested {
  arguments: string;
  call_id: string;
  metadata?: {
    [k: string]: unknown;
  };
  run_id?: string;
  seq?: number;
  session_id?: string;
  stream_id?: string;
  tool_name: string;
}
export interface ToolApprovalResolved {
  call_id: string;
  reason?: string;
  seq?: number;
  session_id?: string;
  stream_id?: string;
}
export interface McpApprovalRequested {
  arguments: string;
  kind: string;
  request_id: string;
  run_id?: string;
  seq?: number;
  server_label: string;
  session_id?: string;
  stream_id?: string;
  tool_name: string;
}
export interface McpApprovalResolved {
  reason?: string;
  request_id: string;
  seq?: number;
  session_id?: string;
  stream_id?: string;
}
export interface ClientRequest {
  args: {
    [k: string]: unknown;
  };
  function: string;
  idempotency_key?: string;
  request_id: string;
  run_id?: string;
  seq?: number;
  session_id?: string;
  stream_id?: string;
}
export interface ClientRequestResolved {
  reason?: string;
  request_id: string;
  seq?: number;
  session_id?: string;
  stream_id?: string;
}
export interface QueueChanged {
  depth: number;
  items: unknown[];
  seq?: number;
  session_id: string;
  stream_id?: string;
}
export interface AccountChanged {
  account?: {
    [k: string]: unknown;
  };
  provider: string;
  reason: string;
  seq?: number;
  stream_id?: string;
}
export interface ElicitationRequested {
  elicitation_id: string;
  expires_at?: string;
  input_schema?: {
    [k: string]: unknown;
  };
  item_id?: string;
  kind: string;
  message: string;
  options?: unknown[];
  persist_response?: boolean;
  run_id?: string;
  seq?: number;
  session_id?: string;
  source?: string;
  stream_id?: string;
  timeout_ms?: number;
  title?: string | null;
  url?: string;
}
export interface ElicitationResolved {
  action?: string;
  elicitation_id: string;
  reason?: string;
  run_id?: string;
  seq?: number;
  session_id?: string;
  status: string;
  stream_id?: string;
  value?: {
    [k: string]: unknown;
  };
}
export interface McpServerStatusChanged {
  at?: string;
  auth_state?: string;
  previous_status?: string;
  reason?: string;
  reason_code?: string;
  seq?: number;
  server_name: string;
  status: string;
  stream_id?: string;
}
export interface SessionForked {
  from_item_id?: string;
  new_session_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
}
export interface SessionVariablesChanged {
  changed: unknown[];
  seq?: number;
  session_id: string;
  stream_id?: string;
  variables: {
    [k: string]: unknown;
  };
}
export interface FsEvents {
  environment_generation?: number;
  environment_id: string;
  events: unknown[];
  seq?: number;
  stream_id?: string;
  watch_id: string;
  workspace_id?: string;
}
export interface FsRescanRequired {
  environment_generation?: number;
  environment_id: string;
  reason: string;
  seq?: number;
  stream_id?: string;
  watch_id: string;
  workspace_id?: string;
}
export interface FsTransferProgress {
  direction: string;
  environment_generation?: number;
  environment_id: string;
  seq?: number;
  stream_id?: string;
  total: number;
  transfer_id: string;
  transferred: number;
  workspace_id?: string;
}
export interface GitOperationProgress {
  detail?: {
    [k: string]: unknown;
  };
  environment_generation?: number;
  environment_id: string;
  operation: string;
  operation_id: string;
  phase: string;
  repo: string;
  seq?: number;
  stream_id?: string;
  workspace_id?: string;
}
export interface ThreadUpdated {
  cascaded_thread_ids?: unknown[];
  changed: unknown[];
  seq?: number;
  stream_id?: string;
  thread: {
    [k: string]: unknown;
  };
  thread_id: string;
}
export interface ItemUpdated {
  content: {
    [k: string]: unknown;
  };
  item_id: string;
  item_seq: number;
  kind: string;
  revision: number;
  seq?: number;
  session_id: string;
  status: string;
  stream_id?: string;
  thread_id: string;
  turn_id?: string;
  updated_at?: number;
}
