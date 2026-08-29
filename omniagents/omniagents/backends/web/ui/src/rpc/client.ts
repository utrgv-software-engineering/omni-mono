import type {
  Capabilities,
  EnvironmentSelection,
  InitializeParams,
  InitializeResult,
  RpcMethodMap,
  RpcNotificationMap,
} from '../protocol/generated/gui-v1'
import {
  classifyCloseCode,
  ConnectionClosedError,
  DEFAULT_LIFECYCLE_POLICY,
  ReconnectController,
  RpcAbortError,
  RpcTimeoutError,
  type ConnectionState,
  type LifecyclePolicy,
} from './lifecycle'
import type {
  GetModelResult,
  ListModelsResult,
  ListProvidersResult,
  SetSessionModelResult,
  SetSessionReasoningResult,
  SetVoiceModelResult,
} from '../protocol/catalog'
import { SessionReplayCoordinator, type ResumeResult } from './replay'
import { REQUESTED_EXPERIMENTAL_OPERATIONS } from '../protocol/experimental'

type JSONRPCId = number | string

type JSONRPCRequest = {
  jsonrpc: '2.0'
  id: JSONRPCId
  method: string
  params?: unknown
}

type JSONRPCResponse = {
  jsonrpc: '2.0'
  id: JSONRPCId
  result?: unknown
  error?: { code?: number; message: string; data?: unknown }
}

type JSONRPCNotification = {
  jsonrpc: '2.0'
  method: string
  params?: Record<string, unknown>
}

/**
 * Every server→client notification the GUI protocol defines.
 * `satisfies Record<keyof …, true>` makes the record exhaustive: a
 * protocol regen that adds a notification fails type-check here until it
 * is added (and, if experimental, opted into in ../protocol/experimental).
 *
 * It is a COMPILE-TIME guard only — dispatch stays permissive on
 * purpose. `realtime_event` is not in the generated map (the voice
 * pipeline rides the same channel out of band), so gating `emit` on this
 * record would silently take voice offline. Anything not listed here is
 * still delivered; it just does not get the drift guarantee.
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
  tool_approval_reviewed: true,
  plan_completion_reviewed: true,
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
} as const satisfies Record<keyof RpcNotificationMap, true>

/** True for a notification the generated protocol defines. */
export function isProtocolNotification(method: string): boolean {
  return method in SERVER_NOTIFICATION_METHODS
}

const CLIENT_CAPABILITIES: Capabilities = {
  realtime: true,
  mcp_apps: true,
  client_functions: true,
  approvals: true,
  artifacts: true,
  replay: true,
  // This UI ships a working TerminalPanel (terminal.create / terminal_id
  // over the session's PTY backend), so declaring false was simply
  // untrue. No server reads this field today, which is exactly why it
  // was able to drift — and why a server that starts gating on it would
  // take the web terminal offline for no discoverable reason.
  terminal: true,
  experimental_operations: [...REQUESTED_EXPERIMENTAL_OPERATIONS],
  disabled_notifications: [],
}

export type ServerEvent = JSONRPCNotification

type Listener = (payload: unknown) => void

/** WebSocket close codes that mean "your credentials are the problem" —
 * reconnecting with the same credentials will fail again, so callers
 * must never retry these automatically. */
export const WS_CLOSE_UNAUTHENTICATED = 4401
export const WS_CLOSE_FORBIDDEN = 4403

export function isAuthCloseCode(code: number): boolean {
  return code === WS_CLOSE_UNAUTHENTICATED || code === WS_CLOSE_FORBIDDEN
}

/**
 * Exchange a bearer token for a short-lived single-use connect ticket.
 *
 * Browsers cannot set headers on WebSocket upgrades, so the token goes
 * over an authenticated HTTP POST and only the one-time ticket appears
 * in the dial URL (the launcher's fetch-token-then-dial pattern).
 */
