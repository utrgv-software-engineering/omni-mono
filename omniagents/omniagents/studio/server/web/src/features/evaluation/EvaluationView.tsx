import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Edit3, Trash2, Loader2, X, Upload, FileText, Clock, ArrowLeft, Play, Sparkles, Database, RefreshCw, ExternalLink, ChevronRight, ChevronDown, SplitSquareVertical, Gavel, BarChart3 } from 'lucide-react'
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
  EvaluationDatasetSplit,
  EvaluationDatasetSplitDetails,
  EvaluationScenarioSplitPayload,
  EvaluationScenarioSplitResponse,
  EvaluationDatasetDeletePayload,
  EvaluationDatasetDeleteResponse,
  EvaluationScenarioSetDeletePayload,
  EvaluationScenarioSetDeleteResponse,
  EvaluationJudge,
  EvaluationJudgesResponse,
  EvaluationJudgeCreatePayload,
  EvaluationJudgeUpdatePayload,
  EvaluationJudgeDeleteResponse,
  EvaluationJudgeOptimizationRequest,
  EvaluationJudgeOptimizationStatus,
  EvaluationSuiteRunStatus,
  EvaluationSuiteRunRequest,
} from '../../types'
import { useTraceStore } from '../traces/store/traceStore'

type EvaluationTab = 'scenarios' | 'datasets' | 'dimensions' | 'judges'

interface EvaluationViewProps {
  activeTab: EvaluationTab
  onTabChange: (tab: EvaluationTab) => void
  projectName?: string | null
  isAvailable: boolean
  isLoadingMetadata: boolean
  onNavigateToLogs?: (projectName: string | null, workflowName: string | null) => void
}

const DEFAULT_JUDGE_INSTRUCTIONS = `AI Agent Information
- Name: {{ agent_label or agent_name }}
{% if agent_description -%}
- Description: {{ agent_description }}
{%- endif %}

Role
- You are an evaluation judge for the category: {{ category }}.

Goal
- Decide whether the assistant meets the criterion defined below.

Input
- You will receive a conversation trace between a user and the AI Agent described above.

Output Schema (Do Not Change)
- Return ONLY JSON with keys:
  - reasoning: string
  - answer: "Pass" or "Fail"
- Example:
  {"reasoning": "...", "answer": "Pass"}

Decision Policy
- Positive (label Fail): [FILL: WHAT COUNTS AS A VIOLATION]
- Negative (label Pass): [FILL: WHAT COUNTS AS ACCEPTABLE BEHAVIOR]
- Use only the provided input and any structured fields (e.g., history_json). Do not infer outside context.

Rules
1) Core decision rules:
   - [FILL: RULE 1]
   - [FILL: RULE 2]
   - [FILL: RULE 3]
2) Edge cases and clarifications:
   - [FILL: EDGE CASE 1]
   - [FILL: EDGE CASE 2]
3) Tie-breakers (when ambiguous):
   - [FILL: TIEBREAKER 1]
   - [FILL: TIEBREAKER 2]
4) Evidence requirements:
   - Require clear evidence before deciding Fail; otherwise prefer Pass.
   - Do not reward/penalize style unless it affects the criterion.

Common Pitfalls
- [FILL: PITFALL 1]
- [FILL: PITFALL 2]
- [FILL: PITFALL 3]

Examples
- Example A (Pass — Meets criterion)
  - Case: [FILL: CLEAR PASS CASE]
  - Why: [FILL: SPECIFIC REASON FOR PASS]
  - Output:
  {"reasoning": "[FILL: PASS REASONING]", "answer": "Pass"}

- Example B (Fail — Violates criterion)
  - Case: [FILL: CLEAR FAIL CASE]
  - Why: [FILL: SPECIFIC REASON FOR FAIL]
  - Output:
  {"reasoning": "[FILL: FAIL REASONING]", "answer": "Fail"}

- Example C (Pass — Ambiguous/insufficient evidence)
  - Case: [FILL: AMBIGUOUS/INSUFFICIENT EVIDENCE CASE]
  - Why: [FILL: WHY AMBIGUITY ⇒ PASS]
  - Output:
  {"reasoning": "[FILL: AMBIGUITY REASONING]", "answer": "Pass"}

- Example D (Pass — Valid clarification)
  - Case: [FILL: CLARIFICATION CASE THAT REMAINS ON-CRITERION]
  - Why: [FILL: WHY CLARIFICATION ⇒ PASS]
  - Output:
  {"reasoning": "[FILL: CLARIFICATION REASONING]", "answer": "Pass"}

Return ONLY the JSON object per schema—no extra text.`

const DEFAULT_JUDGE_TEMPLATE = `Decide if the assistant satisfies the evaluation criterion for this case.
Return JSON {reasoning:string, answer:"Pass"|"Fail"}.

Conversation history (JSON):
---
{{ history_json }}
---

Assistant message:
---
{{ text }}
---`

