/**
 * TypeScript types for OmniAgents JSON-RPC 2.0 protocol
 *
 * Based on omniagents/rpc/protocol.md
 */

// ============================================================================
// JSON-RPC 2.0 Base Types
// ============================================================================

export interface JSONRPCRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: string;
  params?: unknown;
}

export interface JSONRPCResponse {
  jsonrpc: "2.0";
  id: string | number;
  result?: unknown;
  error?: JSONRPCErrorObject;
}

export interface JSONRPCErrorObject {
  code: number;
  message: string;
  data?: unknown;
}

export interface JSONRPCNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

// ============================================================================
// Server Events (sent from server to client as notifications)
// ============================================================================

export type ServerEvent =
  | ToolCalledEvent
  | ToolResultEvent
  | MessageOutputEvent
  | RunStartedEvent
  | RunEndEvent
  | TokenEvent
  | RunStatusEvent
  | ToolApprovalRequestedEvent
  | ToolApprovalResolvedEvent
  | McpApprovalRequestedEvent
  | McpApprovalResolvedEvent
  | QueueChangedEvent;

export interface ToolCalledEvent {
  jsonrpc: "2.0";
  method: "tool_called";
  params: {
    run_id: string;
    session_id: string;
    tool: string;
    input: string; // JSON string of arguments
    call_id: string;
  };
}

export interface ToolResultEvent {
  jsonrpc: "2.0";
  method: "tool_result";
  params: {
    run_id: string;
    session_id: string;
    tool: string;
    output: string;
    call_id: string;
    metadata?: Record<string, unknown>;
  };
}

export interface MessageOutputEvent {
  jsonrpc: "2.0";
  method: "message_output";
  params: {
    run_id: string;
    session_id: string;
    content: string; // Note: No role field in actual protocol!
  };
}

export interface RunStartedEvent {
  jsonrpc: "2.0";
  method: "run_started";
  params: {
    run_id: string;
    session_id: string;
    // Present when the server initiated the run from a queued item or
    // background automation. Clients use this to render the user turn
    // in the transcript, since no local submit happened to push it.
    prompt?: string;
  };
}

export interface RunEndEvent {
  jsonrpc: "2.0";
  method: "run_end";
  params: {
    run_id: string;
    session_id: string;
    end_reason: string;
    usage?: {
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
    };
    model?: string;
    max_input_tokens?: number;
    max_output_tokens?: number;
    truncation?: string;
    error?: {
      message: string;
      code?: string;
      details?: unknown;
    };
  };
}

export interface TokenEvent {
  jsonrpc: "2.0";
  method: "token";
  params: {
    run_id: string;
    session_id: string;
    turn: number;
    response_id: string;
    model?: string;
    max_input_tokens?: number;
    max_output_tokens?: number;
    truncation?: string;
    delta: {
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
    };
    totals: {
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
    };
  };
}

export interface RunStatusEvent {
  jsonrpc: "2.0";
  method: "run_status";
  params: {
    run_id: string;
    session_id: string;
    status: string;
    message: string;
    attempt: number;
    total: number;
    next_delay?: number;
  };
}

// ============================================================================
// Client Functions (server can invoke these on the client)
// ============================================================================

export interface ClientRequest {
  jsonrpc: "2.0";
  method: "client_request";
  params: {
    request_id: string;
    function: string;
    args: Record<string, unknown>; // Note: 'args', not 'arguments'!
    session_id?: string;
  };
}

// SDK 0.16 tool-approval flow: dedicated event + RPC, not client_request.

export interface ToolApprovalRequestedEvent {
  jsonrpc: "2.0";
  method: "tool_approval_requested";
  params: {
    call_id: string;
    tool_name: string;
    arguments: string; // raw JSON-encoded argument string from the model
    metadata?: {
      display_type?: string;
      summary?: string;
      preview?: string;
      [key: string]: unknown;
    };
    session_id?: string;
    run_id?: string;
  };
}

export interface ToolApprovalResolvedEvent {
  jsonrpc: "2.0";
  method: "tool_approval_resolved";
  params: {
    call_id: string;
    session_id?: string;
  };
}

export interface ToolApprovalResponseRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "tool_approval_response";
  params: {
    call_id: string;
    decision: "approve" | "reject";
    always_approve?: boolean;
    rejection_message?: string;
  };
}