export async function fetchWsTicket(wsUrl: string, token: string): Promise<string> {
  const httpUrl = new URL(wsUrl)
  httpUrl.protocol = httpUrl.protocol === 'wss:' ? 'https:' : 'http:'
  httpUrl.pathname = '/auth/ws-ticket'
  httpUrl.search = ''
  const res = await fetch(httpUrl.toString(), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error(`Authentication failed (${res.status})`)
  const body = (await res.json()) as { ticket?: string }
  if (!body.ticket) throw new Error('Authentication failed (no ticket issued)')
  return body.ticket
}

/** Append a freshly minted connect ticket to a WebSocket URL when a
 * token is configured; otherwise return the URL unchanged. */
export async function withConnectTicket(wsUrl: string, token?: string): Promise<string> {
  if (!token) return wsUrl
  const ticket = await fetchWsTicket(wsUrl, token)
  const url = new URL(wsUrl)
  url.searchParams.set('ticket', ticket)
  return url.toString()
}

export class JSONRPCError extends Error {
  readonly code: number
  readonly data?: unknown

  constructor(code: number, message: string, data?: unknown) {
    super(message)
    this.name = 'JSONRPCError'
    this.code = code
    this.data = data
  }
}

/**
 * The experimental capability names a `-32013` handshake rejection
 * blames, or null when the error is anything else.
 *
 * Without this the manifest would be a liability rather than a feature:
 * a server older than one operation on the list rejects the WHOLE
 * `initialize`, and this client treats a structured handshake failure as
 * permanent — so a single unknown name would take the entire UI offline
 * instead of costing it one capability.
 */
function unsupportedCapabilitiesOf(error: unknown): Set<string> | null {
  if (!(error instanceof JSONRPCError) || error.code !== -32013) return null
  const data = error.data as { kind?: string; unsupported_capabilities?: unknown } | undefined
  if (data?.kind !== 'capability_not_negotiated') return null
  if (!Array.isArray(data.unsupported_capabilities)) return null
  return new Set(data.unsupported_capabilities.filter((name): name is string => typeof name === 'string'))
}

export type RequestOptions = {
  /** Per-call deadline in ms. Omit for the policy default; null disables the deadline. */
  timeoutMs?: number | null
  /** Abort long-running calls; rejects the promise with RpcAbortError. */
  signal?: AbortSignal
}

type PendingEntry = {
  method: string
  resolve: (v: unknown) => void
  reject: (e: Error) => void
  cleanup: () => void
}

export class RPCClient {
  private ws: WebSocket | null = null
  private url: string
  private token?: string
  private policy: LifecyclePolicy
  private nextId = 0
  private pending = new Map<JSONRPCId, PendingEntry>()
  private listeners = new Map<string, Set<Listener>>()
  private connectionListeners = new Set<(state: ConnectionState) => void>()
  private negotiatedInitialization: InitializeResult | null = null
  private reconnectCtrl: ReconnectController
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private closedByUser = false
  private lastConnectionState: ConnectionState = { state: 'connecting' }
  private connectSettlers: { resolve: () => void; reject: (e: Error) => void } | null = null
  private replay: SessionReplayCoordinator
  private ackTimers = new Map<string, ReturnType<typeof setTimeout>>()
  /**
   * The manifest to request at the next handshake. Starts as the full
   * opt-in list and shrinks permanently the first time a server rejects
   * part of it, so a reconnect loop against an older server does not
   * re-negotiate the same failure on every attempt.
   */
  private experimentalToRequest: readonly string[] = REQUESTED_EXPERIMENTAL_OPERATIONS

  /**
   * Fired when the sequenced event stream cannot be recovered by replay
   * (stream epoch changed or the server no longer retains the cursor).
   * Host apps must refetch authoritative state via getSessionHistory.
   */
  onResyncRequired: (sessionId: string) => void = () => {}

  constructor(url: string, token?: string, policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY) {
    this.url = url
    this.token = token
    this.policy = policy
    this.reconnectCtrl = new ReconnectController(policy)
    // Replay cursors live on the client (not the socket), so sequence
    // dedup and gap backfill survive lifecycle reconnects.
    this.replay = new SessionReplayCoordinator(
      (sessionId, streamId, afterSeq) => this.resumeSession(sessionId, streamId ?? undefined, afterSeq),
      (method, params) => this.emit(method, params),
      (sessionId) => this.onResyncRequired(sessionId),
    )
  }

  get initializeResult(): InitializeResult | null {
    return this.negotiatedInitialization
  }

  /**
   * Whether the server granted `operation` at the handshake. Stable
   * operations are always available and never appear in the grant list;
   * this answers for experimental requests AND notifications — UIs
   * feature-detect panels with it instead of try/catching -32013.
   *
   * Only meaningful once the handshake has landed: it reads a field
   * assigned immediately before the connection is reported connected.
   */
  isGranted(operation: string): boolean {
    const granted = this.negotiatedInitialization?.capabilities.experimental_operations
    return granted ? granted.includes(operation) : false
  }

  /** The experimental operations the server granted (empty until ready). */
  get grantedExperimentalOperations(): readonly string[] {
    return this.negotiatedInitialization?.capabilities.experimental_operations ?? []
  }

  get connectionState(): ConnectionState {
    return this.lastConnectionState
  }

  /**
   * Subscribe to connection-state changes so the UI can distinguish
   * retryable outages (`reconnecting`) from terminal failures
   * (`closed` with `permanent: true`). Returns an unsubscribe function.
   */
  onConnectionState(handler: (state: ConnectionState) => void): () => void {
    this.connectionListeners.add(handler)
    return () => this.connectionListeners.delete(handler)
  }

  private emitConnectionState(state: ConnectionState): void {
    this.lastConnectionState = state
    for (const fn of this.connectionListeners) {
      try { fn(state) } catch {}
    }
  }

  /**
   * Connect and keep the connection alive: retryable failures reconnect on
   * the policy backoff schedule; permanent failures (auth rejection,
   * handshake rejection, exhausted budget) stop for good. The returned
   * promise settles on the first successful handshake or the first
   * permanent failure.
   */
  async connect(): Promise<void> {
    this.closedByUser = false
    return new Promise<void>((resolve, reject) => {
      this.connectSettlers = { resolve, reject }
      this.emitConnectionState({ state: 'connecting' })
      this.openSocket()
    })
  }

  private settleConnect(outcome: 'resolve' | 'reject', error?: Error): void {
    const settlers = this.connectSettlers
    if (!settlers) return
    this.connectSettlers = null
    if (outcome === 'resolve') settlers.resolve()
    else settlers.reject(error ?? new ConnectionClosedError('Connection failed', { permanent: true }))
  }

  private openSocket(): void {
    // Credential resolution is async (bearer token -> one-time connect
    // ticket), so dialing is fire-and-forget; every failure path below
    // settles the connect promise itself.
    void this.dialSocket()
  }

  /**
   * Resolve credentials, then dial. Browsers cannot set upgrade headers, so
   * the bearer token is exchanged for a short-lived single-use ticket over
   * HTTP and only the ticket ever appears in the URL.
   */
  private async dialSocket(): Promise<void> {
    let wsUrl: string
    try {
      wsUrl = await withConnectTicket(this.url, this.token)
    } catch (e) {
      // A failed ticket exchange is a credentials problem: deterministic,
      // so it must never enter the reconnect loop.
      const error = new ConnectionClosedError(
        (e as Error).message || 'Authentication failed',
        { permanent: true },
      )
      this.rejectAllPending(error)
      this.emitConnectionState({ state: 'closed', permanent: true, reason: error.message })
      this.settleConnect('reject', error)
      return
    }
    if (this.closedByUser) return
    this.dial(wsUrl)
  }

  private dial(wsUrl: string): void {
    const ws = new WebSocket(wsUrl)
    this.ws = ws
    let settled = false
    let ready = false

    const fail = (closeError: ConnectionClosedError, connectError?: Error): void => {
      if (settled) return
      settled = true
      clearTimeout(connectTimer)
      ws.onopen = null
      ws.onerror = null
      ws.onmessage = null
      ws.onclose = null
      try { ws.close() } catch {}
      if (this.ws === ws) this.ws = null
      this.rejectAllPending(closeError)
      if (this.closedByUser) return
      const decision = this.reconnectCtrl.recordFailure({ permanent: closeError.permanent })
      if (!decision.retry) {
        const terminal = new ConnectionClosedError(closeError.message, {
          permanent: true,
          closeCode: closeError.closeCode,
          reason: closeError.reason,
        })
        this.emitConnectionState({
          state: 'closed',
          permanent: true,
          closeCode: closeError.closeCode,
          reason: closeError.reason ?? closeError.message,
        })
        this.settleConnect('reject', connectError ?? terminal)
        return
      }
      this.emitConnectionState({ state: 'reconnecting', attempt: decision.attempt, delayMs: decision.delayMs })
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null
        this.openSocket()
      }, decision.delayMs)
    }

    const connectTimer = setTimeout(() => {
      fail(new ConnectionClosedError(`Connect timed out after ${this.policy.connectTimeoutMs}ms`))
    }, this.policy.connectTimeoutMs)

    ws.onmessage = (ev) => {
      this.handleMessage(JSON.parse(ev.data as string) as JSONRPCResponse | JSONRPCNotification)
    }
    ws.onerror = () => {
      if (!ready) fail(new ConnectionClosedError('WebSocket error'))
    }
    ws.onclose = (ev?: CloseEvent) => {
      const code = ev?.code
      // 4401/4403 classify as permanent (44xx), so credential failures stop
      // the reconnect loop instead of retrying forever.
      const permanent = classifyCloseCode(code) === 'permanent'
      const reason =
        ev?.reason ||
        (code !== undefined && isAuthCloseCode(code) ? 'Authentication failed' : 'Connection closed')
      fail(new ConnectionClosedError(reason, { permanent, closeCode: code, reason: ev?.reason }))
    }
    ws.onopen = async () => {
      try {
        const attempt = (operations: readonly string[]): Promise<InitializeResult> => {
          const initialize: InitializeParams = {
            protocol_version: '1.0.0',
            identity: { name: 'omniagents-web-ui', version: '0.0.0' },
            platform: { os: 'browser', arch: 'unknown' },
            capabilities: { ...CLIENT_CAPABILITIES, experimental_operations: [...operations] },
          }
          // The connect timer bounds the whole attempt; no per-call deadline.
          return this.sendRequest<InitializeResult>('initialize', initialize, { timeoutMs: null })
        }
        // If the server predates part of our experimental manifest it
        // rejects the whole call naming the unsupported subset; strip
        // exactly that subset and retry once. The reduction is remembered
        // so later reconnects negotiate cleanly on the first attempt.
        let result: InitializeResult
        try {
          result = await attempt(this.experimentalToRequest)
        } catch (e) {
          const unsupported = unsupportedCapabilitiesOf(e)
          if (!unsupported) throw e
          this.experimentalToRequest = this.experimentalToRequest.filter((name) => !unsupported.has(name))
          result = await attempt(this.experimentalToRequest)
        }
        this.negotiatedInitialization = result
        this.sendNotification('initialized', {})
        ready = true
        clearTimeout(connectTimer)
        this.reconnectCtrl.recordSuccess()
        this.emitConnectionState({ state: 'connected' })
        this.settleConnect('resolve')
      } catch (e) {
        // A structured handshake rejection is deterministic — do not retry
        // it on a loop. Preserve the original error for the caller.
        fail(new ConnectionClosedError((e as Error).message, { permanent: true }), e as Error)
      }
    }
  }

  private rejectAllPending(error: ConnectionClosedError): void {
    const entries = [...this.pending.values()]
    this.pending.clear()
    for (const entry of entries) {
      entry.cleanup()
      entry.reject(error)
    }
  }

  private handleMessage(msg: JSONRPCResponse | JSONRPCNotification): void {
    try {
      if ('id' in msg) {
        const pending = this.pending.get(msg.id)
        if (pending) {
          this.pending.delete(msg.id)
          pending.cleanup()
          if (msg.error) {
            pending.reject(new JSONRPCError(msg.error.code ?? -32603, msg.error.message, msg.error.data))
          } else {
            pending.resolve(msg.result)
          }
        }
      } else {
        // Sequence-aware dedup / gap recovery: the coordinator drops
        // duplicate deliveries (so replays never create duplicate UI
        // items), backfills gaps via resume_session, and reports
        // unrecoverable streams through onResyncRequired.
        if (this.replay.handle(msg.method, msg.params)) {
          return
        }
        this.emit(msg.method, msg.params)
        this.scheduleAck(msg.params)
      }
    } catch (e) {
      console.error('RPC parse error', e)
    }
  }

  /** Debounced ack of the latest applied sequence id per session. */
  private scheduleAck(params?: Record<string, unknown>): void {
    const sessionId = params?.session_id
    if (typeof sessionId !== 'string' || typeof params?.seq !== 'number') return
    if (this.ackTimers.has(sessionId)) return
    this.ackTimers.set(
      sessionId,
      setTimeout(() => {
        this.ackTimers.delete(sessionId)
        const tracker = this.replay.tracker(sessionId)
        if (!tracker.streamId || tracker.lastSeq <= 0) return
        this.ackEvents(sessionId, tracker.streamId, tracker.lastSeq).catch(() => {})
      }, 500),
    )
  }

  private sendNotification(method: string, params: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new ConnectionClosedError('WebSocket not connected', { permanent: this.closedByUser })
    }
    this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params }))
  }

  /**
   * Issue a raw JSON-RPC request with lifecycle options (deadline, abort).
   * Prefer the typed wrappers for known methods.
   */
  request<Result = unknown>(method: string, params?: unknown, opts?: RequestOptions): Promise<Result> {
    return this.sendRequest<Result>(method, params, opts)
  }

  private sendRequest<Result>(method: string, params?: unknown, opts: RequestOptions = {}): Promise<Result> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new ConnectionClosedError('WebSocket not connected', { permanent: this.closedByUser })
    }
    if (opts.signal?.aborted) throw new RpcAbortError(method)
    const id = ++this.nextId
    const req: JSONRPCRequest = { jsonrpc: '2.0', id, method }
    if (params !== undefined) req.params = params
    const timeoutMs = opts.timeoutMs === undefined ? this.policy.rpcTimeoutMs : opts.timeoutMs
    const response = new Promise<Result>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | null = null
      const onAbort = (): void => {
        this.pending.delete(id)
        cleanup()
        reject(new RpcAbortError(method))
      }
      const cleanup = (): void => {
        if (timer !== null) clearTimeout(timer)
        opts.signal?.removeEventListener('abort', onAbort)
      }
      if (timeoutMs !== null) {
        timer = setTimeout(() => {
          this.pending.delete(id)
          cleanup()
          reject(new RpcTimeoutError(method, timeoutMs))
        }, timeoutMs)
      }
      opts.signal?.addEventListener('abort', onAbort)
      this.pending.set(id, { method, resolve, reject, cleanup })
    })
    this.ws.send(JSON.stringify(req))
    return response
  }

  disconnect(): void {
    this.closedByUser = true
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const error = new ConnectionClosedError('Connection closed by client', { permanent: true })
    this.rejectAllPending(error)
    if (this.ws) {
      const ws = this.ws
      this.ws = null
      ws.onclose = null
      ws.onerror = null
      try { ws.close() } catch {}
    }
    this.emitConnectionState({ state: 'closed', permanent: true, reason: 'closed by client' })
    this.settleConnect('reject', error)
  }

  on<E extends keyof RpcNotificationMap>(event: E, handler: (payload: RpcNotificationMap[E]) => void): () => void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set())
    const set = this.listeners.get(event)!
    set.add(handler)
    return () => set.delete(handler)
  }

  private emit(event: string, payload: unknown): void {
    const set = this.listeners.get(event)
    if (!set) return
    for (const fn of set) {
      try { fn(payload) } catch {}
    }
  }

  private async call<M extends keyof RpcMethodMap>(
    method: M,
    // eslint-disable-next-line @typescript-eslint/no-empty-object-type -- `{} extends P` is the canonical "all params optional" probe; `object`/`unknown` would break the conditional
    ...args: {} extends RpcMethodMap[M]['params']
      ? [params?: RpcMethodMap[M]['params']]
      : [params: RpcMethodMap[M]['params']]
  ): Promise<RpcMethodMap[M]['result']> {
    return this.sendRequest<RpcMethodMap[M]['result']>(method, args[0])
  }

  // RPC methods
  defaultEnvironmentSelection(): EnvironmentSelection {
    const environmentId = this.negotiatedInitialization?.agent_host.default_environment_id
    return environmentId
      ? { mode: 'explicit', environment_id: environmentId }
      : { mode: 'none' }
  }

  async startRun(prompt: string, environmentSelection: EnvironmentSelection, sessionId?: string, variables?: Record<string, unknown>, content?: unknown): Promise<{ run_id: string; session_id: string }> {
    const params: Record<string, unknown> = { prompt, environment_selection: environmentSelection }
    if (sessionId) params.session_id = sessionId
    if (variables) params.variables = variables
    if (content != null) params.content = content
    return this.call('start_run', params as unknown as RpcMethodMap['start_run']['params'])
  }

  async stopRun(runId: string): Promise<void> {
    await this.call('stop_run', { run_id: runId })
  }

  /**
   * Replay retained sequenced events after `afterSeq` from the server's
   * durable journal. Rejects with JSONRPCError code -32030 when the
   * cursor cannot be served (resync required).
   */
  async resumeSession(sessionId: string, streamId?: string, afterSeq?: number): Promise<ResumeResult> {
    const params: Record<string, unknown> = { session_id: sessionId }
    if (streamId) params.stream_id = streamId
    if (afterSeq != null) params.after_seq = afterSeq
    return this.call('resume_session', params as unknown as RpcMethodMap['resume_session']['params']) as Promise<ResumeResult>
  }

  /** Acknowledge applied events so the server can compact its journal. */
  async ackEvents(sessionId: string, streamId: string, seq: number): Promise<Record<string, unknown>> {
    return this.call('ack_events', { session_id: sessionId, stream_id: streamId, seq }) as Promise<Record<string, unknown>>
  }

  async getSessionHistory(sessionId: string): Promise<Array<{ role: string; content: unknown; timestamp: string }>> {
    return this.call('get_session_history', { session_id: sessionId }) as Promise<Array<{ role: string; content: unknown; timestamp: string }>>
  }

  async listSessions(): Promise<Array<{ id: string; created_at: string; archived: boolean; message_count: number; first_message?: unknown; last_message?: unknown }>> {
    return this.call('list_sessions') as Promise<Array<{ id: string; created_at: string; archived: boolean; message_count: number; first_message?: unknown; last_message?: unknown }>>
  }

  async deleteSession(sessionId: string): Promise<boolean> {
    return this.call('delete_session', { session_id: sessionId })
  }

  async clientResponse(requestId: string, ok: boolean, result?: Record<string, unknown>, error?: Record<string, unknown>): Promise<void> {
    const params: Record<string, unknown> = { request_id: requestId, ok }
    if (result) params.result = result
    if (error) params.error = error
    await this.call('client_response', params as unknown as RpcMethodMap['client_response']['params'])
  }

  async toolApprovalResponse(callId: string, decision: 'approve' | 'reject', alwaysApprove: boolean = false, rejectionMessage?: string): Promise<boolean> {
    const params: Record<string, unknown> = { call_id: callId, decision }
    if (alwaysApprove) params.always_approve = true
    if (rejectionMessage) params.rejection_message = rejectionMessage
    return this.call('tool_approval_response', params as unknown as RpcMethodMap['tool_approval_response']['params'])
  }

  // Hosted MCP-tool approvals use a parallel RPC method that takes a
  // ``request_id`` (minted by the model on the ``McpApprovalRequest``)
  // instead of the ``call_id`` used by function-tool approvals. There is
  // intentionally no ``always_approve`` flag — see protocol.md §"MCP
  // Approval Flow".
  async mcpApprovalResponse(requestId: string, decision: 'approve' | 'reject', rejectionMessage?: string): Promise<boolean> {
    const params: Record<string, unknown> = { request_id: requestId, decision }
    if (rejectionMessage) params.rejection_message = rejectionMessage
    return this.call('mcp_approval_response', params as unknown as RpcMethodMap['mcp_approval_response']['params'])
  }

  // Structured user elicitation (protocol.md § "User Elicitation Flow").
  // First response across tabs wins; the server answers duplicates with
  // the typed -32051 elicitation_already_resolved error.
  async elicitationResponse(elicitationId: string, action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { elicitation_id: elicitationId, action }
    if (value !== undefined) params.value = value
    if (reason) params.reason = reason
    return this.call('elicitation_response', params as unknown as RpcMethodMap['elicitation_response']['params']) as Promise<Record<string, unknown>>
  }

  async listServerFunctions(): Promise<Array<{ name: string; description?: string; params_schema?: Record<string, unknown>; result_schema?: Record<string, unknown> }>> {
    return this.call('list_server_functions') as Promise<Array<{ name: string; description?: string; params_schema?: Record<string, unknown>; result_schema?: Record<string, unknown> }>>
  }

  async serverCall(func: string, args?: Record<string, unknown>, sessionId?: string, opts?: RequestOptions): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { function: func }
    if (args) params.args = args
    if (sessionId) params.session_id = sessionId
    return this.sendRequest<Record<string, unknown>>('server_call', params, opts)
  }

  async clientFunctions(version: number, functions: Array<{ name: string; description?: string }>): Promise<void> {
    await this.call('client_functions', { version, functions })
  }

  // ---------------------------------------------------------------------
  // Model & capability catalog — the provider-neutral catalog behind all
  // GUI model pickers. See protocol.md § "Model & Capability Catalog"
  // and ../protocol/catalog.ts for the typed contract.
  // ---------------------------------------------------------------------

  async listModels(opts?: { includeHidden?: boolean; modality?: string; sessionId?: string }): Promise<ListModelsResult> {
    const params: Record<string, unknown> = {}
    if (opts?.includeHidden) params.include_hidden = true
    if (opts?.modality) params.modality = opts.modality
    if (opts?.sessionId) params.session_id = opts.sessionId
    return this.call('list_models', params as unknown as RpcMethodMap['list_models']['params']) as Promise<ListModelsResult>
  }

  async getModel(model: string): Promise<GetModelResult> {
    return this.call('get_model', { model }) as Promise<GetModelResult>
  }

  async listProviders(): Promise<ListProvidersResult> {
    return this.call('list_providers') as Promise<ListProvidersResult>
  }

  async setSessionModel(sessionId: string, model: string): Promise<SetSessionModelResult> {
    return this.call('set_session_model', { session_id: sessionId, model }) as Promise<SetSessionModelResult>
  }

  async setSessionReasoning(sessionId: string, effort: string): Promise<SetSessionReasoningResult> {
    return this.call('set_session_reasoning', { session_id: sessionId, effort }) as Promise<SetSessionReasoningResult>
  }

  async setVoiceModel(model: string): Promise<SetVoiceModelResult> {
    return this.call('set_voice_model', { model }) as Promise<SetVoiceModelResult>
  }

  async getAgentInfo(): Promise<{ name?: string; header_title?: string; page_title?: string; welcome_text?: string; page_title_suffix?: string; theme_color?: string }> {
    return this.call('get_agent_info') as Promise<{ name?: string; header_title?: string; page_title?: string; welcome_text?: string; page_title_suffix?: string; theme_color?: string }>
  }

  // ---------------------------------------------------------------------
  // Queue — background-injected messages serialized against current_task.
  // See omniagents/core/agents/service.py::enqueue_message and friends.
  // ---------------------------------------------------------------------

  async enqueueMessage(
    sessionId: string,
    content: string,
    opts?: {
      role?: string
      triggerRun?: boolean
      variables?: Record<string, unknown>
      safeToolOverrides?: Record<string, unknown>
      source?: string
    },
  ): Promise<{ ok: boolean; id?: string; depth?: number; reason?: string; session_id?: string; enqueued_at?: number }> {
    const params: Record<string, unknown> = { session_id: sessionId, content }
    if (opts?.role) params.role = opts.role
    if (opts?.triggerRun) params.trigger_run = true
    if (opts?.variables) params.variables = opts.variables
    if (opts?.safeToolOverrides) params.safe_tool_overrides = opts.safeToolOverrides
    if (opts?.source) params.source = opts.source
    return this.call('enqueue_message', params as unknown as RpcMethodMap['enqueue_message']['params']) as Promise<{ ok: boolean; id?: string; depth?: number; reason?: string; session_id?: string; enqueued_at?: number }>
  }

  async listQueue(sessionId: string): Promise<{ session_id: string; depth: number; items: QueuedMessage[] }> {
    return this.call('list_queue', { session_id: sessionId }) as Promise<{ session_id: string; depth: number; items: QueuedMessage[] }>
  }

  async cancelQueuedMessage(
    sessionId: string,
    itemId: string,
  ): Promise<{ ok: boolean; id?: string; depth?: number; reason?: string }> {
    return this.call('cancel_queued_message', { session_id: sessionId, item_id: itemId }) as Promise<{ ok: boolean; id?: string; depth?: number; reason?: string }>
  }

  // ---------------------------------------------------------------------
  // Provider accounts & usage — see protocol.md §"Provider Accounts".
  // Snapshots are redacted server-side (identity/quota only, never
  // credentials); subscribe to the `account_changed` notification via
  // `on('account_changed', …)` to track state without polling.
  // ---------------------------------------------------------------------

  async accountStatus(): Promise<AccountStatusResult> {
    return this.call('account_status') as Promise<AccountStatusResult>
  }

  async accountUsage(provider?: string): Promise<AccountUsageResult> {
    const params: Record<string, unknown> = {}
    if (provider) params.provider = provider
    return this.call('account_usage', params as RpcMethodMap['account_usage']['params']) as Promise<AccountUsageResult>
  }

  async accountLoginStart(
    provider: string,
    mode: 'device_code' | 'browser' | 'api_key',
    opts?: { apiKey?: string; redirectUri?: string },
  ): Promise<AccountLoginState> {
    const params: Record<string, unknown> = { provider, mode }
    if (opts?.apiKey) params.api_key = opts.apiKey
    if (opts?.redirectUri) params.redirect_uri = opts.redirectUri
    return this.call('account_login_start', params as unknown as RpcMethodMap['account_login_start']['params']) as Promise<AccountLoginState>
  }

  async accountLoginComplete(loginId: string, code?: string): Promise<AccountLoginState> {
    const params: Record<string, unknown> = { login_id: loginId }
    if (code) params.code = code
    return this.call('account_login_complete', params as unknown as RpcMethodMap['account_login_complete']['params']) as Promise<AccountLoginState>
  }

  async accountLoginCancel(loginId: string): Promise<boolean> {
    return this.call('account_login_cancel', { login_id: loginId })
  }

  async accountLogout(provider: string): Promise<{ ok: boolean; account?: AccountSnapshot }> {
    return this.call('account_logout', { provider }) as Promise<{ ok: boolean; account?: AccountSnapshot }>
  }

  async accountRefresh(provider: string): Promise<{ ok: boolean; account?: AccountSnapshot }> {
    return this.call('account_refresh', { provider }) as Promise<{ ok: boolean; account?: AccountSnapshot }>
  }

  async accountSelect(provider: string): Promise<{ selected_provider: string; account?: AccountSnapshot }> {
    return this.call('account_select', { provider }) as Promise<{ selected_provider: string; account?: AccountSnapshot }>
  }

  // ---------------------------------------------------------------------
  // MCP server management, status & OAuth — see protocol.md §"MCP Server
  // Management". Snapshots are redacted server-side (header/env values are
  // write-only `{is_set}` markers); subscribe to `mcp_server_status_changed`
  // via `on('mcp_server_status_changed', …)` for startup/auth transitions,
  // and refetch with `mcpListServers()` after a reconnect (status events
  // are broadcast, not journaled).
  // ---------------------------------------------------------------------

  async mcpListServers(sessionId?: string): Promise<McpListServersResult> {
    const params: Record<string, unknown> = {}
    if (sessionId) params.session_id = sessionId
    return this.call('mcp_list_servers', params as RpcMethodMap['mcp_list_servers']['params']) as Promise<McpListServersResult>
  }

  async mcpGetServer(serverName: string, refresh?: boolean): Promise<McpServerSnapshot> {
    const params: Record<string, unknown> = { server_name: serverName }
    if (refresh !== undefined) params.refresh = refresh
    return this.call('mcp_get_server', params as unknown as RpcMethodMap['mcp_get_server']['params']) as Promise<McpServerSnapshot>
  }

  async mcpCreateServer(
    serverName: string,
    type: McpTransport,
    params: Record<string, unknown>,
    serverOptions?: Record<string, unknown>,
  ): Promise<{ ok: boolean; server: McpServerSnapshot }> {
    const payload: Record<string, unknown> = { server_name: serverName, type, params }
    if (serverOptions) payload.server_options = serverOptions
    return this.call('mcp_create_server', payload as unknown as RpcMethodMap['mcp_create_server']['params']) as Promise<{ ok: boolean; server: McpServerSnapshot }>
  }

  async mcpUpdateServer(
    serverName: string,
    updates: { type?: McpTransport; params?: Record<string, unknown>; serverOptions?: Record<string, unknown> },
  ): Promise<{ ok: boolean; server: McpServerSnapshot }> {
    const payload: Record<string, unknown> = { server_name: serverName }
    if (updates.type) payload.type = updates.type
    if (updates.params) payload.params = updates.params
    if (updates.serverOptions) payload.server_options = updates.serverOptions
    return this.call('mcp_update_server', payload as unknown as RpcMethodMap['mcp_update_server']['params']) as Promise<{ ok: boolean; server: McpServerSnapshot }>
  }

  async mcpDeleteServer(serverName: string): Promise<{ ok: boolean; server_name: string }> {
    return this.call('mcp_delete_server', { server_name: serverName }) as Promise<{ ok: boolean; server_name: string }>
  }

  async mcpReloadServer(serverName?: string): Promise<{ ok: boolean; reloaded_sessions: number }> {
    const params: Record<string, unknown> = {}
    if (serverName) params.server_name = serverName
    return this.call('mcp_reload_server', params as RpcMethodMap['mcp_reload_server']['params']) as Promise<{ ok: boolean; reloaded_sessions: number }>
  }

  async mcpAuthStart(serverName: string, opts?: { redirectUri?: string; sessionId?: string }): Promise<McpAuthState> {
    const params: Record<string, unknown> = { server_name: serverName }
    if (opts?.redirectUri) params.redirect_uri = opts.redirectUri
    if (opts?.sessionId) params.session_id = opts.sessionId
    return this.call('mcp_auth_start', params as unknown as RpcMethodMap['mcp_auth_start']['params']) as Promise<McpAuthState>
  }

  async mcpAuthComplete(authId: string, code: string): Promise<McpAuthState> {
    return this.call('mcp_auth_complete', { auth_id: authId, code }) as Promise<McpAuthState>
  }

  async mcpAuthCancel(authId: string): Promise<boolean> {
    return this.call('mcp_auth_cancel', { auth_id: authId })
  }

  // ---------------------------------------------------------------------
  // Canonical conversation domain objects — see protocol.md
  // §"Conversation Domain Objects". These reads serve the PERSISTED model:
  // a completed conversation renders from `listItems()` alone, with no
  // event-journal replay. Live turns still stream over the existing
  // notifications; `item_id` correlation is derived, not transported.
  // ---------------------------------------------------------------------

  async getThread(threadId: string): Promise<ConversationThread> {
    return this.call('get_thread', { thread_id: threadId }) as unknown as Promise<ConversationThread>
  }

  async listTurns(
    threadId: string,
    opts?: { limit?: number; cursor?: string; order?: 'asc' | 'desc' },
  ): Promise<ConversationTurnPage> {
    const params: Record<string, unknown> = { thread_id: threadId }
    if (opts?.limit !== undefined) params.limit = opts.limit
    if (opts?.cursor) params.cursor = opts.cursor
    if (opts?.order) params.order = opts.order
    return this.call('list_turns', params as unknown as RpcMethodMap['list_turns']['params']) as unknown as Promise<ConversationTurnPage>
  }

  async listItems(
    threadId: string,
    opts?: {
      turnId?: string
      kinds?: ConversationItemKind[]
      limit?: number
      cursor?: string
      order?: 'asc' | 'desc'
    },
  ): Promise<ConversationItemPage> {
    const params: Record<string, unknown> = { thread_id: threadId }
    if (opts?.turnId) params.turn_id = opts.turnId
    if (opts?.kinds) params.kinds = opts.kinds
    if (opts?.limit !== undefined) params.limit = opts.limit
    if (opts?.cursor) params.cursor = opts.cursor
    if (opts?.order) params.order = opts.order
    return this.call('list_items', params as unknown as RpcMethodMap['list_items']['params']) as unknown as Promise<ConversationItemPage>
  }

  async getItem(threadId: string, itemId: string): Promise<ConversationItem> {
    return this.call('get_item', { thread_id: threadId, item_id: itemId }) as unknown as Promise<ConversationItem>
  }

  /**
   * Walk every page of a thread's items. Pagination is keyset-based, so a
   * thread that grows mid-walk neither skips nor repeats rows — which is
   * the whole reason large histories are paged instead of returned whole.
   */
  async listAllItems(
    threadId: string,
    opts?: { turnId?: string; kinds?: ConversationItemKind[]; pageSize?: number },
  ): Promise<ConversationItem[]> {
    const out: ConversationItem[] = []
    let cursor: string | undefined
    for (;;) {
      const page = await this.listItems(threadId, {
        turnId: opts?.turnId,
        kinds: opts?.kinds,
        limit: opts?.pageSize,
        cursor,
      })
      out.push(...page.items)
      if (!page.has_more || !page.next_cursor) break
      cursor = page.next_cursor
    }
    return out
  }
  // ----- Durable plans & aggregated run diffs (get_plan / get_run_diff) -----

  /**
   * The live structured plan for `scope` (default `main`), plus every plan
   * the thread has held. Render progress from `steps[].status`; there is
   * never a reason to parse assistant prose for it.
   */
  async getPlan(threadId: string, scope?: string): Promise<GetPlanResult> {
    const params: Record<string, unknown> = { thread_id: threadId }
    if (scope) params.scope = scope
    return this.call('get_plan', params as unknown as RpcMethodMap['get_plan']['params']) as unknown as Promise<GetPlanResult>
  }

  /**
   * The aggregated unified diff for one turn — the latest turn by default.
   * This is the NET change the run is responsible for: repeated edits to one
   * file collapse, and a file created then deleted inside the run does not
   * appear at all.
   */
  async getRunDiff(threadId: string, turnId?: string): Promise<GetRunDiffResult> {
    const params: Record<string, unknown> = { thread_id: threadId }
    if (turnId) params.turn_id = turnId
    return this.call('get_run_diff', params as unknown as RpcMethodMap['get_run_diff']['params']) as unknown as Promise<GetRunDiffResult>
  }

  // ----- Workspace filesystem watch & chunked transfer (fs_*, experimental) -----

  async fsWatch(
    environmentId: string,
    path: string,
    opts?: { recursive?: boolean; pollIntervalMs?: number },
  ): Promise<FsWatchResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, path }
    if (opts?.recursive !== undefined) params.recursive = opts.recursive
    if (opts?.pollIntervalMs !== undefined) params.poll_interval_ms = opts.pollIntervalMs
    return this.call('fs_watch', params as unknown as RpcMethodMap['fs_watch']['params']) as Promise<FsWatchResult>
  }

  async fsUnwatch(environmentId: string, watchId: string): Promise<boolean> {
    return this.call('fs_unwatch', { environment_id: environmentId, watch_id: watchId })
  }

  async fsList(environmentId: string, path: string, recursive = false): Promise<FsListResult> {
    return this.call('fs_list', { environment_id: environmentId, path, recursive }) as Promise<FsListResult>
  }

  async fsStat(environmentId: string, path: string): Promise<FsStatResult> {
    return this.call('fs_stat', { environment_id: environmentId, path }) as Promise<FsStatResult>
  }

  async fsDownloadOpen(environmentId: string, path: string): Promise<FsDownloadOpenResult> {
    return this.call('fs_download_open', { environment_id: environmentId, path }) as Promise<FsDownloadOpenResult>
  }

  async fsDownloadRead(
    environmentId: string,
    transferId: string,
    opts?: { offset?: number; length?: number },
  ): Promise<FsDownloadChunk> {
    const params: Record<string, unknown> = { environment_id: environmentId, transfer_id: transferId }
    if (opts?.offset !== undefined) params.offset = opts.offset
    if (opts?.length !== undefined) params.length = opts.length
    return this.call('fs_download_read', params as unknown as RpcMethodMap['fs_download_read']['params']) as Promise<FsDownloadChunk>
  }

  async fsDownloadClose(environmentId: string, transferId: string): Promise<boolean> {
    return this.call('fs_download_close', { environment_id: environmentId, transfer_id: transferId })
  }

  async fsUploadOpen(
    environmentId: string,
    path: string,
    size: number,
    opts?: { sha256?: string; expectedSha256?: string; overwrite?: boolean },
  ): Promise<FsUploadOpenResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, path, size }
    if (opts?.sha256 !== undefined) params.sha256 = opts.sha256
    if (opts?.expectedSha256 !== undefined) params.expected_sha256 = opts.expectedSha256
    if (opts?.overwrite !== undefined) params.overwrite = opts.overwrite
    return this.call('fs_upload_open', params as unknown as RpcMethodMap['fs_upload_open']['params']) as Promise<FsUploadOpenResult>
  }

  async fsUploadChunk(
    environmentId: string,
    transferId: string,
    offset: number,
    data: string,
  ): Promise<FsUploadChunkResult> {
    return this.call('fs_upload_chunk', {
      environment_id: environmentId,
      transfer_id: transferId,
      offset,
      data,
    }) as Promise<FsUploadChunkResult>
  }

  async fsUploadCommit(environmentId: string, transferId: string): Promise<FsUploadCommitResult> {
    return this.call('fs_upload_commit', { environment_id: environmentId, transfer_id: transferId }) as Promise<FsUploadCommitResult>
  }

  async fsUploadAbort(environmentId: string, transferId: string): Promise<boolean> {
    return this.call('fs_upload_abort', { environment_id: environmentId, transfer_id: transferId })
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
      'git_list_repositories',
      params as unknown as RpcMethodMap['git_list_repositories']['params'],
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
      'git_status',
      params as unknown as RpcMethodMap['git_status']['params'],
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
    },
  ): Promise<GitDiffResult> {
    const params: Record<string, unknown> = { environment_id: environmentId, repo }
    if (opts?.mode !== undefined) params.mode = opts.mode
    if (opts?.paths !== undefined) params.paths = opts.paths
    if (opts?.contextLines !== undefined) params.context_lines = opts.contextLines
    if (opts?.fromRev !== undefined) params.from_rev = opts.fromRev
    if (opts?.toRev !== undefined) params.to_rev = opts.toRev
    return this.call(
      'git_diff',
      params as unknown as RpcMethodMap['git_diff']['params'],
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
      'git_log',
      params as unknown as RpcMethodMap['git_log']['params'],
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
      'git_list_branches',
      params as unknown as RpcMethodMap['git_list_branches']['params'],
    ) as Promise<GitListBranchesResult>
  }

  async gitListWorktrees(environmentId: string, repo: string): Promise<GitListWorktreesResult> {
    return this.call('git_list_worktrees', {
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
      'git_conflicts',
      params as unknown as RpcMethodMap['git_conflicts']['params'],
    ) as Promise<GitConflictsResult>
  }

  async gitStage(
    environmentId: string,
    repo: string,
    selection: GitSelection,
  ): Promise<GitStageResult> {
    return this.call(
      'git_stage',
      gitSelectionParams(environmentId, repo, selection) as unknown as RpcMethodMap['git_stage']['params'],
    ) as Promise<GitStageResult>
  }

  async gitUnstage(
    environmentId: string,
    repo: string,
    selection: GitSelection,
  ): Promise<GitUnstageResult> {
    return this.call(
      'git_unstage',
      gitSelectionParams(environmentId, repo, selection) as unknown as RpcMethodMap['git_unstage']['params'],
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
      'git_discard',
      params as unknown as RpcMethodMap['git_discard']['params'],
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
      'git_commit',
      params as unknown as RpcMethodMap['git_commit']['params'],
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
      'git_checkout',
      params as unknown as RpcMethodMap['git_checkout']['params'],
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
      'git_reset',
      params as unknown as RpcMethodMap['git_reset']['params'],
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
      'git_fetch',
      params as unknown as RpcMethodMap['git_fetch']['params'],
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
      'git_pull',
      params as unknown as RpcMethodMap['git_pull']['params'],
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
      'git_push',
      params as unknown as RpcMethodMap['git_push']['params'],
    ) as Promise<GitPushResult>
  }
}

