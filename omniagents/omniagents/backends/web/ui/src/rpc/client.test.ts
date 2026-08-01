import { afterEach, describe, expect, it, vi } from 'vitest'
import { JSONRPCError, RPCClient } from './client'
import {
  ConnectionClosedError,
  RpcAbortError,
  RpcTimeoutError,
  type ConnectionState,
} from './lifecycle'

const initializeResult = {
  protocol_version: '1.0.0',
  identity: { name: 'omniagents', version: '1.2.3' },
  platform: { os: 'linux', arch: 'x86_64' },
  capabilities: {
    realtime: true,
    mcp_apps: true,
    client_functions: true,
    approvals: true,
    artifacts: true,
    replay: true,
    terminal: false,
    experimental_operations: [],
    disabled_notifications: [],
  },
}

class MockWebSocket {
  static readonly OPEN = 1
  static instances: MockWebSocket[] = []
  static initializeError: { code: number; message: string; data?: unknown } | null = null
  static failConnects = false

  readonly sent: string[] = []
  readonly readyState = MockWebSocket.OPEN
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code?: number; reason?: string }) => void) | null = null

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this)
    if (MockWebSocket.failConnects) {
      queueMicrotask(() => this.onerror?.())
    } else {
      queueMicrotask(() => this.onopen?.())
    }
  }

  send(data: string): void {
    this.sent.push(data)
    const request = JSON.parse(data) as { id?: number; method: string }
    if (request.method !== 'initialize') return
    const response = MockWebSocket.initializeError
      ? { jsonrpc: '2.0', id: request.id, error: MockWebSocket.initializeError }
      : { jsonrpc: '2.0', id: request.id, result: initializeResult }
    queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(response) }))
  }

  close(): void {}

  triggerClose(code?: number, reason?: string): void {
    this.onclose?.({ code, reason })
  }

  respond(id: number, result: unknown): void {
    this.onmessage?.({ data: JSON.stringify({ jsonrpc: '2.0', id, result }) })
  }
}

vi.stubGlobal('WebSocket', MockWebSocket)

const ticketRequests: Array<{ url: string; authorization?: string }> = []

vi.stubGlobal('fetch', async (url: string | URL, init?: RequestInit) => {
  ticketRequests.push({
    url: String(url),
    authorization: (init?.headers as Record<string, string> | undefined)?.Authorization,
  })
  return {
    ok: true,
    status: 200,
    json: async () => ({ ticket: 'one-time-ticket', expires_in: 60 }),
  } as Response
})

afterEach(() => {
  MockWebSocket.instances = []
  MockWebSocket.initializeError = null
  ticketRequests.length = 0
  MockWebSocket.failConnects = false
  vi.useRealTimers()
})

describe('RPCClient initialization', () => {
  it('negotiates capabilities and notifies initialized before becoming ready', async () => {
    const client = new RPCClient('ws://example.test/ws', 'secret')

    await client.connect()

    const socket = MockWebSocket.instances[0]!
    // The bearer token is exchanged for a one-time ticket over HTTP and
    // never appears in the WebSocket URL.
    expect(ticketRequests).toEqual([
      {
        url: 'http://example.test/auth/ws-ticket',
        authorization: 'Bearer secret',
      },
    ])
    expect(socket.url).toBe('ws://example.test/ws?ticket=one-time-ticket')
    expect(socket.url).not.toContain('secret')
    expect(socket.sent.map((message) => JSON.parse(message))).toEqual([
      {
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: {
          protocol_version: '1.0.0',
          identity: { name: 'omniagents-web-ui', version: '0.0.0' },
          platform: { os: 'browser', arch: 'unknown' },
          capabilities: {
            realtime: true,
            mcp_apps: true,
            client_functions: true,
            approvals: true,
            artifacts: true,
            replay: true,
            terminal: false,
            experimental_operations: [],
            disabled_notifications: [],
          },
        },
      },
      { jsonrpc: '2.0', method: 'initialized', params: {} },
    ])
    expect(client.initializeResult).toEqual(initializeResult)
  })

  it('preserves structured initialization errors', async () => {
    MockWebSocket.initializeError = {
      code: -32002,
      message: 'Unsupported client',
      data: { capability: 'realtime' },
    }
    const client = new RPCClient('ws://example.test/ws')

    await expect(client.connect()).rejects.toMatchObject({
      name: 'JSONRPCError',
      code: -32002,
      message: 'Unsupported client',
      data: { capability: 'realtime' },
    } satisfies Partial<JSONRPCError>)
    expect(MockWebSocket.instances[0]!.sent).toHaveLength(1)
    expect(client.initializeResult).toBeNull()
  })
})

