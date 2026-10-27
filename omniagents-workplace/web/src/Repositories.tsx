import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Input } from "@omniagents/ui/input";
import { Switch } from "@omniagents/ui/switch";
import { Checkbox } from "@omniagents/ui/checkbox";
import { toast } from "sonner";

type Provider = "standin" | "github";
type Settings = {
  provider: Provider; repositories: string[]; factory_repository: string | null;
  client_id: string; callback_url: string; webhook_url: string; webhook_configured: boolean;
  self_improvement: boolean; connected_as: string | null; oauth_configured: boolean;
};
type DeviceLogin = { id: string; status: "pending"; user_code: string | null; verification_uri: string };
type DeviceResult = DeviceLogin | { status: "connected"; settings: Settings };
type RepositoryPage = { repositories: Repository[]; page: number; has_more: boolean; limited: boolean; incomplete: boolean };
type Repository = { full_name: string; html_url: string };
export async function repositoryRequest<T>(path = "", method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/workplace/api/repositories${path}`, {
    method, signal, headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || "Unable to update repository settings");
  return value as T;
}

export function RepositoriesDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [available, setAvailable] = useState<Repository[]>([]);
  const [repositorySearch, setRepositorySearch] = useState("");
  const [repositoryPage, setRepositoryPage] = useState(1);
  const [repositoryResult, setRepositoryResult] = useState<RepositoryPage | null>(null);
  const [repositoriesLoading, setRepositoriesLoading] = useState(false);
  const [repositoryError, setRepositoryError] = useState("");
  const [device, setDevice] = useState<DeviceLogin | null>(null);
  const [token, setToken] = useState("");
  const [connectionRevision, setConnectionRevision] = useState(0);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setToken("");
    if (!open) return;
    setError("");
    setSettings(null);
    setClientSecret(""); setWebhookSecret("");
    repositoryRequest<Settings>().then(value => { setSettings(value); setClientId(value.client_id); }).catch(e => setError(e.message));
  }, [open]);
  useEffect(() => {
    setRepositorySearch(""); setRepositoryPage(1);
  }, [open, settings?.provider, settings?.connected_as, connectionRevision]);
  useEffect(() => {
    setAvailable([]); setRepositoryResult(null); setRepositoryError("");
    if (!open || !settings || (settings.provider === "github" && !settings.connected_as)) {
      setRepositoriesLoading(false); return;
    }
    const controller = new AbortController();
    setRepositoriesLoading(true);
    const load = () => {
      const params = new URLSearchParams({provider: settings.provider, page: String(repositoryPage), q: repositorySearch.trim()});
      void repositoryRequest<RepositoryPage>(`/available?${params}`, "GET", undefined, controller.signal)
        .then(value => { if (!controller.signal.aborted) { setAvailable(value.repositories); setRepositoryResult(value); } })
        .catch(e => { if (!controller.signal.aborted) setRepositoryError(e.message); })
        .finally(() => { if (!controller.signal.aborted) setRepositoriesLoading(false); });
    };
    const timer = repositorySearch.trim() ? setTimeout(load, 400) : null;
    if (!timer) load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [open, settings?.provider, settings?.connected_as, connectionRevision, repositorySearch, repositoryPage]);
  useEffect(() => {
    if (!device) return;
    const path = `/github/device/${device.id}`;
    const cancel = () => { void repositoryRequest(path, "DELETE").catch(() => {}); };
    if (!open || settings?.provider !== "github") { cancel(); setDevice(null); return; }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await repositoryRequest<DeviceResult>(path, "POST", {});
        if (disposed) return;
        if (result.status === "connected") {
          setSettings(result.settings); setDevice(null); setToken("");
          setConnectionRevision(v => v + 1);
          window.dispatchEvent(new Event("repository-settings-changed"));
          toast.success("GitHub connected");
        } else { setDevice(result); timer = setTimeout(poll, 2000); }
      } catch (e) { if (!disposed) { setError((e as Error).message); setDevice(null); } }
    };
    timer = setTimeout(poll, 1000);
    return () => { disposed = true; clearTimeout(timer); cancel(); };
  }, [device?.id, open, settings?.provider]);
  const act = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
      <DialogHeader><DialogTitle>Repositories</DialogTitle><DialogDescription>GitHub access and factory self-improvement.</DialogDescription></DialogHeader>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {settings && <FieldGroup>
        <Field><FieldLabel htmlFor="repository-provider">Provider</FieldLabel>
          <NativeSelect id="repository-provider" value={settings.provider} disabled={busy} onChange={e => {
            setError(""); setSettings({ ...settings, provider: e.target.value as Provider, repositories: [], factory_repository: null, self_improvement: false });
          }}><NativeSelectOption value="standin">GitHub stand-in</NativeSelectOption><NativeSelectOption value="github">GitHub</NativeSelectOption></NativeSelect>
        </Field>
        {settings.provider === "github" && <Field>
          {settings.connected_as && <FieldDescription>Connected as {settings.connected_as}</FieldDescription>}
          <div className="flex gap-2">
            <Button disabled={busy || !!device} onClick={() => void act(async () => {
              setDevice(await repositoryRequest<DeviceLogin>("/github/device", "POST", {}));
            })}>{settings.connected_as ? "Sign in again" : "Sign in with GitHub"}</Button>
            {settings.connected_as && <Button variant="outline" disabled={busy || !!device} onClick={() => void act(async () => {
              setToken(""); setSettings(await repositoryRequest<Settings>("/github/connection", "DELETE"));
            })}>Disconnect</Button>}
          </div>
          {device && <Field>
            {device.user_code ? <>
              <FieldLabel htmlFor="github-device-code">Enter this code on GitHub</FieldLabel>
              <Input id="github-device-code" readOnly value={device.user_code} />
              <Button asChild variant="outline"><a href={device.verification_uri} target="_blank" rel="noreferrer">Open GitHub</a></Button>
              <FieldDescription role="status">Waiting for GitHub authorization…</FieldDescription>
            </> : <FieldDescription role="status">Getting a sign-in code…</FieldDescription>}
            <Button variant="ghost" onClick={() => setDevice(null)}>Cancel sign-in</Button>
          </Field>}
        </Field>}
        {settings.provider === "github" && <details>
          <summary className="cursor-pointer text-sm font-medium">Use an access token instead</summary>
          <FieldGroup className="mt-4">
          <FieldLabel htmlFor="github-token">Personal access token</FieldLabel>
          <Input id="github-token" type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="new-password" disabled={busy || !!device} />
          <FieldDescription><a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noreferrer" className="underline underline-offset-4">Create a fine-grained token</a> for one account or organization. For access across organizations, use device sign-in above. Grant Contents, Issues and Pull requests read/write, and Actions and Checks read access. Webhooks are optional.</FieldDescription>
          <div className="flex gap-2">
            <Button variant="outline" disabled={busy || !!device || !token.trim()} onClick={() => void act(async () => {
              const value = token.trim(); setToken("");
              setSettings(await repositoryRequest<Settings>("/github/token", "PUT", { token: value }));
              setConnectionRevision(v => v + 1);
              window.dispatchEvent(new Event("repository-settings-changed"));
              toast.success("GitHub connected");
            })}>{settings.connected_as ? "Replace token" : "Connect GitHub"}</Button>
          </div>
          </FieldGroup>
        </details>}
        {settings.provider === "github" && <details>
          <summary className="cursor-pointer text-sm font-medium">Connect with a GitHub App instead</summary>
          {settings.oauth_configured && <Button variant="outline" className="mt-4" disabled={busy || !!device} onClick={() => void act(async () => {
            const v = await repositoryRequest<{url: string}>("/github/connect", "POST", {}); window.location.assign(v.url);
          })}>Authorize GitHub App</Button>}
          <FieldGroup className="mt-4">
            <FieldDescription><a href="https://github.com/settings/apps/new" target="_blank" rel="noreferrer" className="underline underline-offset-4">Create a GitHub App</a> with Contents, Issues and Pull requests write access, and Actions and Checks read access. Install it on the repositories you want to share.</FieldDescription>
            <Field><FieldLabel htmlFor="github-client-id">Client ID</FieldLabel><Input id="github-client-id" value={clientId} onChange={e => setClientId(e.target.value)} autoComplete="off" /></Field>
            <Field><FieldLabel htmlFor="github-client-secret">Client secret</FieldLabel><Input id="github-client-secret" type="password" value={clientSecret} onChange={e => setClientSecret(e.target.value)} placeholder={settings.oauth_configured ? "Leave blank to keep current secret" : ""} autoComplete="new-password" /></Field>
            <Field><FieldLabel htmlFor="github-webhook-secret">Webhook secret (optional)</FieldLabel><Input id="github-webhook-secret" type="password" value={webhookSecret} onChange={e => setWebhookSecret(e.target.value)} placeholder={settings.webhook_configured ? "Leave blank to keep current secret" : ""} autoComplete="new-password" /></Field>
            <Field><FieldLabel>Callback URL</FieldLabel><Input readOnly aria-label="Callback URL" value={settings.callback_url} /></Field>
            <Field><FieldLabel>Webhook URL</FieldLabel><Input readOnly aria-label="Webhook URL" value={settings.webhook_url} /></Field>
            <Button variant="outline" disabled={busy || !!device || !clientId || (!settings.oauth_configured && !clientSecret)} onClick={() => void act(async () => {
              const value = await repositoryRequest<Settings>("/github/app", "PUT", { client_id: clientId, client_secret: clientSecret || null, webhook_secret: webhookSecret || null });
              setSettings(current => current ? { ...current, client_id: value.client_id, oauth_configured: value.oauth_configured, webhook_configured: value.webhook_configured, connected_as: value.connected_as, self_improvement: false } : value);
              setClientSecret(""); setWebhookSecret(""); toast.success("GitHub App saved");
            })}>Save GitHub App</Button>
          </FieldGroup>
        </details>}
        <Field><FieldLabel>Workspace repositories</FieldLabel>
          <FieldDescription>Select repositories to check out in the agent workspace.</FieldDescription>
          <Input type="search" aria-label="Search repositories" placeholder="Search repositories…" value={repositorySearch} onChange={e => { setRepositorySearch(e.target.value); setRepositoryPage(1); }} />
          {repositoriesLoading && <FieldDescription role="status">Loading repositories…</FieldDescription>}
          {repositoryError && <FieldDescription role="alert">{repositoryError}</FieldDescription>}
          {!repositoriesLoading && !repositoryError && repositoryResult && !available.length && <FieldDescription role="status">No matching repositories.</FieldDescription>}
          {repositoryResult?.limited && <FieldDescription>GitHub returns up to 1,000 search results. Narrow your search to see more.</FieldDescription>}
          {repositoryResult?.incomplete && <FieldDescription>GitHub returned partial results. Narrow your search and try again.</FieldDescription>}
          <div className="flex max-h-52 flex-col gap-3 overflow-y-auto">
            {available.map(repo => <Field key={repo.full_name} orientation="horizontal">
              <Checkbox id={`repo-${repo.full_name}`} disabled={busy} checked={settings.repositories.includes(repo.full_name)} onCheckedChange={checked => {
                const repositories = checked ? [...settings.repositories, repo.full_name] : settings.repositories.filter(r => r !== repo.full_name);
                const unlinked = settings.factory_repository === repo.full_name && !checked;
                setSettings({ ...settings, repositories, ...(unlinked ? { factory_repository: null, self_improvement: false } : {}) });
              }} /><FieldLabel htmlFor={`repo-${repo.full_name}`}>{repo.full_name}</FieldLabel>
            </Field>)}
          </div>
          <div className="flex items-center justify-between gap-2">
            <Button variant="outline" disabled={repositoriesLoading || repositoryPage === 1} onClick={() => setRepositoryPage(p => p - 1)}>Previous</Button>
            <span className="text-sm text-muted-foreground">Page {repositoryPage}</span>
            <Button variant="outline" disabled={repositoriesLoading || !repositoryResult?.has_more} onClick={() => setRepositoryPage(p => p + 1)}>Next</Button>
          </div>
        </Field>
        <Field><FieldLabel htmlFor="factory-repository">Factory repository</FieldLabel>
          <NativeSelect id="factory-repository" value={settings.factory_repository || ""} disabled={busy} onChange={e => setSettings({ ...settings, factory_repository: e.target.value || null, self_improvement: false })}>
            <NativeSelectOption value="">None</NativeSelectOption>
            {settings.repositories.map(name => <NativeSelectOption key={name} value={name}>{name}</NativeSelectOption>)}
          </NativeSelect>
        </Field>
        <Field orientation="horizontal"><Switch id="self-improvement" checked={settings.self_improvement} disabled={busy || !settings.factory_repository || (settings.provider === "github" && !settings.connected_as)} onCheckedChange={checked => setSettings({ ...settings, self_improvement: checked })} />
          <FieldLabel htmlFor="self-improvement">Self-improvement</FieldLabel>
        </Field>
        <Button disabled={busy || !!device || (settings.provider === "github" && !settings.connected_as)} onClick={() => void act(async () => {
          const { provider, repositories, factory_repository, self_improvement } = settings;
          setSettings(await repositoryRequest<Settings>("", "PUT", { provider, repositories, factory_repository, self_improvement }));
          window.dispatchEvent(new Event("repository-settings-changed"));
          toast.success("Repository settings saved");
          onOpenChange(false);
        })}>{busy ? "Saving…" : "Save"}</Button>
      </FieldGroup>}
    </DialogContent>
  </Dialog>;
}

export function RepositoryView({ standin, admin, onSettings }: { standin: React.ReactNode; admin: boolean; onSettings: () => void }) {
  const [value, setValue] = useState<{ provider: Provider; repositories: string[] } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const load = () => { repositoryRequest<{provider: Provider; repositories: string[]}>("/view").then(setValue).catch(e => setError(e.message)); };
    load(); window.addEventListener("repository-settings-changed", load);
    return () => window.removeEventListener("repository-settings-changed", load);
  }, []);
  if (error) return <p role="alert" className="p-6">{error}</p>;
  if (!value) return null;
  if (value.provider === "standin") return <>{standin}</>;
  return <section className="flex flex-1 flex-col gap-4 p-6">
    <div className="flex items-center justify-between"><h1 className="text-lg font-semibold">Repositories</h1>{admin && <Button variant="outline" onClick={onSettings}>Settings</Button>}</div>
    {!admin ? <p>Repository access requires an administrator account.</p> : value.repositories.length ? value.repositories.map(name => <a key={name} className="underline underline-offset-4" href={`https://github.com/${name}`} target="_blank" rel="noreferrer">{name}</a>) : <p className="text-sm text-muted-foreground">No repositories selected.</p>}
  </section>;
}