/** One entry in fs_list results and fs_events payloads. */
export type FsEntryType = 'file' | 'directory'

export type FsWatchResult = {
  watch_id: string
  environment_id: string
  path: string
  recursive: boolean
  mechanism: 'poll:host' | 'poll:workspace'
  poll_interval_ms: number
}

export type FsListEntry = {
  path: string
  type: FsEntryType
  size: number | null
  mtime: number | null
}

export type FsListResult = { path: string; entries: FsListEntry[]; truncated: boolean }

export type FsStatResult = {
  path: string
  type: FsEntryType
  size: number | null
  mtime: number | null
  mime?: string
}

export type FsDownloadOpenResult = {
  transfer_id: string
  environment_id: string
  path: string
  size: number
  mime: string
  mtime: number | null
  sha256: string
  chunk_size: number
}

export type FsDownloadChunk = {
  transfer_id: string
  offset: number
  data: string
  bytes: number
  eof: boolean
  size: number
}

export type FsUploadOpenResult = {
  transfer_id: string
  environment_id: string
  path: string
  chunk_size: number
}

export type FsUploadChunkResult = { transfer_id: string; received: number; size: number }

export type FsUploadCommitResult = { path: string; size: number; sha256: string }

/** Payload of the `fs_events` server notification. */
export type FsEventsPayload = {
  environment_id: string
  watch_id: string
  events: Array<{ type: 'created' | 'modified' | 'deleted'; path: string; entry_type: FsEntryType }>
}

