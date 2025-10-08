import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Edit3, Trash2, Loader2, X, Upload, FileText, Clock, ArrowLeft, Play, Sparkles, Database, History, RefreshCw, HardDrive } from 'lucide-react'
import { api } from '../../lib/api'
import type {
  EvaluationDimension,
  EvaluationDimensionValue,
  EvaluationScenarioSet,
  EvaluationScenarioPromotionPayload,
  EvaluationScenarioCreatePayload,
  EvaluationScenarioUpdatePayload,
  EvaluationScenarioDeletePayload,
  EvaluationScenarioMutationResponse,
  EvaluationScenarioRunRequest,
  EvaluationScenarioRunStatus,
  EvaluationScenarioGenerationStatus,
  EvaluationDataset,
  EvaluationDatasetSourceKind,
  EvaluationScenarioAnnotatePayload,
  EvaluationScenarioAnnotateResponse,
  EvaluationScenarioSetDeletePayload,
  EvaluationScenarioSetDeleteResponse,
} from '../../types'

type EvaluationTab = 'dimensions' | 'scenarios' | 'datasets'

interface EvaluationViewProps {
  activeTab: EvaluationTab
  onTabChange: (tab: EvaluationTab) => void
  projectName?: string | null
  isAvailable: boolean
  isLoadingMetadata: boolean
}

