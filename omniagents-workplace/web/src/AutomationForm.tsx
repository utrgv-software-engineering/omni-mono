import { lazy, Suspense, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import { NativeSelect, NativeSelectOptGroup, NativeSelectOption } from "@omniagents/ui/native-select";
import { factoryFetch } from "./factory-scope";
import { EVENTS, describeTrigger, type Trigger } from "./automation-triggers";
import { API, json, loadTree, savedMessage, send, type DefinitionError, type Saved } from "./definition-api";
import { useDark } from "./Agents";
import { scopeLabel, useScopeLabels } from "./automation-scope-labels";

const CodeMirrorEditor = lazy(() => import("@omniagents/ui/code-editor").then(m => ({ default: m.CodeMirrorEditor })));

export type AutomationFields = {
  description: string;
  agent: string | null;
  triggers: Trigger[];
  instruction: string;
  conversation: "auto" | "thread" | "new";
};

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const PROVIDERS: Record<string, string> = { github: "GitHub", "azure-devops": "Azure DevOps", teams: "Teams", slack: "Slack", linear: "Linear", shared: "Shared files", memory: "Memory" };
/** The filters a form shows for each source, as comma-separated lists; others are kept as they are. */
const FILTERS: Record<string, { key: string; label: string; placeholder: string }[]> = {
  "azure-devops": [
    { key: "connections", label: "Connections", placeholder: "Connection ID" },
    { key: "projects", label: "Projects", placeholder: "Project GUID" },
    { key: "repositories", label: "Repositories", placeholder: "Repository GUID" },
    { key: "pipelines", label: "Pipelines", placeholder: "Pipeline ID" },
    { key: "branches", label: "Branches", placeholder: "refs/heads/main" },
    { key: "base_branches", label: "Target branches", placeholder: "refs/heads/main" },
    { key: "statuses", label: "Statuses", placeholder: "active, completed" },
    { key: "results", label: "Build results", placeholder: "succeeded, failed" },
  ],
  github: [
    { key: "repos", label: "Repositories", placeholder: "acme/webapp, */api-service" },
    { key: "labels", label: "Labels", placeholder: "bug" },
    { key: "branches", label: "Branches", placeholder: "main" },
  ],
  slack: [{ key: "channels", label: "Channels", placeholder: "support" }],
  teams: [{ key: "channels", label: "Channels", placeholder: "Teams channel ID" }],
  linear: [{ key: "teams", label: "Teams", placeholder: "ENG" }],
};

type Schedule = { kind: "daily" | "weekdays" | "weekly" | "custom"; time: string; day: number; cron: string };

/** A cron in the form's words, when it is one of them. */
export function readCron(cron: string): Schedule {
  const [minute, hour, dom, month, dow] = cron.trim().split(/\s+/);
  const time = /^\d+$/.test(minute ?? "") && /^\d+$/.test(hour ?? "") ? `${hour!.padStart(2, "0")}:${minute!.padStart(2, "0")}` : "09:00";
  if (/^\d+$/.test(minute ?? "") && /^\d+$/.test(hour ?? "") && dom === "*" && month === "*") {
    if (dow === "*") return { kind: "daily", time, day: 1, cron };
    if (dow === "1-5") return { kind: "weekdays", time, day: 1, cron };
    if (/^[0-6]$/.test(dow ?? "")) return { kind: "weekly", time, day: Number(dow), cron };
  }
  return { kind: "custom", time, day: 1, cron };
}

export function writeCron(schedule: Schedule): string {
  const [hour, minute] = schedule.time.split(":").map(Number);
  const at = `${minute ?? 0} ${hour ?? 9}`;
  if (schedule.kind === "daily") return `${at} * * *`;
  if (schedule.kind === "weekdays") return `${at} * * 1-5`;
  if (schedule.kind === "weekly") return `${at} * * ${schedule.day}`;
  return schedule.cron;
}

const list = (value: unknown) => (Array.isArray(value) ? value.map(String).join(", ") : value == null ? "" : String(value));
const parse = (text: string) => text.split(",").map(v => v.trim()).filter(Boolean);

/** One thing that starts the automation: a schedule, or an event with its filters. */
function TriggerRow({ trigger, name, onChange, onRemove }: { trigger: Trigger; name: string; onChange: (t: Trigger) => void; onRemove?: () => void }) {
  const labels = useScopeLabels([trigger]);
  const kind = trigger.provider === "schedule" ? "schedule" : `${trigger.provider}:${trigger.event}`;
  const schedule = readCron(trigger.schedule?.cron ?? "0 9 * * *");
  const setSchedule = (next: Partial<Schedule>) => {
    const merged = { ...schedule, ...next };
    onChange({ ...trigger, provider: "schedule", schedule: { ...trigger.schedule, name: trigger.schedule?.name || name, cron: writeCron(merged) } });
  };
  const byProvider = Object.keys(EVENTS).reduce<Record<string, string[]>>((acc, key) => {
    const provider = key.split(":")[0]!;
    (acc[provider] ??= []).push(key);
    return acc;
  }, {});
  return (
    <div className="flex flex-col gap-2 rounded-md border p-3">
      <div className="flex items-center gap-2">
        <NativeSelect
          aria-label="When"
          className="min-w-0 flex-1"
          value={kind}
          onChange={e => {
            const value = e.target.value;
            if (value === "schedule") onChange({ provider: "schedule", schedule: { name, cron: "0 9 * * *" } });
            else {
              const [provider, event] = value.split(":");
              onChange({ provider, event, ...(provider === trigger.provider ? { filter: trigger.filter } : {}) });
            }
          }}
        >
          <NativeSelectOption value="schedule">On a schedule</NativeSelectOption>
          {Object.entries(byProvider).map(([provider, keys]) => (
            <NativeSelectOptGroup key={provider} label={PROVIDERS[provider] ?? provider}>
              {keys.map(key => <NativeSelectOption key={key} value={key}>{EVENTS[key]}</NativeSelectOption>)}
            </NativeSelectOptGroup>
          ))}
          {kind !== "schedule" && !EVENTS[kind] && <NativeSelectOption value={kind}>{describeTrigger(trigger)}</NativeSelectOption>}
        </NativeSelect>
        {onRemove && <Button variant="ghost" size="icon-sm" aria-label="Remove this trigger" onClick={onRemove}><Trash2 /></Button>}
      </div>
      {trigger.provider === "schedule" ? (
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect aria-label="How often" value={schedule.kind} onChange={e => setSchedule({ kind: e.target.value as Schedule["kind"] })}>
            <NativeSelectOption value="daily">Every day</NativeSelectOption>
            <NativeSelectOption value="weekdays">Every weekday</NativeSelectOption>
            <NativeSelectOption value="weekly">Every week</NativeSelectOption>
            <NativeSelectOption value="custom">Custom (cron)</NativeSelectOption>
          </NativeSelect>
          {schedule.kind === "weekly" && (
            <NativeSelect aria-label="Day" value={String(schedule.day)} onChange={e => setSchedule({ day: Number(e.target.value) })}>
              {DAYS.map((day, index) => <NativeSelectOption key={day} value={String(index)}>{day}</NativeSelectOption>)}
            </NativeSelect>
          )}
          {schedule.kind === "custom" ? (
            <Input aria-label="Cron" className="w-40" value={schedule.cron} onChange={e => setSchedule({ cron: e.target.value })} />
          ) : (
            <Input aria-label="Time" type="time" className="w-32" value={schedule.time} onChange={e => setSchedule({ time: e.target.value })} />
          )}
          <span className="text-xs text-muted-foreground">server time</span>
        </div>
      ) : (
        (FILTERS[trigger.provider ?? ""] ?? []).map(filter => (
          <div key={filter.key} className="flex flex-wrap items-center gap-2">
            <Label className="w-28 shrink-0 text-xs text-muted-foreground">{filter.label}</Label>
            <Input
              aria-label={filter.label}
              placeholder={`Any (e.g. ${filter.placeholder})`}
              value={list(trigger.filter?.[filter.key])}
              onChange={e => {
                const next = { ...(trigger.filter ?? {}) };
                const values = parse(e.target.value);
                if (values.length) next[filter.key] = values;
                else delete next[filter.key];
                onChange({ ...trigger, filter: Object.keys(next).length ? next : undefined });
              }}
            />
            {trigger.provider === "azure-devops" && ["connections", "projects", "repositories"].includes(filter.key) && (
              <span className="w-full pl-28 text-xs text-muted-foreground">
                {parse(list(trigger.filter?.[filter.key])).map(id => scopeLabel(labels, trigger, filter.key === "connections" ? "connection" : filter.key === "projects" ? "project" : "repo", id)).join(", ")}
              </span>
            )}
          </div>
        ))
      )}
    </div>
  );
}

/** A new automation (``name`` undefined) or an existing one, as fields. */
export function AutomationForm({
  name: existing,
  initial,
  commit,
  agents,
  onSaved,
  onCancel,
}: {
  name?: string;
  initial?: AutomationFields;
  commit?: string;
  agents: { key: string; name: string }[];
  onSaved: (name: string) => void;
  onCancel: () => void;
}) {
  const dark = useDark();
  const [name, setName] = useState(existing ?? "");
  const [fields, setFields] = useState<AutomationFields>(
    initial ?? { description: "", agent: null, triggers: [{ provider: "schedule", schedule: { name: "", cron: "0 9 * * 1-5" } }], instruction: "", conversation: "auto" },
  );
  const [base, setBase] = useState(commit);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  useEffect(() => {
    if (!base) void loadTree().then(tree => setBase(tree.main)).catch(() => {});
  }, [base]);
  const set = (next: Partial<AutomationFields>) => setFields(current => ({ ...current, ...next }));
  const key = existing ?? name;
  const save = async () => {
    if (!base) return;
    setBusy(true);
    setProblems([]);
    try {
      const triggers = fields.triggers.map(t => (t.provider === "schedule" ? { ...t, schedule: { ...t.schedule, name: t.schedule?.name || key } } : t));
      const saved = await send<Saved>(`/automations/${encodeURIComponent(key)}`, "PUT", { base_commit: base, ...fields, triggers }, "Unable to save the automation");
      const message = savedMessage(saved, existing ? "Saved. It runs this way from now on." : "Added. It runs from now on.");
      toast[message.kind](message.text);
      onSaved(key);
    } catch (e) {
      const err = e as DefinitionError;
      if (err.problems) setProblems(Object.values(err.problems).flat());
      else toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-5">
      {!existing && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="automation-name">Name</Label>
          <Input id="automation-name" value={name} placeholder="weekly-report" onChange={e => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))} />
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="automation-description">What it does</Label>
        <Input id="automation-description" value={fields.description} placeholder="writes the weekly report" onChange={e => set({ description: e.target.value })} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>When</Label>
        {fields.triggers.map((trigger, index) => (
          <TriggerRow
            key={index}
            trigger={trigger}
            name={key}
            onChange={t => set({ triggers: fields.triggers.map((old, i) => (i === index ? t : old)) })}
            onRemove={fields.triggers.length > 1 ? () => set({ triggers: fields.triggers.filter((_, i) => i !== index) }) : undefined}
          />
        ))}
        <Button variant="ghost" size="sm" className="self-start" onClick={() => set({ triggers: [...fields.triggers, { provider: "github", event: "pull_request_opened" }] })}>
          <Plus />Another trigger
        </Button>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="automation-agent">Who does it</Label>
        <NativeSelect id="automation-agent" value={fields.agent ?? ""} onChange={e => set({ agent: e.target.value || null })}>
          <NativeSelectOption value="">The default agent for automations</NativeSelectOption>
          {agents.map(a => <NativeSelectOption key={a.key} value={a.key}>{a.name}</NativeSelectOption>)}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>What it's told</Label>
        <div className="h-64 overflow-hidden rounded-md border">
          <Suspense fallback={<p className="p-3 text-sm text-muted-foreground">Loading the editor…</p>}>
            <CodeMirrorEditor value={fields.instruction} onChange={instruction => set({ instruction })} onSave={() => void save()} path="instruction.md" dark={dark} ariaLabel="What it's told" />
          </Suspense>
        </div>
      </div>
      {problems.length > 0 && (
        <ul role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm">
          {problems.map(p => <li key={p}>{p}</li>)}
        </ul>
      )}
      <div className="flex gap-2">
        <Button disabled={busy || !base || !key || !fields.description.trim() || !fields.instruction.trim()} onClick={() => void save()}>
          {existing ? "Save" : "Add automation"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/** An existing automation's fields, read from the factory; ``null`` when only its file can show it. */
export function loadAutomation(name: string) {
  return factoryFetch(`${API}/automations/${encodeURIComponent(name)}`).then(r =>
    json<{ commit: string; path: string; fields: AutomationFields | null }>(r, "Unable to open the automation"),
  );
}

export async function deleteAutomation(name: string) {
  const saved = await send<Saved>(`/automations/${encodeURIComponent(name)}`, "DELETE", undefined, "Unable to delete the automation");
  return savedMessage(saved, "Deleted.");
}
