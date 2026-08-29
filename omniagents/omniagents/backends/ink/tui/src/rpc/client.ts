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
  EnvironmentSelection,
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
import { REQUESTED_EXPERIMENTAL_OPERATIONS } from "../protocol/experimental.js";
import type {
  ConversationItem,
  ConversationItemKind,
  ConversationItemPage,
  ConversationThread,
  ConversationTurnPage,
  GetPlanResult,
  GetRunDiffResult,
} from "../types/conversation.js";

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
  experimental_operations: [...REQUESTED_EXPERIMENTAL_OPERATIONS],
  disabled_notifications: [],
};

/**
 * When `initialize` is rejected because the server predates some of the
 * experimental operations we request, the typed error names exactly the
 * unsupported subset. Returns that subset, or null for any other error —
 * only this precise shape is safe to retry against.
 */
function unsupportedCapabilitiesOf(error: unknown): Set<string> | null {
  if (!(error instanceof JSONRPCError) || error.code !== -32013) return null;
  const data = error.data as
    | { kind?: string; unsupported_capabilities?: unknown }
    | undefined;
  if (data?.kind !== "capability_not_negotiated") return null;
  if (!Array.isArray(data.unsupported_capabilities)) return null;
  return new Set(data.unsupported_capabilities.filter(
    (name): name is string => typeof name === "string",
  ));
}

/**
 * Every server→client notification in the GUI protocol, re-emitted by the
 * client under its own method name. `satisfies Record<keyof …, true>`
 * makes the record exhaustive: a protocol regen that adds a notification
 * fails type-check until it is added (and, if experimental, opted into in
 * ../protocol/experimental.ts).
 */
