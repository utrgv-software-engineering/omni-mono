import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Badge } from "@omniagents/ui/badge";
import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
import { Input } from "@omniagents/ui/input";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import { Textarea } from "@omniagents/ui/textarea";
import {
  Archive,
  Bell,
  BellOff,
  Bot,
  Circle,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleSlash,
  Columns3,
  FileText,
  Inbox,
  LayoutList,
  Link2,
  MessageSquare,
  Paperclip,
  Pencil,
  Plus,
  Repeat,
  Settings,
  Sparkles,
  Square,
  Target,
  Trash2,
  User,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  linear,
  type LinearComment,
  type LinearDocument,
  type LinearIssue,
  type LinearIssueRef,
  type LinearIssueSummary,
  type LinearNotification,
  type LinearProject,
  type LinearSession,
  type LinearTeam,
  type LinearUser,
  type LinearWorkspace,
} from "./api";
import { Markdown } from "./GitHub";
import { MentionsContext, MentionTextarea } from "./Mentions";
import { AppNav } from "./mobile-nav";

/** A route in the Linear part of the workplace, laid out as linear.app lays it out. */
export interface LinearRoute {
  view:
    | "team"
    | "issue"
    | "new"
    | "inbox"
    | "mine"
    | "projects"
    | "project"
    | "documents"
    | "document"
    | "cycles"
    | "cycle"
    | "settings";
  team?: string;
  identifier?: string;
  id?: string;
}

export function parseLinearPath(pathname: string): LinearRoute | null {
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] !== "linear") return null;
  const [, , kind, a, b, c] = parts;
  if (kind === "issue" && a) return { view: "issue", identifier: a };
  if (kind === "inbox") return { view: "inbox" };
  if (kind === "my-issues") return { view: "mine" };
  if (kind === "projects") return { view: "projects" };
  if (kind === "project" && a) return { view: "project", id: a };
  if (kind === "documents") return { view: "documents" };
  if (kind === "document" && a) return { view: "document", id: a };
  if (kind === "team" && a) {
    if (b === "new") return { view: "new", team: a };
    if (b === "settings") return { view: "settings", team: a };
    if (b === "cycles") return { view: "cycles", team: a };
    if (b === "cycle" && c) return { view: "cycle", team: a, id: c };
    return { view: "team", team: a };
  }
  return { view: "team" };
}

const STATE_ICON: Record<string, React.ReactNode> = {
  triage: <CircleDashed className="size-4 text-orange-500" />,
  backlog: <CircleDashed className="size-4 text-muted-foreground" />,
  unstarted: <Circle className="size-4 text-muted-foreground" />,
  started: <CircleDot className="size-4 text-amber-500" />,
  completed: <CircleCheck className="size-4 text-indigo-500" />,
  canceled: <CircleSlash className="size-4 text-muted-foreground" />,
  duplicate: <CircleSlash className="size-4 text-muted-foreground" />,
};

const ORDER = [
  "triage",
  "started",
  "unstarted",
  "backlog",
  "completed",
  "canceled",
  "duplicate",
];
const PRIORITY = ["No priority", "Urgent", "High", "Medium", "Low"];
const ESTIMATES = [0, 1, 2, 3, 5, 8, 13];
const HEALTH: Record<string, { name: string; className: string }> = {
  onTrack: { name: "On track", className: "text-emerald-600" },
  atRisk: { name: "At risk", className: "text-amber-600" },
  offTrack: { name: "Off track", className: "text-rose-600" },
};

const fail = (e: unknown) => {
  toast.error((e as Error).message);
};

function when(iso: string) {
  return new Date(iso).toLocaleString();
}

function day(date: string | null) {
  return date
    ? new Date(`${date}T00:00:00`).toLocaleDateString([], {
        month: "short",
        day: "numeric",
      })
    : "";
}

interface Props {
  route: LinearRoute;
  /** The signed-in person's Linear user. */
  me: string;
  version: number;
  navigate: (path: string) => void;
}

interface Ctx {
  workspace: LinearWorkspace;
  me: LinearUser;
  org: string;
  version: number;
  navigate: (path: string) => void;
  reload: () => Promise<void>;
}

