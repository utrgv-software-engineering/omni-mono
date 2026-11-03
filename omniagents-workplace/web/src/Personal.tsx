import type { Presentation } from "@omniagents/chat-ui/lib/presentation";
import { ResizablePanelGroup, ResizablePanel, ResizableHandle } from "@omniagents/ui/resizable";
import { Monitor } from "lucide-react";
import { AgentReadiness } from "./AgentReadiness";
import { AddAgentDialog } from "./Agents";
import { AgentAvatar } from "@omniagents/chat-ui/components/omni-avatar/AgentAvatar";
import { Avatar, AvatarFallback } from "@omniagents/ui/avatar";
import { Popover, PopoverContent, PopoverTrigger } from "@omniagents/ui/popover";
import { ChatViewer } from "./ChatViewer";
import { useDesktopAvailable } from "./Desktop";
import type { Person } from "./api";
import { factoryFetch, factoryUrl, factoryStorageKey } from "./factory-scope";
import { PublishedChat } from "./PublishedChat";
import { type ChatHost, ChatHostProvider } from "@omniagents/chat-ui/host";
import { RPCClientProvider } from "@omniagents/chat-ui/rpc-context";
import { UiConfigProvider } from "@omniagents/chat-ui/ui-config";
import { TooltipProvider } from "@omniagents/ui/tooltip";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@omniagents/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@omniagents/ui/sheet";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@omniagents/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
} from "@omniagents/ui/dropdown-menu";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@omniagents/ui/collapsible";
import { Input } from "@omniagents/ui/input";
import { Field, FieldLabel } from "@omniagents/ui/field";
import {
  PanelLeft,
  Plus,
  MoreHorizontal,
  ChevronRight,
  Gauge,
  Pencil,
  MessagesSquare,
  Search,
  X,
  LoaderCircle,
  CircleAlert,
} from "lucide-react";
import { toast } from "sonner";

