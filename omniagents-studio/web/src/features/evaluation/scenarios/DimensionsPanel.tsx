import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { Textarea } from '@omniagents/ui/textarea'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { api } from '../../../lib/api'
import type { Dimension, Draft } from '../../../types/evaluation'
import { JobPanel } from '../jobs'
import { useJob } from '../useJob'

const blankDimension = (): Dimension => ({ name: '', label: null, description: null, values: [] })

/**
 * The dimensions synthetic scenarios are drawn from (each a set of values
 * with examples), and generating a scenario set from them.
 */
export function DimensionsPanel() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['evaluation', 'dimensions'], queryFn: () => api.getDimensions() })
  const [draft, setDraft] = useState<Dimension[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => setDraft(data?.dimensions ?? []), [data])

  const save = useMutation({
    mutationFn: () => api.setDimensions(draft),
    onSuccess: (saved) => {
      setError(null)
      queryClient.setQueryData(['evaluation', 'dimensions'], saved)
    },
    onError: (cause: Error) => setError(cause.message),
  })

  const update = (index: number, change: Partial<Dimension>) =>
    setDraft((current) => current.map((dim, i) => (i === index ? { ...dim, ...change } : dim)))

  const changed = JSON.stringify(draft) !== JSON.stringify(data?.dimensions ?? [])

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold">Dimensions</h3>
          <p className="text-sm text-muted-foreground">
            The ways your users differ. Generated scenarios combine one value of each, so the set covers the space
            instead of the cases you thought of. Saved in evaluation.yml.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setDraft((current) => [...current, blankDimension()])}>
            <Plus /> Dimension
          </Button>
          <Button size="sm" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {draft.length === 0 ? <p className="text-sm text-muted-foreground">No dimensions yet.</p> : null}
      {draft.map((dimension, index) => (
        <div key={index} className="space-y-3 rounded-md border border-border p-4">
          <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-3">
            <div className="grid gap-1.5">
              <Label>Name</Label>
              <Input value={dimension.name} onChange={(e) => update(index, { name: e.target.value })} />
            </div>
            <div className="grid gap-1.5">
              <Label>Description</Label>
              <Input value={dimension.description ?? ''} onChange={(e) => update(index, { description: e.target.value })} />
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Remove dimension"
              onClick={() => setDraft((current) => current.filter((_, i) => i !== index))}
            >
              <Trash2 />
            </Button>
          </div>
          <div className="space-y-2">
            {dimension.values.map((value, valueIndex) => (
              <div key={valueIndex} className="grid grid-cols-[12rem_1fr_auto] items-start gap-2">
                <Input
                  aria-label="Value"
                  value={value.name}
                  onChange={(e) =>
                    update(index, {
                      values: dimension.values.map((v, i) => (i === valueIndex ? { ...v, name: e.target.value } : v)),
                    })
                  }
                />
                <Textarea
                  aria-label="Examples"
                  rows={1}
                  placeholder="Examples, one per line"
                  value={value.examples.join('\n')}
                  onChange={(e) =>
                    update(index, {
                      values: dimension.values.map((v, i) =>
                        i === valueIndex ? { ...v, examples: e.target.value.split('\n') } : v,
                      ),
                    })
                  }
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Remove value"
                  onClick={() => update(index, { values: dimension.values.filter((_, i) => i !== valueIndex) })}
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              size="xs"
              onClick={() =>
                update(index, {
                  values: [...dimension.values, { name: '', label: null, description: null, examples: [] }],
                })
              }
            >
              <Plus /> Value
            </Button>
          </div>
        </div>
      ))}
      <GenerateScenarios dimensions={data?.dimensions ?? []} />
    </div>
  )
}

/**
 * A generated set, in two steps. First draft a sample of combinations of
 * dimension values; review it (remove the ones real users never present, add
 * the ones missing), since you know which occur. Then generate scenarios from
 * exactly those; queries a model rates unrealistic are left out.
 */
function GenerateScenarios({ dimensions }: { dimensions: Dimension[] }) {
  const queryClient = useQueryClient()
  const drafts = useQuery({ queryKey: ['evaluation', 'drafts'], queryFn: () => api.getDrafts() })
  const [name, setName] = useState('')
  const [count, setCount] = useState('20')
  const [seed, setSeed] = useState('')
  const [excluded, setExcluded] = useState<Record<string, string[]>>({})
  const draft = useMutation({
    mutationFn: () => {
      const valueFilter = Object.fromEntries(
        dimensions
          .filter((dim) => (excluded[dim.name] ?? []).length > 0)
          .map((dim) => [dim.name, dim.values.map((v) => v.name).filter((v) => !(excluded[dim.name] ?? []).includes(v))]),
      )
      return api.draftCombinations({
        name: name.trim(),
        count: Number(count) || 20,
        seed: seed ? Number(seed) : null,
        dimension_values: Object.keys(valueFilter).length ? valueFilter : null,
      })
    },
    onSuccess: () => {
      setName('')
      void queryClient.invalidateQueries({ queryKey: ['evaluation', 'drafts'] })
    },
  })

  if (dimensions.length === 0) {
    return null
  }

  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/30 p-4">
      <div>
        <h4 className="text-sm font-semibold">Generate a scenario set</h4>
        <p className="text-xs text-muted-foreground">
          Draft the combinations of values first and review them: remove the ones real users never present, add the
          ones missing. Scenarios are generated from exactly those into generated/&lt;name&gt;/, and queries a model rates
          unrealistic are set aside in filtered.yml.
        </p>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="draft-name">Set name</Label>
          <Input id="draft-name" placeholder="batch_1" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="draft-count">Combinations</Label>
          <Input id="draft-count" type="number" min={1} value={count} onChange={(e) => setCount(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="draft-seed">Seed (optional)</Label>
          <Input id="draft-seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
        </div>
      </div>
      <div className="space-y-1">
        <p className="text-xs font-medium">Values to draw from</p>
        {dimensions.map((dim) => (
          <div key={dim.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <span className="w-32 font-medium">{dim.label ?? dim.name}</span>
            {dim.values.map((value) => (
              <label key={value.name} className="flex items-center gap-1.5">
                <Checkbox
                  checked={!(excluded[dim.name] ?? []).includes(value.name)}
                  onCheckedChange={(checked) =>
                    setExcluded((current) => ({
                      ...current,
                      [dim.name]: checked
                        ? (current[dim.name] ?? []).filter((v) => v !== value.name)
                        : [...(current[dim.name] ?? []), value.name],
                    }))
                  }
                />
                {value.label ?? value.name}
              </label>
            ))}
          </div>
        ))}
      </div>
      <Button size="sm" disabled={!name.trim() || draft.isPending} onClick={() => draft.mutate()}>
        Draft combinations
      </Button>
      {draft.error ? <p className="text-xs text-destructive">{(draft.error as Error).message}</p> : null}
      {(drafts.data?.drafts ?? []).map((item) => (
        <DraftReview key={item.name} draft={item} dimensions={dimensions} />
      ))}
    </div>
  )
}

function DraftReview({ draft, dimensions }: { draft: Draft; dimensions: Dimension[] }) {
  const queryClient = useQueryClient()
  const job = useJob([['evaluation', 'scenario-sets'], ['evaluation', 'drafts']])
  const [combinations, setCombinations] = useState(draft.combinations)
  const [adding, setAdding] = useState<Record<string, string>>(() =>
    Object.fromEntries(dimensions.map((dim) => [dim.name, dim.values[0]?.name ?? ''])),
  )
  const [count, setCount] = useState(String(draft.combinations.length * 2))
  useEffect(() => setCombinations(draft.combinations), [draft.combinations])
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation', 'drafts'] })
  const save = useMutation({ mutationFn: () => api.setCombinations(draft.name, combinations), onSuccess: refresh })
  const remove = useMutation({ mutationFn: () => api.removeDraft(draft.name), onSuccess: refresh })
  const changed = JSON.stringify(combinations) !== JSON.stringify(draft.combinations)
  const key = (c: Record<string, string>) => dimensions.map((d) => c[d.name]).join(' · ')

  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3">
      <div className="flex items-center justify-between gap-2">
        <h5 className="text-sm font-medium">
          {draft.name} <span className="font-normal text-muted-foreground">({combinations.length} combinations to review)</span>
        </h5>
        <Button size="icon-xs" variant="ghost" aria-label="Delete draft" onClick={() => remove.mutate()}>
          <Trash2 />
        </Button>
      </div>
      <ul className="max-h-60 space-y-0.5 overflow-y-auto text-xs">
        {combinations.map((combination) => (
          <li key={key(combination)} className="flex items-center justify-between gap-2 rounded px-1 hover:bg-muted">
            <span>{key(combination)}</span>
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label="Remove combination"
              onClick={() => setCombinations(combinations.filter((c) => key(c) !== key(combination)))}
            >
              <Trash2 />
            </Button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {dimensions.map((dim) => (
          <select
            key={dim.name}
            aria-label={dim.label ?? dim.name}
            className="rounded-sm border border-border bg-background px-1 py-0.5"
            value={adding[dim.name] ?? ''}
            onChange={(e) => setAdding({ ...adding, [dim.name]: e.target.value })}
          >
            {dim.values.map((value) => (
              <option key={value.name} value={value.name}>
                {value.label ?? value.name}
              </option>
            ))}
          </select>
        ))}
        <Button
          size="xs"
          variant="outline"
          onClick={() => {
            if (!combinations.some((c) => key(c) === key(adding))) setCombinations([...combinations, { ...adding }])
          }}
        >
          <Plus /> Add combination
        </Button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Button size="sm" variant="outline" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
          Save review
        </Button>
        <div className="grid gap-1">
          <Label htmlFor={`generate-count-${draft.name}`} className="text-xs">
            Scenarios
          </Label>
          <Input
            id={`generate-count-${draft.name}`}
            className="h-8 w-24"
            type="number"
            min={1}
            value={count}
            onChange={(e) => setCount(e.target.value)}
          />
        </div>
        <Button
          size="sm"
          disabled={changed || combinations.length === 0 || job.running}
          title={changed ? 'Save the review first' : undefined}
          onClick={() => void job.start({ kind: 'generate', name: draft.name, count: Number(count) || null })}
        >
          Generate from these
        </Button>
      </div>
      {save.error ? <p className="text-xs text-destructive">{(save.error as Error).message}</p> : null}
      <JobPanel job={job.job} error={job.error} onDismiss={job.dismiss} />
    </div>
  )
}
