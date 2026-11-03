import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import { Input } from "@omniagents/ui/input";
import { factoryBase } from "./factory-scope";

type Plan = { repository: string; provider: string; branch: string; path: string; revision: string;
  services: {name: string; ports: number[]}[]; variables: string[]; generated: string[];
  open_pulls: {number: number; title: string}[] };

async function request(action: string, body: unknown) {
  const id = factoryBase().split("/").pop();
  const response = await fetch(`/workplace/api/factories/${id}/previews/${action}`, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Unable to configure previews");
  return value;
}

export function PreviewSetup({ repository, onClose }: {repository: string | null; onClose: () => void}) {
  const [path, setPath] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [services, setServices] = useState<Record<string, number>>({});
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [enabled, setEnabled] = useState(false);
  // Open pull requests to preview too; previews otherwise begin with PRs opened after enabling.
  const [include, setInclude] = useState<number[]>([]);
  useEffect(() => { setPath(""); setPlan(null); setError(""); setVariables({}); setServices({}); setEnabled(false); setInclude([]); }, [repository]);
  async function inspect() {
    setBusy(true); setError(""); setPlan(null);
    try {
      const value: Plan = await request("inspect", {repository, path});
      setPlan(value); setPath(value.path);
      const web = value.services.filter(s => ["web", "app", "frontend"].includes(s.name) && s.ports.length === 1);
      setServices(web.length === 1 ? {[web[0].name]: web[0].ports[0]} : {});
    } catch(e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <Dialog open={!!repository} onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <DialogContent><DialogHeader><DialogTitle>PR previews</DialogTitle><DialogDescription>{repository}</DialogDescription></DialogHeader>
      {enabled ? <p>Enabled. Pull requests opened from now on targeting {plan?.branch}{include.length ? `, and ${include.map(n => `#${n}`).join(", ")},` : ""} deploy automatically. Preview links and build status appear on each PR.</p> : <FieldGroup>
        <Field><FieldLabel htmlFor="preview-compose">Compose file</FieldLabel><Input id="preview-compose" value={path} placeholder="Detect automatically" disabled={busy} onChange={e => {setPath(e.target.value); setPlan(null);}} />
          <Button variant="outline" disabled={busy} onClick={() => void inspect()}>{busy && !plan ? "Inspecting…" : "Inspect repository"}</Button></Field>
        {plan && <>
          <FieldDescription>Branch: {plan.branch}. Select the services to expose over HTTP; databases and workers stay private.</FieldDescription>
          {plan.services.map(service => <Field key={service.name} orientation="horizontal">
            <Checkbox id={`preview-${service.name}`} checked={service.name in services} disabled={busy} onCheckedChange={checked => setServices(current => {
              const next = {...current}; if (checked) next[service.name] = service.ports[0] || 3000; else delete next[service.name]; return next;
            })} /><FieldLabel htmlFor={`preview-${service.name}`}>{service.name}</FieldLabel>
            {service.name in services && <Input aria-label={`${service.name} HTTP port`} type="number" min={1} max={65535} value={services[service.name]} disabled={busy} onChange={e => setServices({...services, [service.name]: Number(e.target.value)})} />}
          </Field>)}
          {plan.variables.map(name => <Field key={name}><FieldLabel htmlFor={`preview-var-${name}`}>{name}</FieldLabel><Input id={`preview-var-${name}`} type="password" autoComplete="new-password" placeholder={plan.generated.includes(name) ? "Generate automatically" : "Preview value"} value={variables[name] || ""} disabled={busy} onChange={e => setVariables({...variables, [name]: e.target.value})} /></Field>)}
          {plan.generated.length > 0 && <FieldDescription>Generated automatically: {plan.generated.join(", ")}.</FieldDescription>}
          <FieldDescription>Previews begin with pull requests opened from now on.{plan.open_pulls.length > 0 && " Choose any open ones to preview as well."}</FieldDescription>
          {plan.open_pulls.length > 0 && <div className="flex max-h-48 flex-col gap-2 overflow-y-auto">{plan.open_pulls.map(pull => <Field key={pull.number} orientation="horizontal">
            <Checkbox id={`preview-pull-${pull.number}`} checked={include.includes(pull.number)} disabled={busy} onCheckedChange={checked => setInclude(current => checked ? [...current, pull.number] : current.filter(n => n !== pull.number))} />
            <FieldLabel htmlFor={`preview-pull-${pull.number}`} className="font-normal">#{pull.number} {pull.title}</FieldLabel>
          </Field>)}</div>}
          <Button disabled={busy || !Object.keys(services).length} onClick={() => {
            setBusy(true); setError("");
            void request("enable", {...plan, services, variables, include}).then(() => {setEnabled(true); setVariables({});}).catch(e => setError(e.message)).finally(() => setBusy(false));
          }}>{busy ? "Setting up previews…" : "Enable PR previews"}</Button>
        </>}
      </FieldGroup>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </DialogContent>
  </Dialog>;
}
