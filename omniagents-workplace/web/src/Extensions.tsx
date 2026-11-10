import { useState } from "react";
import { Plug, Plus } from "lucide-react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { Switch } from "@omniagents/ui/switch";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "@omniagents/ui/field";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@omniagents/ui/dialog";
import { extensionRequest, type AppCatalog, type InstalledApp } from "./McpAppFrame";

export function ExtensionsDialog({ admin, open, onOpenChange, catalog, refresh }: { admin: boolean; open: boolean; onOpenChange: (open: boolean) => void; catalog: AppCatalog | null; refresh: () => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const update = async (app: InstalledApp, change: Partial<InstalledApp>) => {
    setBusy(true); setError("");
    try { await extensionRequest(`/${app.id}`, { enabled: app.enabled, permissions: app.permissions, ...change }, "PUT"); await refresh(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const navigation = async (app: InstalledApp, show: boolean) => {
    setBusy(true); setError("");
    try {await extensionRequest(`/${app.id}/navigation`, {show_in_navigation: show}, "PUT"); await refresh();}
    catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  };
  const install = async () => {
    setBusy(true); setError("");
    try { await extensionRequest("", {name,url,token}); setName("");setUrl("");setToken("");setAdding(false); await refresh(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl">
    <DialogHeader><DialogTitle>Apps</DialogTitle><DialogDescription>Apps installed in this factory.</DialogDescription></DialogHeader>
    <div className="flex flex-col gap-5">
      {catalog?.apps.map(app => <div key={app.id} className="flex flex-col gap-3 border-b pb-4">
        <Field orientation="horizontal"><Plug className="size-4 text-muted-foreground" /><span className="flex-1 font-medium">{app.name}</span>{admin && app.kind !== "native" && <><FieldLabel htmlFor={`app-${app.id}`}>Enabled</FieldLabel><Switch id={`app-${app.id}`} disabled={busy} checked={app.enabled} onCheckedChange={enabled=>void update(app,{enabled})} /></>}</Field>
        <Field orientation="horizontal"><FieldLabel htmlFor={`nav-${app.id}`} className="flex-1">Show in navigation</FieldLabel><Switch id={`nav-${app.id}`} disabled={busy} checked={app.showInNavigation !== false} onCheckedChange={show => void navigation(app, show)} /></Field>
        {app.kind === "remote" && <div className="text-xs text-muted-foreground">{app.url}</div>}
        {admin && (app.id === "recorder" || app.kind === "remote") && app.enabled && <div className="flex flex-col gap-3 pl-7">
          {(["microphone", ...(app.kind === "remote" ? ["camera", "clipboardWrite", "geolocation"] : [])]).map(permission => <Field key={permission} orientation="horizontal"><FieldLabel htmlFor={`${app.id}-${permission}`} className="flex-1">{{ microphone: "Microphone", camera: "Camera", clipboardWrite: "Copy to clipboard", geolocation: "Location" }[permission]}</FieldLabel><Switch id={`${app.id}-${permission}`} checked={!!app.permissions[permission]} disabled={busy} onCheckedChange={enabled=>void update(app,{permissions:{...app.permissions,[permission]:enabled}})} /></Field>)}
        </div>}
        {admin && app.kind === "remote" && <Button variant="ghost" size="sm" className="self-start" disabled={busy} onClick={()=>{ void extensionRequest(`/${app.id}`,{},"DELETE").then(refresh).catch(e=>setError(e.message)); }}>Remove app</Button>}
      </div>)}
      {admin && (adding ? <form onSubmit={event=>{event.preventDefault();void install();}}><FieldGroup>
        <Field><FieldLabel htmlFor="app-name">Name</FieldLabel><Input id="app-name" value={name} onChange={e=>setName(e.target.value)} required /></Field>
        <Field><FieldLabel htmlFor="app-url">MCP endpoint</FieldLabel><Input id="app-url" type="url" placeholder="https://example.com/mcp" value={url} onChange={e=>setUrl(e.target.value)} required /><FieldDescription>The server must expose a global MCP App entrypoint.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="app-token">Access token</FieldLabel><Input id="app-token" type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} /><FieldDescription>Optional. Stored on the server, never passed to the app iframe.</FieldDescription></Field>
        <div className="flex gap-2"><Button disabled={busy} type="submit">{busy ? "Connecting…" : "Install app"}</Button><Button variant="ghost" type="button" onClick={()=>{setAdding(false);setToken("");}}>Cancel</Button></div>
      </FieldGroup></form> : <Button variant="outline" className="self-start" onClick={()=>setAdding(true)}><Plus data-icon="inline-start" />Install app</Button>)}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  </DialogContent></Dialog>;
}
