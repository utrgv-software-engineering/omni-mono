import { useEffect, useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { Badge } from "@omniagents/ui/badge";
import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@omniagents/ui/field";
import { Input } from "@omniagents/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@omniagents/ui/popover";
import { factoryBase } from "./factory-scope";

type Pull = {number: number; title: string};
type Plan = { repository: string; provider: string; branch: string; path: string; revision: string;
  services: {name: string; ports: number[]}[]; variables: string[]; generated: string[];
  open_pulls: Pull[] };

async function request(action: string, body: unknown) {
  const id = factoryBase().split("/").pop();
  const response = await fetch(`/workplace/api/factories/${id}/previews/${action}`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Unable to configure previews");
  return value;
}

// Open pull requests to preview as well; previews otherwise begin with PRs opened after enabling.
function PullPicker({ pulls, chosen, disabled, onChange }: {pulls: Pull[]; chosen: number[]; disabled: boolean; onChange: (chosen: number[]) => void}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const term = filter.trim().toLowerCase().replace(/^#/, "");
  const shown = pulls.filter(p => !term || String(p.number).startsWith(term) || p.title.toLowerCase().includes(term));
  const toggle = (n: number) => onChange(chosen.includes(n) ? chosen.filter(c => c !== n) : [...chosen, n]);
  return <Field>
    <FieldLabel>Open pull requests</FieldLabel>
    <div className="flex flex-wrap items-center gap-1.5">
      {chosen.map(n => <Badge key={n} variant="secondary" className="gap-1">#{n}
        <button type="button" aria-label={`Remove #${n}`} disabled={disabled} onClick={() => toggle(n)}><X className="size-3" /></button>
      </Badge>)}
      <Popover open={open} onOpenChange={o => { setOpen(o); setFilter(""); }}>
        <PopoverTrigger asChild><Button variant="outline" size="sm" disabled={disabled}>
          {chosen.length ? "Choose more" : `Choose from ${pulls.length} open`}<ChevronDown />
        </Button></PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-0">
          <div className="border-b p-2"><Input autoFocus value={filter} onChange={e => setFilter(e.target.value)} placeholder="Number or title" className="h-7 text-xs" /></div>
          <div className="max-h-64 overflow-y-auto py-1">
            {shown.map(p => <button key={p.number} type="button" onClick={() => toggle(p.number)}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-accent">
              <Check className={`size-3.5 shrink-0 ${chosen.includes(p.number) ? "" : "invisible"}`} />
              <span className="text-muted-foreground">#{p.number}</span><span className="truncate">{p.title}</span>
            </button>)}
            {shown.length === 0 && <div className="px-3 py-2 text-xs text-muted-foreground">No pull request matches.</div>}
          </div>
        </PopoverContent>
      </Popover>
    </div>
    <FieldDescription>Pull requests opened from now on preview automatically. Choose open ones to preview too.</FieldDescription>
  </Field>;
}

export function PreviewSetup({ repository, onClose }: {repository: string | null; onClose: () => void}) {
  const [path, setPath] = useState("");
  const [editingPath, setEditingPath] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [services, setServices] = useState<Record<string, number>>({});
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [include, setInclude] = useState<number[]>([]);
  async function inspect(chosenPath: string) {
    if (!repository) return;
    setBusy(true); setError(""); setPlan(null);
    try {
      const value: Plan = await request("inspect", {repository, path: chosenPath});
      setPlan(value); setPath(value.path); setEditingPath(false);
      const web = value.services.filter(s => ["web", "app", "frontend"].includes(s.name) && s.ports.length === 1);
      setServices(web.length === 1 ? {[web[0].name]: web[0].ports[0]} : {});
    } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => {
    setPath(""); setEditingPath(false); setPlan(null); setError(""); setVariables({}); setServices({}); setEnabled(false); setInclude([]);
    if (repository) void inspect("");
  }, [repository]);
  // Generated values (database passwords, secret key bases) need no one's input.
  const asked = plan ? plan.variables.filter(v => !plan.generated.includes(v)) : [];
  const ready = !!plan && Object.keys(services).length > 0 && asked.every(v => variables[v]);
  function enable() {
    if (!plan) return;
    setBusy(true); setError("");
    void request("enable", {...plan, services, variables, include}).then(() => {setEnabled(true); setVariables({});}).catch(e => setError(e.message)).finally(() => setBusy(false));
  }
  return <Dialog open={!!repository} onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <DialogContent><DialogHeader><DialogTitle>PR previews</DialogTitle><DialogDescription>{repository}</DialogDescription></DialogHeader>
      {enabled ? <p className="text-sm">Enabled. Pull requests opened from now on targeting {plan?.branch}{include.length ? `, and ${include.map(n => `#${n}`).join(", ")},` : ""} deploy automatically. Preview links and build status appear on each PR.</p>
      : editingPath || (!plan && error) ? <form className="flex items-end gap-2" onSubmit={e => { e.preventDefault(); void inspect(path); }}>
          <Field className="flex-1"><FieldLabel htmlFor="preview-compose">Compose file</FieldLabel>
            <Input id="preview-compose" value={path} placeholder="Detect automatically" disabled={busy} onChange={e => setPath(e.target.value)} /></Field>
          <Button type="submit" variant="outline" disabled={busy}>{busy ? "Reading…" : "Read"}</Button>
        </form>
      : !plan ? <p className="text-sm text-muted-foreground">Reading the repository…</p>
      : <FieldGroup>
          <p className="text-sm text-muted-foreground">From <code>{plan.path}</code> on {plan.branch} · <button type="button" className="underline underline-offset-2 hover:text-foreground" disabled={busy} onClick={() => setEditingPath(true)}>Change</button></p>
          <FieldSet><FieldLegend variant="label">Public services</FieldLegend>
            <FieldDescription>Databases and workers stay private.</FieldDescription>
            <div className="flex flex-col gap-1.5">{plan.services.map(service => <div key={service.name} className="flex h-8 items-center gap-2">
              <Checkbox id={`preview-${service.name}`} checked={service.name in services} disabled={busy} onCheckedChange={checked => setServices(current => {
                const next = {...current}; if (checked) next[service.name] = service.ports[0] || 3000; else delete next[service.name]; return next;
              })} /><FieldLabel htmlFor={`preview-${service.name}`} className="w-24 font-normal">{service.name}</FieldLabel>
              {service.name in services && <Input aria-label={`${service.name} HTTP port`} type="number" min={1} max={65535} className="h-8 w-24" value={services[service.name]} disabled={busy} onChange={e => setServices({...services, [service.name]: Number(e.target.value)})} />}
            </div>)}</div>
          </FieldSet>
          {asked.map(name => <Field key={name}><FieldLabel htmlFor={`preview-var-${name}`}>{name}</FieldLabel><Input id={`preview-var-${name}`} type="password" autoComplete="new-password" placeholder="Preview value" value={variables[name] || ""} disabled={busy} onChange={e => setVariables({...variables, [name]: e.target.value})} /></Field>)}
          {plan.open_pulls.length > 0 ? <PullPicker pulls={plan.open_pulls} chosen={include} disabled={busy} onChange={setInclude} />
            : <FieldDescription>Pull requests opened from now on preview automatically.</FieldDescription>}
        </FieldGroup>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <DialogFooter>
        {enabled ? <Button onClick={onClose}>Done</Button> : <>
          <Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>
          <Button disabled={busy || !ready} onClick={enable}>{busy && plan ? "Setting up previews…" : "Enable PR previews"}</Button>
        </>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
