import { panelList, panelRow, panelTitle, panelIcon } from "./panel-layout";
import { useCallback, useEffect, useState, useRef, Fragment } from "react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@omniagents/ui/tabs";
import { Button } from "@omniagents/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@omniagents/ui/tooltip";
import { OmniPlanet } from "@omniagents/chat-ui/components/omni-avatar/planet";
import { agentPalette } from "@omniagents/chat-ui/components/omni-avatar/agent-palette";
import { ElicitationCard } from "@omniagents/chat-ui/components/ElicitationCard";
import type { ElicitationRequest } from "@omniagents/client/rpc/elicitation";
import { factoryFetch } from "./factory-scope";
import { X, List, Users, Brain, Check, CircleAlert, LoaderCircle, Circle, ChevronRight, ArrowLeft } from "lucide-react";
import { MemoryApp } from "./Memory";

type Item = { item_id: string; seq: number; status: string; kind: string; created_at?: string | number; content: Record<string, any> };
type Agent = { worker_id: string; name?: string; task: string; status: string; error?: string };
type Page = { items: Item[]; next_cursor?: string; attention: Item[] };
const printable = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
async function request<T>(chatId: string, path: string, body?: unknown): Promise<T> {
  const response = await factoryFetch(`/workplace/api/personal/chats/${encodeURIComponent(chatId)}/${path}`, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Unable to load agent activity");
  return data;
}
function Decision({ chatId, item, refresh }: { chatId: string; item: Item; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const c = item.content;
  async function decide(action: string, value?: unknown) {
    setBusy(true); setError("");
    try { await request(chatId, "decisions", {item_id: item.item_id, action, value}); await refresh(); }
    catch (e) { setError((e as Error).message); throw e; }
    finally { setBusy(false); }
  }
  if (item.kind === "elicitation") return <ElicitationCard request={{
    elicitationId: c.elicitation_id, kind: c.elicitation_kind, message: c.message,
    title: c.title, options: c.options, inputSchema: c.input_schema, url: c.url,
    persistResponse: false, sensitive: false,
  } as ElicitationRequest} onRespond={answer => decide(answer.action, "value" in answer ? answer.value : undefined)} />;
  return <section className="flex flex-col gap-2 rounded-lg border p-3" aria-label="Approval needed">
    <p className="text-sm font-medium">{c.tool || "Approval needed"}</p>
    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{printable(c.arguments ?? c.input)}</pre>
    {error && <p role="alert">{error}</p>}
    <div className="flex gap-2"><Button disabled={busy} onClick={() => void decide("approve").catch(() => {})}>Approve</Button>
      <Button variant="outline" disabled={busy} onClick={() => void decide("reject").catch(() => {})}>Reject</Button></div>
  </section>;
}
function StatusIcon({ status }: { status: string }) {
  const Icon = status === "completed" ? Check : ["failed", "error", "blocked"].includes(status) ? CircleAlert : ["started", "running"].includes(status) ? LoaderCircle : Circle;
  return <span title={status} aria-label={status} className={panelIcon}><Icon className={Icon === LoaderCircle ? "size-4 animate-spin" : "size-4"} /></span>;
}
function dateOf(value?: string | number) {
  if (!value) return undefined;
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? undefined : date;
}
export function activityDay(value?: string | number, now = new Date()) {
  const date = dateOf(value);
  if (!date) return "Earlier";
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, {month: "short", day: "numeric", ...(date.getFullYear() !== now.getFullYear() ? {year: "numeric" as const} : {})});
}
const readable = (name: string) => {
  const text = name.replace(/^mcp_.*?__/, "").replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
};
function ToolRow({ item }: { item: Item }) {
  const c = item.content;
  const title = readable(String(c.tool_label || c.tool || "Tool call"));
  const description = typeof c.metadata?.summary === "string" ? c.metadata.summary : "";
  const date = dateOf(item.created_at);
  return <details className="group min-w-0">
    <summary className={`${panelRow} cursor-pointer list-none [&::-webkit-details-marker]:hidden`}>
      <StatusIcon status={item.status} />
      <span className="min-w-0 flex-1"><span className={panelTitle}>{description ? description.split(/\n/)[0] : title}</span>
        <span className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted-foreground">{description ? title : item.status === "failed" ? "Failed" : ""}</span>
        {date && <time dateTime={date.toISOString()} className="block text-xs text-muted-foreground">{date.toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"})}</time>}
      </span>
      <ChevronRight className="mt-2 size-3 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 group-open:rotate-90 group-open:opacity-100" />
    </summary>
    <div className="mb-3 ml-14 min-w-0 border-l pl-3">
      <p className="break-words text-xs text-muted-foreground">{c.tool} · {item.status}</p>
      {["input", "output"].map(field => c[field] !== undefined && <div key={field} className="mt-3">
        <h3 className="text-xs font-medium">{field === "input" ? "Input" : "Result"}</h3>
        <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">{printable(c[field])}</pre>
      </div>)}
    </div>
  </details>;
}
/** The agent's face in its own colours (``agentPalette``), as in the chat list and header. */
function Identity({ connected, name, agentId }: { connected: boolean | null; name: string; agentId?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const planet = new OmniPlanet(canvas.current!, {size: 128, mood: connected === false ? "asleep" : "idle", ...(agentId ? {palette: agentPalette(agentId)} : {})});
    const pause = () => planet.setPaused(document.hidden);
    pause(); document.addEventListener("visibilitychange", pause);
    return () => { document.removeEventListener("visibilitychange", pause); planet.dispose(); };
  }, [connected, agentId]);
  return <div className="flex flex-col items-center pb-7 pt-10">
    <canvas ref={canvas} className="size-32" aria-label={name} role="img" />
    <h2 className="mt-1 text-xl font-medium tracking-tight">{name}</h2>
    <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground"><span className={connected ? "size-1.5 rounded-full bg-primary" : "size-1.5 rounded-full bg-muted-foreground"} />{connected === null ? "Connecting…" : connected ? "Connected" : "Reconnecting…"}</p>
  </div>;
}
function Activity({ chatId, worker = "" }: { chatId: string; worker?: string }) {
  const [items, setItems] = useState<Item[]>([]);
  const [attention, setAttention] = useState<Item[]>([]);
  const [cursor, setCursor] = useState<string>();
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [olderBusy, setOlderBusy] = useState(false);
  const path = `activity?worker=${encodeURIComponent(worker)}`;
  const refresh = useCallback(async () => {
    const page = await request<Page>(chatId, path);
    setItems(previous => {
      const merged = new Map(previous.map(item => [item.item_id, item]));
      page.items.forEach(item => merged.set(item.item_id, item));
      return [...merged.values()].sort((a, b) => b.seq - a.seq);
    });
    setCursor(previous => previous === undefined ? page.next_cursor || "" : previous);
    setAttention(page.attention); setLoaded(true); setError("");
  }, [chatId, path]);
  useEffect(() => {
    let alive = true, pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try { if (alive) await refresh(); } catch (e) { if (alive) setError((e as Error).message); }
      finally { pending = false; }
    };
    void poll(); const timer = setInterval(() => void poll(), 2000);
    return () => { alive = false; clearInterval(timer); };
  }, [refresh]);
  async function older() {
    setOlderBusy(true);
    try {
      const page = await request<Page>(chatId, `${path}&cursor=${encodeURIComponent(cursor!)}`);
      setItems(previous => [...new Map([...previous, ...page.items].map(item => [item.item_id, item])).values()].sort((a,b) => b.seq-a.seq));
      setCursor(page.next_cursor || ""); setError("");
    } catch (e) { setError((e as Error).message); }
    finally { setOlderBusy(false); }
  }
  return <div className={panelList}>
    {error && <p role="alert" className="text-sm">{error}</p>}
    {attention.map(item => <Decision key={item.item_id} chatId={chatId} item={item} refresh={refresh} />)}
    {!loaded && !error && <p role="status">Loading…</p>}
    {loaded && !items.length && <p className="text-sm text-muted-foreground">No tool calls yet.</p>}
    {items.map((item, index) => <Fragment key={item.item_id}>
      {(index === 0 || activityDay(items[index - 1].created_at) !== activityDay(item.created_at)) && <h3 className="px-2 pb-1 pt-5 text-sm font-medium">{activityDay(item.created_at)}</h3>}
      <ToolRow item={item} />
    </Fragment>)}
    {cursor && <Button variant="outline" disabled={olderBusy} onClick={() => void older()}>Load older calls</Button>}
  </div>;
}
function Agents({ chatId, agents, loaded, error }: { chatId: string; agents: Agent[]; loaded: boolean; error: string }) {
  const [selected, setSelected] = useState<Agent>();
  if (selected) return <><div className="px-5 pt-4"><Button variant="ghost" size="sm" onClick={() => setSelected(undefined)}><ArrowLeft /> Agents</Button><h3 className="mt-4 text-sm font-medium">{selected.name || selected.worker_id}</h3><details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer">Task</summary><p className="mt-2 whitespace-pre-wrap break-words leading-5">{selected.task}</p></details></div><Activity key={selected.worker_id} chatId={chatId} worker={selected.worker_id} /></>;
  return <div className={panelList}>
    {error && <p role="alert" className="px-2 text-sm">{error}</p>}
    {!loaded && !error && <p role="status" className="px-2 text-sm text-muted-foreground">Loading…</p>}
    {loaded && !agents.length && <p className="px-2 text-sm text-muted-foreground">No subagents yet.</p>}
    {agents.map(agent => <button key={agent.worker_id} aria-label={`View activity: ${agent.name || agent.worker_id}`} onClick={() => setSelected(agent)} className={panelRow}>
      <StatusIcon status={agent.status} /><span className="min-w-0 flex-1"><span className="block text-sm font-medium">{agent.name || agent.worker_id}</span><span className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">{agent.task}</span>{agent.error && <span className="mt-1 line-clamp-2 text-xs text-destructive">{agent.error}</span>}</span><ChevronRight className="mt-2 size-3 shrink-0 text-muted-foreground" />
    </button>)}
  </div>;
}
export function AgentPanel({ agentName = "Agent", agentId, chatId, tab, onTab, onClose }: { agentName?: string; agentId?: string; chatId: string; tab: string; onTab: (tab: string) => void; onClose?: () => void }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true, pending = false;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try { const data = await request<{agents: Agent[]}>(chatId, "agents"); if (alive) {setAgents(data.agents); setConnected(true); setError("");} }
      catch (e) { if (alive) {setConnected(false); setError((e as Error).message);} }
      finally { pending = false; }
    };
    void poll(); const timer = setInterval(() => void poll(), 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [chatId]);
  return <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto" style={{scrollbarGutter: "stable"}}>
    {onClose && <Button className="absolute right-3 top-3" variant="ghost" size="icon-sm" aria-label="Close viewer" onClick={onClose}><X className="size-4" /></Button>}
    <Identity connected={connected} name={agentName} agentId={agentId} />
    <TooltipProvider><Tabs value={tab} onValueChange={onTab} className="gap-0">
      <TabsList className="mx-4 grid w-auto grid-cols-3 rounded-full" aria-label="Agent details">
        {([{id:"activity",label:"Activity",Icon:List},{id:"agents",label:"Agents",Icon:Users},{id:"memory",label:"Memory",Icon:Brain}]).map(({id,label,Icon}) => <Tooltip key={id}><TooltipTrigger asChild><TabsTrigger value={id} aria-label={label} className="rounded-full"><Icon className="size-4" /></TabsTrigger></TooltipTrigger><TooltipContent>{label}</TooltipContent></Tooltip>)}
      </TabsList>
      <TabsContent value="activity"><Activity key={chatId} chatId={chatId} /></TabsContent>
      <TabsContent value="agents"><Agents key={chatId} chatId={chatId} agents={agents} loaded={connected !== null} error={error} /></TabsContent>
      <TabsContent value="memory"><MemoryApp key={chatId} agent={chatId} /></TabsContent>
    </Tabs></TooltipProvider>
  </div>;
}
