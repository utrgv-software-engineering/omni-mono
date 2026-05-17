/**
 * JSON-RPC 2.0 WebSocket client for OmniAgents
 */

import WebSocket from "ws";
import { EventEmitter } from "events";
import type {
  JSONRPCRequest,
  JSONRPCResponse,
  JSONRPCNotification,
  ServerEvent,
  ClientRequest,
  WebSocketMessage,
  SendMessageResponse,
  GetHistoryResponse,
  ListSessionsResponse,
  GetAgentInfoResponse,
} from "../types/protocol.js";

export interface RPCClientOptions {
  url: string;
  token?: string;
  debug?: boolean;
}

export type ClientFunction = (
  args: Record<string, unknown>,
) => Promise<unknown>;

/**
 * RPC Client that handles WebSocket communication with the OmniAgents server
 */
export class RPCClient extends EventEmitter {
  private ws: WebSocket | null = null;
  private url: string;
  private token?: string;
  private debug: boolean;
  private requestId = 0;
  private pendingRequests = new Map<
    string | number,
    {
      resolve: (value: unknown) => void;
      reject: (error: Error) => void;
    }
  >();
  private registeredClientFunctions = new Map<string, ClientFunction>();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private rpcReady = false;

  constructor(options: RPCClientOptions) {
    super();
    this.url = options.url;
    this.token = options.token;
    this.debug = options.debug || false;
  }

  /**
   * Register a client function that the server can invoke
   */
  registerClientFunction(name: string, handler: ClientFunction): void {
    this.registeredClientFunctions.set(name, handler);
  }

  /**
   * Unregister a client function
   */
  unregisterClientFunction(name: string): void {
    this.registeredClientFunctions.delete(name);
  }

  /**
   * Connect to the WebSocket server
   */
  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        const wsUrl = this.token
          ? `${this.url}?token=${encodeURIComponent(this.token)}`
          : this.url;

        this.ws = new WebSocket(wsUrl);

        this.ws.on("open", () => {
          this.log("Connected to server");
          this.reconnectAttempts = 0;
          this.emit("connected");

          // Wait a brief moment to ensure server-side RPC handlers are ready
          // This matches the bubbletea backend's approach which delays before
          // signaling RPC readiness to avoid race conditions
          setTimeout(() => {
            this.rpcReady = true;
            this.emit("rpc_ready");
            this.log("RPC ready");
          }, 50);

          resolve();
        });

        this.ws.on("message", (data: WebSocket.Data) => {
          try {
            const message = JSON.parse(data.toString()) as WebSocketMessage;
            this.handleMessage(message);
          } catch (error) {
            this.log("Failed to parse message:", error);
          }
        });

        this.ws.on("close", () => {
          this.log("Disconnected from server");
          this.rpcReady = false;
          this.emit("disconnected");
          this.attemptReconnect();
        });