const SERVER_NOTIFICATION_METHODS = {
  run_started: true,
  run_status: true,
  tool_called: true,
  tool_result: true,
  message_output: true,
  token: true,
  run_end: true,
  tool_approval_requested: true,
  tool_approval_resolved: true,
  mcp_approval_requested: true,
  mcp_approval_resolved: true,
  client_request: true,
  client_request_resolved: true,
  queue_changed: true,
  account_changed: true,
  elicitation_requested: true,
  elicitation_resolved: true,
  mcp_server_status_changed: true,
  session_forked: true,
  session_variables_changed: true,
  fs_events: true,
  fs_rescan_required: true,
  fs_transfer_progress: true,
  git_operation_progress: true,
  git_status_changed: true,
  thread_updated: true,
  item_updated: true,
  // Review outcomes. These arrive INSTEAD of an approval dialog when a
  // non-human reviewer (the guardian) is configured for the session, so
  // a client that ignores them shows nothing where a decision happened.
  tool_approval_reviewed: true,
  plan_completion_reviewed: true,
} as const satisfies Record<keyof GeneratedRpcNotificationMap, true>;

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
  // The experimental operations to request at the next handshake. Starts
  // at the full manifest; when an older server names an unsupported
  // subset in its typed rejection, that subset is stripped here so
  // reconnects don't re-trip the same rejection.
  private experimentalToRequest: string[] = [
    ...REQUESTED_EXPERIMENTAL_OPERATIONS,
  ];
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

  /**
   * Whether the server granted `operation` at the handshake. Stable
   * operations are always available and never appear in the grant list;
   * this answers for experimental requests AND notifications — UIs
   * feature-detect panels with it instead of try/catching -32013.
   */
  isGranted(operation: string): boolean {
    const granted =
      this.negotiatedInitialization?.capabilities.experimental_operations;
    return granted ? granted.includes(operation) : false;
  }

  /** The experimental operations the server granted (empty until ready). */
  get grantedExperimentalOperations(): readonly string[] {
    return (
      this.negotiatedInitialization?.capabilities.experimental_operations ?? []
    );
  }

  /**
   * Run the initialize handshake. If the server predates part of our
   * experimental manifest it rejects the whole call naming the
   * unsupported subset; strip exactly that subset and retry once. The
   * reduction is remembered on the client so later reconnects negotiate
   * cleanly on the first attempt.
   */
  private async performInitialize(): Promise<InitializeResult> {
    const attempt = (operations: string[]): Promise<InitializeResult> => {
      const initialize: InitializeParams = {
        protocol_version: "1.0.0",
        identity: { name: "omniagents-ink", version: "0.1.0" },
        platform: { os: process.platform, arch: process.arch },
        capabilities: {
          ...CLIENT_CAPABILITIES,
          experimental_operations: operations,
        },
      };
      return this.sendRequest<InitializeResult>("initialize", initialize, {
        timeoutMs: null,
      });
    };
    try {
      return await attempt(this.experimentalToRequest);
    } catch (error) {
      const unsupported = unsupportedCapabilitiesOf(error);
      if (!unsupported) throw error;
      this.log("Server rejected experimental capabilities:", [...unsupported]);
      this.experimentalToRequest = this.experimentalToRequest.filter(
        (name) => !unsupported.has(name),
      );
      return attempt(this.experimentalToRequest);
    }
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
            // The connect timer bounds the whole attempt.
            this.negotiatedInitialization = await this.performInitialize();
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
    environmentSelection: EnvironmentSelection,
    sessionId?: string,
    variables?: Record<string, unknown>,
  ): Promise<{ run_id: string; session_id: string }> {
    const params: Record<string, unknown> = {
      prompt,
      environment_selection: environmentSelection,
    };
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

  defaultEnvironmentSelection(): EnvironmentSelection {
    const environmentId = this.defaultEnvironmentId();
    return environmentId
      ? { mode: "explicit", environment_id: environmentId }
      : { mode: "none" };
  }

  /**
   * The AgentHost's default execution environment, from the handshake.
   * The id to route environment-scoped operations (fs_*, git_*,
   * server_call read leases) through when the UI has not chosen another.
   */
  defaultEnvironmentId(): string | null {
    return (
      this.negotiatedInitialization?.agent_host.default_environment_id ?? null
    );
  }

  /**
   * Stop an active run
   */
  async stopRun(runId: string): Promise<void> {
    await this.call("stop_run", { run_id: runId });
  }

  /**
   * Inject a user message into an active run (mid-run steering). The
   * server delivers it to the model at the run's next turn boundary.
   */
  async sendUserMessage(runId: string, content: string): Promise<void> {
    await this.call("send_user_message", { run_id: runId, content });
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
   * Delete a session permanently (archive is the recoverable variant).
   * `cascade` also deletes the session's forked descendants.
   */
  async deleteSession(sessionId: string, cascade?: boolean): Promise<boolean> {
    const params: Record<string, unknown> = { session_id: sessionId };
    if (cascade !== undefined) params.cascade = cascade;
    return this.call(
      "delete_session",
      params as unknown as RpcMethodMap["delete_session"]["params"],
    );
  }

  /** Static agent metadata (name, model, tool inventory, …). */
  async getAgentInfo(): Promise<Record<string, unknown>> {
    return this.call("get_agent_info") as Promise<Record<string, unknown>>;
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
   * Respond to an elicitation_requested event (structured user
   * elicitation — protocol.md § "User Elicitation Flow"). First response
   * across connected clients wins; duplicates reject with the typed
   * -32051 elicitation_already_resolved error.
   */
  async elicitationResponse(
    elicitationId: string,
    action: "accept" | "decline" | "cancel",
    value?: Record<string, unknown>,
    reason?: string,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = {
      elicitation_id: elicitationId,
      action,
    };
    if (value !== undefined) {
      params.value = value;
    }
    if (reason) {
      params.reason = reason;
    }
    return (await this.call(
      "elicitation_response",
      params as unknown as RpcMethodMap["elicitation_response"]["params"],
    )) as Record<string, unknown>;
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
   * Call a server function. An `OPTIONAL_WORKSPACE_READ` extension only
   * receives its centrally resolved read lease when the caller routes an
   * environment — pass `environmentId` (typically `defaultEnvironmentId()`)
   * for functions that read the workspace.
   */
  /** The RPC endpoint URL (for same-origin side sockets like /ws/terminal). */
  getUrl(): string {
    return this.url;
  }

  /** The bearer token, if this connection authenticates with one. */
  getToken(): string | undefined {
    return this.token;
  }

  async serverCall(
    func: string,
    args?: Record<string, unknown>,
    sessionId?: string,
    opts?: RequestOptions & {
      environmentId?: string;
      workspaceId?: string;
      environmentGeneration?: number;
    },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { function: func };
    if (args) {
      params.args = args;
    }
    if (sessionId) {
      params.session_id = sessionId;
    }
    if (opts?.environmentId) params.environment_id = opts.environmentId;
    if (opts?.workspaceId) params.workspace_id = opts.workspaceId;
    if (opts?.environmentGeneration !== undefined)
      params.environment_generation = opts.environmentGeneration;
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
  // Session control (experimental — gated on the initialize grant; see
  // ../protocol/experimental.ts and protocol.md).
  // ---------------------------------------------------------------------

  /**
   * Fork a session at an item/turn boundary. The server broadcasts
   * `session_forked` so every connected client learns the new id.
   */
  async forkSession(
    sessionId: string,
    opts?: { newSessionId?: string; fromItemId?: string; fromTurnId?: string },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { session_id: sessionId };
    if (opts?.newSessionId) params.new_session_id = opts.newSessionId;
    if (opts?.fromItemId) params.from_item_id = opts.fromItemId;
    if (opts?.fromTurnId) params.from_turn_id = opts.fromTurnId;
    return this.call(
      "fork_session",
      params as unknown as RpcMethodMap["fork_session"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** Hold (or release) queue draining for a session. */
  async setSessionHold(
    sessionId: string,
    hold: boolean,
  ): Promise<Record<string, unknown>> {
    return this.call("set_session_hold", {
      session_id: sessionId,
      hold,
    }) as Promise<Record<string, unknown>>;
  }

  /** Queue depth / hold state without the full item list. */
  async queueStatus(sessionId: string): Promise<Record<string, unknown>> {
    return this.call("queue_status", {
      session_id: sessionId,
    }) as Promise<Record<string, unknown>>;
  }

  /**
   * Enqueue a background notification message (never triggers a run,
   * unlike `enqueueMessage`).
   */
  async enqueueNotification(
    sessionId: string,
    content: string,
    opts?: { source?: string; safeToolOverrides?: Record<string, unknown> },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { session_id: sessionId, content };
    if (opts?.source) params.source = opts.source;
    if (opts?.safeToolOverrides)
      params.safe_tool_overrides = opts.safeToolOverrides;
    return this.call(
      "enqueue_notification",
      params as unknown as RpcMethodMap["enqueue_notification"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /**
   * Patch session variables; the server broadcasts
   * `session_variables_changed` with the changed keys and merged state.
   */
  async updateSessionVariables(
    sessionId: string,
    updates: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.call("update_session_variables", {
      session_id: sessionId,
      updates,
    }) as Promise<Record<string, unknown>>;
  }

  /** Publish a short client status line (surfaced to other clients). */
  async setClientStatus(text: string): Promise<void> {
    await this.call("set_client_status", { text });
  }

  /** Disable a tool for the remainder of the serve process. */
  async disableTool(toolName: string): Promise<void> {
    await this.call("disable_tool", { tool_name: toolName });
  }

  /** Disable an MCP server for the remainder of the serve process. */
  async disableMcpServer(serverName: string): Promise<void> {
    await this.call("disable_mcp_server", { server_name: serverName });
  }

  /** File an incident report, optionally disabling tools/servers with it. */
  async reportIncident(
    sessionId: string,
    description: string,
    opts?: { disableTools?: string[]; disableMcpServers?: string[] },
  ): Promise<void> {
    const params: Record<string, unknown> = {
      session_id: sessionId,
      description,
    };
    if (opts?.disableTools) params.disable_tools = opts.disableTools;
    if (opts?.disableMcpServers)
      params.disable_mcp_servers = opts.disableMcpServers;
    await this.call(
      "report_incident",
      params as unknown as RpcMethodMap["report_incident"]["params"],
    );
  }

  /** Export a session transcript; `redact` strips sensitive tool output. */
  async exportSession(
    sessionId: string,
    redact?: boolean,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { session_id: sessionId };
    if (redact !== undefined) params.redact = redact;
    return this.call(
      "export_session",
      params as unknown as RpcMethodMap["export_session"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  // ---------------------------------------------------------------------
  // Layered configuration (experimental) — read, validate and write the
  // server's layered settings. See protocol.md §"Configuration".
  // ---------------------------------------------------------------------

  async getConfig(): Promise<Record<string, unknown>> {
    return this.call("get_config") as Promise<Record<string, unknown>>;
  }

  async validateConfig(
    updates: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.call("validate_config", { updates }) as Promise<
      Record<string, unknown>
    >;
  }

  async writeConfig(
    updates: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.call("write_config", { updates }) as Promise<
      Record<string, unknown>
    >;
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

  // ---------------------------------------------------------------------
  // Provider accounts & usage — see protocol.md §"Provider Accounts".
  // Snapshots are redacted server-side (identity/quota only, never
  // credentials); listen for the `account_changed` notification to track
  // state without polling.
  // ---------------------------------------------------------------------

  async accountStatus(): Promise<AccountStatusResult> {
    return this.call("account_status") as Promise<AccountStatusResult>;
  }

  async accountUsage(provider?: string): Promise<AccountUsageResult> {
    const params: Record<string, unknown> = {};
    if (provider) params.provider = provider;
    return this.call(
      "account_usage",
      params as RpcMethodMap["account_usage"]["params"],
    ) as Promise<AccountUsageResult>;
  }

  async accountLoginStart(
    provider: string,
    mode: "device_code" | "browser" | "api_key",
    opts?: { apiKey?: string; redirectUri?: string },
  ): Promise<AccountLoginState> {
    const params: Record<string, unknown> = { provider, mode };
    if (opts?.apiKey) params.api_key = opts.apiKey;
    if (opts?.redirectUri) params.redirect_uri = opts.redirectUri;
    return this.call(
      "account_login_start",
      params as unknown as RpcMethodMap["account_login_start"]["params"],
    ) as Promise<AccountLoginState>;
  }

  async accountLoginComplete(
    loginId: string,
    code?: string,
  ): Promise<AccountLoginState> {
    const params: Record<string, unknown> = { login_id: loginId };
    if (code) params.code = code;
    return this.call(
      "account_login_complete",
      params as unknown as RpcMethodMap["account_login_complete"]["params"],
    ) as Promise<AccountLoginState>;
  }

  async accountLoginCancel(loginId: string): Promise<boolean> {
    return this.call("account_login_cancel", { login_id: loginId });
  }

  async accountLogout(
    provider: string,
  ): Promise<{ ok: boolean; account?: AccountSnapshot }> {
    return this.call("account_logout", { provider }) as Promise<{
      ok: boolean;
      account?: AccountSnapshot;
    }>;
  }

  async accountRefresh(
    provider: string,
  ): Promise<{ ok: boolean; account?: AccountSnapshot }> {
    return this.call("account_refresh", { provider }) as Promise<{
      ok: boolean;
      account?: AccountSnapshot;
    }>;
  }

  async accountSelect(
    provider: string,
  ): Promise<{ selected_provider: string; account?: AccountSnapshot }> {
    return this.call("account_select", { provider }) as Promise<{
      selected_provider: string;
      account?: AccountSnapshot;
    }>;
  }

  // ---------------------------------------------------------------------
  // MCP server management, status & OAuth — see protocol.md §"MCP Server
  // Management". Snapshots are redacted server-side (header/env values
  // are write-only `{is_set}` markers); listen for the
  // `mcp_server_status_changed` notification for startup/auth
  // transitions and refetch with `mcpListServers()` after a reconnect
  // (status events are broadcast, not journaled).
  // ---------------------------------------------------------------------

  async mcpListServers(sessionId?: string): Promise<McpListServersResult> {
    const params: Record<string, unknown> = {};
    if (sessionId) params.session_id = sessionId;
    return this.call(
      "mcp_list_servers",
      params as RpcMethodMap["mcp_list_servers"]["params"],
    ) as Promise<McpListServersResult>;
  }

  async mcpGetServer(
    serverName: string,
    refresh?: boolean,
  ): Promise<McpServerSnapshot> {
    const params: Record<string, unknown> = { server_name: serverName };
    if (refresh !== undefined) params.refresh = refresh;
    return this.call(
      "mcp_get_server",
      params as unknown as RpcMethodMap["mcp_get_server"]["params"],
    ) as Promise<McpServerSnapshot>;
  }

  async mcpCreateServer(
    serverName: string,
    type: McpTransport,
    params: Record<string, unknown>,
    serverOptions?: Record<string, unknown>,
  ): Promise<{ ok: boolean; server: McpServerSnapshot }> {
    const payload: Record<string, unknown> = {
      server_name: serverName,
      type,
      params,
    };
    if (serverOptions) payload.server_options = serverOptions;
    return this.call(
      "mcp_create_server",
      payload as unknown as RpcMethodMap["mcp_create_server"]["params"],
    ) as Promise<{ ok: boolean; server: McpServerSnapshot }>;
  }

  async mcpUpdateServer(
    serverName: string,
    updates: {
      type?: McpTransport;
      params?: Record<string, unknown>;
      serverOptions?: Record<string, unknown>;
    },
  ): Promise<{ ok: boolean; server: McpServerSnapshot }> {
    const payload: Record<string, unknown> = { server_name: serverName };
    if (updates.type) payload.type = updates.type;
    if (updates.params) payload.params = updates.params;
    if (updates.serverOptions) payload.server_options = updates.serverOptions;
    return this.call(
      "mcp_update_server",
      payload as unknown as RpcMethodMap["mcp_update_server"]["params"],
    ) as Promise<{ ok: boolean; server: McpServerSnapshot }>;
  }

  async mcpDeleteServer(
    serverName: string,
  ): Promise<{ ok: boolean; server_name: string }> {
    return this.call("mcp_delete_server", {
      server_name: serverName,
    }) as Promise<{ ok: boolean; server_name: string }>;
  }

  async mcpReloadServer(
    serverName?: string,
  ): Promise<{ ok: boolean; reloaded_sessions: number }> {
    const params: Record<string, unknown> = {};
    if (serverName) params.server_name = serverName;
    return this.call(
      "mcp_reload_server",
      params as RpcMethodMap["mcp_reload_server"]["params"],
    ) as Promise<{ ok: boolean; reloaded_sessions: number }>;
  }

  async mcpAuthStart(
    serverName: string,
    opts?: { redirectUri?: string; sessionId?: string },
  ): Promise<McpAuthState> {
    const params: Record<string, unknown> = { server_name: serverName };
    if (opts?.redirectUri) params.redirect_uri = opts.redirectUri;
    if (opts?.sessionId) params.session_id = opts.sessionId;
    return this.call(
      "mcp_auth_start",
      params as unknown as RpcMethodMap["mcp_auth_start"]["params"],
    ) as Promise<McpAuthState>;
  }

  async mcpAuthComplete(authId: string, code: string): Promise<McpAuthState> {
    return this.call("mcp_auth_complete", {
      auth_id: authId,
      code,
    }) as Promise<McpAuthState>;
  }

  async mcpAuthCancel(authId: string): Promise<boolean> {
    return this.call("mcp_auth_cancel", { auth_id: authId });
  }

  // ----- MCP content access (experimental) -----

  /** Read an MCP resource by URI. */
  async mcpReadResource(
    serverName: string,
    uri: string,
    sessionId?: string,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { server_name: serverName, uri };
    if (sessionId) params.session_id = sessionId;
    return this.call(
      "mcp_read_resource",
      params as unknown as RpcMethodMap["mcp_read_resource"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** Invoke an MCP tool directly (outside an agent run). */
  async mcpCallTool(
    serverName: string,
    toolName: string,
    sessionId: string,
    args?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = {
      server_name: serverName,
      tool_name: toolName,
      session_id: sessionId,
    };
    if (args) params.args = args;
    return this.call(
      "mcp_call_tool",
      params as unknown as RpcMethodMap["mcp_call_tool"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** Resolve an MCP prompt template. */
  async mcpGetPrompt(
    serverName: string,
    promptName: string,
    opts?: { args?: Record<string, unknown>; sessionId?: string },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = {
      server_name: serverName,
      prompt_name: promptName,
    };
    if (opts?.args) params.args = opts.args;
    if (opts?.sessionId) params.session_id = opts.sessionId;
    return this.call(
      "mcp_get_prompt",
      params as unknown as RpcMethodMap["mcp_get_prompt"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  // ---------------------------------------------------------------------
  // Canonical conversation domain objects — see protocol.md
  // §"Conversation Domain Objects". Reads serve the PERSISTED model: a
  // completed conversation renders from `listItems()` alone, with no
  // event-journal replay.
  // ---------------------------------------------------------------------

  async getThread(threadId: string): Promise<ConversationThread> {
    return this.call("get_thread", {
      thread_id: threadId,
    }) as unknown as Promise<ConversationThread>;
  }

  async listTurns(
    threadId: string,
    opts?: { limit?: number; cursor?: string; order?: "asc" | "desc" },
  ): Promise<ConversationTurnPage> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (opts?.limit !== undefined) params.limit = opts.limit;
    if (opts?.cursor) params.cursor = opts.cursor;
    if (opts?.order) params.order = opts.order;
    return this.call(
      "list_turns",
      params as unknown as RpcMethodMap["list_turns"]["params"],
    ) as unknown as Promise<ConversationTurnPage>;
  }

  async listItems(
    threadId: string,
    opts?: {
      turnId?: string;
      kinds?: ConversationItemKind[];
      limit?: number;
      cursor?: string;
      order?: "asc" | "desc";
    },
  ): Promise<ConversationItemPage> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (opts?.turnId) params.turn_id = opts.turnId;
    if (opts?.kinds) params.kinds = opts.kinds;
    if (opts?.limit !== undefined) params.limit = opts.limit;
    if (opts?.cursor) params.cursor = opts.cursor;
    if (opts?.order) params.order = opts.order;
    return this.call(
      "list_items",
      params as unknown as RpcMethodMap["list_items"]["params"],
    ) as unknown as Promise<ConversationItemPage>;
  }

  async getItem(threadId: string, itemId: string): Promise<ConversationItem> {
    return this.call("get_item", {
      thread_id: threadId,
      item_id: itemId,
    }) as unknown as Promise<ConversationItem>;
  }

  /**
   * Walk every page of a thread's items. Keyset pagination means a thread
   * that grows mid-walk neither skips nor repeats rows.
   */
  async listAllItems(
    threadId: string,
    opts?: {
      turnId?: string;
      kinds?: ConversationItemKind[];
      pageSize?: number;
    },
  ): Promise<ConversationItem[]> {
    const out: ConversationItem[] = [];
    let cursor: string | undefined;
    for (;;) {
      const page = await this.listItems(threadId, {
        turnId: opts?.turnId,
        kinds: opts?.kinds,
        limit: opts?.pageSize,
        cursor,
      });
      out.push(...page.items);
      if (!page.has_more || !page.next_cursor) break;
      cursor = page.next_cursor;
    }
    return out;
  }

  // ----- Thread organization & lifecycle (experimental) -----

  /** List threads with structured filters, keyset-paginated. */
  async listThreads(opts?: {
    status?: string;
    pinned?: boolean;
    source?: string;
    model?: string;
    parentThreadId?: string;
    createdAfter?: number;
    createdBefore?: number;
    updatedAfter?: number;
    updatedBefore?: number;
    limit?: number;
    cursor?: string;
    order?: string;
  }): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = {};
    if (opts?.status) params.status = opts.status;
    if (opts?.pinned !== undefined) params.pinned = opts.pinned;
    if (opts?.source) params.source = opts.source;
    if (opts?.model) params.model = opts.model;
    if (opts?.parentThreadId) params.parent_thread_id = opts.parentThreadId;
    if (opts?.createdAfter !== undefined) params.created_after = opts.createdAfter;
    if (opts?.createdBefore !== undefined)
      params.created_before = opts.createdBefore;
    if (opts?.updatedAfter !== undefined) params.updated_after = opts.updatedAfter;
    if (opts?.updatedBefore !== undefined)
      params.updated_before = opts.updatedBefore;
    if (opts?.limit !== undefined) params.limit = opts.limit;
    if (opts?.cursor) params.cursor = opts.cursor;
    if (opts?.order) params.order = opts.order;
    return this.call(
      "list_threads",
      params as unknown as RpcMethodMap["list_threads"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** Full-text thread search; accepts the same filters as listThreads. */
  async searchThreads(
    query: string,
    opts?: {
      status?: string;
      pinned?: boolean;
      source?: string;
      model?: string;
      parentThreadId?: string;
      createdAfter?: number;
      createdBefore?: number;
      updatedAfter?: number;
      updatedBefore?: number;
      limit?: number;
      cursor?: string;
    },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { query };
    if (opts?.status) params.status = opts.status;
    if (opts?.pinned !== undefined) params.pinned = opts.pinned;
    if (opts?.source) params.source = opts.source;
    if (opts?.model) params.model = opts.model;
    if (opts?.parentThreadId) params.parent_thread_id = opts.parentThreadId;
    if (opts?.createdAfter !== undefined) params.created_after = opts.createdAfter;
    if (opts?.createdBefore !== undefined)
      params.created_before = opts.createdBefore;
    if (opts?.updatedAfter !== undefined) params.updated_after = opts.updatedAfter;
    if (opts?.updatedBefore !== undefined)
      params.updated_before = opts.updatedBefore;
    if (opts?.limit !== undefined) params.limit = opts.limit;
    if (opts?.cursor) params.cursor = opts.cursor;
    return this.call(
      "search_threads",
      params as unknown as RpcMethodMap["search_threads"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /**
   * Update thread metadata (title, pinned, status, metadata). The server
   * broadcasts `thread_updated` naming the changed fields.
   */
  async updateThread(
    threadId: string,
    updates: {
      /**
       * ``null`` CLEARS the title (the protocol's `string | null`);
       * omitting the key leaves it untouched. The two are different
       * requests, so the type has to be able to say both.
       */
      title?: string | null;
      pinned?: boolean;
      status?: string;
      metadata?: Record<string, unknown>;
    },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (updates.title !== undefined) params.title = updates.title;
    if (updates.pinned !== undefined) params.pinned = updates.pinned;
    if (updates.status !== undefined) params.status = updates.status;
    if (updates.metadata !== undefined) params.metadata = updates.metadata;
    return this.call(
      "update_thread",
      params as unknown as RpcMethodMap["update_thread"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** The fork tree below a thread. */
  async listThreadDescendants(
    threadId: string,
    opts?: { maxDepth?: number; limit?: number },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (opts?.maxDepth !== undefined) params.max_depth = opts.maxDepth;
    if (opts?.limit !== undefined) params.limit = opts.limit;
    return this.call(
      "list_thread_descendants",
      params as unknown as RpcMethodMap["list_thread_descendants"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** Export a thread (optionally with descendants), paginated. */
  async exportThread(
    threadId: string,
    opts?: { limit?: number; cursor?: string; includeDescendants?: boolean },
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (opts?.limit !== undefined) params.limit = opts.limit;
    if (opts?.cursor) params.cursor = opts.cursor;
    if (opts?.includeDescendants !== undefined)
      params.include_descendants = opts.includeDescendants;
    return this.call(
      "export_thread",
      params as unknown as RpcMethodMap["export_thread"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  /** Purge threads older than the retention window; dry-run by default off. */
  async purgeThreads(
    retentionDays: number,
    dryRun?: boolean,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { retention_days: retentionDays };
    if (dryRun !== undefined) params.dry_run = dryRun;
    return this.call(
      "purge_threads",
      params as unknown as RpcMethodMap["purge_threads"]["params"],
    ) as Promise<Record<string, unknown>>;
  }

  // ----- Durable plans & aggregated run diffs (get_plan / get_run_diff) -----

  /**
   * The live structured plan for `scope` (default `main`), plus every plan
   * the thread has held. Render progress from `steps[].status`; there is
   * never a reason to parse assistant prose for it.
   */
  async getPlan(threadId: string, scope?: string): Promise<GetPlanResult> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (scope) params.scope = scope;
    return this.call(
      "get_plan",
      params as unknown as RpcMethodMap["get_plan"]["params"],
    ) as unknown as Promise<GetPlanResult>;
  }

  /**
   * The aggregated unified diff for one turn — the latest turn by default.
   * The NET change the run is responsible for: repeated edits to one file
   * collapse, and a file created then deleted inside the run does not appear.
   */
  async getRunDiff(
    threadId: string,
    turnId?: string,
  ): Promise<GetRunDiffResult> {
    const params: Record<string, unknown> = { thread_id: threadId };
    if (turnId) params.turn_id = turnId;
    return this.call(
      "get_run_diff",
      params as unknown as RpcMethodMap["get_run_diff"]["params"],
    ) as unknown as Promise<GetRunDiffResult>;
  }

  // ----- Workspace filesystem watch & chunked transfer (fs_*, experimental) -----

  async fsWatch(
    environmentId: string,
    path: string,
    opts?: { recursive?: boolean; pollIntervalMs?: number },
  ): Promise<FsWatchResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, path };
    if (opts?.recursive !== undefined) params.recursive = opts.recursive;
    if (opts?.pollIntervalMs !== undefined)
      params.poll_interval_ms = opts.pollIntervalMs;
    return this.call(
      "fs_watch",
      params as unknown as RpcMethodMap["fs_watch"]["params"],
    ) as Promise<FsWatchResult>;
  }

  async fsUnwatch(environmentId: string, watchId: string): Promise<boolean> {
    return this.call("fs_unwatch", {
      environment_id: environmentId,
      watch_id: watchId,
    });
  }

  async fsList(
    environmentId: string,
    path: string,
    recursive = false,
  ): Promise<FsListResult> {
    return this.call("fs_list", {
      environment_id: environmentId,
      path,
      recursive,
    }) as Promise<FsListResult>;
  }

  async fsStat(environmentId: string, path: string): Promise<FsStatResult> {
    return this.call("fs_stat", {
      environment_id: environmentId,
      path,
    }) as Promise<FsStatResult>;
  }

  async fsDownloadOpen(
    environmentId: string,
    path: string,
  ): Promise<FsDownloadOpenResult> {
    return this.call("fs_download_open", {
      environment_id: environmentId,
      path,
    }) as Promise<FsDownloadOpenResult>;
  }

  async fsDownloadRead(
    environmentId: string,
    transferId: string,
    opts?: { offset?: number; length?: number },
  ): Promise<FsDownloadChunk> {
    const params: Record<string, unknown> = {
      environment_id: environmentId,
      transfer_id: transferId,
    };
    if (opts?.offset !== undefined) params.offset = opts.offset;
    if (opts?.length !== undefined) params.length = opts.length;
    return this.call(
      "fs_download_read",
      params as unknown as RpcMethodMap["fs_download_read"]["params"],
    ) as Promise<FsDownloadChunk>;
  }

  async fsDownloadClose(
    environmentId: string,
    transferId: string,
  ): Promise<boolean> {
    return this.call("fs_download_close", {
      environment_id: environmentId,
      transfer_id: transferId,
    });
  }

  async fsUploadOpen(
    environmentId: string,
    path: string,
    size: number,
    opts?: { sha256?: string; expectedSha256?: string; overwrite?: boolean },
  ): Promise<FsUploadOpenResult> {
    const params: Record<string, unknown> = {
      environment_id: environmentId,
      path,
      size,
    };
    if (opts?.sha256 !== undefined) params.sha256 = opts.sha256;
    if (opts?.expectedSha256 !== undefined)
      params.expected_sha256 = opts.expectedSha256;
    if (opts?.overwrite !== undefined) params.overwrite = opts.overwrite;
    return this.call(
      "fs_upload_open",
      params as unknown as RpcMethodMap["fs_upload_open"]["params"],
    ) as Promise<FsUploadOpenResult>;
  }

  async fsUploadChunk(
    environmentId: string,
    transferId: string,
    offset: number,
    data: string,
  ): Promise<FsUploadChunkResult> {
    return this.call("fs_upload_chunk", {
      environment_id: environmentId,
      transfer_id: transferId,
      offset,
      data,
    }) as Promise<FsUploadChunkResult>;
  }

  async fsUploadCommit(
    environmentId: string,
    transferId: string,
  ): Promise<FsUploadCommitResult> {
    return this.call("fs_upload_commit", {
      environment_id: environmentId,
      transfer_id: transferId,
    }) as Promise<FsUploadCommitResult>;
  }

  async fsUploadAbort(
    environmentId: string,
    transferId: string,
  ): Promise<boolean> {
    return this.call("fs_upload_abort", {
      environment_id: environmentId,
      transfer_id: transferId,
    });
  }

  // ----- Typed git operations (git_*, experimental) -----

  async gitListRepositories(
    environmentId: string,
    opts?: { path?: string; maxDepth?: number },
  ): Promise<GitListRepositoriesResult> {
    const params: Record<string, unknown> = { environment_id: environmentId }
    if (opts?.path !== undefined) params.path = opts.path
    if (opts?.maxDepth !== undefined) params.max_depth = opts.maxDepth
    return this.call(
      "git_list_repositories",
      params as unknown as RpcMethodMap["git_list_repositories"]["params"],
    ) as Promise<GitListRepositoriesResult>
  }

  async gitStatus(
    environmentId: string,
    repo: string,
    opts?: { includeUntracked?: boolean; includeIgnored?: boolean; paths?: string[] },
  ): Promise<GitStatusResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.includeUntracked !== undefined) params.include_untracked = opts.includeUntracked
    if (opts?.includeIgnored !== undefined) params.include_ignored = opts.includeIgnored
    if (opts?.paths !== undefined) params.paths = opts.paths
    return this.call(
      "git_status",
      params as unknown as RpcMethodMap["git_status"]["params"],
    ) as Promise<GitStatusResult>
  }

  async gitDiff(
    environmentId: string,
    repo: string,
    opts?: {
      mode?: GitDiffMode
      paths?: string[]
      contextLines?: number
      fromRev?: string
      toRev?: string
      includeUntracked?: boolean
    },
  ): Promise<GitDiffResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.mode !== undefined) params.mode = opts.mode
    if (opts?.paths !== undefined) params.paths = opts.paths
    if (opts?.contextLines !== undefined) params.context_lines = opts.contextLines
    if (opts?.fromRev !== undefined) params.from_rev = opts.fromRev
    if (opts?.toRev !== undefined) params.to_rev = opts.toRev
    if (opts?.includeUntracked !== undefined)
      params.include_untracked = opts.includeUntracked
    return this.call(
      "git_diff",
      params as unknown as RpcMethodMap["git_diff"]["params"],
    ) as Promise<GitDiffResult>
  }

  async gitLog(
    environmentId: string,
    repo: string,
    opts?: { rev?: string; maxCount?: number; skip?: number; paths?: string[] },
  ): Promise<GitLogResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.rev !== undefined) params.rev = opts.rev
    if (opts?.maxCount !== undefined) params.max_count = opts.maxCount
    if (opts?.skip !== undefined) params.skip = opts.skip
    if (opts?.paths !== undefined) params.paths = opts.paths
    return this.call(
      "git_log",
      params as unknown as RpcMethodMap["git_log"]["params"],
    ) as Promise<GitLogResult>
  }

  async gitListBranches(
    environmentId: string,
    repo: string,
    includeRemote?: boolean,
  ): Promise<GitListBranchesResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (includeRemote !== undefined) params.include_remote = includeRemote
    return this.call(
      "git_list_branches",
      params as unknown as RpcMethodMap["git_list_branches"]["params"],
    ) as Promise<GitListBranchesResult>
  }

  async gitListWorktrees(environmentId: string, repo: string): Promise<GitListWorktreesResult> {
    return this.call("git_list_worktrees", {
      environment_id: environmentId,
      repo,
    }) as Promise<GitListWorktreesResult>
  }

  async gitConflicts(
    environmentId: string,
    repo: string,
    paths?: string[],
  ): Promise<GitConflictsResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (paths !== undefined) params.paths = paths
    return this.call(
      "git_conflicts",
      params as unknown as RpcMethodMap["git_conflicts"]["params"],
    ) as Promise<GitConflictsResult>
  }

  async gitStage(
    environmentId: string,
    repo: string,
    selection: GitSelection,
  ): Promise<GitStageResult> {
    return this.call(
      "git_stage",
      gitSelectionParams(environmentId, repo, selection) as unknown as RpcMethodMap["git_stage"]["params"],
    ) as Promise<GitStageResult>
  }

  async gitUnstage(
    environmentId: string,
    repo: string,
    selection: GitSelection,
  ): Promise<GitUnstageResult> {
    return this.call(
      "git_unstage",
      gitSelectionParams(environmentId, repo, selection) as unknown as RpcMethodMap["git_unstage"]["params"],
    ) as Promise<GitUnstageResult>
  }

  /**
   * Discard worktree changes. Destructive: the first call answers
   * `-32093 git_confirmation_required` with an impact description and a
   * single-use token that must be echoed back in `confirmationToken`.
   */
  async gitDiscard(
    environmentId: string,
    repo: string,
    selection: GitSelection & { confirmationToken?: string },
  ): Promise<GitDiscardResult> {
    const params = gitSelectionParams(environmentId, repo, selection)
    if (selection.confirmationToken !== undefined) {
      params.confirmation_token = selection.confirmationToken
    }
    return this.call(
      "git_discard",
      params as unknown as RpcMethodMap["git_discard"]["params"],
    ) as Promise<GitDiscardResult>
  }

  async gitCommit(
    environmentId: string,
    repo: string,
    message: string,
    opts?: { amend?: boolean; allowEmpty?: boolean; author?: string; confirmationToken?: string },
  ): Promise<GitCommitResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo, message }
    if (opts?.amend !== undefined) params.amend = opts.amend
    if (opts?.allowEmpty !== undefined) params.allow_empty = opts.allowEmpty
    if (opts?.author !== undefined) params.author = opts.author
    if (opts?.confirmationToken !== undefined) params.confirmation_token = opts.confirmationToken
    return this.call(
      "git_commit",
      params as unknown as RpcMethodMap["git_commit"]["params"],
    ) as Promise<GitCommitResult>
  }

  async gitCheckout(
    environmentId: string,
    repo: string,
    branch: string,
    opts?: {
      create?: boolean
      startPoint?: string
      detach?: boolean
      discardChanges?: boolean
      confirmationToken?: string
    },
  ): Promise<GitStatusResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo, branch }
    if (opts?.create !== undefined) params.create = opts.create
    if (opts?.startPoint !== undefined) params.start_point = opts.startPoint
    if (opts?.detach !== undefined) params.detach = opts.detach
    if (opts?.discardChanges !== undefined) params.discard_changes = opts.discardChanges
    if (opts?.confirmationToken !== undefined) params.confirmation_token = opts.confirmationToken
    return this.call(
      "git_checkout",
      params as unknown as RpcMethodMap["git_checkout"]["params"],
    ) as Promise<GitStatusResult>
  }

  async gitReset(
    environmentId: string,
    repo: string,
    opts?: { mode?: GitResetMode; rev?: string; paths?: string[]; confirmationToken?: string },
  ): Promise<GitStatusResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.mode !== undefined) params.mode = opts.mode
    if (opts?.rev !== undefined) params.rev = opts.rev
    if (opts?.paths !== undefined) params.paths = opts.paths
    if (opts?.confirmationToken !== undefined) params.confirmation_token = opts.confirmationToken
    return this.call(
      "git_reset",
      params as unknown as RpcMethodMap["git_reset"]["params"],
    ) as Promise<GitStatusResult>
  }

  async gitFetch(
    environmentId: string,
    repo: string,
    opts?: { remote?: string; refspec?: string; prune?: boolean },
  ): Promise<GitFetchResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.remote !== undefined) params.remote = opts.remote
    if (opts?.refspec !== undefined) params.refspec = opts.refspec
    if (opts?.prune !== undefined) params.prune = opts.prune
    return this.call(
      "git_fetch",
      params as unknown as RpcMethodMap["git_fetch"]["params"],
    ) as Promise<GitFetchResult>
  }

  async gitPull(
    environmentId: string,
    repo: string,
    opts?: { remote?: string; refspec?: string; rebase?: boolean },
  ): Promise<GitPullResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.remote !== undefined) params.remote = opts.remote
    if (opts?.refspec !== undefined) params.refspec = opts.refspec
    if (opts?.rebase !== undefined) params.rebase = opts.rebase
    return this.call(
      "git_pull",
      params as unknown as RpcMethodMap["git_pull"]["params"],
    ) as Promise<GitPullResult>
  }

  async gitPush(
    environmentId: string,
    repo: string,
    opts?: {
      remote?: string
      refspec?: string
      force?: boolean
      forceWithLease?: boolean
      setUpstream?: boolean
      confirmationToken?: string
    },
  ): Promise<GitPushResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.remote !== undefined) params.remote = opts.remote
    if (opts?.refspec !== undefined) params.refspec = opts.refspec
    if (opts?.force !== undefined) params.force = opts.force
    if (opts?.forceWithLease !== undefined) params.force_with_lease = opts.forceWithLease
    if (opts?.setUpstream !== undefined) params.set_upstream = opts.setUpstream
    if (opts?.confirmationToken !== undefined) params.confirmation_token = opts.confirmationToken
    return this.call(
      "git_push",
      params as unknown as RpcMethodMap["git_push"]["params"],
    ) as Promise<GitPushResult>
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
   *
   * Every protocol notification re-emits under its own method name.
   * The record below is compile-time exhaustive against the generated
   * notification map, so a protocol regen that adds a notification
   * fails `tsc` here instead of silently hitting the unknown branch.
   * (`client_request` never reaches this method — deliverNotification
   * routes it to the client-function executor first.)
   */
  private handleNotification(notification: JSONRPCNotification): void {
    if (notification.method in SERVER_NOTIFICATION_METHODS) {
      this.emit(notification.method, notification.params);
      return;
    }
    this.log("Unknown notification method:", notification.method);
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

/** Redacted provider account state — never contains credential material. */
export type AccountSnapshot = {
  id: string;
  label: string;
  kind: "oauth" | "api_key";
  capabilities: {
    login_modes: string[];
    logout: boolean;
    refresh: boolean;
    usage: boolean;
  };
  state: "signed_in" | "signed_out" | "error";
  source: "oauth" | "rpc" | "env" | null;
  identity: { account_id?: string; plan?: string; email?: string } | null;
  error: { status?: number | null; at?: number | null } | null;
  selected: boolean;
};

export type AccountStatusResult = {
  providers: AccountSnapshot[];
  selected_provider: string | null;
};

export type AccountRateLimit = {
  name: string;
  used_percent?: number;
  window_minutes?: number;
  resets_at?: number;
};

export type AccountUsageResult = {
  providers: Array<{
    id: string;
    plan: string | null;
    rate_limits: AccountRateLimit[];
    captured_at: number | null;
    notices: unknown[];
  }>;
};

/** Result of account_login_start / account_login_complete. */
export type AccountLoginState = {
  state: "pending" | "completed";
  mode: "device_code" | "browser" | "api_key";
  provider: string;
  login_id?: string;
  user_code?: string;
  verification_url?: string;
  interval?: number;
  auth_url?: string;
  redirect_uri?: string;
  account?: AccountSnapshot;
};

/** Payload of the `account_changed` server notification. */
export type AccountChangedPayload = {
  provider: string;
  reason: "login" | "logout" | "refresh" | "select";
  account?: AccountSnapshot;
};

export type McpTransport = "stdio" | "http" | "sse" | "streamable_http";

/**
 * Redacted MCP server snapshot — `params.headers` / `params.env` values
 * are write-only `{ is_set: true }` markers, never the stored secrets.
 */
export type McpServerSnapshot = {
  server_name: string;
  source: "project" | "user" | "project_local" | "session";
  transport: McpTransport;
  params: Record<string, unknown>;
  server_options: Record<string, unknown>;
  enabled: boolean;
  disabled_reason?: "user_mcp_disabled" | "allowlist_blocked" | "kill_switch";
  read_only: boolean;
  read_only_reason?: string;
  status: McpServerStatus;
  auth: McpAuthSnapshot;
  server_info?: Record<string, unknown> | null;
  capabilities?: Record<string, unknown> | null;
  counts?: {
    tools: number;
    resources: number;
    resource_templates: number;
    prompts: number;
  };
  captured_at?: string;
  tools?: Array<Record<string, unknown>>;
  resources?: Array<Record<string, unknown>>;
  resource_templates?: Array<Record<string, unknown>>;
  prompts?: Array<Record<string, unknown>>;
  supported?: Record<string, boolean>;
};

export type McpServerStatus = {
  state: "configured" | "starting" | "ready" | "failed" | "disabled";
  reason_code?: string;
  reason?: string;
  at?: string;
  origin?: string;
};

export type McpAuthSnapshot = {
  kind: "oauth" | "headers" | "env" | "none";
  state:
    | "authenticated"
    | "expired"
    | "pending"
    | "required"
    | "configured"
    | "not_authenticated"
    | "not_required"
    | "unknown";
};

export type McpListServersResult = {
  servers: McpServerSnapshot[];
  user_mcp_allowed: boolean;
  write_target: string;
};

/** Result of mcp_auth_start / mcp_auth_complete. */
export type McpAuthState = {
  state: "pending" | "completed";
  server_name: string;
  auth_id?: string;
  auth_url?: string;
  redirect_uri?: string;
  auth_state?: string;
};

/**
 * Payload of the `mcp_server_status_changed` server notification.
 * Broadcast (not journaled): refetch via `mcp_list_servers` on reconnect.
 */
export type McpServerStatusChangedPayload = {
  server_name: string;
  status: McpServerStatus["state"];
  previous_status?: McpServerStatus["state"];
  reason_code?: string;
  reason?: string;
  auth_state?: string;
  at?: string;
};

/** One entry in fs_list results and fs_events payloads. */
export type FsEntryType = "file" | "directory";

export type FsWatchResult = {
  watch_id: string;
  environment_id: string;
  path: string;
  recursive: boolean;
  mechanism: "poll:host" | "poll:workspace";
  poll_interval_ms: number;
};

export type FsListEntry = {
  path: string;
  type: FsEntryType;
  size: number | null;
  mtime: number | null;
};

export type FsListResult = {
  path: string;
  entries: FsListEntry[];
  truncated: boolean;
};

export type FsStatResult = {
  path: string;
  type: FsEntryType;
  size: number | null;
  mtime: number | null;
  mime?: string;
};

export type FsDownloadOpenResult = {
  transfer_id: string;
  environment_id: string;
  path: string;
  size: number;
  mime: string;
  mtime: number | null;
  sha256: string;
  chunk_size: number;
};

export type FsDownloadChunk = {
  transfer_id: string;
  offset: number;
  data: string;
  bytes: number;
  eof: boolean;
  size: number;
};

export type FsUploadOpenResult = {
  transfer_id: string;
  environment_id: string;
  path: string;
  chunk_size: number;
};

export type FsUploadChunkResult = {
  transfer_id: string;
  received: number;
  size: number;
};

export type FsUploadCommitResult = {
  path: string;
  size: number;
  sha256: string;
};

/** Payload of the `fs_events` server notification. */
export type FsEventsPayload = {
  environment_id: string;
  watch_id: string;
  events: Array<{
    type: "created" | "modified" | "deleted";
    path: string;
    entry_type: FsEntryType;
  }>;
};

/** Payload of the `fs_rescan_required` server notification. */
export type FsRescanRequiredPayload = {
  environment_id: string;
  watch_id: string;
  reason: "event_overflow" | "scan_limit_exceeded";
};

/** Payload of the `fs_transfer_progress` server notification. */
export type FsTransferProgressPayload = {
  environment_id: string;
  transfer_id: string;
  direction: "download" | "upload";
  transferred: number;
  total: number;
};

// ----- Typed git operations (git_*, experimental) -----

/** A repository is always addressed by its `repo` id — never implicitly. */
export type GitRepository = {
  repo: string
  root: string
  absolute_root: string
  branch: string | null
  detached: boolean
  bare: boolean
  git_common_dir: string | null
  is_linked_worktree: boolean
}

export type GitListRepositoriesResult = {
  environment_id: string
  path: string
  repositories: GitRepository[]
  truncated: boolean
}

export type GitDiffMode = "worktree" | "staged" | "head" | "range"
export type GitResetMode = "soft" | "mixed" | "hard"

export type GitFileStatus =
  | "unmodified"
  | "modified"
  | "type_changed"
  | "added"
  | "deleted"
  | "renamed"
  | "copied"
  | "unmerged"
  | "unknown"

export type GitStatusEntry = {
  path: string
  orig_path: string | null
  xy: string
  index_status: GitFileStatus
  worktree_status: GitFileStatus
  staged: boolean
  unstaged: boolean
  submodule: boolean
  similarity: number | null
  unmerged: {
    base_mode: string
    ours_mode: string
    theirs_mode: string
    worktree_mode: string
    base_oid: string
    ours_oid: string
    theirs_oid: string
  } | null
}

export type GitStatusResult = {
  environment_id: string
  repo: string
  head: { detached: boolean; unborn: boolean; branch: string | null; oid: string | null }
  upstream: { name: string; ahead: number; behind: number } | null
  entries: GitStatusEntry[]
  untracked: string[]
  ignored: string[]
  conflicted: string[]
  stash_count: number
  clean: boolean
  state: "clean" | "merging" | "rebasing" | "cherry_picking" | "reverting"
}

/** One line of a hunk. `origin` is structural — never a rendered prefix. */
export type GitDiffLine = {
  origin: "context" | "add" | "delete" | "no_newline"
  content: string
  old_lineno: number | null
  new_lineno: number | null
}

/**
 * `hunk_id` is content-addressed over the hunk body and the diff scope, so
 * a selection made against a stale diff fails closed (`-32094` with
 * `reason: "hunk_not_found"`) instead of applying somewhere unintended.
 */
export type GitDiffHunk = {
  hunk_id: string
  index: number
  header: string
  section_heading: string
  old_start: number
  old_lines: number
  new_start: number
  new_lines: number
  lines: GitDiffLine[]
}

export type GitDiffFile = {
  path: string
  old_path: string | null
  change: GitFileStatus
  old_mode: string
  new_mode: string
  old_oid: string
  new_oid: string
  similarity: number | null
  binary: boolean
  added_lines: number | null
  deleted_lines: number | null
  unmerged: boolean
  submodule: boolean
  /** False for binary blobs, submodules and unmerged paths: stage whole. */
  hunk_selectable: boolean
  hunks: GitDiffHunk[]
}

export type GitDiffResult = {
  environment_id: string
  repo: string
  mode: GitDiffMode
  context_lines: number
  files: GitDiffFile[]
}

export type GitCommitSummary = {
  oid: string
  short_oid: string
  parents: string[]
  author_name: string
  author_email: string
  authored_at: string
  committer_name: string
  committer_email: string
  committed_at: string
  refs: string[]
  subject: string
  body: string
}

export type GitLogResult = { environment_id: string; repo: string; commits: GitCommitSummary[] }

export type GitBranch = {
  ref: string
  name: string
  oid: string
  remote: boolean
  upstream: string | null
  upstream_remote: string | null
  current: boolean
  worktree_path: string | null
  category: "branch" | "worker"
}

export type GitListBranchesResult = { environment_id: string; repo: string; branches: GitBranch[] }

export type GitWorktree = {
  path: string
  head: string | null
  branch: string | null
  detached: boolean
  bare: boolean
  locked: boolean
  lock_reason: string | null
  prunable: boolean
  /** Workspace-relative `repo` id, or null when outside the workspace. */
  repo: string | null
  /** Worker worktrees live in the framework scratch area and are read-only here. */
  accessible: boolean
  inaccessible_reason: "outside_workspace" | null
  category: "worktree" | "worker"
}

export type GitListWorktreesResult = {
  environment_id: string
  repo: string
  worktrees: GitWorktree[]
}

/** A conflict region delimited by git's fixed ASCII markers. */
export type GitConflictRegion = {
  start_line: number
  end_line: number
  ours_label: string
  base_label: string | null
  theirs_label: string
  ours: string[]
  base: string[] | null
  theirs: string[]
}

export type GitConflict = {
  path: string
  stages: Partial<Record<"base" | "ours" | "theirs", { mode: string; oid: string }>>
  regions: GitConflictRegion[]
  regions_available: boolean
}

export type GitConflictsResult = {
  environment_id: string
  repo: string
  state: GitStatusResult["state"]
  conflicts: GitConflict[]
}

/** A hunk-level selection: `{ path, hunk_id }` from a `git_diff` result. */
export type GitHunkRef = { path: string; hunk_id: string }

export type GitSelection = {
  paths?: string[]
  hunks?: GitHunkRef[]
  contextLines?: number
  /** Which git_diff view the `hunks` came from (git_stage only). */
  mode?: "worktree" | "head"
}

export function gitSelectionParams(
  environmentId: string,
  repo: string,
  selection: GitSelection,
): Record<string, unknown> {
  const params: Record<string, unknown> = { environment_id: environmentId, repo }
  if (selection.paths !== undefined) params.paths = selection.paths
  if (selection.hunks !== undefined) params.hunks = selection.hunks
  if (selection.contextLines !== undefined) params.context_lines = selection.contextLines
  if (selection.mode !== undefined) params.mode = selection.mode
  return params
}

export type GitStageResult = {
  environment_id: string
  repo: string
  staged_paths: string[]
  staged_hunks: GitHunkRef[]
}

export type GitUnstageResult = {
  environment_id: string
  repo: string
  unstaged_paths: string[]
  unstaged_hunks: GitHunkRef[]
}

export type GitDiscardResult = {
  environment_id: string
  repo: string
  discarded_paths: string[]
  discarded_hunks: GitHunkRef[]
}

export type GitCommitResult = {
  environment_id: string
  repo: string
  oid: string
  amended: boolean
}

export type GitRefUpdate = {
  ref: string
  old_oid: string | null
  new_oid: string | null
  change: "created" | "updated" | "deleted"
}

export type GitFetchResult = {
  environment_id: string
  repo: string
  remote: string | null
  updated_refs: GitRefUpdate[]
}

export type GitPullResult = {
  environment_id: string
  repo: string
  ok: boolean
  state: GitStatusResult["state"]
  conflicted: string[]
  head: GitStatusResult["head"]
}

/**
 * `status` comes from `git push --porcelain`'s leading flag character;
 * `summary_raw` is display text and is never parsed.
 */
export type GitPushUpdate = {
  flag: string
  status: "fast_forward" | "forced_update" | "deleted" | "created" | "rejected" | "up_to_date" | "unknown"
  rejected: boolean
  source_ref: string
  target_ref: string
  summary_raw: string
  reason_raw: string | null
}

export type GitPushResult = {
  environment_id: string
  repo: string
  ok: boolean
  remote_url: string | null
  updates: GitPushUpdate[]
  rejected: string[]
}

/** Payload of the `git_operation_progress` server notification. */
export type GitOperationProgressPayload = {
  environment_id: string
  operation_id: string
  repo: string
  operation: "fetch" | "pull" | "push"
  phase: "started" | "completed" | "failed"
  detail?: Record<string, unknown>
}
