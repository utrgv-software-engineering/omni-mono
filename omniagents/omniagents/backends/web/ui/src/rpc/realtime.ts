import { isAuthCloseCode, withConnectTicket } from './client'

type JSONRPCId = number | string

type JSONRPCRequest = {
  jsonrpc: '2.0'
  id: JSONRPCId
  method: string
  params?: Record<string, unknown>
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

type Listener = (payload: any) => void

import { JSONRPCError } from './client'
import {
  classifyCloseCode,
  ConnectionClosedError,
  DEFAULT_LIFECYCLE_POLICY,
  RpcTimeoutError,
  type LifecyclePolicy,
} from './lifecycle'

export class RealtimeRPCClient {
  private ws: WebSocket | null = null
  private url: string
  private token?: string
  private debug: boolean = false
  private policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY
  private nextId = 0
  private pending = new Map<JSONRPCId, { resolve: (v: any) => void; reject: (e: Error) => void }>()
  private listeners = new Map<string, Set<Listener>>()
  private closedByUser = false

  constructor(url: string, token?: string, debug?: boolean) {
    this.url = url
    this.token = token
    this.debug = !!debug
  }

  async connect(): Promise<void> {
    // Token -> short-lived one-time connect ticket; the bearer token
    // itself never appears in the URL (and never in a debug log).
    const wsUrl = await withConnectTicket(this.url, this.token)
    if (this.debug) console.log('[rpc] connect')
    this.closedByUser = false
    this.ws = new WebSocket(wsUrl)
    await new Promise<void>((resolve, reject) => {
      if (!this.ws) return reject(new ConnectionClosedError('WebSocket init failed', { permanent: true }))
      const timer = setTimeout(() => {
        try { this.ws?.close() } catch {}
        reject(new RpcTimeoutError('connect', this.policy.connectTimeoutMs))
      }, this.policy.connectTimeoutMs)
      this.ws.onopen = () => {
        clearTimeout(timer)
        if (this.debug) console.log('[rpc] open')
        resolve()
      }
      this.ws.onclose = (ev) => {
        if (isAuthCloseCode(ev.code)) {
          // Credentials are the problem — terminal, never retried.
          clearTimeout(timer)
          reject(
            new ConnectionClosedError(ev.reason || 'Authentication failed', {
              permanent: true,
              closeCode: ev.code,
              reason: ev.reason,
            }),
          )
        }
      }
      this.ws.onerror = (e) => {
        clearTimeout(timer)
        if (this.debug) console.error('[rpc] error', e)
        reject(new ConnectionClosedError('WebSocket error'))
      }
    })
    if (!this.ws) throw new ConnectionClosedError('WebSocket disconnected')
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as JSONRPCResponse | JSONRPCNotification
        if (this.debug) console.log('[rpc] recv', msg)
        if ('id' in msg) {
          const pending = this.pending.get(msg.id)
          if (pending) {
            this.pending.delete(msg.id)
            const error = (msg as JSONRPCResponse).error
            // Preserve the structured JSON-RPC error (code + data), not
            // just the message string.
            if (error) pending.reject(new JSONRPCError(error.code ?? -32603, error.message, error.data))
            else pending.resolve((msg as JSONRPCResponse).result)
          }
        } else {
          const evt = msg as JSONRPCNotification
          this.emit(evt.method, evt.params)
        }
      } catch (e) {
        if (this.debug) console.error('[rpc] parse error', e)
      }
    }
    this.ws.onclose = (e) => {
      if (this.debug) console.log('[rpc] close', e?.code, e?.reason)
      // No pending promise may remain unresolved after the link drops.
      this.rejectAllPending(
        new ConnectionClosedError(e?.reason || 'Connection closed', {
          permanent: this.closedByUser || classifyCloseCode(e?.code) === 'permanent',
          closeCode: e?.code,
          reason: e?.reason,
        }),
      )
    }
  }

  private rejectAllPending(error: ConnectionClosedError): void {
    const entries = [...this.pending.values()]
    this.pending.clear()
    for (const entry of entries) entry.reject(error)
  }

  disconnect(): void {
    this.closedByUser = true
    this.rejectAllPending(new ConnectionClosedError('Connection closed by client', { permanent: true }))
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  on(event: string, handler: Listener): () => void {
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

  private async call<T = any>(method: string, params?: Record<string, unknown>): Promise<T> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error('WebSocket not connected')
    const id = ++this.nextId
    const req: JSONRPCRequest = { jsonrpc: '2.0', id, method, params }
    if (this.debug) console.log('[rpc] call', method, params)
    const p = new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as any, reject })
    })
    this.ws.send(JSON.stringify(req))
    return p
  }

  private async notify(method: string, params?: Record<string, unknown>): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error('WebSocket not connected')
    const msg: any = { jsonrpc: '2.0', method }
    if (params) msg.params = params
    if (this.debug) console.log('[rpc] notify', method, params)
    this.ws.send(JSON.stringify(msg))
  }

  async capabilities(): Promise<{ enabled: boolean; kind?: string; agent_name?: string | null }> {
    return this.call('capabilities')
  }

  async startSession(sessionId?: string): Promise<{ session_id: string; run_id: string }> {
    const params: Record<string, unknown> = {}
    if (sessionId) params.session_id = sessionId
    return this.call('start_session', params)
  }

  async stopSession(sessionId: string): Promise<boolean> {
    return this.call('stop_session', { session_id: sessionId })
  }

  async sendAudio(sessionId: string, audioBase64: string, commit?: boolean): Promise<boolean> {
    const params: Record<string, unknown> = { session_id: sessionId, audio_base64: audioBase64 }
    if (commit) params.commit = true
    try {
      await this.notify('send_audio', params)
      return true
    } catch {
      return false
    }
  }

  async interrupt(sessionId: string): Promise<boolean> {
    return this.call('interrupt', { session_id: sessionId })
  }

  async clientResponse(requestId: string, ok: boolean, result?: Record<string, unknown>): Promise<boolean> {
    return this.call('client_response', { request_id: requestId, ok, result })
  }

  async toolApprovalResponse(callId: string, decision: 'approve' | 'reject', alwaysApprove: boolean = false, rejectionMessage?: string): Promise<boolean> {
    const params: Record<string, unknown> = { call_id: callId, decision }
    if (alwaysApprove) params.always_approve = true
    if (rejectionMessage) params.rejection_message = rejectionMessage
    return this.call('tool_approval_response', params)
  }

  // Hosted-MCP approvals are keyed by request_id (from the model's
  // McpApprovalRequest) and have no ``always_approve`` affordance.
  async mcpApprovalResponse(requestId: string, decision: 'approve' | 'reject', rejectionMessage?: string): Promise<boolean> {
    const params: Record<string, unknown> = { request_id: requestId, decision }
    if (rejectionMessage) params.rejection_message = rejectionMessage
    return this.call('mcp_approval_response', params)
  }
}
