// The factory's schedules (its automations' cron triggers), with when each
// fires next, and Run now: a nightly review or an audit on demand.
import { useCallback, useEffect, useState } from "react";

import { Button } from "@omniagents/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@omniagents/ui/dialog";
import { toast } from "sonner";

import { type FactorySchedule, factorySchedules } from "./api";

export function FactorySchedulesDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [schedules, setSchedules] = useState<FactorySchedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const load = useCallback(
    () =>
      factorySchedules.get().then(
        (r) => {
          setSchedules(r.schedules);
          setError(r.factory_error);
        },
        (e: unknown) => setError((e as Error).message),
      ),
    [],
  );
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const run = (name: string) => {
    setRunning(name);
    factorySchedules
      .run(name)
      .then(
        () => toast.success(`${name} started`),
        (e: unknown) => toast.error((e as Error).message),
      )
      .finally(() => setRunning(null));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>The factory's schedules</DialogTitle>
          <DialogDescription>
            The automations that run on a schedule. Run now starts one as its
            time would.
          </DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {schedules && schedules.length === 0 && !error && (
          <p className="text-sm text-muted-foreground">
            None of the factory's automations has a schedule.
          </p>
        )}
        <div className="divide-y rounded-md border text-sm">
          {schedules?.map((s) => (
            <div key={s.name} className="flex items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">{s.name}</div>
                <div className="text-xs text-muted-foreground">
                  <code>{s.cron}</code>
                  {s.timezone ? ` (${s.timezone})` : ""} · next{" "}
                  {new Date(s.next).toLocaleString()}
                  {s.review_runs
                    ? ` · reviews the last ${Math.round(s.review_runs / 3600)}h of runs`
                    : ""}
                </div>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={running !== null}
                onClick={() => run(s.name)}
              >
                {running === s.name ? "Starting…" : "Run now"}
              </Button>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
