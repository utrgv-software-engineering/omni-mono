import { afterEach, describe, expect, it, vi } from 'vitest'
import { JSONRPCError, RPCClient } from './client'

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

  readonly sent: string[] = []
  readonly readyState = MockWebSocket.OPEN
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
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
}

vi.stubGlobal('WebSocket', MockWebSocket)

afterEach(() => {
  MockWebSocket.instances = []
  MockWebSocket.initializeError = null
})

describe('RPCClient initialization', () => {
  it('negotiates capabilities and notifies initialized before becoming ready', async () => {
    const client = new RPCClient('ws://example.test/ws', 'secret')

    await client.connect()

    const socket = MockWebSocket.instances[0]!
    expect(socket.url).toBe('ws://example.test/ws?token=secret')
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
