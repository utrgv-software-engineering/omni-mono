import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Header } from './components/Header'
import { MessageList, type MessageItem } from './components/MessageList'
import { Input } from './components/Input'
import { Tasks, type TaskSummary } from './components/Tasks'
import {
  BashJobs,
  type BashJobSummary,
  type BashJobsKillResult,
  type BashJobsTailResult,
} from './components/BashJobs'
import { SessionList, type SessionItem } from './components/SessionList'
import { Sidebar } from './components/Sidebar'
import { ArtifactsPanel, type ArtifactItem } from './components/ArtifactsPanel'
import { ResizableDivider } from './components/ResizableDivider'
import { TerminalPanel } from './components/TerminalPanel'
import { WorkspacePicker } from './components/WorkspacePicker'
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
  const [voiceEnabled, setVoiceEnabled] = useState(false)
  const [items, setItems] = useState<MessageItem[]>([])
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [bashJobs, setBashJobs] = useState<BashJobSummary[]>([])
  const [thinking, setThinking] = useState(false)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [statusSpinner, setStatusSpinner] = useState<boolean>(false)
  const [statusItalic, setStatusItalic] = useState<boolean>(false)
  const [preamble, setPreamble] = useState<string | undefined>(undefined)
  const [toolStatus, setToolStatus] = useState<string | undefined>(undefined)
  const [runId, setRunId] = useState<string | undefined>(undefined)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [_usageTotals, setUsageTotals] = useState<any | undefined>(undefined)
  const [_usageDelta, setUsageDelta] = useState<any | undefined>(undefined)
  const [_modelInfo, setModelInfo] = useState<{ model?: string; max_input_tokens?: number; max_output_tokens?: number } | undefined>(undefined)
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
  const pendingApprovalsRef = useRef(new Map<string, (MessageItem & { session_id?: string })>())
  const pendingMcpApprovalsRef = useRef(new Map<string, (MessageItem & { session_id?: string })>())
  const urlSessionHandledRef = useRef(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [artifactsPanelOpen, setArtifactsPanelOpen] = useState(false)
  const [terminalPanelOpen, setTerminalPanelOpen] = useState(false)
  const [artifactsPanelWidth, setArtifactsPanelWidth] = useState(() => {
    try {
      const stored = localStorage.getItem('artifacts-panel-width')
      return stored ? parseInt(stored, 10) : 480
    } catch {
      return 480
    }
  })
  const [isLargeScreen, setIsLargeScreen] = useState(() => window.innerWidth >= 1024)
  const [minimalMode] = useState(() => {
    try {
      const url = new URL(window.location.href)
      return url.searchParams.get('minimal') === 'true'
    } catch {
      return false
    }
  })
  const [workspaceSupported, setWorkspaceSupported] = useState(false)
  const [workspacePath, setWorkspacePath] = useState<string | null>(null)
  const [workspaceLocked, setWorkspaceLocked] = useState(false)
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false)
  const [initialSessionParam] = useState<string | undefined>(() => {
    try {
      const url = new URL(window.location.href)
      const value = url.searchParams.get('session')
      return value ? value : undefined
    } catch {
      return undefined
    }
  })
  const sessionIdRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])
  const runIdRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    runIdRef.current = runId
  }, [runId])
  const startingRunRef = useRef(false)
  const appendPendingApprovals = useCallback((base: MessageItem[], targetSession?: string) => {
    if (!targetSession) return base
    const additions: MessageItem[] = []
    for (const item of pendingApprovalsRef.current.values()) {
      const itemSession = (item as any).session_id
      if (itemSession && itemSession !== targetSession) continue
      const exists = base.some(existing => existing.type === 'approval' && (existing as any).call_id === (item as any).call_id)
      if (!exists) additions.push(item)
    }
    for (const item of pendingMcpApprovalsRef.current.values()) {
      const itemSession = (item as any).session_id
      if (itemSession && itemSession !== targetSession) continue
      const exists = base.some(existing => existing.type === 'mcp_approval' && (existing as any).request_id === (item as any).request_id)
      if (!exists) additions.push(item)
    }
    return additions.length ? [...base, ...additions] : base
  }, [])

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
        try { await client.clientFunctions(1, [{ name: 'ui.set_status' }, { name: 'ui.add_artifact' }]) } catch {}
        try {
          const info = await client.getAgentInfo()
          const agentName = normalizeAgentName(String(info?.name || 'OmniAgent'))
          setAgentName(agentName)
          const wt = info?.welcome_text ? String(info.welcome_text) : undefined
          setWelcomeText(wt)
          document.title = agentName
        } catch {}
        try {
          const funcs = await client.listServerFunctions()
          const names = new Set(funcs.map(f => f.name))
          if (names.has('fs_list_dir') && names.has('fs_get_workspace_root')) {
            setWorkspaceSupported(true)
            // Use fs_get_workspace_root, not fs_get_cwd: for sandboxed
            // agents the manifest root is what the agent's tools see;
            // omni serve's host cwd may differ.
            try {
              const res = await client.serverCall('fs_get_workspace_root') as any
              if (res?.path) setWorkspacePath(res.path)
            } catch {}
          }
        } catch {}
        try {
          // Probe via capabilities() not startSession(): the latter creates an empty trace per boot.
          const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
          const host = window.location.host
          const base = `${proto}://${host}/ws/realtime`
          const url = new URL(window.location.href)
          const token = url.searchParams.get('token') || undefined
          const { RealtimeRPCClient } = await import('./rpc/realtime')
          const rtc = new RealtimeRPCClient(base, token)
          await rtc.connect()
          try {
            const caps = await rtc.capabilities()
            setVoiceEnabled(!!caps?.enabled)
          } finally { rtc.disconnect() }
        } catch { setVoiceEnabled(false) }
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
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId) {
        if (sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
        if (!sessionIdRef.current && !startingRunRef.current) return
      }
      const content = String(p?.content ?? '')
      if (!content) return
      preambleBufferRef.current.push({ content, timestamp: new Date(), superseded: false })
      setPreamble(content)
      setStatus(undefined)
      setStatusItalic(false)
    })
    const offRunStarted = client.on('run_started', (p: any) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId) {
        if (sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
        if (!sessionIdRef.current && !startingRunRef.current) return
      }
      startingRunRef.current = false
      setRunId(String(p?.run_id ?? ''))
      if (eventSessionId) setSessionId(eventSessionId)
      setThinking(true)
      preambleBufferRef.current = []
      setPreamble(undefined)
      setStatus(undefined)
      setToolStatus(undefined)
      setStatusSpinner(false)
      setStatusItalic(false)
      refreshSessions()
    })
    const offRunEnd = client.on('run_end', (p: any) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
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
      setToolStatus(undefined)
      try {
        const usage = p?.usage || {}
        const info = {
          model: p?.model,
          max_input_tokens: p?.max_input_tokens,
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
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
      const msg = [p?.status, p?.message].filter(Boolean).join(': ')
      setStatus(msg)
      setStatusSpinner(true)
      setStatusItalic(false)
    })
    const offToken = client.on('token', (p: any) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
      try {
        setUsageDelta(p?.delta)
        setUsageTotals(p?.totals)
        setModelInfo({ model: p?.model, max_input_tokens: p?.max_input_tokens, max_output_tokens: p?.max_output_tokens })
      } catch {}
    })
    const offToolCalled = client.on('tool_called', (p: any) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId) {
        if (sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
        if (!sessionIdRef.current && !startingRunRef.current) return
      }
      const tool = String(p?.tool ?? '')
      const input = typeof p?.input === 'string' ? p?.input : JSON.stringify(p?.input)
      const call_id = String(p?.call_id ?? '')
      preambleBufferRef.current.forEach(m => { m.superseded = true })
      setItems(prev => [...prev, { type: 'tool', tool, input, call_id, status: 'called', runId: runIdRef.current }])
    })
    const offToolResult = client.on('tool_result', (p: any) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId) {
        if (sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
        if (!sessionIdRef.current && !startingRunRef.current) return
      }
      const tool = String(p?.tool ?? '')
      const output = typeof p?.output === 'string' ? p?.output : JSON.stringify(p?.output)
      const call_id = String(p?.call_id ?? '')
      const metadata = p?.metadata
      preambleBufferRef.current.forEach(m => { m.superseded = true })
      // Side-channel: any tool can attach a tasks_snapshot to its
      // ui_metadata to refresh the docked task panel above the input.
      // Today only the task_* tools do.
      const snapshot = metadata?.tasks_snapshot
      if (Array.isArray(snapshot)) {
        setTasks(snapshot as TaskSummary[])
      }
      // Same pattern for background bash jobs: bash tools attach a
      // bash_jobs_snapshot so the docked Bash jobs panel stays in sync.
      const jobsSnapshot = metadata?.bash_jobs_snapshot
      if (Array.isArray(jobsSnapshot)) {
        setBashJobs(jobsSnapshot as BashJobSummary[])
      }
      setItems(prev => {
        const idx = prev.findIndex(it => it.type === 'tool' && (it as any).call_id === call_id)
        if (idx >= 0) {
          const next = prev.slice()
          const it = next[idx] as any
          next[idx] = { ...it, output, status: 'result', metadata }
          return next
        }
        return [...prev, { type: 'tool', tool, output, call_id, status: 'result', metadata, runId: runIdRef.current }]
      })
    })
    const offClientRequest = client.on('client_request', (p: any) => {
      const fn = String(p?.function ?? '')
      if (fn === 'ui.add_artifact') {
        const request_id = String(p?.request_id ?? '')
        const args = p?.args || {}
        const title = typeof args?.title === 'string' ? args.title : ''
        const content = typeof args?.content === 'string' ? args.content : ''
        const mode = typeof args?.mode === 'string' ? args.mode : 'markdown'
        const artifact_id = typeof args?.artifact_id === 'string' ? args.artifact_id : undefined
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        const updated_at = Date.now()
        setArtifacts(prev => {
          const next = prev.slice()
          const idx = artifact_id ? next.findIndex(a => a.artifact_id === artifact_id && (!eventSessionId || a.session_id === eventSessionId)) : -1
          const entry: ArtifactItem = { title, content, mode, artifact_id, session_id: eventSessionId, updated_at }
          if (idx >= 0) next[idx] = entry
          else next.push(entry)
          return next
        })
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'ui.bash_jobs.update') {
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args = p?.args || {}
        const snap = args?.snapshot
        if (Array.isArray(snap)) {
          setBashJobs(snap as BashJobSummary[])
        }
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'ui.set_status') {
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args = p?.args || {}
        const text = typeof args?.text === 'string' ? args.text : undefined
        const showSpinner = typeof args?.show_spinner === 'boolean' ? !!args.show_spinner : true
        setStatus(text)
        setStatusSpinner(showSpinner)
        setStatusItalic(false)
        if (text && text.trim()) setToolStatus(text)
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
    })

    const offClientRequestResolved = client.on('client_request_resolved', () => {
      // No-op: tool approvals no longer flow through client_request, so a
      // client_request resolution carries no approval state for us to
      // discard. Kept subscribed for future use / generic UI requests.
    })

    const offToolApprovalRequested = client.on('tool_approval_requested', (p: any) => {
      const call_id = String(p?.call_id ?? '')
      const tool = String(p?.tool_name ?? '')
      const argumentsText = String(p?.arguments ?? '')
      const metadata = p?.metadata
      setThinking(false)
      if (!call_id) return
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      const approvalItem: MessageItem & { session_id?: string } = { type: 'approval', call_id, tool, argumentsText, metadata, session_id: eventSessionId }
      pendingApprovalsRef.current.set(call_id, approvalItem)
      const shouldDisplay = !eventSessionId || !sessionIdRef.current || sessionIdRef.current === eventSessionId
      if (shouldDisplay) {
        setItems(prev => {
          const filtered = prev.filter(it => !(it.type === 'approval' && (it as any).call_id === call_id))
          return [...filtered, approvalItem]
        })
      }
    })

    const offToolApprovalResolved = client.on('tool_approval_resolved', (p: any) => {
      const call_id = String(p?.call_id ?? '')
      if (call_id) {
        pendingApprovalsRef.current.delete(call_id)
        setItems(prev => prev.filter(it => !(it.type === 'approval' && (it as any).call_id === call_id)))
      }
    })

    // Hosted-MCP approval flow — parallel to tool_approval but keyed by
    // request_id (minted by the model on the McpApprovalRequest) rather
    // than call_id, and identifies the MCP server via server_label.
    const offMcpApprovalRequested = client.on('mcp_approval_requested', (p: any) => {
      const request_id = String(p?.request_id ?? '')
      const server_label = String(p?.server_label ?? '')
      const tool = String(p?.tool_name ?? '')
      const argumentsText = String(p?.arguments ?? '')
      setThinking(false)
      if (!request_id) return
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      const approvalItem: MessageItem & { session_id?: string } = { type: 'mcp_approval', request_id, server_label, tool, argumentsText, session_id: eventSessionId }
      pendingMcpApprovalsRef.current.set(request_id, approvalItem)
      const shouldDisplay = !eventSessionId || !sessionIdRef.current || sessionIdRef.current === eventSessionId
      if (shouldDisplay) {
        setItems(prev => {
          const filtered = prev.filter(it => !(it.type === 'mcp_approval' && (it as any).request_id === request_id))
          return [...filtered, approvalItem]
        })
      }
    })

    const offMcpApprovalResolved = client.on('mcp_approval_resolved', (p: any) => {
      const request_id = String(p?.request_id ?? '')
      if (request_id) {
        pendingMcpApprovalsRef.current.delete(request_id)
        setItems(prev => prev.filter(it => !(it.type === 'mcp_approval' && (it as any).request_id === request_id)))
      }
    })

    return () => {
      offMessageOutput(); offRunStarted(); offRunEnd(); offRunStatus(); offClientRequest(); offClientRequestResolved(); offToolApprovalRequested(); offToolApprovalResolved(); offMcpApprovalRequested(); offMcpApprovalResolved(); offToken(); offToolCalled(); offToolResult()
      client.disconnect()
    }
  }, [client, normalizeAgentName, refreshSessions])

  const visibleArtifacts = useMemo(() => {
    if (!sessionId) return artifacts.filter(a => !a.session_id)
    return artifacts.filter(a => !a.session_id || a.session_id === sessionId)
  }, [artifacts, sessionId])

  useEffect(() => {
    const handler = () => setIsLargeScreen(window.innerWidth >= 1024)
    window.addEventListener('resize', handler)
    return () => window.removeEventListener('resize', handler)
  }, [])

  useEffect(() => {
    try { localStorage.setItem('artifacts-panel-width', String(artifactsPanelWidth)) } catch {}
  }, [artifactsPanelWidth])

  useEffect(() => {
    if (visibleArtifacts.length > 0 && !artifactsPanelOpen) {
      setArtifactsPanelOpen(true)
    }
  }, [visibleArtifacts.length])

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
      startingRunRef.current = true
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
      // Always pass workspace_root as variables so the context factory picks it up
      const variables = (workspacePath && workspaceSupported)
        ? { workspace_root: workspacePath }
        : undefined
      await client.startRun(text, sessionId, variables, content)
      if (workspaceSupported) setWorkspaceLocked(true)
    } catch (e) {
      startingRunRef.current = false
      setStatus(String((e as Error)?.message || 'Failed to start run'))
      setStatusItalic(false)
    }
  }, [client, sessionId, workspacePath, workspaceSupported])

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
    // Only send initial message if there's no session param (session param is handled separately)
    const url = new URL(window.location.href)
    const hasSessionParam = url.searchParams.has('session')
    if (hasSessionParam) return
    const initial = url.searchParams.get('initial')
    if (initial && items.length === 0) {
      handleSubmit(initial)
      setInitialSent(true)
    }
  }, [connected, ui, initialSent, items.length, handleSubmit])

  const handleApprovalDecision = useCallback(async (call_id: string, value: 'yes' | 'always' | 'no') => {
    const removeApproval = () => {
      pendingApprovalsRef.current.delete(call_id)
      setItems(prev => prev.filter(it => !(it.type === 'approval' && (it as any).call_id === call_id)))
    }
    try {
      const decision = value === 'no' ? 'reject' : 'approve'
      const alwaysApprove = value === 'always'
      await client.toolApprovalResponse(call_id, decision, alwaysApprove)
      removeApproval()
      setThinking(true)
    } catch (e) {
      await client.toolApprovalResponse(call_id, 'reject', false, String((e as Error)?.message || 'failed'))
      removeApproval()
      setThinking(true)
    }
  }, [client])

  const handleMcpApprovalDecision = useCallback(async (request_id: string, value: 'yes' | 'no') => {
    const removeApproval = () => {
      pendingMcpApprovalsRef.current.delete(request_id)
      setItems(prev => prev.filter(it => !(it.type === 'mcp_approval' && (it as any).request_id === request_id)))
    }
    try {
      const decision = value === 'no' ? 'reject' : 'approve'
      await client.mcpApprovalResponse(request_id, decision)
      removeApproval()
      setThinking(true)
    } catch (e) {
      await client.mcpApprovalResponse(request_id, 'reject', String((e as Error)?.message || 'failed'))
      removeApproval()
      setThinking(true)
    }
  }, [client])

  const handleSelectSession = useCallback(async (id?: string) => {
    setSessionId(id)
    setPreamble(undefined)
    preambleBufferRef.current = []
    // Reset UI state to prevent stale state from previous conversation
    setThinking(false)
    setStatus(undefined)
    setStatusSpinner(false)
    setStatusItalic(false)
    setRunId(undefined)
    // Reset or restore workspace state
    if (!id) {
      // New chat — unlock workspace, restore default (manifest root,
      // not host cwd, so sandboxed agents show the path their tools
      // actually operate on).
      setWorkspaceLocked(false)
      if (workspaceSupported) {
        try {
          const res = await client.serverCall('fs_get_workspace_root') as any
          if (res?.path) setWorkspacePath(res.path)
        } catch {}
      }
    }
    if (id) {
      try {
        const history = await client.getSessionHistory(id)
        const msgs: MessageItem[] = []
        const callIndex: Record<string, number> = {}
        for (const item of history as any[]) {
          const t = String((item && item.type) || '')

          if (t && t.endsWith('_call') && !t.endsWith('_call_output')) {
            const tool = String(item.name || '')
            let input = ''
            try {
              if (typeof item.arguments === 'string') input = item.arguments
              else if (item.arguments != null) input = JSON.stringify(item.arguments)
            } catch { input = String(item.arguments || '') }
            const call_id = String(item.call_id || '')
            const idx = msgs.length
            msgs.push({ type: 'tool', tool, input, call_id, status: 'called' })
            if (call_id) callIndex[call_id] = idx
            continue
          }

          if (t && t.endsWith('_call_output')) {
            const call_id = String(item.call_id || '')
            const tool = String(item.name || '')
            let output = ''
            try { output = typeof item.output === 'string' ? item.output : JSON.stringify(item.output) }
            catch { output = String(item.output || '') }
            const metadata = item.metadata
            const existing = (call_id && callIndex[call_id] != null) ? callIndex[call_id] : -1
            if (existing >= 0) {
              const prev = msgs[existing] as any
              msgs[existing] = { ...prev, output, status: 'result', metadata }
            } else {
              msgs.push({ type: 'tool', tool, output, call_id, status: 'result', metadata })
            }
            continue
          }

          if (item && item.role) {
            const role = String(item.role) as any
            let content = ''
            let attachments: Array<{ type: 'image' | 'file'; url?: string; filename?: string; mime?: string; size?: number }> = []

            if (typeof item.content === 'string') {
              content = item.content
            } else if (Array.isArray(item.content)) {
              const parts = item.content as any[]
              if (role === 'assistant') {
                const textParts = parts
                  .filter(p => p && (p.type === 'output_text' || p.type === 'text'))
                  .map(p => String(p.text || ''))
                content = textParts.join('\n')
              } else {
                const textParts = parts
                  .filter(p => p && (p.type === 'input_text' || p.type === 'text'))
                  .map(p => String(p.text || p.input_text || ''))
                content = textParts.join('\n')
                for (const p of parts) {
                  if (p && p.type === 'input_image' && p.image_url) {
                    attachments.push({ type: 'image', url: String(p.image_url), filename: p.filename })
                  } else if (p && p.type === 'input_file') {
                    attachments.push({ type: 'file', filename: p.filename })
                  }
                }
              }
            } else if (item.content && typeof item.content === 'object') {
              const obj = item.content as any
              content = String(obj.text || obj.input_text || '')
              if (!content) {
                try { content = JSON.stringify(item.content) } catch { content = String(item.content) }
              }
            }

            msgs.push({ type: 'chat', role, content, timestamp: item.timestamp, attachments })
            continue
          }
        }
        const merged = appendPendingApprovals(msgs, id)
        setItems(merged)
        // Replay the most recent tasks_snapshot from history so the
        // docked panel reflects the session state immediately.
        let lastSnapshot: TaskSummary[] | null = null
        let lastJobsSnapshot: BashJobSummary[] | null = null
        for (const m of msgs) {
          if (m.type === 'tool') {
            const snap = (m as any).metadata?.tasks_snapshot
            if (Array.isArray(snap)) lastSnapshot = snap as TaskSummary[]
            const jobsSnap = (m as any).metadata?.bash_jobs_snapshot
            if (Array.isArray(jobsSnap)) lastJobsSnapshot = jobsSnap as BashJobSummary[]
          }
        }
        setTasks(lastSnapshot ?? [])
        setBashJobs(lastJobsSnapshot ?? [])
        // Restore workspace from session and lock it
        if (workspaceSupported) {
          try {
            const res = await client.serverCall('fs_get_workspace_root', {}, id) as any
            if (res?.path) setWorkspacePath(res.path)
          } catch {}
          setWorkspaceLocked(true)
        }
      } catch {}
    } else {
      setItems([])
      setTasks([])
      setBashJobs([])
    }
    setUI('chat')
  }, [client, appendPendingApprovals, workspaceSupported])

  useEffect(() => {
    if (urlSessionHandledRef.current) return
    if (!initialSessionParam) return
    if (!connected) return
    if (ui !== 'chat') return
    urlSessionHandledRef.current = true
    // Load session history, then send initial message only if session is empty
    ;(async () => {
      await handleSelectSession(initialSessionParam)
      const url = new URL(window.location.href)
      const initial = url.searchParams.get('initial')
      if (initial && !initialSent) {
        // Check if session has history - if items is still empty after handleSelectSession, it's a new session
        // We need to check the actual history since handleSelectSession sets items
        try {
          const history = await client.getSessionHistory(initialSessionParam)
          if (history.length === 0) {
            handleSubmit(initial)
            setInitialSent(true)
          }
        } catch {
          // If we can't get history, assume it's new and send initial
          handleSubmit(initial)
          setInitialSent(true)
        }
      }
    })()
  }, [connected, ui, handleSelectSession, initialSessionParam, client, initialSent, handleSubmit])

  useEffect(() => {
    if (!connected) return
    refreshSessions()
  }, [connected, refreshSessions])

  const onNewChat = useCallback(() => {
    handleSelectSession(undefined)
  }, [handleSelectSession])

  const handleDeleteSession = useCallback(async (id: string) => {
    try {
      await client.deleteSession(id)
      setSessions(prev => prev.filter(s => s.id !== id))
      if (sessionId === id) {
        handleSelectSession(undefined)
      }
    } catch (e) {
      console.error('Failed to delete session:', e)
    }
  }, [client, sessionId, handleSelectSession])

  const handleReaction = useCallback(async (type: 'like' | 'dislike', text?: string) => {
    try {
      const func = type === 'like' ? 'good' : 'bad'
      const args: Record<string, unknown> = {}
      if (text) args.text = text
      await client.serverCall(func, args, sessionId)
    } catch {}
  }, [client, sessionId])

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
  const hasArtifacts = visibleArtifacts.length > 0

  return (
    <div className="app h-full flex flex-row min-w-0">
      {!minimalMode && (
        <Sidebar
          open={sidebarOpen}
          sessions={sessions}
          selectedId={sessionId}
          onClose={() => setSidebarOpen(false)}
          onNewChat={onNewChat}
          onSelect={(id) => handleSelectSession(id)}
          onDelete={handleDeleteSession}
        />
      )}
      <div className="flex-1 flex flex-col min-h-0 min-w-0">
        {!minimalMode && (
          <Header
            agentName={agentName}
            connected={connected}
            onMenu={() => setSidebarOpen(v => !v)}
            onArtifactsToggle={hasArtifacts ? () => setArtifactsPanelOpen(v => !v) : undefined}
            onTerminalToggle={() => setTerminalPanelOpen(v => !v)}
            showArtifactsButton={hasArtifacts}
            showTerminalButton
          />
        )}
        <div className="flex-1 flex flex-row min-h-0 min-w-0">
          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            <MessageList items={items} statusText={status} thinking={thinking} statusSpinner={statusSpinner} preambleText={preamble} welcomeText={welcomeText} onApprovalDecision={handleApprovalDecision} onMcpApprovalDecision={handleMcpApprovalDecision} statusItalic={statusItalic} onReaction={handleReaction} currentRunId={runId} toolStatusText={toolStatus} />
            <Tasks tasks={thinking ? tasks : tasks.filter(t => t.status !== 'completed')} />
            <BashJobs
              jobs={thinking ? bashJobs : bashJobs.filter(j => j.running)}
              onKill={async (job_id) => {
                const res = (await client.serverCall('bash_jobs.kill', { job_id }, sessionId)) as unknown as BashJobsKillResult
                if (Array.isArray(res?.snapshot)) setBashJobs(res.snapshot as BashJobSummary[])
                return res
              }}
              onTail={async (job_id, lines) => {
                const args: Record<string, unknown> = { job_id }
                if (typeof lines === 'number') args.lines = lines
                const res = (await client.serverCall('bash_jobs.tail', args, sessionId)) as unknown as BashJobsTailResult & { snapshot?: BashJobSummary[] }
                if (Array.isArray(res?.snapshot)) setBashJobs(res.snapshot as BashJobSummary[])
                return res
              }}
              onWarmup={async () => {
                const res = (await client.serverCall('bash_jobs.list', {}, sessionId)) as unknown as { snapshot?: BashJobSummary[] }
                if (Array.isArray(res?.snapshot)) setBashJobs(res.snapshot as BashJobSummary[])
                return res
              }}
            />
            <Input
              disabled={!connected}
              thinking={thinking}
              onStop={handleStop}
              onSubmit={handleSubmit}
              voiceEnabled={voiceEnabled}
              workspacePath={workspaceSupported ? workspacePath : undefined}
              workspaceLocked={workspaceLocked}
              onWorkspaceClick={() => setWorkspacePickerOpen(true)}
              sessionId={sessionId}
              onVoiceSessionCreated={(id: string) => setSessionId(id)}
              onVoiceClose={() => { if (sessionIdRef.current) handleSelectSession(sessionIdRef.current); refreshSessions(); }}
            />
          </div>
          {isLargeScreen && artifactsPanelOpen && hasArtifacts && (
            <>
              <ResizableDivider
                onResize={setArtifactsPanelWidth}
                currentWidth={artifactsPanelWidth}
                minWidth={320}
                maxWidth={800}
              />
              <div
                className="flex-shrink-0 min-h-0 border-l border-bgCardAlt"
                style={{ width: artifactsPanelWidth }}
              >
                <ArtifactsPanel
                  artifacts={visibleArtifacts}
                  onClose={() => setArtifactsPanelOpen(false)}
                />
              </div>
            </>
          )}
        </div>
      </div>
      {!isLargeScreen && artifactsPanelOpen && hasArtifacts && (
        <ArtifactsPanel
          artifacts={visibleArtifacts}
          onClose={() => setArtifactsPanelOpen(false)}
          asOverlay
        />
      )}
      {!minimalMode && (
        <TerminalPanel
          open={terminalPanelOpen}
          client={client}
          sessionId={sessionId}
          onSessionId={setSessionId}
          onClose={() => setTerminalPanelOpen(false)}
        />
      )}
      {workspacePickerOpen && (
        <WorkspacePicker
          client={client}
          sessionId={sessionId}
          initialPath={workspacePath || undefined}
          onSelect={(path) => {
            setWorkspacePath(path)
            setWorkspacePickerOpen(false)
          }}
          onClose={() => setWorkspacePickerOpen(false)}
        />
      )}
    </div>
  )
}
