import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { Input } from "@omniagents/ui/input";
import { factoryFetch } from "./factory-scope";

type Scope = { project: string; repositories: string[]; boards: boolean; pipelines: number[] };
type Connection = {
  id?: string; revision: number; label: string; organization: string;
  auth_type: "pat" | "bearer_env" | "azcli"; credential_env: string | null;
  scopes: Scope[]; app_ids: string[]; events: string[]; write_tools: string[];
  push_branches: string[]; interval_seconds: number; enabled: boolean;
  credential_configured?: boolean; health?: { error?: string; last_success?: number }; pending_deliveries?: number;
};
type Inventory = { connections: Connection[]; apps: { id: string; name: string }[]; events: string[]; write_tools: string[] };
type Resource = { id: string | number; name: string };
const API = "/workplace/api/azure-devops/connections";

async function request(path: string, method = "GET", body?: unknown) {
  const response = await factoryFetch(path, { method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Azure DevOps request failed. Check the connection fields and permissions.");
  return value;
}

function Choice({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (checked: boolean) => void; disabled: boolean }) {
  return <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />{label}</label>;
}

function toggle<T>(values: T[], value: T, selected: boolean): T[] {
  return selected ? [...new Set([...values, value])] : values.filter(item => item !== value);
}

export function AzureDevOpsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [draft, setDraft] = useState<Connection | null>(null);
  const [token, setToken] = useState("");
  const [projects, setProjects] = useState<Resource[]>([]);
  const [resources, setResources] = useState<Record<string, { repositories: Resource[]; pipelines: Resource[] }>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [savedMessage, setSavedMessage] = useState("");

  useEffect(() => {
    if (!open) return;
    let active = true;
    setDraft(null); setInventory(null); setAdvanced(false); setError(""); setToken(""); setProjects([]); setResources({}); setSavedMessage("");
    const load = () => request(API).then(value => { if (active) setInventory(value); }).catch(cause => { if (active) setError(cause.message); });
    void load();
    const timer = window.setInterval(() => { void load(); }, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, [open]);

  const action = async (work: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await work(); } catch (cause) { setError((cause as Error).message); } finally { setBusy(false); }
  };
  const edit = (connection: Connection) => { setDraft(connection); setAdvanced(false); setToken(""); setProjects([]); setResources({}); setPreview(""); setError(""); setSavedMessage(""); };
  const save = (credentialsOnly = false) => action(async () => {
    if (!draft) return;
    const { id, credential_configured: _credential, health: _health, pending_deliveries: _pending, ...body } = draft;
    const allowed = { revision: body.revision, label: body.label || body.organization, organization: body.organization, auth_type: body.auth_type, credential_env: body.credential_env, scopes: body.scopes, app_ids: body.app_ids, events: inventory?.events ?? body.events, write_tools: body.write_tools, push_branches: body.push_branches, interval_seconds: body.interval_seconds, enabled: body.enabled, ...(token ? { token } : {}) };
    if (credentialsOnly) {
      const current = inventory?.connections.find(value => value.id === id);
      allowed.scopes = current?.scopes ?? [];
      allowed.app_ids = current?.app_ids ?? [];
      allowed.events = current?.events ?? inventory?.events ?? [];
      allowed.write_tools = current?.write_tools ?? [];
      allowed.push_branches = current?.push_branches ?? ["refs/heads/factory/*"];
      allowed.interval_seconds = current?.interval_seconds ?? 60;
      allowed.enabled = current?.enabled ?? false;
    }
    const saved = await request(id ? `${API}/${id}` : API, id ? "PUT" : "POST", allowed);
    setDraft(credentialsOnly ? { ...draft, id: saved.id, revision: saved.revision, credential_configured: saved.credential_configured, enabled: saved.enabled } : saved);
    setToken(""); setSavedMessage(credentialsOnly ? "Credentials saved. Resource selection is separate; polling was not changed." : "Connection settings saved."); setInventory(await request(API));
  });
  const updateScope = (project: string, changes: Partial<Scope>) => {
    if (!draft) return;
    const existing = draft.scopes.find(scope => scope.project === project) ?? { project, repositories: [], pipelines: [], boards: false };
    setDraft({ ...draft, scopes: [...draft.scopes.filter(scope => scope.project !== project), { ...existing, ...changes }] });
  };

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>Azure DevOps connections</DialogTitle><DialogDescription>Connect your organization and choose projects and repositories.</DialogDescription></DialogHeader>
      {error && <div role="alert" className="space-y-2 text-sm text-destructive"><p>{error}</p><Button variant="outline" disabled={busy} onClick={() => void action(async () => { const current = await request(API); setInventory(current); if (draft?.id) { const found = current.connections.find((value: Connection) => value.id === draft.id); if (found) edit(found); } })}>Reload connection</Button></div>}
      {!inventory && <p role="status">Loading connections…</p>}
      {inventory && <div className="space-y-5">
        <div className="space-y-2">
          {inventory.connections.map(connection => <div key={connection.id} className="flex items-center justify-between gap-3 rounded-md border p-3">
            <div><p className="text-sm font-medium">{connection.label} · {connection.organization}</p><p className="text-xs text-muted-foreground">{connection.enabled ? "Polling enabled" : "Disabled"} · {connection.pending_deliveries ?? 0} queued{connection.health?.last_success ? ` · Last success ${new Date(connection.health.last_success * 1000).toLocaleString()}` : ""}</p>{connection.health?.error && <p className="text-sm text-destructive">{connection.health.error}</p>}</div>
            <Button variant="outline" disabled={busy} onClick={() => edit(connection)}>Configure</Button>
          </div>)}
          <Button variant="outline" disabled={busy} onClick={() => edit({ revision: 0, label: "", organization: "", auth_type: "pat", credential_env: null, scopes: [], app_ids: inventory.apps.length === 1 ? [inventory.apps[0].id] : [], events: inventory.events, write_tools: [], push_branches: ["refs/heads/factory/*"], interval_seconds: 60, enabled: false })}>Add connection</Button>
        </div>
        {draft && <form className="space-y-5 border-t pt-4" onSubmit={event => { event.preventDefault(); void save(); }}>
          <section className="space-y-3" aria-label="Organization and credential">
            <h3 className="text-sm font-medium">Organization and credential</h3>
            <label className="block space-y-1 text-sm">Organization<Input required value={draft.organization} disabled={busy} placeholder="my-organization" onChange={event => setDraft({ ...draft, organization: event.target.value })} /></label>
            {draft.auth_type === "pat" ? <label className="block space-y-1 text-sm">Personal access token<Input type="password" autoComplete="new-password" value={token} disabled={busy} onChange={event => setToken(event.target.value)} /><span className="text-xs text-muted-foreground">{draft.credential_configured ? "Leave blank to keep your saved token." : "Your token stays on the Workplace host, outside agent sandboxes."}</span></label> : <p className="text-sm text-muted-foreground">Using host authentication. Manage it in Advanced.</p>}
            <Button type="button" variant="outline" disabled={busy || !draft.organization} onClick={() => void save(true)}>Save credentials</Button>
            {savedMessage && <p role="status" className="text-sm">{savedMessage}</p>}
          </section>
          <p className="text-sm text-muted-foreground">{draft.id ? "Choose projects and repositories, then enable the connection. Set event triggers in Automations." : "Save your credential first to load projects and repositories."}</p>
          {draft.id && <section className="space-y-3" aria-label="Projects and repositories">
            <Button type="button" variant="outline" disabled={busy} onClick={() => void action(async () => { setProjects((await request(`${API}/${draft.id}/available`)).projects); })}>Load projects</Button>
            {[...new Map([...draft.scopes.map(scope => ({ id: scope.project, name: scope.project })), ...projects].map(project => [String(project.id), project])).values()].map(project => {
              const id = String(project.id); const scope = draft.scopes.find(value => value.project === id);
              return <div key={id} className="space-y-3 rounded-md border p-3">
                <Choice label={project.name} checked={!!scope} disabled={busy} onChange={selected => selected ? updateScope(id, {}) : setDraft({ ...draft, scopes: draft.scopes.filter(value => value.project !== id) })} />
                {scope && <div className="space-y-2 pl-6">
                  <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void action(async () => { const found = await request(`${API}/${draft.id}/available?project=${encodeURIComponent(id)}`); setResources(current => ({ ...current, [id]: { repositories: found.repositories, pipelines: current[id]?.pipelines ?? [] } })); })}>Load repositories</Button>
                  <p className="text-xs text-muted-foreground">Selected repositories: {scope.repositories.length}</p>
                  {resources[id]?.repositories.map(repo => <Choice key={repo.id} label={`Repository · ${repo.name}`} checked={scope.repositories.includes(String(repo.id))} disabled={busy} onChange={selected => updateScope(id, { repositories: toggle(scope.repositories, String(repo.id), selected) })} />)}
              {preview && <p role="status" className="text-sm">{preview}</p>}
            </div>}
              </div>;
            })}
          </section>}
          <details open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)} className="space-y-3">
            <summary className="cursor-pointer text-sm font-medium">Advanced</summary>
            {advanced && <div className="space-y-5 rounded-md border p-3">
              <label className="block space-y-1 text-sm">Connection name<Input value={draft.label} placeholder={draft.organization} maxLength={120} disabled={busy} onChange={event => setDraft({ ...draft, label: event.target.value })} /></label>
              <label className="block space-y-1 text-sm">Authentication<select className="w-full rounded-md border bg-background p-2" value={draft.auth_type} disabled={busy} onChange={event => setDraft({ ...draft, auth_type: event.target.value as Connection["auth_type"] })}><option value="pat">Personal access token</option><option value="bearer_env">Host bearer environment variable</option><option value="azcli">Host Azure CLI identity</option></select></label>
              {draft.auth_type === "bearer_env" && <label className="block space-y-1 text-sm">Host environment variable<Input value={draft.credential_env ?? ""} disabled={busy} onChange={event => setDraft({ ...draft, credential_env: event.target.value || null })} /></label>}
              <p className="text-xs text-muted-foreground">Disable the connection before changing its organization or authentication.</p>
              <section className="space-y-2"><h3 className="text-sm font-medium">Authorized apps</h3>{inventory.apps.map(app => <Choice key={app.id} label={app.name} checked={draft.app_ids.includes(app.id)} disabled={busy} onChange={selected => setDraft({ ...draft, app_ids: toggle(draft.app_ids, app.id, selected) })} />)}</section>
              <section className="space-y-3" aria-label="Boards and pipelines"><h3 className="text-sm font-medium">Boards and pipelines</h3>{draft.scopes.map(scope => <div key={scope.project} className="space-y-2">
                <p className="text-xs text-muted-foreground">{projects.find(project => String(project.id) === scope.project)?.name ?? scope.project}</p>
                <Choice label="Boards work items and comments" checked={scope.boards} disabled={busy} onChange={boards => updateScope(scope.project, { boards })} />
                <p className="text-xs text-muted-foreground">Selected pipelines: {scope.pipelines.length}</p>
                {draft.id && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void action(async () => { const found = await request(`${API}/${draft.id}/available?project=${encodeURIComponent(scope.project)}&resource=pipelines`); setResources(current => ({ ...current, [scope.project]: { repositories: current[scope.project]?.repositories ?? [], pipelines: found.pipelines } })); })}>Load pipelines</Button>}
                {resources[scope.project]?.pipelines.map(pipeline => <Choice key={pipeline.id} label={`Pipeline · ${pipeline.name}`} checked={scope.pipelines.includes(Number(pipeline.id))} disabled={busy} onChange={selected => updateScope(scope.project, { pipelines: toggle(scope.pipelines, Number(pipeline.id), selected) })} />)}
              </div>)}</section>
          <details className="space-y-2"><summary className="cursor-pointer text-sm font-medium">Write capabilities ({draft.write_tools.length})</summary><p className="text-sm text-muted-foreground">Disabled by default. Enable only necessary actions and configure Factory tool approvals. Merge, review votes, autocomplete, build cancellation and Git push can change external systems.</p><div className="grid gap-2 py-2 sm:grid-cols-2">{inventory.write_tools.map(name => <Choice key={name} label={name.replace(/^ado_/, "").replaceAll("_", " ")} checked={draft.write_tools.includes(name)} disabled={busy} onChange={selected => setDraft({ ...draft, write_tools: toggle(draft.write_tools, name, selected) })} />)}</div><label className="block space-y-1 text-sm">Allowed Git push branches (comma-separated)<Input value={draft.push_branches.join(", ")} disabled={busy} onChange={event => setDraft({ ...draft, push_branches: event.target.value.split(",").map(value => value.trim()).filter(Boolean) })} /></label></details>
          <label className="block space-y-1 text-sm">Poll interval (seconds)<Input type="number" min={15} max={3600} value={draft.interval_seconds} disabled={busy} onChange={event => setDraft({ ...draft, interval_seconds: Number(event.target.value) })} /></label>
              {draft.id && <Button type="button" variant="outline" disabled={busy} onClick={() => void action(async () => { const value = await request(`${API}/${draft.id}/preview`); setPreview(`Read-only preview: ${value.resources.reduce((total: number, resource: { pull_requests: unknown[] }) => total + resource.pull_requests.length, 0)} active pull requests. No dispatches created.`); })}>Preview</Button>}
                </div>}
          </details>
          <p className="text-sm text-muted-foreground">{draft.write_tools.length ? `${draft.write_tools.length} write capabilities enabled. Manage them in Advanced.` : "Tools are read-only. Enable write capabilities in Advanced only when needed."}</p>
          {inventory.apps.length !== 1 && !draft.app_ids.length && <p className="text-sm text-muted-foreground">Select an authorized app in Advanced before enabling this connection.</p>}
          <Choice label="Enable connection" checked={draft.enabled} disabled={busy} onChange={enabled => setDraft({ ...draft, enabled })} />
          <p className="text-xs text-muted-foreground">Enabling polling needs selected resources and an authorized Factory app. Neither is required to save credentials.</p>
          <p className="text-sm text-muted-foreground">The first successful poll establishes a baseline without starting automations. Later observed changes enter the durable queue.</p>
          <div className="flex flex-wrap justify-end gap-2">
            {draft.id && <Button type="button" variant="destructive" disabled={busy} onClick={() => { if (window.confirm("Delete this connection and revoke its access?")) void action(async () => { await request(`${API}/${draft.id}?revision=${draft.revision}`, "DELETE"); setDraft(null); setInventory(await request(API)); }); }}>Delete</Button>}
            <Button type="submit" disabled={busy}>{busy ? "Working…" : "Save connection"}</Button>
          </div>
        </form>}
      </div>}
    </DialogContent>
  </Dialog>;
}
