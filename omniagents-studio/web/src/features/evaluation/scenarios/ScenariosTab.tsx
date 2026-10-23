import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@omniagents/ui/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowUpToLine, Pencil, Play, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'

import { cn } from '../../../lib/utils'
import { api } from '../../../lib/api'
import type { Scenario, ScenarioMatch, ScenarioSet } from '../../../types/evaluation'
import { excerpt, relativePath, when } from '../format'
import { JobPanel } from '../jobs'
import { useJob } from '../useJob'
import { DimensionsPanel } from './DimensionsPanel'
import { ScenarioDialog } from './ScenarioDialog'

const DIMENSIONS = '__dimensions__'

function matchOf(scenario: Scenario, index: number): ScenarioMatch {
  return scenario.id ? { id: String(scenario.id) } : { name: scenario.name, index }
}

function what(scenario: Scenario): string {
  if (scenario.goal) return `Simulated user: ${excerpt(scenario.goal)}`
  if (scenario.prompts?.length) return `${scenario.prompts.length} messages: ${excerpt(scenario.prompts[0])}`
  return excerpt(scenario.prompt)
}

/**
 * The project's scenarios (what the suite runs) and generated scenario
 * sets, each edited in its file; plus the dimensions sets are generated
 * from.
 */
export function ScenariosTab({ agent }: { agent: string | null }) {
  const queryClient = useQueryClient()
  const sets = useQuery({ queryKey: ['evaluation', 'scenario-sets'], queryFn: () => api.getScenarioSets() })
  const categories = useQuery({ queryKey: ['evaluation', 'categories'], queryFn: () => api.getCategories() })
  const measures = useQuery({ queryKey: ['evaluation', 'measures'], queryFn: () => api.getMeasures() })
  const [selected, setSelected] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ scenario: Scenario | null; match: ScenarioMatch | null } | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const runJob = useJob([['evaluation', 'runs']])

  const all = useMemo(() => sets.data?.sets ?? [], [sets.data])
  const root = sets.data?.evaluations_dir ?? null
  const current = useMemo<ScenarioSet | null>(() => {
    if (selected === DIMENSIONS) return null
    return all.find((set) => set.path === selected) ?? all[0] ?? null
  }, [all, selected])

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation', 'scenario-sets'] })
  const save = useMutation({
    mutationFn: (scenario: Scenario) =>
      editing?.match
        ? api.updateScenario(current!.path, editing.match, scenario)
        : api.addScenario(current!.path, scenario),
    onSuccess: () => {
      setEditing(null)
      refresh()
    },
    onError: (cause: Error) => setSaveError(cause.message),
  })
  const act = useMutation({
    mutationFn: (action: () => Promise<unknown>) => action(),
    onSuccess: refresh,
  })

  if (sets.isLoading) {
    return <p className="p-6 text-sm text-muted-foreground">Loading scenarios…</p>
  }
  if (sets.error) {
    return <p className="p-6 text-sm text-destructive">{(sets.error as Error).message}</p>
  }

  const project = all.filter((set) => set.kind === 'project')
  const generated = all.filter((set) => set.kind === 'generated')

  return (
    <div className="grid h-full grid-cols-[16rem_1fr]">
      <nav className="space-y-4 overflow-y-auto border-r border-border p-3 text-sm">
        <SetList title="Project scenarios" hint="What the suite runs" sets={project} current={current} root={root} onSelect={setSelected} />
        <SetList title="Generated sets" hint="generated/" sets={generated} current={current} root={root} onSelect={setSelected} />
        <button
          type="button"
          onClick={() => setSelected(DIMENSIONS)}
          className={cn(
            'w-full rounded-md px-2 py-1.5 text-left hover:bg-muted',
            selected === DIMENSIONS && 'bg-muted font-medium',
          )}
        >
          Dimensions and generation
        </button>
      </nav>
      <div className="min-w-0 overflow-y-auto p-6">
        {selected === DIMENSIONS ? (
          <DimensionsPanel />
        ) : !current ? (
          <p className="text-sm text-muted-foreground">
            No scenarios yet. Add a file to scenarios/ in the evaluations directory, or generate a set from dimensions.
          </p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold">{current.label}</h3>
                <p className="text-xs text-muted-foreground">
                  {current.kind === 'project' ? 'Run by the suite' : 'Generated'} · {current.scenarios.length} scenarios ·
                  updated {when(current.updated_at)}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={runJob.running || current.scenarios.length === 0}
                  onClick={() => void runJob.start({ kind: 'run', agent, scenario_set: current.path })}
                >
                  <Play /> Run these
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setSaveError(null)
                    setEditing({ scenario: null, match: null })
                  }}
                >
                  <Plus /> Scenario
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    if (window.confirm(`Delete ${current.label}?`)) {
                      act.mutate(() => api.removeScenarioSet(current.path))
                      setSelected(null)
                    }
                  }}
                >
                  <Trash2 /> Delete set
                </Button>
              </div>
            </div>
            <JobPanel job={runJob.job} error={runJob.error} onDismiss={runJob.dismiss} />
            {act.error ? <p className="text-sm text-destructive">{(act.error as Error).message}</p> : null}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Scenario</TableHead>
                  <TableHead>What is sent</TableHead>
                  <TableHead>Probes</TableHead>
                  <TableHead>Scored by</TableHead>
                  <TableHead className="w-28" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {current.scenarios.map((scenario, index) => (
                  <TableRow key={`${scenario.id ?? scenario.name}-${index}`}>
                    <TableCell className="align-top">
                      <div className="font-medium">{scenario.name}</div>
                      {scenario.tags?.length ? (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {scenario.tags.map((tag) => (
                            <Badge key={tag} variant="outline" className="text-[10px]">
                              {tag}
                            </Badge>
                          ))}
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell className="max-w-md whitespace-normal align-top text-xs text-muted-foreground">{what(scenario)}</TableCell>
                    <TableCell className="align-top text-xs">{(scenario.failure_categories ?? []).join(', ') || '—'}</TableCell>
                    <TableCell className="align-top font-mono text-[11px]">
                      {(scenario.measures ?? []).map((m) => (typeof m === 'string' ? m : String(m.name))).join(', ') ||
                        (scenario.expect ? 'expectations' : '—')}
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="flex justify-end gap-1">
                        {current.kind === 'generated' ? (
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            title="Add to the project's scenarios"
                            aria-label="Promote"
                            onClick={() => act.mutate(() => api.promoteScenario(current.path, matchOf(scenario, index)))}
                          >
                            <ArrowUpToLine />
                          </Button>
                        ) : null}
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label="Edit"
                          onClick={() => {
                            setSaveError(null)
                            setEditing({ scenario, match: matchOf(scenario, index) })
                          }}
                        >
                          <Pencil />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label="Remove"
                          onClick={() => {
                            if (window.confirm(`Remove ${scenario.name}?`)) {
                              act.mutate(() => api.removeScenario(current.path, matchOf(scenario, index)))
                            }
                          }}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <p className="text-xs text-muted-foreground">{relativePath(current.path, root)}</p>
          </div>
        )}
      </div>
      <ScenarioDialog
        open={editing !== null}
        scenario={editing?.scenario ?? null}
        categories={categories.data?.categories ?? []}
        measures={measures.data?.measures ?? []}
        saving={save.isPending}
        error={saveError}
        onSave={(scenario) => save.mutate(scenario)}
        onClose={() => setEditing(null)}
      />
    </div>
  )
}

function SetList({
  title,
  hint,
  sets,
  current,
  root,
  onSelect,
}: {
  title: string
  hint: string
  sets: ScenarioSet[]
  current: ScenarioSet | null
  root: string | null
  onSelect: (path: string) => void
}) {
  return (
    <div>
      <div className="mb-1 px-2 text-xs font-semibold uppercase text-muted-foreground" title={hint}>
        {title}
      </div>
      {sets.length === 0 ? <p className="px-2 text-xs text-muted-foreground">None</p> : null}
      {sets.map((set) => (
        <button
          key={set.path}
          type="button"
          onClick={() => onSelect(set.path)}
          title={relativePath(set.path, root)}
          className={cn(
            'flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left hover:bg-muted',
            current?.path === set.path && 'bg-muted font-medium',
          )}
        >
          <span className="truncate">{set.label}</span>
          <span className="text-xs text-muted-foreground">{set.scenarios.length}</span>
        </button>
      ))}
    </div>
  )
}
