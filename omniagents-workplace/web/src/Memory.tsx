import { panelList, panelRow, panelIcon } from "./panel-layout";
import { factoryUrl } from "./factory-scope";
import { factoryFetch } from "./factory-scope";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@omniagents/ui/input-group";
import { Textarea } from "@omniagents/ui/textarea";
import { Field, FieldLabel } from "@omniagents/ui/field";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { ArrowLeft, FileText, ChevronRight, Plus, RefreshCw, Search, Pencil, LoaderCircle } from "lucide-react";
import { toast } from "sonner";

type Store = { id: string; name: string; access: string };
type MemoryPage = {items: Memory[]; total: number; offset: number; limit: number};
type Memory = { id: string; store: string; text: string; kind: string; version: number; author: string;
  source: { run_id?: string }; tags: string[]; history?: Memory[]; reason?: string };
async function call<T>(operation: string, args?: unknown, agent = false): Promise<T> {
  const response = await factoryFetch(`/workplace/api/memory/${operation}${agent ? "?scope=agent" : ""}`, args === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Memory request failed");
  return data;
}
export function MemoryApp({ agent = false }: { agent?: boolean }) {
  const request = useCallback(<T,>(operation: string, args?: unknown) => call<T>(operation, args, agent), [agent]);
  const [stores, setStores] = useState<Store[]>([]);
  const [store, setStore] = useState("");
  const [query, setQuery] = useState("");
  const [pages, setPages] = useState<Record<string, MemoryPage>>({});
  const [offsets, setOffsets] = useState<Record<string, number>>({});
  const [pageError, setPageError] = useState("");
  const [items, setItems] = useState<Memory[]>([]);
  const [text, setText] = useState("");
  const [kind, setKind] = useState("fact");
  const [selected, setSelected] = useState<Memory | null>(null);
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const writable = stores.find(s => s.id === store)?.access === "write";
  const loadPages = useCallback(async () => Object.fromEntries(await Promise.all(stores.map(async s => [s.id, await request<MemoryPage>(`page?${new URLSearchParams({store:s.id,query,offset:String(offsets[s.id] || 0),limit:"10"})}`)]))), [stores, query, offsets, request]);
  const refresh = useCallback(async () => {
    if (agent) setItems(await request<Memory[]>("search", {query, stores:store ? [store] : []}));
    else {setPages(await loadPages());setPageError("");}
  }, [agent, query, store, request, loadPages]);
  useEffect(() => { request<Store[]>("stores").then(s => {
    setStores(s); if (agent) setStore(s[0]?.id || "");
  }).catch(e => toast.error(e.message)).finally(() => setLoading(false)); }, [request, agent]);
  useEffect(() => {
    let alive = true;
    if (agent) {
      if (!store) return;
      request<Memory[]>("search", {query, stores:[store]}).then(m => {if(alive) setItems(m);}).catch(e => {if(alive) toast.error(e.message);});
    } else {
      setPageError("");setRefreshing(true);
      loadPages().then(p => {if(alive) setPages(p);}).catch(e => {if(alive) setPageError(e.message);}).finally(() => {if(alive) setRefreshing(false);});
    }
    return () => {alive = false;};
  }, [agent, store, query, request, loadPages]);
  async function mutate(operation: string, args: unknown) {
    setBusy(true);
    try { await request(operation, args); setSelected(null); setEditing(false); setText(""); setReason(""); await refresh(); }
    catch (e) { toast.error((e as Error).message); }
    finally { setBusy(false); }
  }
  const back = () => { setSelected(null); setEditing(false); setText(""); setReason(""); };
    const open = async (m: Memory) => {
      setOpening(m.id);
      try { const value = await request<Memory>("get", {id:m.id}); setStore(value.store); setSelected(value); setText(value.text); setEditing(false); setReason(""); }
      catch (e) { toast.error((e as Error).message); }
      finally { setOpening(null); }
    };
    const newMemory = (id: string) => {setStore(id);setText("");setKind("fact");setEditing(true);};
    const rows = (memories: Memory[]) => <div className="flex flex-col">{memories.map(m => <button key={m.id} onClick={() => void open(m)} disabled={opening !== null} aria-busy={opening === m.id} className={panelRow}>
      <span className={panelIcon}>{opening === m.id ? <LoaderCircle className="size-4 animate-spin" /> : <FileText className="size-4" />}</span>
      <span className="min-w-0 flex-1 line-clamp-3 break-words text-sm leading-6">{m.text}</span><ChevronRight className="mt-2 size-3 shrink-0 text-muted-foreground" />
    </button>)}</div>;
    const content = <section aria-label={agent ? "Agent memory" : "Memory"} className={`${agent ? panelList : "flex min-w-0 flex-col"} gap-4`}>
      {selected || editing ? <>
        <div className="flex items-center justify-between gap-2"><Button variant="ghost" size="sm" onClick={back} disabled={busy}><ArrowLeft />Memory</Button>{!agent && <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{stores.find(s => s.id === store)?.name}</span>}
          {selected && !editing && writable && <Button variant="ghost" size="sm" onClick={() => setEditing(true)}><Pencil className="size-3.5" />Edit</Button>}
        </div>
        {editing ? <form className="flex flex-col gap-4" onSubmit={e => {e.preventDefault(); void mutate(selected ? "update" : "remember", selected ? {id:selected.id,text} : {store,text,kind,...(kind === "log" ? {date:new Date().toISOString().slice(0,10)} : {})});}}>
          <Field><FieldLabel htmlFor="agent-memory-text">{selected ? "Edit memory" : "New memory"}</FieldLabel><Textarea id="agent-memory-text" className="min-h-48" value={text} onChange={e => setText(e.target.value)} required /></Field>
          {!selected && <Field><FieldLabel htmlFor="agent-memory-kind">Kind</FieldLabel><NativeSelect id="agent-memory-kind" value={kind} onChange={e => setKind(e.target.value)}>{["fact","core","log","note"].map(k => <NativeSelectOption key={k} value={k}>{k}</NativeSelectOption>)}</NativeSelect></Field>}
          <div className="flex gap-2"><Button disabled={busy || !text.trim()}>Save memory</Button><Button type="button" variant="ghost" disabled={busy} onClick={() => {if (selected) {setEditing(false);setText(selected.text);} else back();}}>Cancel</Button></div>
        </form> : selected && <>
          <p className="whitespace-pre-wrap break-words px-2 text-sm leading-7">{selected.text}</p>
          <details className="px-2 text-sm"><summary className="cursor-pointer text-muted-foreground">Details</summary><dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-3 text-xs leading-5"><dt className="text-muted-foreground">Kind</dt><dd>{selected.kind}</dd>{selected.tags.length > 0 && <><dt className="text-muted-foreground">Tags</dt><dd className="break-words">{selected.tags.join(", ")}</dd></>}{selected.source.run_id && <><dt className="text-muted-foreground">Source</dt><dd><a className="underline" href={factoryUrl(`/factory/runs/${encodeURIComponent(selected.source.run_id)}`)}>View run</a></dd></>}</dl></details>
          <details className="px-2 text-sm"><summary className="cursor-pointer text-muted-foreground">History</summary><div className="mt-3 flex flex-col gap-4">{selected.history?.map(v => <div key={v.version}><p className="text-xs text-muted-foreground">Version {v.version}{v.reason ? ` · ${v.reason}` : ""}</p><p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6">{v.text}</p></div>)}</div></details>
          {writable && <details className="px-2 text-sm"><summary className="cursor-pointer text-muted-foreground">Forget memory</summary><div className="mt-3 flex flex-col gap-3"><Field><FieldLabel htmlFor="agent-memory-reason">Reason</FieldLabel><Input id="agent-memory-reason" value={reason} onChange={e => setReason(e.target.value)} /></Field><Button variant="destructive" disabled={busy || !reason.trim()} onClick={() => void mutate("forget", {id:selected.id,reason})}>Forget memory</Button></div></details>}
        </>}
      </> : <>
        <div className="flex items-center gap-2 px-2">
          <InputGroup className="min-w-0 flex-1"><InputGroupAddon><Search className="size-4" /></InputGroupAddon><InputGroupInput aria-label="Search memories" placeholder="Search memories" value={query} onChange={e => {setQuery(e.target.value);setOffsets({});}} /><InputGroupAddon align="inline-end"><InputGroupButton title="Refresh memories" aria-label="Refresh memories" disabled={refreshing} onClick={async () => {setRefreshing(true);try {await refresh();} catch(e) {toast.error((e as Error).message);} finally {setRefreshing(false);}}}><RefreshCw className={refreshing ? "size-3.5 animate-spin" : "size-3.5"} /></InputGroupButton></InputGroupAddon></InputGroup>
          {agent && writable && <Button variant="ghost" size="sm" aria-label="New memory" onClick={() => newMemory(store)}><Plus className="size-4" />New</Button>}
        </div>
        {loading ? <p role="status" className="px-2 text-sm text-muted-foreground">Loading…</p> : agent ? <>
          {!items.length && <p className="px-2 py-4 text-sm text-muted-foreground">No memories found.</p>}{rows(items)}
        </> : <>
          {pageError && <p role="alert" className="px-2 text-sm text-destructive">{pageError}</p>}
          <div className="flex flex-col gap-6">{stores.map(s => {
            const page = pages[s.id];
            return <section key={s.id} aria-label={s.name} className="flex flex-col gap-1">
              <header className="flex items-center justify-between gap-3 px-2"><h2 className="flex min-w-0 items-baseline gap-2 text-sm font-medium"><span className="truncate">{s.name.charAt(0).toUpperCase()+s.name.slice(1)}</span><span className="text-xs font-normal tabular-nums text-muted-foreground">{page?.total ?? "…"}</span></h2>
                {s.access === "write" && <Button variant="ghost" size="sm" aria-label={`Add memory to ${s.name}`} onClick={() => newMemory(s.id)}><Plus className="size-4" />Add</Button>}
              </header>
              {!page ? <p role="status" className="px-2 py-3 text-sm text-muted-foreground">Loading…</p> : page.items.length ? rows(page.items) : <p className="px-2 py-3 text-sm text-muted-foreground">{query ? "No matching memories." : "No memories yet."}</p>}
              {page && page.total > page.limit && <nav aria-label={`${s.name} pages`} className="flex flex-wrap items-center justify-between gap-2 px-2 pt-2">
                <span className="text-xs tabular-nums text-muted-foreground">{page.offset + 1}–{page.offset + page.items.length} of {page.total}</span>
                <div className="flex gap-1"><Button variant="ghost" size="sm" disabled={refreshing || page.offset === 0} onClick={() => setOffsets(previous => ({...previous,[s.id]:Math.max(0,page.offset-page.limit)}))}>Previous</Button><Button variant="ghost" size="sm" disabled={refreshing || page.offset + page.limit >= page.total} onClick={() => setOffsets(previous => ({...previous,[s.id]:page.offset+page.limit}))}>Next</Button></div>
              </nav>}
            </section>;
          })}</div>
          {!stores.length && <p className="px-2 py-4 text-sm text-muted-foreground">No memory collections available.</p>}
        </>}
      </>}
    </section>;
  if (agent) return content;
  return <main className="min-h-0 flex-1 overflow-auto">
    <div className="workplace-page">
      {content}
    </div>
  </main>;
}
