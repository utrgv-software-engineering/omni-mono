import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { NativeSelect, NativeSelectOption } from '@omniagents/ui/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@omniagents/ui/table'
import { Textarea } from '@omniagents/ui/textarea'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { cn } from '../../../lib/utils'
import { api } from '../../../lib/api'
import type { Judge, JudgeDataset } from '../../../types/evaluation'
import { excerpt } from '../format'
import { JobPanel } from '../jobs'
import { useJob } from '../useJob'

/**
 * Judges (a model that reads a run and decides a failure category), the
 * datasets they are checked and optimized on, and optimizing one.
 */
export function JudgesTab() {
  const judges = useQuery({ queryKey: ['evaluation', 'judges'], queryFn: () => api.getJudges() })
  const [selected, setSelected] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const list = judges.data?.judges ?? []
  const current = adding ? null : (list.find((judge) => judge.key === selected) ?? list[0] ?? null)

  return (
    <div className="grid h-full grid-cols-[16rem_1fr]">
      <nav className="space-y-1 overflow-y-auto border-r border-border p-3 text-sm">
        <Button variant="outline" size="sm" className="mb-2 w-full" onClick={() => setAdding(true)}>
          <Plus /> Judge
        </Button>
        {judges.error ? <p className="text-xs text-destructive">{(judges.error as Error).message}</p> : null}
        {list.map((judge) => (
          <button
            key={judge.key}
            type="button"
            onClick={() => {
              setAdding(false)
              setSelected(judge.key)
            }}
            className={cn('w-full rounded-md px-2 py-1.5 text-left hover:bg-muted', current?.key === judge.key && 'bg-muted font-medium')}
          >
            <div className="truncate">{judge.label}</div>
            <div className="truncate text-xs text-muted-foreground">{judge.failure_category ?? 'no category'}</div>
          </button>
        ))}
      </nav>
      <div className="min-w-0 space-y-8 overflow-y-auto p-6">
        {adding ? (
          <NewJudge
            onCreated={(key) => {
              setAdding(false)
              setSelected(key)
            }}
          />
        ) : current ? (
          <JudgeEditor key={current.key} judge={current} />
        ) : (
          <p className="text-sm text-muted-foreground">No judges yet.</p>
        )}
        <JudgeDatasets judges={list} />
      </div>
    </div>
  )
}

