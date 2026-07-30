import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Header } from './components/Header'
import { ModelPicker } from './components/ModelPicker'
import { MessageList, type ApprovalItem, type ChatMessage, type ElicitationItem, type McpApprovalItem, type MessageItem } from './components/MessageList'
import { Input } from './components/Input'
import { Tasks, type TaskSummary } from './components/Tasks'
import { Notifications, type NotificationInfo } from './components/Notifications'
import { RecapPanel, type RecapInfo } from './components/RecapPanel'
import { EscalationBanner, type EscalationInfo } from './components/EscalationBanner'
import { QueuedMessages } from './components/QueuedMessages'
import { GoalPanel, type GoalSnapshot } from './components/GoalPanel'
import { WakeupPanel, type WakeupSnapshot } from './components/WakeupPanel'
import { LoopPanel, type LoopTaskSnapshot } from './components/LoopPanel'
import { WorkersPanel, type WorkerSummary, type WorkersKillResult } from './components/WorkersPanel'
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
import { RPCClient, type QueuedMessage } from './rpc/client'

type UIState = 'connecting' | 'resume' | 'chat' | 'error'

// Tools whose user-visible side-effect is painted entirely by client-
// side handlers (see the ``client_request`` switch below) — their
// tool_called / tool_result events are suppressed in the transcript so
// the docked panel / banner is the only render. ``goal_complete`` is a
// pure control signal surfaced via the goal state instead.
const HIDDEN_TOOLS = new Set(['notify', 'escalate', 'goal_complete'])

function wsUrlFromLocation(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host = window.location.host
  return `${proto}://${host}/ws`
}

function tokenFromLocation(): string | undefined {
  // The launcher hands the page its access token as a query param; the
  // client exchanges it for a one-time connect ticket instead of ever
  // putting it in the WebSocket URL.
  const url = new URL(window.location.href)
  return url.searchParams.get('token') || undefined
}

