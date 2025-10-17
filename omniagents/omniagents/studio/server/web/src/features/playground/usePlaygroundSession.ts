import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { api } from '../../lib/api'

export type PlaygroundConnectionState = 'idle' | 'connecting' | 'connected' | 'closed' | 'error'

export interface PlaygroundChatEvent {
  id: string
  type: 'user' | 'assistant' | 'tool' | 'status' | 'approval'
  content?: string
  runId?: string
  callId?: string
  name?: string
  status?: 'called' | 'completed'
  input?: string
  output?: string
  severity?: 'info' | 'error'
  approvalId?: string
  requestId?: string
  arguments?: string
  metadata?: Record<string, unknown>
}

export interface PlaygroundUsageTotals {
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface PlaygroundApprovalDecision {
  approved: boolean
  alwaysApprove?: boolean
  errorMessage?: string
  toolName?: string
}

interface ChatState {
  events: PlaygroundChatEvent[]
  activeRunId: string | null
  running: boolean
  usageTotals: PlaygroundUsageTotals | null
}

interface AppendUserAction {
  type: 'append-user'
  event: PlaygroundChatEvent
}

interface AppendAssistantAction {
  type: 'append-assistant'
  runId: string | undefined
  id: string
  content: string
}

interface ToolCalledAction {
  type: 'tool-called'
  callId: string
  id: string
  name: string
  input?: string
}

interface ToolResultAction {
  type: 'tool-result'
  callId: string
  output?: string
}

interface StatusAction {
  type: 'status'
  event: PlaygroundChatEvent
}

interface AppendApprovalAction {
  type: 'append-approval'
  event: PlaygroundChatEvent
}

interface ResolveApprovalAction {
  type: 'resolve-approval'
  approvalId: string
}

interface SetActiveRunAction {
  type: 'set-active-run'
  runId: string
}

interface ClearActiveRunAction {
  type: 'clear-active-run'
}

interface UsageAction {
  type: 'set-usage'
  totals: PlaygroundUsageTotals
}

interface ResetAction {
  type: 'reset'
}

type ChatAction =
  | AppendUserAction
  | AppendAssistantAction
  | ToolCalledAction
  | ToolResultAction
  | StatusAction
  | SetActiveRunAction
  | ClearActiveRunAction
  | UsageAction
  | ResetAction
  | AppendApprovalAction
  | ResolveApprovalAction

const initialChatState: ChatState = {
  events: [],
  activeRunId: null,
  running: false,
  usageTotals: null,
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'reset':
      return initialChatState
    case 'append-user':
      return {
        ...state,
        events: [...state.events, action.event],
      }
    case 'append-assistant': {
      const events = [...state.events]
      const last = events[events.length - 1]
      if (last && last.type === 'assistant' && last.runId === action.runId) {
        events[events.length - 1] = {
          ...last,
          content: (last.content || '') + action.content,
        }
      } else {
        events.push({ id: action.id, type: 'assistant', content: action.content, runId: action.runId })
      }
      return { ...state, events }
    }
    case 'tool-called':
      return {
        ...state,
        events: [
          ...state.events,
          {
            id: action.id,
            type: 'tool',
            callId: action.callId,
            name: action.name,
            status: 'called',
            input: action.input,
          },
        ],
      }
    case 'tool-result': {
      const events = state.events.map((event) => {
        if (event.type === 'tool' && event.callId === action.callId) {
          return {
            ...event,
            status: 'completed' as const,
            output: action.output,
          }
        }
        return event
      })
      return { ...state, events }
    }
    case 'status':
      return {
        ...state,
        events: [...state.events, action.event],
      }
    case 'append-approval':
      return {
        ...state,
        events: [...state.events, action.event],
      }
    case 'resolve-approval':
      return {
        ...state,
        events: state.events.filter((event) => event.type !== 'approval' || event.approvalId !== action.approvalId),
      }
    case 'set-active-run':
      return {
        ...state,
        activeRunId: action.runId,
        running: true,
      }
    case 'clear-active-run':
      return {
        ...state,
        activeRunId: null,
        running: false,
      }
    case 'set-usage':
      return {
        ...state,
        usageTotals: action.totals,
      }
    default:
      return state
  }
}

function createId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2)
}

