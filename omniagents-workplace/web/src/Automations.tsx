import { useCallback, useEffect, useState } from "react";
import { Archive, ArrowLeft, Clock, FolderOpen, GitBranch, Hash, Pencil, Play, Sparkles, Zap, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { AgentAvatar } from "@omniagents/chat-ui/components/omni-avatar/AgentAvatar";
import { Badge } from "@omniagents/ui/badge";
import { Button } from "@omniagents/ui/button";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Switch } from "@omniagents/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@omniagents/ui/tabs";
import { factoryFetch } from "./factory-scope";
import { describeTrigger, scheduleName, type Trigger } from "./automation-triggers";
import { FileEditor } from "./Agents";

type Grade = { judge: string; agent?: string; passed: boolean | null; reason?: string };
type Run = {
  id: number;
  agent: string;
  session_id: string;
  status: string;
  error?: string | null;
  started_at: number;
  finished_at?: number | null;
  thread_key?: string | null;
  grades?: Grade[];
  chat?: boolean;
};
type Skipped = { id: number; provider: string; event: string; thread_key?: string | null; received_at: number; status: string; detail?: string | null };
export type Automation = {
  name: string;
  description: string;
  instruction: string;
  triggers: Trigger[];
  self_improvement: boolean;
  enabled: boolean;
  agent: string;
  conversation: "auto" | "thread" | "new";
  runs: Run[];
  skipped: Skipped[];
};
type View = {
  automations: Automation[];
  chat_agent?: string | null;
  chat_agents?: string[];
  agent_details?: { id: string; name: string }[];
};

const CONVERSATIONS = {
  auto: "Follow the work item; new chat for scheduled tasks",
  new: "New conversation for each event",
  thread: "Continue the source thread",
} as const;

const SOURCES: Record<string, { icon: LucideIcon; label: string }> = {
  github: { icon: GitBranch, label: "GitHub" },
  slack: { icon: Hash, label: "Slack" },
  linear: { icon: Sparkles, label: "Linear" },
  schedule: { icon: Clock, label: "Schedule" },
  shared: { icon: FolderOpen, label: "Shared" },
  memory: { icon: Archive, label: "Memory" },
};

type Health = "off" | "failing" | "never" | "running" | "ok" | "quiet";
const HEALTH: Record<Health, { label: string; dot: string }> = {
  off: { label: "Off", dot: "border border-muted-foreground" },
  failing: { label: "Its last run failed", dot: "bg-destructive" },
  never: { label: "Never runs: its filters leave out every event", dot: "bg-amber-500" },
  running: { label: "Running", dot: "bg-sky-500 animate-pulse" },
  ok: { label: "Working", dot: "bg-emerald-500" },
  quiet: { label: "Nothing to do yet", dot: "bg-muted-foreground/40" },
};

export const humanize = (name: string) => name.replace(/[_-]+/g, " ").replace(/^\w/, c => c.toUpperCase());
const when = (seconds?: number | null) => (seconds ? new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "–");

/** How an automation is doing, in one of six words a dot can carry. */
export function health(automation: Automation): Health {
  if (!automation.enabled) return "off";
  const last = automation.runs[0];
  if (last?.status === "running") return "running";
  if (last && ["failed", "interrupted"].includes(last.status)) return "failing";
  if (!automation.runs.length && automation.skipped.length) return "never";
  return automation.runs.length ? "ok" : "quiet";
}

/** "When a pull request is opened in api → Code Reviewer reviews the pull request". */
export function sentence(automation: Automation, agentName: string) {
  const [first, ...more] = automation.triggers;
  const does = automation.description || humanize(automation.name).toLowerCase();
  const start = !first
    ? "When started by hand"
    : first.provider === "schedule"
      ? describeTrigger(first)
      : `When ${describeTrigger(first).replace(/^\w/, c => c.toLowerCase())}`;
  return `${start}${more.length ? ` (or ${more.length} more)` : ""} → ${agentName} ${does}`;
}

