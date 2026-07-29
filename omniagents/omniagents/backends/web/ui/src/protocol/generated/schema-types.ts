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
  | SessionForked
  | SessionVariablesChanged;

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
  function: string;
  session_id?: string;
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
export interface ForkSession {
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
export interface SessionForked {
  new_session_id: string;
  seq?: number;
  session_id: string;
  stream_id?: string;
}
export interface SessionVariablesChanged {
  changed: {
    [k: string]: unknown;
  };
  seq?: number;
  session_id: string;
  stream_id?: string;
  variables: {
    [k: string]: unknown;
  };
}
