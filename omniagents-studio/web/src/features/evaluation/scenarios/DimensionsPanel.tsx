import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { Textarea } from '@omniagents/ui/textarea'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { api } from '../../../lib/api'
import type { Dimension } from '../../../types/evaluation'
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

/** Generate a scenario set from the saved dimensions, optionally only some values. */
function GenerateScenarios({ dimensions }: { dimensions: Dimension[] }) {
  const job = useJob([['evaluation', 'scenario-sets']])
  const [name, setName] = useState('')
  const [count, setCount] = useState('20')
  const [seed, setSeed] = useState('')
  const [excluded, setExcluded] = useState<Record<string, string[]>>({})

  if (dimensions.length === 0) {
    return null
  }
  const valueFilter = Object.fromEntries(
    dimensions
      .filter((dim) => (excluded[dim.name] ?? []).length > 0)
      .map((dim) => [dim.name, dim.values.map((v) => v.name).filter((v) => !(excluded[dim.name] ?? []).includes(v))]),
  )

  return (
    <div className="space-y-3 rounded-md border border-border bg-muted/30 p-4">
      <div>
        <h4 className="text-sm font-semibold">Generate a scenario set</h4>
        <p className="text-xs text-muted-foreground">
          Writes artifacts/eval/&lt;name&gt;/. Run it, review the conversations in Logs, then promote the good scenarios or
          build a judge dataset from the reviews.
        </p>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="generate-name">Set name</Label>
          <Input id="generate-name" placeholder="batch_1" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="generate-count">Scenarios</Label>
          <Input id="generate-count" type="number" min={1} value={count} onChange={(e) => setCount(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="generate-seed">Seed (optional)</Label>
          <Input id="generate-seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
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
      <Button
        size="sm"
        disabled={!name.trim() || job.running}
        onClick={() =>
          void job.start({
            kind: 'generate',
            name: name.trim(),
            count: Number(count) || null,
            seed: seed ? Number(seed) : null,
            dimension_values: Object.keys(valueFilter).length ? valueFilter : null,
          })
        }
      >
        Generate
      </Button>
      <JobPanel job={job.job} error={job.error} onDismiss={job.dismiss} />
    </div>
  )
}
