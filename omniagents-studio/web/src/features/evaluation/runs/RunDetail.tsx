import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@omniagents/ui/table'
import { useQuery } from '@tanstack/react-query'
import { MessageSquareText } from 'lucide-react'
import { Fragment } from 'react'

import { api } from '../../../lib/api'
import type { JsonValue } from '../../../types'
import type { Metric, RunComparison, RunManifest } from '../../../types/evaluation'
import { excerpt, percent, rateWithInterval, when } from '../format'

const MANIFEST_LABELS: Array<[keyof RunManifest, string]> = [
  ['agent', 'Agent'],
  ['model', 'Model'],
  ['instructions_sha', 'Instructions'],
  ['agent_yml_sha', 'agent.yml'],
  ['tools', 'Tools'],
  ['capabilities', 'Capabilities'],
  ['max_turns', 'Turn limit'],
  ['select', 'Selected'],
  ['exclude', 'Excluded'],
  ['split', 'Holdout split'],
  ['repeats', 'Runs per scenario'],
  ['scenarios', 'Scenarios'],
  ['omniagents_version', 'omniagents'],
]

function show(value: JsonValue | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—'
  if (typeof value === 'object') return JSON.stringify(value)
  const text = String(value)
  return text.length === 64 || text.length === 40 ? text.slice(0, 10) : text
}

