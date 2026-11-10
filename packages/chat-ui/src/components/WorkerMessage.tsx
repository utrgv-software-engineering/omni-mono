import type { WorkerMessage as WorkerReport } from '@omniagents/client/transcript/worker-messages';
import { Button } from '@omniagents/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@omniagents/ui/collapsible';
import { BotIcon, ChevronRightIcon } from 'lucide-react';

export function WorkerMessage({ worker, onOpenTranscript }: {
  worker: WorkerReport; onOpenTranscript?: () => void;
}) {
  const label = worker.task || worker.name;
  return <Collapsible className="min-w-0 max-w-full">
    <CollapsibleTrigger asChild>
      <Button variant="ghost" size="sm" className="max-w-full justify-start" title={label}>
        <BotIcon className="size-4 shrink-0" />
        <span className="truncate">{label}</span>
        <span className="shrink-0 text-muted-foreground">View message</span>
        <ChevronRightIcon className="size-3 shrink-0" />
      </Button>
    </CollapsibleTrigger>
    <CollapsibleContent className="min-w-0 px-3 py-2">
      {worker.task && <p className="mb-2 text-xs text-muted-foreground">{worker.name}</p>}
      <div className="whitespace-pre-wrap break-words text-sm [overflow-wrap:anywhere]">{worker.message}</div>
      {onOpenTranscript && <Button variant="link" size="sm" onClick={onOpenTranscript}>View transcript</Button>}
    </CollapsibleContent>
  </Collapsible>;
}