describe('RPCClient lifecycle', () => {
  async function connectedClient(): Promise<{ client: RPCClient; socket: MockWebSocket }> {
    const client = new RPCClient('ws://example.test/ws')
    await client.connect()
    return { client, socket: MockWebSocket.instances[MockWebSocket.instances.length - 1]! }
  }

  it('rejects every pending call when the connection closes', async () => {
    const { client, socket } = await connectedClient()

    const first = client.request('server_call', { function: 'slow' })
    const second = client.request('list_sessions')
    socket.triggerClose(4403, 'auth required')

    await expect(first).rejects.toMatchObject({
      name: 'ConnectionClosedError',
      permanent: true,
      closeCode: 4403,
    })
    await expect(second).rejects.toBeInstanceOf(ConnectionClosedError)
  })

  it('does not reconnect after a permanent close code', async () => {
    vi.useFakeTimers()
    const client = new RPCClient('ws://example.test/ws')
    const connected = client.connect()
    await vi.runAllTicks()
    await connected

    const states: ConnectionState[] = []
    client.onConnectionState((state) => states.push(state))

    MockWebSocket.instances[0]!.triggerClose(4403, 'invalid token')
    await vi.advanceTimersByTimeAsync(60000)

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(states).toEqual([
      { state: 'closed', permanent: true, closeCode: 4403, reason: 'invalid token' },
    ])
  })

  it('reconnects with backoff after a retryable close and re-handshakes', async () => {
    vi.useFakeTimers()
    const client = new RPCClient('ws://example.test/ws')
    const connected = client.connect()
    await vi.runAllTicks()
    await connected

    const states: ConnectionState[] = []
    client.onConnectionState((state) => states.push(state))

    MockWebSocket.instances[0]!.triggerClose(1006)
    expect(states[0]).toMatchObject({ state: 'reconnecting', attempt: 1 })

    await vi.advanceTimersByTimeAsync(600)
    await vi.runAllTicks()

    expect(MockWebSocket.instances).toHaveLength(2)
    const replaySocket = MockWebSocket.instances[1]!
    expect(JSON.parse(replaySocket.sent[0]!)).toMatchObject({ method: 'initialize' })
    expect(JSON.parse(replaySocket.sent[1]!)).toMatchObject({ method: 'initialized' })
    expect(states[states.length - 1]).toEqual({ state: 'connected' })
  })

  it('stops retrying and reports a terminal state when the budget is exhausted', async () => {
    vi.useFakeTimers()
    const client = new RPCClient('ws://example.test/ws', undefined, {
      connectTimeoutMs: 10000,
      rpcTimeoutMs: 60000,
      pingIntervalMs: 20000,
      pingTimeoutMs: 10000,
      reconnectInitialDelayMs: 10,
      reconnectMultiplier: 2.0,
      reconnectMaxDelayMs: 50,
      reconnectJitter: 0,
      reconnectMaxAttempts: 2,
    })
    const connected = client.connect()
    await vi.runAllTicks()
    await connected

    const states: ConnectionState[] = []
    client.onConnectionState((state) => states.push(state))

    // Every reconnect attempt fails before the handshake, so the failure
    // counter is never reset and the budget (2 attempts) runs out.
    MockWebSocket.failConnects = true
    MockWebSocket.instances[0]!.triggerClose(1006)
    await vi.advanceTimersByTimeAsync(60000)

    expect(MockWebSocket.instances).toHaveLength(2)
    expect(states[0]).toMatchObject({ state: 'reconnecting', attempt: 1 })
    expect(states[states.length - 1]).toMatchObject({ state: 'closed', permanent: true })
  })

  it('times out calls using the policy deadline', async () => {
    vi.useFakeTimers()
    const client = new RPCClient('ws://example.test/ws')
    const connected = client.connect()
    await vi.runAllTicks()
    await connected

    const call = client.request('server_call', { function: 'slow' })
    call.catch(() => {})
    await vi.advanceTimersByTimeAsync(60000)

    await expect(call).rejects.toMatchObject({
      name: 'RpcTimeoutError',
      method: 'server_call',
      timeoutMs: 60000,
    } satisfies Partial<RpcTimeoutError>)
  })

  it('honors per-call timeout overrides', async () => {
    vi.useFakeTimers()
    const client = new RPCClient('ws://example.test/ws')
    const connected = client.connect()
    await vi.runAllTicks()
    await connected

    const call = client.request('server_call', { function: 'slow' }, { timeoutMs: 100 })
    call.catch(() => {})
    await vi.advanceTimersByTimeAsync(100)

    await expect(call).rejects.toBeInstanceOf(RpcTimeoutError)
  })

  it('aborts long calls via AbortSignal', async () => {
    const { client, socket } = await connectedClient()
    const controller = new AbortController()

    const call = client.request('server_call', { function: 'slow' }, { signal: controller.signal })
    controller.abort()

    await expect(call).rejects.toBeInstanceOf(RpcAbortError)
    // A late response for the aborted call must be ignored quietly.
    const request = JSON.parse(socket.sent[socket.sent.length - 1]!) as { id: number }
    expect(() => socket.respond(request.id, { ok: true })).not.toThrow()
  })

  it('preserves structured errors on regular calls', async () => {
    const { client, socket } = await connectedClient()

    const call = client.request('server_call', { function: 'x' })
    const request = JSON.parse(socket.sent[socket.sent.length - 1]!) as { id: number }
    socket.onmessage?.({
      data: JSON.stringify({
        jsonrpc: '2.0',
        id: request.id,
        error: { code: -32020, message: 'Overloaded', data: { retry_after_ms: 250 } },
      }),
    })

    await expect(call).rejects.toMatchObject({
      name: 'JSONRPCError',
      code: -32020,
      data: { retry_after_ms: 250 },
    } satisfies Partial<JSONRPCError>)
  })

  it('rejects pending calls on deliberate disconnect', async () => {
    const { client } = await connectedClient()

    const call = client.request('server_call', { function: 'slow' })
    client.disconnect()

    await expect(call).rejects.toMatchObject({ name: 'ConnectionClosedError', permanent: true })
    expect(client.connectionState).toMatchObject({ state: 'closed', permanent: true })
  })
})