/** Payload of the `fs_rescan_required` server notification. */
export type FsRescanRequiredPayload = {
  environment_id: string
  watch_id: string
  reason: 'event_overflow' | 'scan_limit_exceeded'
}

/** Payload of the `fs_transfer_progress` server notification. */
export type FsTransferProgressPayload = {
  environment_id: string
  transfer_id: string
  direction: 'download' | 'upload'
  transferred: number
  total: number
}

/** Redacted provider account state — never contains credential material. */
export type AccountSnapshot = {
  id: string
  label: string
  kind: 'oauth' | 'api_key'
  capabilities: { login_modes: string[]; logout: boolean; refresh: boolean; usage: boolean }
  state: 'signed_in' | 'signed_out' | 'error'
  source: 'oauth' | 'rpc' | 'env' | null
  identity: { account_id?: string; plan?: string; email?: string } | null
  error: { status?: number | null; at?: number | null } | null
  selected: boolean
}

export type AccountStatusResult = {
  providers: AccountSnapshot[]
  selected_provider: string | null
}

export type AccountRateLimit = {
  name: string
  used_percent?: number
  window_minutes?: number
  resets_at?: number
}

export type AccountUsageResult = {
  providers: Array<{
    id: string
    plan: string | null
    rate_limits: AccountRateLimit[]
    captured_at: number | null
    notices: unknown[]
  }>
}

