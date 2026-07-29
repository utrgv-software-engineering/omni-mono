import type {
  Capabilities,
  InitializeParams,
  InitializeResult,
  RpcMethodMap,
  RpcNotificationMap,
} from '../protocol/generated/gui-v1'

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

type Listener = (payload: any) => void

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

export class RPCClient {
  private ws: WebSocket | null = null
  private url: string
  private token?: string
  private nextId = 0
  private pending = new Map<JSONRPCId, { resolve: (v: any) => void; reject: (e: Error) => void }>()
  private listeners = new Map<string, Set<Listener>>()
  private negotiatedInitialization: InitializeResult | null = null

  constructor(url: string, token?: string) {
    this.url = url
    this.token = token
  }

  get initializeResult(): InitializeResult | null {
    return this.negotiatedInitialization
  }

  async connect(): Promise<void> {
    const wsUrl = this.token ? `${this.url}?token=${encodeURIComponent(this.token)}` : this.url
    this.ws = new WebSocket(wsUrl)
    await new Promise<void>((resolve, reject) => {
      if (!this.ws) return reject(new Error('WebSocket init failed'))
      this.ws.onopen = () => resolve()
      this.ws.onerror = () => reject(new Error('WebSocket error'))
      this.ws.onmessage = (ev) => {
        this.handleMessage(JSON.parse(ev.data as string) as JSONRPCResponse | JSONRPCNotification)
      }
    })
    const initialize: InitializeParams = {
      protocol_version: '1.0.0',
      identity: { name: 'omniagents-web-ui', version: '0.0.0' },
      platform: { os: 'browser', arch: 'unknown' },
      capabilities: CLIENT_CAPABILITIES,
    }
    this.negotiatedInitialization = await this.sendRequest<InitializeResult>('initialize', initialize)
    this.sendNotification('initialized', {})
  }

  private handleMessage(msg: JSONRPCResponse | JSONRPCNotification): void {
    try {
      if ('id' in msg) {
        const pending = this.pending.get(msg.id)
        if (pending) {
          this.pending.delete(msg.id)
          if (msg.error) {
            pending.reject(new JSONRPCError(msg.error.code ?? -32603, msg.error.message, msg.error.data))
          } else {
            pending.resolve(msg.result)
          }
        }
      } else {
        this.emit(msg.method, msg.params)
      }
    } catch (e) {
      console.error('RPC parse error', e)
    }
  }

  private sendNotification(method: string, params: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error('WebSocket not connected')
    this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params }))
  }

  private sendRequest<Result>(method: string, params?: unknown): Promise<Result> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error('WebSocket not connected')
    const id = ++this.nextId
    const req: JSONRPCRequest = { jsonrpc: '2.0', id, method }
    if (params !== undefined) req.params = params
    const response = new Promise<Result>((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
    })
    this.ws.send(JSON.stringify(req))
    return response
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  on<E extends keyof RpcNotificationMap>(event: E, handler: (payload: RpcNotificationMap[E]) => void): () => void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set())
    const set = this.listeners.get(event)!
    set.add(handler)
    return () => set.delete(handler)
  }

  private emit(event: string, payload: any): void {
    const set = this.listeners.get(event)
    if (!set) return
    for (const fn of set) {
      try { fn(payload) } catch {}
    }
  }

  private async call<M extends keyof RpcMethodMap>(
    method: M,
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

  async serverCall(func: string, args?: Record<string, unknown>, sessionId?: string): Promise<Record<string, unknown>> {
    const params: Record<string, unknown> = { function: func }
    if (args) params.args = args
    if (sessionId) params.session_id = sessionId
    return this.call('server_call', params as unknown as RpcMethodMap['server_call']['params'])
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