function DatasetsPanel({ projectName, onTabChange }: { projectName?: string | null; onTabChange: (tab: EvaluationTab) => void }) {
  const { data, isLoading, isError, refetch, isFetching } = useEvaluationDatasets(true)
  const datasets = useMemo(() => data?.datasets ?? [], [data])
  const resolvedProjectName = projectName ?? data?.project ?? null
  const datasetCount = datasets.length
  const totalRuns = useMemo(() => datasets.reduce((acc, item) => acc + (item.run_count ?? 0), 0), [datasets])
  const sourceLabels: Record<EvaluationDatasetSourceKind, string> = {
    golden: 'Golden',
    generated: 'Generated',
    annotated: 'Annotated',
    unknown: 'Unknown',
  }
  const groups = useMemo(
    () => {
      const bucket: Record<EvaluationDatasetSourceKind, EvaluationDataset[]> = {
        golden: [],
        generated: [],
        annotated: [],
        unknown: [],
      }
      datasets.forEach((dataset) => {
        bucket[dataset.source_kind].push(dataset)
      })
      return [
        { key: 'golden', title: 'Golden Runs', items: bucket.golden },
        { key: 'generated', title: 'Generated Set Runs', items: bucket.generated },
        { key: 'annotated', title: 'Annotated Set Runs', items: bucket.annotated },
        { key: 'unknown', title: 'Other Runs', items: bucket.unknown },
      ].filter((group) => group.items.length > 0)
    },
    [datasets]
  )

  const formatFileSize = (size?: number | null): string => {
    if (!size || size <= 0) {
      return 'Unknown size'
    }
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    const exponent = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)))
    const value = size / 1024 ** exponent
    return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}`
  }

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3 rounded-md border border-border bg-muted/40 px-6 py-5 text-sm text-muted-foreground">
          <span>Unable to load datasets{resolvedProjectName ? ` for ${resolvedProjectName}` : ''}.</span>
          <button
            type="button"
            onClick={() => refetch()}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry
          </button>
        </div>
      </div>
    )
  }

  if (datasetCount === 0) {
    return (
      <div className="flex h-full w-full items-center justify-center px-6">
        <div className="flex flex-col items-center gap-4 rounded-md border border-dashed border-border bg-muted/30 px-6 py-5 text-center text-sm text-muted-foreground">
          <span>No datasets yet. Run a scenario set to capture outputs.</span>
          <button
            type="button"
            onClick={() => onTabChange('scenarios')}
            className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
          >
            View Scenario Sets
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">Datasets</h2>
          <p className="text-sm text-muted-foreground">
            {resolvedProjectName ? `Scenario run artifacts for ${resolvedProjectName}.` : 'Review saved runs from scenario executions.'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted-foreground">
            {totalRuns} run{totalRuns === 1 ? '' : 's'} across {datasetCount} dataset{datasetCount === 1 ? '' : 's'}
          </div>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
          <button
            type="button"
            onClick={() => refetch()}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        <div className="space-y-6">
          {groups.map((group) => {
            const groupRunCount = group.items.reduce((acc, item) => acc + (item.run_count ?? 0), 0)
            return (
              <div key={group.key} className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold uppercase text-muted-foreground">{group.title}</h3>
                  <span className="text-xs text-muted-foreground">
                    {groupRunCount} run{groupRunCount === 1 ? '' : 's'}
                  </span>
                </div>
                {group.items.map((dataset) => (
                  <div key={dataset.path} className="space-y-3 rounded-lg border border-border bg-background p-4">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-2">
                          <Database className="h-4 w-4 text-muted-foreground" />
                        <span className="text-sm font-semibold">{dataset.label}</span>
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                        <FileText className="h-3.5 w-3.5" />
                        <span className="font-mono">{formatScenarioPath(dataset.path)}</span>
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        Source: {sourceLabels[dataset.source_kind]}
                        {dataset.source_label ? ` • ${dataset.source_label}` : ''}
                      </div>
                    </div>
                    <div className="text-right text-xs text-muted-foreground">
                      {dataset.updated_at ? <div>Updated {formatTimestamp(dataset.updated_at)}</div> : null}
                      {dataset.created_at && dataset.created_at !== dataset.updated_at ? <div>Created {formatTimestamp(dataset.created_at)}</div> : null}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                    <div className="flex items-center gap-1">
                      <Database className="h-3.5 w-3.5" />
                      <span>
                        {dataset.run_count} run{dataset.run_count === 1 ? '' : 's'}
                      </span>
                    </div>
                    {typeof dataset.expected_count === 'number' ? (
                      <div className="flex items-center gap-1">
                        <Sparkles className="h-3.5 w-3.5" />
                        <span>Expected {dataset.expected_count}</span>
                      </div>
                    ) : null}
                    {typeof dataset.scenario_count === 'number' ? (
                      <div className="flex items-center gap-1">
                        <FileText className="h-3.5 w-3.5" />
                        <span>
                          {dataset.scenario_count} scenario{dataset.scenario_count === 1 ? '' : 's'}
                        </span>
                      </div>
                    ) : null}
                    <div className="flex items-center gap-1">
                      <History className="h-3.5 w-3.5" />
                      <span>{dataset.includes_history ? 'History included' : 'History skipped'}</span>
                    </div>
                    {typeof dataset.size_bytes === 'number' ? (
                      <div className="flex items-center gap-1">
                        <HardDrive className="h-3.5 w-3.5" />
                        <span>{formatFileSize(dataset.size_bytes)}</span>
                      </div>
                    ) : null}
                  </div>
                  {dataset.workflow_run ? (
                    <div className="text-xs text-muted-foreground">
                      Workflow run <span className="font-mono">{dataset.workflow_run}</span>
                      {dataset.workflow_run_at ? ` • ${formatTimestamp(dataset.workflow_run_at)}` : ''}
                    </div>
                  ) : null}
                  {dataset.workflow_generate ? (
                    <div className="text-xs text-muted-foreground">
                      Generated via <span className="font-mono">{dataset.workflow_generate}</span>
                    </div>
                  ) : null}
                  {dataset.source_path ? (
                    <div className="text-xs text-muted-foreground">
                      Scenario file <span className="font-mono">{formatScenarioPath(dataset.source_path)}</span>
                    </div>
                  ) : null}
                  {dataset.manifest_path ? (
                    <div className="text-xs text-muted-foreground">
                      Manifest <span className="font-mono">{formatScenarioPath(dataset.manifest_path)}</span>
                    </div>
                  ) : null}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onTabChange('scenarios')}
                      className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
                    >
                      View Scenario Sets
                    </button>
                  </div>
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

interface FormValue extends Omit<EvaluationDimensionValue, 'examples'> {
  examplesText: string
}

interface FormDimension {
  name: string
  label: string
  description: string
  values: FormValue[]
}

interface ScenarioFormState {
  id: string
  name: string
  prompt: string
  dimensionsText: string
}

const defaultScenarioForm = (): ScenarioFormState => ({
  id: '',
  name: '',
  prompt: '',
  dimensionsText: '',
})

const defaultValue = (): FormValue => ({
  name: '',
  label: '',
  description: '',
  examplesText: '',
})

const defaultDimension = (): FormDimension => ({
  name: '',
  label: '',
  description: '',
  values: [defaultValue()],
})

function useDimensions(enabled: boolean) {
  return useQuery({
    queryKey: ['evaluation', 'dimensions'],
    queryFn: () => api.getEvaluationDimensions(),
    enabled,
  })
}

function useEvaluationScenarios(enabled: boolean) {
  return useQuery({
    queryKey: ['evaluation', 'scenarios'],
    queryFn: () => api.getEvaluationScenarios(),
    enabled,
  })
}

function useEvaluationDatasets(enabled: boolean) {
  return useQuery({
    queryKey: ['evaluation', 'datasets'],
    queryFn: () => api.getEvaluationDatasets(),
    enabled,
  })
}

type ScenarioItem = EvaluationScenarioSet['scenarios'][number]

function scenarioKey(set: EvaluationScenarioSet, scenario: ScenarioItem) {
  if (typeof scenario.metadata?.index === 'number') {
    return `${set.path}:${scenario.metadata.index}`
  }
  if (scenario.id) {
    return `${set.path}:${scenario.id}`
  }
  return `${set.path}:${scenario.name}`
}

function formatScenarioPath(path: string): string {
  if (!path) {
    return ''
  }
  const normalized = path.replace(/\\/g, '/').trim()
  const markers = ['evaluations/', 'artifacts/']
  for (const marker of markers) {
    const idx = normalized.lastIndexOf(marker)
    if (idx >= 0) {
      const suffix = normalized.slice(idx)
      return suffix.startsWith('/') ? suffix : `/${suffix}`
    }
  }
  const segments = normalized.split('/').filter(Boolean)
  if (segments.length >= 3) {
    const condensed = segments.slice(-3).join('/')
    return `/${condensed}`
  }
  return normalized.startsWith('/') ? normalized : `/${normalized}`
}

function formatTimestamp(value?: string | null): string {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

interface ScenarioSetDetailProps {
  set: EvaluationScenarioSet
  onBack: () => void
  projectName?: string | null
  canPromote: boolean
  onPromote: (scenario: ScenarioItem) => void
  pendingKey: string | null
  isPromoting: boolean
  goldenIds: Set<string>
  goldenNames: Set<string>
  runTrigger?: ScenarioRunTrigger | null
  onRunTriggerConsumed?: () => void
  onDeleteSet?: (set: EvaluationScenarioSet) => void
  isDeletingSet?: boolean
}

interface ScenarioRunTrigger {
  path: string
  token: number
}

function ScenarioSetDetail({
  set,
  onBack,
  projectName,
  canPromote,
  onPromote,
  pendingKey,
  isPromoting,
  goldenIds,
  goldenNames,
  runTrigger,
  onRunTriggerConsumed,
  onDeleteSet,
  isDeletingSet,
}: ScenarioSetDetailProps) {
  const queryClient = useQueryClient()
  const [isFormOpen, setIsFormOpen] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formState, setFormState] = useState<ScenarioFormState>(defaultScenarioForm)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)
  const [isRunDialogOpen, setIsRunDialogOpen] = useState(false)
  const [isStartingRun, setIsStartingRun] = useState(false)
  const [runMaxTurns, setRunMaxTurns] = useState('')
  const [runIncludeHistory, setRunIncludeHistory] = useState(true)
  const [runError, setRunError] = useState<string | null>(null)
  const [activeRun, setActiveRun] = useState<{ jobId: string; status: EvaluationScenarioRunStatus | null } | null>(null)
  const runSocketRef = useRef<WebSocket | null>(null)
  const activeRunRef = useRef<typeof activeRun>(null)
  const runCompletionRef = useRef(false)
  const [annotateBanner, setAnnotateBanner] = useState<{ variant: 'success' | 'error'; message: string } | null>(null)

  useEffect(() => {
    activeRunRef.current = activeRun
  }, [activeRun])

  useEffect(() => {
    setAnnotateBanner(null)
  }, [set.path])

  const createScenario = useMutation<EvaluationScenarioMutationResponse, Error, EvaluationScenarioCreatePayload>({
    mutationFn: (payload) => api.createEvaluationScenario(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
      setIsFormOpen(false)
      setEditingKey(null)
      setFormState(defaultScenarioForm())
      setFormError(null)
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to save scenario'
      setFormError(message)
    },
  })

  const updateScenario = useMutation<EvaluationScenarioMutationResponse, Error, EvaluationScenarioUpdatePayload>({
    mutationFn: (payload) => api.updateEvaluationScenario(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
      setIsFormOpen(false)
      setEditingKey(null)
      setFormState(defaultScenarioForm())
      setFormError(null)
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to update scenario'
      setFormError(message)
    },
  })

  const deleteScenario = useMutation<EvaluationScenarioMutationResponse, Error, EvaluationScenarioDeletePayload>({
    mutationFn: (payload) => api.deleteEvaluationScenario(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to delete scenario'
      window.alert(message)
    },
    onSettled: () => {
      setDeletingKey(null)
    },
  })

  const annotateScenario = useMutation<EvaluationScenarioAnnotateResponse, Error, EvaluationScenarioAnnotatePayload>({
    mutationFn: (payload) => api.annotateEvaluationScenario(payload),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
      const formattedPath = formatScenarioPath(response.annotated_path)
      setAnnotateBanner({ variant: 'success', message: `Annotated scenarios saved to ${formattedPath}` })
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to annotate scenarios'
      setAnnotateBanner({ variant: 'error', message })
    },
  })

  const isSaving = createScenario.isPending || updateScenario.isPending
  const runState = activeRun?.status?.state ?? null
  const isRunActive = runState === 'pending' || runState === 'running'
  const totalForProgress = activeRun?.status?.total ?? set.scenarios.length
  const completedForProgress = activeRun?.status?.completed ?? 0
  const progressPercent = totalForProgress > 0 ? Math.min(100, Math.round((completedForProgress / totalForProgress) * 100)) : 0

  const openRunDialog = () => {
    if (isRunActive) {
      return
    }
    setRunMaxTurns('')
    setRunIncludeHistory(true)
    setRunError(null)
    setIsRunDialogOpen(true)
  }

  const closeRunDialog = () => {
    if (isStartingRun) {
      return
    }
    setIsRunDialogOpen(false)
    setRunError(null)
  }

  const handleDismissRun = () => {
    if (runSocketRef.current) {
      runSocketRef.current.close()
      runSocketRef.current = null
    }
    setActiveRun(null)
    runCompletionRef.current = false
  }

  const handleRunSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (isStartingRun) {
      return
    }
    const trimmed = runMaxTurns.trim()
    let parsedMax: number | undefined
    if (trimmed.length > 0) {
      const parsed = Number(trimmed)
      if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed <= 0) {
        setRunError('Max turns must be a positive integer')
        return
      }
      parsedMax = parsed
    }
    const payload: EvaluationScenarioRunRequest = {
      kind: set.kind,
      set_path: set.path,
      include_history: runIncludeHistory,
    }
    if (parsedMax !== undefined) {
      payload.max_turns = parsedMax
    }
    setIsStartingRun(true)
    setRunError(null)
    try {
      const response = await api.startEvaluationScenarioRun(payload)
      const status = response.status ?? null
      setActiveRun({ jobId: response.job_id, status })
      runCompletionRef.current = false
      setIsRunDialogOpen(false)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to start scenario run'
      setRunError(message)
    } finally {
      setIsStartingRun(false)
    }
  }

  const handleAnnotateSet = () => {
    if (annotateScenario.isPending) {
      return
    }
    const targetKind: EvaluationScenarioAnnotatePayload['kind'] = set.kind === 'annotated' ? 'annotated' : 'generated'
    setAnnotateBanner(null)
    annotateScenario.mutate({ kind: targetKind, set_path: set.path })
  }

  const handleDeleteSet = () => {
    if (!onDeleteSet || set.kind === 'golden' || isDeletingSet) {
      return
    }
    onDeleteSet(set)
  }

  useEffect(() => {
    if (!runTrigger || runTrigger.path !== set.path) {
      return
    }
    if (!isRunActive) {
      openRunDialog()
    }
    if (onRunTriggerConsumed) {
      onRunTriggerConsumed()
    }
  }, [runTrigger?.token, runTrigger?.path, set.path, isRunActive, onRunTriggerConsumed])

  const openCreate = () => {
    setFormState(defaultScenarioForm())
    setEditingKey(null)
    setFormError(null)
    setIsFormOpen(true)
  }

  const openEdit = (scenario: ScenarioItem) => {
    setEditingKey(scenarioKey(set, scenario))
    setFormState({
      id: scenario.id || '',
      name: scenario.name,
      prompt: scenario.prompt || '',
      dimensionsText:
        scenario.dimensions && Object.keys(scenario.dimensions).length > 0
          ? JSON.stringify(scenario.dimensions, null, 2)
          : '',
    })
    setFormError(null)
    setIsFormOpen(true)
  }

  const closeForm = () => {
    if (isSaving) {
      return
    }
    setIsFormOpen(false)
    setEditingKey(null)
    setFormState(defaultScenarioForm())
    setFormError(null)
  }

  const handleDelete = (scenario: ScenarioItem) => {
    if (deleteScenario.isPending) {
      return
    }
    if (!window.confirm(`Delete scenario "${scenario.name}"?`)) {
      return
    }
    const key = scenarioKey(set, scenario)
    setDeletingKey(key)
    deleteScenario.mutate({
      kind: set.kind,
      set_path: set.path,
      scenario_id: scenario.id ?? undefined,
      scenario_name: scenario.name,
      index: typeof scenario.metadata?.index === 'number' ? scenario.metadata.index : undefined,
    })
  }

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (isSaving) {
      return
    }
    const name = formState.name.trim()
    if (!name) {
      setFormError('Scenario name is required')
      return
    }
    const prompt = formState.prompt.trim()
    if (!prompt) {
      setFormError('Scenario prompt is required')
      return
    }

    let dimensions: Record<string, unknown> | null = null
    if (formState.dimensionsText.trim().length > 0) {
      try {
        const parsed = JSON.parse(formState.dimensionsText)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setFormError('Dimensions must be a JSON object')
          return
        }
        dimensions = parsed as Record<string, unknown>
      } catch {
        setFormError('Dimensions must be valid JSON')
        return
      }
    }

    const scenarioPayload: EvaluationScenarioCreatePayload['scenario'] = {
      id: formState.id.trim().length > 0 ? formState.id.trim() : null,
      name,
      prompt,
      dimensions,
    }

    setFormError(null)

    if (editingKey) {
      const target = set.scenarios.find((item) => scenarioKey(set, item) === editingKey)
      if (!target) {
        setFormError('Unable to locate scenario for editing')
        return
      }
      updateScenario.mutate({
        kind: set.kind,
        set_path: set.path,
        scenario: scenarioPayload,
        scenario_id: target.id ?? undefined,
        scenario_name: target.name,
        index: typeof target.metadata?.index === 'number' ? target.metadata.index : undefined,
      })
    } else {
      createScenario.mutate({
        kind: set.kind,
        set_path: set.path,
        scenario: scenarioPayload,
      })
    }
  }

  useEffect(() => {
    if (!activeRun) {
      if (runSocketRef.current) {
        runSocketRef.current.close()
        runSocketRef.current = null
      }
      return
    }
    const jobId = activeRun.jobId
    let socket: WebSocket | null = null
    try {
      socket = api.createScenarioRunWebSocket(jobId)
      runSocketRef.current = socket
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (!payload || payload.job_id !== jobId) {
            return
          }
          const statusPayload = payload.status as EvaluationScenarioRunStatus | null | undefined
          if (statusPayload !== undefined) {
            setActiveRun((prev) => (prev && prev.jobId === jobId ? { jobId, status: statusPayload ?? null } : prev))
          }
          const nextState: string | undefined = statusPayload?.state
          if (nextState === 'completed' || nextState === 'failed' || nextState === 'cancelled') {
            if (socket) {
              socket.close()
            }
          }
        } catch {
          return
        }
      }
      socket.onclose = () => {
        if (runSocketRef.current === socket) {
          runSocketRef.current = null
        }
        const current = activeRunRef.current
        if (!current || current.jobId !== jobId) {
          return
        }
        const currentState = current.status?.state
        if (currentState === 'completed' || currentState === 'failed' || currentState === 'cancelled') {
          return
        }
        api.getEvaluationScenarioRunStatus(jobId)
          .then((res) => {
            if (res && res.job_id === jobId) {
              setActiveRun((prev) => (prev && prev.jobId === jobId ? { jobId, status: res.status ?? null } : prev))
            }
          })
          .catch(() => {})
      }
      socket.onerror = () => {}
    } catch {
      api.getEvaluationScenarioRunStatus(jobId)
        .then((res) => {
          if (res && res.job_id === jobId) {
            setActiveRun((prev) => (prev && prev.jobId === jobId ? { jobId, status: res.status ?? null } : prev))
          }
        })
        .catch(() => {})
    }
    return () => {
      if (runSocketRef.current === socket && socket) {
        runSocketRef.current = null
      }
      if (socket) {
        try {
          socket.close()
        } catch {
        }
      }
    }
  }, [activeRun?.jobId])

  useEffect(() => {
    const status = activeRun?.status
    if (!status) {
      return
    }
    if (status.state === 'completed') {
      if (!runCompletionRef.current) {
        runCompletionRef.current = true
        queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
        queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
      }
    } else if (status.state === 'failed' || status.state === 'cancelled') {
      runCompletionRef.current = true
    }
  }, [activeRun?.status?.state, queryClient])

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <h2 className="text-lg font-semibold">{set.label}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <div className="flex items-center gap-1">
                <FileText className="h-3.5 w-3.5" />
                <span className="font-mono">{formatScenarioPath(set.path)}</span>
              </div>
              <span>•</span>
              <span>{set.updated_at ? new Date(set.updated_at).toLocaleString() : 'Unknown updated time'}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-xs text-muted-foreground">
            {set.scenarios.length} scenario{set.scenarios.length === 1 ? '' : 's'}
          </div>
          {set.kind !== 'golden' ? (
            <button
              type="button"
              onClick={handleAnnotateSet}
              disabled={annotateScenario.isPending}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {annotateScenario.isPending ? 'Annotating…' : 'Annotate Failures'}
            </button>
          ) : null}
          {set.kind !== 'golden' ? (
            <button
              type="button"
              onClick={handleDeleteSet}
              disabled={Boolean(isDeletingSet)}
              className="inline-flex items-center gap-1 rounded-md border border-destructive px-3 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {isDeletingSet ? 'Deleting…' : 'Delete Set'}
            </button>
          ) : null}
          <button
            type="button"
            onClick={openRunDialog}
            disabled={isStartingRun || isRunActive}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" />
            {isRunActive ? 'Running…' : 'Run Set'}
          </button>
          <button
            type="button"
            onClick={openCreate}
            disabled={isSaving}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Scenario
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        {annotateBanner ? (
          <div
            className={`mb-4 rounded-md border px-4 py-3 text-xs ${
              annotateBanner.variant === 'success'
                ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-600'
                : 'border-destructive/60 bg-destructive/10 text-destructive'
            }`}
          >
            {annotateBanner.message}
          </div>
        ) : null}
        {activeRun ? (
          <div className="mb-4 rounded-md border border-border bg-background px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">Scenario Run</div>
                <div className="text-xs text-muted-foreground">{activeRun.jobId}</div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium capitalize">
                  {activeRun.status?.state ? activeRun.status.state.replace(/_/g, ' ') : 'pending'}
                </span>
                {isRunActive ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                <button
                  type="button"
                  onClick={handleDismissRun}
                  className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            {activeRun.status ? (
              <div className="mt-3 space-y-2">
                <div>
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      {completedForProgress} / {totalForProgress} complete
                    </span>
                    <span>{formatTimestamp(activeRun.status.updated_at)}</span>
                  </div>
                  <div className="mt-1 h-2 w-full rounded bg-muted">
                    <div
                      className="h-2 rounded bg-muted-foreground transition-all"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                </div>
                <div className="text-xs text-muted-foreground">
                  Include history {activeRun.status.include_history ? 'enabled' : 'disabled'}
                  {activeRun.status.max_turns ? ` • Max turns ${activeRun.status.max_turns}` : ''}
                </div>
                <div className="text-xs text-muted-foreground">
                  Started {formatTimestamp(activeRun.status.started_at) || 'pending'}
                  {activeRun.status.finished_at ? ` • Finished ${formatTimestamp(activeRun.status.finished_at)}` : ''}
                </div>
                {activeRun.status.runs_path ? (
                  <div className="text-xs text-muted-foreground">
                    Runs written to <span className="font-mono">{formatScenarioPath(activeRun.status.runs_path)}</span>
                  </div>
                ) : null}
                {activeRun.status.workflow_name ? (
                  <div className="text-xs text-muted-foreground">
                    Workflow <span className="font-mono">{activeRun.status.workflow_name}</span>
                  </div>
                ) : null}
                {activeRun.status.error ? (
                  <div className="rounded border border-destructive/60 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                    {activeRun.status.error}
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="mt-3 text-xs text-muted-foreground">Waiting for status…</div>
            )}
          </div>
        ) : null}
        {set.scenarios.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-border p-8 text-sm text-muted-foreground">
            No scenarios in this set yet.
          </div>
        ) : (
          <div className="space-y-3">
            {set.scenarios.map((scenario) => {
              const key = scenarioKey(set, scenario)
              const isDuplicate = (scenario.id && goldenIds.has(scenario.id)) || (!scenario.id && goldenNames.has(scenario.name))
              const isPromotePending = isPromoting && pendingKey === key
              const isDeletingCurrent = deleteScenario.isPending && deletingKey === key
              return (
                <div key={key} className="rounded-md border border-border bg-background px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold">{scenario.name}</div>
                      {scenario.id ? <div className="text-xs text-muted-foreground">ID: {scenario.id}</div> : null}
                      {typeof scenario.metadata?.index === 'number' ? <div className="text-xs text-muted-foreground">Index: {scenario.metadata.index}</div> : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {canPromote ? (
                        <button
                          type="button"
                          onClick={() => onPromote(scenario)}
                          disabled={isDuplicate || isPromoting}
                          className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-50"
                        >
                          <Upload className="h-3.5 w-3.5" />
                          {isPromotePending ? 'Promoting…' : isDuplicate ? 'Already promoted' : 'Promote to Golden'}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => openEdit(scenario)}
                        disabled={isSaving || deleteScenario.isPending}
                        className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(scenario)}
                        disabled={deleteScenario.isPending}
                        className="inline-flex items-center gap-1 rounded-md border border-destructive px-3 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {isDeletingCurrent ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>
                  </div>
                  {scenario.prompt ? <p className="mt-2 text-sm text-muted-foreground">{scenario.prompt}</p> : null}
                  {scenario.dimensions && Object.keys(scenario.dimensions).length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {Object.entries(scenario.dimensions).map(([key, value]) => (
                        <span key={key} className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                          {key}: {String(value)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        )}
      </div>
      {isRunDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form
            onSubmit={handleRunSubmit}
            className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg"
          >
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Run Scenario Set</h3>
                <p className="text-sm text-muted-foreground">Execute all scenarios and record agent outputs.</p>
              </div>
              <button
                type="button"
                onClick={closeRunDialog}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                disabled={isStartingRun}
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Max Turns</label>
                <input
                  type="number"
                  min={1}
                  value={runMaxTurns}
                  onChange={(event) => setRunMaxTurns(event.target.value)}
                  placeholder="Use project default"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={runIncludeHistory}
                  onChange={(event) => setRunIncludeHistory(event.target.checked)}
                  className="h-4 w-4 rounded border-border text-primary focus:ring-primary/50"
                />
                Include conversation history in the saved runs file
              </label>
              <div className="text-xs text-muted-foreground">
                Runs will be stored alongside this scenario file.
              </div>
            </div>

            {runError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {runError}
              </div>
            ) : null}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeRunDialog}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                disabled={isStartingRun}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={isStartingRun}
              >
                {isStartingRun ? 'Starting…' : 'Start Run'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {isFormOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form
            onSubmit={handleSubmit}
            className="w-full max-w-2xl rounded-lg border border-border bg-background p-6 shadow-lg"
          >
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">{editingKey ? 'Edit Scenario' : 'Add Scenario'}</h3>
                <p className="text-sm text-muted-foreground">
                  Define prompt content and optional dimensions for this scenario.
                </p>
              </div>
              <button
                type="button"
                onClick={closeForm}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                disabled={isSaving}
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Name</label>
                  <input
                    value={formState.name}
                    onChange={(event) => setFormState((prev) => ({ ...prev, name: event.target.value }))}
                    placeholder="scenario_name"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">ID (optional)</label>
                  <input
                    value={formState.id}
                    onChange={(event) => setFormState((prev) => ({ ...prev, id: event.target.value }))}
                    placeholder="unique-id"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Prompt</label>
                <textarea
                  value={formState.prompt}
                  onChange={(event) => setFormState((prev) => ({ ...prev, prompt: event.target.value }))}
                  rows={6}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Dimensions JSON (optional)</label>
                <textarea
                  value={formState.dimensionsText}
                  onChange={(event) => setFormState((prev) => ({ ...prev, dimensionsText: event.target.value }))}
                  rows={4}
                  placeholder='{"audience": "students"}'
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
                <p className="mt-1 text-xs text-muted-foreground">Provide a JSON object. Leave empty to remove dimensions.</p>
              </div>
            </div>

            {formError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {formError}
              </div>
            ) : null}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeForm}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                disabled={isSaving}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={isSaving}
              >
                {isSaving ? 'Saving…' : editingKey ? 'Save Changes' : 'Add Scenario'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  )
}

function DimensionsPanel({ projectName }: { projectName?: string | null }) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, refetch } = useDimensions(true)
  const [isFormOpen, setIsFormOpen] = useState(false)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [form, setForm] = useState<FormDimension>(defaultDimension)
  const [formError, setFormError] = useState<string | null>(null)

  const dimensions = useMemo(() => data?.dimensions ?? [], [data])
  const resolvedProjectName = projectName ?? data?.project ?? null

  const mutation = useMutation({
    mutationFn: (nextDimensions: EvaluationDimension[]) => api.updateEvaluationDimensions(nextDimensions),
    onSuccess: (payload) => {
      queryClient.setQueryData(['evaluation', 'dimensions'], payload)
      setIsFormOpen(false)
      setEditingIndex(null)
      setForm(defaultDimension())
      setFormError(null)
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to save dimensions'
      setFormError(message)
    },
  })

  const openCreate = () => {
    setForm(defaultDimension())
    setEditingIndex(null)
    setIsFormOpen(true)
    setFormError(null)
  }

  const openEdit = (index: number) => {
    const target = dimensions[index]
    if (!target) return
    const values: FormValue[] = (target.values || []).map((value) => ({
      name: value.name || '',
      label: value.label || '',
      description: value.description || '',
      examplesText: (value.examples || []).join('\n'),
    }))
    setForm({
      name: target.name || '',
      label: target.label || '',
      description: target.description || '',
      values: values.length ? values : [defaultValue()],
    })
    setEditingIndex(index)
    setIsFormOpen(true)
    setFormError(null)
  }

  const closeForm = () => {
    setIsFormOpen(false)
    setEditingIndex(null)
    setForm(defaultDimension())
    setFormError(null)
  }

  const handleValueChange = (index: number, field: keyof FormValue, value: string) => {
    setForm((prev) => {
      const nextValues = prev.values.map((item, idx) => (idx === index ? { ...item, [field]: value } : item))
      return { ...prev, values: nextValues }
    })
  }

  const addValueRow = () => {
    setForm((prev) => ({ ...prev, values: [...prev.values, defaultValue()] }))
  }

  const removeValueRow = (index: number) => {
    setForm((prev) => ({
      ...prev,
      values: prev.values.length > 1 ? prev.values.filter((_, idx) => idx !== index) : prev.values,
    }))
  }

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmedName = form.name.trim()
    if (!trimmedName) {
      setFormError('Dimension name is required')
      return
    }
    const duplicateIndex = dimensions.findIndex((dimension) => dimension.name === trimmedName)
    if ((editingIndex === null && duplicateIndex !== -1) || (editingIndex !== null && duplicateIndex !== -1 && duplicateIndex !== editingIndex)) {
      setFormError('A dimension with this name already exists')
      return
    }

    const next: EvaluationDimension = {
      name: trimmedName,
      label: form.label.trim() || undefined,
      description: form.description.trim() || undefined,
      values: form.values
        .map((value) => ({
          name: value.name.trim(),
          label: value.label.trim() || undefined,
          description: value.description.trim() || undefined,
          examples: value.examplesText
            .split('\n')
            .map((line) => line.trim())
            .filter((line) => line.length > 0),
        }))
        .filter((value) => value.name.length > 0),
    }
    if (!next.values.length) {
      setFormError('At least one value is required')
      return
    }
    const nextDimensions = [...dimensions]
    if (editingIndex === null) {
      nextDimensions.push(next)
    } else {
      nextDimensions[editingIndex] = next
    }
    mutation.mutate(nextDimensions)
  }

  const handleDelete = (index: number) => {
    const nextDimensions = dimensions.filter((_, idx) => idx !== index)
    mutation.mutate(nextDimensions)
  }

  useEffect(() => {
    if (!isFormOpen) {
      setForm(defaultDimension())
      setFormError(null)
    }
  }, [isFormOpen])

  if (isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
        <span>Unable to load dimensions.</span>
        <button
          type="button"
          onClick={() => refetch()}
          className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
        >
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">Synthetic Dimensions</h2>
          <p className="text-sm text-muted-foreground">
            {resolvedProjectName ? `Configure personas for ${resolvedProjectName}.` : 'Define personas used when generating scenarios.'}
          </p>
        </div>
        <button
          type="button"
          onClick={openCreate}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Add Dimension
        </button>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        {dimensions.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-border p-8 text-sm text-muted-foreground">
            No dimensions configured yet.
          </div>
        ) : (
          <div className="space-y-4">
            {dimensions.map((dimension, index) => (
              <div key={dimension.name || index} className="rounded-md border border-border bg-background">
                <div className="flex items-center justify-between border-b border-border px-4 py-3">
                  <div>
                    <div className="text-sm font-semibold">{dimension.label || dimension.name}</div>
                    <div className="text-xs text-muted-foreground">Key: {dimension.name}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => openEdit(index)}
                      className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-muted"
                    >
                      <Edit3 className="h-3.5 w-3.5" />
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(index)}
                      className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Delete
                    </button>
                  </div>
                </div>
                <div className="px-4 py-3 text-sm text-muted-foreground">
                  {dimension.description ? (
                    <p className="mb-3 text-sm text-foreground">{dimension.description}</p>
                  ) : null}
                  <div className="space-y-2">
                    {dimension.values.map((value) => (
                      <div key={value.name} className="rounded-md border border-border px-3 py-2">
                        <div className="text-sm font-medium">{value.label || value.name}</div>
                        <div className="text-xs text-muted-foreground">Value key: {value.name}</div>
                        {value.description ? (
                          <p className="mt-2 text-sm text-foreground">{value.description}</p>
                        ) : null}
                        {value.examples && value.examples.length > 0 ? (
                          <div className="mt-3">
                            <div className="text-xs font-semibold uppercase text-muted-foreground">Examples</div>
                            <ul className="mt-1 list-disc pl-5 text-sm text-muted-foreground">
                              {value.examples.map((example, idx) => (
                                <li key={idx}>{example}</li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {isFormOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form
            className="w-full max-w-2xl rounded-lg border border-border bg-background p-6 shadow-lg"
            onSubmit={handleSubmit}
          >
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-lg font-semibold">
                  {editingIndex === null ? 'Add Dimension' : 'Edit Dimension'}
                </h3>
                <p className="text-sm text-muted-foreground">
                  Configure the dimension key and its possible values.
                </p>
              </div>
              <button
                type="button"
                onClick={closeForm}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="grid grid-cols-1 gap-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Name</label>
                  <input
                    value={form.name}
                    onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                    placeholder="location_ambiguity"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Label</label>
                  <input
                    value={form.label}
                    onChange={(e) => setForm((prev) => ({ ...prev, label: e.target.value }))}
                    placeholder="Location Ambiguity"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Description</label>
                <textarea
                  value={form.description}
                  onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
                  rows={3}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">Values</span>
                  <button
                    type="button"
                    onClick={addValueRow}
                    className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-muted"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    Add Value
                  </button>
                </div>
                <div className="space-y-4">
                  {form.values.map((value, index) => (
                    <div key={index} className="rounded-md border border-border p-4">
                      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                        <div className="md:col-span-1">
                          <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Name</label>
                          <input
                            value={value.name}
                            onChange={(e) => handleValueChange(index, 'name', e.target.value)}
                            placeholder="ambiguous"
                            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                          />
                        </div>
                        <div className="md:col-span-1">
                          <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Label</label>
                          <input
                            value={value.label || ''}
                            onChange={(e) => handleValueChange(index, 'label', e.target.value)}
                            placeholder="Ambiguous"
                            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                          />
                        </div>
                        <div className="md:col-span-1">
                          <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Description</label>
                          <input
                            value={value.description || ''}
                            onChange={(e) => handleValueChange(index, 'description', e.target.value)}
                            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                          />
                        </div>
                      </div>
                      <div className="mt-3">
                        <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Examples (one per line)</label>
                        <textarea
                          value={value.examplesText}
                          onChange={(e) => handleValueChange(index, 'examplesText', e.target.value)}
                          rows={3}
                          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                        />
                      </div>
                      <div className="mt-3 flex justify-end">
                        <button
                          type="button"
                          onClick={() => removeValueRow(index)}
                          className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-muted"
                          disabled={form.values.length === 1}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {formError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {formError}
              </div>
            ) : null}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeForm}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                disabled={mutation.isPending}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={mutation.isPending}
              >
                {mutation.isPending ? 'Saving…' : 'Save Changes'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}

function ScenariosPanel({ projectName, isAvailable }: { projectName?: string | null; isAvailable: boolean }) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, refetch, isFetching } = useEvaluationScenarios(isAvailable)
  const { data: dimensionsData } = useDimensions(isAvailable)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [selectedSetPath, setSelectedSetPath] = useState<string | null>(null)
  const [runTrigger, setRunTrigger] = useState<ScenarioRunTrigger | null>(null)
  const [isGenerateDialogOpen, setIsGenerateDialogOpen] = useState(false)
  const [generateName, setGenerateName] = useState('')
  const [generateCount, setGenerateCount] = useState('')
  const [generateSeed, setGenerateSeed] = useState('')
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [isStartingGeneration, setIsStartingGeneration] = useState(false)
  const [activeGeneration, setActiveGeneration] = useState<{ jobId: string; status: EvaluationScenarioGenerationStatus | null } | null>(null)
  const generationSocketRef = useRef<WebSocket | null>(null)
  const generationCompletionRef = useRef(false)
  const dimensionOptions = useMemo(() => dimensionsData?.dimensions ?? [], [dimensionsData])
  const initialDimensionSelection = useMemo(() => {
    const defaults: Record<string, string[]> = {}
    dimensionOptions.forEach((dimension) => {
      const dimensionName = String(dimension.name || '').trim()
      if (!dimensionName) {
        return
      }
      const values = Array.isArray(dimension.values) ? dimension.values : []
      const names = values
        .map((value) => String(value.name || '').trim())
        .filter((value) => value.length > 0)
      if (names.length > 0) {
        defaults[dimensionName] = names
      }
    })
    return defaults
  }, [dimensionOptions])
  const [selectedDimensionValues, setSelectedDimensionValues] = useState<Record<string, string[]>>(initialDimensionSelection)
  useEffect(() => {
    setSelectedDimensionValues(initialDimensionSelection)
  }, [initialDimensionSelection])

  const promotion = useMutation({
    mutationFn: (payload: EvaluationScenarioPromotionPayload) => api.promoteEvaluationScenario(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
    },
    onError: (error: unknown) => {
      console.error('Failed to promote scenario', error)
    },
    onSettled: () => {
      setPendingKey(null)
    },
  })

  const deleteSet = useMutation<EvaluationScenarioSetDeleteResponse, Error, EvaluationScenarioSetDeletePayload>({
    mutationFn: (payload) => api.deleteEvaluationScenarioSet(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
      setSelectedSetPath(null)
      setRunTrigger(null)
      setPendingKey(null)
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to delete scenario set'
      window.alert(message)
    },
  })

  const openGenerateDialog = () => {
    setGenerateName('')
    setGenerateCount('')
    setGenerateSeed('')
    setGenerateError(null)
    setSelectedDimensionValues(initialDimensionSelection)
    setIsGenerateDialogOpen(true)
  }

  const closeGenerateDialog = () => {
    if (isStartingGeneration) {
      return
    }
    setIsGenerateDialogOpen(false)
    setGenerateError(null)
    setSelectedDimensionValues(initialDimensionSelection)
  }

  const handleDismissGeneration = () => {
    if (generationSocketRef.current) {
      generationSocketRef.current.close()
      generationSocketRef.current = null
    }
    setActiveGeneration(null)
    generationCompletionRef.current = false
  }

  const handleGenerateSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (isStartingGeneration) {
      return
    }
    const trimmedName = generateName.trim()
    if (!trimmedName) {
      setGenerateError('Scenario set name is required')
      return
    }
    let parsedCount: number | undefined
    const countValue = generateCount.trim()
    if (countValue.length > 0) {
      const parsed = Number(countValue)
      if (!Number.isFinite(parsed) || !Number.isInteger(parsed) || parsed <= 0) {
        setGenerateError('Scenario count must be a positive integer')
        return
      }
      parsedCount = parsed
    }
    let parsedSeed: number | undefined
    const seedValue = generateSeed.trim()
    if (seedValue.length > 0) {
      const parsed = Number(seedValue)
      if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
        setGenerateError('Seed must be an integer')
        return
      }
      parsedSeed = parsed
    }
    const dimensionSelections = Object.fromEntries(
      Object.entries(selectedDimensionValues)
        .map(([dimensionName, values]) => {
          const normalized = Array.from(new Set(values.map((value) => value.trim()).filter((value) => value.length > 0)))
          return [dimensionName, normalized]
        })
        .filter(([, values]) => values.length > 0),
    ) as Record<string, string[]>
    const payload = {
      name: trimmedName,
      count: parsedCount,
      seed: parsedSeed,
      dimensions: Object.keys(dimensionSelections).length > 0 ? dimensionSelections : undefined,
    }
    setIsStartingGeneration(true)
    setGenerateError(null)
    try {
      const response = await api.startEvaluationScenarioGeneration(payload)
      setActiveGeneration({ jobId: response.job_id, status: response.status ?? null })
      generationCompletionRef.current = false
      setIsGenerateDialogOpen(false)
      setGenerateName('')
      setGenerateCount('')
      setGenerateSeed('')
      setSelectedDimensionValues(initialDimensionSelection)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to start scenario generation'
      setGenerateError(message)
    } finally {
      setIsStartingGeneration(false)
    }
  }

  const allSets = useMemo(() => {
    if (!data) {
      return []
    }
    return [...data.golden, ...data.generated, ...data.annotated]
  }, [data])

  const selectedSet = useMemo(() => {
    if (!selectedSetPath) {
      return null
    }
    return allSets.find((item) => item.path === selectedSetPath) ?? null
  }, [selectedSetPath, allSets])

  const generationStatus = activeGeneration?.status
  const generationState = generationStatus?.state
  const generationCompleted = generationStatus?.completed ?? 0
  const generationTotalRaw = generationStatus?.total ?? 0
  const generationEffectiveTotal = generationTotalRaw > 0 ? generationTotalRaw : generationCompleted
  const generationPercent = generationState === 'completed' ? 100 : generationEffectiveTotal > 0 ? Math.min(100, Math.round((generationCompleted / generationEffectiveTotal) * 100)) : 0

  useEffect(() => {
    if (!isLoading && !isFetching && selectedSetPath && !selectedSet) {
      setSelectedSetPath(null)
    }
  }, [isLoading, isFetching, selectedSetPath, selectedSet])

  useEffect(() => {
    const jobId = activeGeneration?.jobId
    if (!jobId) {
      return
    }

    let socket: WebSocket | null = null
    try {
      socket = api.createScenarioGenerationWebSocket(jobId)
      generationSocketRef.current = socket
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (!payload || !payload.status) {
            return
          }
          const status = payload.status as EvaluationScenarioGenerationStatus
          setActiveGeneration({ jobId, status })
          if (status.state === 'completed') {
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
            if (status.scenarios_path) {
              setSelectedSetPath(status.scenarios_path)
            }
          }
          if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
            generationCompletionRef.current = true
            try {
              socket?.close()
            } catch {}
          }
        } catch {
          try {
            socket?.close()
          } catch {}
        }
      }
      socket.onerror = () => {
        try {
          socket?.close()
        } catch {}
      }
      socket.onclose = () => {
        if (generationSocketRef.current === socket) {
          generationSocketRef.current = null
        }
        if (!generationCompletionRef.current) {
          api
            .getEvaluationScenarioGenerationStatus(jobId)
            .then((response) => {
              if (!response || !response.status) {
                return
              }
              const status = response.status as EvaluationScenarioGenerationStatus
              setActiveGeneration({ jobId: response.job_id, status })
              if (status.state === 'completed') {
                queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
                if (status.scenarios_path) {
                  setSelectedSetPath(status.scenarios_path)
                }
              }
              if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
                generationCompletionRef.current = true
              }
            })
            .catch(() => {})
        }
      }
    } catch {
      api
        .getEvaluationScenarioGenerationStatus(jobId)
        .then((response) => {
          if (!response || !response.status) {
            return
          }
          const status = response.status as EvaluationScenarioGenerationStatus
          setActiveGeneration({ jobId: response.job_id, status })
          if (status.state === 'completed') {
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
            if (status.scenarios_path) {
              setSelectedSetPath(status.scenarios_path)
            }
          }
          if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
            generationCompletionRef.current = true
          }
        })
        .catch(() => {})
    }

    return () => {
      if (generationSocketRef.current === socket) {
        generationSocketRef.current = null
      }
      if (socket) {
        try {
          socket.close()
        } catch {}
      }
    }
  }, [activeGeneration?.jobId, queryClient])

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <div className="flex flex-col items-center gap-3 rounded-md border border-border bg-muted/40 px-6 py-5 text-sm text-muted-foreground">
          <span>Unable to load scenarios{projectName ? ` for ${projectName}` : ''}.</span>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  const goldenIds = new Set<string>()
  const goldenNames = new Set<string>()
  data.golden.forEach((set) => {
    set.scenarios.forEach((scenario) => {
      if (scenario.id) {
        goldenIds.add(scenario.id)
      }
      goldenNames.add(scenario.name)
    })
  })

  const promoteScenario = (set: EvaluationScenarioSet, scenario: ScenarioItem) => {
    if (set.kind !== 'generated' && set.kind !== 'annotated') {
      return
    }
    const key = scenarioKey(set, scenario)
    setPendingKey(key)
    promotion.mutate({
      source_kind: set.kind,
      set_path: set.path,
      scenario_id: scenario.id ?? undefined,
      scenario_name: scenario.name,
      index: typeof scenario.metadata?.index === 'number' ? scenario.metadata?.index : undefined,
    })
  }

  const handleViewSet = (set: EvaluationScenarioSet) => {
    setRunTrigger(null)
    setSelectedSetPath(set.path)
  }

  const handleRunSet = (set: EvaluationScenarioSet) => {
    const token = Date.now()
    setRunTrigger({ path: set.path, token })
    setSelectedSetPath(set.path)
  }

  const handleDeleteSet = (set: EvaluationScenarioSet) => {
    if (set.kind === 'golden') {
      return
    }
    if (deleteSet.isPending) {
      return
    }
    const label = set.label || formatScenarioPath(set.path)
    const prompt =
      set.kind === 'generated'
        ? `Delete scenario set "${label}"? This removes the generated folder, including any annotated scenarios and run artifacts within it.`
        : `Delete scenario set "${label}"? This will remove the annotated scenarios from disk.`
    if (!window.confirm(prompt)) {
      return
    }
    deleteSet.mutate({ kind: set.kind, set_path: set.path })
  }

  if (selectedSet) {
    const canPromote = selectedSet.kind === 'generated' || selectedSet.kind === 'annotated'
    return (
      <ScenarioSetDetail
        set={selectedSet}
        onBack={() => {
          setSelectedSetPath(null)
          setPendingKey(null)
          setRunTrigger(null)
        }}
        projectName={projectName}
        canPromote={canPromote}
        onPromote={(scenario) => promoteScenario(selectedSet, scenario)}
        pendingKey={pendingKey}
        isPromoting={promotion.isPending}
        goldenIds={goldenIds}
        goldenNames={goldenNames}
        runTrigger={runTrigger}
        onRunTriggerConsumed={() => setRunTrigger(null)}
        onDeleteSet={handleDeleteSet}
        isDeletingSet={deleteSet.isPending}
      />
    )
  }

  const sections: { title: string; sets: EvaluationScenarioSet[] }[] = [
    { title: 'Golden Scenarios', sets: data.golden },
    { title: 'Generated Sets', sets: data.generated },
    { title: 'Annotated Sets', sets: data.annotated },
  ]

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">Scenario Sets</h2>
          <p className="text-sm text-muted-foreground">
            {projectName ? `Review scenarios associated with ${projectName}.` : 'Review configured and generated scenarios.'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
          <button
            type="button"
            onClick={openGenerateDialog}
            disabled={isStartingGeneration}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            Generate Scenarios
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        {activeGeneration ? (
          <div className="mb-4 rounded-md border border-border bg-background px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">Scenario Generation</div>
                <div className="text-xs text-muted-foreground">{generationStatus?.workflow_name || activeGeneration.jobId}</div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium capitalize">{generationState ? generationState.replace(/_/g, ' ') : 'pending'}</span>
                {generationState === 'running' ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                <button
                  type="button"
                  onClick={handleDismissGeneration}
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
                    {generationCompleted} / {generationEffectiveTotal || generationCompleted} complete
                  </span>
                  <span>{formatTimestamp(generationStatus?.updated_at)}</span>
                </div>
                <div className="mt-1 h-2 w-full rounded bg-muted">
                  <div className="h-2 rounded bg-muted-foreground transition-all" style={{ width: `${generationPercent}%` }} />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                Started {formatTimestamp(generationStatus?.started_at) || 'pending'}
                {generationStatus?.finished_at ? ` • Finished ${formatTimestamp(generationStatus.finished_at)}` : ''}
              </div>
              {generationStatus?.dimensions && Object.keys(generationStatus.dimensions).length > 0 ? (
                <div className="text-xs text-muted-foreground">
                  Filters:{' '}
                  {Object.entries(generationStatus.dimensions).map(([dim, values], index) => (
                    <span key={dim}>
                      {index > 0 ? ', ' : ''}
                      {dim}: {Array.isArray(values) ? values.join(', ') : String(values)}
                    </span>
                  ))}
                </div>
              ) : null}
              {generationStatus?.scenarios_path ? (
                <div className="text-xs text-muted-foreground">
                  Scenarios written to <span className="font-mono">{formatScenarioPath(generationStatus.scenarios_path)}</span>
                </div>
              ) : null}
              {generationStatus?.error ? (
                <div className="rounded border border-destructive/60 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                  {generationStatus.error}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
        {sections.every((section) => section.sets.length === 0) ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-border p-8 text-sm text-muted-foreground">
            No scenarios discovered yet.
          </div>
        ) : (
          <div className="space-y-6">
            {sections.map((section) => (
              <div key={section.title} className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold uppercase text-muted-foreground">{section.title}</h3>
                  {section.sets.length > 0 && (
                    <span className="text-xs text-muted-foreground">{section.sets.reduce((acc, set) => acc + set.scenario_count, 0)} total</span>
                  )}
                </div>
                {section.sets.length === 0 ? (
                  <div className="rounded-md border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
                    No {section.title.toLowerCase()} yet.
                  </div>
                ) : (
                  section.sets.map((set) => (
                    <div key={set.path} className="space-y-3 rounded-lg border border-border bg-background p-4">
                      <div className="flex flex-wrap items-start justify-between gap-4">
                        <div>
                          <div className="text-sm font-semibold">{set.label}</div>
                          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                            <FileText className="h-3.5 w-3.5" />
                            <span className="font-mono">{formatScenarioPath(set.path)}</span>
                          </div>
                          <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                            <Clock className="h-3.5 w-3.5" />
                            <span>{set.updated_at ? new Date(set.updated_at).toLocaleString() : 'Unknown updated time'}</span>
                          </div>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {set.scenario_count} scenario{set.scenario_count === 1 ? '' : 's'}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => handleViewSet(set)}
                          className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
                        >
                          View Set
                        </button>
                        <button
                          type="button"
                          onClick={() => handleRunSet(set)}
                          className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                        >
                          <Play className="h-3.5 w-3.5" />
                          Run Set
                        </button>
                        {set.kind !== 'golden' ? (
                          <button
                            type="button"
                            onClick={() => handleDeleteSet(set)}
                            disabled={deleteSet.isPending}
                            className="inline-flex items-center gap-1 rounded-md border border-destructive px-3 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            {deleteSet.isPending ? 'Deleting…' : 'Delete Set'}
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      {isGenerateDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form onSubmit={handleGenerateSubmit} className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Generate Scenarios</h3>
                <p className="text-sm text-muted-foreground">Create a synthetic scenario set for evaluation.</p>
              </div>
              <button
                type="button"
                onClick={closeGenerateDialog}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                disabled={isStartingGeneration}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Scenario Set Name</label>
                <input
                  value={generateName}
                  onChange={(event) => setGenerateName(event.target.value)}
                  placeholder="synthetic_support"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Target Count</label>
                  <input
                    type="number"
                    min={1}
                    value={generateCount}
                    onChange={(event) => setGenerateCount(event.target.value)}
                    placeholder="Use project default"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Seed</label>
                  <input
                    type="number"
                    value={generateSeed}
                    onChange={(event) => setGenerateSeed(event.target.value)}
                    placeholder="Optional"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
              </div>
              <div className="space-y-3">
                {dimensionOptions.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No synthetic dimensions configured. Leave selections empty to generate from the default template.
                  </p>
                ) : (
                  dimensionOptions.map((dimension) => {
                    const dimensionName = String(dimension.name || '').trim()
                    if (!dimensionName) {
                      return null
                    }
                    const values = Array.isArray(dimension.values) ? dimension.values : []
                    const availableValueNames = values
                      .map((value) => String(value.name || '').trim())
                      .filter((value) => value.length > 0)
                    const selectedValues = selectedDimensionValues[dimensionName] ?? initialDimensionSelection[dimensionName] ?? []
                    return (
                      <div key={dimensionName} className="rounded-md border border-border px-3 py-2">
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <div>
                            <div className="text-xs font-semibold uppercase text-muted-foreground">{dimension.label || dimensionName}</div>
                            <div className="text-[11px] text-muted-foreground">
                              All values selected by default. Adjust selections to target specific tuples.
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() =>
                                setSelectedDimensionValues((prev) => {
                                  if (!availableValueNames.length) {
                                    return prev
                                  }
                                  const unique = Array.from(new Set(availableValueNames))
                                  return { ...prev, [dimensionName]: unique }
                                })
                              }
                              className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
                            >
                              All
                            </button>
                            <button
                              type="button"
                              onClick={() =>
                                setSelectedDimensionValues((prev) => {
                                  if (!prev[dimensionName]) {
                                    return prev
                                  }
                                  const next = { ...prev }
                                  delete next[dimensionName]
                                  return next
                                })
                              }
                              className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
                            >
                              Clear
                            </button>
                          </div>
                        </div>
                        {values.length === 0 ? (
                          <div className="text-xs text-muted-foreground">No values configured.</div>
                        ) : (
                          <div className="flex flex-wrap gap-3">
                            {values.map((value) => {
                              const valueName = String(value.name || '').trim()
                              if (!valueName) {
                                return null
                              }
                              const checked = selectedValues.includes(valueName)
                              return (
                                <label key={valueName} className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() =>
                                      setSelectedDimensionValues((prev) => {
                                        const current = prev[dimensionName] ?? []
                                        const exists = current.includes(valueName)
                                        const nextValues = exists
                                          ? current.filter((item) => item !== valueName)
                                          : [...current, valueName]
                                        const next = { ...prev }
                                        if (nextValues.length > 0) {
                                          next[dimensionName] = nextValues
                                        } else {
                                          delete next[dimensionName]
                                        }
                                        return next
                                      })
                                    }
                                    className="h-4 w-4 rounded border-border text-primary focus:ring-primary/50"
                                  />
                                  <span>{value.label || valueName}</span>
                                </label>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
            </div>
            {generateError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {generateError}
              </div>
            ) : null}
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeGenerateDialog}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                disabled={isStartingGeneration}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={isStartingGeneration}
              >
                {isStartingGeneration ? 'Starting…' : 'Start Generation'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  )
}

export function EvaluationView({ activeTab, onTabChange, projectName, isAvailable, isLoadingMetadata }: EvaluationViewProps) {
  if (isLoadingMetadata) {
    return (
      <div className="flex h-full w-full items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    )
  }

  if (!isAvailable) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <div className="rounded-md border border-border bg-muted/50 px-6 py-5 text-center text-sm text-muted-foreground">
          Evaluation tooling requires launching Studio with a project manifest.
        </div>
      </div>
    )
  }

  if (activeTab === 'dimensions') {
    return <DimensionsPanel projectName={projectName} />
  }

  if (activeTab === 'scenarios') {
    return <ScenariosPanel projectName={projectName} isAvailable={isAvailable} />
  }

  if (activeTab === 'datasets') {
    return <DatasetsPanel projectName={projectName} onTabChange={onTabChange} />
  }

  return (
    <div className="flex h-full w-full items-center justify-center text-muted-foreground">
      Unknown evaluation tab.
    </div>
  )
}
