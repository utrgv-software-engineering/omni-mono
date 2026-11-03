import { useCallback, useEffect, useState } from "react";
import { Archive, ArrowLeft, AtSign, CircleCheck, CircleX, Clock, FileCode, FolderGit2, FolderOpen, GitBranch, GitMerge, Hash, Pencil, Play, Plus, Smile, Sparkles, Tag, Trash2, UserCheck, Users, Zap, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { AgentAvatar } from "@omniagents/chat-ui/components/omni-avatar/AgentAvatar";
import { Badge } from "@omniagents/ui/badge";
import { Button } from "@omniagents/ui/button";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Switch } from "@omniagents/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@omniagents/ui/tabs";
import { factoryFetch } from "./factory-scope";
import { conditionParts, describeEvent, describeTrigger, scheduleName, type Condition, type Trigger } from "./automation-triggers";
import { FileEditor } from "./Agents";
import { AutomationForm, deleteAutomation, loadAutomation, type AutomationFields } from "./AutomationForm";

/** The new-automation page's name in the path; no automation can have it. */
const NEW = "+new";

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
  /** What stops it running at all ("Self-improvement is off"); null when nothing does. */
  blocked?: string | null;
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

type Health = "off" | "blocked" | "failing" | "running" | "ok" | "quiet";
const HEALTH: Record<Health, { label: string; dot: string }> = {
  off: { label: "Off", dot: "border border-muted-foreground" },
  blocked: { label: "Can't run", dot: "bg-amber-500" },
  failing: { label: "Its last run failed", dot: "bg-destructive" },
  running: { label: "Running", dot: "bg-sky-500 animate-pulse" },
  ok: { label: "Working", dot: "bg-emerald-500" },
  quiet: { label: "Nothing to do yet", dot: "bg-muted-foreground/40" },
};

export const humanize = (name: string) => name.replace(/[_-]+/g, " ").replace(/^\w/, c => c.toUpperCase());
const when = (seconds?: number | null) => (seconds ? new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "–");

/** How an automation is doing, in one of six words a dot can carry. */
export function health(automation: Automation): Health {
  if (!automation.enabled) return "off";
  if (automation.blocked) return "blocked";
  const last = automation.runs[0];
  if (last?.status === "running") return "running";
  if (last && ["failed", "interrupted"].includes(last.status)) return "failing";
  return automation.runs.length ? "ok" : "quiet";
}

/** Whether a person has something to do about it: a failed run to look at, or something stopping it. */
const needsAttention = (automation: Automation) => ["failing", "blocked"].includes(health(automation));

/** The agent tab last open, so coming back from an automation returns to it. */
let lastTab: string | undefined;

/** What it does, as a heading: "Reviews the pull request". */
export const action = (automation: Automation) =>
  (automation.description || humanize(automation.name).toLowerCase()).replace(/^\w/, c => c.toUpperCase());

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