/** Result of account_login_start / account_login_complete. */
export type AccountLoginState = {
  state: 'pending' | 'completed'
  mode: 'device_code' | 'browser' | 'api_key'
  provider: string
  login_id?: string
  user_code?: string
  verification_url?: string
  interval?: number
  auth_url?: string
  redirect_uri?: string
  account?: AccountSnapshot
}

/** Payload of the `account_changed` server notification. */
export type AccountChangedPayload = {
  provider: string
  reason: 'login' | 'logout' | 'refresh' | 'select'
  account?: AccountSnapshot
}

export type McpTransport = 'stdio' | 'http' | 'sse' | 'streamable_http'

/**
 * Redacted MCP server snapshot — `params.headers` / `params.env` values
 * are write-only `{ is_set: true }` markers, never the stored secrets.
 */
export type McpServerSnapshot = {
  server_name: string
  source: 'project' | 'user' | 'project_local' | 'session'
  transport: McpTransport
  params: Record<string, unknown>
  server_options: Record<string, unknown>
  enabled: boolean
  disabled_reason?: 'user_mcp_disabled' | 'allowlist_blocked' | 'kill_switch'
  read_only: boolean
  read_only_reason?: string
  status: McpServerStatus
  auth: McpAuthSnapshot
  server_info?: Record<string, unknown> | null
  capabilities?: Record<string, unknown> | null
  counts?: { tools: number; resources: number; resource_templates: number; prompts: number }
  captured_at?: string
  tools?: Array<Record<string, unknown>>
  resources?: Array<Record<string, unknown>>
  resource_templates?: Array<Record<string, unknown>>
  prompts?: Array<Record<string, unknown>>
  supported?: Record<string, boolean>
}

