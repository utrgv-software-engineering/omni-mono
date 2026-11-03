import { McpAppFrame, extensionRequest, type AppCatalog, type InstalledApp } from "./McpAppFrame";
import { AppsLauncher } from "./AppsLauncher";
import { ExtensionsDialog } from "./Extensions";
import { Mic, Plug, LayoutGrid } from "lucide-react";
import { appTransport } from "./extensions/transport";
import { SharedApp } from "./Shared";
import { FolderOpen } from "lucide-react";
import { getConversationDraft, updateConversationDraft } from "@omniagents/client/session/conversation-drafts";
import { factoryBase, factoryPath, factoryUrl } from "./factory-scope";
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
  Monitor,
  Rocket,
  Archive,
  LibraryBig,
  Factory,
  ChevronDown,
  GitBranch,
  Hash,
  Lock,
  Menu,
  MessageSquare,
  Workflow,
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
import { Desktop, useDesktopAvailable } from "./Desktop";
import { LibraryApp } from "./Library";
import { Coolify } from "./Coolify";
import { MemoryApp } from "./Memory";
import { ConfigDialog } from "./Config";
import { RepositoriesDialog, RepositoryView, type RepositoryDialogMode } from "./Repositories";
import { FactoryApp } from "./Factory";
import { parseFactoryRunPath } from "./factory-run-path";