export function App() {
  const [ui, setUI] = useState<UIState>('connecting')
  const [connected, setConnected] = useState(false)
  // Retryable outages ("reconnecting…") versus terminal failures are
  // surfaced distinctly: transient states show a banner over the live UI,
  // permanent closes explain that no further retries will happen.
  const [connectionIssue, setConnectionIssue] = useState<string | null>(null)
  const [connectionTerminal, setConnectionTerminal] = useState(false)
  const [voiceEnabled, setVoiceEnabled] = useState(false)
  const [items, setItems] = useState<MessageItem[]>([])
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([])
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [bashJobs, setBashJobs] = useState<BashJobSummary[]>([])
  const [goalSnapshot, setGoalSnapshot] = useState<GoalSnapshot | null>(null)
  const [wakeupSnapshot, setWakeupSnapshot] = useState<WakeupSnapshot | null>(null)
  const [loopTasks, setLoopTasks] = useState<LoopTaskSnapshot[]>([])
  const [workers, setWorkers] = useState<WorkerSummary[]>([])
  // Dismissed IDs for each docked panel. Snapshotted on user submit so
  // the next run starts with a clean dock — items currently in a
  // terminal state get added to the set and filtered out at render
  // time. Items spawned by the new run aren't in the set yet, so they
  // show normally.
  const [dismissedWorkerIds, setDismissedWorkerIds] = useState<Set<string>>(new Set())
  const [dismissedJobIds, setDismissedJobIds] = useState<Set<string>>(new Set())
  const [dismissedTaskIds, setDismissedTaskIds] = useState<Set<string>>(new Set())
  // Notifications accumulate from the agent's `notify` builtin calls;
  // dismissed manually via the panel buttons.
  const [notifications, setNotifications] = useState<NotificationInfo[]>([])
  // Most recent session recap (from /recap or idle auto-recap). Single-slot,
  // newest replaces old, dismissible.
  const [recap, setRecap] = useState<RecapInfo | null>(null)
  // Pending agent escalation — the next user submit becomes the reply.
  const [escalation, setEscalation] = useState<EscalationInfo | null>(null)
  const [queuedMessages, setQueuedMessages] = useState<QueuedMessage[]>([])
  const [thinking, setThinking] = useState(false)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [statusSpinner, setStatusSpinner] = useState<boolean>(false)
  const [statusItalic, setStatusItalic] = useState<boolean>(false)
  const [preamble, setPreamble] = useState<string | undefined>(undefined)
  const [toolStatus, setToolStatus] = useState<string | undefined>(undefined)
  const [runId, setRunId] = useState<string | undefined>(undefined)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [sessions, setSessions] = useState<SessionItem[]>([])
  const [_usageTotals, setUsageTotals] = useState<Record<string, unknown> | undefined>(undefined)
  const [_usageDelta, setUsageDelta] = useState<Record<string, unknown> | undefined>(undefined)
  const [_modelInfo, setModelInfo] = useState<{ model?: string; max_input_tokens?: number; max_output_tokens?: number } | undefined>(undefined)
  // Catalog identity of the serving model (usage events' `model_ref`) —
  // keeps the header picker in sync with mid-session model changes.
  const [activeModelRef, setActiveModelRef] = useState<string | undefined>(undefined)
  const [agentName, setAgentName] = useState<string>('OmniAgent')
  const [welcomeText, setWelcomeText] = useState<string | undefined>(undefined)
  const normalizeAgentName = useCallback((name: string) => {
    let s = String(name || '').trim()
    s = s.replace(/[_-]+/g, ' ')
    s = s.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    s = s.replace(/\s+/g, ' ')
    return s.split(' ').map(w => w ? (w[0].toUpperCase() + w.slice(1).toLowerCase()) : '').join(' ').trim()
  }, [])

  const client = useMemo(
    () => new RPCClient(wsUrlFromLocation(), tokenFromLocation()),
    []
  )
  const [initialSent, setInitialSent] = useState(false)
  const preambleBufferRef = useRef<Array<{ content: string; timestamp: Date; superseded: boolean }>>([])
  const pendingApprovalsRef = useRef(new Map<string, ApprovalItem>())
  const pendingMcpApprovalsRef = useRef(new Map<string, McpApprovalItem>())
  const pendingElicitationsRef = useRef(new Map<string, ElicitationItem>())
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
  // Seed from ``window.__OMNI_BOOT__`` injected by the web backend's SPA
  // shell route. Kills the "Select workspace" flash that would otherwise
  // last the entire boot window — ``fs_get_workspace_root`` still runs
  // post-connect and overwrites with the live manifest root once it
  // resolves. Falls back to null in dev mode or if the server doesn't
  // bake the global (older builds).
  const [workspacePath, setWorkspacePath] = useState<string | null>(() => {
    try {
      const boot = (window as unknown as { __OMNI_BOOT__?: { workspace_root?: unknown } }).__OMNI_BOOT__
      const root = boot?.workspace_root
      return typeof root === 'string' && root ? root : null
    } catch {
      return null
    }
  })
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
      const itemSession = item.session_id
      if (itemSession && itemSession !== targetSession) continue
      const exists = base.some(existing => existing.type === 'approval' && existing.call_id === item.call_id)
      if (!exists) additions.push(item)
    }
    for (const item of pendingMcpApprovalsRef.current.values()) {
      const itemSession = item.session_id
      if (itemSession && itemSession !== targetSession) continue
      const exists = base.some(existing => existing.type === 'mcp_approval' && existing.request_id === item.request_id)
      if (!exists) additions.push(item)
    }
    for (const item of pendingElicitationsRef.current.values()) {
      const itemSession = item.session_id
      if (itemSession && itemSession !== targetSession) continue
      const exists = base.some(existing => existing.type === 'elicitation' && existing.elicitation_id === item.elicitation_id)
      if (!exists) additions.push(item)
    }
    return additions.length ? [...base, ...additions] : base
  }, [])

  const refreshSessions = useCallback(async () => {
    try {
      const list = await client.listSessions()
      setSessions(list as SessionItem[])
    } catch {}
  }, [client])

  useEffect(() => {
    const offConnectionState = client.onConnectionState((state) => {
      setConnected(state.state === 'connected')
      if (state.state === 'connected') {
        setConnectionIssue(null)
        setConnectionTerminal(false)
      } else if (state.state === 'reconnecting') {
        setConnectionIssue(`Connection lost — reconnecting (attempt ${state.attempt})…`)
        setConnectionTerminal(false)
      } else if (state.state === 'closed') {
        setConnectionIssue(
          state.permanent
            ? `Connection closed${state.reason ? ` (${state.reason})` : ''} — not retrying. Reload to try again.`
            : 'Connection closed.',
        )
        setConnectionTerminal(state.permanent)
      }
    })
      client.connect()
        .then(async () => {
          setConnected(true)
          setVoiceEnabled(client.initializeResult?.capabilities.realtime === true)
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
              const res = await client.serverCall('fs_get_workspace_root') as { path?: string }
              if (res?.path) setWorkspacePath(res.path)
            } catch {}
          }
        } catch {}
        const url = new URL(window.location.href)
        const resume = url.searchParams.get('resume') === 'true'
        const sid = url.searchParams.get('session') || undefined
        if (resume && !sid) {
          try {
            const list = await client.listSessions()
            setSessions(list as SessionItem[])
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

    const offMessageOutput = client.on('message_output', (p) => {
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
    const offQueueChanged = client.on('queue_changed', (p) => {
      // Full-snapshot replace. Only accept events for the currently-loaded
      // session so stale broadcasts (e.g. after switching sessions) don't
      // poison the "Up next" panel.
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
      setQueuedMessages(Array.isArray(p?.items) ? (p.items as QueuedMessage[]) : [])
    })
    const offRunStarted = client.on('run_started', (p) => {
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
      // Queue-drainer / background-automation path: the run was started
      // server-side without a local handleSubmit, so no message has been
      // pushed into the transcript yet. Append it from event.prompt when
      // the last item with matching role doesn't already match
      // (idempotency for the direct-submit case where the optimistic
      // push already ran). ``prompt_role`` distinguishes user submissions
      // from assistant-role notification batches (worker/bash-job
      // completions) — without it, assistant wakeups would render as
      // user bubbles.
      const promptFromEvent = typeof p?.prompt === 'string' ? p.prompt : ''
      const promptRoleFromEvent: 'user' | 'assistant' | 'system' =
        p?.prompt_role === 'assistant' ? 'assistant'
        : p?.prompt_role === 'system' ? 'system'
        : 'user'
      if (promptFromEvent) {
        setItems(prev => {
          const lastMatching = [...prev].reverse().find(
            it => it.type === 'chat' && it.role === promptRoleFromEvent
          ) as ChatMessage | undefined
          if (lastMatching && lastMatching.content === promptFromEvent) return prev
          return [...prev, { type: 'chat', role: promptRoleFromEvent, content: promptFromEvent } as MessageItem]
        })
      }
    })
    const offRunEnd = client.on('run_end', (p) => {
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
        if (typeof p?.model_ref === 'string') setActiveModelRef(p.model_ref)
        setUsageTotals(usage)
      } catch {}
      setStatusSpinner(false)
      setStatusItalic(false)
      refreshSessions()
    })
    const offRunStatus = client.on('run_status', (p) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
      const msg = [p?.status, p?.message].filter(Boolean).join(': ')
      setStatus(msg)
      setStatusSpinner(true)
      setStatusItalic(false)
    })
    const offToken = client.on('token', (p) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
      try {
        setUsageDelta(p?.delta)
        setUsageTotals(p?.totals)
        setModelInfo({ model: p?.model, max_input_tokens: p?.max_input_tokens, max_output_tokens: p?.max_output_tokens })
        if (typeof p?.model_ref === 'string') setActiveModelRef(p.model_ref)
      } catch {}
    })
    const offToolCalled = client.on('tool_called', (p) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId) {
        if (sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
        if (!sessionIdRef.current && !startingRunRef.current) return
      }
      const tool = String(p?.tool ?? '')
      // Hide tools whose UX is painted entirely by client-side handlers
      // (notify -> notifications panel, escalate -> banner, goal_complete
      // -> goal panel). Their tool_called / tool_result events still flow
      // through (LLM still sees them) but the transcript row is noise.
      if (HIDDEN_TOOLS.has(tool)) return
      const input = typeof p?.input === 'string' ? p?.input : JSON.stringify(p?.input)
      const call_id = String(p?.call_id ?? '')
      preambleBufferRef.current.forEach(m => { m.superseded = true })
      setItems(prev => [...prev, { type: 'tool', tool, input, call_id, status: 'called', runId: runIdRef.current }])
    })
    const offToolResult = client.on('tool_result', (p) => {
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      if (eventSessionId) {
        if (sessionIdRef.current && sessionIdRef.current !== eventSessionId) return
        if (!sessionIdRef.current && !startingRunRef.current) return
      }
      const tool = String(p?.tool ?? '')
      if (HIDDEN_TOOLS.has(tool)) return
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
        const idx = prev.findIndex(it => it.type === 'tool' && it.call_id === call_id)
        if (idx >= 0) {
          const next = prev.slice()
          const it = next[idx]
          next[idx] = { ...it, output, status: 'result', metadata } as MessageItem
          return next
        }
        return [...prev, { type: 'tool', tool, output, call_id, status: 'result', metadata, runId: runIdRef.current }]
      })
    })
    const offClientRequest = client.on('client_request', (p) => {
      const fn = String(p?.function ?? '')
      if (fn === 'ui.add_artifact') {
        const request_id = String(p?.request_id ?? '')
        const args: Record<string, unknown> = p?.args || {}
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
      if (fn === 'ui.workers.update') {
        // Broadcast from omni-code's worker spawn / completion hooks.
        // Replace the workers list wholesale.
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const snap = args?.snapshot
        if (Array.isArray(snap)) {
          setWorkers(snap as WorkerSummary[])
        }
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'ui.goal.update') {
        // /goal autopilot loop snapshot. Fired on start, every turn end,
        // and on completion/cancellation. snapshot=null means torn down
        // (panel clears). The omni-code server_functions/goal.py is the
        // source.
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const snap = args?.snapshot
        if (snap === null || snap === undefined) {
          setGoalSnapshot(null)
        } else if (typeof snap === 'object') {
          setGoalSnapshot(snap as GoalSnapshot)
        }
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'ui.wakeup.update') {
        // schedule_wakeup tick loop snapshot. Fired on start, every tick,
        // and on cancel/exhaustion. snapshot=null means torn down (panel
        // clears). The omni-code server_functions/wakeup.py is the source.
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const snap = args?.snapshot
        if (snap === null || snap === undefined) {
          setWakeupSnapshot(null)
        } else if (typeof snap === 'object') {
          setWakeupSnapshot(snap as WakeupSnapshot)
        }
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'ui.loop.update') {
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const snap = args?.snapshot
        if (Array.isArray(snap)) {
          setLoopTasks(snap as LoopTaskSnapshot[])
        }
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
        const args: Record<string, unknown> = p?.args || {}
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
        const args: Record<string, unknown> = p?.args || {}
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
      if (fn === 'notify') {
        // Agent ``notify`` builtin — fire-and-forget heads-up. Push to
        // the docked notifications panel and ack immediately; the user
        // dismisses manually.
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const message = typeof args?.message === 'string' ? args.message : ''
        if (message) {
          setNotifications(prev => [
            ...prev,
            {
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
              message,
              timestamp: Date.now(),
            },
          ])
        }
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'ui.recap') {
        // Session recap pushed from the server (/recap or idle auto-recap).
        // Single-slot panel; newest replaces any prior recap.
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const text = typeof args?.text === 'string' ? args.text : ''
        if (text) {
          setRecap({ text, timestamp: Date.now() })
        }
        if (request_id) {
          client.clientResponse(request_id, true, { ack: true }).catch(() => {})
        }
        return
      }
      if (fn === 'escalate') {
        // Agent ``escalate`` builtin — blocking. We surface the banner
        // and intentionally DO NOT call clientResponse here; the next
        // user submit reads the pending escalation, sends the reply
        // via clientResponse, and clears the banner.
        const request_id = String(p?.request_id ?? '')
        const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
        if (eventSessionId && sessionIdRef.current && sessionIdRef.current !== eventSessionId) {
          if (request_id) {
            client.clientResponse(request_id, true, { ack: true }).catch(() => {})
          }
          return
        }
        const args: Record<string, unknown> = p?.args || {}
        const message = typeof args?.message === 'string' ? args.message : ''
        const runIdArg = typeof p?.run_id === 'string' ? p.run_id : undefined
        if (!request_id) {
          return
        }
        setEscalation({
          request_id,
          message,
          session_id: eventSessionId,
          run_id: runIdArg,
        })
        return
      }
    })

    const offClientRequestResolved = client.on('client_request_resolved', () => {
      // No-op: tool approvals no longer flow through client_request, so a
      // client_request resolution carries no approval state for us to
      // discard. Kept subscribed for future use / generic UI requests.
    })

    const offToolApprovalRequested = client.on('tool_approval_requested', (p) => {
      const call_id = String(p?.call_id ?? '')
      const tool = String(p?.tool_name ?? '')
      const argumentsText = String(p?.arguments ?? '')
      const metadata = p?.metadata
      setThinking(false)
      if (!call_id) return
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      const approvalItem: ApprovalItem = { type: 'approval', call_id, tool, argumentsText, metadata, session_id: eventSessionId }
      pendingApprovalsRef.current.set(call_id, approvalItem)
      const shouldDisplay = !eventSessionId || !sessionIdRef.current || sessionIdRef.current === eventSessionId
      if (shouldDisplay) {
        setItems(prev => {
          const filtered = prev.filter(it => !(it.type === 'approval' && it.call_id === call_id))
          return [...filtered, approvalItem]
        })
      }
    })

    const offToolApprovalResolved = client.on('tool_approval_resolved', (p) => {
      const call_id = String(p?.call_id ?? '')
      if (call_id) {
        pendingApprovalsRef.current.delete(call_id)
        setItems(prev => prev.filter(it => !(it.type === 'approval' && it.call_id === call_id)))
      }
    })

    // Hosted-MCP approval flow — parallel to tool_approval but keyed by
    // request_id (minted by the model on the McpApprovalRequest) rather
    // than call_id, and identifies the MCP server via server_label.
    const offMcpApprovalRequested = client.on('mcp_approval_requested', (p) => {
      const request_id = String(p?.request_id ?? '')
      const server_label = String(p?.server_label ?? '')
      const tool = String(p?.tool_name ?? '')
      const argumentsText = String(p?.arguments ?? '')
      setThinking(false)
      if (!request_id) return
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      const approvalItem: McpApprovalItem = { type: 'mcp_approval', request_id, server_label, tool, argumentsText, session_id: eventSessionId }
      pendingMcpApprovalsRef.current.set(request_id, approvalItem)
      const shouldDisplay = !eventSessionId || !sessionIdRef.current || sessionIdRef.current === eventSessionId
      if (shouldDisplay) {
        setItems(prev => {
          const filtered = prev.filter(it => !(it.type === 'mcp_approval' && it.request_id === request_id))
          return [...filtered, approvalItem]
        })
      }
    })

    const offMcpApprovalResolved = client.on('mcp_approval_resolved', (p) => {
      const request_id = String(p?.request_id ?? '')
      if (request_id) {
        pendingMcpApprovalsRef.current.delete(request_id)
        setItems(prev => prev.filter(it => !(it.type === 'mcp_approval' && it.request_id === request_id)))
      }
    })

    // Structured user elicitation — the non-security input lane
    // (protocol.md § "User Elicitation Flow"). Prompts render as real
    // input cards in the transcript; the first response across tabs wins
    // and elicitation_resolved dismisses the card everywhere.
    const offElicitationRequested = client.on('elicitation_requested', (p) => {
      const elicitation_id = String(p?.elicitation_id ?? '')
      if (!elicitation_id) return
      setThinking(false)
      const eventSessionId = typeof p?.session_id === 'string' ? p.session_id : undefined
      const item: ElicitationItem = {
        type: 'elicitation',
        elicitation_id,
        kind: String(p?.kind ?? ''),
        message: String(p?.message ?? ''),
        title: typeof p?.title === 'string' ? p.title : undefined,
        input_schema: p?.input_schema && typeof p.input_schema === 'object' ? p.input_schema as Record<string, unknown> : undefined,
        options: Array.isArray(p?.options) ? p.options : undefined,
        url: typeof p?.url === 'string' ? p.url : undefined,
        session_id: eventSessionId,
        run_id: typeof p?.run_id === 'string' ? p.run_id : undefined,
        expires_at: typeof p?.expires_at === 'string' ? p.expires_at : undefined,
        persist_response: typeof p?.persist_response === 'boolean' ? p.persist_response : undefined,
      }
      pendingElicitationsRef.current.set(elicitation_id, item)
      const shouldDisplay = !eventSessionId || !sessionIdRef.current || sessionIdRef.current === eventSessionId
      if (shouldDisplay) {
        setItems(prev => {
          const filtered = prev.filter(it => !(it.type === 'elicitation' && it.elicitation_id === elicitation_id))
          return [...filtered, item]
        })
      }
    })

    const offElicitationResolved = client.on('elicitation_resolved', (p) => {
      const elicitation_id = String(p?.elicitation_id ?? '')
      if (elicitation_id) {
        pendingElicitationsRef.current.delete(elicitation_id)
        setItems(prev => prev.filter(it => !(it.type === 'elicitation' && it.elicitation_id === elicitation_id)))
      }
    })

    return () => {
      offQueueChanged(); offMessageOutput(); offRunStarted(); offRunEnd(); offRunStatus(); offClientRequest(); offClientRequestResolved(); offToolApprovalRequested(); offToolApprovalResolved(); offMcpApprovalRequested(); offMcpApprovalResolved(); offElicitationRequested(); offElicitationResolved(); offToken(); offToolCalled(); offToolResult()
      offConnectionState()
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
    // Dismiss successfully-completed items from each docked panel so the
    // next run starts with a clean dock. Failures (worker error/cancelled,
    // non-zero/null bash exit) stick until the user dismisses them so a
    // quietly-failed background task isn't lost behind the next prompt.
    // Items spawned during the new run aren't in the set yet and stay
    // visible until the user's next submit. Slash commands count too — a
    // slash is still a user step boundary.
    setDismissedWorkerIds(prev => {
      const next = new Set(prev)
      for (const w of workers) {
        if (w.status === 'completed') next.add(w.worker_id)
      }
      return next
    })
    setDismissedJobIds(prev => {
      const next = new Set(prev)
      for (const j of bashJobs) {
        if (!j.running && j.exit_code === 0) next.add(j.job_id)
      }
      return next
    })
    setDismissedTaskIds(prev => {
      const next = new Set(prev)
      for (const t of tasks) {
        if (t.status === 'completed') next.add(t.id)
      }
      return next
    })


    // Escalation reply: when the agent is paused on an ``escalate``
    // tool call, route the user's next message back as the tool's
    // reply via client_response (and clear the banner) instead of
    // starting a new run. Slash commands pass through so the user can
    // still issue /goal.stop, /help, etc. mid-escalation.
    if (escalation && !text.startsWith('/')) {
      const pending = escalation
      setEscalation(null)
      try {
        await client.clientResponse(pending.request_id, true, { reply: text })
      } catch {}
      setItems(prev => [...prev, { type: 'chat', role: 'user', content: text }])
      return
    }

    // Slash commands
    if (text.startsWith('/')) {
      const parts = text.trim().split(/\s+/)
      const name = parts[0].slice(1)
      const argText = text.slice(parts[0].length).trim()
      try {
        // Anchor the session before dispatching. Two problems this
        // solves for the slash path:
        //   1. ``sessionId`` is undefined when slash is the user's very
        //      first action — startRun/sendMessage would normally
        //      mint-on-first-call, but ``serverCall(name, args, undef)``
        //      hits the function session-less and goal returns
        //      "No session context". session.ensure mints one and
        //      returns the id.
        //   2. Server functions like /goal trigger start_run via the
        //      drainer, not via our handleSubmit's startRun(variables)
        //      call — so the session must already carry workspace_root
        //      or the framework raises WorkspaceRootRequiredError.
        // Local ``workspacePath`` state tracks both fs_get_workspace_root
        // and the WorkspacePicker, so a user switching workspace via
        // the pill before submit is respected.
        const ensureArgs: Record<string, unknown> = {}
        if (sessionId) ensureArgs.session_id = sessionId
        if (workspacePath && workspaceSupported) ensureArgs.workspace_root = workspacePath
        let sid: string | undefined = sessionId
        try {
          const ensured = await client.serverCall('session.ensure', ensureArgs) as { session_id?: unknown } | undefined
          const ensuredId = typeof ensured?.session_id === 'string' ? ensured.session_id : undefined
          if (ensuredId) {
            if (!sid) setSessionId(ensuredId)
            sid = ensuredId
          }
        } catch {
          /* best-effort — let the dispatch attempt anyway */
        }
        const funcs = await client.listServerFunctions()
        const found = funcs.find(f => String(f.name).toLowerCase() === name.toLowerCase())
        if (!found) {
          // Not a known server function; send to LLM. Route through the
          // queue when a run is active or the queue is non-empty — same
          // guard as the main path below.
          if ((thinking || queuedMessages.length > 0) && sid) {
            await client.enqueueMessage(sid, text, { triggerRun: true, role: 'user', source: 'ui' })
          } else {
            await client.startRun(text, sid)
          }
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
        const result = await client.serverCall(name, args, sid)
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
      let content: unknown = undefined
      let attachments: Array<{ type: 'image' | 'file'; url?: string; filename?: string; mime?: string; size?: number }> = []
      if (files && files.length > 0) {
        const parts: Array<Record<string, unknown>> = []
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
            const param: Record<string, unknown> = { type: 'input_file', file_data: base64 }
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
      // Always pass workspace_root as variables so the context factory picks it up
      const variables = (workspacePath && workspaceSupported)
        ? { workspace_root: workspacePath }
        : undefined

      // Queue the message instead of starting a run directly when a run
      // is currently active or the queue is non-empty. The server-side
      // drainer calls start_run for us once the current run finishes,
      // and the run_started handler appends the user message to the
      // transcript via event.prompt when the queued run actually fires.
      // Skip the optimistic local push on this path — it would land in
      // the transcript before the queue is even drained.
      if ((thinking || queuedMessages.length > 0) && sessionId) {
        startingRunRef.current = false
        await client.enqueueMessage(sessionId, text, {
          triggerRun: true,
          role: 'user',
          variables,
          source: 'ui',
        })
        if (workspaceSupported) setWorkspaceLocked(true)
        return
      }

      // Direct-start path: optimistic user-message push, then start_run.
      setItems(prev => [...prev, { type: 'chat', role: 'user', content: text, attachments }])
      await client.startRun(text, sessionId, variables, content)
      if (workspaceSupported) setWorkspaceLocked(true)
    } catch (e) {
      startingRunRef.current = false
      setStatus(String((e as Error)?.message || 'Failed to start run'))
      setStatusItalic(false)
    }
  }, [client, sessionId, workspacePath, workspaceSupported, thinking, queuedMessages.length, escalation, workers, bashJobs, tasks])

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
      setItems(prev => prev.filter(it => !(it.type === 'approval' && it.call_id === call_id)))
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
      setItems(prev => prev.filter(it => !(it.type === 'mcp_approval' && it.request_id === request_id)))
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

  const handleElicitationRespond = useCallback(async (elicitation_id: string, action: 'accept' | 'decline' | 'cancel', value?: Record<string, unknown>, reason?: string) => {
    const removeElicitation = () => {
      pendingElicitationsRef.current.delete(elicitation_id)
      setItems(prev => prev.filter(it => !(it.type === 'elicitation' && it.elicitation_id === elicitation_id)))
    }
    try {
      await client.elicitationResponse(elicitation_id, action, value, reason)
      removeElicitation()
      setThinking(true)
    } catch (e) {
      // Typed servers-side rejections: another tab already answered
      // (-32051) or the prompt no longer exists (-32050) — dismiss the
      // stale card. Validation errors (-32052) keep the card so the user
      // can fix the input.
      const code = (e as { code?: number })?.code
      if (code === -32050 || code === -32051) {
        removeElicitation()
      }
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
    // Reset the queue panel; we'll seed it from server state below if the
    // newly-selected session has anything pending. ``queue_changed``
    // notifications keep it fresh after that.
    setQueuedMessages([])
    if (id) {
      try {
        const snap = await client.listQueue(id)
        setQueuedMessages(snap.items)
      } catch {}
    }
    // Reset or restore workspace state
    if (!id) {
      // New chat — unlock workspace, restore default (manifest root,
      // not host cwd, so sandboxed agents show the path their tools
      // actually operate on).
      setWorkspaceLocked(false)
      if (workspaceSupported) {
        try {
          const res = await client.serverCall('fs_get_workspace_root') as { path?: string }
          if (res?.path) setWorkspacePath(res.path)
        } catch {}
      }
    }
    if (id) {
      try {
        const history = await client.getSessionHistory(id)
        const msgs: MessageItem[] = []
        const callIndex: Record<string, number> = {}
        for (const item of history as Array<Record<string, unknown>>) {
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
            const metadata = item.metadata as Record<string, unknown> | undefined
            const existing = (call_id && callIndex[call_id] != null) ? callIndex[call_id] : -1
            if (existing >= 0) {
              const prev = msgs[existing]
              msgs[existing] = { ...prev, output, status: 'result', metadata } as MessageItem
            } else {
              msgs.push({ type: 'tool', tool, output, call_id, status: 'result', metadata })
            }
            continue
          }

          if (item && item.role) {
            const role = String(item.role) as ChatMessage['role']
            let content = ''
            const attachments: Array<{ type: 'image' | 'file'; url?: string; filename?: string; mime?: string; size?: number }> = []

            if (typeof item.content === 'string') {
              content = item.content
            } else if (Array.isArray(item.content)) {
              const parts = item.content as Array<Record<string, unknown>>
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
                    attachments.push({ type: 'image', url: String(p.image_url), filename: typeof p.filename === 'string' ? p.filename : undefined })
                  } else if (p && p.type === 'input_file') {
                    attachments.push({ type: 'file', filename: typeof p.filename === 'string' ? p.filename : undefined })
                  }
                }
              }
            } else if (item.content && typeof item.content === 'object') {
              const obj = item.content as Record<string, unknown>
              content = String(obj.text || obj.input_text || '')
              if (!content) {
                try { content = JSON.stringify(item.content) } catch { content = String(item.content) }
              }
            }

            msgs.push({ type: 'chat', role, content, timestamp: typeof item.timestamp === 'string' ? item.timestamp : undefined, attachments })
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
            const snap = m.metadata?.tasks_snapshot
            if (Array.isArray(snap)) lastSnapshot = snap as TaskSummary[]
            const jobsSnap = m.metadata?.bash_jobs_snapshot
            if (Array.isArray(jobsSnap)) lastJobsSnapshot = jobsSnap as BashJobSummary[]
          }
        }
        setTasks(lastSnapshot ?? [])
        setBashJobs(lastJobsSnapshot ?? [])
        // Restore workspace from session and lock it
        if (workspaceSupported) {
          try {
            const res = await client.serverCall('fs_get_workspace_root', {}, id) as { path?: string }
            if (res?.path) setWorkspacePath(res.path)
          } catch {}
          setWorkspaceLocked(true)
        }
        // Seed the /goal panel from server state. The autopilot loop
        // broadcasts ui.goal.update on every state change, but if a goal
        // is already running when we bind to this session we need to
        // pull the current snapshot so the panel renders immediately
        // instead of waiting for the next turn end.
        try {
          const res = await client.serverCall('goal.status', {}, id) as { snapshot?: unknown } | undefined
          const snap = res?.snapshot
          setGoalSnapshot(snap && typeof snap === 'object' ? snap as GoalSnapshot : null)
        } catch {
          setGoalSnapshot(null)
        }
        // Seed the wakeup panel. Same rationale — picks up a schedule
        // that was already running before we bound to this session.
        // wakeup.status may not be registered on every agent; silently
        // ignore so the panel stays empty.
        try {
          const res = await client.serverCall('wakeup.status', {}, id) as { snapshot?: unknown } | undefined
          const snap = res?.snapshot
          setWakeupSnapshot(snap && typeof snap === 'object' ? snap as WakeupSnapshot : null)
        } catch {
          setWakeupSnapshot(null)
        }
        try {
          const res = await client.serverCall('loop.status', {}, id) as { snapshot?: unknown } | undefined
          const snap = res?.snapshot
          setLoopTasks(Array.isArray(snap) ? snap as LoopTaskSnapshot[] : [])
        } catch {
          setLoopTasks([])
        }
        // Seed the workers panel. Same rationale — picks up workers that
        // were already running before we bound to this session.
        try {
          const res = await client.serverCall('workers.list', {}, id) as { snapshot?: unknown } | undefined
          const snap = res?.snapshot
          setWorkers(Array.isArray(snap) ? snap as WorkerSummary[] : [])
        } catch {
          setWorkers([])
        }
      } catch {}
    } else {
      setItems([])
      setTasks([])
      setBashJobs([])
      setGoalSnapshot(null)
      setWakeupSnapshot(null)
      setLoopTasks([])
      setWorkers([])
    }
    // Reset dock dismissal sets on session change so the fresh session
    // starts with the panels showing their full server-side snapshot.
    setDismissedWorkerIds(new Set())
    setDismissedJobIds(new Set())
    setDismissedTaskIds(new Set())
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
    return (
      <div className="app flex-col">
        <Header agentName={agentName} connected={false} />
        <div className="container-chat px-3 py-3 text-sm text-errorRed">
          {connectionTerminal ? connectionIssue ?? 'Failed to connect — not retrying.' : 'Failed to connect.'}
        </div>
      </div>
    )
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
            modelPicker={<ModelPicker client={client} sessionId={sessionId} currentModel={activeModelRef} />}
          />
        )}
        {connectionIssue && (
          <div
            className={`px-3 py-1.5 text-xs ${connectionTerminal ? 'bg-errorRed/10 text-errorRed' : 'bg-warningOrange/10 text-textSubtle'}`}
            role="status"
          >
            {connectionIssue}
          </div>
        )}
        <div className="flex-1 flex flex-row min-h-0 min-w-0">
          <div className="flex-1 flex flex-col min-h-0 min-w-0">
            <MessageList items={items} statusText={status} thinking={thinking} statusSpinner={statusSpinner} preambleText={preamble} welcomeText={welcomeText} onApprovalDecision={handleApprovalDecision} onMcpApprovalDecision={handleMcpApprovalDecision} onElicitationRespond={handleElicitationRespond} statusItalic={statusItalic} onReaction={handleReaction} currentRunId={runId} toolStatusText={toolStatus} />
            <Tasks tasks={(() => {
              const live = tasks.filter(t => !(t.status === 'completed' && dismissedTaskIds.has(t.id)))
              return thinking ? live : live.filter(t => t.status !== 'completed')
            })()} />
            <BashJobs
              jobs={(() => {
                const live = bashJobs.filter(j => !(!j.running && dismissedJobIds.has(j.job_id)))
                // Idle: keep running jobs and failed exits; only successful
                // exits (exit_code === 0) drop out of view.
                return thinking ? live : live.filter(j => j.running || j.exit_code !== 0)
              })()}
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
            <GoalPanel snapshot={goalSnapshot} />
            <WakeupPanel snapshot={wakeupSnapshot} />
            <LoopPanel tasks={loopTasks} />
            <WorkersPanel
              workers={(() => {
                const live = workers.filter(w => !(w.status !== 'running' && dismissedWorkerIds.has(w.worker_id)))
                // Idle: keep running workers and failures (error/cancelled);
                // only successful completions drop out of view.
                return thinking ? live : live.filter(w => w.status !== 'completed')
              })()}
              onKill={async (worker_id) => {
                const res = (await client.serverCall('workers.kill', { worker_id }, sessionId)) as unknown as WorkersKillResult
                if (Array.isArray(res?.snapshot)) setWorkers(res.snapshot as WorkerSummary[])
                return res
              }}
            />
            <QueuedMessages
              items={queuedMessages}
              onCancel={(id) => {
                if (!sessionId) return
                // Optimistic remove — server queue_changed broadcast is
                // the source of truth and will overwrite this if the
                // cancel raced with a drainer pop (returns not_found).
                setQueuedMessages(prev => prev.filter(it => it.id !== id))
                client.cancelQueuedMessage(sessionId, id).catch(() => {})
              }}
            />
            <Notifications
              notifications={notifications}
              onDismiss={(id) => setNotifications(prev => prev.filter(n => n.id !== id))}
              onDismissAll={() => setNotifications([])}
            />
            <RecapPanel recap={recap} onDismiss={() => setRecap(null)} />
            <EscalationBanner escalation={escalation} />
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
