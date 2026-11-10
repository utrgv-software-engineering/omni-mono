import { useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import { Input } from "@omniagents/ui/input";
import { factoryFetch } from "./factory-scope";

type Source = { source: string; identity: string; marker: string };
type Settings = {
  revision: number;
  display_name: string;
  sources: Source[];
  applied_revision: number | null;
  status: "applied" | "pending";
  error: string | null;
};

async function request(method = "GET", body?: unknown): Promise<Settings> {
  const response = await factoryFetch("/workplace/api/factory/settings", {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Unable to save Factory settings");
  return value;
}

export function FactorySettingsDialog({ open, onOpenChange, onSaved }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: () => void;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [name, setName] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setSettings(null);
    setError("");
    void request().then(value => {
      if (!active) return;
      setSettings(value);
      setName(value.display_name);
      setSources(value.sources);
    }).catch(cause => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [open, reload]);

  useEffect(() => {
    if (!open || settings?.status !== "pending") return;
    let active = true;
    const timer = window.setInterval(() => {
      void request().then(value => {
        if (active && value.revision === settings.revision) setSettings(value);
      }).catch(() => {});
    }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [open, settings?.status, settings?.revision]);

  const valid = name.trim().length > 0 && name.length <= 120 && !/[\u0000-\u001f]/.test(name)
    && sources.every(source => /^@[A-Za-z0-9_-]{1,64}$/.test(source.marker));
  const dirty = settings && (name !== settings.display_name || JSON.stringify(sources) !== JSON.stringify(settings.sources));
  const save = async () => {
    if (!settings || !valid || busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await request("PUT", { revision: settings.revision, display_name: name.trim(), sources });
      setSettings(value);
      setName(value.display_name);
      setSources(value.sources);
      onSaved?.();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Factory settings</DialogTitle>
        <DialogDescription>Change how your Factory is named and called into a Teams thread.</DialogDescription>
      </DialogHeader>
      {error && <div role="alert" className="space-y-2 text-sm text-destructive">
        <p>{error}</p>
        <Button variant="outline" disabled={busy} onClick={() => setReload(value => value + 1)}>Reload settings</Button>
      </div>}
      {!settings && !error && <p role="status" className="text-sm text-muted-foreground">Loading settings…</p>}
      {settings && <form onSubmit={event => { event.preventDefault(); void save(); }} className="space-y-6">
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="factory-display-name">Display name</FieldLabel>
            <Input id="factory-display-name" value={name} maxLength={120} required disabled={busy} onChange={event => setName(event.target.value)} />
            <FieldDescription>This changes the visible name, not the Factory’s identity or existing conversations.</FieldDescription>
          </Field>
          <section className="space-y-4 border-t pt-4" aria-label="Event sources">
            <h3 className="text-sm font-medium">Event sources</h3>
            {sources.length === 0 && <p className="text-sm text-muted-foreground">No Teams activation sources are configured for this Factory.</p>}
            {sources.map((source, index) => <Field key={source.source}>
              <FieldLabel htmlFor={`factory-marker-${index}`}>Teams activation marker · {source.source}</FieldLabel>
              <Input id={`factory-marker-${index}`} value={source.marker} maxLength={65} required pattern="@[A-Za-z0-9_\-]{1,64}" disabled={busy} spellCheck={false} autoCapitalize="none" onChange={event => setSources(current => current.map((item, position) => position === index ? { ...item, marker: event.target.value } : item))} />
              <FieldDescription>Use @ followed by letters, numbers, underscores or hyphens. This is a text marker, not a Teams account mention.</FieldDescription>
              <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm" aria-label="Example Teams message">
                <span className="font-mono font-medium">{source.marker || "@your-factory"}</span> help with this thread
              </div>
            </Field>)}
            {sources.length > 0 && <p className="text-sm text-muted-foreground">New threads use the saved marker. Already active threads continue normally. No Power Automate changes are needed.</p>}
          </section>
        </FieldGroup>
        <div className="space-y-3 border-t pt-4">
          <p role="status" className="text-sm text-muted-foreground">{settings.status === "applied" ? "Applied to Factory" : "Saved · waiting for Factory to apply"}</p>
          {settings.error && <p className="text-sm text-muted-foreground">{settings.error} We’ll retry automatically.</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={() => onOpenChange(false)}>Close</Button>
            <Button type="submit" disabled={busy || !valid || !dirty}>{busy ? "Saving…" : "Save changes"}</Button>
          </div>
        </div>
      </form>}
    </DialogContent>
  </Dialog>;
}
