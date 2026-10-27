import { useEffect, useRef, useState } from "react";

import { Badge } from "@omniagents/ui/badge";
import { Button } from "@omniagents/ui/button";
import { ArrowDownLeft, ArrowUpRight, Trash2, X } from "lucide-react";

import type { WireEntry } from "./api";

const KIND: Record<WireEntry["kind"], string> = {
  mcp: "MCP",
  api: "Web API",
  event: "Event",
  socket: "Socket",
  git: "Git",
};

/** Every exchange between the workplace and the outside, as it happens. */
export function WirePanel({
  entries,
  onClear,
  onClose,
}: {
  entries: WireEntry[];
  onClear: () => void;
  onClose: () => void;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const last = entries.at(-1)?.id;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [last]);

  return (
    <section className="flex h-[38vh] min-h-40 shrink-0 flex-col border-t bg-muted/20" aria-label="Event log">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3 text-sm">
        <span className="font-semibold">Event log</span>
        <span className="hidden text-xs text-muted-foreground sm:inline">
          <ArrowUpRight className="inline size-3" /> the workplace calls the
          factory (events) · <ArrowDownLeft className="inline size-3" /> the
          factory calls the workplace (MCP, Web API)
        </span>
        <div className="ml-auto flex gap-1">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onClear}
            title="Clear event log"
          >
            <Trash2 />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={onClose}
            title="Hide event log"
          >
            <X />
          </Button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
        {entries.length === 0 && (
          <div className="p-4 font-sans text-sm text-muted-foreground">
            Nothing yet. Connect a factory through Account → Connections and mention
            it in a channel.
          </div>
        )}
        {entries.map((e) => (
          <div key={e.id} className="border-b border-border/50">
            <button
              type="button"
              onClick={() => setOpen(open === e.id ? null : e.id)}
              className="flex w-full items-center gap-2 px-3 py-1 text-left hover:bg-accent"
            >
              <span className="w-16 shrink-0 text-muted-foreground">
                {new Date(e.at * 1000).toLocaleTimeString([], {
                  hour12: false,
                })}
              </span>
              {e.direction === "out" ? (
                <ArrowUpRight className="size-3.5 shrink-0 text-amber-600" />
              ) : (
                <ArrowDownLeft className="size-3.5 shrink-0 text-sky-600" />
              )}
              <Badge
                variant="outline"
                className="w-16 shrink-0 justify-center font-sans"
              >
                {KIND[e.kind]}
              </Badge>
              <span className="truncate font-semibold">{e.title}</span>
              {e.actor && (
                <span className="truncate font-sans text-muted-foreground">
                  {e.direction === "out" ? `→ ${e.actor}` : e.actor}
                </span>
              )}
              <span className="ml-auto flex shrink-0 items-center gap-2">
                {e.duration_ms !== null && (
                  <span className="text-muted-foreground">
                    {e.duration_ms} ms
                  </span>
                )}
                <span
                  className={`size-2 rounded-full ${e.status === "ok" ? "bg-emerald-500" : e.status === "error" ? "bg-red-500" : "animate-pulse bg-amber-500"}`}
                  title={e.status}
                />
              </span>
            </button>
            {open === e.id && (
              <div className="grid gap-2 px-3 pb-2 md:grid-cols-2">
                <Json
                  label={e.kind === "event" ? "Sent" : "Request"}
                  value={e.request}
                />
                <Json
                  label={e.kind === "event" ? "Acknowledgement" : "Response"}
                  value={e.response ?? e.detail}
                />
              </div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
    </section>
  );
}

function Json({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 font-sans text-muted-foreground">{label}</div>
      <pre className="max-h-64 overflow-auto rounded border bg-background p-2 whitespace-pre-wrap">
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
