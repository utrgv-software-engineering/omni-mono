import { useCallback, useEffect, useState } from "react";
import { Button } from "@omniagents/ui/button";
import { Input } from "@omniagents/ui/input";
import { Textarea } from "@omniagents/ui/textarea";
import { Field, FieldGroup, FieldLabel } from "@omniagents/ui/field";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Badge } from "@omniagents/ui/badge";
import { Card, CardHeader, CardTitle, CardContent, CardFooter } from "@omniagents/ui/card";
import { toast } from "sonner";

type Store = { id: string; name: string; access: string };
type Memory = { id: string; store: string; text: string; kind: string; version: number; author: string;
  source: { run_id?: string }; tags: string[]; history?: Memory[]; reason?: string };
async function call<T>(operation: string, args?: unknown): Promise<T> {
  const response = await fetch(`/workplace/api/memory/${operation}`, args === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Memory request failed");
  return data;
}
export function MemoryApp() {
  const [stores, setStores] = useState<Store[]>([]);
  const [store, setStore] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Memory[]>([]);
  const [text, setText] = useState("");
  const [kind, setKind] = useState("fact");
  const [selected, setSelected] = useState<Memory | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const writable = stores.find(s => s.id === store)?.access === "write";
  const refresh = useCallback(async () => {
    setItems(await call<Memory[]>("search", { query, stores: store ? [store] : [] }));
  }, [query, store]);
  useEffect(() => { call<Store[]>("stores").then(s => {
    setStores(s); setStore(s.find(x => x.id.startsWith("personal:"))?.id || s[0]?.id || "");
  }).catch(e => toast.error(e.message)).finally(() => setLoading(false)); }, []);
  useEffect(() => {
    let alive = true;
    call<Memory[]>("search", { query, stores: store ? [store] : [] }).then(m => { if (alive) setItems(m); })
      .catch(e => { if (alive) toast.error(e.message); });
    return () => { alive = false; };
  }, [store, query]);
  async function mutate(operation: string, args: unknown) {
    setBusy(true);
    try { await call(operation, args); setSelected(null); setText(""); setReason(""); await refresh(); }
    catch (e) { toast.error((e as Error).message); }
    finally { setBusy(false); }
  }
  return <main className="flex min-h-0 flex-1 flex-col gap-6 overflow-auto p-6">
    <div className="flex items-center justify-between"><h1 className="text-xl font-semibold">Memory</h1>
      <Button variant="outline" onClick={() => void refresh().catch(e => toast.error(e.message))}>Refresh</Button>
    </div>
    <p className="text-sm text-muted-foreground">Lessons and preferences shared across runs. Every edit keeps its history.</p>
    <FieldGroup>
      <Field><FieldLabel htmlFor="memory-store">Store</FieldLabel>
        <NativeSelect id="memory-store" value={store} onChange={e => { setStore(e.target.value); setSelected(null); setText(""); }}>
          {stores.map(s => <NativeSelectOption key={s.id} value={s.id}>{s.name} · {s.id} ({s.access})</NativeSelectOption>)}
        </NativeSelect>
      </Field>
      <Field><FieldLabel htmlFor="memory-search">Search</FieldLabel>
        <Input id="memory-search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search memories and tags" />
      </Field>
    </FieldGroup>
    {loading ? <p>Loading stores…</p> : items.length === 0 ? <p className="text-muted-foreground">No memories found.</p> : null}
    <div className="grid gap-4 md:grid-cols-2">
      {items.map(m => <Card key={m.id}>
        <CardHeader><CardTitle><Badge variant="secondary">{m.kind}</Badge></CardTitle></CardHeader>
        <CardContent><p className="whitespace-pre-wrap">{m.text}</p>
          <p className="text-sm text-muted-foreground">{m.tags.join(", ")}</p>
          {m.source.run_id && <a className="underline" href={`/factory/runs/${encodeURIComponent(m.source.run_id)}`}>Source run</a>}
        </CardContent>
        <CardFooter><Button variant="outline" onClick={() => call<Memory>("get", { id: m.id }).then(value => {
          setSelected(value); setText(value.text); setReason("");
        }).catch(e => toast.error(e.message))}>View history{writable ? " / edit" : ""}</Button></CardFooter>
      </Card>)}
    </div>
    {selected && <section aria-label="Memory history"><h2 className="font-semibold">History</h2>
      {selected.history?.map(v => <p key={v.version} className="my-2 whitespace-pre-wrap text-sm">
        Version {v.version} · {v.author}{v.reason ? ` · ${v.reason}` : ""}: {v.text}
      </p>)}
    </section>}
    {writable && <form onSubmit={e => { e.preventDefault(); void mutate(selected ? "update" : "remember",
      selected ? { id: selected.id, text } : { store, text, kind, ...(kind === "log" ? { date: new Date().toISOString().slice(0, 10) } : {}) }); }}>
      <FieldGroup>
        <Field><FieldLabel htmlFor="memory-text">{selected ? "Edit memory" : "New memory"}</FieldLabel>
          <Textarea id="memory-text" value={text} onChange={e => setText(e.target.value)} maxLength={16000} required />
        </Field>
        {!selected && <Field><FieldLabel htmlFor="memory-kind">Kind</FieldLabel>
          <NativeSelect id="memory-kind" value={kind} onChange={e => setKind(e.target.value)}>
            {["fact", "core", "log", "note"].map(k => <NativeSelectOption key={k} value={k}>{k}</NativeSelectOption>)}
          </NativeSelect>
        </Field>}
        <div className="flex gap-2"><Button disabled={busy || !text.trim()}>Save memory</Button>
          {selected && <Button type="button" variant="outline" onClick={() => { setSelected(null); setText(""); }}>Cancel edit</Button>}</div>
        {selected && <Field><FieldLabel htmlFor="memory-reason">Reason for forgetting</FieldLabel>
          <Input id="memory-reason" value={reason} onChange={e => setReason(e.target.value)} />
          <Button type="button" variant="destructive" disabled={busy || !reason.trim()}
            onClick={() => void mutate("forget", { id: selected.id, reason })}>Forget memory</Button>
        </Field>}
      </FieldGroup>
    </form>}
  </main>;
}
