import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@omniagents/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from "@omniagents/ui/dropdown-menu";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@omniagents/ui/resizable";
import { useIsMobile } from "@omniagents/ui/hooks/use-mobile";
import { Input } from "@omniagents/ui/input";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "@omniagents/ui/sheet";
import { Toaster } from "@omniagents/ui/sonner";
import {
  Archive,
  LibraryBig,
  Factory,
  ChevronDown,
  GitBranch,
  Hash,
  Lock,
  Menu,
  MessageSquare,
  Pencil,
  Search,
  Sparkles,
  Users,
  UserRound,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  api,
  subscribe,
  type Person,
  type SlackChannel,
  type SlackMessage,
  type WireEntry,
  type WorkplaceState,
} from "./api";
import { Composer } from "./Composer";
import { CanvasApp, parseCanvasPath } from "./Canvas";
import { LibraryApp } from "./Library";
import { MemoryApp } from "./Memory";
import { ConfigDialog } from "./Config";
import { RepositoriesDialog, RepositoryView } from "./Repositories";
import { FactoryApp } from "./Factory";
import { parseFactoryRunPath } from "./factory-run-path";

// The run page carries the chat UI: loaded when a run is opened.
const PersonalApp = lazy(() => import("./Personal").then((m) => ({ default: m.PersonalApp })));
const FactoryRun = lazy(() => import("./FactoryRun").then((m) => ({ default: m.FactoryRun })));
import { FactoryModelDialog } from "./FactoryModel";
import { FactorySchedulesDialog } from "./FactorySchedules";
import { GitHubApp, parseGitHubPath } from "./GitHub";
import { LinearApp, parseLinearPath } from "./Linear";
import { AppsDialog, TokensDialog } from "./Apps";
import { MessageRow } from "./Message";
import { WorkplaceNav } from "./WorkplaceNav";
import { AppNav, NavSlot, useAdoptSlot } from "./mobile-nav";
import { PasswordDialog, PeopleDialog } from "./People";
import {
  BrowseChannels,
  ChannelBar,
  ChannelDialog,
  DraftsDialog,
  ProfileDialog,
  SearchDialog,
  TeamDialog,
} from "./SlackManage";
import { WirePanel } from "./Wire";

interface Thread {
  channel: string;
  ts: string;
}

