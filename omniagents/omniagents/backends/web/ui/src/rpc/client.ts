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
