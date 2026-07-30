/**
 * JSON-RPC 2.0 WebSocket client for OmniAgents
 */

import WebSocket from "ws";
import { EventEmitter } from "events";
import type {
  JSONRPCRequest,
  JSONRPCResponse,
  JSONRPCNotification,
  JSONRPCErrorObject,
  ServerEvent,
  WebSocketMessage,
} from "../types/protocol.js";
import type {
  Capabilities,
  InitializeParams,
  InitializeResult,
  RpcMethodMap as GeneratedRpcMethodMap,
  RpcNotificationMap as GeneratedRpcNotificationMap,
} from "../protocol/generated/gui-v1.js";
import {
  classifyCloseCode,
  ConnectionClosedError,
  DEFAULT_LIFECYCLE_POLICY,
  ReconnectController,
  RpcAbortError,
  RpcTimeoutError,
  WS_CLOSE_FORBIDDEN,
  WS_CLOSE_UNAUTHENTICATED,
  type LifecyclePolicy,
} from "./lifecycle.js";
import type {
  GetModelResult,
  ListModelsResult,
  ListProvidersResult,
  SetSessionModelResult,
  SetSessionReasoningResult,
  SetVoiceModelResult,
} from "../protocol/catalog.js";
import { SessionReplayCoordinator, type ResumeResult } from "./replay.js";

type InkNotificationMap = {
  [Event in ServerEvent as Event["method"]]: Event["params"];
};
type RpcNotificationMap = {
  [Method in keyof GeneratedRpcNotificationMap]: Method extends keyof InkNotificationMap
    ? InkNotificationMap[Method]
    : GeneratedRpcNotificationMap[Method];
};
type RpcMethodMap = GeneratedRpcMethodMap;

const CLIENT_CAPABILITIES: Capabilities = {
  realtime: false,
  mcp_apps: false,
  client_functions: true,
  approvals: true,
  artifacts: false,
  replay: true,
  terminal: true,
  experimental_operations: [],
  disabled_notifications: [],
};

export class JSONRPCError extends Error {
  readonly code: number;
  readonly data?: unknown;

  constructor(error: JSONRPCErrorObject) {
    super(error.message);
    this.name = "JSONRPCError";
    this.code = error.code;
    this.data = error.data;
  }
}

export interface RPCClientOptions {
  url: string;
  token?: string;
  debug?: boolean;
  policy?: LifecyclePolicy;
}