describe('RPCClient typed git wrappers', () => {
  async function connectedClient(): Promise<{ client: RPCClient; socket: MockWebSocket }> {
    const client = new RPCClient('ws://example.test/ws')
    await client.connect()
    return { client, socket: MockWebSocket.instances[MockWebSocket.instances.length - 1]! }
  }

  function lastRequest(socket: MockWebSocket): { method: string; params: unknown } {
    return JSON.parse(socket.sent[socket.sent.length - 1]!) as {
      method: string
      params: unknown
    }
  }

  it('omits unset options instead of sending nulls', async () => {
    const { client, socket } = await connectedClient()

    void client.gitStatus('sess', 'apps/web')
    expect(lastRequest(socket)).toMatchObject({
      method: 'git_status',
      params: { session_id: 'sess', repo: 'apps/web' },
    })
    expect(Object.keys((lastRequest(socket).params as object) ?? {})).toEqual([
      'session_id',
      'repo',
    ])

    void client.gitDiff('sess', 'apps/web', { mode: 'staged', contextLines: 5 })
    expect(lastRequest(socket)).toMatchObject({
      method: 'git_diff',
      params: { session_id: 'sess', repo: 'apps/web', mode: 'staged', context_lines: 5 },
    })
  })

  it('sends hunk selections verbatim so ids survive the round trip', async () => {
    const { client, socket } = await connectedClient()

    void client.gitStage('sess', '.', {
      paths: ['README.md'],
      hunks: [{ path: 'src/app.ts', hunk_id: 'deadbeefdeadbeef' }],
    })

    expect(lastRequest(socket)).toEqual({
      jsonrpc: '2.0',
      id: expect.any(Number) as unknown as number,
      method: 'git_stage',
      params: {
        session_id: 'sess',
        repo: '.',
        paths: ['README.md'],
        hunks: [{ path: 'src/app.ts', hunk_id: 'deadbeefdeadbeef' }],
      },
    })
  })

  it('threads the confirmation token through destructive operations', async () => {
    const { client, socket } = await connectedClient()

    void client.gitDiscard('sess', '.', {
      hunks: [{ path: 'a.txt', hunk_id: 'abc123abc123abc1' }],
      confirmationToken: 'token-xyz',
    })
    expect(lastRequest(socket)).toMatchObject({
      method: 'git_discard',
      params: { confirmation_token: 'token-xyz' },
    })

    void client.gitPush('sess', '.', { forceWithLease: true, confirmationToken: 'token-2' })
    expect(lastRequest(socket)).toMatchObject({
      method: 'git_push',
      params: { force_with_lease: true, confirmation_token: 'token-2' },
    })
  })

  it('surfaces the typed confirmation error unchanged', async () => {
    const { client, socket } = await connectedClient()

    const pending = client.gitReset('sess', '.', { mode: 'hard' })
    const request = JSON.parse(socket.sent[socket.sent.length - 1]!) as { id: number }
    socket.onmessage?.({
      data: JSON.stringify({
        jsonrpc: '2.0',
        id: request.id,
        error: {
          code: -32093,
          message: 'reset_hard discards work and must be confirmed explicitly',
          data: {
            kind: 'git_confirmation_required',
            operation: 'reset_hard',
            confirmation_token: 'tok',
            impact: { dirty_paths: ['a.txt'] },
          },
        },
      }),
    })

    await expect(pending).rejects.toMatchObject({
      name: 'JSONRPCError',
      code: -32093,
      data: { kind: 'git_confirmation_required', confirmation_token: 'tok' },
    })
  })
})

