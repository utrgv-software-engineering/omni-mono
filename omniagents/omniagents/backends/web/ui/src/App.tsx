import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Header } from './components/Header'
import { MessageList, type MessageItem } from './components/MessageList'
import { Input } from './components/Input'
import { SessionList, type SessionItem } from './components/SessionList'
import { Sidebar } from './components/Sidebar'
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
  const [statusSpinner, setStatusSpinner] = useState<boolean>(false)
  const [statusItalic, setStatusItalic] = useState<boolean>(false)
  const [preamble, setPreamble] = useState<string | undefined>(undefined)
  const [runId, setRunId] = useState<string | undefined>(undefined)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [_usageTotals, setUsageTotals] = useState<any | undefined>(undefined)
  const [_usageDelta, setUsageDelta] = useState<any | undefined>(undefined)
  const [_modelInfo, setModelInfo] = useState<{ model?: string; context_window?: number; max_output_tokens?: number } | undefined>(undefined)
  const [agentName, setAgentName] = useState<string>('OmniAgent')
  const [welcomeText, setWelcomeText] = useState<string | undefined>(undefined)
  const normalizeAgentName = useCallback((name: string) => {
    let s = String(name || '').trim()
    s = s.replace(/[_-]+/g, ' ')
    s = s.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    s = s.replace(/\s+/g, ' ')
    return s.split(' ').map(w => w ? (w[0].toUpperCase() + w.slice(1).toLowerCase()) : '').join(' ').trim()
  }, [])

  const client = useMemo(() => new RPCClient(wsUrlFromLocation()), [])
  const [initialSent, setInitialSent] = useState(false)
  const preambleBufferRef = useRef<Array<{ content: string; timestamp: Date; superseded: boolean }>>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const refreshSessions = useCallback(async () => {
    try {
      const list = await client.listSessions()
      setSessions(list)
    } catch {}
  }, [client])

  useEffect(() => {
    client.connect()
      .then(async () => {
        setConnected(true)
        try { await client.clientFunctions(1, [{ name: 'ui.request_tool_approval' }, { name: 'ui.set_status' }]) } catch {}
        try {
          const info = await client.getAgentInfo()
          const headerRaw = String(info?.header_title || info?.name || 'OmniAgent')
          const header = normalizeAgentName(headerRaw)
          setAgentName(header)
          const wt = info?.welcome_text ? String(info.welcome_text) : undefined
          setWelcomeText(wt)
          const titleBase = String(info?.page_title || header)
          const suffix = info?.page_title_suffix ? String(info.page_title_suffix) : ''
          document.title = suffix ? `${titleBase} ${suffix}` : titleBase
        } catch {}
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
      if (!content) return
      preambleBufferRef.current.push({ content, timestamp: new Date(), superseded: false })
      setPreamble(content)
      setStatus(undefined)
      setStatusItalic(false)
    })
    const offRunStarted = client.on('run_started', (p: any) => {
      setRunId(String(p?.run_id ?? ''))
      setSessionId(String(p?.session_id ?? ''))
      setThinking(true)
      preambleBufferRef.current = []
      setPreamble(undefined)
      setStatus(undefined)
      setStatusSpinner(false)
      setStatusItalic(false)
      refreshSessions()
    })
    const offRunEnd = client.on('run_end', (p: any) => {
      setThinking(false)
      const nonSuperseded = preambleBufferRef.current.filter(m => !m.superseded)
      if (nonSuperseded.length) {
        setItems(prev => [
          ...prev,
          ...nonSuperseded.map(m => ({ type: 'chat', role: 'assistant', content: m.content } as MessageItem)),
        ])
      }
      preambleBufferRef.current = []
      setPreamble(undefined)
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
      setStatusSpinner(false)
      setStatusItalic(false)
      refreshSessions()
    })
    const offRunStatus = client.on('run_status', (p: any) => {
      const msg = [p?.status, p?.message].filter(Boolean).join(': ')
      setStatus(msg)
      setStatusSpinner(true)
      setStatusItalic(false)
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
      preambleBufferRef.current.forEach(m => { m.superseded = true })
      setItems(prev => [...prev, { type: 'tool', tool, input, call_id, status: 'called' }])
    })
    const offToolResult = client.on('tool_result', (p: any) => {
      const tool = String(p?.tool ?? '')
      const output = typeof p?.output === 'string' ? p?.output : JSON.stringify(p?.output)
      const call_id = String(p?.call_id ?? '')
      const metadata = p?.metadata
      preambleBufferRef.current.forEach(m => { m.superseded = true })
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
        setThinking(false)
        setItems(prev => [...prev, { type: 'approval', request_id, tool, argumentsText, metadata }])
        return
      }
      if (fn === 'ui.set_status') {
        const request_id = String(p?.request_id ?? '')
        const args = p?.args || {}
        const text = typeof args?.text === 'string' ? args.text : undefined
        const showSpinner = typeof args?.show_spinner === 'boolean' ? !!args.show_spinner : true
        setStatus(text)
        setStatusSpinner(showSpinner)
        setStatusItalic(false)
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
    })

    return () => {
      offMessageOutput(); offRunStarted(); offRunEnd(); offRunStatus(); offClientRequest(); offToken(); offToolCalled(); offToolResult()
      client.disconnect()
    }
  }, [client, normalizeAgentName, refreshSessions])

  // moved below handleSubmit

  const handleSubmit = useCallback(async (text: string, files?: File[]) => {
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
      let content: any | undefined = undefined
      let attachments: Array<{ type: 'image' | 'file'; url?: string; filename?: string; mime?: string; size?: number }> = []
      if (files && files.length > 0) {
        const parts: any[] = []
        if (text.trim().length > 0) parts.push({ type: 'input_text', text })
        const processed = await Promise.all(files.map(async (f) => {
          if (f.type && f.type.startsWith('image/')) {
            const dataUrl = await new Promise<string>((resolve, reject) => {
              const reader = new FileReader()
              reader.onload = () => resolve(String(reader.result || ''))
              reader.onerror = () => reject(new Error('Failed to read image'))
              reader.readAsDataURL(f)
            })
            return {
              filePart: { type: 'input_image', image_url: dataUrl, detail: 'auto' },
              attachment: { type: 'image', url: dataUrl, filename: f.name, mime: f.type, size: f.size },
            }
          } else {
            const base64 = await new Promise<string>((resolve, reject) => {
              const reader = new FileReader()
              reader.onload = () => {
                try {
                  const buf = reader.result as ArrayBuffer
                  const bytes = new Uint8Array(buf)
                  let binary = ''
                  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
                  resolve(btoa(binary))
                } catch {
                  reject(new Error('Failed to encode file'))
                }
              }
              reader.onerror = () => reject(new Error('Failed to read file'))
              reader.readAsArrayBuffer(f)
            })
            const param: any = { type: 'input_file', file_data: base64 }
            if (f.name) param.filename = f.name
            return {
              filePart: param,
              attachment: { type: 'file', filename: f.name, mime: f.type, size: f.size },
            }
          }
        }))
        parts.push(...processed.map(p => p.filePart))
        attachments = processed.map(p => p.attachment)
        content = parts
      }
      setItems(prev => [...prev, { type: 'chat', role: 'user', content: text, attachments }])
      await client.startRun(text, sessionId, undefined, content)
    } catch (e) {
      setStatus(String((e as Error)?.message || 'Failed to start run'))
      setStatusItalic(false)
    }
  }, [client, sessionId])

  const handleStop = useCallback(() => {
    if (!runId) return
    try {
      preambleBufferRef.current.forEach(m => { m.superseded = true })
    } catch {}
    setThinking(false)
    setStatusSpinner(false)
    setStatusItalic(true)
    setStatus('Cancelled by user')
    setPreamble(undefined)
    client.stopRun(runId).catch(() => {})
  }, [client, runId])

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
      setThinking(true)
    } catch (e) {
      await client.clientResponse(request_id, false, undefined, { message: String((e as Error)?.message || 'failed') })
      setItems(prev => prev.filter(it => !(it.type === 'approval' && (it as any).request_id === request_id)))
      setThinking(true)
    }
  }, [client])

  const handleSelectSession = useCallback(async (id?: string) => {
    setSessionId(id)
    setPreamble(undefined)
    preambleBufferRef.current = []
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

  useEffect(() => {
    if (!connected) return
    refreshSessions()
  }, [connected, refreshSessions])

  const onNewChat = useCallback(() => {
    handleSelectSession(undefined)
  }, [handleSelectSession])

  if (ui === 'connecting') {
    return <div className="app flex-col"><Header agentName={agentName} connected={false} /><div className="container-chat px-3 py-3 text-sm text-textSubtle">Connecting…</div></div>
  }
  if (ui === 'error') {
    return <div className="app flex-col"><Header agentName={agentName} connected={false} /><div className="container-chat px-3 py-3 text-sm text-errorRed">Failed to connect.</div></div>
  }
  if (ui === 'resume') {
    return (
      <div className="app flex-col">
        <Header agentName={agentName} connected={connected} />
        <div className="container-chat">
          <SessionList sessions={sessions} onSelect={handleSelectSession} />
        </div>
      </div>
    )
  }
  return (
    <div className="app h-full flex flex-row">
      <Sidebar
        open={sidebarOpen}
        sessions={sessions}
        selectedId={sessionId}
        onClose={() => setSidebarOpen(false)}
        onNewChat={onNewChat}
        onSelect={(id) => handleSelectSession(id)}
      />
      <div className="flex-1 flex flex-col min-h-0">
        <Header agentName={agentName} connected={connected} onMenu={() => setSidebarOpen(v => !v)} />
        <div className="flex-1 flex flex-col min-h-0">
          <MessageList items={items} statusText={status} thinking={thinking} statusSpinner={statusSpinner} preambleText={preamble} welcomeText={welcomeText} onApprovalDecision={handleApprovalDecision} statusItalic={statusItalic} />
          <Input disabled={!connected} thinking={thinking} onStop={handleStop} onSubmit={handleSubmit} />
        </div>
      </div>
    </div>
  )
}