const identity = { name: "omniagents-workplace", version: "workplace" };
const host: ChatHost = {
  notifyError: (title, description) => toast.error(title, { description }),
  voiceMode: "hosted",
  audioSettings: () => ({
    inputDeviceId: null,
    outputDeviceId: null,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }),
};
type Chat = {
  id: string;
  title: string;
  archived: boolean;
  kind: "main" | "side" | "automation";
  agent?: string;
  automation?: string | null;
  running?: boolean;
  waiting?: boolean;
  unread?: boolean;
  working_for?: string | null;
  revision?: string;
  offline?: boolean;
  last_message?: { role: "user" | "assistant"; author_name?: string; text: string; at: number } | null;
};
type ChatAgent = { id: string; name: string; description?: string };
function agentName(id?: string) {
  return id ? id.replace(/[_-]+/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "Agent";
}
function humanize(name?: string | null) {
  return name ? name.replace(/[_-]+/g, " ").replace(/^\w/, c => c.toUpperCase()) : "";
}
/** "3:45 PM" today, "Yesterday", a weekday this week, else a date. */
function when(seconds?: number) {
  if (!seconds) return "";
  const at = new Date(seconds * 1000);
  const today = new Date();
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(today) - day(at)) / 86400000);
  if (days === 0) return at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (days === 1) return "Yesterday";
  if (days < 7) return at.toLocaleDateString(undefined, { weekday: "long" });
  return at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words.length > 1 ? (words[words.length - 1]?.[0] ?? "") : "")).toUpperCase() || "?";
}
const selectedKey = "workplace.chat.selected";
function saved<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(factoryStorageKey(key)) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: unknown) {
  try {
    localStorage.setItem(factoryStorageKey(key), JSON.stringify(value));
  } catch {
    /* Storage may be disabled. */
  }
}
async function request(path = "", method = "GET", body?: unknown) {
  const response = await factoryFetch(`/workplace/api/personal/chats${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Unable to open chat");
  return data;
}

/** Needs input, then working, then unread: one status mark for a chat or an agent. */
function StatusMark({ waiting, running, unread }: { waiting?: boolean; running?: boolean; unread?: boolean }) {
  if (waiting) return <CircleAlert aria-label="Needs your input" />;
  if (running) return <LoaderCircle className="animate-spin" aria-label="Working" />;
  if (unread)
    return <span className="size-2 shrink-0 rounded-full bg-primary" role="img" aria-label="Unread activity" />;
  return null;
}

export function PersonalApp({ account, navigate }: { account: Person; navigate?: (path: string) => void }) {
  const [panelTabs, setPanelTabs] = useState<Record<string, string>>({});
  const [views, setViews] = useState<Record<string, Presentation | undefined>>({});
  const [split, setSplit] = useState(() => window.matchMedia("(min-width: 1024px)").matches);
  const desktopAvailable = useDesktopAvailable(true);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setSplit(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const present = useCallback((id: string, value: Presentation) => setViews(previous => ({ ...previous, [id]: value })), []);

  const [chats, setChats] = useState<Chat[]>([]);
  const [agents, setAgents] = useState<string[]>([]);
  const [agentDetails, setAgentDetails] = useState<ChatAgent[]>([]);
  const [defaultAgent, setDefaultAgent] = useState("");
  const [selected, setSelected] = useState(
    () =>
      new URLSearchParams(window.location.search).get("chat") ||
      saved(selectedKey, ""),
  );
  const [query, setQuery] = useState("");
  const [threadsOpen, setThreadsOpen] = useState(false);
  const [picker, setPicker] = useState(false);
  const [wide, setWide] = useState(
    () => window.matchMedia("(min-width: 1401px)").matches,
  );
  const [editing, setEditing] = useState<Chat>();
  const [deleting, setDeleting] = useState<Chat>();
  const [readiness, setReadiness] = useState<string>();
  const [addingAgent, setAddingAgent] = useState(false);
  // A just-added agent: opened once Chat lists it.
  const [added, setAdded] = useState<string>();
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [loaded, setLoaded] = useState(false);
  const [opened, setOpened] = useState<string[]>([]);
  const revision = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1401px)");
    const update = () => setWide(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const select = useCallback((id: string) => {
    setSelected(id);
    setThreadsOpen(false);
    remember(selectedKey, id);
    if (!window.matchMedia("(min-width: 1401px)").matches) setPicker(false);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("chat", id);
    else url.searchParams.delete("chat");
    window.history.replaceState(null, "", url);
  }, []);

  const refreshChats = useCallback(async () => {
    const currentRevision = ++revision.current;
    const data = await request();
    if (currentRevision !== revision.current) return;
    setChats(data.chats);
    setAgents(data.agents || []);
    setDefaultAgent(data.chat_agent || "");
    setAgentDetails(data.agent_details || []);
    setLoaded(true);
    setError(undefined);
    const current = data.chats.find(
      (chat: Chat) => chat.id === selectedRef.current,
    );
    if (current) remember(selectedKey, current.id);
    else
      select(
        data.chats.find((chat: Chat) => chat.kind === "main" && chat.agent === data.chat_agent)?.id ||
          data.chats.find((chat: Chat) => !chat.archived)?.id ||
          "",
      );
  }, [select]);

  useEffect(() => {
    let disposed = false;
    let polling = false;
    const refresh = async () => {
      if (polling || document.visibilityState === "hidden") return;
      polling = true;
      try {
        await refreshChats();
      } catch (e) {
        if (!disposed)
          setError(e instanceof Error ? e.message : "Unable to open chat");
      } finally {
        polling = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      disposed = true;
      ++revision.current;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [refreshChats]);

  // Reading a chat marks it read for this admin only (the server keeps it).
  const markedRef = useRef<Record<string, string>>({});
  useEffect(() => {
    const markSeen = () => {
      const current = chats.find((chat) => chat.id === selected);
      const seenRevision = current?.revision;
      if (
        document.visibilityState === "hidden" ||
        !current?.unread ||
        !seenRevision ||
        current.offline ||
        markedRef.current[current.id] === seenRevision
      )
        return;
      markedRef.current[current.id] = seenRevision;
      setChats(previous => previous.map(chat => chat.id === current.id ? { ...chat, unread: false } : chat));
      void request(`/${encodeURIComponent(current.id)}/seen`, "POST", { revision: seenRevision }).catch(() => {
        delete markedRef.current[current.id];
      });
    };
    markSeen();
    document.addEventListener("visibilitychange", markSeen);
    return () => document.removeEventListener("visibilitychange", markSeen);
  }, [chats, selected]);

  async function mutate(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    ++revision.current;
    try {
      await action();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to update chat");
    } finally {
      setBusy(false);
    }
  }

  const displayAgent = (id?: string) => agentDetails.find(agent => agent.id === id)?.name || agentName(id);
  const current = chats.find((chat) => chat.id === selected);
  const selectedAgent = current?.agent || defaultAgent;
  const mainOf = (agent: string) => chats.find(chat => chat.kind === "main" && chat.agent === agent);
  const fallbackFor = (agent: string | undefined, excluding: string) =>
    (agent && mainOf(agent)?.id) || chats.find(chat => chat.id !== excluding && !chat.archived)?.id || "";
  const newSideChat = () => void mutate(async () => {
    const chat = await request("", "POST", { agent: selectedAgent });
    await refreshChats();
    select(chat.id);
  });
  const view = views[selected];
  const closeViewer = () => setViews(previous => ({ ...previous, [selected]: undefined }));
  const viewer = view ? <ChatViewer agentId={current?.agent} agentName={displayAgent(current?.agent)} chatId={selected} tab={panelTabs[selected] || "activity"} onTab={tab => setPanelTabs(previous => ({...previous, [selected]: tab}))} value={view} account={account} onClose={closeViewer} onPresent={value => present(selected, value)} /> : null;
  const sessionId = loaded && current ? selected : undefined;
  useEffect(() => {
    if (sessionId) setOpened(previous => previous.includes(sessionId) ? previous : [...previous, sessionId]);
  }, [sessionId]);
  function row(chat: Chat) {
    const detail = chat.working_for
      ? `Working for ${chat.working_for}`
      : chat.kind === "automation"
        ? humanize(chat.automation)
        : "";
    return (
      <div key={chat.id} className="flex items-center gap-1">
        <Button
          className="h-auto min-w-0 flex-1 justify-start py-2"
          variant={chat.id === selected ? "secondary" : "ghost"}
          onClick={() => select(chat.id)}
          aria-current={chat.id === selected ? "page" : undefined}
          title={chat.title}
          aria-label={chat.title}
        >
          <span className="min-w-0 flex-1 truncate text-left">
            <span className="block truncate">{chat.title}</span>
            {detail && <span className="block truncate text-xs font-normal text-muted-foreground">{detail}</span>}
          </span>
          <StatusMark waiting={chat.waiting} running={chat.running} unread={chat.id !== selected && chat.unread} />
        </Button>
        {chat.kind !== "main" && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="icon-sm"
                variant="ghost"
                aria-label={`Manage ${chat.title}`}
              >
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuGroup>
                <DropdownMenuItem
                  onSelect={() => {
                    setEditing(chat);
                    setTitle(chat.title);
                  }}
                >
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={busy}
                  onSelect={() =>
                    void mutate(async () => {
                      await request(`/${chat.id}`, "PATCH", {
                        archived: !chat.archived,
                      });
                      if (!chat.archived && selected === chat.id) select(fallbackFor(chat.agent, chat.id));
                      await refreshChats();
                    })
                  }
                >
                  {chat.archived ? "Restore" : "Archive"}
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => setDeleting(chat)}
                >
                  Delete…
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    );
  }
  function group(label: string, items: Chat[]) {
    if (!items.length) return null;
    const marks = {
      waiting: items.some(chat => chat.waiting),
      running: items.some(chat => chat.running),
      unread: items.some(chat => chat.id !== selected && chat.unread),
    };
    return (
      <Collapsible defaultOpen={items.some(chat => chat.id === selected)}>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" className="group w-full justify-start">
            <ChevronRight className="group-data-[state=open]:rotate-90" />
            <span className="flex-1 text-left">{label} · {items.length}</span>
            <StatusMark {...marks} />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="flex flex-col gap-1">{items.map(row)}</CollapsibleContent>
      </Collapsible>
    );
  }
  const agentChats = chats.filter(chat => chat.agent === selectedAgent);
  const main = mainOf(selectedAgent);
  // One row per agent, as a messaging app's conversations: the default agent
  // first, then by its latest activity in any of its chats.
  const agentRows = agents
    .map(agent => {
      const own = chats.filter(chat => chat.agent === agent && !chat.archived);
      const home = mainOf(agent);
      const at = Math.max(0, ...own.map(chat => chat.last_message?.at ?? 0));
      const last = home?.last_message;
      const preview = last ? (last.role === "user" ? `${last.author_name}: ${last.text}` : last.text) : "";
      return {
        agent,
        name: displayAgent(agent),
        home,
        at,
        text: preview.replace(/\s+/g, " "),
        waiting: own.some(chat => chat.waiting),
        running: own.some(chat => chat.running),
        working: own.find(chat => chat.working_for)?.working_for,
        unread: own.some(chat => chat.id !== selected && chat.unread),
        disabled: !own.length && !home,
      };
    })
    .sort((a, b) => (a.agent === defaultAgent ? -1 : b.agent === defaultAgent ? 1 : b.at - a.at || a.name.localeCompare(b.name)));
  const needle = query.trim().toLowerCase();
  const visibleRows = needle
    ? agentRows.filter(row => row.name.toLowerCase().includes(needle) || row.text.toLowerCase().includes(needle))
    : agentRows;
  const navigation = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative px-3 pb-2">
        <Search className="pointer-events-none absolute left-5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search agents"
          placeholder="Search"
          value={query}
          onChange={event => setQuery(event.target.value)}
          className="pl-8"
        />
      </div>
      <nav aria-label="Agents" className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-2">
        {visibleRows.map(row => (
          <button
            key={row.agent}
            type="button"
            aria-label={row.name}
            aria-current={row.agent === selectedAgent ? "true" : undefined}
            disabled={row.disabled}
            onClick={() => select(fallbackFor(row.agent, ""))}
            className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-accent disabled:opacity-50 aria-[current=true]:bg-secondary"
          >
            <span className="relative shrink-0">
              <AgentAvatar seed={row.agent} size={36} mood={row.waiting ? "waiting" : row.running ? "thinking" : "idle"} />
              {row.unread && (
                <span className="absolute -right-0.5 -top-0.5 size-2.5 rounded-full bg-destructive ring-2 ring-background" role="img" aria-label="Unread activity" />
              )}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{row.name}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{when(row.at)}</span>
              </span>
              <span className={`block truncate text-sm ${row.waiting ? "text-foreground" : "text-muted-foreground"}`}>
                {row.waiting ? "Needs your input" : row.working ? `Working for ${row.working}` : row.text}
              </span>
            </span>
          </button>
        ))}
        {needle && !visibleRows.length && <p className="px-2 py-3 text-sm text-muted-foreground">No agents match.</p>}
        {navigate && (
          <button
            type="button"
            onClick={() => setAddingAgent(true)}
            className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground hover:bg-accent"
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-dashed">
              <Plus className="size-4" />
            </span>
            Add agent
          </button>
        )}
      </nav>
      <div className="flex items-center gap-2 border-t px-4 py-3">
        <Avatar size="sm" aria-hidden>
          <AvatarFallback>{initials(account.name || account.username || "?")}</AvatarFallback>
        </Avatar>
        <span className="truncate text-sm">{account.name || account.username}</span>
      </div>
    </div>
  );
  const threadMarks = {
    waiting: agentChats.some(chat => chat.kind !== "main" && !chat.archived && chat.waiting),
    running: agentChats.some(chat => chat.kind !== "main" && !chat.archived && chat.running),
    unread: agentChats.some(chat => chat.kind !== "main" && !chat.archived && chat.id !== selected && chat.unread),
  };
  const threads = (
    <div className="flex max-h-[70vh] flex-col gap-1 overflow-y-auto">
      {main && row(main)}
      <div className="mt-2 flex items-center justify-between px-3">
        <h4 className="text-xs font-medium text-muted-foreground">Side Chats</h4>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="New side chat"
          disabled={busy || !selectedAgent}
          onClick={newSideChat}
        >
          <Plus />
        </Button>
      </div>
      {agentChats.filter(chat => chat.kind === "side" && !chat.archived).map(row)}
      {group("Automations", agentChats.filter(chat => chat.kind === "automation" && !chat.archived))}
      {group("Archived", agentChats.filter(chat => chat.archived))}
    </div>
  );
  useEffect(() => {
    if (!added || !agents.includes(added)) return;
    const home = fallbackFor(added, "");
    if (home) {
      select(home);
      setAdded(undefined);
    }
  }, [added, agents]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1">
      {wide && picker && (
        <aside
          aria-label="Conversations"
          className="flex w-[280px] shrink-0 flex-col border-r bg-background"
        >
          <header className="flex items-center justify-between p-4">
            <h2 className="font-semibold">Agents</h2>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close chat sidebar"
              onClick={() => setPicker(false)}
            >
              <X />
            </Button>
          </header>
          {navigation}
        </aside>
      )}
      {!wide && (
        <Sheet open={picker} onOpenChange={setPicker}>
          <SheetContent
            side="left"
            className="w-[280px] max-w-[85%]"
            aria-describedby={undefined}
          >
            <SheetHeader>
              <SheetTitle>Agents</SheetTitle>
            </SheetHeader>
            {navigation}
          </SheetContent>
        </Sheet>
      )}
      <ResizablePanelGroup orientation="horizontal">
      <ResizablePanel id="chat" minSize={split ? "320px" : "0px"}>
      <div className="relative flex min-h-0 min-w-0 h-full flex-col">
        {loaded && (
          // Over the conversation, which fades beneath it (PublishedChat); the
          // middle column keeps the agent's avatar clear of the name and actions.
          <header className="pointer-events-none absolute inset-x-0 top-0 z-20 grid grid-cols-[minmax(0,1fr)_7rem_minmax(0,1fr)] items-center gap-2 px-3 py-2 [&>*]:pointer-events-auto">
            <div className="flex min-w-0 items-center gap-2">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={picker ? "Hide chats" : "Show chats"}
              aria-expanded={picker}
              onClick={() => setPicker((value) => !value)}
            >
              <PanelLeft />
            </Button>
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-sm font-medium">{current ? displayAgent(current.agent) : "Agents"}</h2>
              {current && (current.working_for || current.kind !== "main") && (
                <p className="truncate text-xs text-muted-foreground">
                  {current.working_for ? `Working for ${current.working_for}` : current.title}
                </p>
              )}
            </div>
            </div>
            <div aria-hidden="true" />
            <div className="flex min-w-0 items-center justify-end gap-1">
              {current && (
                <Popover open={threadsOpen} onOpenChange={setThreadsOpen}>
                  <PopoverTrigger asChild>
                    <Button variant="ghost" size="sm" aria-label={`Threads with ${displayAgent(selectedAgent)}`}>
                      <MessagesSquare />
                      <span className="hidden sm:inline">Threads</span>
                      <StatusMark {...threadMarks} />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent align="end" className="w-80 p-2" aria-label="Threads">
                    {threads}
                  </PopoverContent>
                </Popover>
              )}
              {current && navigate && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Edit ${displayAgent(selectedAgent)}`}
                  onClick={() => navigate(`/agents/${encodeURIComponent(selectedAgent)}`)}
                >
                  <Pencil />
                </Button>
              )}
              {current && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Readiness of ${displayAgent(selectedAgent)}`}
                  onClick={() => setReadiness(selectedAgent)}
                >
                  <Gauge />
                </Button>
              )}
              {desktopAvailable && <Button variant="ghost" size="icon-sm" aria-label="Open desktop"
                onClick={() => present(selected, { kind: "desktop", title: "Desktop", presentation_id: crypto.randomUUID(), issued_at: Date.now() })}><Monitor /></Button>}
            </div>
          </header>
        )}
        {!loaded && error ? (
          <div role="alert" className="p-4 pt-14 text-sm">
            <p>{error}</p>
            <Button
              variant="ghost"
              onClick={() =>
                void refreshChats().catch((e) => setError(e.message))
              }
            >
              Retry
            </Button>
          </div>
        ) : sessionId ? (
          <div className="relative min-h-0 flex-1">
            {opened.filter(id => chats.some(chat => chat.id === id)).map(id => <ConversationView key={id} agentId={chats.find(chat => chat.id === id)?.agent} agentName={displayAgent(chats.find(chat => chat.id === id)?.agent)} sessionId={id} active={id === sessionId} showAvatar={!views[id]} onPresent={present} />)}
          </div>
        ) : (
          <p className="p-4 pt-14 text-sm text-muted-foreground">{loaded ? "No chats yet." : "Connecting…"}</p>
        )}
      </div>
      </ResizablePanel>
      {split && view && <><ResizableHandle withHandle /><ResizablePanel id="viewer" defaultSize={view.kind === "activity" ? "380px" : "50%"} minSize="360px">{viewer}</ResizablePanel></>}
      </ResizablePanelGroup>
      {!split && <Sheet open={!!view} onOpenChange={open => { if (!open) closeViewer(); }}>
        <SheetContent side="right" showCloseButton={false} className="w-full max-w-none gap-0 p-0 sm:max-w-none" aria-describedby={undefined}>
          <SheetHeader className="sr-only"><SheetTitle>{view?.title || "Viewer"}</SheetTitle></SheetHeader>
          {viewer}
        </SheetContent>
      </Sheet>}
      {addingAgent && (
        <AddAgentDialog
          onClose={() => setAddingAgent(false)}
          onAdded={key => {
            setAddingAgent(false);
            setAdded(key);
            void refreshChats();
          }}
        />
      )}
      {readiness && (
        <AgentReadiness agent={readiness} name={displayAgent(readiness)} onClose={() => setReadiness(undefined)} />
      )}
      <Dialog
        open={!!editing}
        onOpenChange={(open) => {
          if (!open && !busy) setEditing(undefined);
        }}
      >
        <DialogContent aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Rename chat</DialogTitle>
          </DialogHeader>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(async () => {
                await request(`/${editing!.id}`, "PATCH", {
                  title: title.trim(),
                });
                await refreshChats();
                setEditing(undefined);
              });
            }}
          >
            <Field>
              <FieldLabel htmlFor="chat-title">Chat name</FieldLabel>
              <Input
                id="chat-title"
                value={title}
                maxLength={100}
                onChange={(event) => setTitle(event.target.value)}
                autoFocus
              />
            </Field>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setEditing(undefined)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={busy || !title.trim()}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !busy) setDeleting(undefined);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete chat?</DialogTitle>
            <DialogDescription>
              Delete “{deleting?.title}” and its conversation history?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setDeleting(undefined)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() =>
                void mutate(async () => {
                  await request(`/${deleting!.id}`, "DELETE");
                  if (selected === deleting!.id) select(fallbackFor(deleting!.agent, deleting!.id));
                  setDeleting(undefined);
                  await refreshChats();
                })
              }
            >
              Delete chat
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}


const ConversationView = memo(function ConversationView({ sessionId, agentId, agentName, active, showAvatar, onPresent }: { agentId?: string; agentName: string; sessionId: string; active: boolean; showAvatar: boolean; onPresent: (id: string, value: Presentation) => void }) {
  const conversationHost = useMemo(() => ({ ...host, presentView: (value: Presentation) => onPresent(sessionId, value) }), [sessionId, onPresent]);
  const connection = useMemo(() => ({ baseUrl: factoryUrl(`${window.location.origin}/workplace/api/personal/chats/${encodeURIComponent(sessionId)}/runtime/?minimal=true`), realtime: true }), [sessionId]);
  return <div className="absolute inset-0 min-h-0" style={{ visibility: active ? "visible" : "hidden" }} aria-hidden={!active} inert={!active}>
    <ChatHostProvider host={conversationHost}><TooltipProvider delayDuration={300}><UiConfigProvider connection={connection}><RPCClientProvider identity={identity}>
      <PublishedChat agentId={agentId} agentName={agentName} sessionId={sessionId} active={active} showAvatar={showAvatar} onPresent={value => onPresent(sessionId, value)} />
    </RPCClientProvider></UiConfigProvider></TooltipProvider></ChatHostProvider>
  </div>;
});