function DatasetsPanel({
  projectName,
  onTabChange,
  onNavigateToLogs: _onNavigateToLogs,
  focusRequest,
  onFocusConsumed,
}: {
  projectName?: string | null
  onTabChange: (tab: EvaluationTab) => void
  onNavigateToLogs?: (projectName: string | null, workflowName: string | null) => void
  focusRequest?: DatasetFocusRequest | null
  onFocusConsumed?: () => void
}) {
  void _onNavigateToLogs
  const { data, isLoading, isError, refetch, isFetching } = useEvaluationDatasets(true)
  const queryClient = useQueryClient()
  const datasets = useMemo(() => data?.datasets ?? [], [data])
  const resolvedProjectName = projectName ?? data?.project ?? null
  const datasetCount = datasets.length
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({})
  const datasetRows = useMemo<
    {
      key: string
      dataset: EvaluationDataset
      totals: { scenarios: number; positives: number; negatives: number }
      latestUpdated: string | null
    }[]
  >(() => {
    const copy = [...datasets]
    copy.sort((a, b) => {
      const setCompare = (a.set_name || '').localeCompare(b.set_name || '')
      if (setCompare !== 0) {
        return setCompare
      }
      return (a.category || '').localeCompare(b.category || '')
    })
    return copy.map((dataset) => {
      const datasetKey = `${dataset.artifact_dir}:${dataset.category}`
      let latestUpdated: string | null = null
      let latestTimestamp = -Infinity
      const totals = dataset.splits.reduce(
        (acc, split) => {
          acc.scenarios += split.scenario_count
          acc.positives += split.positives
          acc.negatives += split.negatives
          if (split.updated_at) {
            const parsed = Date.parse(split.updated_at)
            if (Number.isFinite(parsed) && parsed > latestTimestamp) {
              latestTimestamp = parsed
              latestUpdated = split.updated_at
            }
          }
          return acc
        },
        { scenarios: 0, positives: 0, negatives: 0 }
      )
      return {
        key: datasetKey,
        dataset,
        totals,
        latestUpdated,
      }
    })
  }, [datasets])
  const [activeSplit, setActiveSplit] = useState<{ dataset: EvaluationDataset; split: EvaluationDatasetSplit } | null>(null)
  useEffect(() => {
    setExpandedKeys((prev) => {
      const next: Record<string, boolean> = {}
      datasetRows.forEach((row) => {
        if (prev[row.key]) {
          next[row.key] = true
        }
      })
      return next
    })
  }, [datasetRows])
  useEffect(() => {
    if (!activeSplit) {
      return
    }
    const exists = datasetRows.some((row) => row.dataset.splits.some((split) => split.path === activeSplit.split.path))
    if (!exists) {
      setActiveSplit(null)
    }
  }, [activeSplit, datasetRows])
  useEffect(() => {
    if (!focusRequest) {
      return
    }
    const target = datasetRows.find((row) => row.dataset.set_name === focusRequest.set_name && row.dataset.category === focusRequest.category)
    if (!target) {
      return
    }
    setExpandedKeys((prev) => ({ ...prev, [target.key]: true }))
    onFocusConsumed?.()
  }, [focusRequest, datasetRows, onFocusConsumed])
  const splitQuery = useQuery<EvaluationDatasetSplitDetails, Error>({
    queryKey: ['evaluation', 'datasets', 'split', activeSplit?.split.path],
    queryFn: () => {
      if (!activeSplit?.split.path) {
        throw new Error('Missing split path')
      }
      return api.getEvaluationDatasetSplit(activeSplit.split.path)
    },
    enabled: Boolean(activeSplit?.split.path),
  })
  const totalSplitCount = useMemo(
    () => datasets.reduce((acc, item) => acc + item.splits.length, 0),
    [datasets]
  )
  const totalScenarioCount = useMemo(
    () =>
      datasets.reduce(
        (acc, item) =>
          acc + item.splits.reduce((inner, split) => inner + split.scenario_count, 0),
        0
      ),
    [datasets]
  )
  const formatCount = (value: number, noun: string) => `${value} ${noun}${value === 1 ? '' : 's'}`
  const closeSplit = () => setActiveSplit(null)
  const formatDimensionsList = (dimensions?: Record<string, any> | null) => {
    if (!dimensions || Object.keys(dimensions).length === 0) {
      return '—'
    }
    return Object.entries(dimensions)
      .map(([key, value]) => `${key}: ${String(value)}`)
      .join(', ')
  }
  const splitData = splitQuery.data
  const splitError = splitQuery.error
  const isSplitLoading = splitQuery.isLoading
  const isSplitFetching = splitQuery.isFetching
  const splitSummary = activeSplit ? splitData?.split ?? activeSplit.split : null
  const splitScenarios = splitData?.scenarios ?? []
  const [pendingDeleteKey, setPendingDeleteKey] = useState<string | null>(null)
  const deleteDataset = useMutation<EvaluationDatasetDeleteResponse, Error, EvaluationDatasetDeletePayload>({
    mutationFn: (payload) => api.deleteEvaluationDataset(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to delete dataset'
      window.alert(message)
    },
    onSettled: () => {
      setPendingDeleteKey(null)
    },
  })

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
          <span>No datasets yet. Split a scenario set to create train/dev/test files.</span>
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
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">Datasets</h2>
          <p className="text-sm text-muted-foreground">
            {resolvedProjectName
              ? `Scenario splits for ${resolvedProjectName}.`
              : 'Review saved train/dev/test splits.'}
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div className="text-xs text-muted-foreground">
            {formatCount(totalSplitCount, 'split')} • {formatCount(totalScenarioCount, 'scenario')} • {formatCount(datasetCount, 'dataset')}
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
        <div className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-0">
            <thead className="bg-muted/40">
              <tr>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Dataset</th>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Category</th>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Splits</th>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Scenarios</th>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Positives</th>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Negatives</th>
                <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Updated</th>
                <th className="border-b border-border/60 px-4 py-3 text-right text-xs font-semibold uppercase text-muted-foreground">Actions</th>
              </tr>
            </thead>
            <tbody>
              {datasetRows.flatMap(({ key, dataset, totals, latestUpdated }) => {
                const isExpanded = Boolean(expandedKeys[key])
                const toggleRow = () => {
                  setExpandedKeys((prev) => ({ ...prev, [key]: !prev[key] }))
                }
                const latestLabel = latestUpdated ? formatTimestamp(latestUpdated) : '—'
                const isDeletingThis = deleteDataset.isPending && pendingDeleteKey === key
                const handleDatasetDelete = () => {
                  if (isDeletingThis) {
                    return
                  }
                  if (!dataset.set_name || !dataset.category) {
                    window.alert('Dataset identifiers missing; cannot delete this entry.')
                    return
                  }
                  if (!window.confirm('Delete these splits? This cannot be undone.')) {
                    return
                  }
                  setPendingDeleteKey(key)
                  deleteDataset.mutate({ set_name: dataset.set_name, category: dataset.category })
                }
                const mainRow = (
                  <tr key={key} className="border-b border-border/60">
                    <td className="px-4 py-3 align-top">
                      <div className="flex items-start gap-3">
                        <button
                          type="button"
                          onClick={toggleRow}
                          className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted/40"
                          aria-expanded={isExpanded}
                        >
                          {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                        <div className="flex items-start gap-2">
                          <Database className="mt-0.5 h-4 w-4 text-muted-foreground" />
                          <div>
                            <div className="text-sm font-semibold text-foreground">{dataset.set_name || 'Unnamed set'}</div>
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3 align-top text-sm text-muted-foreground">{dataset.category || 'default'}</td>
                    <td className="px-4 py-3 align-top text-sm text-muted-foreground">{dataset.splits.length}</td>
                    <td className="px-4 py-3 align-top text-sm text-muted-foreground">{totals.scenarios}</td>
                    <td className="px-4 py-3 align-top text-sm text-muted-foreground">{totals.positives}</td>
                    <td className="px-4 py-3 align-top text-sm text-muted-foreground">{totals.negatives}</td>
                    <td className="px-4 py-3 align-top text-sm text-muted-foreground">{latestLabel}</td>
                    <td className="px-4 py-3 align-top text-right">
                      <button
                        type="button"
                        onClick={handleDatasetDelete}
                        disabled={isDeletingThis}
                        className="inline-flex items-center gap-1 rounded-md border border-destructive px-3 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        {isDeletingThis ? 'Deleting…' : 'Delete'}
                      </button>
                    </td>
                  </tr>
                )
                if (!isExpanded) {
                  return [mainRow]
                }
                const expandedRow = (
                  <tr key={`${key}-details`} className="border-b border-border/60 bg-muted/30">
                    <td colSpan={8} className="px-6 py-4">
                      <div className="overflow-x-auto">
                        <table className="min-w-full border-separate border-spacing-0">
                          <thead className="bg-muted/30">
                            <tr>
                              <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Split</th>
                              <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Scenarios</th>
                              <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Positives</th>
                              <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Negatives</th>
                              <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Updated</th>
                              <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Path</th>
                            </tr>
                          </thead>
                          <tbody>
                            {dataset.splits.map((split) => (
                              <tr key={split.path} className="border-b border-border/50">
                                <td className="px-3 py-2 align-top text-xs font-semibold text-foreground">
                                  <button
                                    type="button"
                                    onClick={() => setActiveSplit({ dataset, split })}
                                    className="rounded-md border border-border px-2 py-0.5 uppercase text-xs font-semibold transition-colors hover:bg-muted"
                                  >
                                    {split.split}
                                  </button>
                                </td>
                                <td className="px-3 py-2 align-top text-sm text-muted-foreground">{split.scenario_count}</td>
                                <td className="px-3 py-2 align-top text-sm text-muted-foreground">{split.positives}</td>
                                <td className="px-3 py-2 align-top text-sm text-muted-foreground">{split.negatives}</td>
                                <td className="px-3 py-2 align-top text-sm text-muted-foreground">{split.updated_at ? formatTimestamp(split.updated_at) : '—'}</td>
                                <td className="px-3 py-2 align-top text-xs text-muted-foreground">
                                  <span className="font-mono text-[11px] leading-tight">{formatScenarioPath(split.path)}</span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </td>
                  </tr>
                )
                return [mainRow, expandedRow]
              })}
            </tbody>
          </table>
        </div>
      </div>
      {activeSplit ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <div className="flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-border bg-background shadow-lg">
            <div className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <h3 className="text-lg font-semibold">
                  {activeSplit.dataset.set_name || 'Unnamed set'}
                </h3>
                <p className="text-sm text-muted-foreground">
                  {(activeSplit.dataset.category || 'default') + ' • ' + (splitSummary ? splitSummary.split.toUpperCase() : activeSplit.split.split.toUpperCase())}
                </p>
              </div>
              <div className="flex items-center gap-3">
                {isSplitFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                <button
                  type="button"
                  onClick={closeSplit}
                  className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-hidden px-5 py-4">
              {isSplitLoading ? (
                <div className="flex h-full items-center justify-center text-muted-foreground">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : splitError ? (
                <div className="flex h-full items-center justify-center">
                  <div className="flex flex-col items-center gap-3 rounded-md border border-border bg-muted/40 px-6 py-5 text-sm text-muted-foreground">
                    <span>Unable to load split scenarios.</span>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => splitQuery.refetch()}
                        className="rounded-md border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
                      >
                        Retry
                      </button>
                      <button
                        type="button"
                        onClick={closeSplit}
                        className="rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-muted"
                      >
                        Close
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex h-full flex-col">
                  {splitSummary ? (
                    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                      <span>{formatCount(splitSummary.scenario_count, 'scenario')}</span>
                      <span>{formatCount(splitSummary.positives, 'positive')}</span>
                      <span>{formatCount(splitSummary.negatives, 'negative')}</span>
                      {splitSummary.updated_at ? <span>Updated {formatTimestamp(splitSummary.updated_at)}</span> : null}
                      <span className="font-mono text-[11px] leading-tight">{formatScenarioPath(splitSummary.path)}</span>
                    </div>
                  ) : null}
                  {splitScenarios.length === 0 ? (
                    <div className="mt-6 flex flex-1 items-center justify-center rounded-md border border-dashed border-border bg-muted/30 px-6 py-5 text-sm text-muted-foreground">
                      No scenarios found in this split.
                    </div>
                  ) : (
                    <div className="mt-4 flex-1 overflow-auto">
                      <table className="min-w-full border-separate border-spacing-0">
                        <thead className="bg-muted/30">
                          <tr>
                            <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Name</th>
                            <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Label</th>
                            <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Dimensions</th>
                            <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Prompt</th>
                            <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Assistant Output</th>
                          </tr>
                        </thead>
                        <tbody>
                          {splitScenarios.map((scenario, index) => {
                            const labelValue = scenario.label
                            const labelText = labelValue === true ? 'Positive' : labelValue === false ? 'Negative' : '—'
                            const labelClass = labelValue === true
                              ? 'border-emerald-500/60 bg-emerald-500/10 text-emerald-600'
                              : labelValue === false
                                ? 'border-destructive/60 bg-destructive/10 text-destructive'
                                : 'border-border text-muted-foreground'
                            const assistantMessageRaw = scenario.metadata && typeof scenario.metadata.assistant_last_message === 'string' ? scenario.metadata.assistant_last_message : null
                            const assistantMessage = assistantMessageRaw && assistantMessageRaw.trim().length > 0 ? assistantMessageRaw : '—'
                            return (
                              <tr key={`${scenario.id || scenario.name || 'scenario'}-${index}`} className="border-b border-border/50">
                                <td className="px-3 py-2 align-top text-sm font-medium text-foreground">{scenario.name}</td>
                                <td className="px-3 py-2 align-top text-xs">
                                  <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium ${labelClass}`}>{labelText}</span>
                                </td>
                                <td className="px-3 py-2 align-top text-xs text-muted-foreground">{formatDimensionsList(scenario.dimensions)}</td>
                                <td className="px-3 py-2 align-top text-sm text-foreground">
                                  <div className="max-h-36 overflow-y-auto whitespace-pre-wrap">{scenario.prompt}</div>
                                </td>
                                <td className="px-3 py-2 align-top text-sm text-muted-foreground">
                                  <div className="max-h-36 overflow-y-auto whitespace-pre-wrap text-foreground">
                                    {assistantMessage}
                                  </div>
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <div className="mt-4 flex justify-end">
                    <button
                      type="button"
                      onClick={closeSplit}
                      className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                    >
                      Close
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
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

function useEvaluationJudges(enabled: boolean) {
  return useQuery({
    queryKey: ['evaluation', 'judges'],
    queryFn: () => api.getEvaluationJudges(),
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

function getScenarioFailureCategories(scenario: ScenarioItem): string[] {
  const categories: string[] = []
  if (Array.isArray(scenario.failure_categories)) {
    scenario.failure_categories.forEach((item) => {
      if (typeof item === 'string') {
        const trimmed = item.trim()
        if (trimmed.length > 0) {
          categories.push(trimmed)
        }
      }
    })
  }
  const metadataSource = scenario.metadata
  if (metadataSource && typeof metadataSource === 'object') {
    const metadata = metadataSource as Record<string, any>
    const metadataCategories = metadata.failure_categories
    if (Array.isArray(metadataCategories)) {
      metadataCategories.forEach((item) => {
        if (typeof item === 'string') {
          const trimmed = item.trim()
          if (trimmed.length > 0) {
            categories.push(trimmed)
          }
        }
      })
    }
  }
  return Array.from(new Set(categories))
}

interface DatasetFocusRequest {
  set_name: string
  category: string
  split_path?: string | null
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

function extractWorkflowFromMetadata(metadata?: Record<string, any> | null): string | null {
  if (!metadata || typeof metadata !== 'object') {
    return null
  }
  const direct = metadata.workflow_run
  if (typeof direct === 'string' && direct.trim()) {
    return direct.trim()
  }
  const manifest = metadata.manifest
  if (manifest && typeof manifest === 'object') {
    const manifestRun = manifest.workflow_run || manifest.workflow || manifest.workflow_name || manifest.workflowName
    if (typeof manifestRun === 'string' && manifestRun.trim()) {
      return manifestRun.trim()
    }
  }
  return null
}

interface ScenarioSetDetailProps {
  set: EvaluationScenarioSet
  onBack: () => void
  canPromote: boolean
  onPromote: (scenario: ScenarioItem) => void
  pendingKey: string | null
  isPromoting: boolean
  goldenIds: Set<string>
  goldenNames: Set<string>
  runTrigger?: ScenarioRunTrigger | null
  onRunTriggerConsumed?: () => void
  splitTrigger?: ScenarioSplitTrigger | null
  onSplitTriggerConsumed?: () => void
  onViewRun?: (workflowName: string) => void
  onDeleteSet?: (set: EvaluationScenarioSet) => void
  isDeletingSet?: boolean
  onSplitSuccess?: (response: EvaluationScenarioSplitResponse) => void
}

interface ScenarioRunTrigger {
  path: string
  token: number
}

interface ScenarioSplitTrigger {
  path: string
  token: number
}

function ScenarioSetDetail({
  set,
  onBack,
  canPromote,
  onPromote,
  pendingKey,
  isPromoting,
  goldenIds,
  goldenNames,
  runTrigger,
  onRunTriggerConsumed,
  splitTrigger,
  onSplitTriggerConsumed,
  onViewRun,
  onDeleteSet,
  isDeletingSet,
  onSplitSuccess,
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
  const [isSplitDialogOpen, setIsSplitDialogOpen] = useState(false)
  const [splitCategory, setSplitCategory] = useState('')
  const [isCustomSplitCategory, setIsCustomSplitCategory] = useState(false)
  const [splitTrain, setSplitTrain] = useState('0.15')
  const [splitDev, setSplitDev] = useState('0.425')
  const [splitTest, setSplitTest] = useState('0.425')
  const [splitSeed, setSplitSeed] = useState('42')
  const [splitNegativesRatio, setSplitNegativesRatio] = useState('')
  const [splitError, setSplitError] = useState<string | null>(null)

  const availableFailureCategories = useMemo(() => {
    const categories = new Set<string>()
    set.scenarios.forEach((scenario) => {
      getScenarioFailureCategories(scenario).forEach((category) => {
        categories.add(category)
      })
    })
    return Array.from(categories).sort((a, b) => a.localeCompare(b))
  }, [set.scenarios])

  useEffect(() => {
    activeRunRef.current = activeRun
  }, [activeRun])

  const workflowFromMetadata = extractWorkflowFromMetadata(set.metadata)
  const runWorkflowNameRaw = activeRun?.status?.workflow_name
  const runWorkflowName = typeof runWorkflowNameRaw === 'string' && runWorkflowNameRaw.trim().length > 0 ? runWorkflowNameRaw.trim() : null
  const effectiveWorkflowName = runWorkflowName || workflowFromMetadata
  const totalScenarios = set.scenarios.length
  const labeledCount = useMemo(
    () => set.scenarios.filter((scenario) => Array.isArray(scenario.failure_categories) && scenario.failure_categories.length > 0).length,
    [set.scenarios]
  )
  const analyzedCount = useMemo(
    () =>
      set.scenarios.filter((scenario) => {
        const analysis = scenario.analysis || (scenario.metadata?.analysis as Record<string, any> | undefined)
        if (!analysis) {
          return false
        }
        return Boolean(analysis.judgment || (typeof analysis.notes === 'string' && analysis.notes.trim().length > 0))
      }).length,
    [set.scenarios]
  )

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

  const splitScenario = useMutation<EvaluationScenarioSplitResponse, Error, EvaluationScenarioSplitPayload>({
    mutationFn: (payload) => api.splitEvaluationScenario(payload),
    onSuccess: (response) => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
      setIsSplitDialogOpen(false)
      setSplitError(null)
      if (response) {
        onSplitSuccess?.(response)
      }
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Failed to split scenario set'
      setSplitError(message)
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

  const openSplitDialog = () => {
    if (splitScenario.isPending) {
      return
    }
    const defaultCategory = availableFailureCategories[0] ?? ''
    setSplitCategory(defaultCategory)
    setIsCustomSplitCategory(defaultCategory.length === 0)
    setSplitTrain('0.15')
    setSplitDev('0.425')
    setSplitTest('0.425')
    setSplitSeed('42')
    setSplitNegativesRatio('')
    setSplitError(null)
    setIsSplitDialogOpen(true)
  }

  const closeSplitDialog = () => {
    if (splitScenario.isPending) {
      return
    }
    setIsSplitDialogOpen(false)
    setIsCustomSplitCategory(false)
    setSplitError(null)
  }

  const handleSplitSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (splitScenario.isPending) {
      return
    }
    const category = splitCategory.trim()
    if (!category) {
      setSplitError('Category is required')
      return
    }
    const parsedTrain = Number(splitTrain)
    const parsedDev = Number(splitDev)
    const parsedTest = Number(splitTest)
    if (!Number.isFinite(parsedTrain) || parsedTrain < 0 || !Number.isFinite(parsedDev) || parsedDev < 0 || !Number.isFinite(parsedTest) || parsedTest < 0) {
      setSplitError('Fractions must be non-negative numbers')
      return
    }
    if (parsedTrain + parsedDev + parsedTest <= 0) {
      setSplitError('Fractions must sum to more than zero')
      return
    }
    const parsedSeed = Number(splitSeed)
    if (!Number.isFinite(parsedSeed) || !Number.isInteger(parsedSeed)) {
      setSplitError('Seed must be an integer')
      return
    }
    let negativesValue: number | undefined
    const negativesTrimmed = splitNegativesRatio.trim()
    if (negativesTrimmed.length > 0) {
      const parsedNegatives = Number(negativesTrimmed)
      if (!Number.isFinite(parsedNegatives) || parsedNegatives < 0) {
        setSplitError('Negatives ratio must be a non-negative number')
        return
      }
      negativesValue = parsedNegatives
    }
    const payload: EvaluationScenarioSplitPayload = {
      set_path: set.path,
      kind: set.kind,
      category,
      train: parsedTrain,
      dev: parsedDev,
      test: parsedTest,
      seed: Math.trunc(parsedSeed),
    }
    if (negativesValue !== undefined) {
      payload.negatives_ratio = negativesValue
    }
    setSplitError(null)
    splitScenario.mutate(payload)
  }

  const handleSplitCategoryChange = (value: string) => {
    if (value === '__custom__') {
      setIsCustomSplitCategory(true)
      setSplitCategory('')
      return
    }
    setIsCustomSplitCategory(false)
    setSplitCategory(value)
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

  useEffect(() => {
    if (!splitTrigger || splitTrigger.path !== set.path) {
      return
    }
    if (!isSplitDialogOpen) {
      openSplitDialog()
    }
    if (onSplitTriggerConsumed) {
      onSplitTriggerConsumed()
    }
  }, [splitTrigger?.token, splitTrigger?.path, set.path, isSplitDialogOpen, onSplitTriggerConsumed])

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
        <div className="flex flex-wrap items-center gap-3">
          <div className="text-xs text-muted-foreground">
            {totalScenarios} scenario{totalScenarios === 1 ? '' : 's'}
            {totalScenarios > 0 ? ` • ${labeledCount} labeled` : ''}
            {analyzedCount > 0 ? ` • ${analyzedCount} annotated` : ''}
          </div>
          {effectiveWorkflowName && onViewRun ? (
            <button
              type="button"
              onClick={() => onViewRun(effectiveWorkflowName)}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              View Run
            </button>
          ) : null}
          {set.kind !== 'golden' ? (
            <button
              type="button"
              onClick={openSplitDialog}
              disabled={splitScenario.isPending}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
            >
              <SplitSquareVertical className="h-3.5 w-3.5" />
              {splitScenario.isPending ? 'Splitting…' : 'Split Set'}
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
              const metadata = (scenario.metadata ?? null) as Record<string, any> | null
              const failureCategories = getScenarioFailureCategories(scenario)
              const analysisBlock = (() => {
                if (scenario.analysis && typeof scenario.analysis === 'object') {
                  return scenario.analysis as Record<string, any>
                }
                if (metadata?.analysis && typeof metadata.analysis === 'object') {
                  return metadata.analysis as Record<string, any>
                }
                return null
              })()
              const measures = (() => {
                let source: any[] = []
                if (Array.isArray(scenario.measures)) {
                  source = scenario.measures
                } else if (Array.isArray(metadata?.measures)) {
                  source = metadata.measures
                }
                return source
                  .map((entry) => {
                    if (typeof entry === 'string') {
                      const trimmed = entry.trim()
                      return trimmed.length > 0 ? trimmed : null
                    }
                    if (typeof entry === 'number' || typeof entry === 'boolean') {
                      return String(entry)
                    }
                    if (entry === null || typeof entry === 'undefined') {
                      return null
                    }
                    try {
                      return JSON.stringify(entry)
                    } catch {
                      return String(entry)
                    }
                  })
                  .filter((value): value is string => Boolean(value && value.length > 0))
              })()
              const judgment = typeof analysisBlock?.judgment === 'string' ? analysisBlock.judgment : null
              const notes = typeof analysisBlock?.notes === 'string' ? analysisBlock.notes : ''
              const analysisUpdatedAt = typeof analysisBlock?.updated_at === 'string' ? analysisBlock.updated_at : null
              const showFailureCategories = failureCategories.length > 0 || Boolean(judgment || notes)
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
                  {measures.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {measures.map((measure, index) => (
                        <span key={`${key}-measure-${index}`} className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground">
                          {measure}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {showFailureCategories ? (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {failureCategories.length > 0 ? (
                        failureCategories.map((category) => (
                          <span key={category} className="rounded-md border border-destructive/40 bg-destructive/10 px-2 py-0.5 text-xs text-destructive">
                            {category}
                          </span>
                        ))
                      ) : (
                        <span className="rounded-md border border-border px-2 py-0.5 text-xs text-muted-foreground">No failure categories yet</span>
                      )}
                    </div>
                  ) : null}
                  {analysisBlock ? (
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                      {judgment ? (
                        <span
                          className={`rounded-md border px-2 py-0.5 ${
                            judgment === 'unacceptable'
                              ? 'border-destructive/40 bg-destructive/10 text-destructive'
                              : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600'
                          }`}
                        >
                          Judgment: {judgment === 'unacceptable' ? 'unacceptable' : 'acceptable'}
                        </span>
                      ) : null}
                      {typeof analysisBlock?.workflow === 'string' && analysisBlock.workflow.trim().length > 0 ? (
                        <span className="rounded-md border border-border px-2 py-0.5 text-muted-foreground">
                          Workflow: {analysisBlock.workflow}
                        </span>
                      ) : null}
                      {analysisUpdatedAt ? (
                        <span className="rounded-md border border-border px-2 py-0.5 text-muted-foreground">
                          Updated {formatTimestamp(analysisUpdatedAt)}
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                  {notes ? (
                    <p className="mt-2 text-xs text-muted-foreground italic">{notes}</p>
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
      {isSplitDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form
            onSubmit={handleSplitSubmit}
            className="w-full max-w-2xl rounded-lg border border-border bg-background p-6 shadow-lg"
          >
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Split Scenario Set</h3>
                <p className="text-sm text-muted-foreground">Create train/dev/test splits for a failure category.</p>
              </div>
              <button
                type="button"
                onClick={closeSplitDialog}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                disabled={splitScenario.isPending}
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Category</label>
                {availableFailureCategories.length > 0 ? (
                  <div className="space-y-2">
                    <select
                      value={isCustomSplitCategory ? '__custom__' : splitCategory}
                      onChange={(event) => handleSplitCategoryChange(event.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                      disabled={splitScenario.isPending}
                    >
                      {availableFailureCategories.map((category) => (
                        <option key={category} value={category}>
                          {category}
                        </option>
                      ))}
                      <option value="__custom__">Custom category…</option>
                    </select>
                    {isCustomSplitCategory ? (
                      <input
                        value={splitCategory}
                        onChange={(event) => setSplitCategory(event.target.value)}
                        placeholder="Enter category"
                        className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                        disabled={splitScenario.isPending}
                      />
                    ) : null}
                  </div>
                ) : (
                  <input
                    value={splitCategory}
                    onChange={(event) => setSplitCategory(event.target.value)}
                    placeholder="failure_category"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    disabled={splitScenario.isPending}
                  />
                )}
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Train Fraction</label>
                  <input
                    type="number"
                    step="0.001"
                    min={0}
                    value={splitTrain}
                    onChange={(event) => setSplitTrain(event.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    disabled={splitScenario.isPending}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Dev Fraction</label>
                  <input
                    type="number"
                    step="0.001"
                    min={0}
                    value={splitDev}
                    onChange={(event) => setSplitDev(event.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    disabled={splitScenario.isPending}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Test Fraction</label>
                  <input
                    type="number"
                    step="0.001"
                    min={0}
                    value={splitTest}
                    onChange={(event) => setSplitTest(event.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    disabled={splitScenario.isPending}
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Random Seed</label>
                  <input
                    type="number"
                    value={splitSeed}
                    onChange={(event) => setSplitSeed(event.target.value)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    disabled={splitScenario.isPending}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Negatives Ratio (optional)</label>
                  <input
                    type="number"
                    step="0.01"
                    min={0}
                    value={splitNegativesRatio}
                    onChange={(event) => setSplitNegativesRatio(event.target.value)}
                    placeholder="Auto"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    disabled={splitScenario.isPending}
                  />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                Fractions do not need to sum to one, but they must total more than zero.
              </div>
            </div>

            {splitError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {splitError}
              </div>
            ) : null}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeSplitDialog}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                disabled={splitScenario.isPending}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={splitScenario.isPending}
              >
                {splitScenario.isPending ? 'Splitting…' : 'Create Splits'}
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
  const [expandedKeys, setExpandedKeys] = useState<Record<string, boolean>>({})
  const [examplesDialog, setExamplesDialog] = useState<{
    dimensionName: string
    dimensionLabel?: string
    valueName: string
    valueLabel?: string
    examples: string[]
  } | null>(null)

  const dimensions = useMemo(() => data?.dimensions ?? [], [data])
  const resolvedProjectName = projectName ?? data?.project ?? null

  useEffect(() => {
    setExpandedKeys((prev) => {
      const next: Record<string, boolean> = {}
      dimensions.forEach((dimension, index) => {
        const key = dimension.name || `dimension-${index}`
        if (prev[key]) {
          next[key] = true
        }
      })
      return next
    })
  }, [dimensions])

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

  const toggleRow = (key: string) => {
    setExpandedKeys((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  const openExamples = (dimension: EvaluationDimension, value: EvaluationDimensionValue, examples: string[]) => {
    setExamplesDialog({
      dimensionName: dimension.name,
      dimensionLabel: dimension.label,
      valueName: value.name,
      valueLabel: value.label,
      examples,
    })
  }

  const closeExamples = () => {
    setExamplesDialog(null)
  }

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
        .map((value) => {
          const nextName = value.name.trim()
          const nextLabel = (value.label ?? '').trim()
          const nextDescription = (value.description ?? '').trim()
          const examples = value.examplesText
            .split('\n')
            .map((line) => line.trim())
            .filter((line) => line.length > 0)
          return {
            name: nextName,
            label: nextLabel || undefined,
            description: nextDescription || undefined,
            examples,
          }
        })
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
    const target = dimensions[index]
    if (!target) {
      return
    }
    const label = target.label || target.name
    if (!window.confirm(`Delete dimension "${label}"?`)) {
      return
    }
    const nextDimensions = dimensions.filter((_, idx) => idx !== index)
    mutation.mutate(nextDimensions)
  }

  useEffect(() => {
    if (!isFormOpen) {
      setForm(defaultDimension())
      setFormError(null)
    }
  }, [isFormOpen])

  const getLastEdited = (dimension: EvaluationDimension): string => {
    const raw = (dimension as any).updated_at
    if (typeof raw === 'string' && raw.trim().length > 0) {
      const date = new Date(raw)
      if (!Number.isNaN(date.getTime())) {
        return date.toLocaleString()
      }
      return raw
    }
    return '—'
  }

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
          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead className="bg-muted/40">
                <tr>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Dimension</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Key</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Values</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Last Edited</th>
                  <th className="border-b border-border/60 px-4 py-3 text-right text-xs font-semibold uppercase text-muted-foreground">Actions</th>
                </tr>
              </thead>
              <tbody>
                {dimensions.map((dimension, index) => {
                  const dimensionKey = dimension.name || `dimension-${index}`
                  const isExpanded = Boolean(expandedKeys[dimensionKey])
                  const valueCount = Array.isArray(dimension.values) ? dimension.values.length : 0
                  const description = typeof dimension.description === 'string' ? dimension.description.trim() : ''
                  const lastEdited = getLastEdited(dimension)
                  const mainRow = (
                    <tr key={dimensionKey} className="border-b border-border/60">
                      <td className="px-4 py-3 align-top">
                        <div className="flex items-start gap-3">
                          <button
                            type="button"
                            onClick={() => toggleRow(dimensionKey)}
                            className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted/40"
                            aria-expanded={isExpanded}
                            aria-label={isExpanded ? 'Collapse dimension values' : 'Expand dimension values'}
                          >
                            {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                          </button>
                          <div>
                            <div className="text-sm font-semibold text-foreground">{dimension.label || dimension.name}</div>
                            {description ? <p className="mt-1 text-xs text-muted-foreground">{description}</p> : null}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top text-sm font-mono text-muted-foreground">{dimension.name}</td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{valueCount}</td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{lastEdited}</td>
                      <td className="px-4 py-3 align-top">
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => openEdit(index)}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                          >
                            <Edit3 className="h-3.5 w-3.5" />
                            Edit
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(index)}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  )

                  const expandedRow = !isExpanded
                    ? null
                    : (
                        <tr key={`${dimensionKey}-expanded`} className="border-b border-border/60 bg-muted/30">
                          <td colSpan={5} className="px-6 py-4">
                            {valueCount === 0 ? (
                              <div className="rounded-md border border-border/60 bg-background px-4 py-3 text-sm text-muted-foreground">
                                No values configured for this dimension.
                              </div>
                            ) : (
                              <div className="overflow-x-auto">
                                <table className="min-w-full border-separate border-spacing-0">
                                  <thead className="bg-muted/30">
                                    <tr>
                                      <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Value Label</th>
                                      <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Value Key</th>
                                      <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Description</th>
                                      <th className="border-b border-border/50 px-3 py-2 text-left text-xs font-semibold uppercase text-muted-foreground">Examples</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {dimension.values.map((value, valueIndex) => {
                                      const trimmedDescription = typeof value.description === 'string' ? value.description.trim() : ''
                                      const examples = Array.isArray(value.examples)
                                        ? value.examples
                                            .map((example) => (typeof example === 'string' ? example.trim() : String(example)))
                                            .filter((example) => example.length > 0)
                                        : []
                                      return (
                                        <tr key={`${dimensionKey}-value-${value.name || valueIndex}`} className="border-b border-border/50">
                                          <td className="px-3 py-2 align-top text-sm font-medium text-foreground">{value.label || value.name}</td>
                                          <td className="px-3 py-2 align-top text-xs font-mono text-muted-foreground">{value.name}</td>
                                          <td className="px-3 py-2 align-top text-sm text-muted-foreground">{trimmedDescription || '—'}</td>
                                          <td className="px-3 py-2 align-top text-xs text-muted-foreground">
                                            {examples.length > 0 ? (
                                              <div className="flex items-center gap-2">
                                                <span>{examples.length}</span>
                                                <button
                                                  type="button"
                                                  onClick={() => openExamples(dimension, value, examples)}
                                                  className="rounded-md border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                                                >
                                                  View
                                                </button>
                                              </div>
                                            ) : (
                                              '—'
                                            )}
                                          </td>
                                        </tr>
                                      )
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </td>
                        </tr>
                      )

                  return [mainRow, expandedRow]
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {examplesDialog ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <div className="w-full max-w-lg max-h-[80vh] rounded-lg border border-border bg-background p-6 shadow-lg flex flex-col">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Value Examples</h3>
                <p className="text-sm text-muted-foreground">
                  {(examplesDialog.dimensionLabel || examplesDialog.dimensionName) + ' - ' + (examplesDialog.valueLabel || examplesDialog.valueName)}
                </p>
              </div>
              <button
                type="button"
                onClick={closeExamples}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-auto">
              {examplesDialog.examples.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">
                  No examples provided.
                </div>
              ) : (
                <ul className="space-y-2 pr-1">
                  {examplesDialog.examples.map((example, index) => (
                    <li key={`${examplesDialog.dimensionName}-${examplesDialog.valueName}-example-${index}`} className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-sm text-foreground">
                      {example}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="mt-6 flex justify-end">
              <button
                type="button"
                onClick={closeExamples}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}

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

interface JudgeFormState {
  key: string
  model: string
  agentLabel: string
  agentName: string
  source: string
  failureCategory: string
  instructions: string
  template: string
  addMeasure: boolean
}

interface JudgeOptimizeFormState {
  datasetKey: string
  maxMetricCalls: string
  seed: string
}

const defaultJudgeForm = (): JudgeFormState => ({
  key: '',
  model: '',
  agentLabel: '',
  agentName: '',
  source: 'final_assistant_text',
  failureCategory: '',
  instructions: DEFAULT_JUDGE_INSTRUCTIONS,
  template: DEFAULT_JUDGE_TEMPLATE,
  addMeasure: true,
})

const defaultOptimizeForm = (): JudgeOptimizeFormState => ({
  datasetKey: '',
  maxMetricCalls: '100',
  seed: '',
})

const slugify = (value: string) =>
  value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')

function JudgesPanel({ projectName }: { projectName?: string | null }) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, refetch, isFetching } = useEvaluationJudges(true)
  const judges = useMemo(() => data?.judges ?? [], [data])
  const resolvedProjectName = projectName ?? data?.project ?? null
  const [isFormOpen, setIsFormOpen] = useState(false)
  const [editingJudge, setEditingJudge] = useState<EvaluationJudge | null>(null)
  const [form, setForm] = useState<JudgeFormState>(defaultJudgeForm)
  const [formError, setFormError] = useState<string | null>(null)
  const { failureCategories, loadFailureCategories, selectedProject: selectedProjectFromStore } = useTraceStore()
  const { data: scenariosData } = useEvaluationScenarios(true)
  const { data: datasetsData } = useEvaluationDatasets(true)
  const datasets = useMemo(() => datasetsData?.datasets ?? [], [datasetsData])
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
    const map = new Map<string, EvaluationDataset>()
    datasetOptions.forEach((option) => {
      map.set(option.key, option.dataset)
    })
    return map
  }, [datasetOptions])
  const [isOptimizeDialogOpen, setIsOptimizeDialogOpen] = useState(false)
  const [optimizeTarget, setOptimizeTarget] = useState<EvaluationJudge | null>(null)
  const [optimizeForm, setOptimizeForm] = useState<JudgeOptimizeFormState>(defaultOptimizeForm())
  const [optimizeError, setOptimizeError] = useState<string | null>(null)
  const [isStartingOptimization, setIsStartingOptimization] = useState(false)
  const [activeOptimization, setActiveOptimization] = useState<{ jobId: string; status: EvaluationJudgeOptimizationStatus | null } | null>(null)
  const optimizationSocketRef = useRef<WebSocket | null>(null)
  const optimizationCompletionRef = useRef(false)
  const projectForCategories = projectName ?? data?.project ?? selectedProjectFromStore ?? null
  useEffect(() => {
    if (projectForCategories && failureCategories.length === 0) {
      void loadFailureCategories(projectForCategories)
    }
  }, [projectForCategories, failureCategories.length, loadFailureCategories])
  useEffect(() => {
    const jobId = activeOptimization?.jobId
    if (!jobId) {
      return
    }
    let socket: WebSocket | null = null
    try {
      socket = api.createJudgeOptimizationWebSocket(jobId)
      optimizationSocketRef.current = socket
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (!payload || !payload.status) {
            return
          }
          const status = payload.status as EvaluationJudgeOptimizationStatus
          setActiveOptimization({ jobId, status })
          if (status.state === 'completed') {
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'judges'] })
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
          }
          if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
            optimizationCompletionRef.current = true
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
        if (optimizationSocketRef.current === socket) {
          optimizationSocketRef.current = null
        }
        if (!optimizationCompletionRef.current) {
          api
            .getJudgeOptimizationStatus(jobId)
            .then((response) => {
              if (!response || !response.status) {
                return
              }
              const status = response.status as EvaluationJudgeOptimizationStatus
              setActiveOptimization({ jobId: response.job_id, status })
              if (status.state === 'completed') {
                queryClient.invalidateQueries({ queryKey: ['evaluation', 'judges'] })
                queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
              }
              if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
                optimizationCompletionRef.current = true
              }
            })
            .catch(() => {})
        }
      }
    } catch {
      api
        .getJudgeOptimizationStatus(jobId)
        .then((response) => {
          if (!response || !response.status) {
            return
          }
          const status = response.status as EvaluationJudgeOptimizationStatus
          setActiveOptimization({ jobId: response.job_id, status })
          if (status.state === 'completed') {
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'judges'] })
            queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
          }
          if (status.state === 'completed' || status.state === 'failed' || status.state === 'cancelled') {
            optimizationCompletionRef.current = true
          }
        })
        .catch(() => {})
    }

    return () => {
      if (optimizationSocketRef.current === socket) {
        optimizationSocketRef.current = null
      }
      if (socket) {
        try {
          socket.close()
        } catch {}
      }
    }
  }, [activeOptimization?.jobId, queryClient])
  const failureCategoryOptions = useMemo(() => {
    const names = new Set<string>()
    failureCategories.forEach((category) => {
      if (category?.name) {
        names.add(category.name)
      }
    })
    const addFromSet = (sets?: EvaluationScenarioSet[]) => {
      sets?.forEach((set) => {
        set.scenarios.forEach((scenario) => {
          getScenarioFailureCategories(scenario).forEach((cat) => {
            if (cat) {
              names.add(cat)
            }
          })
        })
      })
    }
    addFromSet(scenariosData?.golden)
    addFromSet(scenariosData?.generated)
    addFromSet(scenariosData?.annotated)
    const current = form.failureCategory.trim()
    if (current) {
      names.add(current)
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b))
  }, [failureCategories, form.failureCategory, scenariosData])
  const isCreateMode = editingJudge === null
  const createJudge = useMutation<EvaluationJudgesResponse, Error, EvaluationJudgeCreatePayload>({
    mutationFn: (payload) => api.createEvaluationJudge(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'judges'] })
      setIsFormOpen(false)
      setEditingJudge(null)
      setForm(defaultJudgeForm())
      setFormError(null)
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Failed to save judge'
      setFormError(message)
    },
  })
  const updateJudge = useMutation<EvaluationJudgesResponse, Error, EvaluationJudgeUpdatePayload>({
    mutationFn: (payload) => api.updateEvaluationJudge(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'judges'] })
      setIsFormOpen(false)
      setEditingJudge(null)
      setForm(defaultJudgeForm())
      setFormError(null)
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Failed to save judge'
      setFormError(message)
    },
  })
  const deleteJudge = useMutation<EvaluationJudgeDeleteResponse, Error, string>({
    mutationFn: (key) => api.deleteEvaluationJudge(key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['evaluation', 'judges'] })
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Failed to delete judge'
      window.alert(message)
    },
  })
  const isSaving = createJudge.isPending || updateJudge.isPending
  const openCreate = () => {
    setEditingJudge(null)
    setForm(defaultJudgeForm())
    setIsFormOpen(true)
    setFormError(null)
  }
  const openEdit = (judge: EvaluationJudge) => {
    setEditingJudge(judge)
    setForm({
      key: judge.key,
      model: judge.model || '',
      agentLabel: judge.agent_label || '',
      agentName: judge.agent_name || '',
      source: judge.source || 'final_assistant_text',
      failureCategory: judge.failure_category || '',
      instructions: judge.instructions ?? '',
      template: judge.template ?? '',
      addMeasure: true,
    })
    setIsFormOpen(true)
    setFormError(null)
  }
  const closeForm = () => {
    if (isSaving) {
      return
    }
    setIsFormOpen(false)
    setEditingJudge(null)
    setForm(defaultJudgeForm())
    setFormError(null)
  }
  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (isCreateMode) {
      const trimmedName = form.agentLabel.trim()
      if (!trimmedName) {
        setFormError('Name is required')
        return
      }
      const payload: EvaluationJudgeCreatePayload = {
        name: trimmedName,
        failure_category: form.failureCategory.trim() || undefined,
        add_measure: form.addMeasure,
      }
      createJudge.mutate(payload)
      return
    }
    const resolvedModel = form.model.trim()
    if (!resolvedModel) {
      setFormError('Model is required')
      return
    }
    if (editingJudge) {
      const payload: EvaluationJudgeUpdatePayload = {
        key: editingJudge.key,
        model: resolvedModel,
        agent_label: form.agentLabel.trim() || undefined,
        agent_name: form.agentName.trim() || undefined,
        source: form.source.trim() || undefined,
        failure_category: form.failureCategory.trim() ? form.failureCategory.trim() : undefined,
        instructions: form.instructions,
        template: form.template,
      }
      updateJudge.mutate(payload)
    }
  }
  const handleDelete = (judge: EvaluationJudge) => {
    if (deleteJudge.isPending) {
      return
    }
    if (!window.confirm(`Delete judge "${judge.key}"? This cannot be undone.`)) {
      return
    }
    deleteJudge.mutate(judge.key)
  }
  const openOptimize = (judge: EvaluationJudge) => {
    setOptimizeTarget(judge)
    const judgeCategory = (judge.failure_category || '').trim()
    const preferred = datasetOptions.find((option) => option.dataset.category === judgeCategory)
    const fallback = datasetOptions[0] ?? null
    const nextKey = preferred?.key ?? fallback?.key ?? ''
    setOptimizeForm({ datasetKey: nextKey, maxMetricCalls: '100', seed: '' })
    setOptimizeError(datasetOptions.length === 0 ? 'No datasets available. Create a split before optimizing.' : null)
    setIsOptimizeDialogOpen(true)
  }
  const closeOptimizeDialog = () => {
    if (isStartingOptimization) {
      return
    }
    setIsOptimizeDialogOpen(false)
    setOptimizeTarget(null)
    setOptimizeForm(defaultOptimizeForm())
    setOptimizeError(null)
  }
  const handleOptimizeSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!optimizeTarget) {
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
    const seedText = optimizeForm.seed.trim()
    const parsedSeed = seedText.length > 0 ? Number(seedText) : undefined
    if (parsedSeed !== undefined && (!Number.isFinite(parsedSeed) || !Number.isInteger(parsedSeed))) {
      setOptimizeError('Seed must be an integer value')
      return
    }
    setIsStartingOptimization(true)
    setOptimizeError(null)
    try {
      const payload: EvaluationJudgeOptimizationRequest = {
        judge_key: optimizeTarget.key,
        dataset: {
          set_name: dataset.set_name,
          category: dataset.category,
          artifact_dir: dataset.artifact_dir,
          train_path: trainSplit.path,
          dev_path: devSplit.path,
          test_path: testSplit.path,
          runs_path: dataset.artifact_dir ? `${dataset.artifact_dir}/generated_runs.json` : undefined,
        },
        max_metric_calls: parsedBudget,
      }
      if (parsedSeed !== undefined) {
        payload.seed = parsedSeed
      }
      const response = await api.startJudgeOptimization(payload)
      setActiveOptimization({ jobId: response.job_id, status: response.status ?? null })
      optimizationCompletionRef.current = false
      setIsOptimizeDialogOpen(false)
      setOptimizeTarget(null)
      setOptimizeForm(defaultOptimizeForm())
      setOptimizeError(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to start judge optimization'
      setOptimizeError(message)
    } finally {
      setIsStartingOptimization(false)
    }
  }
  const handleDismissOptimization = () => {
    if (optimizationSocketRef.current) {
      try {
        optimizationSocketRef.current.close()
      } catch {}
      optimizationSocketRef.current = null
    }
    setActiveOptimization(null)
    optimizationCompletionRef.current = false
  }
  const canSubmit = !isSaving && (!isCreateMode || form.agentLabel.trim().length > 0)
  const optimizationStatus = activeOptimization?.status
  const optimizationState = optimizationStatus?.state
  const optimizationCompleted = optimizationStatus?.completed ?? 0
  const optimizationTotal = optimizationStatus?.total ?? 1
  const optimizationPercent = optimizationState === 'completed' ? 100 : Math.min(100, Math.round((optimizationCompleted / Math.max(1, optimizationTotal)) * 100))
  const optimizationDatasetLabel = optimizationStatus ? `${optimizationStatus.dataset.set_name} • ${optimizationStatus.dataset.category}` : ''
  const canStartOptimization = optimizeForm.datasetKey.trim().length > 0 && optimizeForm.maxMetricCalls.trim().length > 0 && !isStartingOptimization
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
          <span>Unable to load judges{resolvedProjectName ? ` for ${resolvedProjectName}` : ''}.</span>
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
  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <h2 className="text-lg font-semibold">Judges</h2>
          <p className="text-sm text-muted-foreground">
            {resolvedProjectName ? `Create and refine judges for ${resolvedProjectName}.` : 'Create and refine evaluation judges.'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Judge
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        {activeOptimization ? (
          <div className="mb-4 rounded-md border border-border bg-background px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">Judge Optimization</div>
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
                  <span>{formatTimestamp(optimizationStatus?.updated_at)}</span>
                </div>
                <div className="mt-1 h-2 w-full rounded bg-muted">
                  <div className="h-2 rounded bg-muted-foreground transition-all" style={{ width: `${optimizationPercent}%` }} />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                Started {formatTimestamp(optimizationStatus?.started_at) || 'pending'}
                {optimizationStatus?.finished_at ? ` • Finished ${formatTimestamp(optimizationStatus.finished_at)}` : ''}
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
        {judges.length === 0 ? (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-border p-8 text-sm text-muted-foreground">
            No judges configured yet.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full border-separate border-spacing-0">
              <thead className="bg-muted/40">
                <tr>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Judge</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Model</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Source</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Failure Category</th>
                  <th className="border-b border-border/60 px-4 py-3 text-left text-xs font-semibold uppercase text-muted-foreground">Updated</th>
                  <th className="border-b border-border/60 px-4 py-3 text-right text-xs font-semibold uppercase text-muted-foreground">Actions</th>
                </tr>
              </thead>
              <tbody>
                {judges.map((judge) => {
                  const updatedLabel = judge.updated_at ? formatTimestamp(judge.updated_at) : '—'
                  return (
                    <tr key={judge.key} className="border-b border-border/60">
                      <td className="px-4 py-3 align-top">
                        <div className="flex items-start gap-2">
                          <div className="flex h-8 w-8 items-center justify-center rounded-md border border-border bg-muted/40">
                            <Gavel className="h-4 w-4 text-muted-foreground" />
                          </div>
                          <div>
                            <div className="text-sm font-semibold text-foreground">{judge.agent_label || judge.key}</div>
                            <div className="text-xs text-muted-foreground">{judge.agent_name || judge.key}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{judge.model || '—'}</td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{judge.source === 'history' ? 'History' : 'Assistant output'}</td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{judge.failure_category || '—'}</td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">{updatedLabel}</td>
                      <td className="px-4 py-3 align-top text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => openEdit(judge)}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                          >
                            <Edit3 className="h-3.5 w-3.5" />
                            Edit
                          </button>
                          <button
                            type="button"
                            onClick={() => openOptimize(judge)}
                            disabled={isStartingOptimization || datasetOptions.length === 0}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
                          >
                            <Sparkles className="h-3.5 w-3.5" />
                            Optimize
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(judge)}
                            disabled={deleteJudge.isPending}
                            className="inline-flex items-center gap-1 rounded-md border border-destructive px-3 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {isFormOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form onSubmit={handleSubmit} className="w-full max-w-2xl rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">{editingJudge ? 'Edit Judge' : 'Create Judge'}</h3>
                <p className="text-sm text-muted-foreground">
                  {editingJudge ? 'Update judge configuration and prompts.' : 'Define a new LLM judge for evaluation.'}
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
            {isCreateMode ? (
              <div className="space-y-4">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Name</label>
                  <input
                    value={form.agentLabel}
                    onChange={(event) => {
                      const value = event.target.value
                      setForm((prev) => {
                        const slug = slugify(value)
                        return { ...prev, agentLabel: value, key: slug, agentName: slug }
                      })
                    }}
                    placeholder="Quality Judge"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Failure Category</label>
                  <select
                    value={form.failureCategory}
                    onChange={(event) => setForm((prev) => ({ ...prev, failureCategory: event.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  >
                    <option value="">Select failure category</option>
                    {failureCategoryOptions.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Model</label>
                    <input
                      value={form.model}
                      onChange={(event) => setForm((prev) => ({ ...prev, model: event.target.value }))}
                      placeholder="gpt-4.1-mini"
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Agent Label</label>
                    <input
                      value={form.agentLabel}
                      onChange={(event) => setForm((prev) => ({ ...prev, agentLabel: event.target.value }))}
                      placeholder="Quality Judge"
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Agent Name</label>
                    <input
                      value={form.agentName}
                      onChange={(event) => setForm((prev) => ({ ...prev, agentName: event.target.value }))}
                      placeholder="quality_judge"
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Source</label>
                    <select
                      value={form.source}
                      onChange={(event) => setForm((prev) => ({ ...prev, source: event.target.value }))}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    >
                      <option value="final_assistant_text">Assistant output</option>
                      <option value="history">Conversation history</option>
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Failure Category</label>
                    <select
                      value={form.failureCategory}
                      onChange={(event) => setForm((prev) => ({ ...prev, failureCategory: event.target.value }))}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    >
                      <option value="">Select failure category</option>
                      {failureCategoryOptions.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="mt-4 space-y-4">
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Instructions</label>
                    <textarea
                      value={form.instructions}
                      onChange={(event) => setForm((prev) => ({ ...prev, instructions: event.target.value }))}
                      rows={6}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Template</label>
                    <textarea
                      value={form.template}
                      onChange={(event) => setForm((prev) => ({ ...prev, template: event.target.value }))}
                      rows={6}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                    />
                  </div>
                </div>
              </>
            )}
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
                disabled={!canSubmit}
              >
                {isSaving ? 'Saving…' : editingJudge ? 'Save Changes' : 'Create Judge'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {isOptimizeDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form onSubmit={handleOptimizeSubmit} className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Optimize Judge</h3>
                <p className="text-sm text-muted-foreground">
                  {optimizeTarget ? `Optimize ${optimizeTarget.agent_label || optimizeTarget.key} against a dataset.` : 'Select a dataset and budget to optimize this judge.'}
                </p>
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
                  <div className="mt-1 text-xs text-muted-foreground">Maximum evaluation calls during optimization.</div>
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
    </div>
  )
}

interface SuiteRunDetailProps {
  jobId: string
  status: EvaluationSuiteRunStatus | null
  onBack: () => void
  onDismiss: () => void
  onNavigateToLogs?: (projectName: string | null, workflowName: string | null) => void
  projectName?: string | null
}

function SuiteRunDetail({ jobId, status, onBack, onDismiss, onNavigateToLogs, projectName }: SuiteRunDetailProps) {
  const state = status?.state ?? 'pending'
  const total = status?.total ?? 0
  const completed = status?.completed ?? 0
  const percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0
  const workflowName = status?.workflow_name && status.workflow_name.trim().length > 0 ? status.workflow_name.trim() : null
  const summary = status?.summary ?? null
  const resultPath = status?.result_path ? formatScenarioPath(status.result_path) : null
  const stateLabel = state.replace(/_/g, ' ')
  const includeLabel = status?.include_unmeasured ? 'Including unmeasured scenarios' : 'Measured scenarios only'

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
            <div className="flex items-center gap-2 text-lg font-semibold">
              <BarChart3 className="h-4 w-4 text-muted-foreground" />
              Evaluation Suite Run
            </div>
            <div className="mt-1 text-xs text-muted-foreground">Job {jobId}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {workflowName && onNavigateToLogs ? (
            <button
              type="button"
              onClick={() => onNavigateToLogs(projectName ?? null, workflowName)}
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              View Logs
            </button>
          ) : null}
          <button
            type="button"
            onClick={onDismiss}
            className="flex h-9 w-9 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-auto px-6 py-4">
        <div className="space-y-4">
          <div className="rounded-md border border-border bg-background px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold capitalize">{stateLabel}</div>
                <div className="text-xs text-muted-foreground">Updated {formatTimestamp(status?.updated_at)}</div>
              </div>
              {state === 'running' ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
            </div>
            <div className="mt-3 space-y-2">
              <div>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>
                    {completed} / {total || completed} complete
                  </span>
                  <span>{formatTimestamp(status?.started_at) || 'pending'}</span>
                </div>
                <div className="mt-1 h-2 w-full rounded bg-muted">
                  <div className="h-2 rounded bg-muted-foreground transition-all" style={{ width: `${percent}%` }} />
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                {includeLabel}
                {status?.agent ? ` • Agent ${status.agent}` : ''}
              </div>
              {status?.select ? (
                <div className="text-xs text-muted-foreground">Select {status.select}</div>
              ) : null}
              {status?.exclude ? (
                <div className="text-xs text-muted-foreground">Exclude {status.exclude}</div>
              ) : null}
              {resultPath ? (
                <div className="text-xs text-muted-foreground">
                  Results written to <span className="font-mono">{resultPath}</span>
                </div>
              ) : null}
              {workflowName ? (
                <div className="text-xs text-muted-foreground">
                  Workflow <span className="font-mono">{workflowName}</span>
                </div>
              ) : null}
              {status?.finished_at ? (
                <div className="text-xs text-muted-foreground">Finished {formatTimestamp(status.finished_at)}</div>
              ) : null}
              {status?.error ? (
                <div className="rounded border border-destructive/60 bg-destructive/10 px-3 py-2 text-xs text-destructive">{status.error}</div>
              ) : null}
            </div>
          </div>
          {summary ? (
            <div className="rounded-md border border-border bg-background px-4 py-3">
              <div className="text-sm font-semibold">Summary</div>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <div>
                  <div className="text-xs text-muted-foreground">Checks</div>
                  <div className="text-base font-semibold">{summary.passed_checks}/{summary.total_checks} passed</div>
                  <div className="text-xs text-muted-foreground">Failed {summary.failed_checks} • Skipped {summary.skipped_checks}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Measures</div>
                  <div className="text-base font-semibold">{summary.measures_total - summary.measures_failed - summary.measures_skipped}/{summary.measures_total} passed</div>
                  <div className="text-xs text-muted-foreground">Failed {summary.measures_failed} • Skipped {summary.measures_skipped}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Metrics</div>
                  <div className="text-base font-semibold">{summary.metrics_total - summary.metrics_failed - summary.metrics_skipped}/{summary.metrics_total} met target</div>
                  <div className="text-xs text-muted-foreground">Failed {summary.metrics_failed} • Skipped {summary.metrics_skipped}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">Scenarios</div>
                  <div className="text-base font-semibold">{summary.scenarios_included}/{summary.scenarios_total} evaluated</div>
                  <div className="text-xs text-muted-foreground">Skipped {summary.scenarios_skipped}</div>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function ScenariosPanel({ projectName, isAvailable, onNavigateToLogs, onTabChange, onDatasetFocus }: { projectName?: string | null; isAvailable: boolean; onNavigateToLogs?: (projectName: string | null, workflowName: string | null) => void; onTabChange: (tab: EvaluationTab) => void; onDatasetFocus?: (focus: DatasetFocusRequest) => void }) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError, refetch, isFetching } = useEvaluationScenarios(isAvailable)
  const { data: dimensionsData } = useDimensions(isAvailable)
  const [pendingKey, setPendingKey] = useState<string | null>(null)
  const [selectedSetPath, setSelectedSetPath] = useState<string | null>(null)
  const [runTrigger, setRunTrigger] = useState<ScenarioRunTrigger | null>(null)
  const [splitTrigger, setSplitTrigger] = useState<ScenarioSplitTrigger | null>(null)
  const [isGenerateDialogOpen, setIsGenerateDialogOpen] = useState(false)
  const [generateName, setGenerateName] = useState('')
  const [generateCount, setGenerateCount] = useState('')
  const [generateSeed, setGenerateSeed] = useState('')
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [isStartingGeneration, setIsStartingGeneration] = useState(false)
  const [activeGeneration, setActiveGeneration] = useState<{ jobId: string; status: EvaluationScenarioGenerationStatus | null } | null>(null)
  const generationSocketRef = useRef<WebSocket | null>(null)
  const generationCompletionRef = useRef(false)
  const [isSuiteDialogOpen, setIsSuiteDialogOpen] = useState(false)
  const [suiteName, setSuiteName] = useState('')
  const [suiteSelect, setSuiteSelect] = useState('')
  const [suiteExclude, setSuiteExclude] = useState('')
  const [suiteIncludeUnmeasured, setSuiteIncludeUnmeasured] = useState(false)
  const [suiteOutputPath, setSuiteOutputPath] = useState('')
  const [suiteAgent, setSuiteAgent] = useState('')
  const [suiteError, setSuiteError] = useState<string | null>(null)
  const [isStartingSuite, setIsStartingSuite] = useState(false)
  const [activeSuiteRun, setActiveSuiteRun] = useState<{ jobId: string; status: EvaluationSuiteRunStatus | null } | null>(null)
  const [showSuiteDetail, setShowSuiteDetail] = useState(false)
  const suiteSocketRef = useRef<WebSocket | null>(null)
  const suiteCompletionRef = useRef(false)
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
       setSplitTrigger(null)
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

  const openSuiteDialog = () => {
    setSuiteName('')
    setSuiteSelect('')
    setSuiteExclude('')
    setSuiteOutputPath('')
    setSuiteAgent('')
    setSuiteIncludeUnmeasured(false)
    setSuiteError(null)
    setIsSuiteDialogOpen(true)
  }

  const closeSuiteDialog = () => {
    if (isStartingSuite) {
      return
    }
    setIsSuiteDialogOpen(false)
    setSuiteError(null)
  }

  const handleDismissGeneration = () => {
    if (generationSocketRef.current) {
      generationSocketRef.current.close()
      generationSocketRef.current = null
    }
    setActiveGeneration(null)
    generationCompletionRef.current = false
  }

  const handleSuiteSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (isStartingSuite) {
      return
    }
    const trimmedName = suiteName.trim()
    if (!trimmedName) {
      setSuiteError('Suite name is required')
      return
    }
    const payload: EvaluationSuiteRunRequest = {
      name: trimmedName,
      select: suiteSelect.trim() ? suiteSelect.trim() : undefined,
      exclude: suiteExclude.trim() ? suiteExclude.trim() : undefined,
      include_unmeasured: suiteIncludeUnmeasured,
      output_path: suiteOutputPath.trim() ? suiteOutputPath.trim() : undefined,
      agent: suiteAgent.trim() ? suiteAgent.trim() : undefined,
    }
    setIsStartingSuite(true)
    setSuiteError(null)
    try {
      const response = await api.startEvaluationSuiteRun(payload)
      setActiveSuiteRun({ jobId: response.job_id, status: response.status ?? null })
      suiteCompletionRef.current = false
      setShowSuiteDetail(true)
      setIsSuiteDialogOpen(false)
      setSuiteName('')
      setSuiteSelect('')
      setSuiteExclude('')
      setSuiteOutputPath('')
      setSuiteAgent('')
      setSuiteIncludeUnmeasured(false)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to start evaluation suite'
      setSuiteError(message)
    } finally {
      setIsStartingSuite(false)
    }
  }

  const handleDismissSuiteRun = () => {
    if (suiteSocketRef.current) {
      suiteSocketRef.current.close()
      suiteSocketRef.current = null
    }
    setActiveSuiteRun(null)
    setShowSuiteDetail(false)
    suiteCompletionRef.current = false
  }

  const handleShowSuiteDetail = () => {
    if (activeSuiteRun) {
      setShowSuiteDetail(true)
    }
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
    return [...data.golden, ...data.generated]
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

  const suiteStatus = activeSuiteRun?.status ?? null
  const suiteState = suiteStatus?.state ?? null
  const suiteCompleted = suiteStatus?.completed ?? 0
  const suiteTotalRaw = suiteStatus?.total ?? 0
  const suiteEffectiveTotal = suiteTotalRaw > 0 ? suiteTotalRaw : suiteCompleted
  const suitePercent = suiteState === 'completed' ? 100 : suiteEffectiveTotal > 0 ? Math.min(100, Math.round((suiteCompleted / suiteEffectiveTotal) * 100)) : 0

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

  useEffect(() => {
    const jobId = activeSuiteRun?.jobId
    if (!jobId) {
      if (suiteSocketRef.current) {
        try {
          suiteSocketRef.current.close()
        } catch {}
        suiteSocketRef.current = null
      }
      return
    }
    let socket: WebSocket | null = null
    try {
      socket = api.createSuiteRunWebSocket(jobId)
      suiteSocketRef.current = socket
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data)
          if (!payload || payload.job_id !== jobId) {
            return
          }
          const status = payload.status as EvaluationSuiteRunStatus | null | undefined
          if (status !== undefined) {
            setActiveSuiteRun({ jobId, status: status ?? null })
            const state = status?.state
            if (state === 'completed' || state === 'failed' || state === 'cancelled') {
              suiteCompletionRef.current = true
              try {
                socket?.close()
              } catch {}
            }
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
        if (suiteSocketRef.current === socket) {
          suiteSocketRef.current = null
        }
        if (!suiteCompletionRef.current) {
          api
            .getEvaluationSuiteRunStatus(jobId)
            .then((response) => {
              if (!response || !response.status) {
                return
              }
              const status = response.status as EvaluationSuiteRunStatus
              setActiveSuiteRun({ jobId: response.job_id, status })
              const state = status.state
              if (state === 'completed' || state === 'failed' || state === 'cancelled') {
                suiteCompletionRef.current = true
              }
            })
            .catch(() => {})
        }
      }
    } catch {
      api
        .getEvaluationSuiteRunStatus(jobId)
        .then((response) => {
          if (!response || !response.status) {
            return
          }
          const status = response.status as EvaluationSuiteRunStatus
          setActiveSuiteRun({ jobId: response.job_id, status })
          const state = status.state
          if (state === 'completed' || state === 'failed' || state === 'cancelled') {
            suiteCompletionRef.current = true
          }
        })
        .catch(() => {})
    }
    return () => {
      if (suiteSocketRef.current === socket) {
        suiteSocketRef.current = null
      }
      if (socket) {
        try {
          socket.close()
        } catch {}
      }
    }
  }, [activeSuiteRun?.jobId])

  useEffect(() => {
    const status = activeSuiteRun?.status
    if (!status) {
      return
    }
    if (status.state === 'completed') {
      if (!suiteCompletionRef.current) {
        suiteCompletionRef.current = true
        queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenarios'] })
        queryClient.invalidateQueries({ queryKey: ['evaluation', 'datasets'] })
      }
    } else if (status.state === 'failed' || status.state === 'cancelled') {
      suiteCompletionRef.current = true
    }
  }, [activeSuiteRun?.status?.state, queryClient])

  if (showSuiteDetail && activeSuiteRun) {
    return (
      <SuiteRunDetail
        jobId={activeSuiteRun.jobId}
        status={activeSuiteRun.status ?? null}
        onBack={() => setShowSuiteDetail(false)}
        onDismiss={handleDismissSuiteRun}
        onNavigateToLogs={onNavigateToLogs}
        projectName={projectName ?? null}
      />
    )
  }

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

  const handleSplitSuccess = (response: EvaluationScenarioSplitResponse) => {
    const dataset = response?.dataset
    if (dataset) {
      const firstSplit = dataset.splits[0] ?? null
      onDatasetFocus?.({
        set_name: dataset.set_name,
        category: dataset.category,
        split_path: firstSplit?.path ?? null,
      })
    }
    setPendingKey(null)
    setSelectedSetPath(null)
    setRunTrigger(null)
    setSplitTrigger(null)
    onTabChange('datasets')
  }

  const handleViewSet = (set: EvaluationScenarioSet) => {
    setRunTrigger(null)
    setSplitTrigger(null)
    setSelectedSetPath(set.path)
  }

  const handleRunSet = (set: EvaluationScenarioSet) => {
    const token = Date.now()
    setRunTrigger({ path: set.path, token })
    setSplitTrigger(null)
    setSelectedSetPath(set.path)
  }

  const handleSplitSet = (set: EvaluationScenarioSet) => {
    if (set.kind === 'golden') {
      return
    }
    const token = Date.now()
    setSplitTrigger({ path: set.path, token })
    setRunTrigger(null)
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

  const handleViewRun = (workflow: string) => {
    if (!workflow) {
      return
    }
    const normalized = workflow.trim()
    if (!normalized) {
      return
    }
    onNavigateToLogs?.(projectName ?? null, normalized)
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
          setSplitTrigger(null)
        }}
        canPromote={canPromote}
        onPromote={(scenario) => promoteScenario(selectedSet, scenario)}
        pendingKey={pendingKey}
        isPromoting={promotion.isPending}
        goldenIds={goldenIds}
        goldenNames={goldenNames}
        runTrigger={runTrigger}
        onRunTriggerConsumed={() => setRunTrigger(null)}
        splitTrigger={splitTrigger}
        onSplitTriggerConsumed={() => setSplitTrigger(null)}
        onViewRun={handleViewRun}
        onDeleteSet={handleDeleteSet}
        isDeletingSet={deleteSet.isPending}
        onSplitSuccess={handleSplitSuccess}
      />
    )
  }

  const sections: { title: string; sets: EvaluationScenarioSet[] }[] = [
    { title: 'Golden Scenarios', sets: data.golden },
    { title: 'Scenario Sets', sets: data.generated },
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
            onClick={openSuiteDialog}
            disabled={isStartingSuite}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted disabled:opacity-50"
          >
            <BarChart3 className="h-3.5 w-3.5" />
            {isStartingSuite ? 'Starting…' : 'Run Eval Suite'}
          </button>
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
        {activeSuiteRun ? (
          <div className="mb-4 rounded-md border border-border bg-background px-4 py-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold">Evaluation Suite Run</div>
                <div className="text-xs text-muted-foreground">{activeSuiteRun.jobId}</div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium capitalize">{suiteState ? suiteState.replace(/_/g, ' ') : 'pending'}</span>
                {suiteState === 'running' ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                <button
                  type="button"
                  onClick={handleDismissSuiteRun}
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
                    {suiteCompleted} / {suiteEffectiveTotal || suiteCompleted} complete
                  </span>
                  <span>{formatTimestamp(suiteStatus?.updated_at)}</span>
                </div>
                <div className="mt-1 h-2 w-full rounded bg-muted">
                  <div className="h-2 rounded bg-muted-foreground transition-all" style={{ width: `${suitePercent}%` }} />
                </div>
              </div>
              {suiteStatus?.summary ? (
                <div className="text-xs text-muted-foreground">
                  {suiteStatus.summary.passed_checks} passed • {suiteStatus.summary.failed_checks} failed • {suiteStatus.summary.skipped_checks} skipped
                </div>
              ) : null}
              {suiteStatus?.result_path ? (
                <div className="text-xs text-muted-foreground">
                  Results <span className="font-mono">{formatScenarioPath(suiteStatus.result_path)}</span>
                </div>
              ) : null}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleShowSuiteDetail}
                  className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                >
                  <BarChart3 className="h-3.5 w-3.5" />
                  View Details
                </button>
              </div>
            </div>
          </div>
        ) : null}
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
                  section.sets.map((set) => {
                    const workflowName = extractWorkflowFromMetadata(set.metadata)
                    const labeledFromMetadata =
                      typeof set.metadata?.labeled_count === 'number' ? set.metadata.labeled_count : null
                    const analysisFromMetadata =
                      typeof set.metadata?.analysis_count === 'number' ? set.metadata.analysis_count : null
                    const hasFailureLabels = labeledFromMetadata !== null
                      ? labeledFromMetadata > 0
                      : set.scenarios.some((scenario) => Array.isArray(scenario.failure_categories) && scenario.failure_categories.length > 0)
                    const hasScenarioAnnotations = analysisFromMetadata !== null
                      ? analysisFromMetadata > 0
                      : set.scenarios.some((scenario) => {
                          const analysis = scenario.analysis || (scenario.metadata?.analysis as Record<string, any> | undefined)
                          if (!analysis) {
                            return false
                          }
                          if (typeof analysis.judgment === 'string' && analysis.judgment.trim().length > 0) {
                            return true
                          }
                          if (typeof analysis.notes === 'string' && analysis.notes.trim().length > 0) {
                            return true
                          }
                          return false
                        })
                    const canSplitFromIndex = set.kind !== 'golden' && (hasScenarioAnnotations || hasFailureLabels)
                    return (
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
                            {typeof labeledFromMetadata === 'number' ? ` • ${labeledFromMetadata} labeled` : ''}
                            {typeof analysisFromMetadata === 'number' ? ` • ${analysisFromMetadata} annotated` : ''}
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
                          {workflowName && onNavigateToLogs ? (
                            <button
                              type="button"
                              onClick={() => onNavigateToLogs(projectName ?? null, workflowName)}
                              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                              View Run
                            </button>
                          ) : null}
                          <button
                            type="button"
                            onClick={() => handleRunSet(set)}
                            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                          >
                            <Play className="h-3.5 w-3.5" />
                            Run Set
                          </button>
                          {canSplitFromIndex ? (
                            <button
                              type="button"
                              onClick={() => handleSplitSet(set)}
                              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted"
                            >
                              <SplitSquareVertical className="h-3.5 w-3.5" />
                              Split Set
                            </button>
                          ) : null}
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
                    )
                  })
                )}
              </div>
            ))}
          </div>
        )}
      </div>
      {isSuiteDialogOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <form onSubmit={handleSuiteSubmit} className="w-full max-w-lg rounded-lg border border-border bg-background p-6 shadow-lg">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Run Evaluation Suite</h3>
                <p className="text-sm text-muted-foreground">Execute configured evaluation scenarios and record metrics.</p>
              </div>
              <button
                type="button"
                onClick={closeSuiteDialog}
                className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                disabled={isStartingSuite}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Run Name</label>
                <input
                  value={suiteName}
                  onChange={(event) => setSuiteName(event.target.value)}
                  placeholder="compliance_suite"
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Agent Override</label>
                  <input
                    value={suiteAgent}
                    onChange={(event) => setSuiteAgent(event.target.value)}
                    placeholder="Leave blank for entrypoint"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Output Path</label>
                  <input
                    value={suiteOutputPath}
                    onChange={(event) => setSuiteOutputPath(event.target.value)}
                    placeholder="artifacts/eval/compliance/run_results.json"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Select Filter</label>
                  <input
                    value={suiteSelect}
                    onChange={(event) => setSuiteSelect(event.target.value)}
                    placeholder="Tag expression"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase text-muted-foreground">Exclude Filter</label>
                  <input
                    value={suiteExclude}
                    onChange={(event) => setSuiteExclude(event.target.value)}
                    placeholder="Exclude expression"
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={suiteIncludeUnmeasured}
                  onChange={(event) => setSuiteIncludeUnmeasured(event.target.checked)}
                  className="h-4 w-4 rounded border-border text-primary focus:ring-primary/50"
                />
                Include scenarios without measures
              </label>
            </div>
            {suiteError ? (
              <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">{suiteError}</div>
            ) : null}
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeSuiteDialog}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted"
                disabled={isStartingSuite}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                disabled={isStartingSuite}
              >
                {isStartingSuite ? 'Starting…' : 'Start Suite'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
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

export function EvaluationView({ activeTab, onTabChange, projectName, isAvailable, isLoadingMetadata, onNavigateToLogs }: EvaluationViewProps) {
  const [pendingDatasetFocus, setPendingDatasetFocus] = useState<DatasetFocusRequest | null>(null)
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

  if (activeTab === 'scenarios') {
    return (
      <ScenariosPanel
        projectName={projectName}
        isAvailable={isAvailable}
        onNavigateToLogs={onNavigateToLogs}
        onTabChange={onTabChange}
        onDatasetFocus={(focus) => setPendingDatasetFocus(focus)}
      />
    )
  }

  if (activeTab === 'datasets') {
    return (
      <DatasetsPanel
        projectName={projectName}
        onTabChange={onTabChange}
        onNavigateToLogs={onNavigateToLogs}
        focusRequest={pendingDatasetFocus}
        onFocusConsumed={() => setPendingDatasetFocus(null)}
      />
    )
  }

  if (activeTab === 'dimensions') {
    return <DimensionsPanel projectName={projectName} />
  }

  if (activeTab === 'judges') {
    return <JudgesPanel projectName={projectName} />
  }

  return (
    <div className="flex h-full w-full items-center justify-center text-muted-foreground">
      Unknown evaluation tab.
    </div>
  )
}
