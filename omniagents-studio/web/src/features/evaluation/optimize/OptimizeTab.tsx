import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { NativeSelect, NativeSelectOption } from '@omniagents/ui/native-select'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '../../../lib/api'
import { JobPanel } from '../jobs'
import { useJob } from '../useJob'

/**
 * Improving an agent's instructions against a judge dataset: candidate
 * instructions are scored on train and dev and the best is saved into the
 * agent's files (keeping the rest of them), with its test score reported.
 */
export function OptimizeTab({ agent }: { agent: string | null }) {
  const agents = useQuery({ queryKey: ['agents', 'list'], queryFn: () => api.getAgents() })
  const datasets = useQuery({ queryKey: ['evaluation', 'judge-datasets'], queryFn: () => api.getJudgeDatasets() })
  const job = useJob([['agents'], ['evaluation', 'runs']])
  const complete = (datasets.data?.datasets ?? []).filter((dataset) => dataset.splits.length === 3)
  const [target, setTarget] = useState(agent ?? '')
  const [datasetKey, setDatasetKey] = useState('')
  const [budget, setBudget] = useState('100')
  const [seed, setSeed] = useState('0')
  const [positivesOnly, setPositivesOnly] = useState(false)
  const chosen = complete.find((dataset) => `${dataset.set}/${dataset.category}` === datasetKey) ?? complete[0]
  const agentKey = target || agents.data?.agents.find((a) => a.is_entry)?.key || ''

  return (
    <div className="max-w-3xl space-y-4 p-6">
      <div>
        <h3 className="text-base font-semibold">Optimize an agent</h3>
        <p className="text-sm text-muted-foreground">
          Rewrites the agent&apos;s instructions to fail a category less, scoring each candidate on the dataset&apos;s train
          and dev scenarios. The best is saved into the agent&apos;s files; its test score tells you whether it generalizes.
          Each candidate runs the agent, so the budget is the number of runs it may spend.
        </p>
      </div>
      {complete.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          This needs a judge dataset with train, dev and test: build one from a reviewed generated set on the Scenarios tab.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="optimize-agent">Agent</Label>
            <NativeSelect id="optimize-agent" value={agentKey} onChange={(e) => setTarget(e.target.value)}>
              {(agents.data?.agents ?? []).map((a) => (
                <NativeSelectOption key={a.key} value={a.key}>
                  {a.label}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="optimize-dataset">Judge dataset</Label>
            <NativeSelect
              id="optimize-dataset"
              value={chosen ? `${chosen.set}/${chosen.category}` : ''}
              onChange={(e) => setDatasetKey(e.target.value)}
            >
              {complete.map((dataset) => (
                <NativeSelectOption key={`${dataset.set}/${dataset.category}`} value={`${dataset.set}/${dataset.category}`}>
                  {dataset.set} · {dataset.category}
                </NativeSelectOption>
              ))}
            </NativeSelect>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="optimize-budget">Budget (runs)</Label>
            <Input id="optimize-budget" type="number" min={6} value={budget} onChange={(e) => setBudget(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="optimize-seed">Seed</Label>
            <Input id="optimize-seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </div>
          <label className="col-span-2 flex items-center gap-2 text-sm">
            <Checkbox checked={positivesOnly} onCheckedChange={(checked) => setPositivesOnly(Boolean(checked))} />
            Train only on scenarios that show the failure
          </label>
          <div className="col-span-2">
            <Button
              disabled={!agentKey || !chosen || job.running}
              onClick={() =>
                chosen &&
                void job.start({
                  kind: 'optimize_agent',
                  agent: agentKey,
                  set_path: chosen.set_path,
                  category: chosen.category,
                  positives_only: positivesOnly,
                  max_metric_calls: Math.max(6, Number(budget) || 100),
                  seed: Number(seed) || 0,
                })
              }
            >
              Optimize
            </Button>
          </div>
        </div>
      )}
      <JobPanel job={job.job} error={job.error} onDismiss={job.dismiss} />
      {job.job?.state === 'completed' && job.job.result ? (
        <div className="rounded-md border border-border p-3 text-sm">
          <p>
            Dev score {String(job.job.result.dev_score ?? '—')} · test score {String(job.job.result.test_score ?? '—')}
          </p>
          <p className="text-xs text-muted-foreground">
            {job.job.result.applied
              ? 'The new instructions are saved in the agent.'
              : `Not saved: ${String(job.job.result.apply_error ?? 'nothing to apply')}`}
          </p>
        </div>
      ) : null}
    </div>
  )
}
