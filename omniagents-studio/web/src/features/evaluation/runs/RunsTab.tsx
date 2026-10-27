import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Checkbox } from '@omniagents/ui/checkbox'
import { Input } from '@omniagents/ui/input'
import { Label } from '@omniagents/ui/label'
import { NativeSelect, NativeSelectOption } from '@omniagents/ui/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@omniagents/ui/table'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Play } from 'lucide-react'
import { useState } from 'react'

import { api } from '../../../lib/api'
import type { SavedRunEntry } from '../../../types/evaluation'
import { percent, rateWithInterval, when } from '../format'
import { JobPanel } from '../jobs'
import { useJob } from '../useJob'
import { RunComparisonView, RunDetail } from './RunDetail'

/**
 * Running the project's scenarios and reading what runs found: each saved
 * run with its pass rate and interval, a run's detail, and two runs
 * compared.
 */
export function RunsTab({
  agent,
  onOpenConversation,
}: {
  agent: string | null
  onOpenConversation: (agent: string | null, threadId: string) => void
}) {
  const runs = useQuery({ queryKey: ['evaluation', 'runs'], queryFn: () => api.getRuns() })
  const [open, setOpen] = useState<string | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [comparing, setComparing] = useState(false)

  if (open || (comparing && picked.length === 2)) {
    // Compared oldest first, so the change reads forward in time.
    const ordered = [...picked].sort(
      (x, y) =>
        (runs.data?.runs.findIndex((r) => r.path === y) ?? 0) - (runs.data?.runs.findIndex((r) => r.path === x) ?? 0),
    )
    return (
      <div className="space-y-4 p-6">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setOpen(null)
            setComparing(false)
          }}
        >
          <ArrowLeft /> All runs
        </Button>
        {open ? (
          <>
            <Baseline
              entry={runs.data?.runs.find((r) => r.path === open) ?? null}
              onRemoved={() => setOpen(null)}
            />
            <RunDetail path={open} onOpenConversation={onOpenConversation} />
          </>
        ) : (
          <RunComparisonView a={ordered[0]!} b={ordered[1]!} />
        )}
      </div>
    )
  }

  const entries = runs.data?.runs ?? []
  return (
    <div className="space-y-6 p-6">
      <div className="grid grid-cols-[2fr_1fr] gap-6">
        <RunSuite agent={agent} />
        <HoldoutCard />
      </div>
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold">Saved runs</h3>
          <Button size="sm" variant="outline" disabled={picked.length !== 2} onClick={() => setComparing(true)}>
            Compare the two selected
          </Button>
        </div>
        {runs.error ? <p className="text-sm text-destructive">{(runs.error as Error).message}</p> : null}
        {entries.length === 0 && !runs.isLoading ? (
          <p className="text-sm text-muted-foreground">No runs saved yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>Saved</TableHead>
                <TableHead>Scenarios</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Passed (95% interval)</TableHead>
                <TableHead>Answered</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((entry) => (
                <RunRow
                  key={entry.path}
                  entry={entry}
                  picked={picked.includes(entry.path)}
                  onPick={(on) =>
                    setPicked((current) =>
                      on ? [...current.filter((p) => p !== entry.path), entry.path].slice(-2) : current.filter((p) => p !== entry.path),
                    )
                  }
                  onOpen={() => setOpen(entry.path)}
                />
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  )
}

function RunRow({
  entry,
  picked,
  onPick,
  onOpen,
}: {
  entry: SavedRunEntry
  picked: boolean
  onPick: (on: boolean) => void
  onOpen: () => void
}) {
  const summary = entry.summary
  const total = summary.total_scenarios ?? 0
  const passRate = summary.pass_rate ?? (total ? (summary.passed ?? 0) / total : null)
  return (
    <TableRow className="cursor-pointer" onClick={onOpen}>
      <TableCell onClick={(event) => event.stopPropagation()}>
        <Checkbox checked={picked} onCheckedChange={(checked) => onPick(Boolean(checked))} aria-label="Select to compare" />
      </TableCell>
      <TableCell className="text-xs">{when(entry.timestamp)}</TableCell>
      <TableCell className="text-xs">
        {entry.baseline ? (
          <Badge variant="secondary" className="mr-1" title="Kept in baselines/ (committed)">
            {entry.baseline}
          </Badge>
        ) : null}
        {entry.label}
        <span className="ml-1 text-muted-foreground">({total})</span>
        {entry.manifest?.split ? <span className="ml-1 text-muted-foreground">· {entry.manifest.split}</span> : null}
      </TableCell>
      <TableCell className="text-xs">{entry.agent ?? '—'}</TableCell>
      <TableCell className="font-mono text-xs">{entry.manifest?.model ?? '—'}</TableCell>
      <TableCell className="text-xs">{rateWithInterval(passRate, summary.interval ?? null)}</TableCell>
      <TableCell className="text-xs">{total && summary.answered !== undefined ? percent(summary.answered / total) : '—'}</TableCell>
    </TableRow>
  )
}

/** Run the project's scenarios (the suite), all or a selection of them. */
function RunSuite({ agent }: { agent: string | null }) {
  const job = useJob([['evaluation', 'runs']])
  const holdout = useQuery({ queryKey: ['evaluation', 'holdout'], queryFn: () => api.getHoldout() })
  const [name, setName] = useState('')
  const [select, setSelect] = useState('')
  const [exclude, setExclude] = useState('')
  const [split, setSplit] = useState('')
  const [unmeasured, setUnmeasured] = useState(false)
  const [maxTurns, setMaxTurns] = useState('')
  return (
    <section className="space-y-3 rounded-md border border-border p-4">
      <div>
        <h3 className="text-base font-semibold">Run the suite</h3>
        <p className="text-xs text-muted-foreground">
          The project&apos;s scenarios against {agent ?? 'the entrypoint agent'}, saved with what it ran with so runs can be
          compared.
        </p>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="run-name">Label (optional)</Label>
          <Input id="run-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="run-select">Only</Label>
          <Input id="run-select" placeholder="tag:smoke, name:refund*" value={select} onChange={(e) => setSelect(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="run-exclude">Except</Label>
          <Input id="run-exclude" value={exclude} onChange={(e) => setExclude(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="run-split">Holdout split</Label>
          <NativeSelect id="run-split" value={split} disabled={!holdout.data?.holdout} onChange={(e) => setSplit(e.target.value)}>
            <NativeSelectOption value="">All scenarios</NativeSelectOption>
            <NativeSelectOption value="train">train</NativeSelectOption>
            <NativeSelectOption value="dev">dev</NativeSelectOption>
            <NativeSelectOption value="test">test</NativeSelectOption>
          </NativeSelect>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="run-turns">Turn limit (optional)</Label>
          <Input id="run-turns" type="number" min={1} value={maxTurns} onChange={(e) => setMaxTurns(e.target.value)} />
        </div>
        <label className="flex items-end gap-2 pb-2 text-sm">
          <Checkbox checked={unmeasured} onCheckedChange={(checked) => setUnmeasured(Boolean(checked))} />
          Include unmeasured scenarios
        </label>
      </div>
      <Button
        size="sm"
        disabled={job.running}
        onClick={() =>
          void job.start({
            kind: 'run',
            agent,
            name: name.trim() || null,
            select: select.trim() || null,
            exclude: exclude.trim() || null,
            split: split || null,
            include_unmeasured: unmeasured,
            max_turns: maxTurns ? Number(maxTurns) : null,
          })
        }
      >
        <Play /> Run
      </Button>
      <JobPanel job={job.job} error={job.error} onDismiss={job.dismiss} />
    </section>
  )
}

/** The train/dev/test holdout of the project's scenarios. */
function HoldoutCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['evaluation', 'holdout'], queryFn: () => api.getHoldout() })
  const [strata, setStrata] = useState('')
  const [fractions, setFractions] = useState('0.5, 0.25, 0.25')
  const [seed, setSeed] = useState('42')
  const create = useMutation({
    mutationFn: () => {
      const parts = fractions.split(',').map((part) => Number(part.trim()))
      return api.createHoldout({
        strata: strata.split(',').map((s) => s.trim()).filter(Boolean),
        fractions: [parts[0] ?? 0.5, parts[1] ?? 0.25, parts[2] ?? 0.25],
        seed: Number(seed) || 42,
      })
    },
    onSuccess: (result) => queryClient.setQueryData(['evaluation', 'holdout'], result),
  })
  const holdout = data?.holdout
  return (
    <section className="space-y-3 rounded-md border border-border p-4">
      <div>
        <h3 className="text-base font-semibold">Holdout</h3>
        <p className="text-xs text-muted-foreground">
          Tune the agent on train and dev; score it once on test, which it was not tuned on.
        </p>
      </div>
      {holdout ? (
        <p className="text-sm">
          train {holdout.counts.train} · dev {holdout.counts.dev} · test {holdout.counts.test}
          <span className="block text-xs text-muted-foreground">
            seed {holdout.seed}
            {holdout.strata.length ? ` · stratified by ${holdout.strata.join(', ')}` : ''} · {when(holdout.updated_at)}
          </span>
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">No holdout yet.</p>
      )}
      <div className="grid gap-2">
        <Input aria-label="Strata" placeholder="Stratify by: expect.tone, tags" value={strata} onChange={(e) => setStrata(e.target.value)} />
        <div className="grid grid-cols-2 gap-2">
          <Input aria-label="Fractions" value={fractions} onChange={(e) => setFractions(e.target.value)} />
          <Input aria-label="Seed" value={seed} onChange={(e) => setSeed(e.target.value)} />
        </div>
      </div>
      <Button size="sm" variant="outline" disabled={create.isPending} onClick={() => create.mutate()}>
        {holdout ? 'Make a new holdout' : 'Make a holdout'}
      </Button>
      {create.error ? <p className="text-xs text-destructive">{(create.error as Error).message}</p> : null}
    </section>
  )
}

/**
 * Keeping a run as a baseline copies it, with the agent it tested, into
 * baselines/ (committed): the evidence for a number someone reports. Runs
 * themselves stay on this machine.
 */
function Baseline({ entry, onRemoved }: { entry: SavedRunEntry | null; onRemoved: () => void }) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['evaluation', 'runs'] })
  const keep = useMutation({ mutationFn: () => api.keepRun(entry!.path, name.trim()), onSuccess: refresh })
  const remove = useMutation({
    mutationFn: () => api.removeBaseline(entry!.baseline!),
    onSuccess: () => {
      refresh()
      onRemoved()
    },
  })
  if (!entry) return null
  if (entry.baseline) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Badge variant="secondary">Baseline {entry.baseline}</Badge>
        <span className="text-xs text-muted-foreground">Kept in baselines/, committed with the project.</span>
        <Button
          size="sm"
          variant="ghost"
          disabled={remove.isPending}
          onClick={() => window.confirm(`Delete the baseline ${entry.baseline}?`) && remove.mutate()}
        >
          Delete baseline
        </Button>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-end gap-2">
      <div className="grid gap-1">
        <Label htmlFor="baseline-name" className="text-xs">
          Keep as baseline
        </Label>
        <Input
          id="baseline-name"
          className="h-8 w-56"
          placeholder="e.g. before-prompt-rewrite"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <Button size="sm" variant="outline" disabled={!name.trim() || keep.isPending} onClick={() => keep.mutate()}>
        Keep
      </Button>
      {keep.error ? <span className="text-xs text-destructive">{(keep.error as Error).message}</span> : null}
      {keep.isSuccess ? <span className="text-xs text-muted-foreground">Kept in baselines/.</span> : null}
    </div>
  )
}