function NewJudge({ onCreated }: { onCreated: (key: string) => void }) {
  const queryClient = useQueryClient()
  const categories = useQuery({ queryKey: ['evaluation', 'categories'], queryFn: () => api.getCategories() })
  const [key, setKey] = useState('')
  const [model, setModel] = useState('gpt-4.1')
  const [category, setCategory] = useState('')
  const create = useMutation({
    mutationFn: () => api.addJudge({ key: key.trim(), model: model.trim(), failure_category: category || null }),
    onSuccess: (judge) => {
      void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
      onCreated(judge.key)
    },
  })
  return (
    <section className="max-w-xl space-y-3">
      <h3 className="text-base font-semibold">New judge</h3>
      <p className="text-xs text-muted-foreground">
        Written to judges/&lt;key&gt;/ with instructions and a template to edit, and a &lt;key&gt;_agent measure that reports
        its verdict (listed under the category it decides).
      </p>
      <div className="grid grid-cols-3 gap-3">
        <Input aria-label="Key" placeholder="politeness_judge" value={key} onChange={(e) => setKey(e.target.value)} />
        <Input aria-label="Model" value={model} onChange={(e) => setModel(e.target.value)} />
        <NativeSelect aria-label="Failure category" value={category} onChange={(e) => setCategory(e.target.value)}>
          <NativeSelectOption value="">No category</NativeSelectOption>
          {(categories.data?.categories ?? []).map((c) => (
            <NativeSelectOption key={c.id} value={c.name}>
              {c.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>
      <Button size="sm" disabled={!key.trim() || !model.trim() || create.isPending} onClick={() => create.mutate()}>
        Create
      </Button>
      {create.error ? <p className="text-sm text-destructive">{(create.error as Error).message}</p> : null}
    </section>
  )
}

function JudgeEditor({ judge }: { judge: Judge }) {
  const queryClient = useQueryClient()
  const categories = useQuery({ queryKey: ['evaluation', 'categories'], queryFn: () => api.getCategories() })
  const [draft, setDraft] = useState(judge)
  useEffect(() => setDraft(judge), [judge])
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation'] })
  const save = useMutation({
    mutationFn: () =>
      api.updateJudge(judge.key, {
        model: draft.model,
        label: draft.label,
        source: draft.source,
        failure_category: draft.failure_category ?? '',
        instructions: draft.instructions,
        template: draft.template,
      }),
    onSuccess: refresh,
  })
  const remove = useMutation({ mutationFn: () => api.removeJudge(judge.key), onSuccess: refresh })
  const changed = JSON.stringify(draft) !== JSON.stringify(judge)

  return (
    <section className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold">{judge.label}</h3>
          <p className="font-mono text-xs text-muted-foreground">{judge.defined_in}</p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => window.confirm(`Delete ${judge.label}?`) && remove.mutate()}
          >
            <Trash2 /> Delete
          </Button>
        </div>
      </div>
      {save.error || remove.error ? (
        <p className="text-sm text-destructive">{((save.error ?? remove.error) as Error).message}</p>
      ) : null}
      <div className="grid grid-cols-4 gap-3">
        <div className="grid gap-1.5">
          <Label>Label</Label>
          <Input value={draft.label} onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
        </div>
        <div className="grid gap-1.5">
          <Label>Model</Label>
          <Input value={draft.model ?? ''} onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
        </div>
        <div className="grid gap-1.5">
          <Label>Reads</Label>
          <NativeSelect value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })}>
            <NativeSelectOption value="final_assistant_text">The final reply</NativeSelectOption>
            <NativeSelectOption value="history">The whole conversation</NativeSelectOption>
          </NativeSelect>
        </div>
        <div className="grid gap-1.5">
          <Label>Decides</Label>
          <NativeSelect
            value={draft.failure_category ?? ''}
            onChange={(e) => setDraft({ ...draft, failure_category: e.target.value || null })}
          >
            <NativeSelectOption value="">No category</NativeSelectOption>
            {(categories.data?.categories ?? []).map((c) => (
              <NativeSelectOption key={c.id} value={c.name}>
                {c.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
      </div>
      <div className="grid gap-1.5">
        <Label>Instructions</Label>
        <Textarea
          rows={10}
          className="font-mono text-xs"
          value={draft.instructions}
          onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
        />
      </div>
      <div className="grid gap-1.5">
        <Label>Template: what the judge is shown ({'{{ text }}'} is the reply or conversation)</Label>
        <Textarea
          rows={6}
          className="font-mono text-xs"
          value={draft.template}
          onChange={(e) => setDraft({ ...draft, template: e.target.value })}
        />
      </div>
    </section>
  )
}

function JudgeDatasets({ judges }: { judges: Judge[] }) {
  const queryClient = useQueryClient()
  const datasets = useQuery({ queryKey: ['evaluation', 'judge-datasets'], queryFn: () => api.getJudgeDatasets() })
  const [browsing, setBrowsing] = useState<string | null>(null)
  const remove = useMutation({
    mutationFn: (dataset: JudgeDataset) => api.removeJudgeDataset(dataset.set, dataset.category),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['evaluation', 'judge-datasets'] }),
  })
  const list = datasets.data?.datasets ?? []
  return (
    <section className="space-y-3">
      <div>
        <h3 className="text-base font-semibold">Judge datasets</h3>
        <p className="text-xs text-muted-foreground">
          Reviewed generated scenarios split into train, dev and test for one category: positives are scenarios reviewers
          labelled with it. Build one from a generated set on the Scenarios tab.
        </p>
      </div>
      {list.length === 0 ? <p className="text-sm text-muted-foreground">No judge datasets yet.</p> : null}
      {list.map((dataset) => (
        <div key={`${dataset.set}/${dataset.category}`} className="space-y-2 rounded-md border border-border p-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">
              {dataset.set} · {dataset.category}
            </span>
            {dataset.splits.map((split) => (
              <button key={split.split} type="button" onClick={() => setBrowsing(browsing === split.path ? null : split.path)}>
                <Badge variant={browsing === split.path ? 'default' : 'outline'}>
                  {split.split}: {split.positives}+ / {split.negatives}−
                </Badge>
              </button>
            ))}
            <Button variant="ghost" size="icon-xs" className="ml-auto" aria-label="Delete dataset" onClick={() => remove.mutate(dataset)}>
              <Trash2 />
            </Button>
          </div>
          {dataset.splits.some((split) => split.path === browsing) ? <SplitBrowser path={browsing!} /> : null}
          <OptimizeJudge dataset={dataset} judges={judges} />
        </div>
      ))}
    </section>
  )
}

function SplitBrowser({ path }: { path: string }) {
  const { data, error } = useQuery({ queryKey: ['evaluation', 'judge-split', path], queryFn: () => api.getJudgeDatasetSplit(path) })
  if (error) return <p className="text-xs text-destructive">{(error as Error).message}</p>
  if (!data) return null
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Scenario</TableHead>
          <TableHead>Label</TableHead>
          <TableHead>Reply the judge reads</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.scenarios.map((scenario) => (
          <TableRow key={String(scenario.id ?? scenario.name)}>
            <TableCell className="align-top text-xs">{scenario.name}</TableCell>
            <TableCell className="align-top">
              <Badge variant={scenario.label ? 'destructive' : 'secondary'}>{scenario.label ? 'shows it' : 'does not'}</Badge>
            </TableCell>
            <TableCell className="max-w-xl whitespace-normal align-top text-xs text-muted-foreground">
              {scenario.reply === null ? <em>No saved run: run the set first</em> : excerpt(scenario.reply, 240)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function OptimizeJudge({ dataset, judges }: { dataset: JudgeDataset; judges: Judge[] }) {
  const job = useJob([['evaluation', 'judges']])
  const deciding = judges.filter((judge) => (judge.failure_category ?? '').toLowerCase().replace(/\s+/g, '_') === dataset.category)
  const [judge, setJudge] = useState(deciding[0]?.key ?? judges[0]?.key ?? '')
  const [budget, setBudget] = useState('60')
  if (judges.length === 0) {
    return <p className="text-xs text-muted-foreground">Create a judge to optimize it on this dataset.</p>
  }
  return (
    <div className="space-y-2">
      <div className="flex items-end gap-2">
        <div className="grid gap-1">
          <Label htmlFor={`optimize-${dataset.set}-${dataset.category}`} className="text-xs">
            Optimize judge
          </Label>
          <NativeSelect id={`optimize-${dataset.set}-${dataset.category}`} size="sm" value={judge} onChange={(e) => setJudge(e.target.value)}>
            {judges.map((j) => (
              <NativeSelectOption key={j.key} value={j.key}>
                {j.label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </div>
        <div className="grid w-24 gap-1">
          <Label htmlFor={`budget-${dataset.set}-${dataset.category}`} className="text-xs">
            Budget
          </Label>
          <Input
            id={`budget-${dataset.set}-${dataset.category}`}
            className="h-8"
            type="number"
            min={6}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={!judge || job.running}
          onClick={() =>
            void job.start({
              kind: 'optimize_judge',
              judge,
              set_path: dataset.set_path,
              category: dataset.category,
              max_metric_calls: Math.max(6, Number(budget) || 60),
            })
          }
        >
          Optimize on this dataset
        </Button>
      </div>
      <JobPanel job={job.job} error={job.error} onDismiss={job.dismiss} />
    </div>
  )
}