describe('RPCClient plan & run-diff wrappers', () => {
  async function connectedClient(): Promise<{ client: RPCClient; socket: MockWebSocket }> {
    const client = new RPCClient('ws://example.test/ws')
    await client.connect()
    return { client, socket: MockWebSocket.instances[MockWebSocket.instances.length - 1]! }
  }

  function lastRequest(socket: MockWebSocket): { method: string; params: unknown } {
    return JSON.parse(socket.sent[socket.sent.length - 1]!) as {
      method: string
      params: unknown
    }
  }

  it('omits the optional scope and turn instead of sending nulls', async () => {
    const { client, socket } = await connectedClient()

    void client.getPlan('thread-1')
    expect(lastRequest(socket)).toMatchObject({
      method: 'get_plan',
      params: { thread_id: 'thread-1' },
    })
    expect(Object.keys((lastRequest(socket).params as object) ?? {})).toEqual(['thread_id'])

    void client.getRunDiff('thread-1')
    expect(Object.keys((lastRequest(socket).params as object) ?? {})).toEqual(['thread_id'])
  })

  it('addresses a worker scope and a historical turn', async () => {
    const { client, socket } = await connectedClient()

    void client.getPlan('thread-1', 'worker-3')
    expect(lastRequest(socket)).toMatchObject({
      method: 'get_plan',
      params: { thread_id: 'thread-1', scope: 'worker-3' },
    })

    void client.getRunDiff('thread-1', 'run_abc')
    expect(lastRequest(socket)).toMatchObject({
      method: 'get_run_diff',
      params: { thread_id: 'thread-1', turn_id: 'run_abc' },
    })
  })

  it('returns the structured plan straight through, with no prose parsing', async () => {
    const { client, socket } = await connectedClient()

    const pending = client.getPlan('thread-1')
    const id = (JSON.parse(socket.sent[socket.sent.length - 1]!) as { id: number }).id
    socket.respond(id, {
      thread_id: 'thread-1',
      scope: 'main',
      plan: {
        plan_id: 'itm_1',
        item_id: 'itm_1',
        thread_id: 'thread-1',
        turn_id: 'run_abc',
        scope: 'main',
        generation: 0,
        steps: [
          {
            id: '1',
            subject: 'write it',
            description: '',
            active_form: 'Writing it',
            status: 'blocked',
            owner: '',
            blocks: [],
            blocked_by: ['2'],
          },
        ],
        counts: { pending: 0, in_progress: 0, completed: 0, blocked: 1 },
        status: 'started',
        revision: 4,
        updated_at: 12.5,
      },
      plans: [],
    })

    const result = await pending
    expect(result.plan?.steps[0]?.status).toBe('blocked')
    expect(result.plan?.steps[0]?.blocked_by).toEqual(['2'])
    expect(result.plan?.counts.blocked).toBe(1)
  })

  it('carries a null run diff for a turn that changed no files', async () => {
    const { client, socket } = await connectedClient()

    const pending = client.getRunDiff('thread-1')
    const id = (JSON.parse(socket.sent[socket.sent.length - 1]!) as { id: number }).id
    socket.respond(id, { thread_id: 'thread-1', turn_id: 'run_abc', run_diff: null })

    await expect(pending).resolves.toEqual({
      thread_id: 'thread-1',
      turn_id: 'run_abc',
      run_diff: null,
    })
  })
})