export type McpServerStatus = {
  state: 'configured' | 'starting' | 'ready' | 'failed' | 'disabled'
  reason_code?: string
  reason?: string
  at?: string
  origin?: string
}

export type McpAuthSnapshot = {
  kind: 'oauth' | 'headers' | 'env' | 'none'
  state: 'authenticated' | 'expired' | 'pending' | 'required' | 'configured' | 'not_authenticated' | 'not_required' | 'unknown'
}

export type McpListServersResult = {
  servers: McpServerSnapshot[]
  user_mcp_allowed: boolean
  write_target: string
}

/** Result of mcp_auth_start / mcp_auth_complete. */
export type McpAuthState = {
  state: 'pending' | 'completed'
  server_name: string
  auth_id?: string
  auth_url?: string
  redirect_uri?: string
  auth_state?: string
}

/**
 * Payload of the `mcp_server_status_changed` server notification.
 * Broadcast (not journaled): refetch via `mcp_list_servers` on reconnect.
 */
export type McpServerStatusChangedPayload = {
  server_name: string
  status: McpServerStatus['state']
  previous_status?: McpServerStatus['state']
  reason_code?: string
  reason?: string
  auth_state?: string
  at?: string
}

/**
 * Wire shape returned by ``list_queue`` and inside the ``queue_changed``
 * notification — matches ``QueuedItem.to_dict()`` on the server.
 */
