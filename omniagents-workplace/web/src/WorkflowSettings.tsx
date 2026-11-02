import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { Textarea } from "@omniagents/ui/textarea";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { toast } from "sonner";
import { factoryFetch } from "./factory-scope";

type Settings = { variables: Record<string, string>; secret_names: string[]; trusted_workflows: Record<string, string>; branch: string | null };

export function WorkflowSettings({ repository }: { repository: string }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [kind, setKind] = useState("secrets");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [path, setPath] = useState(".github/workflows/ci.yml");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function request(body?: unknown) {
    const response = await factoryFetch(`/workplace/api/github/repos/${repository}/workflow-settings`, {
      method: body === undefined ? "GET" : "PUT", headers: { "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Unable to update workflow settings");
    return result as Settings;
  }
  useEffect(() => { setSettings(null); setValue(""); setError(""); let active = true; request().then(result => { if (active) setSettings(result); }).catch(e => { if (active) setError(e.message); }); return () => { active = false; }; }, [repository]);
  async function save(body: unknown) {
    setBusy(true); setError("");
    try { setSettings(await request(body)); setValue(""); toast.success("Workflow settings saved"); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="flex flex-col gap-4">
    <h2 className="border-b pb-2 text-lg">Workflow secrets and variables</h2>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {settings && <>
      <div className="flex flex-col gap-2">
        {settings.secret_names.map(key => <div key={key} className="flex items-center gap-2 text-sm"><code>{key}</code><span className="text-muted-foreground">Secret</span><Button variant="ghost" size="sm" disabled={busy} onClick={() => void save({ secrets: { [key]: null } })}>Remove</Button></div>)}
        {Object.entries(settings.variables).map(([key, val]) => <div key={key} className="flex flex-wrap items-center gap-2 text-sm"><code>{key}</code><span>{val}</span><Button variant="ghost" size="sm" disabled={busy} onClick={() => void save({ variables: { [key]: null } })}>Remove</Button></div>)}
      </div>
      <FieldGroup>
        <Field><FieldLabel htmlFor="workflow-kind">Type</FieldLabel><NativeSelect id="workflow-kind" value={kind} onChange={e => setKind(e.target.value)}><NativeSelectOption value="secrets">Secret</NativeSelectOption><NativeSelectOption value="variables">Variable</NativeSelectOption></NativeSelect></Field>
        <Field><FieldLabel htmlFor="workflow-name">Name</FieldLabel><Input id="workflow-name" value={name} onChange={e => setName(e.target.value.toUpperCase())} placeholder="DEPLOY_KEY" autoComplete="off" /></Field>
        <Field><FieldLabel htmlFor="workflow-value">Value</FieldLabel><Textarea id="workflow-value" value={value} onChange={e => setValue(e.target.value)} autoComplete="off" spellCheck={false} /><FieldDescription>Saved secret values are never displayed.</FieldDescription></Field>
      </FieldGroup>
      <Button className="self-start" disabled={busy || !name || !value} onClick={() => void save({ [kind]: { [name]: value } })}>Save</Button>
      <FieldGroup><Field><FieldLabel htmlFor="workflow-approval">Approved workflows</FieldLabel><FieldDescription>Secrets are available only to approved workflow versions pushed to the protected default branch. Pull requests receive no secrets. Workflow edits require approval again.</FieldDescription>
        {Object.entries(settings.trusted_workflows).map(([file, digest]) => <div key={file} className="flex flex-wrap items-center gap-2 text-sm"><code>{file}</code><code title={digest}>{digest.slice(0, 12)}</code><Button variant="ghost" size="sm" disabled={busy} onClick={() => void save({ revoke_workflows: [file] })}>Revoke</Button></div>)}
        <Input id="workflow-approval" value={path} onChange={e => setPath(e.target.value)} />
      </Field></FieldGroup>
      <Button variant="outline" className="self-start" disabled={busy || !path} onClick={() => void save({ approve_workflows: [path] })}>Approve current version</Button>
    </>}
  </section>;
}
