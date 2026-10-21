import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronRight, Loader2, Plus, Sparkles, Trash2, X } from 'lucide-react'
import { api } from '../../lib/api'
import type {
  AgentCreatePayload,
  AgentCreateResponse,
  AgentDeleteResponse,
  AgentSummary,
  EvaluationDatasetsResponse,
  EvaluationAgentOptimizationRequest,
  EvaluationAgentOptimizationResponse,
  EvaluationAgentOptimizationStatus,
} from '../../types'
import { AgentWorkbench, type PlaygroundContext } from '../playground/PlaygroundView'

interface AgentsViewProps {
  selectedAgent: string | null
  onSelectAgent: (agentKey: string) => void
  onClearAgent: () => void
  onUpdateContext: (context: PlaygroundContext) => void
}

const defaultOptimizeFormState = () => ({
  datasetKey: '',
  maxMetricCalls: '100',
  seed: '',
})

const MIN_OPTIMIZATION_BUDGET = 6

const formatEta = (etaSeconds: number | null | undefined) => {
  if (etaSeconds === null || etaSeconds === undefined || !Number.isFinite(etaSeconds) || etaSeconds <= 0) {
    return null
  }
  const totalSeconds = Math.ceil(etaSeconds)
  if (totalSeconds < 60) {
    return `${totalSeconds}s remaining`
  }
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60)
    const remMinutes = minutes % 60
    return `${hours}h ${remMinutes}m remaining`
  }
  return `${minutes}m ${seconds}s remaining`
}

const defaultCreateFormState = () => ({
  name: '',
  model: '',
})

type AgentOptimizeFormState = ReturnType<typeof defaultOptimizeFormState>

