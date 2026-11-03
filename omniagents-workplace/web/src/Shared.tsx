import { useCallback, useEffect, useRef, useState } from "react";
import { Folder, File, Upload, FolderPlus, Download, MoreHorizontal, MessageSquare, Settings2, ChevronRight, ArrowLeft, Search, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { Textarea } from "@omniagents/ui/textarea";
import { Field, FieldGroup, FieldLabel, FieldDescription } from "@omniagents/ui/field";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@omniagents/ui/dialog";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem } from "@omniagents/ui/dropdown-menu";
import { Checkbox } from "@omniagents/ui/checkbox";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { factoryFetch, factoryUrl } from "./factory-scope";
import type { Person } from "./api";

type Entry = { path: string; kind: "file" | "folder"; size: number; modified: number; revision: string };
type Workflow = { agent?: string | null; conversation?: string; id?: string; name: string; folder: string; instruction: string; patterns: string[]; events: string[]; recursive: boolean; include_agent: boolean; enabled: boolean; revision?: number };
type Change = { id: string; event: string; at: number; origin: string; actor: { name: string }; entries: Entry[] };
const labels: Record<string, string> = { "shared.files_uploaded": "Files added", "shared.file_updated": "File updated", "shared.entry_moved": "File or folder moved", "shared.entry_deleted": "File or folder deleted" };
const error = (e: unknown) => toast.error(e instanceof Error ? e.message : "Request failed");
const name = (path: string) => path.split("/").pop() || "Shared";
const join = (a: string, b: string) => a ? a + "/" + b : b;
const size = (n: number) => n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
async function api<T = any>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await factoryFetch("/workplace/api/shared" + path, { method, headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || "Request failed");
  return data;
}
const fileUrl = (entry: Entry, preview = false) => factoryUrl(`/workplace/api/shared/${entry.kind === "folder" ? "download-folder" : "content"}?path=${encodeURIComponent(entry.path)}${preview ? "&preview=true" : ""}`);

export function SharedApp({ account, onUse }: { account: Person; onUse: (reference: string) => void }) {
  const [folder, setFolder] = useState(() => new URLSearchParams(location.search).get("folder") || "");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState("");
  const [dialog, setDialog] = useState<{ action: "mkdir" | "rename" | "move" | "delete"; entry?: Entry }>();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Entry>();
  const [workflowsOpen, setWorkflowsOpen] = useState(false);
  const [upload, setUpload] = useState<{ done: number; total: number; file: string }>();
  const [replace, setReplace] = useState<{ files: { file: File; path: string }[]; folders: string[] }>();
  const input = useRef<HTMLInputElement>(null), directory = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | undefined>(undefined);
  const current = useRef(folder); current.current = folder;
  const load = useCallback(async () => {
    const result = await api<{ entries: Entry[]; error?: string }>("/files?path=" + encodeURIComponent(folder));
    if (current.current !== folder) return;
    setEntries(result.entries); setProblem(result.error || ""); setLoading(false);
  }, [folder]);
  useEffect(() => {
    setLoading(true);
    void load().catch(e => { setProblem(e.message); setLoading(false); });
    const timer = setInterval(() => void load().catch(e => setProblem(e.message)), 3000);
    return () => clearInterval(timer);
  }, [load]);
  const browse = (path: string) => {
    setFolder(path); setSearch("");
    const url = new URL(location.href); url.searchParams.set("folder", path); history.replaceState(null, "", url);
  };
  async function uploadFiles(files: { file: File; path: string }[], overwrite = false, folders: string[] = []) {
    if ((!files.length && !folders.length) || abort.current) return;
    const controller = new AbortController(); abort.current = controller;
    let id: string | undefined;
    let done = 0; const total = files.reduce((n, f) => n + f.file.size, 0);
    setUpload({ done, total, file: files[0]?.path || folders[0] });
    try {
      const started = await api<{ id: string }>("/uploads", "POST", { files: files.map(f => ({ path: f.path, size: f.file.size })), replace: overwrite, folders });
      id = started.id;
      for (let i = 0; i < files.length; i++) {
        const { file, path } = files[i];
        let offset = 0;
        do {
          if (controller.signal.aborted) throw new DOMException("Upload cancelled", "AbortError");
          const chunk = file.slice(offset, offset + 8 * 1024 * 1024);
          const response = await factoryFetch(`/workplace/api/shared/uploads/${id}/${i}?offset=${offset}`, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: chunk, signal: controller.signal });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Upload failed");
          offset = result.offset; done += chunk.size; setUpload({ done, total, file: path });
        } while (offset < file.size);
      }
      await api(`/uploads/${id}/commit`, "POST", {});
      toast.success(files.length ? `${files.length} ${files.length === 1 ? "file uploaded" : "files uploaded"}` : "Folders uploaded");
      await load();
    } catch (e) {
      if (id) await api(`/uploads/${id}`, "DELETE").catch(() => undefined);
      if (e instanceof Error && e.message.endsWith("already exists") && !overwrite) setReplace({files,folders});
      else if (!(e instanceof DOMException && e.name === "AbortError")) error(e);
    } finally { abort.current = undefined; setUpload(undefined); }
  }
  const picked = (files: FileList | null) => files && void uploadFiles(Array.from(files).map(file => ({ file, path: join(folder, file.webkitRelativePath || file.name) })));
  async function dropped(event: React.DragEvent) {
    event.preventDefault();
    const collected: { file: File; path: string }[] = [];
    const folders: string[] = [];
    async function walk(entry: any, base: string): Promise<void> {
      if (entry.isFile) {
        const file = await new Promise<File>((resolve, reject) => entry.file(resolve, reject));
        collected.push({ file, path: join(base, file.name) });
      } else if (entry.isDirectory) {
        const child = join(base, entry.name), reader = entry.createReader();
        folders.push(child);
        while (true) {
          const children = await new Promise<any[]>((resolve, reject) => reader.readEntries(resolve, reject));
          if (!children.length) break;
          for (const item of children) await walk(item, child);
        }
      }
    }
    const items = Array.from(event.dataTransfer.items).map(i => i.webkitGetAsEntry?.());
    try {
      if (items.some(Boolean)) { for (const item of items) if (item) await walk(item, folder); }
      else for (const file of Array.from(event.dataTransfer.files)) collected.push({ file, path: join(folder, file.name) });
      await uploadFiles(collected, false, folders);
    } catch (e) { error(e); }
  }
  return <main className="min-h-0 flex-1 overflow-auto" onDragOver={e => e.preventDefault()} onDrop={e => void dropped(e)}>
    <div className="workplace-page flex flex-col gap-4">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <nav aria-label="Folder path" className="flex min-w-0 flex-wrap items-center gap-1">
        <Button variant="ghost" onClick={() => browse("")}>Shared</Button>
        {folder.split("/").filter(Boolean).map((part, i, all) => <span key={i} className="flex items-center"><ChevronRight className="size-4" /><Button variant="ghost" onClick={() => browse(all.slice(0, i + 1).join("/"))}>{part}</Button></span>)}
      </nav>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => { setValue(""); setDialog({ action: "mkdir" }); }}><FolderPlus />New folder</Button>
        <DropdownMenu><DropdownMenuTrigger asChild><Button disabled={!!upload}><Upload />Upload</Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuGroup>
          <DropdownMenuItem onSelect={() => input.current?.click()}>Files</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => directory.current?.click()}>Folder</DropdownMenuItem>
        </DropdownMenuGroup></DropdownMenuContent></DropdownMenu>
        <Button variant="outline" onClick={() => setWorkflowsOpen(true)}><Settings2 />Workflows</Button>
      </div>
      <input ref={input} type="file" multiple className="hidden" aria-label="Upload files" onChange={e => { picked(e.target.files); e.target.value = ""; }} />
      <input ref={directory} type="file" multiple {...({ webkitdirectory: "" } as any)} className="hidden" aria-label="Upload folder" onChange={e => { picked(e.target.files); e.target.value = ""; }} />
    </header>
    <div className="flex items-center gap-2">
      {folder && <Button variant="ghost" size="icon" aria-label="Parent folder" onClick={() => browse(folder.split("/").slice(0, -1).join("/"))}><ArrowLeft /></Button>}
      <Search className="size-4 text-muted-foreground" /><Input className="max-w-sm" aria-label="Search files" placeholder="Search this folder…" value={search} onChange={e => setSearch(e.target.value)} />
      <Button variant="ghost" size="icon" aria-label="Download folder" asChild><a href={factoryUrl('/workplace/api/shared/download-folder?path=' + encodeURIComponent(folder))}><Download /></a></Button>
    </div>
    {problem && <p role="alert">{problem}<Button variant="link" onClick={() => void load().catch(error)}>Retry</Button></p>}
    {upload && <div role="status" className="flex items-center gap-3"><progress className="min-w-0 flex-1" max={upload.total || 1} value={upload.done} /><span className="truncate text-sm">{upload.file} · {size(upload.done)} / {size(upload.total)}</span><Button variant="ghost" size="icon" aria-label="Cancel upload" onClick={() => abort.current?.abort()}><X /></Button></div>}
    <div className="overflow-x-auto rounded-md border"><table className="w-full text-sm"><thead><tr className="border-b text-left"><th className="p-3">Name</th><th className="p-3 max-sm:hidden">Size</th><th className="p-3 max-md:hidden">Modified</th><th className="p-3"><span className="sr-only">Actions</span></th></tr></thead><tbody>
      {entries.filter(e => name(e.path).toLowerCase().includes(search.toLowerCase())).sort((a,b) => (a.kind === b.kind ? name(a.path).localeCompare(name(b.path)) : a.kind === "folder" ? -1 : 1)).map(entry => <tr key={entry.path} className="border-b last:border-0">
        <td className="p-2"><Button variant="ghost" className="max-w-full justify-start" onClick={() => entry.kind === "folder" ? browse(entry.path) : setPreview(entry)}>{entry.kind === "folder" ? <Folder /> : <File />}<span className="truncate">{name(entry.path)}</span></Button></td>
        <td className="p-3 max-sm:hidden">{entry.kind === "file" ? size(entry.size) : "—"}</td><td className="p-3 max-md:hidden">{new Date(entry.modified * 1000).toLocaleString()}</td>
        <td className="p-2"><DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="icon" aria-label={`Actions for ${name(entry.path)}`}><MoreHorizontal /></Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuGroup>
          <DropdownMenuItem onSelect={() => onUse(`Shared ${entry.kind}: /workspace/Shared/${entry.path}${entry.kind === "file" ? `\nSHA-256: ${entry.revision}` : ""}`)}><MessageSquare />Use in chat</DropdownMenuItem>
          <DropdownMenuItem asChild><a href={fileUrl(entry)}><Download />Download{entry.kind === "folder" ? " ZIP" : ""}</a></DropdownMenuItem>
          <DropdownMenuItem onSelect={() => { setValue(name(entry.path)); setDialog({ action: "rename", entry }); }}>Rename</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => { setValue(entry.path); setDialog({ action: "move", entry }); }}>Move</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setDialog({ action: "delete", entry })}>Delete</DropdownMenuItem>
        </DropdownMenuGroup></DropdownMenuContent></DropdownMenu></td>
      </tr>)}
      {!entries.length && <tr><td colSpan={4} className="p-8 text-center text-muted-foreground">{loading ? "Loading…" : "Drop files or folders here."}</td></tr>}
    </tbody></table></div>
    <Dialog open={!!dialog} onOpenChange={open => !open && !busy && setDialog(undefined)}><DialogContent><DialogHeader><DialogTitle>{dialog?.action === "mkdir" ? "New folder" : dialog?.action === "delete" ? `Delete ${name(dialog.entry!.path)}?` : dialog?.action === "rename" ? "Rename" : "Move"}</DialogTitle><DialogDescription>{dialog?.action === "delete" ? "This removes it from Shared and the agent’s sandbox, including any files inside." : dialog?.action === "move" ? "Enter the destination path relative to Shared, including the file or folder name." : ""}</DialogDescription></DialogHeader>
      <form className="flex flex-col gap-4" onSubmit={async e => {
        e.preventDefault(); if (!dialog) return; setBusy(true);
        try {
          const action = dialog.action === "rename" ? "move" : dialog.action;
          await api('/files', 'POST', { action, path: action === "mkdir" ? join(folder, value) : dialog.entry!.path, destination: dialog.action === "rename" ? join(dialog.entry!.path.split('/').slice(0,-1).join('/'), value) : value });
          setDialog(undefined); await load();
        } catch (e) { error(e); } finally { setBusy(false); }
      }}>
        {dialog?.action !== "delete" && <FieldGroup><Field><FieldLabel htmlFor="shared-path">{dialog?.action === "move" ? "Destination" : "Name"}</FieldLabel><Input id="shared-path" autoFocus value={value} onChange={e => setValue(e.target.value)} required /></Field></FieldGroup>}
        <DialogFooter><Button variant="outline" type="button" disabled={busy} onClick={() => setDialog(undefined)}>Cancel</Button><Button type="submit" disabled={busy || (dialog?.action !== "delete" && !value.trim())} variant={dialog?.action === "delete" ? "destructive" : "default"}>{dialog?.action === "delete" ? "Delete" : "Save"}</Button></DialogFooter>
      </form>
    </DialogContent></Dialog>
    <Dialog open={!!replace} onOpenChange={open => !open && setReplace(undefined)}><DialogContent><DialogHeader><DialogTitle>Replace existing files?</DialogTitle><DialogDescription>Matching files will be replaced after the entire upload finishes.</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setReplace(undefined)}>Cancel</Button><Button onClick={() => { const files = replace!; setReplace(undefined); void uploadFiles(files.files, true, files.folders); }}>Replace</Button></DialogFooter></DialogContent></Dialog>
    <Dialog open={!!preview} onOpenChange={open => !open && setPreview(undefined)}><DialogContent className="flex h-[85dvh] max-w-[95vw] flex-col sm:max-w-5xl"><DialogHeader><DialogTitle>{preview && name(preview.path)}</DialogTitle></DialogHeader>{preview && <FilePreview entry={preview} />}<DialogFooter>{preview && <><Button variant="outline" onClick={() => onUse(`Shared file: /workspace/Shared/${preview.path}\nSHA-256: ${preview.revision}`)}><MessageSquare />Use in chat</Button><Button asChild><a href={fileUrl(preview)}><Download />Download</a></Button></>}</DialogFooter></DialogContent></Dialog>
    <SharedWorkflows open={workflowsOpen} onClose={() => setWorkflowsOpen(false)} folder={folder} admin={account.admin} />
    </div>
  </main>;
}

