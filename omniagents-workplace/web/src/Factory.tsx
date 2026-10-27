// The factory, as its engineers watch it: what it is doing now, what its pull
// requests took (cost, human touches, time to ship) and where its spend goes.
// Costs come from the factory's own prices (pricing.yml); on a ChatGPT
// subscription they are API-equivalent: what the same calls cost on the API.
import { useCallback, useEffect, useState } from "react";

import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";

import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { toast } from "sonner";

import { FrontierChart, type Marker, type Series, TimeChart } from "./charts";

import {
  type BenchmarkResult,
  factoryBenchmarks,
  type FactoryPull,
  type FactoryReport,
  type FactoryRun,
  factoryReport,
  type Slice,
} from "./api";

const usd = (value: number | null | undefined) =>
  value == null
    ? "–"
    : value < 0.01 && value > 0
      ? "<$0.01"
      : `$${value.toFixed(2)}`;

const duration = (seconds: number | null | undefined) => {
  if (seconds == null) return "–";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
};

const tokens = (n: number) =>
  n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(1)}M`
    : n >= 1000
      ? `${Math.round(n / 1000)}k`
      : String(n);

const when = (epoch: number) => new Date(epoch * 1000).toLocaleString();

const API_EQUIVALENT = "API-equivalent cost";

function Figure({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border p-3" title={hint}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold">{value}</div>
    </div>
  );
}

function Status({ run }: { run: FactoryRun }) {
  const tone =
    run.status === "running"
      ? "bg-sky-500/15 text-sky-700 dark:text-sky-300"
      : run.status === "completed"
        ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
        : "bg-red-500/15 text-red-700 dark:text-red-300";
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${tone}`}
      title={run.error ?? undefined}
    >
      {run.status}
    </span>
  );
}

