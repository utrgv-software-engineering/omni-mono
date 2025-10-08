import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Edit3, Trash2, Loader2, X, Upload, FileText, Clock, ArrowLeft } from 'lucide-react'
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
} from '../../types'

type EvaluationTab = 'dimensions' | 'scenarios' | 'datasets'

interface EvaluationViewProps {
  activeTab: EvaluationTab
  onTabChange: (tab: EvaluationTab) => void
  projectName?: string | null
  isAvailable: boolean
  isLoadingMetadata: boolean
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
}: ScenarioSetDetailProps) {
  const queryClient = useQueryClient()
  const [isFormOpen, setIsFormOpen] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [formState, setFormState] = useState<ScenarioFormState>(defaultScenarioForm)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)

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

  const isSaving = createScenario.isPending || updateScenario.isPending

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
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [selectedSetPath, setSelectedSetPath] = useState<string | null>(null)

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

  useEffect(() => {
    if (!isLoading && selectedSetPath && !selectedSet) {
      setSelectedSetPath(null)
    }
  }, [isLoading, selectedSetPath, selectedSet])

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

  if (selectedSet) {
    const canPromote = selectedSet.kind === 'generated' || selectedSet.kind === 'annotated'
    return (
      <ScenarioSetDetail
        set={selectedSet}
        onBack={() => {
          setSelectedSetPath(null)
          setPendingKey(null)
        }}
        projectName={projectName}
        canPromote={canPromote}
        onPromote={(scenario) => promoteScenario(selectedSet, scenario)}
        pendingKey={pendingKey}
        isPromoting={promotion.isPending}
        goldenIds={goldenIds}
        goldenNames={goldenNames}
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
        {isFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
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
                          onClick={() => setSelectedSetPath(set.path)}
                          className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
                        >
                          View Set
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            ))}
          </div>
        )}
      </div>
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

  return (
    <div className="flex h-full w-full items-center justify-center px-6">
      <div className="max-w-md rounded-md border border-border bg-muted/30 px-6 py-5 text-center text-sm text-muted-foreground">
        {activeTab === 'scenarios'
          ? 'Scenario management is coming soon. Promote and organize scenarios once available.'
          : 'Dataset tooling is coming soon. Define train/dev/test splits in a future update.'}
        <div className="mt-4 text-xs text-muted-foreground">
          Want to manage synthetic dimensions?{' '}
          <button
            type="button"
            onClick={() => onTabChange('dimensions')}
            className="text-primary underline-offset-4 hover:underline"
          >
            Go to Dimensions
          </button>
        </div>
      </div>
    </div>
  )
}