export function LinearApp({ route, me: meId, version, navigate }: Props) {
  const [workspace, setWorkspace] = useState<LinearWorkspace | null>(null);
  const reload = useCallback(
    () => linear.workspace().then(setWorkspace, fail),
    [],
  );
  useEffect(() => {
    void reload();
  }, [version, reload]);
  if (!workspace)
    return (
      <div className="p-6 text-sm text-muted-foreground">Loading Linear…</div>
    );

  const me = workspace.users.find((u) => u.id === meId);
  if (!me)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        You have no Linear user.
      </div>
    );
  const org = workspace.organization.url_key;
  const team =
    workspace.teams.find((t) => t.key === route.team) ??
    workspace.teams.find((t) => t.member) ??
    workspace.teams[0];
  const ctx: Ctx = { workspace, me, org, version, navigate, reload };
  const yours = workspace.teams.filter((t) => t.member);
  const others = workspace.teams.filter((t) => !t.member);
  const at = (path: string, active: boolean, children: React.ReactNode) => (
    <button
      type="button"
      onClick={() => navigate(`/linear/${org}${path}`)}
      className={`flex items-center gap-2 rounded-md px-2 py-1 text-left max-md:min-h-10 ${active ? "bg-accent font-semibold" : "hover:bg-accent"}`}
    >
      {children}
    </button>
  );

  return (
    <MentionsContext.Provider
      value={workspace.users.map((u) => ({
        login: u.display_name,
        name: u.name,
      }))}
    >
      <div className="flex min-h-0 flex-1">
        <AppNav
          className="flex w-56 shrink-0 flex-col gap-0.5 overflow-y-auto border-r bg-muted/30 p-3 text-sm"
          drawerClassName="flex flex-col gap-0.5"
        >
          {at(
            "/inbox",
            route.view === "inbox",
            <>
              <Inbox className="size-4" /> Inbox
              {workspace.unread > 0 && (
                <span className="ml-auto rounded-full bg-indigo-600 px-1.5 text-xs text-white">
                  {workspace.unread}
                </span>
              )}
            </>,
          )}
          {at(
            "/my-issues",
            route.view === "mine",
            <>
              <User className="size-4" /> My issues
            </>,
          )}
          {at(
            "/projects",
            route.view === "projects" || route.view === "project",
            <>
              <Target className="size-4" /> Projects
            </>,
          )}
          {at(
            "/documents",
            route.view === "documents" || route.view === "document",
            <>
              <FileText className="size-4" /> Documents
            </>,
          )}
          {[
            { title: "Your teams", list: yours },
            { title: "Other teams", list: others },
          ].map(
            (group) =>
              group.list.length > 0 && (
                <div key={group.title} className="mt-3">
                  <div className="mb-1 px-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                    {group.title}
                  </div>
                  {group.list.map((t) => {
                    const here =
                      route.team === t.key ||
                      (!route.team &&
                        t.id === team?.id &&
                        route.view === "team");
                    return (
                      <div key={t.id}>
                        {at(
                          `/team/${t.key}`,
                          here && route.view === "team",
                          <>
                            <span className="rounded bg-indigo-500/15 px-1 text-xs text-indigo-600">
                              {t.key}
                            </span>
                            <span className="truncate">{t.name}</span>
                          </>,
                        )}
                        {here && (
                          <div className="ml-4 flex flex-col">
                            {at(
                              `/team/${t.key}/cycles`,
                              route.view === "cycles" || route.view === "cycle",
                              <>
                                <Repeat className="size-3.5" /> Cycles
                              </>,
                            )}
                            {at(
                              `/team/${t.key}/settings`,
                              route.view === "settings",
                              <>
                                <Settings className="size-3.5" /> Settings
                              </>,
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ),
          )}
          <div className="mt-2">
            <NewTeam
              onCreated={(key) => {
                void reload();
                navigate(`/linear/${org}/team/${key}`);
              }}
            />
          </div>
          <div className="mt-4 px-2 text-xs text-muted-foreground">
            You are <b>{me.name}</b>. Delegate an issue to an agent, or
            @-mention it in a comment, to start an agent session.
          </div>
        </AppNav>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {route.view === "issue" && route.identifier ? (
            <IssueView ctx={ctx} identifier={route.identifier} />
          ) : route.view === "inbox" ? (
            <InboxView ctx={ctx} />
          ) : route.view === "mine" ? (
            <IssuesPage ctx={ctx} title="My issues" mine />
          ) : route.view === "projects" ? (
            <ProjectsView ctx={ctx} />
          ) : route.view === "project" && route.id ? (
            <ProjectView ctx={ctx} id={route.id} />
          ) : route.view === "documents" ? (
            <DocumentsView ctx={ctx} />
          ) : route.view === "document" && route.id ? (
            <DocumentView ctx={ctx} id={route.id} />
          ) : !team ? (
            <div className="p-6 text-sm text-muted-foreground">
              No teams yet: make one to track issues in.
            </div>
          ) : route.view === "new" ? (
            <NewIssue ctx={ctx} team={team} />
          ) : route.view === "settings" ? (
            <TeamSettings ctx={ctx} team={team} />
          ) : route.view === "cycles" ? (
            <CyclesView ctx={ctx} team={team} />
          ) : route.view === "cycle" && route.id ? (
            <IssuesPage
              ctx={ctx}
              team={team}
              cycle={route.id}
              title={(() => {
                const c = team.cycles.find((x) => x.id === route.id);
                return c
                  ? `${team.name} · ${c.name ?? `Cycle ${c.number}`} (${day(c.starts_at)} – ${day(c.ends_at)})`
                  : team.name;
              })()}
            />
          ) : (
            <IssuesPage ctx={ctx} team={team} title={team.name} />
          )}
        </div>
      </div>
    </MentionsContext.Provider>
  );
}

// -- issue lists: filters, sorting, grouping, a board ---------------------------------------

function IssuesPage({
  ctx,
  team,
  cycle,
  mine,
  title,
}: {
  ctx: Ctx;
  team?: LinearTeam;
  cycle?: string;
  mine?: boolean;
  title: string;
}) {
  const [issues, setIssues] = useState<LinearIssueSummary[]>([]);
  const [archived, setArchived] = useState(false);
  const load = useCallback(
    () =>
      linear
        .issues({
          team: mine ? undefined : team?.key,
          cycle,
          archived: archived || undefined,
        })
        .then((r) => setIssues(r.issues), fail),
    [team?.key, cycle, mine, archived],
  );
  useEffect(() => {
    void load();
  }, [load, ctx.version]);
  const shown = mine
    ? issues.filter(
        (i) =>
          i.assignee === ctx.me.display_name ||
          i.creator === ctx.me.display_name,
      )
    : issues;
  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-4 flex items-center gap-2">
        <h1 className="text-lg font-semibold">{title}</h1>
        <label className="ml-4 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Checkbox
            checked={archived}
            onCheckedChange={(v) => setArchived(!!v)}
          />
          Archived
        </label>
        {team && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() =>
              ctx.navigate(`/linear/${ctx.org}/team/${team.key}/new`)
            }
          >
            <Plus /> New issue
          </Button>
        )}
      </div>
      <IssueList
        ctx={ctx}
        issues={shown}
        team={team}
        onChanged={load}
        showTeam={!team}
      />
    </div>
  );
}

type Group = "status" | "assignee" | "project" | "priority" | "none";
type Sort = "updated" | "created" | "priority" | "due";

function IssueList({
  ctx,
  issues,
  team,
  onChanged,
  showTeam,
}: {
  ctx: Ctx;
  issues: LinearIssueSummary[];
  team?: LinearTeam;
  onChanged: () => void;
  showTeam?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [label, setLabel] = useState("");
  const [priority, setPriority] = useState("");
  const [project, setProject] = useState("");
  const [group, setGroup] = useState<Group>("status");
  const [sort, setSort] = useState<Sort>("updated");
  const [board, setBoard] = useState(false);
  const labels = [...new Set(issues.flatMap((i) => i.labels))].sort();
  const people = ctx.workspace.users.filter((u) => !u.is_app);

  const filtered = issues
    .filter((i) => {
      if (query) {
        const q = query.toLowerCase();
        if (!`${i.identifier} ${i.title}`.toLowerCase().includes(q))
          return false;
      }
      if (status && i.state_type !== status) return false;
      if (
        assignee === "none" ? i.assignee : assignee && i.assignee !== assignee
      )
        return false;
      if (label && !i.labels.includes(label)) return false;
      if (priority && String(i.priority) !== priority) return false;
      if (project === "none" ? i.project : project && i.project?.id !== project)
        return false;
      return true;
    })
    .sort((a, b) => {
      if (sort === "created") return b.created_at.localeCompare(a.created_at);
      if (sort === "priority") return (a.priority || 9) - (b.priority || 9);
      if (sort === "due")
        return (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999");
      return b.updated_at.localeCompare(a.updated_at);
    });

  const groups: {
    key: string;
    title: React.ReactNode;
    items: LinearIssueSummary[];
  }[] =
    group === "status"
      ? ORDER.map((type) => ({
          key: type,
          title: (
            <>
              {STATE_ICON[type]}{" "}
              {filtered.find((i) => i.state_type === type)?.state ?? type}
            </>
          ),
          items: filtered.filter((i) => i.state_type === type),
        }))
      : group === "assignee"
        ? [
            ...people.map((u) => ({
              key: u.id,
              title: <>{u.name}</>,
              items: filtered.filter((i) => i.assignee === u.display_name),
            })),
            {
              key: "none",
              title: <>No assignee</>,
              items: filtered.filter((i) => !i.assignee),
            },
          ]
        : group === "project"
          ? [
              ...ctx.workspace.projects.map((p) => ({
                key: p.id,
                title: <>{p.name}</>,
                items: filtered.filter((i) => i.project?.id === p.id),
              })),
              {
                key: "none",
                title: <>No project</>,
                items: filtered.filter((i) => !i.project),
              },
            ]
          : group === "priority"
            ? [1, 2, 3, 4, 0].map((n) => ({
                key: String(n),
                title: <>{PRIORITY[n]}</>,
                items: filtered.filter((i) => i.priority === n),
              }))
            : [{ key: "all", title: <>All issues</>, items: filtered }];

  const move = (identifier: string, state: string) =>
    void linear.edit(identifier, { state }).then(onChanged, fail);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Input
          className="h-8 w-56"
          placeholder="Filter by title or ID"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Filter value={status} onChange={setStatus} label="Status">
          {ORDER.map((t) => (
            <NativeSelectOption key={t} value={t}>
              {t}
            </NativeSelectOption>
          ))}
        </Filter>
        <Filter value={assignee} onChange={setAssignee} label="Assignee">
          <NativeSelectOption value="none">No assignee</NativeSelectOption>
          {people.map((u) => (
            <NativeSelectOption key={u.id} value={u.display_name}>
              {u.name}
            </NativeSelectOption>
          ))}
        </Filter>
        <Filter value={label} onChange={setLabel} label="Label">
          {labels.map((l) => (
            <NativeSelectOption key={l} value={l}>
              {l}
            </NativeSelectOption>
          ))}
        </Filter>
        <Filter value={priority} onChange={setPriority} label="Priority">
          {PRIORITY.map((p, n) => (
            <NativeSelectOption key={p} value={String(n)}>
              {p}
            </NativeSelectOption>
          ))}
        </Filter>
        <Filter value={project} onChange={setProject} label="Project">
          <NativeSelectOption value="none">No project</NativeSelectOption>
          {ctx.workspace.projects.map((p) => (
            <NativeSelectOption key={p.id} value={p.id}>
              {p.name}
            </NativeSelectOption>
          ))}
        </Filter>
        <div className="ml-auto flex items-center gap-2">
          <NativeSelect
            className="h-8 text-xs"
            value={group}
            onChange={(e) => setGroup(e.target.value as Group)}
            aria-label="Group by"
          >
            <NativeSelectOption value="status">
              Group: status
            </NativeSelectOption>
            <NativeSelectOption value="assignee">
              Group: assignee
            </NativeSelectOption>
            <NativeSelectOption value="project">
              Group: project
            </NativeSelectOption>
            <NativeSelectOption value="priority">
              Group: priority
            </NativeSelectOption>
            <NativeSelectOption value="none">No grouping</NativeSelectOption>
          </NativeSelect>
          <NativeSelect
            className="h-8 text-xs"
            value={sort}
            onChange={(e) => setSort(e.target.value as Sort)}
            aria-label="Sort by"
          >
            <NativeSelectOption value="updated">
              Sort: updated
            </NativeSelectOption>
            <NativeSelectOption value="created">
              Sort: created
            </NativeSelectOption>
            <NativeSelectOption value="priority">
              Sort: priority
            </NativeSelectOption>
            <NativeSelectOption value="due">Sort: due date</NativeSelectOption>
          </NativeSelect>
          <div className="flex rounded-md border">
            <button
              type="button"
              title="List"
              onClick={() => setBoard(false)}
              className={`p-1.5 ${board ? "" : "bg-accent"}`}
            >
              <LayoutList className="size-4" />
            </button>
            <button
              type="button"
              title="Board"
              onClick={() => setBoard(true)}
              className={`p-1.5 ${board ? "bg-accent" : ""}`}
            >
              <Columns3 className="size-4" />
            </button>
          </div>
        </div>
      </div>
      {filtered.length === 0 && (
        <div className="text-sm text-muted-foreground">No issues.</div>
      )}
      {board ? (
        <div className="flex gap-3 overflow-x-auto pb-2">
          {(team
            ? team.states.map((s) => ({
                key: s.name,
                type: s.type,
                name: s.name,
              }))
            : ORDER.map((t) => ({ key: t, type: t, name: t }))
          ).map((col) => {
            const items = filtered.filter((i) =>
              team ? i.state === col.name : i.state_type === col.type,
            );
            return (
              <div
                key={col.key}
                className="w-64 shrink-0 rounded-md bg-muted/40 p-2"
                onDragOver={(e) => team && e.preventDefault()}
                onDrop={(e) => {
                  const id = e.dataTransfer.getData("text/plain");
                  if (team && id) move(id, col.name);
                }}
              >
                <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                  {STATE_ICON[col.type]} {col.name}
                  <span className="text-muted-foreground">{items.length}</span>
                </div>
                <div className="space-y-2">
                  {items.map((i) => (
                    <button
                      key={i.id}
                      type="button"
                      draggable={!!team}
                      onDragStart={(e) =>
                        e.dataTransfer.setData("text/plain", i.identifier)
                      }
                      onClick={() =>
                        ctx.navigate(`/linear/${ctx.org}/issue/${i.identifier}`)
                      }
                      className="block w-full rounded-md border bg-background p-2 text-left text-sm shadow-sm hover:bg-accent"
                    >
                      <div className="text-xs text-muted-foreground">
                        {i.identifier}
                      </div>
                      <div className="line-clamp-2">{i.title}</div>
                      <IssueMeta issue={i} />
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        groups
          .filter((g) => g.items.length)
          .map((g) => (
            <div key={g.key}>
              <div className="mb-1 flex items-center gap-2 rounded-md bg-muted/50 px-3 py-1 text-sm font-medium">
                {g.title}{" "}
                <span className="text-muted-foreground">{g.items.length}</span>
              </div>
              {g.items.map((i) => (
                <button
                  key={i.id}
                  type="button"
                  onClick={() =>
                    ctx.navigate(`/linear/${ctx.org}/issue/${i.identifier}`)
                  }
                  className="flex w-full items-center gap-3 border-b px-3 py-2 text-left text-sm hover:bg-accent"
                >
                  <span className="w-16 shrink-0 text-xs text-muted-foreground">
                    {i.identifier}
                  </span>
                  {STATE_ICON[i.state_type]}
                  <span className="truncate">
                    {i.parent && (
                      <span className="text-muted-foreground">
                        {i.parent.identifier} ›{" "}
                      </span>
                    )}
                    {i.title}
                  </span>
                  {showTeam && (
                    <span className="text-xs text-muted-foreground">
                      {i.team}
                    </span>
                  )}
                  <span className="ml-auto shrink-0">
                    <IssueMeta issue={i} />
                  </span>
                </button>
              ))}
            </div>
          ))
      )}
    </div>
  );
}

function Filter({
  value,
  onChange,
  label,
  children,
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <NativeSelect
      className={`h-8 text-xs ${value ? "font-semibold" : ""}`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
    >
      <NativeSelectOption value="">{label}: any</NativeSelectOption>
      {children}
    </NativeSelect>
  );
}

function IssueMeta({ issue: i }: { issue: LinearIssueSummary }) {
  const overdue =
    i.due_date &&
    i.due_date < new Date().toISOString().slice(0, 10) &&
    !["completed", "canceled", "duplicate"].includes(i.state_type);
  return (
    <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      {i.priority > 0 && <span>{PRIORITY[i.priority]}</span>}
      {i.labels.map((l) => (
        <Badge key={l} variant="outline">
          {l}
        </Badge>
      ))}
      {i.project && (
        <span className="flex items-center gap-1">
          <Target className="size-3" /> {i.project.name}
        </span>
      )}
      {i.cycle && (
        <span className="flex items-center gap-1">
          <Repeat className="size-3" />{" "}
          {i.cycle.name ?? `Cycle ${i.cycle.number}`}
        </span>
      )}
      {i.estimate !== null && <span>{i.estimate} pt</span>}
      {i.due_date && (
        <span className={overdue ? "text-rose-600" : ""}>
          Due {day(i.due_date)}
        </span>
      )}
      {i.sub_issues > 0 && <span>{i.sub_issues} sub-issues</span>}
      {i.delegate && (
        <span className="flex items-center gap-1 text-indigo-600">
          <Bot className="size-3.5" /> {i.delegate}
        </span>
      )}
      {i.assignee && <span>@{i.assignee}</span>}
      {i.comments > 0 && (
        <span className="flex items-center gap-1">
          <MessageSquare className="size-3.5" /> {i.comments}
        </span>
      )}
    </span>
  );
}

function NewIssue({
  ctx,
  team,
  parent,
  onCreated,
}: {
  ctx: Ctx;
  team: LinearTeam;
  /** A sub-issue of this one. */
  parent?: string;
  onCreated?: () => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState(0);
  const [labels, setLabels] = useState<string[]>([]);
  const create = async () => {
    try {
      const issue = await linear.create({
        team: team.key,
        title,
        description,
        priority,
        labels,
      });
      if (parent) await linear.edit(issue.identifier, { parent });
      if (onCreated) {
        setTitle("");
        setDescription("");
        onCreated();
      } else ctx.navigate(`/linear/${ctx.org}/issue/${issue.identifier}`);
    } catch (e) {
      fail(e);
    }
  };
  return (
    <div className={parent ? "space-y-2" : "mx-auto max-w-3xl space-y-3 p-4 md:p-6"}>
      {!parent && (
        <h1 className="font-semibold">
          New issue in {team.name}, as {ctx.me.name}
        </h1>
      )}
      <Input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={parent ? "Sub-issue title" : "Issue title"}
      />
      <MentionTextarea
        value={description}
        onValue={setDescription}
        rows={parent ? 3 : 8}
        placeholder="Add description… (markdown)"
      />
      <div className="flex flex-wrap items-center gap-2">
        <NativeSelect
          value={String(priority)}
          onChange={(e) => setPriority(Number(e.target.value))}
          className="h-8 w-40 text-sm"
        >
          {PRIORITY.map((p, n) => (
            <NativeSelectOption key={p} value={String(n)}>
              {p}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <LabelPicker team={team} value={labels} onChange={setLabels} />
      </div>
      <div className="flex justify-end">
        <Button
          size={parent ? "sm" : "default"}
          disabled={!title.trim()}
          onClick={() => void create()}
        >
          {parent ? "Add sub-issue" : "Create issue"}
        </Button>
      </div>
    </div>
  );
}

function LabelPicker({
  team,
  value,
  onChange,
}: {
  team: LinearTeam;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {team.labels
        .filter((l) => !l.retired || value.includes(l.name))
        .map((l) => {
          const on = value.includes(l.name);
          return (
            <button
              key={l.id}
              type="button"
              onClick={() =>
                onChange(
                  on ? value.filter((v) => v !== l.name) : [...value, l.name],
                )
              }
              className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${on ? "font-semibold" : "opacity-60"}`}
            >
              <span
                className="size-2 rounded-full"
                style={{ background: l.color }}
              />{" "}
              {l.name}
            </button>
          );
        })}
    </div>
  );
}

// -- an issue --------------------------------------------------------------------------------

function useAllIssues(version: number) {
  const [all, setAll] = useState<LinearIssueSummary[]>([]);
  useEffect(() => {
    void linear.issues({}).then((r) => setAll(r.issues), fail);
  }, [version]);
  return all;
}

function IssuePicker({
  issues,
  exclude,
  placeholder,
  onPick,
}: {
  issues: LinearIssueSummary[];
  exclude: string[];
  placeholder: string;
  onPick: (identifier: string) => void;
}) {
  return (
    <NativeSelect
      className="h-7 w-full text-xs"
      value=""
      onChange={(e) => e.target.value && onPick(e.target.value)}
    >
      <NativeSelectOption value="">{placeholder}</NativeSelectOption>
      {issues
        .filter((i) => !exclude.includes(i.identifier))
        .map((i) => (
          <NativeSelectOption key={i.id} value={i.identifier}>
            {i.identifier} {i.title}
          </NativeSelectOption>
        ))}
    </NativeSelect>
  );
}

function IssueView({ ctx, identifier }: { ctx: Ctx; identifier: string }) {
  const [issue, setIssue] = useState<LinearIssue | null>(null);
  const [missing, setMissing] = useState(false);
  const [editingTitle, setEditingTitle] = useState<string | null>(null);
  const [editingBody, setEditingBody] = useState<string | null>(null);
  const [addingSub, setAddingSub] = useState(false);
  const [link, setLink] = useState<{ title: string; url: string } | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const all = useAllIssues(ctx.version);
  const load = useCallback(
    () =>
      linear.issue(identifier).then(
        (i) => {
          setIssue(i);
          setMissing(false);
        },
        () => setMissing(true),
      ),
    [identifier],
  );
  useEffect(() => {
    void load();
  }, [load, ctx.version]);
  const team = useMemo(
    () => ctx.workspace.teams.find((t) => t.key === issue?.team),
    [ctx.workspace.teams, issue?.team],
  );
  if (missing)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        {identifier} does not exist (or was deleted).
      </div>
    );
  if (!issue || !team)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Loading {identifier}…
      </div>
    );

  const edit = (body: Parameters<typeof linear.edit>[1]) =>
    void linear.edit(identifier, body).then(load, fail);
  const agents = ctx.workspace.users.filter((u) => u.is_app);
  const people = ctx.workspace.users.filter((u) => !u.is_app);
  const roots = issue.comments.filter((c) => !c.parent_id);
  const replies = (id: string) =>
    issue.comments.filter((c) => c.parent_id === id);
  const project = ctx.workspace.projects.find(
    (p) => p.id === issue.project?.id,
  );
  const related = [
    ...issue.relations.blocks.map((r) => r.identifier),
    ...issue.relations.blocked_by.map((r) => r.identifier),
    ...issue.relations.related.map((r) => r.identifier),
  ];
  const nav = (path: string) => ctx.navigate(`/linear/${ctx.org}${path}`);

  return (
    <div className="mx-auto grid max-w-6xl gap-6 p-4 md:p-6 md:grid-cols-[1fr_260px]">
      <div className="min-w-0 space-y-4">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <button
            type="button"
            className="hover:underline"
            onClick={() => nav(`/team/${team.key}`)}
          >
            {team.name}
          </button>
          ›
          {issue.parent && (
            <>
              <button
                type="button"
                className="hover:underline"
                onClick={() => nav(`/issue/${issue.parent!.identifier}`)}
              >
                {issue.parent.identifier}
              </button>
              ›
            </>
          )}
          {issue.identifier}
          {issue.archived && (
            <Badge variant="outline" className="ml-2">
              Archived
            </Badge>
          )}
        </div>
        {editingTitle !== null ? (
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void linear.edit(identifier, { title: editingTitle }).then(() => {
                setEditingTitle(null);
                void load();
              }, fail);
            }}
          >
            <Input
              autoFocus
              className="text-lg"
              value={editingTitle}
              onChange={(e) => setEditingTitle(e.target.value)}
            />
            <Button type="submit" size="sm">
              Save
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setEditingTitle(null)}
            >
              Cancel
            </Button>
          </form>
        ) : (
          <h1
            className="group flex items-start gap-2 text-2xl font-semibold"
            onDoubleClick={() => setEditingTitle(issue.title)}
          >
            {issue.title}
            <button
              type="button"
              title="Edit the title"
              onClick={() => setEditingTitle(issue.title)}
              className="mt-2 opacity-0 group-hover:opacity-100"
            >
              <Pencil className="size-4 text-muted-foreground" />
            </button>
          </h1>
        )}
        {editingBody !== null ? (
          <div className="space-y-2">
            <MentionTextarea
              value={editingBody}
              onValue={setEditingBody}
              rows={10}
              placeholder="Add description… (markdown)"
            />
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditingBody(null)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={() =>
                  void linear
                    .edit(identifier, { description: editingBody })
                    .then(() => {
                      setEditingBody(null);
                      void load();
                    }, fail)
                }
              >
                Save
              </Button>
            </div>
          </div>
        ) : (
          <div className="group relative">
            {issue.description ? (
              <Markdown text={issue.description} />
            ) : (
              <p className="text-sm text-muted-foreground">No description.</p>
            )}
            <Button
              size="xs"
              variant="ghost"
              className="absolute top-0 right-0 opacity-0 group-hover:opacity-100"
              onClick={() => setEditingBody(issue.description)}
            >
              <Pencil /> Edit
            </Button>
          </div>
        )}

        <section className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            Attachments
            <span className="ml-auto flex gap-1">
              <input
                ref={picker}
                type="file"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file)
                    void linear.attachFile(identifier, file).then(load, fail);
                }}
              />
              <Button
                size="xs"
                variant="outline"
                onClick={() => picker.current?.click()}
              >
                <Paperclip /> File
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setLink({ title: "", url: "" })}
              >
                <Link2 /> Link
              </Button>
            </span>
          </div>
          {link && (
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void linear
                  .attachLink(identifier, link.title || link.url, link.url)
                  .then(() => {
                    setLink(null);
                    void load();
                  }, fail);
              }}
            >
              <Input
                className="h-8"
                placeholder="Pull request"
                value={link.title}
                onChange={(e) => setLink({ ...link, title: e.target.value })}
              />
              <Input
                className="h-8"
                placeholder="https://example.com"
                value={link.url}
                onChange={(e) => setLink({ ...link, url: e.target.value })}
              />
              <Button size="sm" type="submit" disabled={!link.url}>
                Add
              </Button>
              <Button
                size="sm"
                variant="ghost"
                type="button"
                onClick={() => setLink(null)}
              >
                Cancel
              </Button>
            </form>
          )}
          {issue.attachments.length === 0 && !link && (
            <p className="text-xs text-muted-foreground">None.</p>
          )}
          {issue.attachments.map((a) => (
            <div key={a.id} className="group flex items-start gap-2">
              {(a.content_type ?? "").startsWith("image/") ? (
                <a
                  href={a.url}
                  target="_blank"
                  rel="noreferrer"
                  className="block w-fit"
                >
                  <img
                    src={a.url}
                    alt={a.title}
                    className="max-h-72 rounded-md border"
                  />
                </a>
              ) : (
                <a
                  href={a.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-sm text-sky-600 underline"
                >
                  {a.file ? (
                    <Paperclip className="size-3.5" />
                  ) : (
                    <Link2 className="size-3.5" />
                  )}
                  {a.title}
                </a>
              )}
              <button
                type="button"
                title="Remove"
                className="opacity-0 group-hover:opacity-100"
                onClick={() => void linear.detach(a.id).then(load, fail)}
              >
                <X className="size-3.5 text-muted-foreground" />
              </button>
            </div>
          ))}
        </section>

        <section className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold">
            Sub-issues
            <Button
              size="xs"
              variant="outline"
              className="ml-auto"
              onClick={() => setAddingSub((v) => !v)}
            >
              <Plus /> Add
            </Button>
          </div>
          {issue.sub_issues.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => nav(`/issue/${s.identifier}`)}
              className="flex w-full items-center gap-2 rounded-md border px-2 py-1 text-left text-sm hover:bg-accent"
            >
              {STATE_ICON[s.state_type]}
              <span className="text-xs text-muted-foreground">
                {s.identifier}
              </span>
              {s.title}
            </button>
          ))}
          {issue.sub_issues.length === 0 && !addingSub && (
            <p className="text-xs text-muted-foreground">None.</p>
          )}
          {addingSub && (
            <div className="rounded-md border p-3">
              <NewIssue
                ctx={ctx}
                team={team}
                parent={issue.identifier}
                onCreated={() => {
                  setAddingSub(false);
                  void load();
                }}
              />
            </div>
          )}
        </section>

        {issue.documents.length > 0 && (
          <section className="space-y-1">
            <div className="text-sm font-semibold">Documents</div>
            {issue.documents.map((d) => (
              <button
                key={d.id}
                type="button"
                onClick={() => nav(`/document/${d.id}`)}
                className="flex items-center gap-2 text-sm text-sky-600 hover:underline"
              >
                <FileText className="size-3.5" /> {d.title}
              </button>
            ))}
          </section>
        )}

        <div className="border-t pt-4 text-sm font-semibold">Activity</div>
        {roots.map((c) => {
          const session = issue.sessions.find((s) => s.comment_id === c.id);
          return (
            <Thread
              key={c.id}
              root={c}
              replies={replies(c.id)}
              session={session}
              me={ctx.me}
              onChanged={() => void load()}
              onReply={(body) =>
                void linear
                  .comment(identifier, { body, parent_id: c.id })
                  .then(load, fail)
              }
            />
          );
        })}
        <NewComment
          me={ctx.me}
          onSend={(body) =>
            void linear.comment(identifier, { body }).then(load, fail)
          }
        />
      </div>

      <aside className="space-y-4 text-sm">
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            className="flex-1"
            onClick={() => edit({ subscribed: !issue.subscribed })}
            title={
              issue.subscribed
                ? "You are notified of its changes"
                : "Get notified of its changes"
            }
          >
            {issue.subscribed ? <BellOff /> : <Bell />}
            {issue.subscribed ? "Unsubscribe" : "Subscribe"}
          </Button>
        </div>
        <Field label="Status">
          <NativeSelect
            value={issue.state}
            onChange={(e) => edit({ state: e.target.value })}
            className="h-8 w-full text-sm"
          >
            {team.states.map((s) => (
              <NativeSelectOption key={s.id} value={s.name}>
                {s.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Priority">
          <NativeSelect
            value={String(issue.priority)}
            onChange={(e) => edit({ priority: Number(e.target.value) })}
            className="h-8 w-full text-sm"
          >
            {PRIORITY.map((p, n) => (
              <NativeSelectOption key={p} value={String(n)}>
                {p}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Assignee">
          <NativeSelect
            value={
              people.find((u) => u.display_name === issue.assignee)?.id ?? ""
            }
            onChange={(e) =>
              edit(
                e.target.value
                  ? { assignee: e.target.value }
                  : { unassign: true },
              )
            }
            className="h-8 w-full text-sm"
          >
            <NativeSelectOption value="">No one</NativeSelectOption>
            {people.map((u) => (
              <NativeSelectOption key={u.id} value={u.id}>
                {u.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Delegate to agent">
          <NativeSelect
            value={
              agents.find((u) => u.display_name === issue.delegate)?.id ?? ""
            }
            onChange={(e) =>
              edit(
                e.target.value
                  ? { delegate: e.target.value }
                  : { undelegate: true },
              )
            }
            className="h-8 w-full text-sm"
          >
            <NativeSelectOption value="">No agent</NativeSelectOption>
            {agents.map((u) => (
              <NativeSelectOption key={u.id} value={u.id}>
                {u.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Labels">
          <LabelPicker
            team={team}
            value={issue.labels}
            onChange={(labels) => edit({ labels })}
          />
        </Field>
        <Field label="Project">
          <NativeSelect
            value={issue.project?.id ?? ""}
            onChange={(e) => edit({ project: e.target.value || null })}
            className="h-8 w-full text-sm"
          >
            <NativeSelectOption value="">No project</NativeSelectOption>
            {ctx.workspace.projects.map((p) => (
              <NativeSelectOption key={p.id} value={p.id}>
                {p.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        {project && (
          <MilestoneField
            projectId={project.id}
            value={issue.milestone?.id ?? ""}
            version={ctx.version}
            onChange={(milestone) => edit({ milestone })}
          />
        )}
        <Field label="Cycle">
          <NativeSelect
            value={issue.cycle?.id ?? ""}
            onChange={(e) => edit({ cycle: e.target.value || null })}
            className="h-8 w-full text-sm"
          >
            <NativeSelectOption value="">No cycle</NativeSelectOption>
            {team.cycles.map((c) => (
              <NativeSelectOption key={c.id} value={c.id}>
                {c.name ?? `Cycle ${c.number}`} ({day(c.starts_at)} –{" "}
                {day(c.ends_at)}){c.current ? " · current" : ""}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Estimate">
          <NativeSelect
            value={issue.estimate === null ? "" : String(issue.estimate)}
            onChange={(e) =>
              edit({
                estimate: e.target.value === "" ? null : Number(e.target.value),
              })
            }
            className="h-8 w-full text-sm"
          >
            <NativeSelectOption value="">No estimate</NativeSelectOption>
            {[
              ...new Set([
                ...ESTIMATES,
                ...(issue.estimate === null ? [] : [issue.estimate]),
              ]),
            ]
              .sort((a, b) => a - b)
              .map((n) => (
                <NativeSelectOption key={n} value={String(n)}>
                  {n} points
                </NativeSelectOption>
              ))}
          </NativeSelect>
        </Field>
        <Field label="Due date">
          <div className="flex gap-1">
            <Input
              type="date"
              className="h-8"
              value={issue.due_date ?? ""}
              onChange={(e) => edit({ due_date: e.target.value || null })}
            />
            {issue.due_date && (
              <Button
                size="icon-sm"
                variant="ghost"
                title="Clear the due date"
                onClick={() => edit({ due_date: null })}
              >
                <X />
              </Button>
            )}
          </div>
        </Field>
        <Field label="Parent issue">
          {issue.parent ? (
            <RelationRow
              item={issue.parent}
              onOpen={() => nav(`/issue/${issue.parent!.identifier}`)}
              onRemove={() => edit({ parent: null })}
            />
          ) : (
            <IssuePicker
              issues={all.filter((i) => i.team === issue.team)}
              exclude={[
                issue.identifier,
                ...issue.sub_issues.map((s) => s.identifier),
              ]}
              placeholder="Set a parent…"
              onPick={(id) => edit({ parent: id })}
            />
          )}
        </Field>
        <Field label="Relations">
          <div className="space-y-2">
            {(
              [
                ["Blocks", issue.relations.blocks, "remove_blocks", "blocks"],
                [
                  "Blocked by",
                  issue.relations.blocked_by,
                  "remove_blocked_by",
                  "blocked_by",
                ],
                [
                  "Related",
                  issue.relations.related,
                  "remove_related",
                  "related",
                ],
              ] as const
            ).map(([title, items, removeKey, addKey]) => (
              <div key={title}>
                <div className="text-xs text-muted-foreground">{title}</div>
                {items.map((r) => (
                  <RelationRow
                    key={r.identifier}
                    item={r}
                    onOpen={() => nav(`/issue/${r.identifier}`)}
                    onRemove={() => edit({ [removeKey]: [r.identifier] })}
                  />
                ))}
                <IssuePicker
                  issues={all}
                  exclude={[issue.identifier, ...related]}
                  placeholder={`Add (${title.toLowerCase()})…`}
                  onPick={(id) => edit({ [addKey]: [id] })}
                />
              </div>
            ))}
            <div>
              <div className="text-xs text-muted-foreground">Duplicate of</div>
              {issue.relations.duplicate_of ? (
                <RelationRow
                  item={issue.relations.duplicate_of}
                  onOpen={() =>
                    nav(`/issue/${issue.relations.duplicate_of!.identifier}`)
                  }
                  onRemove={() => edit({ duplicate_of: null })}
                />
              ) : (
                <IssuePicker
                  issues={all}
                  exclude={[issue.identifier]}
                  placeholder="Mark as a duplicate of…"
                  onPick={(id) => edit({ duplicate_of: id })}
                />
              )}
              {issue.relations.duplicates.map((r) => (
                <div
                  key={r.identifier}
                  className="text-xs text-muted-foreground"
                >
                  Duplicated by{" "}
                  <button
                    type="button"
                    className="hover:underline"
                    onClick={() => nav(`/issue/${r.identifier}`)}
                  >
                    {r.identifier}
                  </button>
                </div>
              ))}
            </div>
          </div>
        </Field>
        <div className="flex flex-col gap-2 border-t pt-3">
          <Button
            size="sm"
            variant="outline"
            onClick={() => edit({ archived: !issue.archived })}
          >
            <Archive /> {issue.archived ? "Unarchive" : "Archive"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive"
            onClick={() => {
              if (
                window.confirm(
                  `Delete ${issue.identifier}, its comments and agent sessions? This can't be undone.`,
                )
              )
                void linear
                  .deleteIssue(issue.identifier)
                  .then(() => nav(`/team/${team.key}`), fail);
            }}
          >
            <Trash2 /> Delete
          </Button>
        </div>
      </aside>
    </div>
  );
}

function RelationRow({
  item,
  onOpen,
  onRemove,
}: {
  item: LinearIssueRef;
  onOpen: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="group flex items-center gap-1 text-xs">
      {item.state_type && STATE_ICON[item.state_type]}
      <button
        type="button"
        onClick={onOpen}
        className="truncate hover:underline"
      >
        {item.identifier} {item.title}
      </button>
      <button
        type="button"
        title="Remove"
        className="ml-auto opacity-0 group-hover:opacity-100"
        onClick={onRemove}
      >
        <X className="size-3" />
      </button>
    </div>
  );
}

function MilestoneField({
  projectId,
  value,
  version,
  onChange,
}: {
  projectId: string;
  value: string;
  version: number;
  onChange: (milestone: string | null) => void;
}) {
  const [milestones, setMilestones] = useState<LinearProject["milestones"]>([]);
  useEffect(() => {
    void linear
      .project(projectId)
      .then((p) => setMilestones(p.milestones), fail);
  }, [projectId, version]);
  if (!milestones.length) return null;
  return (
    <Field label="Milestone">
      <NativeSelect
        value={value}
        onChange={(e) => onChange(e.target.value || null)}
        className="h-8 w-full text-sm"
      >
        <NativeSelectOption value="">No milestone</NativeSelectOption>
        {milestones.map((m) => (
          <NativeSelectOption key={m.id} value={m.id}>
            {m.name}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </Field>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

const SESSION_STATUS: Record<string, string> = {
  pending: "Waiting for the agent",
  active: "Working",
  awaitingInput: "Needs your input",
  complete: "Done",
  error: "Failed",
  stale: "The agent did not respond",
};

function Thread({
  root,
  replies,
  session,
  me,
  onReply,
  onChanged,
}: {
  root: LinearComment;
  replies: LinearComment[];
  session?: LinearSession;
  me: LinearUser;
  onReply: (body: string) => void;
  onChanged: () => void;
}) {
  const [text, setText] = useState("");
  // A session's thread interleaves people's replies (prompts) with the agent's activities.
  const entries = [
    ...replies.map((c) => ({
      at: c.at,
      key: c.id,
      node: <CommentRow comment={c} me={me} onChanged={onChanged} />,
    })),
    ...(session?.activities ?? [])
      .filter((a) => a.type !== "prompt" || a.signal === "stop")
      .map((a) => ({
        at: a.at,
        key: a.id,
        node:
          a.signal === "stop" ? (
            <div className="text-xs text-muted-foreground">
              {a.user} stopped the agent
            </div>
          ) : (
            <ActivityRow activity={a} />
          ),
      })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  const running =
    session &&
    !session.stopped &&
    ["pending", "active", "awaitingInput"].includes(session.status);
  return (
    <div className="rounded-md border">
      {session && (
        <div className="flex items-center gap-2 border-b bg-indigo-500/5 px-3 py-1.5 text-xs">
          <Sparkles className="size-3.5 text-indigo-600" />
          <b>{session.app}</b> agent session
          <Badge variant="outline" className="ml-auto">
            {session.stopped &&
            running === false &&
            session.status !== "complete"
              ? "Stopped"
              : (SESSION_STATUS[session.status] ?? session.status)}
          </Badge>
          {running && (
            <Button
              size="xs"
              variant="outline"
              onClick={() =>
                void linear.stopSession(session.id).then(onChanged, fail)
              }
            >
              <Square /> Stop
            </Button>
          )}
        </div>
      )}
      {root.body && (
        <div className="px-3 py-2">
          <CommentRow comment={root} me={me} onChanged={onChanged} />
        </div>
      )}
      <div className="space-y-2 px-3 pb-2">
        {entries.map((e) => (
          <div key={e.key}>{e.node}</div>
        ))}
      </div>
      <div className="flex gap-2 border-t p-2">
        <div className="flex-1">
          <MentionTextarea
            value={text}
            onValue={setText}
            rows={1}
            placeholder={session ? "Reply to the agent…" : "Reply…"}
            className="min-h-8 resize-none py-1.5"
          />
        </div>
        <Button
          size="sm"
          disabled={!text.trim()}
          onClick={() => {
            onReply(text);
            setText("");
          }}
        >
          Reply
        </Button>
      </div>
    </div>
  );
}

function CommentRow({
  comment,
  me,
  onChanged,
}: {
  comment: LinearComment;
  me: LinearUser;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const mine = comment.user_id === me.id;
  return (
    <div className="group text-sm">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <b className="text-foreground">{comment.author_name}</b> ·{" "}
        {when(comment.at)}
        {comment.edited && <span>(edited)</span>}
        {mine && editing === null && (
          <span className="ml-auto hidden gap-1 group-hover:flex">
            <button
              type="button"
              title="Edit"
              onClick={() => setEditing(comment.body)}
            >
              <Pencil className="size-3.5" />
            </button>
            <button
              type="button"
              title="Delete"
              onClick={() => {
                if (window.confirm("Delete this comment and its replies?"))
                  void linear.deleteComment(comment.id).then(onChanged, fail);
              }}
            >
              <Trash2 className="size-3.5 text-destructive" />
            </button>
          </span>
        )}
      </div>
      {editing !== null ? (
        <div className="mt-1 space-y-2">
          <Textarea
            rows={3}
            value={editing}
            autoFocus
            onChange={(e) => setEditing(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!editing.trim()}
              onClick={() =>
                void linear.editComment(comment.id, editing).then(() => {
                  setEditing(null);
                  onChanged();
                }, fail)
              }
            >
              Save
            </Button>
          </div>
        </div>
      ) : (
        <Markdown text={comment.body} />
      )}
    </div>
  );
}

function ActivityRow({
  activity,
}: {
  activity: LinearSession["activities"][number];
}) {
  const c = activity.content;
  const label =
    {
      thought: "Thinking",
      action: "Action",
      response: "Response",
      elicitation: "Question",
      error: "Error",
    }[activity.type] ?? activity.type;
  return (
    <div
      className={`rounded-md px-2 py-1 text-sm ${activity.type === "error" ? "bg-red-500/10" : activity.type === "response" ? "bg-indigo-500/10" : "bg-muted/40"}`}
    >
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <Bot className="size-3.5" /> {activity.user} · {label}
      </div>
      {activity.type === "action" ? (
        <div className="font-mono text-xs">
          {c.action} {c.parameter}
          {c.result && (
            <div className="text-muted-foreground">→ {c.result}</div>
          )}
        </div>
      ) : (
        <Markdown text={c.body ?? ""} />
      )}
    </div>
  );
}

function NewComment({
  me,
  onSend,
}: {
  me: LinearUser;
  onSend: (body: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div className="space-y-2">
      <MentionTextarea
        value={text}
        onValue={setText}
        rows={3}
        placeholder={`Comment as ${me.name}…`}
      />
      <div className="flex justify-end">
        <Button
          size="sm"
          disabled={!text.trim()}
          onClick={() => {
            onSend(text);
            setText("");
          }}
        >
          Comment
        </Button>
      </div>
    </div>
  );
}

function NewTeam({ onCreated }: { onCreated: (key: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="w-full rounded-md px-2 py-1 text-left text-muted-foreground hover:bg-accent"
      >
        + New team
      </button>
    );
  return (
    <form
      className="space-y-2 px-2"
      onSubmit={(e) => {
        e.preventDefault();
        linear.createTeam({ key, name }).then(
          (team) => {
            setOpen(false);
            setName("");
            setKey("");
            onCreated(team.key);
          },
          (err: Error) => toast.error(err.message),
        );
      }}
    >
      <Input
        placeholder="Engineering"
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          if (!key || key === name.slice(0, 3).toUpperCase())
            setKey(
              e.target.value
                .replace(/[^A-Za-z0-9]/g, "")
                .slice(0, 3)
                .toUpperCase(),
            );
        }}
      />
      <Input
        placeholder="ENG"
        value={key}
        onChange={(e) => setKey(e.target.value.toUpperCase())}
      />
      <div className="flex justify-end gap-1">
        <Button
          type="button"
          size="xs"
          variant="ghost"
          onClick={() => setOpen(false)}
        >
          Cancel
        </Button>
        <Button type="submit" size="xs" disabled={!name.trim() || !key.trim()}>
          Create
        </Button>
      </div>
    </form>
  );
}

// -- a team's settings ------------------------------------------------------------------------

const STATUS_TYPES = [
  "triage",
  "backlog",
  "unstarted",
  "started",
  "completed",
  "canceled",
  "duplicate",
];

function TeamSettings({ ctx, team }: { ctx: Ctx; team: LinearTeam }) {
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description);
  const [status, setStatus] = useState({
    name: "",
    type: "unstarted",
    color: "#e2e2e2",
  });
  const [label, setLabel] = useState({ name: "", color: "#5e6ad2" });
  const [member, setMember] = useState("");
  useEffect(() => {
    setName(team.name);
    setDescription(team.description);
  }, [team.id, team.name, team.description]);
  const done = () => void ctx.reload();
  const statuses = [...team.states].sort((a, b) => a.position - b.position);
  const users = new Map(ctx.workspace.users.map((u) => [u.id, u]));

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-4 md:p-6">
      <h1 className="text-lg font-semibold">{team.name} settings</h1>
      <section className="space-y-2">
        <h2 className="border-b pb-1 font-medium">General</h2>
        <div className="flex gap-2">
          <Input
            placeholder="Engineering"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <span className="flex items-center rounded-md border px-2 text-xs text-muted-foreground">
            {team.key}
          </span>
        </div>
        <Textarea
          rows={2}
          placeholder="What the team works on"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <div className="flex justify-end">
          <Button
            size="sm"
            disabled={name === team.name && description === team.description}
            onClick={() =>
              void linear
                .editTeam(team.key, { name, description })
                .then(done, fail)
            }
          >
            Save
          </Button>
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="border-b pb-1 font-medium">Workflow</h2>
        {statuses.map((s, n) => (
          <StatusRow
            key={s.id}
            team={team}
            status={s}
            first={n === 0}
            last={n === statuses.length - 1}
            onChanged={done}
          />
        ))}
        <form
          className="flex gap-2 pt-2"
          onSubmit={(e) => {
            e.preventDefault();
            void linear.addStatus(team.key, status).then(() => {
              setStatus({ name: "", type: status.type, color: status.color });
              done();
            }, fail);
          }}
        >
          <Input
            className="h-8"
            placeholder="In Review"
            value={status.name}
            onChange={(e) => setStatus({ ...status, name: e.target.value })}
          />
          <NativeSelect
            className="h-8 text-xs"
            value={status.type}
            onChange={(e) => setStatus({ ...status, type: e.target.value })}
          >
            {STATUS_TYPES.map((t) => (
              <NativeSelectOption key={t} value={t}>
                {t}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <input
            type="color"
            value={status.color}
            onChange={(e) => setStatus({ ...status, color: e.target.value })}
            className="h-8 w-10 rounded border"
            aria-label="Color"
          />
          <Button size="sm" type="submit" disabled={!status.name.trim()}>
            Add status
          </Button>
        </form>
      </section>

      <section className="space-y-2">
        <h2 className="border-b pb-1 font-medium">Labels</h2>
        {team.labels.map((l) => (
          <LabelRow key={l.id} label={l} onChanged={done} />
        ))}
        <form
          className="flex gap-2 pt-2"
          onSubmit={(e) => {
            e.preventDefault();
            void linear.addLabel(team.key, label).then(() => {
              setLabel({ name: "", color: label.color });
              done();
            }, fail);
          }}
        >
          <Input
            className="h-8"
            placeholder="Bug"
            value={label.name}
            onChange={(e) => setLabel({ ...label, name: e.target.value })}
          />
          <input
            type="color"
            value={label.color}
            onChange={(e) => setLabel({ ...label, color: e.target.value })}
            className="h-8 w-10 rounded border"
            aria-label="Color"
          />
          <Button size="sm" type="submit" disabled={!label.name.trim()}>
            Add label
          </Button>
        </form>
      </section>

      <section className="space-y-2">
        <h2 className="border-b pb-1 font-medium">Members</h2>
        {team.members.map((id) => (
          <div key={id} className="flex items-center gap-2 text-sm">
            {users.get(id)?.name ?? id}
            <span className="text-xs text-muted-foreground">
              @{users.get(id)?.display_name}
            </span>
            <Button
              size="xs"
              variant="ghost"
              className="ml-auto"
              onClick={() =>
                void linear.removeMember(team.key, id).then(done, fail)
              }
            >
              {id === ctx.me.id ? "Leave" : "Remove"}
            </Button>
          </div>
        ))}
        <div className="flex gap-2 pt-2">
          <NativeSelect
            className="h-8 flex-1 text-sm"
            value={member}
            onChange={(e) => setMember(e.target.value)}
          >
            <NativeSelectOption value="">Add someone…</NativeSelectOption>
            {ctx.workspace.users
              .filter((u) => !team.members.includes(u.id))
              .map((u) => (
                <NativeSelectOption key={u.id} value={u.id}>
                  {u.name}
                  {u.is_app ? " (agent)" : ""}
                </NativeSelectOption>
              ))}
          </NativeSelect>
          <Button
            size="sm"
            disabled={!member}
            onClick={() =>
              void linear.addMember(team.key, member).then(() => {
                setMember("");
                done();
              }, fail)
            }
          >
            Add
          </Button>
        </div>
      </section>
    </div>
  );
}

function StatusRow({
  team,
  status,
  first,
  last,
  onChanged,
}: {
  team: LinearTeam;
  status: LinearTeam["states"][number];
  first: boolean;
  last: boolean;
  onChanged: () => void;
}) {
  const [name, setName] = useState(status.name);
  const [moveTo, setMoveTo] = useState("");
  const [deleting, setDeleting] = useState(false);
  const edit = (body: { name?: string; color?: string; position?: number }) =>
    void linear.editStatus(team.key, status.id, body).then(onChanged, fail);
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {STATE_ICON[status.type]}
      <input
        type="color"
        value={status.color}
        onChange={(e) => edit({ color: e.target.value })}
        className="h-6 w-8 rounded border"
        aria-label={`${status.name}'s color`}
      />
      <Input
        className="h-7 w-48"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name !== status.name && name.trim() && edit({ name })}
      />
      <span className="text-xs text-muted-foreground">{status.type}</span>
      <span className="ml-auto flex gap-1">
        <Button
          size="xs"
          variant="ghost"
          disabled={first}
          onClick={() => edit({ position: status.position - 1 })}
        >
          ↑
        </Button>
        <Button
          size="xs"
          variant="ghost"
          disabled={last}
          onClick={() => edit({ position: status.position + 1 })}
        >
          ↓
        </Button>
        {deleting ? (
          <>
            <NativeSelect
              className="h-6 text-xs"
              value={moveTo}
              onChange={(e) => setMoveTo(e.target.value)}
            >
              <NativeSelectOption value="">
                Move its issues to…
              </NativeSelectOption>
              {team.states
                .filter((s) => s.id !== status.id)
                .map((s) => (
                  <NativeSelectOption key={s.id} value={s.id}>
                    {s.name}
                  </NativeSelectOption>
                ))}
            </NativeSelect>
            <Button
              size="xs"
              variant="destructive"
              onClick={() =>
                void linear
                  .deleteStatus(team.key, status.id, moveTo || undefined)
                  .then(onChanged, fail)
              }
            >
              Delete
            </Button>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setDeleting(false)}
            >
              Cancel
            </Button>
          </>
        ) : (
          <Button size="xs" variant="ghost" onClick={() => setDeleting(true)}>
            <Trash2 />
          </Button>
        )}
      </span>
    </div>
  );
}

function LabelRow({
  label,
  onChanged,
}: {
  label: LinearTeam["labels"][number];
  onChanged: () => void;
}) {
  const [name, setName] = useState(label.name);
  const edit = (body: { name?: string; color?: string; retired?: boolean }) =>
    void linear.editLabel(label.id, body).then(onChanged, fail);
  return (
    <div
      className={`flex items-center gap-2 text-sm ${label.retired ? "opacity-60" : ""}`}
    >
      <input
        type="color"
        value={label.color}
        onChange={(e) => edit({ color: e.target.value })}
        className="h-6 w-8 rounded border"
        aria-label={`${label.name}'s color`}
      />
      <Input
        className="h-7 w-48"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name !== label.name && name.trim() && edit({ name })}
      />
      <span className="text-xs text-muted-foreground">
        {label.issues} issues{label.team ? "" : " · workspace"}
        {label.retired ? " · retired" : ""}
      </span>
      <span className="ml-auto flex gap-1">
        <Button
          size="xs"
          variant="ghost"
          onClick={() => edit({ retired: !label.retired })}
        >
          {label.retired ? "Restore" : "Retire"}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => {
            if (
              window.confirm(
                `Delete the label "${label.name}"? It comes off ${label.issues} issues.`,
              )
            )
              void linear.deleteLabel(label.id).then(onChanged, fail);
          }}
        >
          <Trash2 />
        </Button>
      </span>
    </div>
  );
}

// -- cycles ---------------------------------------------------------------------------------------

function CyclesView({ ctx, team }: { ctx: Ctx; team: LinearTeam }) {
  const [issues, setIssues] = useState<LinearIssueSummary[]>([]);
  const today = new Date().toISOString().slice(0, 10);
  const nextStart = team.cycles.at(-1)?.ends_at ?? today;
  const plusDays = (date: string, n: number) => {
    const d = new Date(`${date}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  };
  const [form, setForm] = useState({
    starts_at: nextStart,
    ends_at: plusDays(nextStart, 14),
    name: "",
  });
  useEffect(() => {
    void linear
      .issues({ team: team.key })
      .then((r) => setIssues(r.issues), fail);
  }, [team.key, ctx.version]);
  useEffect(() => {
    setForm({
      starts_at: nextStart,
      ends_at: plusDays(nextStart, 14),
      name: "",
    });
  }, [nextStart]);
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 md:p-6">
      <h1 className="text-lg font-semibold">{team.name} cycles</h1>
      {team.cycles.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No cycles yet. A cycle is a stretch of time the team plans issues
          into.
        </p>
      )}
      {[...team.cycles].reverse().map((c) => {
        const mine = issues.filter((i) => i.cycle?.id === c.id);
        const done = mine.filter((i) => i.state_type === "completed").length;
        return (
          <div
            key={c.id}
            className="flex items-center gap-3 rounded-md border p-3"
          >
            <button
              type="button"
              className="min-w-0 flex-1 text-left"
              onClick={() =>
                ctx.navigate(
                  `/linear/${ctx.org}/team/${team.key}/cycle/${c.id}`,
                )
              }
            >
              <div className="font-medium">
                {c.name ?? `Cycle ${c.number}`}
                {c.current && (
                  <Badge variant="outline" className="ml-2">
                    Current
                  </Badge>
                )}
              </div>
              <div className="text-xs text-muted-foreground">
                {day(c.starts_at)} – {day(c.ends_at)} · {mine.length} issues ·{" "}
                {mine.length ? Math.round((done / mine.length) * 100) : 0}% done
              </div>
            </button>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                if (
                  window.confirm(`Delete cycle ${c.number}? Its issues stay.`)
                )
                  void linear.deleteCycle(c.id).then(() => ctx.reload(), fail);
              }}
            >
              <Trash2 />
            </Button>
          </div>
        );
      })}
      <form
        className="flex flex-wrap items-end gap-2 border-t pt-4"
        onSubmit={(e) => {
          e.preventDefault();
          void linear
            .addCycle(team.key, { ...form, name: form.name || undefined })
            .then(() => ctx.reload(), fail);
        }}
      >
        <label className="text-xs text-muted-foreground">
          Starts
          <Input
            type="date"
            className="h-8"
            value={form.starts_at}
            onChange={(e) => setForm({ ...form, starts_at: e.target.value })}
          />
        </label>
        <label className="text-xs text-muted-foreground">
          Ends
          <Input
            type="date"
            className="h-8"
            value={form.ends_at}
            onChange={(e) => setForm({ ...form, ends_at: e.target.value })}
          />
        </label>
        <Input
          className="h-8 w-48"
          placeholder="Sprint 1"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <Button size="sm" type="submit">
          <Plus /> New cycle
        </Button>
      </form>
    </div>
  );
}

// -- projects -------------------------------------------------------------------------------------

function ProjectsView({ ctx }: { ctx: Ctx }) {
  const [archived, setArchived] = useState(false);
  const [list, setList] = useState(ctx.workspace.projects);
  const [form, setForm] = useState<{ name: string; team: string } | null>(null);
  useEffect(() => {
    void linear.projects(archived).then((r) => setList(r.projects), fail);
  }, [archived, ctx.version]);
  const users = new Map(ctx.workspace.users.map((u) => [u.id, u]));
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Projects</h1>
        <label className="ml-4 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Checkbox
            checked={archived}
            onCheckedChange={(v) => setArchived(!!v)}
          />
          Archived
        </label>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={() =>
            setForm({ name: "", team: ctx.workspace.teams[0]?.key ?? "" })
          }
        >
          <Plus /> New project
        </Button>
      </div>
      {form && (
        <form
          className="flex gap-2 rounded-md border p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void linear
              .createProject({
                name: form.name,
                teams: [form.team],
                lead: ctx.me.id,
              })
              .then((p) => {
                setForm(null);
                void ctx.reload();
                ctx.navigate(`/linear/${ctx.org}/project/${p.id}`);
              }, fail);
          }}
        >
          <Input
            autoFocus
            className="h-8"
            placeholder="Payments overhaul"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <NativeSelect
            className="h-8 text-sm"
            value={form.team}
            onChange={(e) => setForm({ ...form, team: e.target.value })}
          >
            {ctx.workspace.teams.map((t) => (
              <NativeSelectOption key={t.id} value={t.key}>
                {t.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <Button
            size="sm"
            type="submit"
            disabled={!form.name.trim() || !form.team}
          >
            Create
          </Button>
          <Button
            size="sm"
            variant="ghost"
            type="button"
            onClick={() => setForm(null)}
          >
            Cancel
          </Button>
        </form>
      )}
      {list.length === 0 && (
        <p className="text-sm text-muted-foreground">No projects.</p>
      )}
      <div className="divide-y rounded-md border">
        {list.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => ctx.navigate(`/linear/${ctx.org}/project/${p.id}`)}
            className="flex w-full items-center gap-4 px-3 py-2 text-left text-sm hover:bg-accent"
          >
            <span
              className="size-2.5 rounded-full"
              style={{ background: p.color }}
            />
            <span className="min-w-0 flex-1">
              <span className="font-medium">{p.name}</span>
              <span className="ml-2 text-xs text-muted-foreground">
                {p.summary}
              </span>
            </span>
            <span className="text-xs text-muted-foreground">
              {p.status_name}
            </span>
            <span className="w-24 text-xs text-muted-foreground">
              {p.lead_id ? users.get(p.lead_id)?.name : "No lead"}
            </span>
            <span className="w-20 text-xs text-muted-foreground">
              {p.target_date ? day(p.target_date) : ""}
            </span>
            <Progress value={p.progress} />
          </button>
        ))}
      </div>
    </div>
  );
}

function Progress({ value }: { value: number }) {
  return (
    <span className="flex w-24 items-center gap-1 text-xs text-muted-foreground">
      <span className="h-1.5 flex-1 rounded-full bg-muted">
        <span
          className="block h-full rounded-full bg-indigo-500"
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </span>
      {Math.round(value * 100)}%
    </span>
  );
}

function ProjectView({ ctx, id }: { ctx: Ctx; id: string }) {
  const [project, setProject] = useState<LinearProject | null>(null);
  const [missing, setMissing] = useState(false);
  const [description, setDescription] = useState<string | null>(null);
  const [milestone, setMilestone] = useState({ name: "", target_date: "" });
  const [update, setUpdate] = useState({ body: "", health: "onTrack" });
  const [link, setLink] = useState({ title: "", url: "" });
  const load = useCallback(
    () =>
      linear.project(id).then(
        (p) => {
          setProject(p);
          setMissing(false);
        },
        () => setMissing(true),
      ),
    [id],
  );
  useEffect(() => {
    void load();
  }, [load, ctx.version]);
  if (missing)
    return (
      <div className="p-6 text-sm text-muted-foreground">No such project.</div>
    );
  if (!project)
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  const edit = (body: Parameters<typeof linear.editProject>[1]) =>
    void linear.editProject(project.id, body).then(() => {
      void load();
      void ctx.reload();
    }, fail);
  const people = ctx.workspace.users.filter((u) => !u.is_app);
  const users = new Map(ctx.workspace.users.map((u) => [u.id, u]));
  const byMilestone = [
    ...project.milestones.map((m) => ({
      key: m.id,
      title: m.name,
      items: project.issues.filter((i) => i.milestone?.id === m.id),
    })),
    {
      key: "none",
      title: "No milestone",
      items: project.issues.filter((i) => !i.milestone),
    },
  ].filter((g) => g.items.length);

  return (
    <div className="mx-auto grid max-w-6xl gap-6 p-4 md:p-6 md:grid-cols-[1fr_260px]">
      <div className="min-w-0 space-y-6">
        <div>
          <div className="text-xs text-muted-foreground">
            {project.identifier}
            {project.archived && " · archived"}
          </div>
          <EditableText
            className="text-2xl font-semibold"
            value={project.name}
            placeholder="Project name"
            onSave={(name) => edit({ name })}
          />
          <EditableText
            className="text-sm text-muted-foreground"
            value={project.summary}
            placeholder="Add a short summary"
            onSave={(summary) => edit({ summary })}
          />
        </div>
        <div className="flex items-center gap-3">
          <Progress value={project.progress} />
          <span className="text-xs text-muted-foreground">
            {project.issues.length} issues
          </span>
        </div>
        {description !== null ? (
          <div className="space-y-2">
            <MentionTextarea
              value={description}
              onValue={setDescription}
              rows={8}
              placeholder="What the project is, and why (markdown)"
            />
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setDescription(null)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  edit({ description });
                  setDescription(null);
                }}
              >
                Save
              </Button>
            </div>
          </div>
        ) : (
          <div className="group relative">
            {project.description ? (
              <Markdown text={project.description} />
            ) : (
              <p className="text-sm text-muted-foreground">No description.</p>
            )}
            <Button
              size="xs"
              variant="ghost"
              className="absolute top-0 right-0 opacity-0 group-hover:opacity-100"
              onClick={() => setDescription(project.description)}
            >
              <Pencil /> Edit
            </Button>
          </div>
        )}

        <section className="space-y-2">
          <h2 className="font-medium">Milestones</h2>
          {project.milestones.map((m) => (
            <MilestoneRow
              key={m.id}
              milestone={m}
              onChanged={() => void load()}
            />
          ))}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void linear
                .addMilestone(project.id, {
                  name: milestone.name,
                  target_date: milestone.target_date || null,
                })
                .then(() => {
                  setMilestone({ name: "", target_date: "" });
                  void load();
                }, fail);
            }}
          >
            <Input
              className="h-8"
              placeholder="Beta"
              value={milestone.name}
              onChange={(e) =>
                setMilestone({ ...milestone, name: e.target.value })
              }
            />
            <Input
              type="date"
              className="h-8 w-40"
              value={milestone.target_date}
              onChange={(e) =>
                setMilestone({ ...milestone, target_date: e.target.value })
              }
            />
            <Button size="sm" type="submit" disabled={!milestone.name.trim()}>
              Add milestone
            </Button>
          </form>
        </section>

        <section className="space-y-2">
          <h2 className="font-medium">Issues</h2>
          {byMilestone.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No issues: set an issue's project to add it here.
            </p>
          )}
          {byMilestone.map((g) => (
            <div key={g.key}>
              <div className="mb-1 rounded-md bg-muted/50 px-3 py-1 text-sm font-medium">
                {g.title}{" "}
                <span className="text-muted-foreground">{g.items.length}</span>
              </div>
              {g.items.map((i) => (
                <button
                  key={i.id}
                  type="button"
                  onClick={() =>
                    ctx.navigate(`/linear/${ctx.org}/issue/${i.identifier}`)
                  }
                  className="flex w-full items-center gap-3 border-b px-3 py-1.5 text-left text-sm hover:bg-accent"
                >
                  <span className="w-16 shrink-0 text-xs text-muted-foreground">
                    {i.identifier}
                  </span>
                  {STATE_ICON[i.state_type]}
                  <span className="truncate">{i.title}</span>
                  <span className="ml-auto">
                    <IssueMeta issue={i} />
                  </span>
                </button>
              ))}
            </div>
          ))}
        </section>

        <section className="space-y-2">
          <div className="flex items-center gap-2">
            <h2 className="font-medium">Documents</h2>
            <Button
              size="xs"
              variant="outline"
              className="ml-auto"
              onClick={() =>
                void linear
                  .createDocument({ title: "Untitled", project: project.id })
                  .then(
                    (d) => ctx.navigate(`/linear/${ctx.org}/document/${d.id}`),
                    fail,
                  )
              }
            >
              <Plus /> New document
            </Button>
          </div>
          {project.documents.map((d) => (
            <button
              key={d.id}
              type="button"
              onClick={() =>
                ctx.navigate(`/linear/${ctx.org}/document/${d.id}`)
              }
              className="flex items-center gap-2 text-sm text-sky-600 hover:underline"
            >
              <FileText className="size-3.5" /> {d.title}
            </button>
          ))}
        </section>

        <section className="space-y-3">
          <h2 className="font-medium">Updates</h2>
          <div className="space-y-2 rounded-md border p-3">
            <MentionTextarea
              value={update.body}
              onValue={(body) => setUpdate({ ...update, body })}
              rows={3}
              placeholder="How the project is going (markdown)"
            />
            <div className="flex items-center gap-2">
              <NativeSelect
                className="h-8 text-sm"
                value={update.health}
                onChange={(e) =>
                  setUpdate({ ...update, health: e.target.value })
                }
              >
                {Object.entries(HEALTH).map(([k, h]) => (
                  <NativeSelectOption key={k} value={k}>
                    {h.name}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <Button
                size="sm"
                className="ml-auto"
                disabled={!update.body.trim()}
                onClick={() =>
                  void linear.postUpdate(project.id, update).then(() => {
                    setUpdate({ body: "", health: update.health });
                    void load();
                  }, fail)
                }
              >
                Post update
              </Button>
            </div>
          </div>
          {project.updates.map((u) => (
            <div key={u.id} className="group rounded-md border p-3 text-sm">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span
                  className={`font-semibold ${HEALTH[u.health]?.className ?? ""}`}
                >
                  {HEALTH[u.health]?.name ?? u.health}
                </span>
                · {u.author} · {when(u.at)}
                {u.user_id === ctx.me.id && (
                  <button
                    type="button"
                    className="ml-auto hidden group-hover:block"
                    title="Delete"
                    onClick={() =>
                      void linear.deleteUpdate(u.id).then(load, fail)
                    }
                  >
                    <Trash2 className="size-3.5 text-destructive" />
                  </button>
                )}
              </div>
              <Markdown text={u.body} />
            </div>
          ))}
        </section>
      </div>

      <aside className="space-y-4 text-sm">
        <Field label="Status">
          <NativeSelect
            className="h-8 w-full text-sm"
            value={project.status}
            onChange={(e) => edit({ status: e.target.value })}
          >
            {ctx.workspace.project_statuses.map((s) => (
              <NativeSelectOption key={s.type} value={s.type}>
                {s.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Priority">
          <NativeSelect
            className="h-8 w-full text-sm"
            value={String(project.priority)}
            onChange={(e) => edit({ priority: Number(e.target.value) })}
          >
            {PRIORITY.map((p, n) => (
              <NativeSelectOption key={p} value={String(n)}>
                {p}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Lead">
          <NativeSelect
            className="h-8 w-full text-sm"
            value={project.lead_id ?? ""}
            onChange={(e) => edit({ lead: e.target.value || null })}
          >
            <NativeSelectOption value="">No lead</NativeSelectOption>
            {people.map((u) => (
              <NativeSelectOption key={u.id} value={u.id}>
                {u.name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Start date">
          <Input
            type="date"
            className="h-8"
            value={project.start_date ?? ""}
            onChange={(e) => edit({ start_date: e.target.value || null })}
          />
        </Field>
        <Field label="Target date">
          <Input
            type="date"
            className="h-8"
            value={project.target_date ?? ""}
            onChange={(e) => edit({ target_date: e.target.value || null })}
          />
        </Field>
        <Field label="Teams">
          <div className="space-y-1">
            {ctx.workspace.teams.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={project.teams.includes(t.key)}
                  disabled={
                    project.teams.length === 1 && project.teams.includes(t.key)
                  }
                  onCheckedChange={(v) =>
                    edit({
                      teams: v
                        ? [...project.teams, t.key]
                        : project.teams.filter((k) => k !== t.key),
                    })
                  }
                />
                {t.name}
              </label>
            ))}
          </div>
        </Field>
        <Field label="Members">
          <div className="space-y-1">
            {project.members.map((m) => (
              <div key={m} className="group flex items-center gap-1">
                {users.get(m)?.name ?? m}
                <button
                  type="button"
                  className="ml-auto opacity-0 group-hover:opacity-100"
                  onClick={() => edit({ remove_members: [m] })}
                  title="Remove"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
            <NativeSelect
              className="h-7 w-full text-xs"
              value=""
              onChange={(e) =>
                e.target.value && edit({ add_members: [e.target.value] })
              }
            >
              <NativeSelectOption value="">Add a member…</NativeSelectOption>
              {ctx.workspace.users
                .filter((u) => !project.members.includes(u.id))
                .map((u) => (
                  <NativeSelectOption key={u.id} value={u.id}>
                    {u.name}
                  </NativeSelectOption>
                ))}
            </NativeSelect>
          </div>
        </Field>
        <Field label="Links">
          <div className="space-y-1">
            {project.links.map((l) => (
              <a
                key={l.id}
                href={l.url}
                target="_blank"
                rel="noreferrer"
                className="block truncate text-sky-600 underline"
              >
                {l.title}
              </a>
            ))}
            <form
              className="flex gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                edit({
                  links: [{ title: link.title || link.url, url: link.url }],
                });
                setLink({ title: "", url: "" });
              }}
            >
              <Input
                className="h-7 text-xs"
                placeholder="https://example.com"
                value={link.url}
                onChange={(e) => setLink({ ...link, url: e.target.value })}
              />
              <Button size="xs" type="submit" disabled={!link.url}>
                Add
              </Button>
            </form>
          </div>
        </Field>
        <div className="flex flex-col gap-2 border-t pt-3">
          <Button
            size="sm"
            variant="outline"
            onClick={() => edit({ archived: !project.archived })}
          >
            <Archive /> {project.archived ? "Unarchive" : "Archive"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive"
            onClick={() => {
              if (
                window.confirm(
                  `Delete ${project.name}, its milestones, updates and documents? Its issues stay.`,
                )
              )
                void linear.deleteProject(project.id).then(() => {
                  void ctx.reload();
                  ctx.navigate(`/linear/${ctx.org}/projects`);
                }, fail);
            }}
          >
            <Trash2 /> Delete
          </Button>
        </div>
      </aside>
    </div>
  );
}

function MilestoneRow({
  milestone: m,
  onChanged,
}: {
  milestone: LinearProject["milestones"][number];
  onChanged: () => void;
}) {
  const [name, setName] = useState(m.name);
  const edit = (body: { name?: string; target_date?: string | null }) =>
    void linear.editMilestone(m.id, body).then(onChanged, fail);
  return (
    <div className="flex items-center gap-2 text-sm">
      <Input
        className="h-7 w-56"
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={() => name !== m.name && name.trim() && edit({ name })}
      />
      <Input
        type="date"
        className="h-7 w-40"
        value={m.target_date ?? ""}
        onChange={(e) => edit({ target_date: e.target.value || null })}
      />
      <span className="text-xs text-muted-foreground">
        {m.done}/{m.issues} done
      </span>
      <Button
        size="xs"
        variant="ghost"
        className="ml-auto"
        onClick={() => {
          if (
            window.confirm(`Delete the milestone "${m.name}"? Its issues stay.`)
          )
            void linear.deleteMilestone(m.id).then(onChanged, fail);
        }}
      >
        <Trash2 />
      </Button>
    </div>
  );
}

function EditableText({
  value,
  placeholder,
  className,
  onSave,
}: {
  value: string;
  placeholder: string;
  className: string;
  onSave: (value: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  if (editing !== null)
    return (
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSave(editing);
          setEditing(null);
        }}
      >
        <Input
          autoFocus
          value={editing}
          placeholder={placeholder}
          onChange={(e) => setEditing(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
        />
        <Button size="sm" type="submit">
          Save
        </Button>
      </form>
    );
  return (
    <button
      type="button"
      onClick={() => setEditing(value)}
      className={`block text-left hover:bg-accent/50 ${className} ${value ? "" : "italic opacity-60"}`}
    >
      {value || placeholder}
    </button>
  );
}

// -- documents ------------------------------------------------------------------------------------

function DocumentsView({ ctx }: { ctx: Ctx }) {
  const [docs, setDocs] = useState<Omit<LinearDocument, "content">[]>([]);
  useEffect(() => {
    void linear.documents().then((r) => setDocs(r.documents), fail);
  }, [ctx.version]);
  return (
    <div className="mx-auto max-w-4xl space-y-3 p-4 md:p-6">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Documents</h1>
        {ctx.workspace.teams[0] && (
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() =>
              void linear
                .createDocument({
                  title: "Untitled",
                  team: (
                    ctx.workspace.teams.find((t) => t.member) ??
                    ctx.workspace.teams[0]
                  ).key,
                })
                .then(
                  (d) => ctx.navigate(`/linear/${ctx.org}/document/${d.id}`),
                  fail,
                )
            }
          >
            <Plus /> New document
          </Button>
        )}
      </div>
      {docs.length === 0 && (
        <p className="text-sm text-muted-foreground">No documents.</p>
      )}
      <div className="divide-y rounded-md border">
        {docs.map((d) => (
          <button
            key={d.id}
            type="button"
            onClick={() => ctx.navigate(`/linear/${ctx.org}/document/${d.id}`)}
            className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-accent"
          >
            <FileText className="size-4 text-muted-foreground" />
            <span className="flex-1 truncate">{d.title}</span>
            <span className="text-xs text-muted-foreground">
              {d.project?.name ?? (d.issue ? d.issue.identifier : d.team)}
            </span>
            <span className="text-xs text-muted-foreground">
              {when(d.updated_at)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function DocumentView({ ctx, id }: { ctx: Ctx; id: string }) {
  const [doc, setDoc] = useState<LinearDocument | null>(null);
  const [missing, setMissing] = useState(false);
  const [draft, setDraft] = useState<{ title: string; content: string } | null>(
    null,
  );
  const load = useCallback(
    () =>
      linear.document(id).then(
        (d) => {
          setDoc(d);
          setMissing(false);
        },
        () => setMissing(true),
      ),
    [id],
  );
  useEffect(() => {
    void load();
  }, [load, ctx.version]);
  if (missing)
    return (
      <div className="p-6 text-sm text-muted-foreground">No such document.</div>
    );
  if (!doc)
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  const nav = (path: string) => ctx.navigate(`/linear/${ctx.org}${path}`);
  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 md:p-6">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {doc.project && (
          <button
            type="button"
            className="hover:underline"
            onClick={() => nav(`/project/${doc.project!.id}`)}
          >
            {doc.project.name}
          </button>
        )}
        {doc.issue && (
          <button
            type="button"
            className="hover:underline"
            onClick={() => nav(`/issue/${doc.issue!.identifier}`)}
          >
            {doc.issue.identifier}
          </button>
        )}
        {doc.team && <span>{doc.team}</span>}
        <span className="ml-auto">
          {doc.updated_by ? `Edited by ${doc.updated_by}` : `By ${doc.creator}`}{" "}
          · {when(doc.updated_at)}
        </span>
      </div>
      {draft ? (
        <div className="space-y-2">
          <Input
            className="text-lg"
            value={draft.title}
            placeholder="Untitled"
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          />
          <MentionTextarea
            value={draft.content}
            onValue={(content) => setDraft({ ...draft, content })}
            rows={18}
            placeholder="Write in markdown"
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!draft.title.trim()}
              onClick={() =>
                void linear.editDocument(doc.id, draft).then(() => {
                  setDraft(null);
                  void load();
                }, fail)
              }
            >
              Save
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-start gap-2">
            <h1 className="flex-1 text-2xl font-semibold">{doc.title}</h1>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                setDraft({ title: doc.title, content: doc.content })
              }
            >
              <Pencil /> Edit
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                if (window.confirm(`Delete "${doc.title}"?`))
                  void linear
                    .deleteDocument(doc.id)
                    .then(
                      () =>
                        nav(
                          doc.project
                            ? `/project/${doc.project.id}`
                            : "/documents",
                        ),
                      fail,
                    );
              }}
            >
              <Trash2 />
            </Button>
          </div>
          {doc.content ? (
            <Markdown text={doc.content} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Empty: edit it to write.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// -- the inbox ------------------------------------------------------------------------------------

function InboxView({ ctx }: { ctx: Ctx }) {
  const [items, setItems] = useState<LinearNotification[]>([]);
  const load = useCallback(
    () => linear.inbox().then((r) => setItems(r.notifications), fail),
    [],
  );
  useEffect(() => {
    void load();
  }, [load, ctx.version]);
  const changed = () => {
    void load();
    void ctx.reload();
  };
  const open = (n: LinearNotification) => {
    if (!n.read) void linear.mark(n.id, { read: true }).then(changed, fail);
    if (n.issue) ctx.navigate(`/linear/${ctx.org}/issue/${n.issue.identifier}`);
    else if (n.project)
      ctx.navigate(`/linear/${ctx.org}/project/${n.project.id}`);
  };
  const snooze = (n: LinearNotification, hours: number) =>
    void linear
      .mark(n.id, { snoozed_until: Date.now() / 1000 + hours * 3600 })
      .then(changed, fail);
  return (
    <div className="mx-auto max-w-3xl space-y-3 p-4 md:p-6">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold">Inbox</h1>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={!items.some((n) => !n.read)}
          onClick={() => void linear.readAll().then(changed, fail)}
        >
          Mark all read
        </Button>
      </div>
      {items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing here. You hear about issues you are assigned, mentioned on,
          subscribed to, or delegated to an agent.
        </p>
      )}
      <div className="divide-y rounded-md border">
        {items.map((n) => (
          <div
            key={n.id}
            className={`group flex items-start gap-3 px-3 py-2 text-sm ${n.read ? "text-muted-foreground" : ""}`}
          >
            <span
              className={`mt-1.5 size-2 shrink-0 rounded-full ${n.read ? "bg-transparent" : "bg-indigo-500"}`}
            />
            <button
              type="button"
              className="min-w-0 flex-1 text-left"
              onClick={() => open(n)}
            >
              <div className={n.read ? "" : "font-medium"}>{n.text}</div>
              {n.issue && (
                <div className="truncate text-xs text-muted-foreground">
                  {n.issue.identifier} {n.issue.title}
                </div>
              )}
              {n.comment && (
                <div className="line-clamp-2 text-xs text-muted-foreground">
                  {n.comment}
                </div>
              )}
              <div className="text-xs text-muted-foreground">{when(n.at)}</div>
            </button>
            <span className="hidden shrink-0 gap-1 group-hover:flex">
              <Button
                size="xs"
                variant="ghost"
                onClick={() =>
                  void linear.mark(n.id, { read: !n.read }).then(changed, fail)
                }
              >
                {n.read ? "Unread" : "Read"}
              </Button>
              <Button size="xs" variant="ghost" onClick={() => snooze(n, 3)}>
                Snooze 3h
              </Button>
              <Button size="xs" variant="ghost" onClick={() => snooze(n, 24)}>
                Tomorrow
              </Button>
              <Button
                size="xs"
                variant="ghost"
                title="Dismiss"
                onClick={() => void linear.dismiss(n.id).then(changed, fail)}
              >
                <X />
              </Button>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