export type RequestOptions = {
  /** Per-call deadline in ms. Omit for the policy default; null disables the deadline. */
  timeoutMs?: number | null;
  /** Abort long-running calls; rejects the promise with RpcAbortError. */
  signal?: AbortSignal;
};

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
  private policy: LifecyclePolicy;
  private requestId = 0;
  private pendingRequests = new Map<
    string | number,
    {
      method: string;
      resolve: (value: unknown) => void;
      reject: (error: Error) => void;
      cleanup: () => void;
    }
  >();
  private registeredClientFunctions = new Map<string, ClientFunction>();
  private reconnectCtrl: ReconnectController;
  private closedByUser = false;
  private handshakeRejected = false;
  private negotiatedInitialization: InitializeResult | null = null;
  private replay: SessionReplayCoordinator;
  private ackTimers = new Map<string, ReturnType<typeof setTimeout>>();

  /**
   * Fired when the sequenced event stream cannot be recovered by replay
   * (stream epoch changed or the server no longer retains the cursor).
   * Host apps must refetch authoritative state via getSessionHistory.
   */
  onResyncRequired: (sessionId: string) => void = () => {};

  constructor(options: RPCClientOptions) {
    super();
    this.url = options.url;
    this.token = options.token;
    this.debug = options.debug || false;
    this.policy = options.policy ?? DEFAULT_LIFECYCLE_POLICY;
    this.reconnectCtrl = new ReconnectController(this.policy);
    // Replay cursors live on the client (not the socket), so sequence
    // dedup and gap backfill survive lifecycle reconnects.
    this.replay = new SessionReplayCoordinator(
      (sessionId, streamId, afterSeq) =>
        this.resumeSession(sessionId, streamId ?? undefined, afterSeq),
      (method, params) =>
        this.deliverNotification({
          jsonrpc: "2.0",
          method,
          params,
        } as JSONRPCNotification),
      (sessionId) => this.onResyncRequired(sessionId),
    );
  }

  get initializeResult(): InitializeResult | null {
    return this.negotiatedInitialization;
  }

  override on<Event extends keyof RpcNotificationMap>(
    event: Event,
    listener: (payload: RpcNotificationMap[Event]) => void,
  ): this;
  override on(
    event:
      | "connected"
      | "rpc_ready"
      | "disconnected"
      | "error"
      | "reconnecting"
      | "reconnect_failed",
    listener: (...args: any[]) => void,
  ): this;
  override on(event: string, listener: (...args: any[]) => void): this {
    return super.on(event, listener);
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
   * Return whether the given name is registered as a client function.
   * Used by the message hook to hide agent tool calls that are actually
   * UI dispatches — the client paints the user-visible side-effect in
   * its handler, so the tool's transcript row would be redundant noise.
   */
  hasClientFunction(name: string): boolean {
    return this.registeredClientFunctions.has(name);
  }

  /**
   * Connect to the WebSocket server
   */
  async connect(): Promise<void> {
    this.closedByUser = false;
    this.handshakeRejected = false;
    return new Promise((resolve, reject) => {
      try {
        // The token travels as an Authorization header (never in the
        // URL): Node's ws client supports upgrade headers directly.
        this.ws = this.token
          ? new WebSocket(this.url, {
              headers: { Authorization: `Bearer ${this.token}` },
            })
          : new WebSocket(this.url);
        let ready = false;

        const connectTimer = setTimeout(() => {
          if (ready) return;
          this.log("Connect timed out");
          try {
            this.ws?.close();
          } catch {
            /* ignore */
          }
          reject(new RpcTimeoutError("connect", this.policy.connectTimeoutMs));
        }, this.policy.connectTimeoutMs);

        this.ws.on("message", (data: WebSocket.Data) => {
          try {
            const message = JSON.parse(data.toString()) as WebSocketMessage;
            this.handleMessage(message);
          } catch (error) {
            this.log("Failed to parse message:", error);
          }
        });

        this.ws.on("open", async () => {
          this.log("Connected to server");
          this.emit("connected");

          try {
            const initialize: InitializeParams = {
              protocol_version: "1.0.0",
              identity: { name: "omniagents-ink", version: "0.1.0" },
              platform: { os: process.platform, arch: process.arch },
              capabilities: CLIENT_CAPABILITIES,
            };
            // The connect timer bounds the whole attempt.
            this.negotiatedInitialization =
              await this.sendRequest<InitializeResult>("initialize", initialize, {
                timeoutMs: null,
              });
            this.sendNotification("initialized", {});
            ready = true;
            clearTimeout(connectTimer);
            this.reconnectCtrl.recordSuccess();
            this.emit("rpc_ready");
            this.log("RPC ready");
            resolve();
          } catch (error) {
            // A structured handshake rejection is deterministic; do not
            // loop on it. Closing the socket routes the close handler
            // into the permanent-failure path.
            clearTimeout(connectTimer);
            this.handshakeRejected = true;
            try {
              this.ws?.close();
            } catch {
              /* ignore */
            }
            reject(error);
          }
        });

        this.ws.on("close", (code?: number, reason?: Buffer) => {
          clearTimeout(connectTimer);
          this.log("Disconnected from server");
          this.emit("disconnected");
          const reasonText = reason?.toString() || undefined;
          const permanent =
            this.closedByUser ||
            this.handshakeRejected ||
            classifyCloseCode(code) === "permanent";
          if (code === WS_CLOSE_UNAUTHENTICATED || code === WS_CLOSE_FORBIDDEN) {
            // Credentials are the problem: surface it distinctly so the UI
            // can prompt instead of reporting a generic drop. The 44xx range
            // already classifies as permanent, so no reconnect is attempted.
            this.emit(
              "auth_failed",
              new Error(reasonText || "Authentication failed"),
            );
          }
          // No pending promise may remain unresolved after a disconnect.
          this.rejectAllPending(
            new ConnectionClosedError(reasonText || "Connection closed", {
              permanent,
              closeCode: code,
              reason: reasonText,
            }),
          );
          this.attemptReconnect(permanent);
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
    this.closedByUser = true;
    this.rejectAllPending(
      new ConnectionClosedError("Connection closed by client", {
        permanent: true,
      }),
    );
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private rejectAllPending(error: ConnectionClosedError): void {
    const entries = [...this.pendingRequests.values()];
    this.pendingRequests.clear();
    for (const entry of entries) {
      entry.cleanup();
      entry.reject(error);
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
    return this.call(
      "start_run",
      params as unknown as RpcMethodMap["start_run"]["params"],
    );
  }

  /**
   * Stop an active run
   */
  async stopRun(runId: string): Promise<void> {
    await this.call("stop_run", { run_id: runId });
  }

  /**
   * Get conversation history for a session
   */
  async getSessionHistory(
    sessionId: string,
  ): Promise<Array<{ role: string; content: unknown; timestamp: string }>> {
    return this.call("get_session_history", { session_id: sessionId }) as Promise<
      Array<{ role: string; content: unknown; timestamp: string }>
    >;
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
    return this.call("get_user_history", {
      limit,
      include_archived: includeArchived,
    }) as Promise<
      Array<{ content: string; session_id?: string; timestamp?: string }>
    >;
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
    return this.call("list_sessions") as Promise<
      Array<{
        id: string;
        created_at: string;
        archived: boolean;
        message_count: number;
        first_message?: unknown;
        last_message?: unknown;
      }>
    >;
  }

  /**
   * Archive a session
   */
  async archiveSession(sessionId: string): Promise<void> {
    await this.call("archive_session", { session_id: sessionId });
  }

  /**
   * Replay retained sequenced events after `afterSeq` from the server's
   * durable journal. Rejects with JSONRPCError code -32030 when the
   * cursor cannot be served (resync required).
   */
  async resumeSession(
    sessionId: string,
    streamId?: string,
    afterSeq?: number,
  ): Promise<ResumeResult> {
    const params: Record<string, unknown> = { session_id: sessionId };
    if (streamId) params.stream_id = streamId;
    if (afterSeq != null) params.after_seq = afterSeq;
    return (await this.call(
      "resume_session",
      params as unknown as RpcMethodMap["resume_session"]["params"],
    )) as ResumeResult;
  }

  /** Acknowledge applied events so the server can compact its journal. */
  async ackEvents(
    sessionId: string,
    streamId: string,
    seq: number,
  ): Promise<Record<string, unknown>> {
    return (await this.call("ack_events", {
      session_id: sessionId,
      stream_id: streamId,
      seq,
    })) as Record<string, unknown>;
  }

  /**
   * Advertise client capabilities (optional)
   */
  async clientFunctions(
    version: number,
    functions: Array<{ name: string; description?: string }>,
  ): Promise<void> {
    await this.call("client_functions", { version, functions });
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
    await this.call(
      "client_response",
      params as unknown as RpcMethodMap["client_response"]["params"],
    );
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
    return this.call(
      "tool_approval_response",
      params as unknown as RpcMethodMap["tool_approval_response"]["params"],
    );
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
    return this.call(
      "mcp_approval_response",
      params as unknown as RpcMethodMap["mcp_approval_response"]["params"],
    );
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
    return this.call("list_server_functions") as Promise<
      Array<{
        name: string;
        description?: string;
        params_schema?: Record<string, unknown>;
        result_schema?: Record<string, unknown>;
      }>
    >;
  }

  /**
   * Call a server function
   */
  async serverCall(
    func: string,
    args?: Record<string, unknown>,
    sessionId?: string,
    opts?: RequestOptions,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { function: func };
    if (args) {
      params.args = args;
    }
    if (sessionId) {
      params.session_id = sessionId;
    }
    return this.sendRequest<Record<string, unknown>>("server_call", params, opts);
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
    if (opts?.safeToolOverrides)
      params.safe_tool_overrides = opts.safeToolOverrides;
    if (opts?.source) params.source = opts.source;
    return this.call(
      "enqueue_message",
      params as unknown as RpcMethodMap["enqueue_message"]["params"],
    ) as Promise<{
      ok: boolean;
      id?: string;
      depth?: number;
      reason?: string;
      session_id?: string;
      enqueued_at?: number;
    }>;
  }

  async listQueue(sessionId: string): Promise<{
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
    return this.call("list_queue", { session_id: sessionId }) as Promise<{
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
    }>;
  }

  async cancelQueuedMessage(
    sessionId: string,
    itemId: string,
  ): Promise<{ ok: boolean; id?: string; depth?: number; reason?: string }> {
    return this.call("cancel_queued_message", {
      session_id: sessionId,
      item_id: itemId,
    }) as Promise<{ ok: boolean; id?: string; depth?: number; reason?: string }>;
  }

  // ---------------------------------------------------------------------
  // Model & capability catalog — the provider-neutral catalog behind all
  // GUI model pickers. See protocol.md § "Model & Capability Catalog"
  // and ../protocol/catalog.ts for the typed contract.
  // ---------------------------------------------------------------------

  async listModels(opts?: {
    includeHidden?: boolean;
    modality?: string;
    sessionId?: string;
  }): Promise<ListModelsResult> {
    const params: Record<string, unknown> = {};
    if (opts?.includeHidden) params.include_hidden = true;
    if (opts?.modality) params.modality = opts.modality;
    if (opts?.sessionId) params.session_id = opts.sessionId;
    return this.call(
      "list_models",
      params as unknown as RpcMethodMap["list_models"]["params"],
    ) as Promise<ListModelsResult>;
  }

  async getModel(model: string): Promise<GetModelResult> {
    return this.call("get_model", { model }) as Promise<GetModelResult>;
  }

  async listProviders(): Promise<ListProvidersResult> {
    return this.call("list_providers") as Promise<ListProvidersResult>;
  }

  async setSessionModel(
    sessionId: string,
    model: string,
  ): Promise<SetSessionModelResult> {
    return this.call("set_session_model", {
      session_id: sessionId,
      model,
    }) as Promise<SetSessionModelResult>;
  }

  async setSessionReasoning(
    sessionId: string,
    effort: string,
  ): Promise<SetSessionReasoningResult> {
    return this.call("set_session_reasoning", {
      session_id: sessionId,
      effort,
    }) as Promise<SetSessionReasoningResult>;
  }

  async setVoiceModel(model: string): Promise<SetVoiceModelResult> {
    return this.call("set_voice_model", { model }) as Promise<SetVoiceModelResult>;
  }

  /**
   * Make a generic RPC call
   */
  private async call<Method extends keyof RpcMethodMap>(
    method: Method,
    ...args: Record<string, never> extends RpcMethodMap[Method]["params"]
      ? [params?: RpcMethodMap[Method]["params"]]
      : [params: RpcMethodMap[Method]["params"]]
  ): Promise<RpcMethodMap[Method]["result"]> {
    return this.sendRequest<RpcMethodMap[Method]["result"]>(method, args[0]);
  }

  private sendNotification(
    method: string,
    params: Record<string, unknown>,
  ): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket not connected");
    }

    this.ws.send(JSON.stringify({ jsonrpc: "2.0", method, params }));
  }

  /**
   * Make a raw JSON-RPC request with lifecycle options (deadline, abort).
   * Prefer the typed wrappers for known methods.
   */
  request<Result = unknown>(
    method: string,
    params?: unknown,
    opts?: RequestOptions,
  ): Promise<Result> {
    return this.sendRequest<Result>(method, params, opts);
  }

  private sendRequest<Result>(
    method: string,
    params?: unknown,
    opts: RequestOptions = {},
  ): Promise<Result> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new ConnectionClosedError("WebSocket not connected", {
        permanent: this.closedByUser,
      });
    }
    if (opts.signal?.aborted) {
      throw new RpcAbortError(method);
    }

    const id = ++this.requestId;
    const request: JSONRPCRequest = {
      jsonrpc: "2.0",
      id,
      method,
    };
    if (params !== undefined) request.params = params;
    const timeoutMs =
      opts.timeoutMs === undefined ? this.policy.rpcTimeoutMs : opts.timeoutMs;

    return new Promise<Result>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | null = null;
      const onAbort = (): void => {
        this.pendingRequests.delete(id);
        cleanup();
        reject(new RpcAbortError(method));
      };
      const cleanup = (): void => {
        if (timer !== null) clearTimeout(timer);
        opts.signal?.removeEventListener("abort", onAbort);
      };
      if (timeoutMs !== null) {
        timer = setTimeout(() => {
          this.pendingRequests.delete(id);
          cleanup();
          reject(new RpcTimeoutError(method, timeoutMs));
        }, timeoutMs);
      }
      opts.signal?.addEventListener("abort", onAbort);
      this.pendingRequests.set(id, {
        method,
        resolve: resolve as (value: unknown) => void,
        reject,
        cleanup,
      });

      this.ws!.send(JSON.stringify(request), (error) => {
        if (error) {
          this.pendingRequests.delete(id);
          cleanup();
          reject(error);
        }
      });
    });
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
      const notification = message as JSONRPCNotification;
      // Sequence-aware dedup / gap recovery: drops duplicate deliveries
      // (a replayed client_request must not re-execute the function),
      // backfills gaps via resume_session, and reports unrecoverable
      // streams through onResyncRequired.
      if (
        this.replay.handle(
          notification.method,
          notification.params as Record<string, unknown> | undefined,
        )
      ) {
        return;
      }
      this.deliverNotification(notification);
      this.scheduleAck(notification.params as Record<string, unknown> | undefined);
      return;
    }

    this.log("Unknown message type:", message);
  }

  /** Dispatch a (deduplicated) notification to its handler. */
  private deliverNotification(notification: JSONRPCNotification): void {
    // Check if it's a client_request notification (special case)
    if (notification.method === "client_request") {
      this.handleClientRequest(notification);
      return;
    }
    this.handleNotification(notification);
  }

  /** Debounced ack of the latest applied sequence id per session. */
  private scheduleAck(params?: Record<string, unknown>): void {
    const sessionId = params?.session_id;
    if (typeof sessionId !== "string" || typeof params?.seq !== "number") {
      return;
    }
    if (this.ackTimers.has(sessionId)) return;
    this.ackTimers.set(
      sessionId,
      setTimeout(() => {
        this.ackTimers.delete(sessionId);
        const tracker = this.replay.tracker(sessionId);
        if (!tracker.streamId || tracker.lastSeq <= 0) return;
        this.ackEvents(sessionId, tracker.streamId, tracker.lastSeq).catch(
          () => {},
        );
      }, 500),
    );
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
      pending.reject(new JSONRPCError(response.error));
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
        this.log(
          "Unknown notification method:",
          (event as JSONRPCNotification).method,
        );
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
   * Attempt to reconnect to the server. Retryable failures back off on the
   * shared exponential schedule; permanent failures (auth rejection,
   * handshake rejection, user-initiated close, exhausted budget) stop for
   * good and surface a terminal `reconnect_failed` event.
   */
  private attemptReconnect(permanent: boolean): void {
    if (this.closedByUser) return;
    const decision = this.reconnectCtrl.recordFailure({ permanent });
    if (!decision.retry) {
      this.emit("reconnect_failed", {
        permanent: true,
        attempts: decision.attempt,
      });
      return;
    }

    this.emit("reconnecting", decision.attempt);

    setTimeout(() => {
      this.connect().catch(() => {
        // Connection failed, will retry via close event
      });
    }, decision.delayMs);
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
