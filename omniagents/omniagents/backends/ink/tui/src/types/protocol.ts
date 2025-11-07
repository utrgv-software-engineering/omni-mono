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
  error?: JSONRPCError;
}

export interface JSONRPCError {
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
  | RunStatusEvent;

export interface ToolCalledEvent {
  jsonrpc: "2.0";
  method: "tool_called";
  params: {
    tool: string;
    input: string; // JSON string of arguments
    call_id: string;
  };
}

export interface ToolResultEvent {
  jsonrpc: "2.0";
  method: "tool_result";
  params: {
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
    content: string; // Note: No role field in actual protocol!
  };
}

export interface RunStartedEvent {
  jsonrpc: "2.0";
  method: "run_started";
  params: {
    run_id: string;
    session_id: string;
  };
}

export interface RunEndEvent {
  jsonrpc: "2.0";
  method: "run_end";
  params: {
    run_id: string;
    end_reason: string;
    usage?: {
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
    };
    model?: string;
    context_window?: number;
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
    turn: number;
    response_id: string;
    model?: string;
    context_window?: number;
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
  id: string | number;
  method: "client_request";
  params: {
    request_id: string;
    function: string;
    args: Record<string, unknown>; // Note: 'args', not 'arguments'!
    session_id?: string;
  };
}

export interface ToolApprovalRequest {
  function: "ui.request_tool_approval";
  arguments: {
    tool_name: string;
    tool_arguments: Record<string, unknown>;
    metadata?: {
      title?: string;
      description?: string;
      fields?: Array<{
        name: string;
        value: string;
        label?: string;
        type?: string;
      }>;
      warning?: string;
    };
  };
}

export interface ToolApprovalResponse {
  approved: boolean;
  reason?: string;
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