export default function App({
  account,
  environment,
  configurable,
  factory,
  architecture,
  onSignOut,
}: {
  account: Person;
  /** development, test, staging or production: shown unless production. */
  environment: string;
  /** Development, served by `up`: the configuration page edits workplace.yml. */
  configurable: boolean;
  /** Served with a factory's workplace.yml: its model is configured here. */
  factory: boolean;
  architecture: "personal" | "specialist";
  onSignOut: () => void;
}) {
  const [state, setState] = useState<WorkplaceState | null>(null);
  // The signed-in person, as Slack knows them.
  const actingAs = account.slack_id;
  const [peopleOpen, setPeopleOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [tokensOpen, setTokensOpen] = useState(false);
  const [channelId, setChannelId] = useState<string>("");
  const [messages, setMessages] = useState<SlackMessage[]>([]);
  const [thread, setThread] = useState<Thread | null>(null);
  const [threadMessages, setThreadMessages] = useState<{
    parent: SlackMessage;
    replies: SlackMessage[];
  } | null>(null);
  const [wire, setWire] = useState<WireEntry[]>([]);
  const [wireOpen, setWireOpen] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [path, setPath] = useState(window.location.pathname);
  const [githubVersion, setGithubVersion] = useState(0);
  const [linearVersion, setLinearVersion] = useState(0);
  const [canvasVersion, setCanvasVersion] = useState(0);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [channelTab, setChannelTab] = useState<string | null>(null);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [teamOpen, setTeamOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [repositoriesOpen, setRepositoriesOpen] = useState(() => new URLSearchParams(window.location.search).has("connection"));
  const [modelOpen, setModelOpen] = useState(false);
  const [schedulesOpen, setSchedulesOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [query, setQuery] = useState("");
  // Bumped when a channel's pins or bookmarks may have changed.
  const [barVersion, setBarVersion] = useState(0);
  // A message to bring into view once its channel's messages are shown.
  const [jumpTo, setJumpTo] = useState<string | null>(null);
  // Phones: the open app's navigation; app switching lives in the bottom bar.
  const mobile = useIsMobile();
  const [navOpen, setNavOpen] = useState(false);
  const [navSlot] = useState(() => document.createElement("div"));
  const adoptSlot = useAdoptSlot(navSlot);

  const navigate = useCallback((to: string) => {
    window.history.pushState(null, "", to);
    setPath(to);
  }, []);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const libraryRoute = path === "/library" || path.startsWith("/library/") || path === "/canvas";
  const personalRoute = path === "/personal";
  const memoryRoute = path === "/memory";
  const factoryRoute = path === "/factory" || path.startsWith("/factory/");
  const canvasRoute = libraryRoute || factoryRoute || personalRoute ? null : parseCanvasPath(path);
  const linearRoute = libraryRoute || factoryRoute || personalRoute || canvasRoute ? null : parseLinearPath(path);
  const route =
    libraryRoute || factoryRoute || personalRoute || canvasRoute || linearRoute ? null : parseGitHubPath(path);
  // Going somewhere closes the drawer.
  useEffect(() => setNavOpen(false), [path, channelId]);

  const current = useRef({ channelId, thread });
  current.current = { channelId, thread };

  const loadState = useCallback(async () => {
    const next = await api.state();
    setState(next);
    setChannelId((id) =>
      id && next.slack.channels.some((c) => c.id === id)
        ? id
        : (next.slack.channels.find(
            (c) =>
              !c.is_im &&
              !c.is_mpim &&
              !c.is_archived &&
              c.members.includes(account.slack_id),
          )?.id ??
          next.slack.channels.find((c) => !c.is_im && !c.is_mpim)?.id ??
          ""),
    );
  }, []);

  const loadMessages = useCallback(async (id: string) => {
    if (!id) return;
    const { messages } = await api.messages(id);
    if (current.current.channelId === id) setMessages(messages);
  }, []);

  const loadThread = useCallback(async (t: Thread | null) => {
    if (!t) return setThreadMessages(null);
    try {
      const data = await api.thread(t.channel, t.ts);
      const now = current.current.thread;
      if (now && now.channel === t.channel && now.ts === t.ts)
        setThreadMessages(data);
    } catch {
      setThreadMessages(null);
    }
  }, []);

  useEffect(() => {
    void loadState();
    if (account.admin) void api.wire().then(({ entries }) => setWire(entries)).catch(() => setWire([]));
    else { setWire([]); setWireOpen(false); }
  }, [loadState, account.admin]);

  useEffect(() => {
    setMessages([]);
    void loadMessages(channelId);
  }, [channelId, loadMessages]);

  useEffect(() => {
    void loadThread(thread);
  }, [thread, loadThread]);

  // What you see is read: the open conversation, up to its last message.
  const marked = useRef<Record<string, string>>({});
  useEffect(() => {
    const last = messages.at(-1)?.ts;
    const open = state?.slack.channels.find((c) => c.id === channelId);
    if (!last || !open || !open.members.includes(account.slack_id)) return;
    if (marked.current[channelId] === last) return;
    marked.current[channelId] = last;
    const [count] = state?.slack.unread[channelId] ?? [0, 0];
    void api.read(channelId, last).then(
      () => (count ? loadState() : undefined),
      () => undefined,
    );
  }, [messages, channelId, state, account.slack_id, loadState]);

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === "ready") return;
        if (event.type === "canvas") {
          setCanvasVersion((v) => v + 1);
          return;
        }
        if (event.type === "linear") {
          setLinearVersion((v) => v + 1);
          return;
        }
        if (event.type === "github") {
          setGithubVersion((v) => v + 1);
          return;
        }
        if (event.type === "wire") {
          const entry = event.data;
          setWire((entries) => {
            const at = entries.findIndex((e) => e.id === entry.id);
            if (at === -1) return [...entries.slice(-499), entry];
            const next = entries.slice();
            next[at] = entry;
            return next;
          });
          if (
            entry.kind === "socket" ||
            (entry.kind === "git" && entry.status !== "pending")
          )
            void loadState();
          return;
        }
        const { kind, channel } = event.data;
        if (["channel", "user", "app", "team"].includes(kind)) void loadState();
        // New and deleted messages elsewhere change what is unread.
        if (
          ["message", "message_deleted"].includes(kind) &&
          channel &&
          channel.id !== current.current.channelId
        )
          void loadState();
        if (kind === "message_changed" || kind === "message_deleted")
          setBarVersion((v) => v + 1);
        if (channel && channel.id === current.current.channelId)
          void loadMessages(channel.id);
        const t = current.current.thread;
        if (channel && t && channel.id === t.channel) void loadThread(t);
      }),
    [loadState, loadMessages, loadThread],
  );

  const users = useMemo(
    () => new Map((state?.slack.users ?? []).map((u) => [u.id, u])),
    [state],
  );
  const channels = useMemo(
    () => new Map((state?.slack.channels ?? []).map((c) => [c.id, c])),
    [state],
  );
  const apps = useMemo(
    () => new Map((state?.slack.apps ?? []).map((a) => [a.id, a])),
    [state],
  );
  const lookup = useMemo(
    () => ({ users, channels, onChannel: setChannelId }),
    [users, channels],
  );

  if (!state) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Connecting to the workplace…
      </div>
    );
  }

  const me = users.get(actingAs);
  const channel = channels.get(channelId);
  // The sidebar lists the channels you are in; Browse finds the rest.
  const visible = state.slack.channels.filter(
    (c) =>
      !c.is_im && !c.is_mpim && !c.is_archived && c.members.includes(actingAs),
  );
  const unread = (id: string) => state.slack.unread[id] ?? [0, 0];
  const dms = state.slack.channels.filter(
    (c) => (c.is_im || c.is_mpim) && c.members.includes(actingAs),
  );
  const people = state.slack.users.filter(
    (u) => u.id !== actingAs && !u.deleted && !u.is_bot,
  );

  const label = (c: SlackChannel) =>
    c.is_im || c.is_mpim
      ? c.members
          .filter((m) => m !== actingAs)
          .map((m) => users.get(m)?.real_name ?? m)
          .join(", ") || `${me?.real_name ?? ""} (you)`
      : c.name;

  const labelOf = (id: string) => {
    const c = channels.get(id);
    if (!c) return "a conversation you are not in";
    return c.is_im || c.is_mpim ? label(c) : `#${c.name}`;
  };

  const open = (id: string) => {
    setChannelId(id);
    setThread(null);
  };

  const attach = async (file: File, comment: string, threadTs?: string) => {
    if (!channel) return;
    try {
      await api.upload({
        channel: channel.id,
        file,
        comment,
        thread_ts: threadTs,
      });
    } catch (error) {
      toast.error(`Slack said: ${(error as Error).message}`);
    }
  };

  const failed = (error: Error) => toast.error(`Slack said: ${error.message}`);

  const actions = (m: SlackMessage) => ({
    onEdit: (text: string) =>
      api.editMessage(channelId, m.ts, text).then(
        () => undefined,
        (e: Error) => {
          failed(e);
          throw e;
        },
      ),
    onDelete:
      m.user === actingAs || account.admin
        ? () => void api.deleteMessage(channelId, m.ts).catch(failed)
        : undefined,
    onPin: joined
      ? (pinned: boolean) =>
          void api
            .pin(channelId, m.ts, pinned)
            .then(() => setBarVersion((v) => v + 1), failed)
      : undefined,
    onMessage: (userId: string) => void openDm(userId),
  });

  const send = async (text: string, threadTs?: string) => {
    if (!channel) return;
    try {
      await api.post({
        channel: channel.id,
        text,
        thread_ts: threadTs,
      });
    } catch (error) {
      toast.error(`Slack said: ${(error as Error).message}`);
    }
  };

  const react = async (message: SlackMessage, name: string) => {
    const mine = message.reactions
      ?.find((r) => r.name === name)
      ?.users.includes(actingAs);
    try {
      await api.react({
        channel: channelId,
        ts: message.ts,
        emoji: name,
        remove: mine,
      });
    } catch (error) {
      toast.error(`Slack said: ${(error as Error).message}`);
    }
  };

  const openDm = async (userId: string) => {
    if (userId === actingAs) return;
    try {
      const { channel } = await api.openDm([userId]);
      await loadState();
      setChannelId(channel.id);
      setThread(null);
    } catch (error) {
      failed(error as Error);
    }
  };

  const joined = channel ? channel.members.includes(actingAs) : false;

  const channelView = (
    <main className="flex h-full min-w-0 flex-col">
      {channel ? (
        <>
          <div className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
            {channel.is_im || channel.is_mpim ? (
              <span className="truncate font-semibold">{label(channel)}</span>
            ) : (
              <button
                type="button"
                onClick={() => setChannelTab("about")}
                className="flex shrink-0 items-center gap-1 rounded-md px-1 font-semibold whitespace-nowrap hover:bg-accent"
                title="Channel details"
              >
                {channel.is_archived && (
                  <Archive className="size-3.5" />
                )}
                {channel.is_private ? "🔒" : "#"} {channel.name}
                <ChevronDown className="size-3.5" />
              </button>
            )}
            <span className="min-w-0 truncate text-xs text-muted-foreground">
              {channel.topic.value || channel.purpose.value}
            </span>
            {!channel.is_im && !channel.is_mpim && (
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                title="Members"
                onClick={() => setChannelTab("members")}
              >
                <Users /> {channel.members.length}
              </Button>
            )}
          </div>
          <ChannelBar
            channel={channel}
            member={joined}
            version={barVersion}
            lookup={lookup}
            onJump={setJumpTo}
          />
          <MessageList
            messages={messages}
            jumpTo={jumpTo}
            onJumped={() => setJumpTo(null)}
            render={(m) => (
              <MessageRow
                key={m.ts}
                message={m}
                lookup={lookup}
                apps={apps}
                actingAs={actingAs}
                onReact={(name) => void react(m, name)}
                onThread={() =>
                  setThread({ channel: channel.id, ts: m.ts })
                }
                {...actions(m)}
              />
            )}
          />
          {channel.is_archived ? (
            <div className="border-t p-4 text-center text-sm text-muted-foreground">
              You are viewing #{channel.name}, an archived channel.
            </div>
          ) : joined ? (
            <Composer
              placeholder={`Message ${channel.is_im || channel.is_mpim ? label(channel) : "#" + channel.name} as ${me?.real_name ?? ""}`}
              users={state.slack.users}
              onSend={(text) => send(text)}
              onAttach={(file, comment) => attach(file, comment)}
            />
          ) : (
            <div className="flex items-center justify-center gap-3 border-t p-4 text-sm text-muted-foreground">
              You are viewing #{channel.name}.
              <Button
                size="sm"
                onClick={() =>
                  void api.join(channel.id).then(loadState, failed)
                }
              >
                Join channel
              </Button>
            </div>
          )}
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Pick a channel
        </div>
      )}
    </main>
  );
  const threadView =
    thread && threadMessages && channel && thread.channel === channel.id ? (
      <aside className="flex h-full flex-col">
        <div className="flex h-12 shrink-0 items-center justify-between border-b px-4">
          <span className="font-semibold">Thread</span>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setThread(null)}
            title="Close the thread"
          >
            <X />
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-2">
          <MessageRow
            message={threadMessages.parent}
            lookup={lookup}
            apps={apps}
            actingAs={actingAs}
            onReact={(name) =>
              void react(threadMessages.parent, name)
            }
            {...actions(threadMessages.parent)}
          />
          <div className="mx-4 my-2 flex items-center gap-2 text-xs text-muted-foreground">
            {threadMessages.replies.length}{" "}
            {threadMessages.replies.length === 1
              ? "reply"
              : "replies"}
            <span className="h-px flex-1 bg-border" />
          </div>
          {threadMessages.replies.map((m) => (
            <MessageRow
              key={m.ts}
              message={m}
              lookup={lookup}
              apps={apps}
              actingAs={actingAs}
              onReact={(name) => void react(m, name)}
              {...actions(m)}
            />
          ))}
        </div>
        {joined && !channel.is_archived && (
          <Composer
            placeholder="Reply…"
            users={state.slack.users}
            onSend={(text) => send(text, thread.ts)}
            onAttach={(file, comment) =>
              attach(file, comment, thread.ts)
            }
          />
        )}
      </aside>
    ) : null;

  // One ordered set of destinations for the rail, phone bar, and More sheet.
  const slackRoute = !libraryRoute && !route && !linearRoute && !canvasRoute && !factoryRoute && !personalRoute && !memoryRoute;
  const sections = [
    ...(factory && architecture === "personal" && account.admin ? [{ name: "Chat", to: "/personal", icon: MessageSquare, active: personalRoute }] : []),
    ...(factory && (architecture !== "personal" || !account.admin) ? [{ name: "Factory", to: "/factory", icon: Factory, active: factoryRoute }] : []),
    { name: "Slack", to: "/", icon: Hash, active: slackRoute },
    { name: "GitHub", to: "/github", icon: GitBranch, active: !!route },
    { name: "Linear", to: "/linear", icon: Sparkles, active: !!linearRoute },
    { name: "Library", to: "/library", icon: LibraryBig, active: libraryRoute || !!canvasRoute },
    { name: "Memory", to: "/memory", icon: Archive, active: memoryRoute },
    ...(factory && architecture === "personal" && account.admin ? [{ name: "Factory", to: "/factory", icon: Factory, active: factoryRoute }] : []),
  ];
  const accountMenu = (closeNavigation: () => void) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" className="h-12 w-full flex-col gap-1 px-1" aria-label="Your account">
                <UserRound aria-hidden="true" />
                <span className="text-[11px]">Account</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side={mobile ? "top" : "right"}>
              <DropdownMenuLabel className="font-normal">
                <div className="font-medium">{account.name}</div>
                <div className="text-xs text-muted-foreground">
                  @{account.username}
                  {account.admin ? " · admin" : ""}
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
              <DropdownMenuItem onSelect={() => setProfileOpen(true)}>
                Profile
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setPeopleOpen(true)}>
                People
              </DropdownMenuItem>
              {account.admin && (
                <DropdownMenuItem onSelect={() => setTeamOpen(true)}>
                  Workspace settings
                </DropdownMenuItem>
              )}
              {account.admin && (
                <DropdownMenuItem onSelect={() => setRepositoriesOpen(true)}>Repositories</DropdownMenuItem>
              )}
              {account.admin && factory && (
                <DropdownMenuItem onSelect={() => setModelOpen(true)}>
                  Factory model
                </DropdownMenuItem>
              )}
              {account.admin && factory && (
                <DropdownMenuItem onSelect={() => setSchedulesOpen(true)}>
                  Factory schedules
                </DropdownMenuItem>
              )}
              {account.admin && configurable && (
                <DropdownMenuItem onSelect={() => setConfigOpen(true)}>
                  Configuration
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => { closeNavigation(); setConnectOpen(true); }}>
                Connections
              </DropdownMenuItem>
              {account.admin && (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>Developer tools</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent>
                    <DropdownMenuGroup>
                      <DropdownMenuItem onSelect={() => { closeNavigation(); setWireOpen(true); }}>
                        Event log
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              )}
              <DropdownMenuItem onSelect={() => setTokensOpen(true)}>
                Your tokens
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setPasswordOpen(true)}>
                Change password
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onSignOut}>Sign out</DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
  );

  return (
    <NavSlot.Provider value={navSlot}>
    <div className="flex h-[var(--workplace-viewport-height,100%)] flex-col bg-background text-foreground md:flex-row">
      <WorkplaceNav sections={sections} brand={state.slack.team.name} personalMode={architecture === "personal"} navigate={navigate}
        account={accountMenu} />
      <div className="order-first flex min-h-0 min-w-0 flex-1 flex-col md:order-none" style={personalRoute ? { paddingTop: "env(safe-area-inset-top)" } : undefined}>
      <Toaster />
      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent side="left" className="w-80 gap-0 overflow-y-auto p-0">
          <SheetTitle className="flex h-12 shrink-0 items-center gap-2 border-b px-4 text-base">
            <span className="flex size-7 items-center justify-center rounded-md bg-primary text-sm text-primary-foreground">
              {state.slack.team.name.slice(0, 1)}
            </span>
            {sections.find((a) => a.active)?.name} navigation
          </SheetTitle>
          <SheetDescription className="sr-only">Browse the current app.</SheetDescription>
          <div className="flex flex-col gap-4 p-3 text-sm">
            {slackRoute && (
              <form
                className="relative xl:hidden"
                onSubmit={(e) => {
                  e.preventDefault();
                  setNavOpen(false);
                  setQuery(searchText.trim());
                }}
              >
                <Search className="pointer-events-none absolute top-1/2 left-2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pl-8"
                  placeholder={`Search ${state.slack.team.name}`}
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                />
              </form>
            )}
            {/* The open app's navigation, below md (empty otherwise). */}
            <div ref={adoptSlot} />
          </div>
        </SheetContent>
      </Sheet>
      {!personalRoute && <header className="box-content flex h-12 shrink-0 items-center gap-2 border-b px-2 pt-[env(safe-area-inset-top)] lg:gap-3 lg:px-3">
        {(slackRoute || linearRoute) && <Button variant="ghost" size="icon" className="md:hidden"
          aria-label={`${slackRoute ? "Slack" : "Linear"} navigation`} onClick={() => setNavOpen(true)}><Menu /></Button>}
        <div className="flex min-w-0 items-center gap-3">
          <span className="truncate font-semibold">{sections.find((a) => a.active)?.name}</span>
          <span className="hidden truncate text-sm text-muted-foreground sm:inline">{state.slack.team.name}</span>
          {environment !== "production" && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">{environment}</span>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {!libraryRoute && !route && !linearRoute && !canvasRoute && !factoryRoute && !personalRoute && !memoryRoute && (
            <form
              className="relative hidden md:block"
              onSubmit={(e) => {
                e.preventDefault();
                setQuery(searchText.trim());
              }}
            >
              <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="h-8 w-40 pl-7 text-sm lg:w-56"
                placeholder={`Search ${state.slack.team.name}`}
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
              />
            </form>
          )}
        </div>
      </header>}

      {personalRoute ? (
        architecture !== "personal" ? <p className="p-6">Chat is disabled in the specialist architecture.</p> : account.admin ? <Suspense fallback={<div className="p-6">Opening Chat…</div>}><PersonalApp key={account.id} /></Suspense>
          : <p className="p-6">Chat currently requires an administrator account.</p>
      ) : factoryRoute && parseFactoryRunPath(path) != null ? (
        <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Opening the run…</div>}>
          <FactoryRun runId={parseFactoryRunPath(path)!} navigate={navigate} />
        </Suspense>
      ) : factoryRoute ? (
        <FactoryApp navigate={navigate} />
      ) : memoryRoute ? (
        <MemoryApp />
      ) : libraryRoute ? (
        <LibraryApp key={path} id={path.startsWith("/library/") ? decodeURIComponent(path.split("/")[2]) : undefined} account={account} version={canvasVersion} navigate={navigate} />
      ) : canvasRoute ? (
        <CanvasApp
          route={canvasRoute}
          account={account}
          version={canvasVersion}
          navigate={to => navigate(to === "/canvas" ? "/library" : to)}
        />
      ) : linearRoute ? (
        <LinearApp
          route={linearRoute}
          me={account.linear_id}
          version={linearVersion}
          navigate={navigate}
        />
      ) : route ? (
        <RepositoryView admin={account.admin} onSettings={() => setRepositoriesOpen(true)} standin={<GitHubApp
          route={route}
          actor={account.github_login}
          admin={account.admin}
          version={githubVersion}
          navigate={navigate}
        />} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <AppNav className="flex w-60 shrink-0 flex-col gap-4 overflow-y-auto border-r bg-muted/30 p-3 text-sm">
            <div className="flex flex-col">
              <NavItem
                active={false}
                onClick={() => setDraftsOpen(true)}
                muted={!state.slack.drafts}
              >
                <Pencil className="size-3.5" />
                Drafts and scheduled
                {!!state.slack.drafts && (
                  <span className="ml-auto text-xs">{state.slack.drafts}</span>
                )}
              </NavItem>
            </div>
            <Section title="Channels">
              <NavItem active={false} muted onClick={() => setBrowseOpen(true)}>
                <Search className="size-3.5" /> Browse channels
              </NavItem>
              <NewChannel
                onCreated={(id) => {
                  void loadState().then(() => {
                    setChannelId(id);
                    setThread(null);
                  });
                }}
              />
              {visible.map((c) => (
                <NavItem
                  key={c.id}
                  active={c.id === channelId}
                  unread={unread(c.id)}
                  onClick={() => open(c.id)}
                >
                  {c.is_private ? (
                    <Lock className="size-3.5" />
                  ) : (
                    <Hash className="size-3.5" />
                  )}
                  {c.name}
                </NavItem>
              ))}
            </Section>
            <Section title="Direct messages">
              {dms.map((c) => (
                <NavItem
                  key={c.id}
                  active={c.id === channelId}
                  unread={unread(c.id)}
                  onClick={() => open(c.id)}
                >
                  <MessageSquare className="size-3.5" />
                  {label(c)}
                </NavItem>
              ))}
              {people
                .filter(
                  (u) => !dms.some((c) => c.is_im && c.members.includes(u.id)),
                )
                .map((u) => (
                  <NavItem
                    key={u.id}
                    active={false}
                    onClick={() => void openDm(u.id)}
                    muted
                  >
                    <MessageSquare className="size-3.5" />
                    {u.real_name}
                  </NavItem>
                ))}
            </Section>
            <Section title="Apps">
              {state.slack.apps.map((a) => (
                <button
                  type="button"
                  key={a.id}
                  title={`Message ${a.name}`}
                  onClick={() => void openDm(a.user)}
                  className="flex items-start gap-2 rounded-md px-2 py-1 text-left hover:bg-accent"
                >
                  <span
                    className={`mt-1.5 size-2 shrink-0 rounded-full ${a.connected ? "bg-emerald-500" : "bg-muted-foreground/40"}`}
                  />
                  <div>
                    <div className="font-medium">{a.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {a.delivery === "socket"
                        ? a.connected
                          ? "Connected over Socket Mode"
                          : "Not connected"
                        : `Events to ${a.request_url ?? "no URL"}`}
                      {" · acts as "}@{users.get(a.user)?.name}
                    </div>
                  </div>
                </button>
              ))}
            </Section>
            <Section title="Repositories">
              {state.github.repositories.map((r) => (
                <button
                  type="button"
                  key={r.full_name}
                  onClick={() => navigate(`/${r.full_name}`)}
                  className="flex items-start gap-2 rounded-md px-2 py-1 text-left hover:bg-accent"
                  title={r.description}
                >
                  <GitBranch className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="truncate font-medium">{r.full_name}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {r.open_issues} issues · {r.open_pulls} pull requests
                    </div>
                  </div>
                </button>
              ))}
            </Section>
          </AppNav>

          {mobile ? (
            // Phones: one column; an open thread covers its channel.
            <div className="flex min-w-0 flex-1 flex-col">{threadView ?? channelView}</div>
          ) : (
            <ResizablePanelGroup
              orientation="horizontal"
              className="min-w-0 flex-1"
            >
              <ResizablePanel id="channel" minSize={320}>
                {channelView}
              </ResizablePanel>
              {threadView && (
                <>
                  <ResizableHandle withHandle />
                  <ResizablePanel
                    id="thread"
                    defaultSize={384}
                    minSize={280}
                    maxSize="70"
                    groupResizeBehavior="preserve-pixel-size"
                  >
                    {threadView}
                  </ResizablePanel>
                </>
              )}
            </ResizablePanelGroup>
          )}
        </div>
      )}

      {account.admin && wireOpen && (
        <WirePanel
          entries={wire}
          onClear={() => void api.clearWire().then(() => setWire([]))}
          onClose={() => setWireOpen(false)}
        />
      )}
      <AppsDialog
        open={connectOpen}
        onOpenChange={setConnectOpen}
        repositories={state.github.repositories.map((r) => r.full_name)}
        admin={account.admin}
      />
      <TokensDialog open={tokensOpen} onOpenChange={setTokensOpen} />
      {account.admin && factory && (
        <FactoryModelDialog open={modelOpen} onOpenChange={setModelOpen} />
      )}
      {account.admin && factory && (
        <FactorySchedulesDialog
          open={schedulesOpen}
          onOpenChange={setSchedulesOpen}
        />
      )}
      {account.admin && <RepositoriesDialog open={repositoriesOpen} onOpenChange={setRepositoriesOpen} />}
      {configurable && (
        <ConfigDialog
          open={configOpen}
          onOpenChange={(o) => {
            setConfigOpen(o);
            if (!o) void loadState();
          }}
        />
      )}
      <BrowseChannels
        open={browseOpen}
        onOpenChange={setBrowseOpen}
        channels={state.slack.channels}
        actingAs={actingAs}
        onOpen={open}
        onChanged={loadState}
      />
      {channel && !channel.is_im && !channel.is_mpim && (
        <ChannelDialog
          open={channelTab !== null}
          onOpenChange={(o) => !o && setChannelTab(null)}
          tab={channelTab ?? "about"}
          onTab={setChannelTab}
          channel={channel}
          users={users}
          actingAs={actingAs}
          isAdmin={account.admin}
          onChanged={loadState}
          onMessage={(id) => void openDm(id)}
        />
      )}
      <DraftsDialog
        open={draftsOpen}
        onOpenChange={setDraftsOpen}
        label={labelOf}
        onChanged={loadState}
      />
      <SearchDialog
        query={query}
        onClose={() => setQuery("")}
        lookup={lookup}
        label={labelOf}
        onOpenChannel={open}
        onMessage={(id) => void openDm(id)}
        onOpenMessage={(m) => {
          if (!m.channel) return;
          setChannelId(m.channel);
          if (m.thread_ts && m.thread_ts !== m.ts) {
            setThread({ channel: m.channel, ts: m.thread_ts });
            setJumpTo(m.thread_ts);
          } else {
            setThread(null);
            setJumpTo(m.ts);
          }
        }}
      />
      {me && (
        <ProfileDialog
          open={profileOpen}
          onOpenChange={setProfileOpen}
          me={me}
          account={account}
          onSaved={loadState}
        />
      )}
      {account.admin && (
        <TeamDialog
          open={teamOpen}
          onOpenChange={setTeamOpen}
          team={state.slack.team}
          channels={state.slack.channels}
          onSaved={loadState}
        />
      )}
      <PeopleDialog
        open={peopleOpen}
        onOpenChange={setPeopleOpen}
        me={account}
      />
      <PasswordDialog open={passwordOpen} onOpenChange={setPasswordOpen} />
      </div>
    </div>
    </NavSlot.Provider>
  );
}

