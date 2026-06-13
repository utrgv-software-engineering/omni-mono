import React from "react";

export type LoopTaskSnapshot = {
  id: string;
  prompt: string;
  mode: "dynamic" | "fixed_interval" | "fixed_cron";
  cron: string | null;
  interval_seconds: number | null;
  recurring: boolean;
  created_at: number;
  expires_at: number | null;
  next_fire_at: number | null;
  fires: number;
  status: "active" | "cancelled" | "expired" | "completed";
  last_fire_at: number | null;
  last_reason: string | null;
  jitter_seconds: number;
};

const PROMPT_TRUNCATE = 80;

function shortPrompt(prompt: string): string {
  const oneLine = prompt.replace(/\s+/g, " ").trim();
  if (oneLine.length <= PROMPT_TRUNCATE) return oneLine;
  return oneLine.slice(0, PROMPT_TRUNCATE - 1) + "…";
}

function formatInterval(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes - hours * 60;
  return rem === 0 ? `${hours}h` : `${hours}h${rem}m`;
}

function formatTimeRemaining(unixTimestamp: number): string {
  const remaining = Math.max(0, unixTimestamp - Date.now() / 1000);
  return formatInterval(remaining);
}

export function LoopPanel({ tasks }: { tasks: LoopTaskSnapshot[] }) {
  const active = tasks.filter((task) => task.status === "active");
  if (active.length === 0) return null;

  const next = [...active].sort(
    (a, b) =>
      (a.next_fire_at ?? Number.MAX_SAFE_INTEGER) -
      (b.next_fire_at ?? Number.MAX_SAFE_INTEGER),
  )[0];
  const cadence =
    next.mode === "dynamic"
      ? "dynamic"
      : next.interval_seconds
        ? `every ${formatInterval(next.interval_seconds)}`
        : `cron ${next.cron}`;

  const tail = [cadence, `id ${next.id}`];
  if (next.next_fire_at)
    tail.unshift(`in ${formatTimeRemaining(next.next_fire_at)}`);
  if (active.length > 1) tail.push(`${active.length} loops`);

  return (
    <div className="px-3 py-1 mx-3 mb-1 text-xs text-muted-foreground flex items-center gap-2 border-t border-border/40">
      <span
        className="inline-block w-2 h-2 rounded-full shrink-0 bg-tweetBlue animate-pulse"
        aria-hidden
      />
      <span className="opacity-70 shrink-0">loop</span>
      <span className="opacity-40 shrink-0">·</span>
      <span className="truncate flex-1 min-w-0" title={next.prompt}>
        {shortPrompt(next.prompt)}
      </span>
      {tail.map((part, idx) => (
        <React.Fragment key={idx}>
          <span className="opacity-40 shrink-0">·</span>
          <span className="shrink-0">{part}</span>
        </React.Fragment>
      ))}
    </div>
  );
}