        this.ws.on("error", (error) => {
          this.log("WebSocket error:", error);
          this.emit("error", error);
          reject(error);
        });
      } catch (error) {
        reject(error);
      }
    });
  }

  /**
   * Disconnect from the server
   */
  disconnect(): void {
    if (this.ws) {
      this.rpcReady = false;
      this.ws.close();
      this.ws = null;
    }
  }

  /**
   * Start a new agent run
   */
  async startRun(
    prompt: string,
    sessionId?: string,
    variables?: Record<string, unknown>,
  ): Promise<{ run_id: string; session_id: string }> {
    const params: Record<string, unknown> = { prompt };
    if (sessionId) {
      params.session_id = sessionId;
    }
    if (variables) {
      params.variables = variables;
    }
    return this.call<{ run_id: string; session_id: string }>(
      "start_run",
      params,
    );
  }

  /**
   * Stop an active run
   */
  async stopRun(runId: string): Promise<void> {
    await this.call<void>("stop_run", { run_id: runId });
  }

  /**
   * Get conversation history for a session
   */
  async getSessionHistory(
    sessionId: string,
  ): Promise<Array<{ role: string; content: unknown; timestamp: string }>> {
    return this.call<
      Array<{ role: string; content: unknown; timestamp: string }>
    >("get_session_history", { session_id: sessionId });
  }

  /**
   * Get user history across all sessions
   */
  async getUserHistory(
    limit: number = 200,
    includeArchived: boolean = true,
  ): Promise<
    Array<{ content: string; session_id?: string; timestamp?: string }>
  > {
    return this.call<
      Array<{ content: string; session_id?: string; timestamp?: string }>
    >("get_user_history", {
      limit,
      include_archived: includeArchived,
    });
  }

  /**
   * List all sessions
   */
  async listSessions(): Promise<
    Array<{
      id: string;
      created_at: string;
      archived: boolean;
      message_count: number;
      first_message?: unknown;
      last_message?: unknown;
    }>
  > {
    return this.call<
      Array<{
        id: string;
        created_at: string;
        archived: boolean;
        message_count: number;
        first_message?: unknown;
        last_message?: unknown;
      }>
    >("list_sessions", {});
  }

  /**
   * Archive a session
   */
  async archiveSession(sessionId: string): Promise<void> {
    await this.call<void>("archive_session", { session_id: sessionId });
  }

  /**
   * Advertise client capabilities (optional)
   */
  async clientFunctions(
    version: number,
    functions: Array<{ name: string; description?: string }>,
  ): Promise<void> {
    await this.call<void>("client_functions", { version, functions });
  }

  /**
   * Send response to a client_request from the server
   */
  async clientResponse(
    requestId: string,
    ok: boolean,
    result?: Record<string, unknown>,
    error?: Record<string, unknown>,
  ): Promise<void> {
    const params: Record<string, unknown> = { request_id: requestId, ok };
    if (result) {
      params.result = result;
    }
    if (error) {
      params.error = error;
    }
    await this.call<void>("client_response", params);
  }

  /**
   * Respond to a tool_approval_requested event (SDK 0.16 interruption flow).
   */
  async toolApprovalResponse(
    callId: string,
    decision: "approve" | "reject",
    alwaysApprove: boolean = false,
    rejectionMessage?: string,
  ): Promise<boolean> {
    const params: Record<string, unknown> = {
      call_id: callId,
      decision,
    };
    if (alwaysApprove) {
      params.always_approve = true;
    }
    if (rejectionMessage) {
      params.rejection_message = rejectionMessage;
    }
    return this.call<boolean>("tool_approval_response", params);
  }

  /**
   * Respond to an mcp_approval_requested event (SDK 0.16 hosted-MCP
   * interruption flow). The wire format is similar to
   * ``tool_approval_response`` but the identifier is ``request_id`` (from
   * the model's ``McpApprovalRequest``) and there is intentionally no
   * ``always_approve`` flag — see protocol.md §"MCP Approval Flow".
   */
  async mcpApprovalResponse(
    requestId: string,
    decision: "approve" | "reject",
    rejectionMessage?: string,
  ): Promise<boolean> {
    const params: Record<string, unknown> = {
      request_id: requestId,
      decision,
    };
    if (rejectionMessage) {
      params.rejection_message = rejectionMessage;
    }
    return this.call<boolean>("mcp_approval_response", params);
  }

  /**
   * List available server functions
   */
  async listServerFunctions(): Promise<
    Array<{
      name: string;
      description?: string;
      params_schema?: Record<string, unknown>;
      result_schema?: Record<string, unknown>;
    }>
  > {
    return this.call<
      Array<{
        name: string;
        description?: string;
        params_schema?: Record<string, unknown>;
        result_schema?: Record<string, unknown>;
      }>
    >("list_server_functions", {});
  }

  /**
   * Call a server function
   */
  async serverCall(
    func: string,
    args?: Record<string, unknown>,
    sessionId?: string,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { function: func };
    if (args) {
      params.args = args;
    }
    if (sessionId) {
      params.session_id = sessionId;
    }
    return this.call<Record<string, unknown>>("server_call", params);
  }

  // ---------------------------------------------------------------------
  // Queue — background-injected messages serialized against current_task.
  // See omniagents/core/agents/service.py::enqueue_message and friends.
  // ---------------------------------------------------------------------

  async enqueueMessage(
    sessionId: string,
    content: string,
    opts?: {
      role?: string;
      triggerRun?: boolean;
      variables?: Record<string, unknown>;
      safeToolOverrides?: Record<string, unknown>;
      source?: string;
    },
  ): Promise<{
    ok: boolean;
    id?: string;
    depth?: number;
    reason?: string;
    session_id?: string;
    enqueued_at?: number;
  }> {
    const params: Record<string, unknown> = { session_id: sessionId, content };
    if (opts?.role) params.role = opts.role;
    if (opts?.triggerRun) params.trigger_run = true;
    if (opts?.variables) params.variables = opts.variables;
    if (opts?.safeToolOverrides) params.safe_tool_overrides = opts.safeToolOverrides;
    if (opts?.source) params.source = opts.source;
    return this.call("enqueue_message", params);
  }

  async listQueue(
    sessionId: string,
  ): Promise<{
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
  }> {
    return this.call("list_queue", { session_id: sessionId });
  }

  async cancelQueuedMessage(
    sessionId: string,
    itemId: string,
  ): Promise<{ ok: boolean; id?: string; depth?: number; reason?: string }> {
    return this.call("cancel_queued_message", {
      session_id: sessionId,
      item_id: itemId,
    });
  }

  /**
   * Make a generic RPC call
   */
  private async call<T>(method: string, params: unknown): Promise<T> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket not connected");
    }

    const id = ++this.requestId;
    const request: JSONRPCRequest = {
      jsonrpc: "2.0",
      id,
      method,
      params,
    };

    return new Promise((resolve, reject) => {
      this.pendingRequests.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
      });

      this.ws!.send(JSON.stringify(request), (error) => {
        if (error) {
          this.pendingRequests.delete(id);
          reject(error);
        }
      });
    }) as Promise<T>;
  }

  /**
   * Handle incoming WebSocket messages
   */
  private handleMessage(message: WebSocketMessage): void {
    // Handle JSON-RPC responses
    if ("result" in message || "error" in message) {
      this.handleResponse(message as JSONRPCResponse);
      return;
    }

    // Handle server events (notifications)
    if ("method" in message && !("id" in message)) {
      // Check if it's a client_request notification (special case)
      if (message.method === "client_request") {
        this.handleClientRequest(message as JSONRPCNotification);
        return;
      }
      this.handleNotification(message as JSONRPCNotification);
      return;
    }

    this.log("Unknown message type:", message);
  }

  /**
   * Handle JSON-RPC responses
   */
  private handleResponse(response: JSONRPCResponse): void {
    const pending = this.pendingRequests.get(response.id);
    if (!pending) {
      this.log("Received response for unknown request:", response.id);
      return;
    }

    this.pendingRequests.delete(response.id);

    if (response.error) {
      pending.reject(new Error(response.error.message));
    } else {
      pending.resolve(response.result);
    }
  }

  /**
   * Handle server notifications (events)
   */
  private handleNotification(notification: JSONRPCNotification): void {
    const event = notification as ServerEvent;

    switch (event.method) {
      case "tool_called":
        this.emit("tool_called", event.params);
        break;
      case "tool_result":
        this.emit("tool_result", event.params);
        break;
      case "message_output":
        this.emit("message_output", event.params);
        break;
      case "run_started":
        this.emit("run_started", event.params);
        break;
      case "run_end":
        this.emit("run_end", event.params);
        break;
      case "token":
        this.emit("token", event.params);
        break;
      case "run_status":
        this.emit("run_status", event.params);
        break;
      case "tool_approval_requested":
        this.emit("tool_approval_requested", event.params);
        break;
      case "tool_approval_resolved":
        this.emit("tool_approval_resolved", event.params);
        break;
      case "mcp_approval_requested":
        this.emit("mcp_approval_requested", event.params);
        break;
      case "mcp_approval_resolved":
        this.emit("mcp_approval_resolved", event.params);
        break;
      case "queue_changed":
        this.emit("queue_changed", event.params);
        break;
      default:
        this.log("Unknown notification method:", event.method);
    }
  }

  /**
   * Handle client requests (server invoking client functions via notification)
   * Note: client_request is sent as a NOTIFICATION, not a request!
   * The client must call client_response RPC method to send the result back.
   */
  private async handleClientRequest(
    notification: JSONRPCNotification,
  ): Promise<void> {
    const params = notification.params as {
      request_id: string;
      function: string;
      args: Record<string, unknown>;
      session_id?: string;
      run_id?: string;
    };

    const { request_id, function: funcName, args } = params;

    const handler = this.registeredClientFunctions.get(funcName);
    if (!handler) {
      this.log(`Client function not found: ${funcName}`);
      // Send error response via client_response RPC method
      await this.clientResponse(request_id, false, undefined, {
        code: -32601,
        message: `Function not found: ${funcName}`,
      });
      return;
    }

    try {
      const result = await handler({
        ...args,
        request_id,
        session_id: params.session_id,
        run_id: params.run_id,
      });
      // Send success response via client_response RPC method
      await this.clientResponse(
        request_id,
        true,
        result as Record<string, unknown>,
      );
    } catch (error) {
      // Send error response via client_response RPC method
      await this.clientResponse(request_id, false, undefined, {
        code: -32603,
        message: `Function execution failed: ${(error as Error).message}`,
      });
    }
  }

  /**
   * Send a JSON-RPC response
   */
  private sendResponse(id: string | number, result: unknown): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    const response: JSONRPCResponse = {
      jsonrpc: "2.0",
      id,
      result,
    };

    this.ws.send(JSON.stringify(response));
  }

  /**
   * Send a JSON-RPC error response
   */
  private sendError(id: string | number, code: number, message: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    const response: JSONRPCResponse = {
      jsonrpc: "2.0",
      id,
      error: { code, message },
    };

    this.ws.send(JSON.stringify(response));
  }

  /**
   * Attempt to reconnect to the server
   */
  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.emit("reconnect_failed");
      return;
    }

    this.reconnectAttempts++;
    this.emit("reconnecting", this.reconnectAttempts);

    setTimeout(() => {
      this.connect().catch(() => {
        // Connection failed, will retry via close event
      });
    }, this.reconnectDelay * this.reconnectAttempts);
  }

  /**
   * Debug logging
   */
  private log(...args: unknown[]): void {
    if (this.debug) {
      console.error("[RPC]", ...args);
    }
  }
}
