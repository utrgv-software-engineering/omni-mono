import { Badge } from '@omniagents/ui/badge'
import { Button } from '@omniagents/ui/button'
import { Progress } from '@omniagents/ui/progress'
import { Loader2, X } from 'lucide-react'

import type { Job } from '../../types/evaluation'

const LABELS: Record<Job['kind'], string> = {
  run: 'Run',
  generate: 'Scenario generation',
  optimize_judge: 'Judge optimization',
  optimize_agent: 'Agent optimization',
  propose_modes: 'Failure modes from notes',
  suggest_labels: 'Finding a failure mode',
}

/** A job's progress, its last log lines and how it ended. */
export function JobPanel({ job, error, onDismiss }: { job: Job | null; error: string | null; onDismiss: () => void }) {
  if (!job && !error) {
    return null
  }
  const total = job?.total ?? null
  const value = total ? Math.min(100, Math.round(((job?.completed ?? 0) / total) * 100)) : null
  const active = job?.state === 'queued' || job?.state === 'running'
  return (
    <div className="space-y-2 rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex items-center gap-2">
        {active ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : null}
        <span className="font-medium">{job ? LABELS[job.kind] : 'Job'}</span>
        {job ? (
          <Badge variant={job.state === 'failed' ? 'destructive' : job.state === 'completed' ? 'secondary' : 'outline'}>
            {job.state}
          </Badge>
        ) : null}
        {total ? (
          <span className="text-xs text-muted-foreground">
            {job?.completed ?? 0} / {total}
          </span>
        ) : null}
        <Button variant="ghost" size="icon-xs" className="ml-auto" onClick={onDismiss} aria-label="Dismiss">
          <X />
        </Button>
      </div>
      {active && value !== null ? <Progress value={value} /> : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {job?.error ? <p className="text-xs text-destructive">{job.error}</p> : null}
      {job && job.log.length > 0 ? (
        <pre className="max-h-32 overflow-y-auto rounded bg-muted px-2 py-1 font-mono text-[11px] text-muted-foreground">
          {job.log.slice(-12).join('\n')}
        </pre>
      ) : null}
    </div>
  )
}