function MessageList({
  messages,
  render,
  jumpTo,
  onJumped,
}: {
  messages: SlackMessage[];
  render: (m: SlackMessage) => React.ReactNode;
  /** A message to bring into view (and mark) once it is shown. */
  jumpTo?: string | null;
  onJumped?: () => void;
}) {
  const end = useRef<HTMLDivElement>(null);
  const last = messages.at(-1)?.ts;
  const [marked, setMarked] = useState<string | null>(null);
  useEffect(() => {
    if (!jumpTo) end.current?.scrollIntoView({ block: "end" });
  }, [last]);
  useEffect(() => {
    if (!jumpTo || !messages.some((m) => m.ts === jumpTo)) return;
    document
      .getElementById(`message-${jumpTo}`)
      ?.scrollIntoView({ block: "center" });
    setMarked(jumpTo);
    onJumped?.();
    const t = setTimeout(() => setMarked(null), 2500);
    return () => clearTimeout(t);
  }, [jumpTo, messages]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-2">
      {messages.length === 0 && (
        <div className="p-6 text-center text-sm text-muted-foreground">
          No messages yet.
        </div>
      )}
      {messages.map((m) => (
        <div
          key={m.ts}
          id={`message-${m.ts}`}
          className={
            marked === m.ts ? "bg-amber-500/15 transition-colors" : undefined
          }
        >
          {render(m)}
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 px-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </div>
      <div className="flex flex-col">{children}</div>
    </div>
  );
}

function NavItem({
  active,
  muted,
  unread,
  onClick,
  children,
}: {
  active: boolean;
  muted?: boolean;
  /** [unread messages, mentions of you]: bold, with a badge for mentions. */
  unread?: [number, number];
  onClick: () => void;
  children: React.ReactNode;
}) {
  const [count, mentions] = unread ?? [0, 0];
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-2 truncate rounded-md px-2 py-1 text-left max-md:min-h-10 ${
        active
          ? "bg-primary text-primary-foreground"
          : muted
            ? "text-muted-foreground hover:bg-accent"
            : "hover:bg-accent"
      } ${!active && count ? "font-semibold" : ""}`}
    >
      {children}
      {!active && mentions > 0 && (
        <span className="ml-auto rounded-full bg-rose-600 px-1.5 text-xs font-semibold text-white">
          {mentions}
        </span>
      )}
    </button>
  );
}

function NewChannel({ onCreated }: { onCreated: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [isPrivate, setPrivate] = useState(false);
  if (!open)
    return (
      <NavItem active={false} muted onClick={() => setOpen(true)}>
        + New channel
      </NavItem>
    );
  return (
    <form
      className="mb-1 space-y-1 px-2"
      onSubmit={(e) => {
        e.preventDefault();
        api.createChannel({ name, is_private: isPrivate }).then(
          ({ channel }) => {
            setOpen(false);
            setName("");
            onCreated(channel.id);
          },
          (err: Error) => toast.error(`Slack said: ${err.message}`),
        );
      }}
    >
      <input
        className="w-full rounded-md border bg-background px-2 py-1 text-sm"
        placeholder="channel-name"
        value={name}
        autoFocus
        onChange={(e) => setName(e.target.value)}
      />
      <label className="flex items-center gap-1 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={isPrivate}
          onChange={(e) => setPrivate(e.target.checked)}
        />
        Private
      </label>
    </form>
  );
}