function Runs({
  runs,
  navigate,
  onBenchmark,
}: {
  runs: FactoryRun[];
  navigate: (p: string) => void;
  onBenchmark: (run: FactoryRun) => void;
}) {
  if (runs.length === 0)
    return <p className="text-sm text-muted-foreground">No runs yet.</p>;
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="p-2">Run</th>
            <th className="p-2">From</th>
            <th className="p-2">Status</th>
            <th className="p-2">Grades</th>
            <th className="p-2">Pull requests</th>
            <th className="p-2 text-right">Tokens</th>
            <th className="p-2 text-right" title={API_EQUIVALENT}>
              Cost
            </th>
            <th className="p-2" />
          </tr>
        </thead>
        <tbody className="divide-y">
          {[...runs].reverse().map((run) => {
            const input = run.usage.reduce((n, u) => n + u.input_tokens, 0);
            const cached = run.usage.reduce((n, u) => n + u.cached_tokens, 0);
            const output = run.usage.reduce((n, u) => n + u.output_tokens, 0);
            return (
              <tr key={run.id} className="align-top">
                <td className="p-2">
                  <button
                    type="button"
                    className="text-left font-medium text-sky-700 hover:underline dark:text-sky-300"
                    title="Open the run: its sessions, as the agents did it"
                    onClick={() => navigate(`/factory/runs/${run.id}`)}
                  >
                    {run.automation}
                  </button>
                  <div className="text-xs text-muted-foreground">
                    {run.agent} · {when(run.started_at)}
                    {run.finished_at
                      ? ` · ${duration(run.finished_at - run.started_at)}`
                      : ""}
                  </div>
                </td>
                <td className="p-2">
                  {run.source.link ? (
                    <a
                      className="text-sky-700 hover:underline dark:text-sky-300"
                      href={run.source.link}
                    >
                      {run.source.provider} {run.source.event}
                    </a>
                  ) : (
                    <span>
                      {run.source.provider} {run.source.event}
                    </span>
                  )}
                  <div className="text-xs text-muted-foreground">
                    {run.source.person ?? "an automation"}
                  </div>
                </td>
                <td className="p-2">
                  <Status run={run} />
                </td>
                <td className="p-2 text-xs">
                  {run.grades.length === 0
                    ? "–"
                    : run.grades.map((g) => (
                        <div key={`${g.judge}${g.agent}`}>
                          {g.judge}{" "}
                          <b>
                            {g.passed
                              ? "Pass"
                              : g.passed === false
                                ? "Fail"
                                : "?"}
                          </b>{" "}
                          <span className="text-muted-foreground">
                            ({g.agent})
                          </span>
                        </div>
                      ))}
                </td>
                <td className="p-2 text-xs">
                  {run.pull_requests.map((p) => (
                    <button
                      key={`${p.repository}#${p.number}`}
                      type="button"
                      className="block text-sky-700 hover:underline dark:text-sky-300"
                      onClick={() =>
                        navigate(`/${p.repository}/pull/${p.number}`)
                      }
                    >
                      {p.repository}#{p.number}{" "}
                      <span className="text-muted-foreground">
                        {p.relation}
                      </span>
                    </button>
                  ))}
                </td>
                <td
                  className="p-2 text-right text-xs"
                  title={run.usage
                    .map(
                      (u) =>
                        `${u.agent} on ${u.model}: ${u.requests} requests, ${u.input_tokens} in (${u.cached_tokens} cached), ${u.output_tokens} out (${u.reasoning_tokens} reasoning)`,
                    )
                    .join("\n")}
                >
                  {tokens(input)} in ({input ? Math.round((cached / input) * 100) : 0}%
                  cached) · {tokens(output)} out
                </td>
                <td className="p-2 text-right">{usd(run.cost)}</td>
                <td className="p-2">
                  {run.status === "completed" && (
                    <button
                      type="button"
                      className="text-xs text-sky-700 hover:underline dark:text-sky-300"
                      title="Make this run's task part of a benchmark suite (a pull request on the factory)"
                      onClick={() => onBenchmark(run)}
                    >
                      Benchmark
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Pulls({
  pulls,
  navigate,
}: {
  pulls: FactoryPull[];
  navigate: (p: string) => void;
}) {
  if (pulls.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        The factory opened no pull requests in this period.
      </p>
    );
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
          <tr>
            <th className="p-2">Pull request</th>
            <th className="p-2">Asked by</th>
            <th className="p-2">State</th>
            <th className="p-2 text-right">Human touches</th>
            <th className="p-2 text-right">Time to ship</th>
            <th className="p-2 text-right" title="Lines changed">
              Size
            </th>
            <th className="p-2 text-right">Runs</th>
            <th className="p-2 text-right" title={API_EQUIVALENT}>
              Cost
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {pulls.map((p) => (
            <tr key={`${p.repository}#${p.number}`}>
              <td className="p-2">
                <button
                  type="button"
                  className="text-left text-sky-700 hover:underline dark:text-sky-300"
                  onClick={() => navigate(`/${p.repository}/pull/${p.number}`)}
                >
                  {p.title}
                </button>
                <div className="text-xs text-muted-foreground">
                  {p.repository}#{p.number}
                </div>
              </td>
              <td className="p-2">{p.requested_by ?? "an automation"}</td>
              <td className="p-2">
                {p.state}
                {p.reverted && (
                  <span className="ml-1 text-xs text-muted-foreground">
                    (reverted)
                  </span>
                )}
              </td>
              <td className="p-2 text-right">{p.human_touches}</td>
              <td className="p-2 text-right">{duration(p.time_to_ship)}</td>
              <td className="p-2 text-right">{p.size ?? "–"}</td>
              <td className="p-2 text-right">{p.runs.length}</td>
              <td className="p-2 text-right">{usd(p.cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Slices({
  title,
  slices,
}: {
  title: string;
  slices: Record<string, Slice>;
}) {
  const entries = Object.entries(slices);
  const top = Math.max(0.0001, ...entries.map(([, s]) => s.cost));
  return (
    <div className="space-y-1.5 rounded-md border p-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      {entries.length === 0 && (
        <p className="text-xs text-muted-foreground">Nothing yet.</p>
      )}
      {entries.map(([name, s]) => (
        <div key={name} className="text-xs">
          <div className="flex justify-between gap-2">
            <span className="truncate">{name}</span>
            <span>
              {usd(s.cost)}
              {s.unpriced_requests > 0 && (
                <span
                  className="text-amber-600"
                  title={`${s.unpriced_requests} requests on models without a price in pricing.yml`}
                >
                  {" "}
                  +?
                </span>
              )}
            </span>
          </div>
          <div className="mt-0.5 h-1.5 rounded bg-muted">
            <div
              className="h-1.5 rounded bg-sky-500"
              style={{ width: `${(s.cost / top) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function OverTime({ report }: { report: FactoryReport }) {
  const periods = report.over_time ?? [];
  const markers: Marker[] = (report.promotions ?? []).map((p) => ({
    x: p.merged_at,
    label: `#${p.number}`,
    title: `Promoted ${p.commit}: ${p.title}`,
  }));
  const one = (name: string, pick: (p: (typeof periods)[number]) => number | null): Series[] => [
    { name, points: periods.map((p) => ({ x: p.start, y: pick(p) })) },
  ];
  const per = report.bucket === "week" ? "week" : "day";
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <TimeChart
        title="Cost per merged pull request"
        series={one("Cost", (p) => p.cost_per_merged_pr)}
        markers={markers}
        format={(v) => usd(v)}
      />
      <TimeChart
        title="Human touches per merged pull request"
        series={one("Touches", (p) => p.human_touches_per_merged_pr)}
        markers={markers}
        format={(v) => v.toFixed(1)}
      />
      <TimeChart
        title="Median time to ship"
        series={one("Time to ship", (p) => p.median_time_to_ship)}
        markers={markers}
        format={(v) => duration(v)}
      />
      <TimeChart
        title={`Merged pull requests a ${per}`}
        series={[
          {
            name: "Merged",
            points: periods.map((p) => ({
              x: p.start,
              y: p.merged,
              note: p.reverted
                ? `${p.reverted} reverted`
                : p.median_size != null
                  ? `median ${p.median_size} lines`
                  : undefined,
            })),
          },
        ]}
        markers={markers}
        format={(v) => String(Math.round(v))}
      />
      <TimeChart
        title={`Spend a ${per}`}
        series={one("Spend", (p) => p.spend)}
        markers={markers}
        format={(v) => usd(v)}
      />
    </div>
  );
}

function Quality({ report }: { report: FactoryReport }) {
  const periods = report.over_time ?? [];
  const judges = [
    ...new Set(periods.flatMap((p) => Object.keys(p.pass_rates))),
  ].sort();
  const markers: Marker[] = (report.promotions ?? []).map((p) => ({
    x: p.merged_at,
    label: `#${p.number}`,
    title: `Promoted ${p.commit}: ${p.title}`,
  }));
  const series: Series[] = judges.map((judge) => ({
    name: judge,
    points: periods.map((p) => {
      const r = p.pass_rates[judge];
      return r
        ? {
            x: p.start,
            y: r.rate,
            low: r.interval?.[0],
            high: r.interval?.[1],
            note: `${r.passed} of ${r.graded} passed`,
          }
        : { x: p.start, y: null };
    }),
  }));
  const pct = (v: number) => `${Math.round(v * 100)}%`;
  return (
    <div className="space-y-3">
      <TimeChart
        title="Judges' pass rate on live runs"
        series={series}
        markers={markers}
        format={pct}
        yMax={1}
      />
      {(report.by_version ?? []).length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="p-2">Factory version</th>
                <th className="p-2">From</th>
                <th className="p-2 text-right">Runs</th>
                {judges.map((j) => (
                  <th key={j} className="p-2 text-right">
                    {j}
                  </th>
                ))}
                <th className="p-2 text-right" title={API_EQUIVALENT}>
                  Spend
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {report.by_version!.map((v) => (
                <tr key={v.version}>
                  <td className="p-2 font-mono text-xs">{v.version.slice(0, 7)}</td>
                  <td className="p-2 text-xs">{when(v.first_run)}</td>
                  <td className="p-2 text-right">{v.runs}</td>
                  {judges.map((j) => {
                    const r = v.pass_rates[j];
                    return (
                      <td
                        key={j}
                        className="p-2 text-right"
                        title={
                          r?.interval
                            ? `95% interval ${pct(r.interval[0])}–${pct(r.interval[1])}`
                            : undefined
                        }
                      >
                        {r ? `${pct(r.rate)} (${r.passed}/${r.graded})` : "–"}
                      </td>
                    );
                  })}
                  <td className="p-2 text-right">{usd(v.spend)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Benchmarks({
  navigate,
  adding,
  onAdded,
}: {
  navigate: (p: string) => void;
  adding: FactoryRun | null;
  onAdded: () => void;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof factoryBenchmarks.get>> | null>(null);
  const [suite, setSuite] = useState("tasks");
  const [bar, setBar] = useState(0.8);
  const load = useCallback(() => factoryBenchmarks.get().then(setData, () => {}), []);
  useEffect(() => {
    void load();
  }, [load]);
  const latest = new Map<string, BenchmarkResult>();
  for (const r of data?.results ?? []) if (!latest.has(r.suite)) latest.set(r.suite, r);
  const add = () => {
    if (!adding) return;
    factoryBenchmarks.add(adding.id, suite.trim()).then(
      (pr) => {
        toast.success(`Pull request #${pr.number} adds ${pr.scenario} to ${suite}`);
        onAdded();
        navigate(new URL(pr.url).pathname);
      },
      (e: unknown) => toast.error((e as Error).message),
    );
  };
  const run = (name: string) => {
    if (!data?.repository) return;
    factoryBenchmarks.run(data.repository, name).then(
      () => {
        toast.success(`The ${name} benchmark is running`);
        navigate(`/${data.repository}/actions`);
      },
      (e: unknown) => toast.error((e as Error).message),
    );
  };
  return (
    <div className="space-y-3">
      {adding && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm">
          <span>
            Add run {adding.id}&apos;s task ({adding.automation}) to the suite
          </span>
          <Input
            value={suite}
            onChange={(e) => setSuite(e.target.value)}
            className="h-8 w-48"
            placeholder="api-features"
            aria-label="Suite"
            list="benchmark-suites"
          />
          <datalist id="benchmark-suites">
            {(data?.suites ?? []).map((s) => (
              <option key={s.name} value={s.name} />
            ))}
          </datalist>
          <Button size="sm" onClick={add} disabled={!suite.trim()}>
            Open the pull request
          </Button>
          <Button size="sm" variant="ghost" onClick={onAdded}>
            Cancel
          </Button>
        </div>
      )}
      {(data?.suites ?? []).length === 0 && !adding && (
        <p className="text-sm text-muted-foreground">
          No benchmark suites yet. A run&apos;s Benchmark action makes its task
          one: a pull request on the factory adding it to a suite.
        </p>
      )}
      {(data?.suites ?? []).map((s) => {
        const result = latest.get(s.name);
        const points = (result?.configs ?? [])
          .filter((c) => c.cost_per_task != null)
          .map((c) => ({
            name: c.name,
            cost: c.cost_per_task!,
            correctness: c.correctness,
            runs: c.runs,
          }));
        const pick = [...(result?.configs ?? [])]
          .filter((c) => c.cost_per_task != null && c.correctness >= bar)
          .sort((a, b) => a.cost_per_task! - b.cost_per_task!)[0];
        return (
          <div key={s.name} className="space-y-2 rounded-md border p-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-semibold">{s.name}</h3>
              <span className="text-xs text-muted-foreground">
                {s.tasks} tasks
                {result ? ` · last run ${when(result.at)}, ${result.runs_per_task} per task` : " · not run yet"}
              </span>
              <Button size="sm" variant="outline" className="ml-auto" onClick={() => run(s.name)}>
                Run
              </Button>
            </div>
            {result && points.length > 0 && (
              <>
                <FrontierChart title={`${s.name}: cost a task against correctness`} points={points} bar={bar} />
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <label htmlFor={`bar-${s.name}`}>Correct at least</label>
                  <input
                    id={`bar-${s.name}`}
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={bar}
                    onChange={(e) => setBar(Number(e.target.value))}
                  />
                  <span>{Math.round(bar * 100)}%:</span>
                  <b>
                    {pick
                      ? `${pick.name} (${pick.model}${pick.reasoning ? `, ${pick.reasoning}` : ""}), ${usd(pick.cost_per_task)} a task`
                      : "no configuration clears it"}
                  </b>
                </div>
              </>
            )}
            {result && (
              <div className="overflow-x-auto rounded-md border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="p-2">Configuration</th>
                      <th className="p-2 text-right">Correct</th>
                      <th className="p-2 text-right" title={API_EQUIVALENT}>
                        Cost a task
                      </th>
                      <th className="p-2 text-right">Tokens</th>
                      <th className="p-2 text-right">Time a task</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {result.configs.map((c) => (
                      <tr key={c.name}>
                        <td className="p-2">
                          {c.name}{" "}
                          <span className="text-xs text-muted-foreground">
                            {c.model}
                            {c.reasoning ? `, ${c.reasoning}` : ""}
                            {result.frontier.includes(c.name) ? " · frontier" : ""}
                          </span>
                        </td>
                        <td className="p-2 text-right">
                          {Math.round(c.correctness * 100)}% ({c.passed}/{c.runs})
                        </td>
                        <td className="p-2 text-right">{usd(c.cost_per_task)}</td>
                        <td className="p-2 text-right text-xs">
                          {tokens(c.input_tokens)} in ({c.input_tokens ? Math.round((c.cached_tokens / c.input_tokens) * 100) : 0}% cached) · {tokens(c.output_tokens)} out
                        </td>
                        <td className="p-2 text-right">{duration(c.seconds_per_task)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function FactoryApp({ navigate }: { navigate: (p: string) => void }) {
  const [days, setDays] = useState(7);
  const [report, setReport] = useState<FactoryReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [benchmarking, setBenchmarking] = useState<FactoryRun | null>(null);
  const load = useCallback(
    () =>
      factoryReport.get(days).then(
        (r) => {
          setReport(r);
          setError(r.factory_error);
        },
        (e: unknown) => setError((e as Error).message),
      ),
    [days],
  );
  useEffect(() => {
    void load();
    // Runs move on their own: keep the view current.
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, [load]);

  const s = report?.summary;
  return (
    <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">The factory</h1>
        <NativeSelect
          value={String(days)}
          onChange={(e) => setDays(Number(e.target.value))}
          className="w-40"
          aria-label="Period"
        >
          <NativeSelectOption value="1">Last day</NativeSelectOption>
          <NativeSelectOption value="7">Last 7 days</NativeSelectOption>
          <NativeSelectOption value="30">Last 30 days</NativeSelectOption>
        </NativeSelect>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {report && !report.priced && !error && (
        <p className="text-sm text-amber-700 dark:text-amber-300">
          The factory has no prices (paths.pricing in its project.yml): costs
          are unknown.
        </p>
      )}
      {s && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <Figure
            label="Spend"
            value={usd(s.spend) + (s.unpriced_runs ? " +?" : "")}
            hint={
              s.unpriced_runs
                ? `${API_EQUIVALENT}; ${s.unpriced_runs} runs used models without a price`
                : API_EQUIVALENT
            }
          />
          <Figure
            label="Cost per merged PR"
            value={usd(s.cost_per_merged_pr)}
            hint={API_EQUIVALENT}
          />
          <Figure
            label="Human touches per merged PR"
            value={
              s.human_touches_per_merged_pr == null
                ? "–"
                : s.human_touches_per_merged_pr.toFixed(1)
            }
            hint="The request, if a person made it; people's messages in its Slack thread after it; people's comments, reviews and review comments on the pull request; and the merge, if a person merged it."
          />
          <Figure
            label="Median time to ship"
            value={duration(s.median_time_to_ship)}
            hint="From the request to the merge"
          />
          <Figure
            label="Pull requests"
            value={`${s.merged} merged of ${s.pull_requests}`}
          />
          <Figure
            label="Runs"
            value={`${s.runs}${s.running ? ` (${s.running} running)` : ""}`}
          />
        </div>
      )}
      {report?.over_time && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Over time</h2>
          <OverTime report={report} />
        </section>
      )}
      {report?.over_time && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Quality</h2>
          <Quality report={report} />
        </section>
      )}
      {report?.pull_requests && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Its pull requests</h2>
          <Pulls pulls={report.pull_requests} navigate={navigate} />
        </section>
      )}
      {report?.by && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Where the spend goes</h2>
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
            <Slices title="By agent" slices={report.by.agent} />
            <Slices title="By model" slices={report.by.model} />
            <Slices title="By source" slices={report.by.source} />
            <Slices title="By who asked" slices={report.by.person} />
          </div>
        </section>
      )}
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Benchmarks</h2>
        <Benchmarks
          navigate={navigate}
          adding={benchmarking}
          onAdded={() => setBenchmarking(null)}
        />
      </section>
      {report?.runs && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Runs</h2>
          <Runs runs={report.runs} navigate={navigate} onBenchmark={setBenchmarking} />
        </section>
      )}
    </div>
  );
}