// Hosted-MCP approval flow (SDK 0.16 HostedMCPTool). Parallel to the
// function-tool approval flow above, but identified by request_id (from
// the model's McpApprovalRequest) and the MCP server_label. There is no
// ``always_approve`` flag on this path — the SDK keys approval by
// (server_label, tool_name) and we don't maintain a parallel always-allow
// set yet (see protocol.md §"MCP Approval Flow").

export interface McpApprovalRequestedEvent {
  jsonrpc: "2.0";
  method: "mcp_approval_requested";
  params: {
    kind: "mcp";
    request_id: string;
    server_label: string;
    tool_name: string;
    arguments: string; // raw JSON-encoded argument string from the model
    session_id?: string;
    run_id?: string;
  };
}

export interface McpApprovalResolvedEvent {
  jsonrpc: "2.0";
  method: "mcp_approval_resolved";
  params: {
    request_id: string;
    session_id?: string;
  };
}

// Snapshot broadcast after every enqueue / cancel / drainer pop. The
// server sends a full replace (not a delta) — the queue is bounded
// (default 100) so it's trivial to apply.
export interface QueueChangedEvent {
  jsonrpc: "2.0";
  method: "queue_changed";
  params: {
    session_id: string;
    depth: number;
    items: Array<{
      id: string;
      content: string;
      role: string;
      trigger_run: boolean;
      variables: Record<string, unknown> | null;
      safe_tool_overrides: Record<string, unknown> | null;
      source: string | null;
      enqueued_at: number;
    }>;
  };
}

export interface McpApprovalResponseRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "mcp_approval_response";
  params: {
    request_id: string;
    decision: "approve" | "reject";
    rejection_message?: string;
  };
}

export interface AddArtifactRequest {
  function: "ui.add_artifact";
  arguments: {
    type: string;
    title?: string;
    description?: string;
    content: string;
    language?: string;
    metadata?: Record<string, unknown>;
  };
}

export interface SetSessionStateRequest {
  function: "ui.set_session_state";
  arguments: {
    model?: string;
    label?: string;
    provider?: string;
    reasoning_effort?: string;
  };
}

// ============================================================================
// Server RPC Methods (client can call these)
// ============================================================================

export interface StartRunRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "start_run";
  params: {
    prompt: string;
    session_id?: string;
    variables?: Record<string, unknown>;
  };
}

export interface StartRunResponse {
  run_id: string;
  session_id: string;
}

export interface StopRunRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "stop_run";
  params: {
    run_id: string;
  };
}

export interface GetSessionHistoryRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "get_session_history";
  params: {
    session_id: string;
  };
}

export interface SessionMessage {
  role: string;
  content: unknown;
  timestamp: string;
}

export interface GetSessionHistoryResponse extends Array<SessionMessage> {}

export interface GetUserHistoryRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "get_user_history";
  params: {
    limit: number;
    include_archived: boolean;
  };
}

export interface UserHistoryItem {
  content: string;
  session_id?: string;
  timestamp?: string;
}

export interface GetUserHistoryResponse extends Array<UserHistoryItem> {}

export interface ListSessionsRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "list_sessions";
  params?: Record<string, never>;
}

export interface Session {
  id: string;
  created_at: string;
  archived: boolean;
  message_count: number;
  first_message?: SessionMessage;
  last_message?: SessionMessage;
}

export interface ListSessionsResponse extends Array<Session> {}

export interface ArchiveSessionRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "archive_session";
  params: {
    session_id: string;
  };
}

export interface ClientFunctionsRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "client_functions";
  params: {
    version: number;
    functions: Array<{
      name: string;
      description?: string;
    }>;
  };
}

export interface ClientResponseRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "client_response";
  params: {
    request_id: string;
    ok: boolean;
    result?: Record<string, unknown>;
    error?: Record<string, unknown>;
  };
}

export interface ListServerFunctionsRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "list_server_functions";
  params?: Record<string, never>;
}

export interface ServerFunctionMeta {
  name: string;
  description?: string;
  params_schema?: Record<string, unknown>;
  result_schema?: Record<string, unknown>;
}

export interface ListServerFunctionsResponse
  extends Array<ServerFunctionMeta> {}

export interface ServerCallRequest {
  jsonrpc: "2.0";
  id: string | number;
  method: "server_call";
  params: {
    function: string;
    args?: Record<string, unknown>;
    session_id?: string;
  };
}

export interface ServerCallResponse extends Record<string, unknown> {}

// ============================================================================
// WebSocket Message Types
// ============================================================================

export type WebSocketMessage =
  | JSONRPCRequest
  | JSONRPCResponse
  | JSONRPCNotification
  | ServerEvent
  | ClientRequest;
