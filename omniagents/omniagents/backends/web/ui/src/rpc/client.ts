import type {
  Capabilities,
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

const CLIENT_CAPABILITIES: Capabilities = {
  realtime: true,
  mcp_apps: true,
  client_functions: true,
  approvals: true,
  artifacts: true,
  replay: true,
  terminal: false,
  experimental_operations: [],
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
        const initialize: InitializeParams = {
          protocol_version: '1.0.0',
          identity: { name: 'omniagents-web-ui', version: '0.0.0' },
          platform: { os: 'browser', arch: 'unknown' },
          capabilities: CLIENT_CAPABILITIES,
        }
        // The connect timer bounds the whole attempt; no per-call deadline.
        const result = await this.sendRequest<InitializeResult>('initialize', initialize, { timeoutMs: null })
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
  async startRun(prompt: string, sessionId?: string, variables?: Record<string, unknown>, content?: unknown): Promise<{ run_id: string; session_id: string }> {
    const params: Record<string, unknown> = { prompt }
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

  // ----- Workspace filesystem watch & chunked transfer (fs_*, experimental) -----

  async fsWatch(
    sessionId: string,
    path: string,
    opts?: { recursive?: boolean; pollIntervalMs?: number },
  ): Promise<FsWatchResult> {
    const params: Record<string, unknown> = { session_id: sessionId, path }
    if (opts?.recursive !== undefined) params.recursive = opts.recursive
    if (opts?.pollIntervalMs !== undefined) params.poll_interval_ms = opts.pollIntervalMs
    return this.call('fs_watch', params as unknown as RpcMethodMap['fs_watch']['params']) as Promise<FsWatchResult>
  }

  async fsUnwatch(sessionId: string, watchId: string): Promise<boolean> {
    return this.call('fs_unwatch', { session_id: sessionId, watch_id: watchId })
  }

  async fsList(sessionId: string, path: string, recursive = false): Promise<FsListResult> {
    return this.call('fs_list', { session_id: sessionId, path, recursive }) as Promise<FsListResult>
  }

  async fsStat(sessionId: string, path: string): Promise<FsStatResult> {
    return this.call('fs_stat', { session_id: sessionId, path }) as Promise<FsStatResult>
  }

  async fsDownloadOpen(sessionId: string, path: string): Promise<FsDownloadOpenResult> {
    return this.call('fs_download_open', { session_id: sessionId, path }) as Promise<FsDownloadOpenResult>
  }

  async fsDownloadRead(
    sessionId: string,
    transferId: string,
    opts?: { offset?: number; length?: number },
  ): Promise<FsDownloadChunk> {
    const params: Record<string, unknown> = { session_id: sessionId, transfer_id: transferId }
    if (opts?.offset !== undefined) params.offset = opts.offset
    if (opts?.length !== undefined) params.length = opts.length
    return this.call('fs_download_read', params as unknown as RpcMethodMap['fs_download_read']['params']) as Promise<FsDownloadChunk>
  }

  async fsDownloadClose(sessionId: string, transferId: string): Promise<boolean> {
    return this.call('fs_download_close', { session_id: sessionId, transfer_id: transferId })
  }

  async fsUploadOpen(
    sessionId: string,
    path: string,
    size: number,
    opts?: { sha256?: string; overwrite?: boolean },
  ): Promise<FsUploadOpenResult> {
    const params: Record<string, unknown> = { session_id: sessionId, path, size }
    if (opts?.sha256 !== undefined) params.sha256 = opts.sha256
    if (opts?.overwrite !== undefined) params.overwrite = opts.overwrite
    return this.call('fs_upload_open', params as unknown as RpcMethodMap['fs_upload_open']['params']) as Promise<FsUploadOpenResult>
  }

  async fsUploadChunk(
    sessionId: string,
    transferId: string,
    offset: number,
    data: string,
  ): Promise<FsUploadChunkResult> {
    return this.call('fs_upload_chunk', {
      session_id: sessionId,
      transfer_id: transferId,
      offset,
      data,
    }) as Promise<FsUploadChunkResult>
  }

  async fsUploadCommit(sessionId: string, transferId: string): Promise<FsUploadCommitResult> {
    return this.call('fs_upload_commit', { session_id: sessionId, transfer_id: transferId }) as Promise<FsUploadCommitResult>
  }

  async fsUploadAbort(sessionId: string, transferId: string): Promise<boolean> {
    return this.call('fs_upload_abort', { session_id: sessionId, transfer_id: transferId })
  }
}

/** One entry in fs_list results and fs_events payloads. */
export type FsEntryType = 'file' | 'directory'

export type FsWatchResult = {
  watch_id: string
  session_id: string
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
  session_id: string
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
  session_id: string
  path: string
  chunk_size: number
}

export type FsUploadChunkResult = { transfer_id: string; received: number; size: number }

export type FsUploadCommitResult = { path: string; size: number; sha256: string }

/** Payload of the `fs_events` server notification. */
export type FsEventsPayload = {
  session_id: string
  watch_id: string
  events: Array<{ type: 'created' | 'modified' | 'deleted'; path: string; entry_type: FsEntryType }>
}

/** Payload of the `fs_rescan_required` server notification. */
export type FsRescanRequiredPayload = {
  session_id: string
  watch_id: string
  reason: 'event_overflow' | 'scan_limit_exceeded'
}

/** Payload of the `fs_transfer_progress` server notification. */
export type FsTransferProgressPayload = {
  session_id: string
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