export type QueuedMessage = {
  id: string
  content: string
  role: string
  trigger_run: boolean
  variables: Record<string, unknown> | null
  safe_tool_overrides: Record<string, unknown> | null
  source: string | null
  enqueued_at: number
}

/** Payload of the ``queue_changed`` server notification. */
export type QueueChangedPayload = {
  session_id: string
  depth: number
  items: QueuedMessage[]
}

// ---------------------------------------------------------------------
// Canonical conversation domain objects (tkt_a-QF2Oc4OAsD)
// ---------------------------------------------------------------------

export type ConversationItemKind =
  | 'user_message'
  | 'agent_message'
  | 'reasoning'
  | 'tool_call'
  | 'approval'
  | 'elicitation'
  | 'artifact'
  | 'compaction'
  /** Durable plan / run-level aggregated diff — see protocol.md
   * § Durable Structured Plans & Aggregated Run Diffs. */
  | 'plan'
  | 'run_diff'

/** Item lifecycle. Exactly one terminal state is ever reached. */
export type ConversationItemStatus = 'started' | 'completed' | 'failed' | 'cancelled'

export type ConversationTurnStatus = 'running' | 'completed' | 'failed' | 'cancelled'

export type ConversationToolKind =
  | 'command'
  | 'file'
  | 'search'
  | 'web'
  | 'mcp'
  | 'plan'
  | 'artifact'
  | 'worker'
  | 'other'