function Manifest({ manifest }: { manifest: RunManifest | null | undefined }) {
  if (!manifest) {
    return <p className="text-xs text-muted-foreground">Saved without a manifest: it cannot be compared.</p>
  }
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-xs">
      {MANIFEST_LABELS.map(([key, label]) => (
        <Fragment key={key}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-mono">{show(manifest[key])}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

function Metrics({ metrics }: { metrics: Metric[] }) {
  if (metrics.length === 0) return null
  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold">Metrics</h4>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Metric</TableHead>
            <TableHead>Value</TableHead>
            <TableHead>Target</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {metrics.map((metric) => (
            <Fragment key={metric.name}>
              <TableRow>
                <TableCell>{metric.label ?? metric.name}</TableCell>
                <TableCell>
                  {typeof metric.value === 'number' && metric.type !== 'count'
                    ? rateWithInterval(metric.value, metric.interval ?? null)
                    : String(metric.value ?? '—')}
                  {metric.runs ? <span className="ml-2 text-xs text-muted-foreground">{metric.runs} runs</span> : null}
                </TableCell>
                <TableCell>
                  {metric.target ? (
                    <Badge variant={metric.meets_target ? 'secondary' : 'destructive'}>
                      {metric.target.op} {metric.target.value}
                    </Badge>
                  ) : (
                    '—'
                  )}
                </TableCell>
              </TableRow>
              {Object.entries(metric.slices ?? {}).map(([field, values]) => (
                <TableRow key={`${metric.name}-${field}`}>
                  <TableCell className="pl-8 text-xs text-muted-foreground">by {field}</TableCell>
                  <TableCell colSpan={2} className="text-xs">
                    {Object.entries(values)
                      .map(([value, rate]) => `${value}: ${rateWithInterval(rate.value, rate.interval)} (${rate.runs})`)
                      .join(' · ')}
                  </TableCell>
                </TableRow>
              ))}
            </Fragment>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

/** One saved run: how it did, what it ran with, and each scenario's runs. */
export function RunDetail({
  path,
  onOpenConversation,
}: {
  path: string
  onOpenConversation: (agent: string | null, threadId: string) => void
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['evaluation', 'run', path],
    queryFn: () => api.getRun(path),
  })
  if (isLoading) return <p className="text-sm text-muted-foreground">Loading the run…</p>
  if (error || !data) return <p className="text-sm text-destructive">{(error as Error)?.message ?? 'Run not found'}</p>

  const { overview } = data
  const agent = (data.metadata.agent as string | null) ?? data.manifest?.agent ?? null
  return (
    <div className="space-y-6">
      <section className="grid grid-cols-4 gap-4">
        <Stat label="Passed" value={rateWithInterval(overview.pass_rate, overview.interval)} hint={`${overview.passed} of ${overview.total_scenarios} runs`} />
        <Stat
          label="Answered"
          value={percent(overview.total_scenarios ? overview.answered / overview.total_scenarios : null)}
          hint={`${overview.non_responses} gave no final answer`}
        />
        <Stat label="Runs per scenario" value={String(overview.repeats)} hint={overview.repeats > 1 ? 'Each scenario repeated' : 'One run each'} />
        <Stat label="Saved" value={when(String(data.metadata.completed_at ?? ''))} hint={String(data.metadata.source ?? '')} />
      </section>
      {data.warnings?.length ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">{data.warnings.join('\n')}</div>
      ) : null}
      <Metrics metrics={data.metrics ?? []} />
      <section className="space-y-2">
        <h4 className="text-sm font-semibold">What it ran with</h4>
        <Manifest manifest={data.manifest} />
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-semibold">Scenarios</h4>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Scenario</TableHead>
              <TableHead>Run</TableHead>
              <TableHead>Result</TableHead>
              <TableHead>Measures</TableHead>
              <TableHead>Reply</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.runs.map((run, index) => (
              <TableRow key={index}>
                <TableCell className="align-top font-medium">{run.scenario_config?.name ?? run.scenario}</TableCell>
                <TableCell className="align-top text-xs text-muted-foreground">
                  {run.repeats && run.repeats > 1 ? `${(run.trial ?? 0) + 1} of ${run.repeats}` : '—'}
                </TableCell>
                <TableCell className="align-top">
                  {run.error ? (
                    <Badge variant="destructive" title={run.error.message}>
                      error
                    </Badge>
                  ) : run.answered === false ? (
                    <Badge variant="destructive">no answer</Badge>
                  ) : (
                    <Badge variant={run.passed ? 'secondary' : 'destructive'}>{run.passed ? 'passed' : 'failed'}</Badge>
                  )}
                </TableCell>
                <TableCell className="align-top text-xs">
                  {run.measures.map((measure) => (
                    <div key={measure.name} className={measure.passed === false ? 'text-destructive' : undefined}>
                      <span className="font-mono">{measure.name}</span>
                      {measure.reason ? <span className="text-muted-foreground"> · {excerpt(String(measure.reason), 80)}</span> : null}
                    </div>
                  ))}
                </TableCell>
                <TableCell className="max-w-sm whitespace-normal align-top text-xs text-muted-foreground">
                  {excerpt(run.final_assistant_text ?? '', 160)}
                </TableCell>
                <TableCell className="align-top">
                  {run.session_id ? (
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label="Review the conversation"
                      title="Review the conversation in Logs"
                      onClick={() => onOpenConversation(agent, String(run.session_id))}
                    >
                      <MessageSquareText />
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  )
}

/** How run b differs from run a. */
export function RunComparisonView({ a, b }: { a: string; b: string }) {
  const { data, isLoading, error } = useQuery<RunComparison>({
    queryKey: ['evaluation', 'compare', a, b],
    queryFn: () => api.compareRuns(a, b),
  })
  if (isLoading) return <p className="text-sm text-muted-foreground">Comparing…</p>
  if (error || !data) return <p className="text-sm text-destructive">{(error as Error)?.message}</p>
  const { pass_rate: rate } = data
  return (
    <div className="space-y-6">
      <section className="grid grid-cols-3 gap-4">
        <Stat label="Earlier run" value={rateWithInterval(rate.a, rate.a_interval)} />
        <Stat label="Later run" value={rateWithInterval(rate.b, rate.b_interval)} />
        <Stat
          label="Change"
          value={rate.change === null ? 'Not comparable' : `${rate.change >= 0 ? '+' : ''}${Math.round(rate.change * 100)} points`}
          hint={
            data.reason ??
            'Intervals that overlap mean the runs may not differ: run more scenarios or repeats before trusting a change.'
          }
        />
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-semibold">What changed</h4>
        {Object.keys(data.differences).length === 0 ? (
          <p className="text-xs text-muted-foreground">Nothing in the manifests differs.</p>
        ) : (
          <dl className="grid grid-cols-[max-content_1fr_1fr] gap-x-6 gap-y-1 text-xs">
            {Object.entries(data.differences).map(([field, [before, after]]) => (
              <Fragment key={field}>
                <dt className="text-muted-foreground">{field}</dt>
                <dd className="font-mono">{show(before)}</dd>
                <dd className="font-mono">{show(after)}</dd>
              </Fragment>
            ))}
          </dl>
        )}
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-semibold">Scenarios whose outcome changed</h4>
        {data.scenarios.length === 0 ? (
          <p className="text-xs text-muted-foreground">Every scenario did the same.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario</TableHead>
                <TableHead>Earlier</TableHead>
                <TableHead>Later</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.scenarios.map((scenario) => (
                <TableRow key={scenario.scenario}>
                  <TableCell>{scenario.name ?? scenario.scenario}</TableCell>
                  <TableCell>{scenario.a === null ? 'not run' : percent(scenario.a)}</TableCell>
                  <TableCell>{scenario.b === null ? 'not run' : percent(scenario.b)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
    </div>
  )
}