const CHIPS: Record<Condition["kind"], { icon: LucideIcon; text: (value: string) => string; mono?: boolean }> = {
  repo: { icon: FolderGit2, text: v => v.replace(/^\*\//, "") },
  base: { icon: GitMerge, text: v => `into ${v}`, mono: true },
  branch: { icon: GitBranch, text: v => v, mono: true },
  path: { icon: FileCode, text: v => v, mono: true },
  label: { icon: Tag, text: v => v },
  not_label: { icon: Tag, text: v => `not ${v}` },
  mention: { icon: AtSign, text: v => v },
  assignee: { icon: UserCheck, text: v => `@${v}` },
  outcome: { icon: CircleX, text: v => v },
  channel: { icon: Hash, text: v => v },
  emoji: { icon: Smile, text: v => `:${v}:` },
  team: { icon: Users, text: v => v },
};
const OUTCOMES: Record<string, { icon: LucideIcon; className: string }> = {
  failure: { icon: CircleX, className: "border-destructive/40 text-destructive" },
  success: { icon: CircleCheck, className: "border-emerald-500/40 text-emerald-700 dark:text-emerald-400" },
};

function Chip({ icon: Icon, children, mono, className = "", title }: { icon: LucideIcon; children: React.ReactNode; mono?: boolean; className?: string; title?: string }) {
  return (
    <Badge variant="outline" title={title} className={`font-normal ${mono ? "font-mono" : ""} ${className}`}>
      <Icon />{children}
    </Badge>
  );
}

/** What starts it: the event in words, then each filter value as a chip. Folded, several repositories are one chip that names them on hover. */
function TriggerLine({ trigger, fold = false }: { trigger: Trigger; fold?: boolean }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <SourceIcon trigger={trigger} />
      <span>{describeEvent(trigger)}</span>
      {trigger.provider !== "schedule" &&
        conditionParts(trigger.filter).flatMap(({ kind, values }) => {
          const chip = CHIPS[kind];
          if (fold && kind === "repo" && values.length > 1)
            return [<Chip key={kind} icon={chip.icon} title={values.map(chip.text).join(", ")}>{values.length} repos</Chip>];
          return values.map(value => {
            const outcome = kind === "outcome" ? OUTCOMES[value] : undefined;
            return (
              <Chip key={`${kind}:${value}`} icon={outcome?.icon ?? chip.icon} mono={chip.mono} className={outcome?.className}>
                {chip.text(value)}
              </Chip>
            );
          });
        })}
    </span>
  );
}

function HealthDot({ value }: { value: Health }) {
  return <span className={`size-2.5 shrink-0 rounded-full ${HEALTH[value].dot}`} role="img" aria-label={HEALTH[value].label} title={HEALTH[value].label} />;
}

/** Every automation, as a sentence each: what starts it and which agent does what. */
export function AutomationsApp({ name, navigate, onFactorySettings }: { name?: string; navigate: (path: string) => void; onFactorySettings?: () => void }) {
  const [view, setView] = useState<View>();
  const [tab, setTab] = useState(lastTab);
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
  if (name === NEW)
    return (
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-6">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
          <div><Button variant="ghost" size="sm" onClick={() => navigate("/automations")}><ArrowLeft />Automations</Button></div>
          <h1 className="text-xl font-semibold">New automation</h1>
          <AutomationForm
            agents={agents.map(a => ({ key: a, name: agentName(a) }))}
            onSaved={saved => navigate(`/automations/${encodeURIComponent(saved)}`)}
            onCancel={() => navigate("/automations")}
          />
        </div>
      </div>
    );
  if (name && !selected) return <p className="p-6 text-sm">No automation named “{name}”.</p>;
  if (selected)
    return <AutomationDetail automation={selected} agents={agents} agentName={agentName} navigate={navigate} reload={load} onToggle={enabled => void toggle(selected, enabled)} onFactorySettings={onFactorySettings} />;

  const order = [...new Set([view.chat_agent, ...(view.chat_agents ?? [])].filter((a): a is string => !!a))];
  const rank = (agent: string) => (order.includes(agent) ? order.indexOf(agent) : order.length);
  // One tab per agent: Chat's agents always (so it's clear where theirs will go), then any other agent with automations.
  const tabs = [...new Set([...order, ...view.automations.map(a => a.agent)])]
    .sort((a, b) => rank(a) - rank(b) || agentName(a).localeCompare(agentName(b)))
    .map(agent => ({ agent, items: view.automations.filter(a => a.agent === agent) }));
  const current = tabs.some(t => t.agent === tab) ? tab! : tabs[0]?.agent;
  const choose = (agent: string) => {
    lastTab = agent;
    setTab(agent);
  };

  // Each tab is one agent, so a row leads with what it does and puts what starts it on a quieter line.
  const row = (automation: Automation) => {
    const [first, ...more] = automation.triggers;
    return (
    <li key={automation.name} className="flex items-center gap-3 px-4 py-3">
      <div className={`flex min-w-0 flex-1 flex-col gap-1 ${automation.enabled ? "" : "opacity-60"}`}>
        <button
          type="button"
          aria-label={humanize(automation.name)}
          onClick={() => navigate(`/automations/${encodeURIComponent(automation.name)}`)}
          className="text-left text-sm font-medium hover:underline"
        >
          {action(automation)}
        </button>
        <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          {first ? <TriggerLine trigger={first} fold /> : "Started by hand"}
          {more.length > 0 && <Badge variant="secondary" title={more.map(describeTrigger).join("\n")}>+{more.length}</Badge>}
        </span>
        {needsAttention(automation) && (
          <span className="text-xs text-muted-foreground">
            {health(automation) === "blocked" ? `${automation.blocked}. Open it to see how to turn it on.` : "Its last run failed. Open it to see why."}
          </span>
        )}
      </div>
      <HealthDot value={health(automation)} />
      <Switch
        aria-label={`${automation.enabled ? "Turn off" : "Turn on"} ${humanize(automation.name)}`}
        checked={automation.enabled}
        onCheckedChange={enabled => void toggle(automation, enabled)}
      />
    </li>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-6">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold">Automations</h1>
            <p className="mt-1 text-sm text-muted-foreground">Work the agents take on without being asked.</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => navigate(`/automations/${encodeURIComponent(NEW)}`)}><Plus />New automation</Button>
        </div>
        {view.automations.length === 0 && <p className="text-sm text-muted-foreground">This factory has no automations.</p>}
        {current && (
          <Tabs value={current} onValueChange={choose}>
            <TabsList className="h-auto flex-wrap justify-start">
              {tabs.map(({ agent, items }) => (
                <TabsTrigger key={agent} value={agent} className="gap-2">
                  <AgentAvatar seed={agent} size={16} />
                  {agentName(agent)} · {items.length}
                  {items.some(needsAttention) && <span className="size-2 rounded-full bg-destructive" role="img" aria-label="Needs attention" />}
                </TabsTrigger>
              ))}
            </TabsList>
            {tabs.map(({ agent, items }) => {
              const attention = items.filter(needsAttention);
              const rest = items.filter(a => !needsAttention(a));
              return (
                <TabsContent key={agent} value={agent} className="flex flex-col gap-6 pt-4">
                  {items.length === 0 && (
                    <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
                      No automations yet. Add one and choose {agentName(agent)} to run it.
                      <Button variant="outline" size="sm" onClick={() => navigate(`/automations/${encodeURIComponent(NEW)}`)}><Plus />New automation</Button>
                    </div>
                  )}
                  {attention.length > 0 && (
                    <section aria-label="Needs attention">
                      <h2 className="mb-2 text-sm font-semibold">Needs attention</h2>
                      <ul className="divide-y rounded-lg border border-amber-500/40">{attention.map(row)}</ul>
                    </section>
                  )}
                  {rest.length > 0 && (
                    <section aria-label={agentName(agent)}>
                      {attention.length > 0 && <h2 className="mb-2 text-sm font-semibold">Everything else</h2>}
                      <ul className="divide-y rounded-lg border">{rest.map(row)}</ul>
                    </section>
                  )}
                </TabsContent>
              );
            })}
          </Tabs>
        )}
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
  onFactorySettings,
}: {
  automation: Automation;
  agents: string[];
  agentName: (id: string) => string;
  navigate: (path: string) => void;
  reload: () => Promise<void>;
  onToggle: (enabled: boolean) => void;
  onFactorySettings?: () => void;
}) {
  const [agent, setAgent] = useState(automation.agent);
  const [conversation, setConversation] = useState(automation.conversation);
  const [busy, setBusy] = useState(false);
  const [fullInstruction, setFullInstruction] = useState(false);
  const [ignored, setIgnored] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<{ commit: string; path: string; fields: AutomationFields | null }>();
  useEffect(() => {
    if (!editing) return setForm(undefined);
    void loadAutomation(automation.name)
      .then(setForm)
      .catch(e => toast.error(e instanceof Error ? e.message : "Unable to open the automation"));
  }, [editing, automation.name]);
  const remove = async () => {
    if (!window.confirm(`Delete the automation ${humanize(automation.name)}? Its runs stay in History.`)) return;
    try {
      const message = await deleteAutomation(automation.name);
      toast[message.kind](message.text);
      navigate("/automations");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to delete the automation");
    }
  };
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
              <Pencil />{editing ? "Close" : "Edit"}
            </Button>
            <Button variant="ghost" size="icon-sm" aria-label={`Delete ${humanize(automation.name)}`} onClick={() => void remove()}>
              <Trash2 />
            </Button>
            <Switch
              aria-label={`${automation.enabled ? "Turn off" : "Turn on"} ${humanize(automation.name)}`}
              checked={automation.enabled}
              onCheckedChange={onToggle}
            />
          </span>
        </div>
        <p className="text-sm">{sentence(automation, agentName(automation.agent))}</p>
        {state === "blocked" && (
          <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/40 p-3 text-sm">
            <span className="flex-1">{automation.blocked}, so this automation can't run. It's turned on in Factory changes, after choosing the factory's repository.</span>
            {onFactorySettings && <Button variant="outline" size="sm" onClick={onFactorySettings}>Open Factory changes</Button>}
          </div>
        )}
        {state === "failing" && (
          <div role="status" className="flex flex-wrap items-center gap-3 rounded-lg border border-destructive/40 p-3 text-sm">
            <span className="min-w-0 flex-1">
              Its last run failed{automation.runs[0].error ? <>: <span className="text-destructive">{automation.runs[0].error}</span></> : "."}
            </span>
            <Button variant="outline" size="sm" onClick={() => navigate(`/factory/runs/${automation.runs[0].id}`)}>Run details</Button>
          </div>
        )}
        {editing && (
          <section aria-label="Edit the automation" className="flex flex-col gap-2">
            {form === undefined ? (
              <p className="text-sm text-muted-foreground">Opening…</p>
            ) : form.fields ? (
              <AutomationForm
                name={automation.name}
                initial={form.fields}
                commit={form.commit}
                agents={agents.map(a => ({ key: a, name: agentName(a) }))}
                onSaved={() => {
                  setEditing(false);
                  void reload();
                }}
                onCancel={() => setEditing(false)}
              />
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  This automation's file has settings the form can't show; edit the file. Its description and triggers are at the top, between the <code>---</code> lines; what it's told follows.
                </p>
                <FileEditor path={form.path} label={`${humanize(automation.name)} automation`} onSaved={() => void reload()} />
              </>
            )}
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
                  <li key={index}><TriggerLine trigger={trigger} /></li>
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
                  {ignored ? "Hide" : "Show"} {automation.skipped.length === 1 ? "the event" : `the ${automation.skipped.length} events`} it didn't run for
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