export interface PlaygroundSessionHook {
  events: PlaygroundChatEvent[]
  usageTotals: PlaygroundUsageTotals | null
  connectionState: PlaygroundConnectionState
  connectionError: string | null
  sendPrompt: (prompt: string) => Promise<void>
  stopRun: () => void
  isRunning: boolean
  respondToApproval: (approvalId: string, decision: PlaygroundApprovalDecision) => Promise<void>
  resetConversation: () => void
}

export function usePlaygroundSession(sessionId: string | null | undefined, wsBase?: string, agentKey?: string): PlaygroundSessionHook {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const [connectionState, setConnectionState] = useState<PlaygroundConnectionState>('idle')
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const requestCounter = useRef(0)
  const pendingRunRef = useRef(false)
  const serverSessionRef = useRef<string | null>(null)
  const pendingMethodRef = useRef<Map<number, string>>(new Map())
  const latestSessionIdRef = useRef<string | null>(null)
  const latestAgentKeyRef = useRef<string | undefined>(undefined)

  const resetConversation = useCallback(() => {
    dispatch({ type: 'reset' })
    const ws = wsRef.current
    if (ws) {
      ws.close()
      wsRef.current = null
    }
    setConnectionState('idle')
    setConnectionError(null)
    pendingRunRef.current = false
    serverSessionRef.current = null
    pendingMethodRef.current.clear()
  }, [])

  useEffect(() => {
    latestSessionIdRef.current = sessionId ?? null
  }, [sessionId])

  useEffect(() => {
    latestAgentKeyRef.current = agentKey ?? undefined
  }, [agentKey])

  useEffect(() => {
    dispatch({ type: 'reset' })
    const methodStore = pendingMethodRef.current
    if (!sessionId) {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      setConnectionState('idle')
      setConnectionError(null)
      pendingRunRef.current = false
      serverSessionRef.current = null
      methodStore.clear()
      return
    }

    setConnectionState('connecting')
    setConnectionError(null)

    const currentSessionId = sessionId
    const currentAgentKey = agentKey ?? undefined
    const ws = api.createPlaygroundWebSocket(sessionId, wsBase, currentAgentKey)
    wsRef.current = ws

    console.debug('[playground] connecting to websocket for session', sessionId, currentAgentKey ? `agent ${currentAgentKey}` : '')

    ws.onopen = () => {
      console.debug('[playground] websocket connected', currentAgentKey ? `agent ${currentAgentKey}` : '')
      setConnectionState('connected')
      setConnectionError(null)
      const rpcId = ++requestCounter.current
      methodStore.set(rpcId, 'client_functions')
      const payload = {
        jsonrpc: '2.0',
        id: rpcId,
        method: 'client_functions',
        params: {
          version: 1,
          functions: [
            {
              name: 'ui.set_status',
              description: 'Override inline status text',
            },
            {
              name: 'ui.request_tool_approval',
              description: 'Prompt the user to approve a tool invocation',
            },
          ],
        },
      }
      console.debug('[playground] → client_functions', payload)
      ws.send(JSON.stringify(payload))
    }

    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data)
        console.debug('[playground] ← message', payload)
        if (payload.id !== undefined) {
          const numericId = Number(payload.id)
          const pendingMethod = methodStore.get(numericId)
          if (pendingMethod === 'start_run' && payload.result && typeof payload.result === 'object') {
            const result = payload.result as { session_id?: string }
            if (typeof result.session_id === 'string') {
              serverSessionRef.current = result.session_id
            }
          }
          methodStore.delete(numericId)
          if (payload.error && payload.error.message) {
            dispatch({
              type: 'status',
              event: {
                id: createId(),
                type: 'status',
                severity: 'error',
                content: String(payload.error.message),
              },
            })
          }
        }
        if (payload.method) {
          const params = payload.params || {}
          switch (payload.method) {
            case 'message_output': {
              const content = typeof params.content === 'string' ? params.content : ''
              if (content) {
                dispatch({
                  type: 'append-assistant',
                  runId: params.run_id,
                  id: createId(),
                  content,
                })
              }
              break
            }
            case 'tool_called': {
              dispatch({
                type: 'tool-called',
                callId: params.call_id || params.callId || createId(),
                id: createId(),
                name: params.tool || 'tool',
                input: params.input,
              })
              break
            }
            case 'tool_result': {
              dispatch({
                type: 'tool-result',
                callId: params.call_id || params.callId,
                output: typeof params.output === 'string' ? params.output : JSON.stringify(params.output ?? ''),
              })
              break
            }
            case 'run_status': {
              dispatch({
                type: 'status',
                event: {
                  id: createId(),
                  type: 'status',
                  content: params.message || 'Status update',
                  severity: 'info',
                },
              })
              break
            }
            case 'run_started': {
              const runId = params.run_id || params.runId
              if (typeof runId === 'string') {
                pendingRunRef.current = false
                dispatch({ type: 'set-active-run', runId })
              }
              if (typeof params.session_id === 'string') {
                serverSessionRef.current = params.session_id
              }
              break
            }
            case 'run_end': {
              pendingRunRef.current = false
              dispatch({ type: 'clear-active-run' })
              if (params.error) {
                dispatch({
                  type: 'status',
                  event: {
                    id: createId(),
                    type: 'status',
                    severity: 'error',
                    content: params.error.message || 'Run ended with an error',
                  },
                })
              } else {
                dispatch({
                  type: 'status',
                  event: {
                    id: createId(),
                    type: 'status',
                    severity: 'info',
                    content: 'Run completed',
                  },
                })
              }
              if (params.usage && typeof params.usage === 'object') {
                const totals = params.usage
                const normalized: PlaygroundUsageTotals = {
                  requests: totals.requests ?? 0,
                  input_tokens: totals.input_tokens ?? 0,
                  output_tokens: totals.output_tokens ?? 0,
                  total_tokens: totals.total_tokens ?? 0,
                }
                dispatch({ type: 'set-usage', totals: normalized })
              }
              break
            }
            case 'token': {
              const totals = params.totals || params.total
              if (totals) {
                const normalized: PlaygroundUsageTotals = {
                  requests: totals.requests ?? 0,
                  input_tokens: totals.input_tokens ?? 0,
                  output_tokens: totals.output_tokens ?? 0,
                  total_tokens: totals.total_tokens ?? 0,
                }
                dispatch({ type: 'set-usage', totals: normalized })
              }
              break
            }
            case 'client_request': {
              const functionNameRaw = params.function || params.name
              const functionName = typeof functionNameRaw === 'string' ? functionNameRaw.toLowerCase() : ''
              const requestId = typeof params.request_id === 'string' ? params.request_id : undefined

              if (functionName === 'ui.request_tool_approval') {
                const args = (params.args as Record<string, unknown> | undefined) ?? {}
                const toolName = typeof args.tool === 'string' && args.tool.trim().length > 0 ? args.tool : 'tool'
                const approvalSource = (params.approval_id ?? params.approvalId ?? args.approval_id ?? args.approvalId) as
                  | string
                  | undefined
                const approvalId = approvalSource && approvalSource.trim().length > 0 ? approvalSource : createId()
                let argsText = ''
                const rawArgs = args.arguments ?? params.arguments
                if (typeof rawArgs === 'string') {
                  argsText = rawArgs
                } else if (rawArgs !== undefined) {
                  try {
                    argsText = JSON.stringify(rawArgs)
                  } catch {
                    argsText = String(rawArgs)
                  }
                }

                let metadata: Record<string, unknown> | undefined
                const rawMetadata = (params.metadata ?? args.metadata) as unknown
                if (typeof rawMetadata === 'string') {
                  try {
                    const parsed = JSON.parse(rawMetadata)
                    if (parsed && typeof parsed === 'object') {
                      metadata = parsed as Record<string, unknown>
                    }
                  } catch {
                    metadata = undefined
                  }
                } else if (rawMetadata && typeof rawMetadata === 'object') {
                  metadata = rawMetadata as Record<string, unknown>
                }

                dispatch({
                  type: 'append-approval',
                  event: {
                    id: createId(),
                    type: 'approval',
                    approvalId,
                    requestId,
                    name: toolName,
                    arguments: argsText,
                    metadata,
                  },
                })
                dispatch({
                  type: 'status',
                  event: {
                    id: createId(),
                    type: 'status',
                    content: `Approval requested for ${toolName}`,
                    severity: 'info',
                  },
                })
                break
              }

              if (functionName === 'ui.set_status' && params.args && typeof params.args.text === 'string') {
                dispatch({
                  type: 'status',
                  event: {
                    id: createId(),
                    type: 'status',
                    content: params.args.text,
                    severity: params.args.show_spinner ? 'info' : 'info',
                  },
                })
              }
              if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && requestId) {
                wsRef.current.send(
                  JSON.stringify({
                    jsonrpc: '2.0',
                    method: 'client_response',
                    params: {
                      request_id: requestId,
                      ok: true,
                      result: { ack: true },
                    },
                  })
                )
              }
              break
            }
            default:
              break
          }
        } else if (payload.id && payload.result) {
          // Handle responses if necessary in the future
        }
      } catch (err) {
        console.error('Failed to process playground event', err)
      }
    }

    ws.onerror = (event) => {
      if (latestSessionIdRef.current !== currentSessionId || latestAgentKeyRef.current !== currentAgentKey) {
        return
      }
      console.error('[playground] websocket error', event)
      setConnectionState('error')
      setConnectionError('WebSocket error')
    }

    ws.onclose = (event) => {
      if (latestSessionIdRef.current !== currentSessionId || latestAgentKeyRef.current !== currentAgentKey) {
        return
      }
      console.info('[playground] websocket closed', event.code, event.reason)
      pendingRunRef.current = false
      wsRef.current = null
      serverSessionRef.current = null
      methodStore.clear()
      if (event.wasClean) {
        setConnectionState('closed')
      } else {
        setConnectionState('error')
        setConnectionError('Connection closed unexpectedly')
      }
    }

    return () => {
      pendingRunRef.current = false
      serverSessionRef.current = null
      methodStore.clear()
      if (wsRef.current === ws) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [sessionId, wsBase, agentKey])

  const sendPrompt = useCallback(
    async (prompt: string) => {
      const trimmed = prompt.trim()
      if (!trimmed) {
        return
      }
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        throw new Error('Playground connection is not ready')
      }
      pendingRunRef.current = true
      const id = createId()
      dispatch({
        type: 'append-user',
        event: {
          id,
          type: 'user',
          content: trimmed,
        },
      })
      const rpcId = ++requestCounter.current
      pendingMethodRef.current.set(rpcId, 'start_run')
      wsRef.current.send(
        JSON.stringify({
          jsonrpc: '2.0',
          id: rpcId,
          method: 'start_run',
          params: {
            prompt: trimmed,
            session_id: serverSessionRef.current || undefined,
          },
        })
      )
    },
    []
  )

  const stopRun = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return
    }
    const runId = state.activeRunId
    if (!runId) {
      return
    }
    const rpcId = ++requestCounter.current
    pendingMethodRef.current.set(rpcId, 'stop_run')
    wsRef.current.send(
      JSON.stringify({
        jsonrpc: '2.0',
        id: rpcId,
        method: 'stop_run',
        params: { run_id: runId },
      })
    )
  }, [state.activeRunId])

  const respondToApproval = useCallback(
    async (approvalId: string, decision: PlaygroundApprovalDecision) => {
      if (!approvalId) {
        throw new Error('Missing approval identifier')
      }
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        throw new Error('Playground connection is not ready')
      }
      const pendingApproval = state.events.find((event) => event.type === 'approval' && event.approvalId === approvalId)
      if (!pendingApproval || !pendingApproval.requestId) {
        throw new Error('Unknown or expired approval request')
      }

      const resultPayload: Record<string, unknown> = {
        approved: decision.approved,
      }
      if (decision.alwaysApprove) {
        resultPayload.always_approve = true
      }
      if (!decision.approved && decision.errorMessage) {
        const trimmed = decision.errorMessage.trim()
        if (trimmed) {
          resultPayload.error_message = trimmed
        }
      }

      wsRef.current.send(
        JSON.stringify({
          jsonrpc: '2.0',
          method: 'client_response',
          params: {
            request_id: pendingApproval.requestId,
            ok: true,
            result: resultPayload,
          },
        })
      )

      dispatch({ type: 'resolve-approval', approvalId })
      const labelSource = decision.toolName && decision.toolName.trim().length > 0 ? decision.toolName : pendingApproval.name
      const label = labelSource && labelSource.trim().length > 0 ? labelSource.trim() : 'tool'
      const message = decision.approved
        ? decision.alwaysApprove
          ? `Always approve enabled for ${label}`
          : `Approved ${label}`
        : `Rejected ${label}`
      dispatch({
        type: 'status',
        event: {
          id: createId(),
          type: 'status',
          content: message,
          severity: decision.approved ? 'info' : 'error',
        },
      })
    },
    [dispatch, state.events]
  )

  const isRunning = useMemo(() => state.running || pendingRunRef.current, [state.running])

  return {
    events: state.events,
    usageTotals: state.usageTotals,
    connectionState,
    connectionError,
    sendPrompt,
    stopRun,
    isRunning,
    respondToApproval,
    resetConversation,
  }
}
