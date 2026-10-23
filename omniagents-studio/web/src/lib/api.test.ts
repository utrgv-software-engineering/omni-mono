import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, splitBaseAndToken } from './api'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
}

function jsonResponse(body: unknown, ok = true, statusText = 'OK') {
  return {
    ok,
    statusText,
    json: async () => body,
  }
}

describe('splitBaseAndToken', () => {
  it('extracts and strips a token from an absolute base URL', () => {
    const { base, token } = splitBaseAndToken('http://studio.test:7860/api?token=sekrit')
    expect(token).toBe('sekrit')
    expect(base).toBe('http://studio.test:7860/api')
    expect(base).not.toContain('sekrit')
  })

  it('extracts and strips a token from a relative base', () => {
    const { base, token } = splitBaseAndToken('/api?token=sekrit')
    expect(token).toBe('sekrit')
    expect(base).toBe('/api')
  })

  it('passes token-less bases through untouched', () => {
    expect(splitBaseAndToken('/api')).toEqual({ base: '/api', token: null })
    expect(splitBaseAndToken('http://studio.test/api')).toEqual({
      base: 'http://studio.test/api',
      token: null,
    })
  })
})

describe('ApiClient credential handling', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    FakeWebSocket.instances = []
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function makeClient(base: string): ApiClient {
    return new ApiClient(base)
  }

  it('sends the token as an Authorization header on HTTP requests, never in the URL', async () => {
    const client = makeClient('http://studio.test:7860/api?token=sekrit')
    fetchMock.mockResolvedValue(jsonResponse({ projects: [] }))

    await client.getProjects()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('http://studio.test:7860/api/projects')
    expect(String(url)).not.toContain('sekrit')
    expect(options.headers).toMatchObject({ Authorization: 'Bearer sekrit' })
  })

  it('dials WebSockets with a one-time ticket instead of the token', async () => {
    const client = makeClient('http://studio.test:7860/api?token=sekrit')
    fetchMock.mockResolvedValue(jsonResponse({ ticket: 'one-time-ticket', expires_in: 60 }))

    const ws = (await client.createSuiteRunWebSocket('job-1')) as unknown as FakeWebSocket

    // Ticket was minted over HTTP with the Authorization header.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [mintUrl, mintOptions] = fetchMock.mock.calls[0]
    expect(mintUrl).toBe('http://studio.test:7860/api/auth/ws-ticket')
    expect(mintOptions.method).toBe('POST')
    expect(mintOptions.headers).toMatchObject({ Authorization: 'Bearer sekrit' })

    // The WS URL carries the ticket, never the token.
    expect(ws.url).toBe(
      'ws://studio.test:7860/api/evaluation/suite/run/job-1/ws?ticket=one-time-ticket'
    )
    expect(ws.url).not.toContain('sekrit')
  })

  it('dials without credentials when no token is configured', async () => {
    const client = makeClient('http://studio.test:7860/api')

    const ws = (await client.createWebSocket()) as unknown as FakeWebSocket

    expect(fetchMock).not.toHaveBeenCalled()
    expect(ws.url).toBe('ws://studio.test:7860/api/ws/traces')
  })

  it('fails the dial when the ticket cannot be minted', async () => {
    const client = makeClient('http://studio.test:7860/api?token=sekrit')
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'nope' }, false, 'Unauthorized'))

    await expect(client.createScenarioRunWebSocket('job-2')).rejects.toThrow(
      /connect ticket/
    )
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('points an agent runtime at its mount and hands the token over as data, never in the URL', () => {
    const client = makeClient('http://studio.test:7860/api?token=sekrit')
    const runtime = client.agentRuntime('main agent')
    expect(runtime.baseUrl).toBe('http://studio.test:7860/api/agents/main%20agent/runtime/')
    expect(runtime.authToken).toBe('sekrit')
    expect(makeClient('/api').agentRuntime('main').baseUrl).toBe(`${window.location.origin}/api/agents/main/runtime/`)
  })
})