function FilePreview({ entry }: { entry: Entry }) {
  const [text, setText] = useState("");
  const ext = name(entry.path).split('.').pop()?.toLowerCase() || '';
  const image = ['png','jpg','jpeg','gif','webp','avif'].includes(ext);
  const textFile = ['txt','md','csv','json','yaml','yml','xml','html','css','js','ts','py','rb','log','svg','toml','sh'].includes(ext);
  useEffect(() => {
    if (!textFile || entry.size > 2 * 1024 * 1024) return;
    const controller = new AbortController();
    void fetch(fileUrl(entry, true), { signal: controller.signal }).then(r => { if (!r.ok) throw Error('Unable to preview'); return r.text(); }).then(setText).catch(e => { if (e.name !== 'AbortError') error(e); });
    return () => controller.abort();
  }, [entry.path, entry.revision, textFile, entry.size]);
  if (image) return <img className="min-h-0 flex-1 object-contain" alt={name(entry.path)} src={fileUrl(entry, true)} />;
  if (ext === 'pdf') return <iframe className="min-h-0 flex-1" title={name(entry.path)} src={fileUrl(entry, true)} />;
  if (['mp4','webm'].includes(ext)) return <video controls className="min-h-0 flex-1" src={fileUrl(entry, true)} />;
  if (['mp3','wav'].includes(ext)) return <audio controls src={fileUrl(entry, true)} />;
  if (textFile && entry.size <= 2 * 1024 * 1024) return <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-md border p-4 text-sm">{text}</pre>;
  return <p className="flex-1 p-4 text-muted-foreground">Download this file to open it.</p>;
}