export function AgentsView({ selectedAgent, onSelectAgent, onClearAgent, onUpdateContext }: AgentsViewProps) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, refetch } = useQuery<{ agents: AgentSummary[] }>({
    queryKey: ['agents', 'list'],
    queryFn: () => api.getAgents(),
    staleTime: 5 * 60 * 1000,
  })
  const { data: datasetsData, isLoading: isDatasetsLoading } = useQuery<EvaluationDatasetsResponse>({
    queryKey: ['evaluation', 'datasets'],
    queryFn: () => api.getEvaluationDatasets(),
    staleTime: 5 * 60 * 1000,
  })

  const datasets = datasetsData?.datasets ?? []
  const datasetOptions = useMemo(
    () =>
      datasets
        .filter((dataset) => {
          const names = new Set(dataset.splits.map((split) => split.split))
          return names.has('train') && names.has('dev') && names.has('test')
        })
        .map((dataset) => ({
          key: `${dataset.set_name}::${dataset.category}`,
          label: `${dataset.set_name} • ${dataset.category}`,
          dataset,
        }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [datasets]
  )
  const datasetOptionMap = useMemo(() => {
    const map = new Map<string, (typeof datasets)[number]>()
    datasetOptions.forEach((option) => {
      map.set(option.key, option.dataset)
    })
    return map
  }, [datasetOptions])

  const [isOptimizeDialogOpen, setIsOptimizeDialogOpen] = useState(false)
  const [optimizeForm, setOptimizeForm] = useState<AgentOptimizeFormState>(() => defaultOptimizeFormState())
  const [optimizeError, setOptimizeError] = useState<string | null>(null)
  const [activeOptimization, setActiveOptimization] = useState<{ jobId: string; status: EvaluationAgentOptimizationStatus | null } | null>(null)
  const optimizationSocketRef = useRef<WebSocket | null>(null)
  const optimizationCompletionRef = useRef(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [createForm, setCreateForm] = useState(() => defaultCreateFormState())
  const [createError, setCreateError] = useState<string | null>(null)
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const startAgentOptimization = useMutation<EvaluationAgentOptimizationResponse, Error, EvaluationAgentOptimizationRequest>({
    mutationFn: (payload) => api.startAgentOptimization(payload),
    onSuccess: (response) => {
      setActiveOptimization({ jobId: response.job_id, status: response.status ?? null })
      optimizationCompletionRef.current = false
      setIsOptimizeDialogOpen(false)
      setOptimizeForm(defaultOptimizeFormState())
      setOptimizeError(null)
      queryClient.invalidateQueries({ queryKey: ['agents', 'list'] })
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Failed to start agent optimization'
      setOptimizeError(message)
    },
  })

  useEffect(() => {
    setIsOptimizeDialogOpen(false)
    setOptimizeForm(defaultOptimizeFormState())
    setOptimizeError(null)
    if (optimizationSocketRef.current) {
      try {
        optimizationSocketRef.current.close()
      } catch (error) { void error }
      optimizationSocketRef.current = null
    }
    setActiveOptimization(null)
    optimizationCompletionRef.current = false
  }, [selectedAgent])

  useEffect(() => {
    setIsDeleteDialogOpen(false)
    setDeleteError(null)
  }, [selectedAgent])

  useEffect(() => {
    const jobId = activeOptimization?.jobId
    if (!jobId) {
      return
    }
    let socket: WebSocket | null = null
    let disposed = false
    const pollStatusFallback = () => {
      api
        .getAgentOptimizationStatus(jobId)
        .then((response) => {
          if (!response || !response.status) {
            return
          }
          const status = response.status as EvaluationAgentOptimizationStatus
          setActiveOptimization({ jobId: response.job_id, status })
          if (status.state === 'completed') {
            queryClient.invalidateQueries({ queryKey: ['agents', 'list'] })
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
          }
          if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
            optimizationCompletionRef.current = true
          }
        })
        .catch(() => {})
    }
    api.createAgentOptimizationWebSocket(jobId).then((ws) => {
      if (disposed) {
        try {
          ws.close()
        } catch (error) { void error }
        return
      }
      socket = ws
      optimizationSocketRef.current = socket
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (!payload || !payload.status) {
            return
          }
          const status = payload.status as EvaluationAgentOptimizationStatus
          setActiveOptimization({ jobId, status })
          if (status.state === 'completed') {
            queryClient.invalidateQueries({ queryKey: ['agents', 'list'] })
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
          }
          if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
            optimizationCompletionRef.current = true
            try {
              socket?.close()
            } catch (error) { void error }
          }
        } catch {
          try {
            socket?.close()
          } catch (error) { void error }
        }
      }
      ws.onerror = () => {
        try {
          socket?.close()
        } catch (error) { void error }
      }
      ws.onclose = () => {
        if (optimizationSocketRef.current === socket) {
          optimizationSocketRef.current = null
        }
        if (!disposed && !optimizationCompletionRef.current) {
          pollStatusFallback()
        }
      }
    }).catch(() => {
      if (!disposed) {
        pollStatusFallback()
      }
    })
    return () => {
      disposed = true
      if (optimizationSocketRef.current === socket) {
        optimizationSocketRef.current = null
      }
      if (socket) {
        try {
          socket.close()
        } catch (error) { void error }
      }
    }
  }, [activeOptimization?.jobId, queryClient])

  const isStartingOptimization = startAgentOptimization.isPending
  const optimizationStatus = activeOptimization?.status
  const optimizationState = optimizationStatus?.state
  const optimizationCompleted = optimizationStatus?.completed ?? 0
  const optimizationTotal = optimizationStatus?.total ?? 1
  const optimizationPercent = optimizationState === 'completed' ? 100 : Math.min(100, Math.round((optimizationCompleted / Math.max(1, optimizationTotal)) * 100))
  const optimizationDatasetLabel = optimizationStatus ? `${optimizationStatus.dataset.set_name} • ${optimizationStatus.dataset.category}` : ''
  const optimizationEtaText = formatEta(optimizationStatus?.eta_seconds)

  const openOptimize = () => {
    const fallbackKey = datasetOptions[0]?.key ?? ''
    setOptimizeForm({
      datasetKey: fallbackKey,
      maxMetricCalls: '100',
      seed: '',
    })
    setOptimizeError(datasetOptions.length === 0 ? 'No datasets available. Create a split before optimizing.' : null)
    setIsOptimizeDialogOpen(true)
  }

  const closeOptimizeDialog = () => {
    if (isStartingOptimization) {
      return
    }
    setIsOptimizeDialogOpen(false)
    setOptimizeForm(defaultOptimizeFormState())
    setOptimizeError(null)
  }

  const handleOptimizeSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedAgent) {
      return
    }
    const datasetKey = optimizeForm.datasetKey.trim()
    if (!datasetKey) {
      setOptimizeError('Select a dataset to optimize against')
      return
    }
    const dataset = datasetOptionMap.get(datasetKey)
    if (!dataset) {
      setOptimizeError('Selected dataset is no longer available')
      return
    }
    const trainSplit = dataset.splits.find((split) => split.split === 'train')
    const devSplit = dataset.splits.find((split) => split.split === 'dev')
    const testSplit = dataset.splits.find((split) => split.split === 'test')
    if (!trainSplit || !devSplit || !testSplit) {
      setOptimizeError('Dataset is missing required train/dev/test splits')
      return
    }
    const budgetText = optimizeForm.maxMetricCalls.trim()
    const parsedBudget = Number(budgetText || '0')
    if (!Number.isFinite(parsedBudget) || parsedBudget <= 0 || !Number.isInteger(parsedBudget)) {
      setOptimizeError('Budget must be a positive integer')
      return
    }
    if (parsedBudget < MIN_OPTIMIZATION_BUDGET) {
      setOptimizeError(`Budget must be at least ${MIN_OPTIMIZATION_BUDGET}`)
      return
    }
    const seedText = optimizeForm.seed.trim()
    let parsedSeed: number | undefined
    if (seedText.length > 0) {
      const seedValue = Number(seedText)
      if (!Number.isFinite(seedValue) || !Number.isInteger(seedValue)) {
        setOptimizeError('Seed must be an integer')
        return
      }
      parsedSeed = seedValue
    }
    setOptimizeError(null)
    const payload: EvaluationAgentOptimizationRequest = {
      agent_key: selectedAgent,
      dataset: {
        set_name: dataset.set_name,
        category: dataset.category,
        artifact_dir: dataset.artifact_dir,
        train_path: trainSplit.path,
        dev_path: devSplit.path,
        test_path: testSplit.path,
      },
      max_metric_calls: parsedBudget,
    }
    if (parsedSeed !== undefined) {
      payload.seed = parsedSeed
    }
    startAgentOptimization.mutate(payload)
  }

  const handleDismissOptimization = () => {
    if (optimizationSocketRef.current) {
      try {
        optimizationSocketRef.current.close()
      } catch (error) { void error }
      optimizationSocketRef.current = null
    }
    setActiveOptimization(null)
    optimizationCompletionRef.current = false
  }

  const canStartOptimization =
    !isStartingOptimization &&
    optimizeForm.datasetKey.trim().length > 0 &&
    datasetOptionMap.has(optimizeForm.datasetKey.trim()) &&
    optimizeForm.maxMetricCalls.trim().length > 0

  const agents = data?.agents ?? []
  const activeAgent = useMemo(() => {
    if (!selectedAgent) {
      return null
    }
    return agents.find((agent) => agent.key === selectedAgent) ?? null
  }, [agents, selectedAgent])

  const createAgentMutation = useMutation<AgentCreateResponse, Error, AgentCreatePayload>({
    mutationFn: (payload) => api.createAgent(payload),
    onSuccess: (response) => {
      setIsCreateDialogOpen(false)
      setCreateForm(defaultCreateFormState())
      setCreateError(null)
      queryClient.invalidateQueries({ queryKey: ['agents', 'list'] })
      queryClient.invalidateQueries({ queryKey: ['agents', response.agent.key, 'metadata'] })
      onSelectAgent(response.agent.key)
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Failed to create agent'
      setCreateError(message)
    },
  })

  const deleteAgentMutation = useMutation<AgentDeleteResponse, Error, string>({
    mutationFn: (agentKey) => api.deleteAgent(agentKey),
    onSuccess: (_, agentKey) => {
      setIsDeleteDialogOpen(false)
      setDeleteError(null)
      queryClient.invalidateQueries({ queryKey: ['agents', 'list'] })
      queryClient.invalidateQueries({ queryKey: ['agents', agentKey, 'metadata'] })
      if (selectedAgent === agentKey) {
        onClearAgent()
      }
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Failed to delete agent'
      setDeleteError(message)
    },
  })

  const isCreatingAgent = createAgentMutation.isPending
  const isDeletingAgent = deleteAgentMutation.isPending

  const handleCreateSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    const name = createForm.name.trim()
    if (!name) {
      setCreateError('Enter a name for the new agent')
      return
    }
    const model = createForm.model.trim()
    setCreateError(null)
    createAgentMutation.mutate({ name, model: model || undefined })
  }

  const handleDeleteAgent = () => {
    if (!selectedAgent || isDeletingAgent) {
      return
    }
    deleteAgentMutation.mutate(selectedAgent)
  }

  if (selectedAgent) {
    if (!activeAgent && !isLoading && !isError && agents.length > 0) {
      return (
        <div className="h-full w-full overflow-y-auto p-6">
          <div className="mx-auto flex h-full max-w-4xl flex-col items-center justify-center gap-4 text-center">
            <div className="text-sm text-muted-foreground">The selected agent is no longer available.</div>
            <button
              type="button"
              onClick={onClearAgent}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
            >
              Return to agents
            </button>
          </div>
        </div>
      )
    }
    const agentLabel = activeAgent?.label ?? selectedAgent
    const agentDescription = activeAgent?.description
    return (
      <div className="h-full w-full overflow-y-auto p-6">
        <div className="mx-auto flex h-full max-w-6xl flex-col gap-4">
          <div className="rounded-md border border-border bg-background px-4 py-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase text-muted-foreground">Agent</div>
                <div className="flex items-center gap-2">
                  <div className="text-xl font-semibold">{agentLabel}</div>
                  {activeAgent?.is_entry ? (
                    <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                      Entrypoint
                    </span>
                  ) : null}
                </div>
                {agentDescription ? (
                  <p className="max-w-2xl text-sm text-muted-foreground">{agentDescription}</p>
                ) : null}
                <div className="text-xs text-muted-foreground">
                  {activeAgent?.model ? `Default model: ${activeAgent.model}` : 'No default model specified'}
                </div>
              </div>
              <div className="flex flex-col items-stretch gap-3">
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {isDatasetsLoading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                  <button
                    type="button"
                    onClick={openOptimize}
                    disabled={datasetOptions.length === 0 || isStartingOptimization}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    Optimize
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setDeleteError(null)
                      setIsDeleteDialogOpen(true)
                    }}
                    className="inline-flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/20"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>
          {activeOptimization ? (
            <div className="rounded-md border border-border bg-background px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold">Agent Optimization</div>
                  <div className="text-xs text-muted-foreground">{optimizationDatasetLabel || activeOptimization.jobId}</div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium capitalize">{optimizationState ? optimizationState.replace(/_/g, ' ') : 'pending'}</span>
                  {optimizationState === 'running' ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                  <button
                    type="button"
                    onClick={handleDismissOptimization}
                    className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <div className="mt-3 space-y-2">
                <div>
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      {optimizationCompleted} / {optimizationTotal} complete
                    </span>
                    <span>{optimizationStatus?.updated_at ? new Date(optimizationStatus.updated_at).toLocaleString() : ''}</span>
                  </div>
                  <div className="mt-1 h-2 w-full rounded bg-muted">
                    <div className="h-2 rounded bg-muted-foreground transition-all" style={{ width: `${optimizationPercent}%` }} />
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  Started {optimizationStatus?.started_at ? new Date(optimizationStatus.started_at).toLocaleString() : 'pending'}
                  {optimizationStatus?.finished_at ? ` • Finished ${new Date(optimizationStatus.finished_at).toLocaleString()}` : ''}
                  {optimizationEtaText ? ` • ${optimizationEtaText}` : ''}
                </div>
                {optimizationStatus?.error ? (
                  <div className="text-xs text-destructive">Error: {optimizationStatus.error}</div>
                ) : null}
                {optimizationStatus?.run_dir ? (
                  <div className="text-xs text-muted-foreground">Run directory: {optimizationStatus.run_dir}</div>
                ) : null}
              </div>
            </div>
          ) : null}
          <AgentWorkbench
            key={selectedAgent}
            metadataQueryKey={['agents', selectedAgent, 'metadata']}
            metadataFetcher={() => api.getAgentMetadata(selectedAgent)}
            configureSession={(payload) => api.createAgentSession(selectedAgent, payload)}
            onUpdateContext={onUpdateContext}
            heading={`Configure ${agentLabel}`}
            agentKey={selectedAgent}
            agentLabel={agentLabel}
            containerPadding={false}
          />
          {isOptimizeDialogOpen ? (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
              <form onSubmit={handleOptimizeSubmit} className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg">
                <div className="mb-4 flex items-center justify-between">
                  <div>
                    <h3 className="text-lg font-semibold">Optimize {agentLabel}</h3>
                    <p className="text-sm text-muted-foreground">Select a dataset and budget to optimize this agent&apos;s instructions.</p>
                  </div>
                  <button
                    type="button"
                    onClick={closeOptimizeDialog}
                    className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                    disabled={isStartingOptimization}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="space-y-4">
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Dataset</label>
                    <select
                      value={optimizeForm.datasetKey}
                      onChange={(event) => setOptimizeForm((prev) => ({ ...prev, datasetKey: event.target.value }))}
                      disabled={datasetOptions.length === 0 || isStartingOptimization}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40 disabled:opacity-50"
                    >
                      <option value="">Select dataset</option>
                      {datasetOptions.map((option) => (
                        <option key={option.key} value={option.key}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    {datasetOptions.length === 0 ? (
                      <div className="mt-1 text-xs text-muted-foreground">No datasets available. Create a split before optimizing.</div>
                    ) : null}
                  </div>
                  <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <div>
                      <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Budget</label>
                      <input
                        value={optimizeForm.maxMetricCalls}
                        onChange={(event) => setOptimizeForm((prev) => ({ ...prev, maxMetricCalls: event.target.value }))}
                        type="number"
                        min={1}
                        step={1}
                        placeholder="100"
                        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                      />
                      <div className="mt-1 text-xs text-muted-foreground">{`Maximum evaluation calls during optimization (minimum ${MIN_OPTIMIZATION_BUDGET}).`}</div>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Seed</label>
                      <input
                        value={optimizeForm.seed}
                        onChange={(event) => setOptimizeForm((prev) => ({ ...prev, seed: event.target.value }))}
                        type="number"
                        step={1}
                        placeholder="0"
                        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                      />
                      <div className="mt-1 text-xs text-muted-foreground">Optional. Controls reproducibility.</div>
                    </div>
                  </div>
                </div>
                {optimizeError ? (
                  <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{optimizeError}</div>
                ) : null}
                <div className="mt-6 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={closeOptimizeDialog}
                    className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                    disabled={isStartingOptimization}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                    disabled={!canStartOptimization}
                  >
                    {isStartingOptimization ? 'Starting…' : 'Start Optimization'}
                  </button>
                </div>
              </form>
            </div>
          ) : null}
          {isDeleteDialogOpen ? (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
              <div className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-lg">
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-lg font-semibold">Delete agent</h3>
                  <button
                    type="button"
                    onClick={() => {
                      if (isDeletingAgent) {
                        return
                      }
                      setIsDeleteDialogOpen(false)
                      setDeleteError(null)
                    }}
                    className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <p className="text-sm text-muted-foreground">This will remove the agent directory and instructions from the project.</p>
                {deleteError ? (
                  <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{deleteError}</div>
                ) : null}
                <div className="mt-6 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      if (isDeletingAgent) {
                        return
                      }
                      setIsDeleteDialogOpen(false)
                      setDeleteError(null)
                    }}
                    className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleDeleteAgent}
                    className="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground transition-colors hover:bg-destructive/90 disabled:opacity-50"
                    disabled={isDeletingAgent}
                  >
                    {isDeletingAgent ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div className="h-full w-full overflow-y-auto p-6">
      <div className="mx-auto flex h-full max-w-6xl flex-col gap-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">Agents</h2>
            <p className="text-sm text-muted-foreground">Select an agent to configure its defaults, tools, and guardrails.</p>
          </div>
          <button
            type="button"
            onClick={() => {
              setCreateForm(defaultCreateFormState())
              setCreateError(null)
              setIsCreateDialogOpen(true)
            }}
            className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
          >
            <Plus className="h-3.5 w-3.5" />
            New agent
          </button>
        </div>
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Loading agents…
          </div>
        ) : isError ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
            <span>Unable to load agents.</span>
            <button
              type="button"
              onClick={() => refetch()}
              className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
            >
              Retry
            </button>
          </div>
        ) : agents.length === 0 ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            No agents discovered in this project.
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <button
                key={agent.key}
                type="button"
                onClick={() => onSelectAgent(agent.key)}
                className="flex h-full flex-col rounded-md border border-border bg-background p-4 text-left transition-shadow hover:shadow"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <Bot className="h-4 w-4" />
                    </div>
                    <div className="space-y-1">
                      <div className="text-sm font-semibold">{agent.label}</div>
                      {agent.is_entry ? (
                        <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                          Entrypoint
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </div>
                {agent.description ? (
                  <p className="mb-3 text-xs text-muted-foreground">{agent.description}</p>
                ) : null}
                <div className="mt-auto text-xs text-muted-foreground">
                  {agent.model ? `Default model: ${agent.model}` : 'No default model specified'}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      {isCreateDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form onSubmit={handleCreateSubmit} className="w-full max-w-sm rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold">Create agent</h3>
              <button
                type="button"
                onClick={() => {
                  if (isCreatingAgent) {
                    return
                  }
                  setIsCreateDialogOpen(false)
                  setCreateError(null)
                }}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground" htmlFor="new-agent-name">
                  Name
                </label>
                <input
                  id="new-agent-name"
                  value={createForm.name}
                  onChange={(event) => {
                    setCreateForm((prev) => ({ ...prev, name: event.target.value }))
                    setCreateError(null)
                  }}
                  placeholder="support"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground" htmlFor="new-agent-model">
                  Model
                </label>
                <input
                  id="new-agent-model"
                  value={createForm.model}
                  onChange={(event) => setCreateForm((prev) => ({ ...prev, model: event.target.value }))}
                  placeholder="gpt-4.1"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
                <div className="mt-1 text-xs text-muted-foreground">Leave blank to use the default model.</div>
              </div>
            </div>
            {createError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{createError}</div>
            ) : null}
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  if (isCreatingAgent) {
                    return
                  }
                  setIsCreateDialogOpen(false)
                  setCreateError(null)
                }}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={isCreatingAgent}
              >
                {isCreatingAgent ? 'Creating…' : 'Create'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  )
}