function SourceIcon({ trigger }: { trigger?: Trigger }) {
  const source = SOURCES[trigger?.provider ?? ""] ?? { icon: Zap, label: trigger?.provider ?? "Automation" };
  const Icon = source.icon;
  return <Icon className="size-4 shrink-0 text-muted-foreground" role="img" aria-label={source.label} />;
}

function HealthDot({ value }: { value: Health }) {
  return <span className={`size-2.5 shrink-0 rounded-full ${HEALTH[value].dot}`} role="img" aria-label={HEALTH[value].label} title={HEALTH[value].label} />;
}

/** Every automation, as a sentence each: what starts it and which agent does what. */
export function AutomationsApp({ name, navigate }: { name?: string; navigate: (path: string) => void }) {
  const [view, setView] = useState<View>();
  const [agents, setAgents] = useState<string[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const [data, routes] = await Promise.all([
        factoryFetch("/workplace/api/factory/automations").then(async r => {
          const body = await r.json();
          if (!r.ok) throw new Error(body.detail || "Unable to load automations");
          return body as View;
        }),
        factoryFetch("/workplace/api/factory/automation-routes").then(r => (r.ok ? r.json() : { agents: [] })),
      ]);
      setView(data);
      setAgents(routes.agents ?? []);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load automations");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (automation: Automation, enabled: boolean) => {
    setView(v => v && { ...v, automations: v.automations.map(a => (a.name === automation.name ? { ...a, enabled } : a)) });
    try {
      const response = await factoryFetch(`/workplace/api/factory/automations/${encodeURIComponent(automation.name)}/enabled`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Unable to change the automation");
    } catch (e) {
      setView(v => v && { ...v, automations: v.automations.map(a => (a.name === automation.name ? { ...a, enabled: !enabled } : a)) });
      toast.error(e instanceof Error ? e.message : "Unable to change the automation");
    }
  };

  if (error) return <div role="alert" className="p-6 text-sm">{error} <Button variant="ghost" onClick={() => void load()}>Retry</Button></div>;
  if (!view) return <p className="p-6 text-sm text-muted-foreground">Loading automations…</p>;
  const agentName = (id: string) => view.agent_details?.find(a => a.id === id)?.name || humanize(id);
  const selected = name ? view.automations.find(a => a.name === name) : undefined;
  if (name && !selected) return <p className="p-6 text-sm">No automation named “{name}”.</p>;
  if (selected)
    return <AutomationDetail automation={selected} agents={agents} agentName={agentName} navigate={navigate} reload={load} onToggle={enabled => void toggle(selected, enabled)} />;

  const order = [...new Set([view.chat_agent, ...(view.chat_agents ?? [])].filter((a): a is string => !!a))];
  const rank = (agent: string) => (order.includes(agent) ? order.indexOf(agent) : order.length);
  const attention = view.automations.filter(a => ["failing", "never"].includes(health(a)));
  const byAgent = [...new Set(view.automations.map(a => a.agent))]
    .sort((a, b) => rank(a) - rank(b) || agentName(a).localeCompare(agentName(b)))
    .map(agent => ({ agent, items: view.automations.filter(a => a.agent === agent && !attention.includes(a)) }))
    .filter(group => group.items.length);

  const row = (automation: Automation) => (
    <li key={automation.name} className="flex items-center gap-3 px-4 py-3">
      <SourceIcon trigger={automation.triggers[0]} />
      <button
        type="button"
        aria-label={humanize(automation.name)}
        onClick={() => navigate(`/automations/${encodeURIComponent(automation.name)}`)}
        className={`min-w-0 flex-1 text-left text-sm hover:underline ${automation.enabled ? "" : "text-muted-foreground"}`}
      >
        {sentence(automation, agentName(automation.agent))}
      </button>
      <HealthDot value={health(automation)} />
      <Switch
        aria-label={`${automation.enabled ? "Turn off" : "Turn on"} ${humanize(automation.name)}`}
        checked={automation.enabled}
        onCheckedChange={enabled => void toggle(automation, enabled)}
      />
    </li>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-6">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <div>
          <h1 className="text-xl font-semibold">Automations</h1>
          <p className="mt-1 text-sm text-muted-foreground">Work the agents take on without being asked.</p>
        </div>
        {view.automations.length === 0 && <p className="text-sm text-muted-foreground">This factory has no automations.</p>}
        {attention.length > 0 && (
          <section aria-label="Needs attention">
            <h2 className="mb-2 text-sm font-semibold">Needs attention</h2>
            <ul className="divide-y rounded-lg border border-amber-500/40">{attention.map(row)}</ul>
          </section>
        )}
        {byAgent.map(group => (
          <section key={group.agent} aria-label={agentName(group.agent)}>
            <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <AgentAvatar seed={group.agent} size={20} />
              {agentName(group.agent)}
            </h2>
            <ul className="divide-y rounded-lg border">{group.items.map(row)}</ul>
          </section>
        ))}
      </div>
    </div>
  );
}

function AutomationDetail({
  automation,
  agents,
  agentName,
  navigate,
  reload,
  onToggle,
}: {
  automation: Automation;
  agents: string[];
  agentName: (id: string) => string;
  navigate: (path: string) => void;
  reload: () => Promise<void>;
  onToggle: (enabled: boolean) => void;
}) {
  const [agent, setAgent] = useState(automation.agent);
  const [conversation, setConversation] = useState(automation.conversation);
  const [busy, setBusy] = useState(false);
  const [fullInstruction, setFullInstruction] = useState(false);
  const [ignored, setIgnored] = useState(false);
  const [editing, setEditing] = useState(false);
  const schedule = scheduleName(automation.triggers);
  const changed = agent !== automation.agent || conversation !== automation.conversation;
  const [lead, ...rest] = automation.instruction.split(/\n\s*\n/);
  async function act(action: () => Promise<Response>, done: string) {
    setBusy(true);
    try {
      const response = await action();
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Something went wrong");
      toast.success(done);
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }
  const state = health(automation);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-6">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        <div>
          <Button variant="ghost" size="sm" onClick={() => navigate("/automations")}><ArrowLeft />Automations</Button>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <SourceIcon trigger={automation.triggers[0]} />
          <h1 className="text-xl font-semibold">{humanize(automation.name)}</h1>
          <span className="flex items-center gap-2 text-sm text-muted-foreground"><HealthDot value={state} />{HEALTH[state].label}</span>
          <span className="ml-auto flex items-center gap-2">
            {schedule && (
              <Button
                variant="outline"
                size="sm"
                disabled={busy || !automation.enabled}
                onClick={() => void act(() => factoryFetch(`/workplace/api/factory/schedules/${encodeURIComponent(schedule)}/run`, { method: "POST" }), "Started")}
              >
                <Play />Run now
              </Button>
            )}
            <Button variant="outline" size="sm" aria-pressed={editing} onClick={() => setEditing(value => !value)}>
              <Pencil />{editing ? "Done" : "Edit"}
            </Button>
            <Switch
              aria-label={`${automation.enabled ? "Turn off" : "Turn on"} ${humanize(automation.name)}`}
              checked={automation.enabled}
              onCheckedChange={onToggle}
            />
          </span>
        </div>
        <p className="text-sm">{sentence(automation, agentName(automation.agent))}</p>
        {editing && (
          <section aria-label="Edit the automation" className="flex flex-col gap-2">
            <p className="text-sm text-muted-foreground">
              Its description and triggers are at the top, between the <code>---</code> lines; what it's told follows.
            </p>
            <FileEditor path={`automations/${automation.name}/automation.md`} label={`${humanize(automation.name)} automation`} onSaved={() => void reload()} />
          </section>
        )}

        <Tabs defaultValue="overview">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="history">History · {automation.runs.length}</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="flex flex-col gap-6 pt-4">
            <section aria-label="When">
              <h2 className="text-sm font-semibold">When</h2>
              <ul className="mt-2 flex flex-col gap-1 text-sm">
                {automation.triggers.map((trigger, index) => (
                  <li key={index} className="flex items-center gap-2"><SourceIcon trigger={trigger} />{describeTrigger(trigger)}</li>
                ))}
              </ul>
            </section>
            <section aria-label="Who">
              <h2 className="text-sm font-semibold">Who</h2>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <NativeSelect aria-label="Agent" value={agent} onChange={e => setAgent(e.target.value)}>
                  {[...new Set([...agents, automation.agent])].map(a => <NativeSelectOption key={a} value={a}>{agentName(a)}</NativeSelectOption>)}
                </NativeSelect>
                <NativeSelect aria-label="Conversation" value={conversation} onChange={e => setConversation(e.target.value as Automation["conversation"])}>
                  {Object.entries(CONVERSATIONS).map(([value, label]) => <NativeSelectOption key={value} value={value}>{label}</NativeSelectOption>)}
                </NativeSelect>
                {changed && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void act(
                        () =>
                          factoryFetch(`/workplace/api/factory/automation-routes/${encodeURIComponent(automation.name)}`, {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ agent, conversation }),
                          }),
                        "Saved; it applies to future events",
                      )
                    }
                  >
                    Save
                  </Button>
                )}
              </div>
            </section>
            <section aria-label="What it's told">
              <h2 className="text-sm font-semibold">What it's told</h2>
              <p className="mt-2 whitespace-pre-wrap text-sm">{fullInstruction ? automation.instruction : lead}</p>
              {rest.length > 0 && (
                <Button variant="link" size="sm" className="px-0" onClick={() => setFullInstruction(value => !value)}>
                  {fullInstruction ? "Show less" : "Show the full instruction"}
                </Button>
              )}
            </section>
          </TabsContent>
          <TabsContent value="history" className="flex flex-col gap-4 pt-4">
            {automation.runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No runs in the last 7 days.</p>
            ) : (
              <ul className="flex flex-col divide-y rounded-lg border">
                {automation.runs.map(run => (
                  <li key={run.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                    <HealthDot value={run.status === "completed" ? "ok" : run.status === "running" ? "running" : "failing"} />
                    <span>{when(run.started_at)}</span>
                    {run.thread_key && <span className="text-muted-foreground">{run.thread_key}</span>}
                    {(run.grades ?? []).filter(g => g.passed === false).map(g => (
                      <Badge key={g.judge} variant="destructive" title={g.reason}>{humanize(g.judge)}: fail</Badge>
                    ))}
                    <span className="ml-auto flex gap-1">
                      {run.chat && <Button variant="ghost" size="sm" onClick={() => navigate(`/personal?chat=${encodeURIComponent(run.session_id)}`)}>Open chat</Button>}
                      <Button variant="ghost" size="sm" onClick={() => navigate(`/factory/runs/${run.id}`)}>Run details</Button>
                    </span>
                    {run.error && <span className="w-full truncate text-destructive" title={run.error}>{run.error}</span>}
                  </li>
                ))}
              </ul>
            )}
            {automation.skipped.length > 0 && (
              <div>
                <Button variant="link" size="sm" className="px-0" onClick={() => setIgnored(value => !value)}>
                  {ignored ? "Hide" : "Show"} {automation.skipped.length === 1 ? "the event" : `the ${automation.skipped.length} events`} it ignored
                </Button>
                {ignored && (
                  <ul className="mt-2 flex flex-col divide-y rounded-lg border">
                    {automation.skipped.map(event => (
                      <li key={event.id} className="flex flex-wrap gap-3 px-4 py-3 text-sm">
                        <span>{when(event.received_at)}</span>
                        <span>{describeTrigger({ provider: event.provider, event: event.event })}</span>
                        {event.thread_key && <span className="text-muted-foreground">{event.thread_key}</span>}
                        <span className="text-muted-foreground">{event.detail || event.status}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