export type ConversationThread = {
  thread_id: string
  user_id: string | null
  status: 'active' | 'archived'
  title: string | null
  created_at: number
  updated_at: number
  last_seq: number
  turn_count: number
  item_count: number
  parent_thread_id: string | null
  branched_from_item_id: string | null
  usage: Record<string, unknown>
  compaction: Record<string, unknown>
  /** `recorded` = written live; `adapter` = projected from legacy history. */
  source: 'recorded' | 'adapter'
  /** When pre-existing history was materialized; null = not yet settled. */
  projected_at: number | null
  schema_version: number
}

export type ConversationTurn = {
  turn_id: string
  thread_id: string
  ordinal: number
  status: ConversationTurnStatus
  created_at: number
  updated_at: number
  completed_at: number | null
  prompt: string | null
  prompt_role: string | null
  end_reason: string | null
  error: Record<string, unknown> | null
  usage: Record<string, unknown>
  model: string | null
  model_ref: string | null
  item_count: number
  first_seq: number | null
  last_seq: number | null
  attempts: number
  source: 'recorded' | 'adapter'
  schema_version: number
}

/**
 * `content` is an open record whose shape follows `kind` — the server keeps
 * it open on purpose so a tool growing a field is not a protocol break.
 */
export type ConversationItem = {
  item_id: string
  thread_id: string
  turn_id: string | null
  /** Thread-monotonic ordinal. The ONLY ordering clients should rely on. */
  seq: number
  kind: ConversationItemKind
  status: ConversationItemStatus
  role: string | null
  created_at: number
  updated_at: number
  completed_at: number | null
  revision: number
  content: Record<string, unknown>
  source_ref: Record<string, unknown>
  /** Exempt from run-end finalization; the owning subsystem lands it. */
  long_lived: boolean
  source: 'recorded' | 'adapter'
  schema_version: number
}

export type ConversationTurnPage = {
  thread_id: string
  turns: ConversationTurn[]
  next_cursor: string | null
  has_more: boolean
  total: number
}

export type ConversationItemPage = {
  thread_id: string
  turn_id: string | null
  items: ConversationItem[]
  next_cursor: string | null
  has_more: boolean
  total: number
}

// ----- Durable plans & aggregated run diffs (experimental) -----
// See protocol.md § "Durable Structured Plans & Aggregated Run Diffs".
// These are convenience reads over the same `plan` / `run_diff` items
// `listItems()` returns: the server owns the scope and generation rules, so
// "what is the plan NOW" is answered once here rather than re-derived in
// every client.

/** Closed set. `blocked` is set by the agent, never inferred from edges. */
export type PlanStepStatus = 'pending' | 'in_progress' | 'completed' | 'blocked'

export type PlanStep = {
  id: string
  subject: string
  description: string
  active_form: string
  status: PlanStepStatus
  owner: string
  blocks: string[]
  blocked_by: string[]
}

export type ConversationPlan = {
  plan_id: string
  item_id: string
  thread_id: string
  turn_id: string | null
  scope: string
  generation: number
  steps: PlanStep[]
  counts: Record<PlanStepStatus, number>
  status: ConversationItemStatus
  /** Why a terminal plan landed (`thread_archived`); null while live. */
  finalized_by: string | null
  revision: number
  updated_at: number
}

export type GetPlanResult = {
  thread_id: string
  scope: string
  /** `null` when the conversation has never planned — not an error. */
  plan: ConversationPlan | null
  /** Every plan the thread has held, oldest first, across scopes. */
  plans: ConversationPlan[]
}

export type RunDiffFile = {
  path: string
  change_type: 'added' | 'modified' | 'deleted'
  additions: number
  deletions: number
  /** Binary or oversize: reported, but carries no hunks. */
  opaque: boolean
  /** The run's baseline for this path was never observed (the observer
   * bound mid-run, or the pre-image read failed). Neither creation nor
   * deletion is asserted and no hunks are carried. */
  baseline_unknown: boolean
}

export type ConversationRunDiff = {
  item_id: string
  run_id: string
  /** One unified diff covering every changed path. Truncated on a FILE
   * boundary when it exceeds the server cap, so it always parses; `files`
   * and `stats` stay complete. */
  diff: string
  files: RunDiffFile[]
  stats: { files_changed: number; additions: number; deletions: number }
  /** The rendered `diff` hit the size ceiling. `files` and `stats` did not. */
  truncated: boolean
  /** The `files` list itself hit its ceiling (500 entries); `stats` still
   * reports the true totals. */
  files_truncated: boolean
  status: ConversationItemStatus
  revision: number
  updated_at: number
}

export type GetRunDiffResult = {
  thread_id: string
  turn_id: string | null
  /** `null` when the turn changed no files — not an error. */
  run_diff: ConversationRunDiff | null
}

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

export type GitDiffMode = 'worktree' | 'staged' | 'head' | 'range'
export type GitResetMode = 'soft' | 'mixed' | 'hard'

export type GitFileStatus =
  | 'unmodified'
  | 'modified'
  | 'type_changed'
  | 'added'
  | 'deleted'
  | 'renamed'
  | 'copied'
  | 'unmerged'
  | 'unknown'

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
  state: 'clean' | 'merging' | 'rebasing' | 'cherry_picking' | 'reverting'
}

/** One line of a hunk. `origin` is structural — never a rendered prefix. */
export type GitDiffLine = {
  origin: 'context' | 'add' | 'delete' | 'no_newline'
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
  category: 'branch' | 'worker'
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
  inaccessible_reason: 'outside_workspace' | null
  category: 'worktree' | 'worker'
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
  stages: Partial<Record<'base' | 'ours' | 'theirs', { mode: string; oid: string }>>
  regions: GitConflictRegion[]
  regions_available: boolean
}

export type GitConflictsResult = {
  environment_id: string
  repo: string
  state: GitStatusResult['state']
  conflicts: GitConflict[]
}

/** A hunk-level selection: `{ path, hunk_id }` from a `git_diff` result. */
export type GitHunkRef = { path: string; hunk_id: string }

export type GitSelection = {
  paths?: string[]
  hunks?: GitHunkRef[]
  contextLines?: number
  /** Which git_diff view the `hunks` came from (git_stage only). */
  mode?: 'worktree' | 'head'
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
  change: 'created' | 'updated' | 'deleted'
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
  state: GitStatusResult['state']
  conflicted: string[]
  head: GitStatusResult['head']
}

/**
 * `status` comes from `git push --porcelain`'s leading flag character;
 * `summary_raw` is display text and is never parsed.
 */
export type GitPushUpdate = {
  flag: string
  status: 'fast_forward' | 'forced_update' | 'deleted' | 'created' | 'rejected' | 'up_to_date' | 'unknown'
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
  operation: 'fetch' | 'pull' | 'push'
  phase: 'started' | 'completed' | 'failed'
  detail?: Record<string, unknown>
}
