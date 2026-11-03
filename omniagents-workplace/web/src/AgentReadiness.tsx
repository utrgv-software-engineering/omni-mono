import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@omniagents/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@omniagents/ui/table";
import { factoryFetch } from "./factory-scope";

type PassRate = { passed: number; graded: number; rate: number; interval: [number, number] | null };
type Week = {
  start: number;
  messages: number;
  lessons: number;
  personal_lessons: number;
  runs: number;
  failed: number;
  pass_rates: Record<string, PassRate>;
};
type Readiness = { agent: string; weeks: Week[]; factory_error: string | null };

const pct = (value: number) => `${Math.round(value * 100)}%`;
const day = (seconds: number) =>
  new Date(seconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

/** An agent's weeks: what people taught it, and how it does on its own. */
export function AgentReadiness({ agent, name, onClose }: { agent: string; name: string; onClose: () => void }) {
  const [data, setData] = useState<Readiness>();
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    void factoryFetch(`/workplace/api/personal/agents/${encodeURIComponent(agent)}/readiness`)
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Unable to load readiness");
        if (!stopped) setData(body);
      })
      .catch((e) => {
        if (!stopped) setError(e instanceof Error ? e.message : "Unable to load readiness");
      });
    return () => {
      stopped = true;
    };
  }, [agent]);
  const judges = [...new Set(data?.weeks.flatMap((week) => Object.keys(week.pass_rates)) ?? [])].sort();
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{name}: readiness</DialogTitle>
          <DialogDescription>
            Lessons that level off while people keep working with {name}, and passing grades on
            its own runs, suggest it can take on work unattended. Personal lessons are one
            person's preferences.
          </DialogDescription>
        </DialogHeader>
        {error && <p role="alert" className="text-sm">{error}</p>}
        {data?.factory_error && (
          <p role="status" className="text-sm text-muted-foreground">
            Runs and grades are unavailable: {data.factory_error}
          </p>
        )}
        {!data && !error ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : data ? (
          <div className="max-h-[60vh] overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Week of</TableHead>
                  <TableHead className="text-right">Messages from people</TableHead>
                  <TableHead className="text-right">Lessons (personal)</TableHead>
                  <TableHead className="text-right">Runs (failed)</TableHead>
                  {judges.map((judge) => (
                    <TableHead key={judge} className="text-right">{judge.replace(/_/g, " ")}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...data.weeks].reverse().map((week) => (
                  <TableRow key={week.start}>
                    <TableCell>{day(week.start)}</TableCell>
                    <TableCell className="text-right">{week.messages}</TableCell>
                    <TableCell className="text-right">
                      {week.lessons} ({week.personal_lessons})
                    </TableCell>
                    <TableCell className="text-right">
                      {week.runs} ({week.failed})
                    </TableCell>
                    {judges.map((judge) => {
                      const rate = week.pass_rates[judge];
                      return (
                        <TableCell
                          key={judge}
                          className="text-right"
                          title={rate?.interval ? `95% interval ${pct(rate.interval[0])}–${pct(rate.interval[1])}` : undefined}
                        >
                          {rate ? `${pct(rate.rate)} (${rate.passed}/${rate.graded})` : "–"}
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