// The run page carries the chat UI: loaded when a run is opened.
const PersonalApp = lazy(() => import("./Personal").then((m) => ({ default: m.PersonalApp })));
const FactoryRun = lazy(() => import("./FactoryRun").then((m) => ({ default: m.FactoryRun })));
import { FactoryModelDialog } from "./FactoryModel";
import { FactorySchedulesDialog } from "./FactorySchedules";
import { AutomationsApp } from "./Automations";
import { AgentsApp } from "./Agents";
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
  onSignOut,
  embedded = false,
}: {
  account: Person;
  embedded?: boolean;
  /** development, test, staging or production: shown unless production. */
  environment: string;
  /** Development, served by `up`: the configuration page edits workplace.yml. */
  configurable: boolean;
  /** Served with a factory's workplace.yml: its model is configured here. */
  factory: boolean;
  onSignOut: () => void;
}) {
  const [state, setState] = useState<WorkplaceState | null>(null);
  const [appCatalog, setAppCatalog] = useState<AppCatalog | null>(null);
  const [extensionsOpen, setExtensionsOpen] = useState(false);
  const [recorderOpened, setRecorderOpened] = useState(false);
  const refreshApps = useCallback(async () => { if (!embedded) setAppCatalog(await extensionRequest<AppCatalog>("")); }, [embedded]);
  useEffect(() => { void refreshApps().catch(error => toast.error(error.message)); }, [refreshApps]);

  // The signed-in person, as Slack knows them.
  const desktopAvailable = useDesktopAvailable(!embedded && account.admin && factory);
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
  const [path, setPath] = useState(factoryPath());
  const [githubVersion, setGithubVersion] = useState(0);
  const [linearVersion, setLinearVersion] = useState(0);
  const [canvasVersion, setCanvasVersion] = useState(0);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [channelTab, setChannelTab] = useState<string | null>(null);
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [teamOpen, setTeamOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [repositoryDialogMode, setRepositoryDialogMode] = useState<RepositoryDialogMode>("connection");
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
    if (appTransport()) { appTransport()!.navigate(to); return; }
    window.history.pushState(null, "", factoryUrl(to));
    setPath(factoryPath());
  }, []);
  useEffect(() => {
    const onPop = () => setPath(factoryPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const [sharedDraft, setSharedDraft] = useState<{id:string;text:string}>();
  const launcherRoute = path === "/apps" || path === "/apps/";
  const extensionRoute = path.startsWith("/apps/") ? path.split("/")[2] : null;
  useEffect(() => { if (extensionRoute === "recorder") setRecorderOpened(true); }, [extensionRoute]);
  const sharedRoute = path === "/shared";
  const libraryRoute = path === "/library" || path.startsWith("/library/") || path === "/canvas";
  const coolifyRoute = path === "/coolify";
  const desktopRoute = path === "/desktop";
  const personalRoute = path === "/personal";
  const memoryRoute = path === "/memory";
  const factoryRoute = path === "/factory" || path.startsWith("/factory/");
  const automationsRoute = path === "/automations" || path.startsWith("/automations/");
  const agentsRoute = path === "/agents" || path.startsWith("/agents/");
  const canvasRoute = launcherRoute || extensionRoute || sharedRoute || libraryRoute || factoryRoute || automationsRoute || agentsRoute || personalRoute ? null : parseCanvasPath(path);
  const linearRoute = launcherRoute || extensionRoute || sharedRoute || libraryRoute || factoryRoute || automationsRoute || agentsRoute || personalRoute || canvasRoute ? null : parseLinearPath(path);
  const route =
    launcherRoute || extensionRoute || sharedRoute || libraryRoute || factoryRoute || automationsRoute || agentsRoute || personalRoute || canvasRoute || linearRoute ? null : parseGitHubPath(path);
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
    if (!embedded || !id) return;
    const { messages } = await api.messages(id);
    if (current.current.channelId === id) setMessages(messages);
  }, []);

  const loadThread = useCallback(async (t: Thread | null) => {
    if (!embedded || !t) return setThreadMessages(null);
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
    if (account.admin && !embedded) void api.wire().then(({ entries }) => setWire(entries)).catch(() => setWire([]));
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
    if (!embedded) return;
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
              insertText={sharedDraft}
              onInserted={() => setSharedDraft(undefined)}
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
  const slackRoute = !launcherRoute && !extensionRoute && !sharedRoute && !coolifyRoute && !desktopRoute && !libraryRoute && !route && !linearRoute && !canvasRoute && !factoryRoute && !automationsRoute && !agentsRoute && !personalRoute && !memoryRoute;
  const sections = [
    ...(factory && account.admin ? [{ name: "Chat", to: "/personal", icon: MessageSquare, active: personalRoute }] : []),
    ...(factory && account.admin ? [{ name: "Automations", to: "/automations", icon: Workflow, active: automationsRoute }] : []),
    ...(factory && !account.admin ? [{ name: "Factory", to: "/factory", icon: Factory, active: factoryRoute }] : []),
    ...(!embedded ? [{name: "Apps", to: "/apps", icon: LayoutGrid, active: launcherRoute}] : []),
    ...(embedded ? [{ name: "Slack", to: "/", icon: Hash, active: slackRoute }] : (appCatalog?.apps ?? []).filter(app => app.enabled && app.entrypoints.length).flatMap(app => app.entrypoints.map((entry, index) => ({
      showInNavigation: app.showInNavigation !== false, name: entry.name, to: app.path ?? `/apps/${app.id}/${index}`, icon: app.id === "slack" ? Hash : app.id === "github" ? GitBranch : app.id === "linear" ? Sparkles : app.id === "recorder" ? Mic : entry.icons?.[0]?.src?.match(/^(https:\/\/|data:image\/)/) ? (props: { "aria-hidden"?: boolean }) => <img {...props} src={entry.icons![0].src} className="size-4" alt="" /> : Plug,
      active: app.id === "slack" ? slackRoute : app.id === "github" ? !!route : app.id === "linear" ? !!linearRoute : extensionRoute === app.id && Number(path.split("/")[3] || 0) === index,
    })))) ,
    { name: "Shared", to: "/shared", icon: FolderOpen, active: sharedRoute },
    { name: "Library", to: "/library", icon: LibraryBig, active: libraryRoute || !!canvasRoute },
    ...(desktopAvailable ? [{ name: "Desktop", to: "/desktop", icon: Monitor, active: desktopRoute }] : []),
    ...(factoryBase() && account.admin ? [{ name: "Coolify", to: "/coolify", icon: Rocket, active: coolifyRoute }] : []),
    { name: "Memory", to: "/memory", icon: Archive, active: memoryRoute },
    ...(factory && account.admin ? [{ name: "Factory", to: "/factory", icon: Factory, active: factoryRoute }] : []),
  ];
  const nativeApps: InstalledApp[] = sections.filter(section => !("showInNavigation" in section) && section.to !== "/apps").map(section => ({
    id: "native:" + section.to.slice(1), name: section.name, kind: "native", enabled: true,
    path: section.to, permissions: {}, showInNavigation: appCatalog?.nativeNavigation?.[section.to.slice(1)] !== false,
    entrypoints: [{tool: "native:" + section.to.slice(1), name: section.name, uri: ""}],
  }));
  const launcherCatalog = appCatalog ? {...appCatalog, apps: [...nativeApps, ...appCatalog.apps]} : null;
  const appSurface = (id: string) => {
    const app = appCatalog?.apps.find(value => value.id === id && value.enabled);
    if (!app || !appCatalog) return <p className="p-6 text-sm text-muted-foreground">{appCatalog ? "This app is disabled." : "Opening app…"}</p>;
    const index = app.kind === "builtin" ? 0 : Number(path.split("/")[3] || 0);
    const entry = app.entrypoints[index];
    if (!entry) return <p className="p-6">App entrypoint not found.</p>;
    return <McpAppFrame key={app.id + JSON.stringify(app.permissions)} app={app} entry={entry} sandboxOrigin={appCatalog.sandboxOrigin} path={path} navigate={navigate} />;
  };
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
              <DropdownMenuItem onSelect={() => {closeNavigation();setTeamOpen(true);}}>Workspace settings</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setExtensionsOpen(true)}>Apps</DropdownMenuItem>
              {account.admin && factory && <DropdownMenuItem onSelect={() => {setRepositoryDialogMode("factory");setRepositoriesOpen(true);}}>Factory changes</DropdownMenuItem>}
              <DropdownMenuItem onSelect={() => setTokensOpen(true)}>
                Your tokens
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setPasswordOpen(true)}>
                Change password
              </DropdownMenuItem>
              {factoryBase() && <DropdownMenuItem onSelect={() => window.location.assign("/")}>Factories</DropdownMenuItem>}
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onSignOut}>Sign out</DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
  );

  return (
    <NavSlot.Provider value={navSlot}>
    <div className="flex h-[var(--workplace-viewport-height,100%)] flex-col bg-background text-foreground md:flex-row">
      {!embedded && <WorkplaceNav sections={sections.filter(section => section.to === "/apps" || ("showInNavigation" in section ? section.showInNavigation : appCatalog?.nativeNavigation?.[section.to.slice(1)] !== false))} brand={state.slack.team.name} personalMode={!!factory && !!account.admin} navigate={navigate}
        account={accountMenu} />}
      <div className="order-first flex min-h-0 min-w-0 flex-1 flex-col md:order-none" style={personalRoute ? { paddingTop: "env(safe-area-inset-top)" } : undefined}>
      <Toaster />
      {!embedded && <ExtensionsDialog admin={account.admin} open={extensionsOpen} onOpenChange={setExtensionsOpen} catalog={launcherCatalog} refresh={refreshApps} />}
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
      {!personalRoute && !desktopRoute && (embedded || !slackRoute) && <header className="box-content flex h-12 shrink-0 items-center gap-2 border-b px-2 pt-[env(safe-area-inset-top)] lg:gap-3 lg:px-3">
        {embedded && (slackRoute || linearRoute) && <Button variant="ghost" size="icon" className="md:hidden"
          aria-label={`${slackRoute ? "Slack" : "Linear"} navigation`} onClick={() => setNavOpen(true)}><Menu /></Button>}
        <div className="flex min-w-0 items-center gap-3">
          <span className="truncate font-semibold">{sections.find((a) => a.active)?.name}</span>
          <span className="hidden truncate text-sm text-muted-foreground sm:inline">{state.slack.team.name}</span>
          {environment !== "production" && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">{environment}</span>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {slackRoute && (
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

      {!embedded && recorderOpened && appCatalog?.apps.some(app => app.id === "recorder" && app.enabled) && <div className={extensionRoute === "recorder" ? "flex min-h-0 flex-1 flex-col" : "hidden"}>{appSurface("recorder")}</div>}
      {!embedded && launcherRoute ? <AppsLauncher catalog={launcherCatalog} refresh={refreshApps} navigate={navigate} manage={() => setExtensionsOpen(true)} /> : !embedded && extensionRoute ? (extensionRoute === "recorder" ? null : appSurface(extensionRoute)) : coolifyRoute ? (
        account.admin && factoryBase() ? <Coolify /> : <p className="p-6">Factory administrator access required.</p>
      ) : desktopRoute ? (
        account.admin ? <Desktop /> : <p className="p-6">Desktop requires an administrator account.</p>
      ) : personalRoute ? (
        account.admin ? <Suspense fallback={<div className="p-6">Opening Chat…</div>}><PersonalApp key={account.id} account={account} navigate={navigate} /></Suspense>
          : <p className="p-6">Chat currently requires an administrator account.</p>
      ) : automationsRoute ? (
        account.admin && factory
          ? <AutomationsApp key={path} name={path.startsWith("/automations/") ? decodeURIComponent(path.slice("/automations/".length)) : undefined} navigate={navigate} onFactorySettings={() => {setRepositoryDialogMode("factory");setRepositoriesOpen(true);}} />
          : <p className="p-6">Automations require a factory administrator.</p>
      ) : agentsRoute ? (
        account.admin && factory
          ? <AgentsApp key={path} agent={path.startsWith("/agents/") ? decodeURIComponent(path.slice("/agents/".length)) : undefined} navigate={navigate} />
          : <p className="p-6">Editing agents requires a factory administrator.</p>
      ) : factoryRoute && parseFactoryRunPath(path) != null ? (
        <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Opening the run…</div>}>
          <FactoryRun runId={parseFactoryRunPath(path)!} navigate={navigate} />
        </Suspense>
      ) : factoryRoute ? (
        <FactoryApp navigate={navigate} onModel={account.admin && factory ? () => setModelOpen(true) : undefined} onSchedules={account.admin && factory ? () => setSchedulesOpen(true) : undefined} onConfigure={account.admin ? () => {setRepositoryDialogMode("factory");setRepositoriesOpen(true);} : undefined} />
      ) : memoryRoute ? (
        <MemoryApp />
      ) : sharedRoute ? (
        <SharedApp account={account} onUse={text => {
          if (account.admin) {
            let id = "factory-personal";
            try { id = JSON.parse(localStorage.getItem((factoryBase() ? factoryBase() + ":" : "") + "workplace.chat.selected") || '"factory-personal"'); } catch { /* use main chat */ }
            const draft = getConversationDraft(id);
            updateConversationDraft(id, {text: [draft.text,text].filter(Boolean).join("\n\n")});
            navigate("/personal?chat=" + encodeURIComponent(id));
          } else { setSharedDraft({id:crypto.randomUUID(),text}); navigate("/"); }
        }} />
      ) : libraryRoute ? (
        <LibraryApp key={path} id={path.startsWith("/library/") ? decodeURIComponent(path.split("/")[2]) : undefined} account={account} version={canvasVersion} navigate={navigate} />
      ) : canvasRoute ? (
        <CanvasApp
          route={canvasRoute}
          account={account}
          version={canvasVersion}
          navigate={to => navigate(to === "/canvas" ? "/library" : to)}
        />
      ) : linearRoute && !embedded ? appSurface("linear") : linearRoute ? (
        <LinearApp
          route={linearRoute}
          me={account.linear_id}
          version={linearVersion}
          navigate={navigate}
        />
      ) : route ? (
        <RepositoryView admin={account.admin} onSettings={mode => {setRepositoryDialogMode(mode);setRepositoriesOpen(true);}} standin={repositories => !embedded ? appSurface("github") : <GitHubApp
          selectedRepositories={repositories}
          route={route}
          actor={account.github_login}
          admin={account.admin}
          version={githubVersion}
          navigate={navigate}
        />} />
      ) : !embedded ? appSurface("slack") : (
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
      {account.admin && <RepositoriesDialog mode={repositoryDialogMode} open={repositoriesOpen} onOpenChange={setRepositoriesOpen} />}
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
      <TeamDialog
          open={teamOpen}
          onOpenChange={setTeamOpen}
          team={state.slack.team}
          channels={state.slack.channels}
          admin={account.admin}
          actions={<>
            {!factoryBase() && <Button variant="outline" onClick={() => {setTeamOpen(false);setPeopleOpen(true);}}>People</Button>}
            <Button variant="outline" onClick={() => {setTeamOpen(false);setConnectOpen(true);}}>Agent connections</Button>
            {account.admin && configurable && <Button variant="outline" onClick={() => {setTeamOpen(false);setConfigOpen(true);}}>Advanced configuration</Button>}
            {account.admin && <Button variant="outline" onClick={() => {setTeamOpen(false);setWireOpen(true);}}>Event log</Button>}
          </>}
          onSaved={loadState}
        />
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
