import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Header } from './components/Header'
import { MessageList, type MessageItem } from './components/MessageList'
import { Input } from './components/Input'
import { SessionList, type SessionItem } from './components/SessionList'
import { RPCClient } from './rpc/client'

type UIState = 'connecting' | 'resume' | 'chat' | 'error'

function wsUrlFromLocation(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.host
  const base = `${proto}://${host}/ws`
  const url = new URL(window.location.href)
  const token = url.searchParams.get('token')
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}

export function App() {
  const [ui, setUI] = useState<UIState>('connecting')
  const [connected, setConnected] = useState(false)
  const [items, setItems] = useState<MessageItem[]>([])
  const [thinking, setThinking] = useState(false)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [runId, setRunId] = useState<string | undefined>(undefined)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [_usageTotals, setUsageTotals] = useState<any | undefined>(undefined)
  const [_usageDelta, setUsageDelta] = useState<any | undefined>(undefined)
  const [_modelInfo, setModelInfo] = useState<{ model?: string; context_window?: number; max_output_tokens?: number } | undefined>(undefined)

  const client = useMemo(() => new RPCClient(wsUrlFromLocation()), [])
  const [initialSent, setInitialSent] = useState(false)

  useEffect(() => {
    client.connect()
      .then(async () => {
        setConnected(true)
        try { await client.clientFunctions(1, [{ name: 'ui.request_tool_approval' }]) } catch {}
        const url = new URL(window.location.href)
        const resume = url.searchParams.get('resume') === 'true'
        const sid = url.searchParams.get('session') || undefined
        if (resume && !sid) {
          try {
            const list = await client.listSessions()
            setSessions(list)
            setUI('resume')
          } catch {
            setUI('chat')
          }
        } else {
          if (sid) setSessionId(sid)
          setUI('chat')
        }
      })
      .catch(() => setUI('error'))

    const offMessageOutput = client.on('message_output', (p: any) => {
      const content = String(p?.content ?? '')
      if (content) setItems(prev => [...prev, { type: 'chat', role: 'assistant', content }])
    })
    const offRunStarted = client.on('run_started', (p: any) => {
      setRunId(String(p?.run_id ?? ''))
      setSessionId(String(p?.session_id ?? ''))
      setThinking(true)
    })
    const offRunEnd = client.on('run_end', (p: any) => {
      setThinking(false)
      try {
        const usage = p?.usage || {}
        const info = {
          model: p?.model,
          context_window: p?.context_window,
          max_output_tokens: p?.max_output_tokens,
        }
        setModelInfo(info)
        setUsageTotals(usage)
      } catch {}
    })
    const offRunStatus = client.on('run_status', (p: any) => {
      const msg = [p?.status, p?.message].filter(Boolean).join(': ')
      setStatus(msg)
    })
    const offToken = client.on('token', (p: any) => {
      try {
        setUsageDelta(p?.delta)
        setUsageTotals(p?.totals)
        setModelInfo({ model: p?.model, context_window: p?.context_window, max_output_tokens: p?.max_output_tokens })
      } catch {}
    })
    const offToolCalled = client.on('tool_called', (p: any) => {
      const tool = String(p?.tool ?? '')
      const input = typeof p?.input === 'string' ? p?.input : JSON.stringify(p?.input)
      const call_id = String(p?.call_id ?? '')
      setItems(prev => [...prev, { type: 'tool', tool, input, call_id, status: 'called' }])
    })
    const offToolResult = client.on('tool_result', (p: any) => {
      const tool = String(p?.tool ?? '')
      const output = typeof p?.output === 'string' ? p?.output : JSON.stringify(p?.output)
      const call_id = String(p?.call_id ?? '')
      const metadata = p?.metadata
      setItems(prev => {
        const idx = prev.findIndex(it => it.type === 'tool' && (it as any).call_id === call_id)
        if (idx >= 0) {
          const next = prev.slice()
          const it = next[idx] as any
          next[idx] = { ...it, output, status: 'result', metadata }
          return next
        }
        return [...prev, { type: 'tool', tool, output, call_id, status: 'result', metadata }]
      })
    })
    const offClientRequest = client.on('client_request', (p: any) => {
      const fn = String(p?.function ?? '')
      if (fn === 'ui.request_tool_approval') {
        const args = p?.args || {}
        const request_id = String(p?.request_id ?? '')
        const tool = String(args?.tool ?? '')
        const argumentsText = String(args?.arguments ?? '')
        const metadata = args?.metadata
        setItems(prev => [...prev, { type: 'approval', request_id, tool, argumentsText, metadata }])
      }
    })

    return () => {
      offMessageOutput(); offRunStarted(); offRunEnd(); offRunStatus(); offClientRequest(); offToken(); offToolCalled(); offToolResult()
      client.disconnect()
    }
  }, [client])

  // moved below handleSubmit

  const handleSubmit = useCallback(async (text: string) => {
    setItems(prev => [...prev, { type: 'chat', role: 'user', content: text }])
    // Slash commands
    if (text.startsWith('/')) {
      const parts = text.trim().split(/\s+/)
      const name = parts[0].slice(1)
      const argText = text.slice(parts[0].length).trim()
      try {
        const funcs = await client.listServerFunctions()
        const found = funcs.find(f => String(f.name).toLowerCase() === name.toLowerCase())
        if (!found) {
          // Not a known server function; send to LLM
          await client.startRun(text, sessionId)
          return
        }
        let args: Record<string, unknown> = {}
        if (argText) {
          try {
            const parsed = JSON.parse(argText)
            if (typeof parsed === 'object' && !Array.isArray(parsed)) args = parsed
            else if (Array.isArray(parsed)) args = { args: parsed }
            else if (typeof parsed === 'string') args = { text: parsed }
            else args = { value: parsed }
          } catch {
            args = { text: argText }
          }
        }
        const result = await client.serverCall(name, args, sessionId)
        const formatted = JSON.stringify(result, null, 2)
        setItems(prev => [...prev, { type: 'chat', role: 'assistant', content: formatted === 'null' ? 'Done.' : formatted }])
        return
      } catch (e) {
        setItems(prev => [...prev, { type: 'chat', role: 'assistant', content: `Error: ${String((e as Error)?.message || e)}` }])
        return
      }
    }
    try {
      await client.startRun(text, sessionId)
    } catch (e) {
      setStatus(String((e as Error)?.message || 'Failed to start run'))
    }
  }, [client, sessionId])

  useEffect(() => {
    if (!connected) return
    if (ui !== 'chat') return
    if (initialSent) return
    const url = new URL(window.location.href)
    const initial = url.searchParams.get('initial')
    if (initial && items.length === 0) {
      handleSubmit(initial)
      setInitialSent(true)
    }
  }, [connected, ui, initialSent, items.length, handleSubmit])

  const handleApprovalDecision = useCallback(async (request_id: string, value: 'yes' | 'always' | 'no') => {
    try {
      const approved = value !== 'no'
      const always_approve = value === 'always'
      await client.clientResponse(request_id, true, { approved, always_approve })
      setItems(prev => prev.filter(it => !(it.type === 'approval' && (it as any).request_id === request_id)))
    } catch (e) {
      await client.clientResponse(request_id, false, undefined, { message: String((e as Error)?.message || 'failed') })
      setItems(prev => prev.filter(it => !(it.type === 'approval' && (it as any).request_id === request_id)))
    }
  }, [client])

  const handleSelectSession = useCallback(async (id?: string) => {
    setSessionId(id)
    if (id) {
      try {
        const history = await client.getSessionHistory(id)
        const msgs: MessageItem[] = []
        for (const item of history) {
          const role = String(item.role) as any
          const content = typeof item.content === 'string' ? item.content : JSON.stringify(item.content)
          msgs.push({ type: 'chat', role, content, timestamp: item.timestamp })
        }
        setItems(msgs)
      } catch {}
    } else {
      setItems([])
    }
    setUI('chat')
  }, [client])

  const agentName = 'OmniAgent'

  if (ui === 'connecting') {
    return <div className="app"><Header agentName={agentName} connected={false} /><div className="container-chat px-3 py-3 text-sm text-textSubtle">Connecting…</div></div>
  }
  if (ui === 'error') {
    return <div className="app"><Header agentName={agentName} connected={false} /><div className="container-chat px-3 py-3 text-sm text-errorRed">Failed to connect.</div></div>
  }
  if (ui === 'resume') {
    return (
      <div className="app">
        <Header agentName={agentName} connected={connected} />
        <div className="container-chat">
          <SessionList sessions={sessions} onSelect={handleSelectSession} />
        </div>
      </div>
    )
  }
  return (
    <div className="app">
      <Header agentName={agentName} connected={connected} />
      <MessageList items={items} statusText={status} thinking={thinking} onApprovalDecision={handleApprovalDecision} />
      <Input disabled={!connected} thinking={thinking} onStop={() => { if (runId) client.stopRun(runId).catch(() => {}) }} onSubmit={handleSubmit} />
    </div>
  )
}
