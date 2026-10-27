import { App } from "@omniagents/chat-ui/App";
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
  running?: boolean;
  waiting?: boolean;
  revision?: string;
  offline?: boolean;
};
const main = "factory-personal";
const selectedKey = "workplace.chat.selected";
const seenKey = "workplace.chat.seen";
function saved<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Storage may be disabled. */
  }
}
async function request(path = "", method = "GET", body?: unknown) {
  const response = await fetch(`/workplace/api/personal/chats${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Unable to open chat");
  return data;
}

export function PersonalApp() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [selected, setSelected] = useState(
    () =>
      new URLSearchParams(window.location.search).get("chat") ||
      saved(selectedKey, main),
  );
  const [seen, setSeen] = useState<Record<string, string>>(() =>
    saved(seenKey, {}),
  );
  const [picker, setPicker] = useState(false);
  const [wide, setWide] = useState(
    () => window.matchMedia("(min-width: 1401px)").matches,
  );
  const [editing, setEditing] = useState<Chat>();
  const [deleting, setDeleting] = useState<Chat>();
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
    remember(selectedKey, id);
    if (!window.matchMedia("(min-width: 1401px)").matches) setPicker(false);
    const url = new URL(window.location.href);
    url.searchParams.set("chat", id);
    window.history.replaceState(null, "", url);
  }, []);

  const refreshChats = useCallback(async () => {
    const currentRevision = ++revision.current;
    const data = await request();
    if (currentRevision !== revision.current) return;
    setChats(data.chats);
    setLoaded(true);
    setError(undefined);
    const current = data.chats.find(
      (chat: Chat) => chat.id === selectedRef.current,
    );
    if (!current) select(main);
    else remember(selectedKey, current.id);
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

  useEffect(() => {
    const markSeen = () => {
      const current = chats.find((chat) => chat.id === selected);
      if (
        document.visibilityState !== "hidden" &&
        current?.revision &&
        !current.offline
      ) {
        setSeen((previous) => {
          if (previous[selected] === current.revision) return previous;
          const next = { ...previous, [selected]: current.revision! };
          remember(seenKey, next);
          return next;
        });
      }
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

  const current = chats.find((chat) => chat.id === selected);
  const sessionId = loaded && current ? selected : undefined;
  useEffect(() => {
    if (sessionId) setOpened(previous => previous.includes(sessionId) ? previous : [...previous, sessionId]);
  }, [sessionId]);
  function row(chat: Chat) {
    const unread =
      chat.id !== selected &&
      !!chat.revision &&
      seen[chat.id] !== chat.revision;
    return (
      <div key={chat.id} className="flex items-center gap-1">
        <Button
          className="min-w-0 flex-1 justify-start"
          variant={chat.id === selected ? "secondary" : "ghost"}
          onClick={() => select(chat.id)}
          aria-current={chat.id === selected ? "page" : undefined}
          title={chat.title}
        >
          <span className="min-w-0 flex-1 truncate text-left">
            {chat.title}
          </span>
          {chat.waiting ? (
            <CircleAlert aria-label="Needs your input" />
          ) : chat.running ? (
            <LoaderCircle className="animate-spin" aria-label="Working" />
          ) : unread ? (
            <span
              className="size-2 shrink-0 rounded-full bg-primary"
              role="img"
              aria-label="Unread activity"
            />
          ) : null}
        </Button>
        {chat.id !== main && (
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
                      if (!chat.archived && selected === chat.id) select(main);
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
  const archived = chats.filter((chat) => chat.archived);
  const navigation = (
    <nav
      aria-label="Chat sessions"
      className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto px-3 pb-4"
    >
      {chats.filter((chat) => chat.id === main).map(row)}
      <div className="mt-3 flex items-center justify-between px-3">
        <h3 className="text-sm font-medium">Side Chats</h3>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="New side chat"
          disabled={busy}
          onClick={() =>
            void mutate(async () => {
              const chat = await request("", "POST", {});
              await refreshChats();
              select(chat.id);
            })
          }
        >
          <Plus />
        </Button>
      </div>
      {chats.filter((chat) => chat.id !== main && !chat.archived).map(row)}
      {archived.length > 0 && (
        <Collapsible>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" className="group w-full justify-start">
              <ChevronRight className="group-data-[state=open]:rotate-90" />
              Archived · {archived.length}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>{archived.map(row)}</CollapsibleContent>
        </Collapsible>
      )}
    </nav>
  );

  return (
    <div className="relative flex min-h-0 min-w-0 flex-1">
      {wide && picker && (
        <aside
          aria-label="Conversations"
          className="flex w-[280px] shrink-0 flex-col border-r bg-background"
        >
          <header className="flex items-center justify-between p-4">
            <h2 className="font-semibold">Chats</h2>
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
              <SheetTitle>Chats</SheetTitle>
            </SheetHeader>
            {navigation}
          </SheetContent>
        </Sheet>
      )}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {loaded && (
          <div className="absolute left-3 top-3 z-10 max-w-[40%]">
            <Button
              variant="ghost"
              size="sm"
              className="max-w-full"
              aria-label={picker ? "Hide chats" : "Show chats"}
              aria-expanded={picker}
              onClick={() => setPicker((value) => !value)}
            >
              <PanelLeft />
              <span className="truncate">{current?.title || "Chats"}</span>
            </Button>
          </div>
        )}
        {!loaded && error ? (
          <div role="alert" className="p-4 text-sm">
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
            {opened.filter(id => chats.some(chat => chat.id === id)).map(id => <ConversationView key={id} sessionId={id} active={id === sessionId} />)}
          </div>
        ) : (
          <p className="p-4 text-sm text-muted-foreground">Connecting…</p>
        )}
      </div>
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
                  if (selected === deleting!.id) select(main);
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


const ConversationView = memo(function ConversationView({ sessionId, active }: { sessionId: string; active: boolean }) {
  const connection = useMemo(() => ({ baseUrl: `${window.location.origin}/workplace/api/personal/chats/${encodeURIComponent(sessionId)}/runtime/?minimal=true`, realtime: true }), [sessionId]);
  return <div className="absolute inset-0 min-h-0" style={{ visibility: active ? "visible" : "hidden" }} aria-hidden={!active} inert={!active}>
    <ChatHostProvider host={host}><TooltipProvider delayDuration={300}><UiConfigProvider connection={connection}><RPCClientProvider identity={identity}>
      <App sessionId={sessionId} active={active} omniAvatar defaultVoiceCaptions={false} />
    </RPCClientProvider></UiConfigProvider></TooltipProvider></ChatHostProvider>
  </div>;
});