function SharedWorkflows({ open, onClose, folder, admin }: { open: boolean; onClose: () => void; folder: string; admin: boolean }) {
  const [agents, setAgents] = useState<string[]>([]);
  useEffect(() => { if (open && admin) void factoryFetch("/workplace/api/factory/automation-routes").then(r => r.json()).then(r => setAgents(r.agents || [])).catch(error); }, [open, admin]);
  const [rules, setRules] = useState<Workflow[]>([]), [edit, setEdit] = useState<Workflow>();
  const [events, setEvents] = useState<Change[]>([]), [deliveries, setDeliveries] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { const [r,e] = await Promise.all([api('/workflows'),api('/events')]); setRules(r.workflows); setEvents(e.events); setDeliveries(e.deliveries); }, []);
  useEffect(() => { if (!open) return; void load().catch(error); const timer=setInterval(() => void load().catch(error),5000);return()=>clearInterval(timer); },[open,load]);
  return <Dialog open={open} onOpenChange={value => { if (!value) { setEdit(undefined); onClose(); } }}><DialogContent className="max-h-[90dvh] overflow-auto sm:max-w-2xl"><DialogHeader><DialogTitle>Shared workflows</DialogTitle><DialogDescription>Only enabled rules start agent work. Agent changes are excluded unless you enable them.</DialogDescription></DialogHeader>
    {edit ? <form className="flex flex-col gap-4" onSubmit={async e => { e.preventDefault(); setBusy(true);try { const {id,revision,...body}=edit;await api('/workflows'+(id?'/'+id:''),id?'PUT':'POST',body);setEdit(undefined);await load(); } catch(e){error(e);}finally{setBusy(false);} }}>
      <FieldGroup>
        <Field><FieldLabel htmlFor="workflow-agent">Agent</FieldLabel><NativeSelect id="workflow-agent" value={edit.agent || ""} onChange={e=>setEdit({...edit,agent:e.target.value || null})}><NativeSelectOption value="">Factory default</NativeSelectOption>{agents.map(a=><NativeSelectOption key={a} value={a}>{a}</NativeSelectOption>)}</NativeSelect></Field>
        <Field><FieldLabel htmlFor="workflow-conversation">Conversation</FieldLabel><NativeSelect id="workflow-conversation" value={edit.conversation || "auto"} onChange={e=>setEdit({...edit,conversation:e.target.value})}><NativeSelectOption value="auto">New conversation for each file event</NativeSelectOption><NativeSelectOption value="thread">Continue this workflow's conversation</NativeSelectOption><NativeSelectOption value="new">Always start a new conversation</NativeSelectOption></NativeSelect></Field>
        <Field><FieldLabel htmlFor="workflow-name">Name</FieldLabel><Input id="workflow-name" value={edit.name} required onChange={e=>setEdit({...edit,name:e.target.value})}/></Field>
        <Field><FieldLabel htmlFor="workflow-folder">Folder</FieldLabel><Input id="workflow-folder" value={edit.folder} placeholder="All Shared files" onChange={e=>setEdit({...edit,folder:e.target.value})}/></Field>
        <Field><FieldLabel htmlFor="workflow-patterns">File patterns</FieldLabel><Input id="workflow-patterns" value={edit.patterns.join(', ')} onChange={e=>setEdit({...edit,patterns:e.target.value.split(',').map(s=>s.trim())})}/><FieldDescription>Comma-separated patterns, such as *.pdf, *.csv.</FieldDescription></Field>
        <Field><FieldLabel htmlFor="workflow-event">When</FieldLabel><NativeSelect id="workflow-event" value={edit.events.length===4?'all':edit.events[0]} onChange={e=>setEdit({...edit,events:e.target.value==='all'?Object.keys(labels):[e.target.value]})}><NativeSelectOption value="all">Any file change</NativeSelectOption>{Object.entries(labels).map(([key,value])=><NativeSelectOption key={key} value={key}>{value}</NativeSelectOption>)}</NativeSelect></Field>
        <Field><FieldLabel htmlFor="workflow-instruction">Instructions</FieldLabel><Textarea id="workflow-instruction" required rows={5} value={edit.instruction} onChange={e=>setEdit({...edit,instruction:e.target.value})}/></Field>
        {(['recursive','include_agent','enabled'] as const).map(key=><Field key={key} orientation="horizontal"><Checkbox id={'workflow-'+key} checked={edit[key]} onCheckedChange={value=>setEdit({...edit,[key]:value===true})}/><FieldLabel htmlFor={'workflow-'+key}>{key==='recursive'?'Include subfolders':key==='include_agent'?'Also trigger on agent changes':'Enabled'}</FieldLabel></Field>)}
        {edit.include_agent && <FieldDescription>Use a separate output folder or narrower patterns so the workflow does not trigger itself.</FieldDescription>}
      </FieldGroup><DialogFooter><Button variant="outline" type="button" onClick={()=>setEdit(undefined)}>Cancel</Button><Button disabled={busy}>Save</Button></DialogFooter>
    </form> : <>
      {admin && <Button onClick={()=>setEdit({name:'',folder,instruction:'',patterns:['*'],events:['shared.files_uploaded'],recursive:true,include_agent:false,enabled:false})}>New workflow</Button>}
      {rules.map(rule=><div key={rule.id} className="flex items-center justify-between gap-3 rounded-md border p-3"><div className="min-w-0"><p className="font-medium">{rule.name}</p><p className="truncate text-sm text-muted-foreground">/{rule.folder} · {rule.enabled?'Enabled':'Disabled'}</p></div>{admin && <div className="flex gap-2"><Button size="sm" variant="outline" onClick={()=>setEdit(rule)}>Edit</Button><Button size="sm" variant="ghost" onClick={async()=>{if(!window.confirm(`Delete workflow “${rule.name}”?`))return;try{await api('/workflows/'+rule.id,'DELETE');await load();}catch(e){error(e);}}}>Delete</Button></div>}</div>)}
      <h3 className="text-sm font-semibold">Recent activity</h3>
      {!events.length && <p className="text-sm text-muted-foreground">No file changes recorded yet.</p>}
      {events.slice(0,30).map(event=><div key={event.id} className="flex flex-col gap-1 border-t pt-2 text-sm"><p>{labels[event.event]} · {event.actor.name}</p><p className="truncate text-muted-foreground">{event.entries.map(e=>e.path).join(', ')}</p><time className="text-xs text-muted-foreground">{new Date(event.at*1000).toLocaleString()}</time>{deliveries.filter(d=>d.delivery.startsWith(event.id+':')).map(d=><p key={d.delivery} className="text-xs">{d.error?.startsWith('Cancelled')?d.error:d.delivered?'Accepted by factory':d.error?`Waiting for factory · ${d.error}`:'Queued'}</p>)}</div>)}
    </>}
  </DialogContent></Dialog>;
}
