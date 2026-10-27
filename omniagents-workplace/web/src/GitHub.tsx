import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Badge } from "@omniagents/ui/badge";
import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import { NativeSelect, NativeSelectOption } from "@omniagents/ui/native-select";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@omniagents/ui/popover";
import {
  BookOpen,
  Check,
  ChevronDown,
  CircleDot,
  Code2,
  Copy,
  File,
  Folder,
  GitBranch,
  GitCommitHorizontal,
  GitMerge,
  GitPullRequest,
  Lock,
  MessageSquare,
  Pencil,
  Play,
  RefreshCw,
  Settings,
  SmilePlus,
  Tag,
  Trash2,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  CodeBlock,
  fenceLanguage,
  languageOf,
  Tokens,
  useLines,
} from "./highlight";
import {
  MentionsContext,
  MentionTextarea,
  PersonMark,
  type Mentionable,
} from "./Mentions";
import { toast } from "sonner";

import {
  type Branch,
  type Commit,
  type Compare,
  type FileChange,
  github,
  type IssueDetail,
  type IssueSummary,
  type RepoDetails,
  type ReviewComment,
  type Tree,
  type WorkflowInfo,
  type WorkflowRun,
} from "./api";

/** A route in the GitHub part of the workplace, laid out as github.com lays it out. */
export interface GitHubRoute {
  repo?: string;
  view:
    | "repos"
    | "code"
    | "issues"
    | "pulls"
    | "issue"
    | "commits"
    | "commit"
    | "actions"
    | "compare"
    | "settings"
    | "branches"
    | "labels"
    | "new-issue";
  ref?: string;
  path?: string;
  number?: number;
  sha?: string;
  tab?: "conversation" | "commits" | "files" | "checks";
  /** compare: the base and head branches (``base...head``, or just ``head``). */
  base?: string;
  head?: string;
}

export function parseGitHubPath(pathname: string): GitHubRoute | null {
  const parts = pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "github" && parts.length === 1) return { view: "repos" };
  if (
    parts.length < 2 ||
    ["slack", "workplace", "github", "linear", "assets"].includes(parts[0])
  )
    return null;
  const repo = `${parts[0]}/${parts[1]}`;
  const [section, ...rest] = parts.slice(2);
  switch (section) {
    case undefined:
      return { repo, view: "code" };
    case "tree":
    case "blob":
      return {
        repo,
        view: "code",
        ref: rest[0],
        path: rest.slice(1).join("/"),
      };
    case "issues":
      if (rest[0] === "new") return { repo, view: "new-issue" };
      return rest[0]
        ? { repo, view: "issue", number: Number(rest[0]) }
        : { repo, view: "issues" };
    case "pulls":
      return { repo, view: "pulls" };
    case "pull": {
      const tab = (
        { commits: "commits", files: "files", checks: "checks" } as const
      )[rest[1] as "commits"];
      return {
        repo,
        view: "issue",
        number: Number(rest[0]),
        tab: tab ?? "conversation",
      };
    }
    case "commits":
      return { repo, view: "commits", ref: rest[0] };
    case "commit":
      return { repo, view: "commit", sha: rest[0] };
    case "actions":
      return { repo, view: "actions" };
    case "settings":
      return { repo, view: "settings" };
    case "branches":
      return { repo, view: "branches" };
    case "labels":
      return { repo, view: "labels" };
    case "compare": {
      const spec = rest.join("/");
      const [base, head] = spec.includes("...")
        ? spec.split("...", 2)
        : [undefined, spec || undefined];
      return { repo, view: "compare", base, head };
    }
    default:
      return { repo, view: "code" };
  }
}

/** Who is looking: their login, and whether they are the workplace's admin
 * (an admin may edit others' comments and bypass branch protections). */
const ViewerContext = createContext<{ login: string; admin: boolean }>({
  login: "",
  admin: false,
});

interface Props {
  route: GitHubRoute;
  actor: string;
  admin?: boolean;
  version: number;
  navigate: (path: string) => void;
}

export function GitHubApp({
  route,
  actor,
  admin = false,
  version,
  navigate,
}: Props) {
  const [repos, setRepos] = useState<RepoDetails[]>([]);
  const [people, setPeople] = useState<Mentionable[]>([]);
  useEffect(() => {
    void github.repos().then((r) => {
      setRepos(r.repositories);
      setPeople(
        r.users.map((u) => ({
          login: u.login,
          name: u.name,
          avatar: `/workplace/api/github/avatars/${u.login}`,
        })),
      );
    });
  }, [version]);
  return (
    <ViewerContext.Provider value={{ login: actor, admin }}>
      <MentionsContext.Provider value={people}>
        <GitHubPages
          route={route}
          actor={actor}
          version={version}
          navigate={navigate}
          repos={repos}
          setRepos={setRepos}
        />
      </MentionsContext.Provider>
    </ViewerContext.Provider>
  );
}

function GitHubPages({
  route,
  actor,
  version,
  navigate,
  repos,
  setRepos,
}: Props & {
  repos: RepoDetails[];
  setRepos: (repos: RepoDetails[]) => void;
}) {
  const repo = repos.find((r) => r.full_name === route.repo);
  const { admin } = useContext(ViewerContext);

  if (route.view === "repos" || !route.repo) {
    return (
      <div className="mx-auto w-full max-w-4xl overflow-y-auto p-4 md:p-6">
        <h1 className="mb-4 text-lg font-semibold">Repositories</h1>
        <NewRepo
          onCreated={(fullName) => {
            void github.repos().then((r) => setRepos(r.repositories));
            navigate(`/${fullName}`);
          }}
        />
        {repos.length === 0 && (
          <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
            No repositories yet.
          </div>
        )}
        <div className="divide-y rounded-md border">
          {repos.map((r) => (
            <button
              key={r.full_name}
              type="button"
              onClick={() => navigate(`/${r.full_name}`)}
              className="flex w-full flex-col items-start gap-1 px-4 py-3 text-left hover:bg-accent"
            >
              <span className="flex items-center gap-2 font-semibold text-sky-600">
                <BookOpen className="size-4" />
                {r.full_name}
              </span>
              <span className="text-sm text-muted-foreground">
                {r.description}
              </span>
              <span className="text-xs text-muted-foreground">
                {r.open_issues} open issues · {r.open_pulls} open pull requests
                · {r.branches.length} branches
              </span>
            </button>
          ))}
        </div>
      </div>
    );
  }
  if (!repo)
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Loading {route.repo}…
      </div>
    );

  const tab =
    route.view === "issue" ? (route.tab ? "pulls" : "issues") : route.view;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b px-4 pt-4 md:px-6">
        <div className="mb-3 flex min-w-0 items-center gap-2 text-lg">
          <BookOpen className="size-5 text-muted-foreground" />
          <button
            type="button"
            className="text-sky-600 hover:underline"
            onClick={() => navigate("/github")}
          >
            {repo.full_name.split("/")[0]}
          </button>
          <span className="text-muted-foreground">/</span>
          <button
            type="button"
            className="font-semibold text-sky-600 hover:underline"
            onClick={() => navigate(`/${repo.full_name}`)}
          >
            {repo.full_name.split("/")[1]}
          </button>
        </div>
        {/* Phones: the tabs scroll sideways. */}
        <nav className="-mb-px flex gap-1 overflow-x-auto text-sm [scrollbar-width:none]">
          <Tab
            active={tab === "code" || tab === "commits" || tab === "commit"}
            onClick={() => navigate(`/${repo.full_name}`)}
          >
            <BookOpen className="size-4" /> Code
          </Tab>
          <Tab
            active={tab === "issues" || tab === "new-issue"}
            onClick={() => navigate(`/${repo.full_name}/issues`)}
          >
            <CircleDot className="size-4" /> Issues{" "}
            <Count n={repo.open_issues} />
          </Tab>
          <Tab
            active={tab === "pulls"}
            onClick={() => navigate(`/${repo.full_name}/pulls`)}
          >
            <GitPullRequest className="size-4" /> Pull requests{" "}
            <Count n={repo.open_pulls} />
          </Tab>
          <Tab
            active={tab === "actions"}
            onClick={() => navigate(`/${repo.full_name}/actions`)}
          >
            <Play className="size-4" /> Actions
          </Tab>
          {admin && (
            <Tab
              active={tab === "settings"}
              onClick={() => navigate(`/${repo.full_name}/settings`)}
            >
              <Settings className="size-4" /> Settings
            </Tab>
          )}
        </nav>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl p-4 md:p-6">
          {(route.view === "code" || route.view === "pulls") && (
            <RecentPushes repo={repo} version={version} navigate={navigate} />
          )}
          {route.view === "compare" && (
            <CompareView
              repo={repo}
              base={route.base ?? repo.default_branch}
              head={route.head}
              navigate={navigate}
            />
          )}
          {route.view === "code" && repo.branches.length === 0 && (
            <EmptyRepo repo={repo} />
          )}
          {route.view === "code" && repo.branches.length > 0 && (
            <CodeView
              repo={repo}
              route={route}
              version={version}
              navigate={navigate}
            />
          )}
          {route.view === "commits" && (
            <CommitsView
              repo={repo}
              ref_={route.ref ?? repo.default_branch}
              version={version}
              navigate={navigate}
            />
          )}
          {route.view === "commit" && route.sha && (
            <CommitView repo={repo} sha={route.sha} />
          )}
          {(route.view === "issues" || route.view === "pulls") && (
            <IssueList
              repo={repo}
              kind={route.view === "pulls" ? "pull" : "issue"}
              version={version}
              navigate={navigate}
            />
          )}
          {route.view === "new-issue" && (
            <NewIssue repo={repo} actor={actor} navigate={navigate} />
          )}
          {route.view === "issue" && route.number !== undefined && (
            <IssueView
              repo={repo}
              number={route.number}
              tab={route.tab}
              actor={actor}
              version={version}
              navigate={navigate}
            />
          )}
          {route.view === "actions" && (
            <ActionsView repo={repo} version={version} navigate={navigate} />
          )}
          {route.view === "settings" && (
            <SettingsView
              repo={repo}
              navigate={navigate}
              onChanged={() =>
                void github.repos().then((r) => setRepos(r.repositories))
              }
            />
          )}
          {route.view === "branches" && (
            <BranchesView repo={repo} version={version} navigate={navigate} />
          )}
          {route.view === "labels" && (
            <LabelsView
              repo={repo}
              onChanged={() =>
                void github.repos().then((r) => setRepos(r.repositories))
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

// -- small pieces -----------------------------------------------------------------

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 whitespace-nowrap ${active ? "border-orange-500 font-semibold" : "border-transparent text-muted-foreground hover:text-foreground"}`}
    >
      {children}
    </button>
  );
}

function Count({ n }: { n: number }) {
  return n ? (
    <span className="rounded-full bg-muted px-1.5 text-xs">{n}</span>
  ) : null;
}

function ago(iso: string | null | undefined) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  const unit = (n: number, word: string) =>
    n === 1 ? `1 ${word} ago` : `${n} ${word}s ago`;
  if (s < 3600) return unit(Math.round(s / 60), "minute");
  if (s < 86400) return unit(Math.round(s / 3600), "hour");
  return unit(Math.round(s / 86400), "day");
}

function Avatar({ login }: { login: string }) {
  return (
    <img
      src={`/workplace/api/github/avatars/${login}`}
      alt=""
      className="size-6 shrink-0 rounded-full"
    />
  );
}

const MARKDOWN_COMPONENTS = {
  // A fenced block that names its language is highlighted; inline code is not.
  code({
    className,
    children,
  }: {
    className?: string;
    children?: React.ReactNode;
  }) {
    const fence = /language-([\w+#-]+)/.exec(className ?? "")?.[1];
    if (!fence) return <code className={className}>{children}</code>;
    return (
      <CodeBlock
        code={String(children).replace(/\n$/, "")}
        lang={fenceLanguage(fence)}
      />
    );
  },
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-sm max-w-none text-sm leading-relaxed [overflow-wrap:anywhere] [&_table]:block [&_table]:overflow-x-auto [&_a]:text-sky-600 [&_a]:underline [&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:font-semibold [&_li]:ml-5 [&_ol]:list-decimal [&_p]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_table]:my-2 [&_td]:border [&_td]:px-2 [&_th]:border [&_th]:px-2 [&_ul]:list-disc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={MARKDOWN_COMPONENTS}
      >
        {text || "_No description provided._"}
      </ReactMarkdown>
    </div>
  );
}

function StateBadge({ item }: { item: IssueSummary }) {
  if (item.is_pull && item.merged)
    return (
      <Badge className="bg-violet-600 text-white">
        <GitMerge className="size-3.5" /> Merged
      </Badge>
    );
  if (item.state === "closed")
    return (
      <Badge
        className={
          item.is_pull ? "bg-red-600 text-white" : "bg-violet-600 text-white"
        }
      >
        Closed
      </Badge>
    );
  if (item.is_pull && item.draft)
    return <Badge variant="secondary">Draft</Badge>;
  return (
    <Badge className="bg-emerald-600 text-white">
      {item.is_pull ? (
        <GitPullRequest className="size-3.5" />
      ) : (
        <CircleDot className="size-3.5" />
      )}{" "}
      Open
    </Badge>
  );
}

function Labels({
  names,
  colors,
}: {
  names: string[];
  colors: Map<string, string>;
}) {
  return (
    <>
      {names.map((n) => (
        <span
          key={n}
          className="rounded-full border px-2 text-xs"
          style={{
            borderColor: `#${colors.get(n) ?? "999999"}`,
            color: `#${colors.get(n) ?? "666666"}`,
          }}
        >
          {n}
        </span>
      ))}
    </>
  );
}

// -- code -----------------------------------------------------------------------------

function CodeView({
  repo,
  route,
  version,
  navigate,
}: {
  repo: RepoDetails;
  route: GitHubRoute;
  version: number;
  navigate: (p: string) => void;
}) {
  const ref = route.ref ?? repo.default_branch;
  const path = route.path ?? "";
  const [tree, setTree] = useState<Tree | null>(null);
  useEffect(() => {
    setTree(null);
    github.tree(repo.full_name, ref, path).then(setTree, () => setTree(null));
  }, [repo.full_name, ref, path, version]);
  const fileLines = useLines(
    tree?.type === "file" && !tree.binary ? (tree.content ?? "") : "",
    languageOf(path),
  );
  const crumbs = path ? path.split("/") : [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <NativeSelect
          value={ref}
          onChange={(e) =>
            navigate(`/${repo.full_name}/tree/${e.target.value}`)
          }
          className="w-56 text-sm"
        >
          {repo.branches.map((b) => (
            <NativeSelectOption key={b.name} value={b.name}>
              {b.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <div className="flex min-w-0 flex-wrap items-center gap-1 text-sm">
          <button
            type="button"
            className="text-sky-600 hover:underline"
            onClick={() => navigate(`/${repo.full_name}/tree/${ref}`)}
          >
            {repo.full_name.split("/")[1]}
          </button>
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1">
              /
              <button
                type="button"
                className="text-sky-600 hover:underline"
                onClick={() =>
                  navigate(
                    `/${repo.full_name}/tree/${ref}/${crumbs.slice(0, i + 1).join("/")}`,
                  )
                }
              >
                {c}
              </button>
            </span>
          ))}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => navigate(`/${repo.full_name}/branches`)}
        >
          <GitBranch /> {repo.branches.length}{" "}
          {repo.branches.length === 1 ? "branch" : "branches"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate(`/${repo.full_name}/commits/${ref}`)}
        >
          <GitCommitHorizontal /> Commits
        </Button>
        <CloneButton repo={repo} />
      </div>
      {!tree && <div className="text-sm text-muted-foreground">Loading…</div>}
      {tree?.last_commit && (
        <div className="flex items-center gap-2 rounded-t-md border bg-muted/40 px-4 py-2 text-sm">
          <span className="font-medium">{tree.last_commit.author}</span>
          <button
            type="button"
            className="truncate text-muted-foreground hover:underline"
            onClick={() =>
              navigate(`/${repo.full_name}/commit/${tree.last_commit!.sha}`)
            }
          >
            {tree.last_commit.subject}
          </button>
          <span className="ml-auto font-mono text-xs text-muted-foreground">
            {tree.last_commit.sha.slice(0, 7)} · {ago(tree.last_commit.date)}
          </span>
        </div>
      )}
      {tree?.type === "dir" && (
        <>
          <div className="-mt-4 divide-y rounded-b-md border text-sm">
            {tree.entries.map((e) => (
              <div
                key={e.path}
                className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] items-center gap-4 px-4 py-1.5 hover:bg-accent/50"
              >
                <button
                  type="button"
                  onClick={() =>
                    navigate(
                      `/${repo.full_name}/${e.type === "dir" ? "tree" : "blob"}/${ref}/${e.path}`,
                    )
                  }
                  className="flex min-w-0 items-center gap-2 text-left hover:text-sky-600 hover:underline"
                >
                  {e.type === "dir" ? (
                    <Folder className="size-4 shrink-0 text-sky-600" />
                  ) : (
                    <File className="size-4 shrink-0 text-muted-foreground" />
                  )}
                  <span className="truncate">{e.name}</span>
                </button>
                {e.last_commit ? (
                  <button
                    type="button"
                    onClick={() =>
                      navigate(
                        `/${repo.full_name}/commit/${e.last_commit!.sha}`,
                      )
                    }
                    className="truncate text-left text-muted-foreground hover:text-sky-600 hover:underline"
                    title={e.last_commit.message}
                  >
                    {e.last_commit.subject}
                  </button>
                ) : (
                  <span />
                )}
                <span
                  className="text-right text-xs whitespace-nowrap text-muted-foreground"
                  title={e.last_commit?.date}
                >
                  {e.last_commit ? ago(e.last_commit.date) : ""}
                </span>
              </div>
            ))}
          </div>
          {tree.readme && (
            <div className="rounded-md border">
              <div className="border-b px-4 py-2 text-sm font-semibold">
                README
              </div>
              <div className="px-6 py-4">
                <Markdown text={tree.readme} />
              </div>
            </div>
          )}
        </>
      )}
      {tree?.type === "file" && (
        <div className="-mt-4 rounded-b-md border">
          <div className="border-b px-4 py-1.5 text-xs text-muted-foreground">
            {tree.size} bytes
          </div>
          {tree.binary ? (
            <div className="p-4 text-sm text-muted-foreground">
              Binary file not shown.
            </div>
          ) : path.toLowerCase().endsWith(".md") ? (
            <div className="px-6 py-4">
              <Markdown text={tree.content ?? ""} />
            </div>
          ) : (
            <pre className="overflow-x-auto text-xs leading-5">
              {(tree.content ?? "").split("\n").map((line, i) => (
                <div key={i} className="flex">
                  <span className="w-12 shrink-0 select-none pr-3 text-right text-muted-foreground">
                    {i + 1}
                  </span>
                  <span className="whitespace-pre">
                    <Tokens line={fileLines?.[i]} text={line} />
                  </span>
                </div>
              ))}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function CommitsView({
  repo,
  ref_,
  version,
  navigate,
}: {
  repo: RepoDetails;
  ref_: string;
  version: number;
  navigate: (p: string) => void;
}) {
  const [commits, setCommits] = useState<Commit[]>([]);
  useEffect(() => {
    void github
      .commits(repo.full_name, ref_)
      .then((r) => setCommits(r.commits));
  }, [repo.full_name, ref_, version]);
  return (
    <div className="space-y-3">
      <h2 className="font-semibold">Commits on {ref_}</h2>
      <div className="divide-y rounded-md border text-sm">
        {commits.map((c) => (
          <CommitRow
            key={c.sha}
            commit={c}
            onOpen={() => navigate(`/${repo.full_name}/commit/${c.sha}`)}
          />
        ))}
      </div>
    </div>
  );
}

function CommitRow({ commit, onOpen }: { commit: Commit; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-accent"
    >
      <GitCommitHorizontal className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <div className="truncate font-medium">{commit.subject}</div>
        <div className="text-xs text-muted-foreground">
          {commit.author} committed {ago(commit.date)}
        </div>
      </div>
      <span className="ml-auto font-mono text-xs text-muted-foreground">
        {commit.sha.slice(0, 7)}
      </span>
    </button>
  );
}

function CommitView({ repo, sha }: { repo: RepoDetails; sha: string }) {
  const [commit, setCommit] = useState<
    (Commit & { files: FileChange[] }) | null
  >(null);
  useEffect(() => {
    void github.commit(repo.full_name, sha).then(setCommit);
  }, [repo.full_name, sha]);
  if (!commit)
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  return (
    <div className="space-y-4">
      <div className="rounded-md border p-4">
        <div className="font-semibold">{commit.subject}</div>
        <pre className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
          {commit.message.split("\n").slice(1).join("\n").trim()}
        </pre>
        <div className="mt-2 text-xs text-muted-foreground">
          {commit.author} committed {ago(commit.date)} ·{" "}
          <span className="font-mono">{commit.sha}</span>
        </div>
      </div>
      {commit.files.map((f) => (
        <FileDiff key={f.filename} file={f} />
      ))}
    </div>
  );
}

// -- diffs -------------------------------------------------------------------------------

interface DiffRow {
  kind: "hunk" | "add" | "del" | "ctx";
  text: string;
  old?: number;
  new?: number;
}

function diffRows(patch: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldN = 0;
  let newN = 0;
  for (const line of patch.split("\n")) {
    const m = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (m) {
      oldN = Number(m[1]);
      newN = Number(m[2]);
      rows.push({ kind: "hunk", text: line });
    } else if (line.startsWith("+"))
      rows.push({ kind: "add", text: line.slice(1), new: newN++ });
    else if (line.startsWith("-"))
      rows.push({ kind: "del", text: line.slice(1), old: oldN++ });
    else if (line.startsWith("\\")) continue;
    else
      rows.push({ kind: "ctx", text: line.slice(1), old: oldN++, new: newN++ });
  }
  return rows;
}

interface Draft {
  path: string;
  line: number;
  side: "LEFT" | "RIGHT";
  body: string;
}

function FileDiff({
  file,
  threads,
  onComment,
  drafts,
  onResolve,
}: {
  file: FileChange;
  threads?: {
    id?: string;
    resolved?: boolean;
    comments: ReviewComment[];
    path: string;
  }[];
  onComment?: (d: Draft) => void;
  drafts?: Draft[];
  onResolve?: (thread: string, resolved: boolean) => void;
}) {
  const [shown, setShown] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<{
    line: number;
    side: "LEFT" | "RIGHT";
  } | null>(null);
  const [text, setText] = useState("");
  const rows = useMemo(() => diffRows(file.patch), [file.patch]);
  // Each side is highlighted whole (the old: context and deletions; the
  // new: context and additions), so a string or comment across lines stays one.
  const sides = useMemo(() => {
    const at: { old?: number; new?: number }[] = [];
    const oldText: string[] = [];
    const newText: string[] = [];
    for (const r of rows) {
      const here: { old?: number; new?: number } = {};
      if (r.kind === "ctx" || r.kind === "del")
        here.old = oldText.push(r.text) - 1;
      if (r.kind === "ctx" || r.kind === "add")
        here.new = newText.push(r.text) - 1;
      at.push(here);
    }
    return { at, old: oldText.join("\n"), new: newText.join("\n") };
  }, [rows]);
  const lang = languageOf(file.filename);
  const oldLines = useLines(sides.old, lang);
  const newLines = useLines(sides.new, lang);
  // The threads that start on a line: each with its comments, resolvable.
  const threadsAt = (line: number | undefined, side: string) =>
    (threads ?? []).filter(
      (t) =>
        t.path === file.filename &&
        t.comments.length > 0 &&
        line !== undefined &&
        t.comments[0].line === line &&
        t.comments[0].side === side,
    );
  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-4 py-2 font-mono text-xs">
        <span className="font-semibold">{file.filename}</span>
        <span className="text-emerald-600">+{file.additions}</span>
        <span className="text-red-600">−{file.deletions}</span>
      </div>
      <div className="overflow-x-auto font-mono text-xs leading-5">
        {rows.map((r, i) => {
          const side = r.kind === "del" ? "LEFT" : "RIGHT";
          const line = r.kind === "del" ? r.old : r.new;
          const posted = threadsAt(line, side);
          const pending = (drafts ?? []).filter(
            (d) =>
              d.path === file.filename && d.line === line && d.side === side,
          );
          return (
            <div key={i}>
              <div
                className={`group flex ${r.kind === "add" ? "bg-emerald-500/10" : r.kind === "del" ? "bg-red-500/10" : r.kind === "hunk" ? "bg-sky-500/10 text-muted-foreground" : ""}`}
              >
                <span className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground">
                  {r.old ?? ""}
                </span>
                <span className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground">
                  {r.new ?? ""}
                </span>
                {onComment && r.kind !== "hunk" ? (
                  <button
                    type="button"
                    title="Comment on this line"
                    onClick={() => {
                      setOpen({ line: line!, side });
                      setText("");
                    }}
                    className="w-5 shrink-0 text-sky-600 opacity-0 group-hover:opacity-100"
                  >
                    +
                  </button>
                ) : (
                  <span className="w-5 shrink-0" />
                )}
                <span className="whitespace-pre pr-4">
                  {r.kind === "add"
                    ? "+"
                    : r.kind === "del"
                      ? "-"
                      : r.kind === "hunk"
                        ? ""
                        : " "}
                  {r.kind === "hunk" ? (
                    r.text
                  ) : (
                    <Tokens
                      line={
                        r.kind === "del"
                          ? oldLines?.[sides.at[i].old!]
                          : newLines?.[sides.at[i].new!]
                      }
                      text={r.text}
                    />
                  )}
                </span>
              </div>
              {posted.map((t, n) => {
                const key = t.id ?? `t${n}`;
                if (t.resolved && !shown.has(key))
                  return (
                    <div
                      key={key}
                      className="mx-4 my-1 flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-1.5 font-sans text-xs text-muted-foreground"
                    >
                      <Check className="size-3.5" /> Resolved conversation ·{" "}
                      {t.comments.length} comment
                      {t.comments.length === 1 ? "" : "s"}
                      <button
                        type="button"
                        className="ml-auto text-sky-600 hover:underline"
                        onClick={() => setShown((s) => new Set(s).add(key))}
                      >
                        Show
                      </button>
                    </div>
                  );
                return (
                  <div
                    key={key}
                    className="mx-4 my-1 rounded-md border bg-background font-sans"
                  >
                    {t.comments.map((c) => (
                      <div key={c.id} className="border-b p-2 last:border-b-0">
                        <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                          <Avatar login={c.user} /> <b>{c.user}</b> {ago(c.at)}
                        </div>
                        <Markdown text={c.body} />
                      </div>
                    ))}
                    {onResolve && t.id && (
                      <div className="border-t bg-muted/30 px-2 py-1.5">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() => {
                            onResolve(t.id!, !t.resolved);
                            setShown((s) => {
                              const next = new Set(s);
                              next.delete(key);
                              return next;
                            });
                          }}
                        >
                          {t.resolved
                            ? "Unresolve conversation"
                            : "Resolve conversation"}
                        </Button>
                      </div>
                    )}
                  </div>
                );
              })}
              {pending
                .map((d, n) => ({
                  id: -1 - n,
                  user: "pending",
                  body: d.body,
                  line: d.line,
                  side: d.side,
                  at: "",
                }))
                .map((c) => (
                  <div
                    key={c.id}
                    className="mx-4 my-1 rounded-md border bg-background p-2 font-sans"
                  >
                    <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                      {c.user === "pending" ? (
                        <Badge variant="secondary">Pending</Badge>
                      ) : (
                        <>
                          <Avatar login={c.user} /> <b>{c.user}</b> {ago(c.at)}
                        </>
                      )}
                    </div>
                    <Markdown text={c.body} />
                  </div>
                ))}
              {open &&
                open.line === line &&
                open.side === side &&
                r.kind !== "hunk" && (
                  <div className="mx-4 my-2 space-y-2 font-sans">
                    <MentionTextarea
                      value={text}
                      onValue={setText}
                      rows={3}
                      placeholder="Leave a comment"
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setOpen(null)}
                      >
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        disabled={!text.trim()}
                        onClick={() => {
                          onComment?.({
                            path: file.filename,
                            line: open.line,
                            side: open.side,
                            body: text.trim(),
                          });
                          setOpen(null);
                        }}
                      >
                        Add review comment
                      </Button>
                    </div>
                  </div>
                )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// -- issues and pull requests -----------------------------------------------------------------

function IssueList({
  repo,
  kind,
  version,
  navigate,
}: {
  repo: RepoDetails;
  kind: "issue" | "pull";
  version: number;
  navigate: (p: string) => void;
}) {
  const [state, setState] = useState("open");
  const { login } = useContext(ViewerContext);
  const [query, setQuery] = useState("");
  const [author, setAuthor] = useState("");
  const [assignee, setAssignee] = useState("");
  const [label, setLabel] = useState("");
  const [items, setItems] = useState<IssueSummary[]>([]);
  const colors = useMemo(
    () => new Map(repo.labels.map((l) => [l.name, l.color])),
    [repo.labels],
  );
  useEffect(() => {
    void github
      .issues(repo.full_name, kind, state)
      .then((r) => setItems(r.items));
  }, [repo.full_name, kind, state, version]);
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const shown = items.filter(
    (i) =>
      (!author || i.user === author) &&
      (!assignee || i.assignees.includes(assignee)) &&
      (!label || i.labels.includes(label)) &&
      words.every((w) =>
        `${i.title} #${i.number} ${i.user}`.toLowerCase().includes(w),
      ),
  );
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex rounded-md border text-sm">
          {["open", "closed", "all"].map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setState(s)}
              className={`px-3 py-1 capitalize ${s === state ? "bg-accent font-semibold" : ""}`}
            >
              {s}
            </button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => navigate(`/${repo.full_name}/labels`)}
        >
          <Tag /> Labels
        </Button>
        {kind === "issue" ? (
          <Button
            size="sm"
            className="bg-emerald-600 text-white hover:bg-emerald-700"
            onClick={() => navigate(`/${repo.full_name}/issues/new`)}
          >
            New issue
          </Button>
        ) : (
          <Button
            size="sm"
            className="bg-emerald-600 text-white hover:bg-emerald-700"
            onClick={() => navigate(`/${repo.full_name}/compare`)}
          >
            New pull request
          </Button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search all ${kind === "pull" ? "pull requests" : "issues"}`}
          className="w-64"
        />
        <Button
          variant={author === login ? "secondary" : "outline"}
          size="sm"
          onClick={() => setAuthor(author === login ? "" : login)}
        >
          Yours
        </Button>
        <Button
          variant={assignee === login ? "secondary" : "outline"}
          size="sm"
          onClick={() => setAssignee(assignee === login ? "" : login)}
        >
          Assigned to you
        </Button>
        <NativeSelect
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          className="w-40 text-sm"
        >
          <NativeSelectOption value="">Author</NativeSelectOption>
          {[...new Set(items.map((i) => i.user))].sort().map((u) => (
            <NativeSelectOption key={u} value={u}>
              {u}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <NativeSelect
          value={assignee}
          onChange={(e) => setAssignee(e.target.value)}
          className="w-40 text-sm"
        >
          <NativeSelectOption value="">Assignee</NativeSelectOption>
          {[...new Set(items.flatMap((i) => i.assignees))].sort().map((u) => (
            <NativeSelectOption key={u} value={u}>
              {u}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <NativeSelect
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="w-40 text-sm"
        >
          <NativeSelectOption value="">Label</NativeSelectOption>
          {repo.labels.map((l) => (
            <NativeSelectOption key={l.name} value={l.name}>
              {l.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        {(query || author || assignee || label) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setQuery("");
              setAuthor("");
              setAssignee("");
              setLabel("");
            }}
          >
            <X /> Clear filters
          </Button>
        )}
      </div>
      <div className="divide-y rounded-md border text-sm">
        {shown.length === 0 && (
          <div className="p-6 text-center text-muted-foreground">
            Nothing here.
          </div>
        )}
        {shown.map((i) => (
          <button
            key={i.number}
            type="button"
            onClick={() =>
              navigate(
                `/${repo.full_name}/${i.is_pull ? "pull" : "issues"}/${i.number}`,
              )
            }
            className="flex w-full items-start gap-3 px-4 py-2 text-left hover:bg-accent"
          >
            {i.is_pull ? (
              <GitPullRequest
                className={`mt-0.5 size-4 ${i.merged ? "text-violet-600" : i.state === "open" ? "text-emerald-600" : "text-red-600"}`}
              />
            ) : (
              <CircleDot
                className={`mt-0.5 size-4 ${i.state === "open" ? "text-emerald-600" : "text-violet-600"}`}
              />
            )}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{i.title}</span>
                <Labels names={i.labels} colors={colors} />
              </div>
              <div className="text-xs text-muted-foreground">
                #{i.number} opened {ago(i.created_at)} by {i.user}
                {i.is_pull && ` · ${i.head} → ${i.base}`}
              </div>
            </div>
            {i.comments > 0 && (
              <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
                <MessageSquare className="size-3.5" /> {i.comments}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

function NewIssue({
  repo,
  actor,
  navigate,
}: {
  repo: RepoDetails;
  actor: string;
  navigate: (p: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [labels, setLabels] = useState<string[]>([]);
  const [assignees, setAssignees] = useState<string[]>([]);
  return (
    <div className="space-y-3">
      <h2 className="font-semibold">
        New issue on {repo.full_name}, as {actor}
      </h2>
      <Input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Title"
      />
      <MarkdownField
        value={body}
        onValue={setBody}
        rows={8}
        placeholder="Description (markdown)"
      />
      <LabelPicker repo={repo} value={labels} onChange={setLabels} />
      <div className="max-w-xs rounded-md border p-3 text-sm">
        <div className="mb-1 flex items-center justify-between font-semibold">
          Assignees
          <PeoplePicker
            title="Assign up to 10 people to this issue"
            selected={assignees}
            onToggle={(login) => setAssignees((a) => toggled(a, login))}
          />
        </div>
        <PeopleList
          logins={assignees}
          empty={
            <>
              No one —{" "}
              <button
                type="button"
                className="hover:text-sky-600 hover:underline"
                onClick={() => setAssignees([actor])}
              >
                assign yourself
              </button>
            </>
          }
        />
      </div>
      <div className="flex justify-end">
        <Button
          disabled={!title.trim()}
          className="bg-emerald-600 text-white hover:bg-emerald-700"
          onClick={() =>
            void github
              .createIssue(repo.full_name, { title, body, labels, assignees })
              .then(
                (i) => navigate(`/${repo.full_name}/issues/${i.number}`),
                (e) => toast.error(`GitHub said: ${(e as Error).message}`),
              )
          }
        >
          Submit new issue
        </Button>
      </div>
    </div>
  );
}

function LabelPicker({
  repo,
  value,
  onChange,
}: {
  repo: RepoDetails;
  value: string[];
  onChange: (labels: string[]) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {repo.labels.map((l) => {
        const on = value.includes(l.name);
        return (
          <button
            key={l.name}
            type="button"
            onClick={() =>
              onChange(
                on ? value.filter((v) => v !== l.name) : [...value, l.name],
              )
            }
            className={`rounded-full border px-2 py-0.5 text-xs ${on ? "font-semibold" : "opacity-60"}`}
            style={{
              borderColor: `#${l.color}`,
              color: `#${l.color}`,
              background: on ? `#${l.color}22` : undefined,
            }}
          >
            {on && <Check className="mr-1 inline size-3" />}
            {l.name}
          </button>
        );
      })}
    </div>
  );
}

function IssueView({
  repo,
  number,
  tab,
  actor,
  version,
  navigate,
}: {
  repo: RepoDetails;
  number: number;
  tab?: GitHubRoute["tab"];
  actor: string;
  version: number;
  navigate: (p: string) => void;
}) {
  const [item, setItem] = useState<IssueDetail | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const load = useCallback(
    () =>
      github.issue(repo.full_name, number).then(setItem, () => setItem(null)),
    [repo.full_name, number],
  );
  useEffect(() => {
    void load();
  }, [load, version]);
  const colors = useMemo(
    () => new Map(repo.labels.map((l) => [l.name, l.color])),
    [repo.labels],
  );
  if (!item)
    return <div className="text-sm text-muted-foreground">Loading…</div>;

  const fail = (e: unknown) =>
    toast.error(`GitHub said: ${(e as Error).message}`);
  const base = `/${repo.full_name}/${item.is_pull ? "pull" : "issues"}/${number}`;
  return (
    <div className="space-y-4">
      <div>
        <TitleEditor item={item} repo={repo.full_name} onDone={load} />
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <StateBadge item={item} />
          <span>
            <b>{item.user}</b> opened this {ago(item.created_at)}
            {item.is_pull && (
              <>
                {" "}
                · wants to merge{" "}
                <code className="rounded bg-muted px-1">
                  {item.head}
                </code> into{" "}
                <code className="rounded bg-muted px-1">{item.base}</code>
              </>
            )}
          </span>
          <Labels names={item.labels} colors={colors} />
        </div>
      </div>
      {item.is_pull && (
        <nav className="flex gap-1 overflow-x-auto border-b text-sm [scrollbar-width:none]">
          <Tab
            active={!tab || tab === "conversation"}
            onClick={() => navigate(base)}
          >
            <MessageSquare className="size-4" /> Conversation
          </Tab>
          <Tab
            active={tab === "commits"}
            onClick={() => navigate(`${base}/commits`)}
          >
            <GitCommitHorizontal className="size-4" /> Commits{" "}
            <Count n={item.commits?.length ?? 0} />
          </Tab>
          <Tab
            active={tab === "checks"}
            onClick={() => navigate(`${base}/checks`)}
          >
            <Play className="size-4" /> Checks{" "}
            <Count n={item.checks?.length ?? 0} />
          </Tab>
          <Tab
            active={tab === "files"}
            onClick={() => navigate(`${base}/files`)}
          >
            <File className="size-4" /> Files changed{" "}
            <Count n={item.files?.length ?? 0} />
          </Tab>
        </nav>
      )}

      {(!item.is_pull || !tab || tab === "conversation") && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-[minmax(0,1fr)_220px]">
          <div className="space-y-4">
            <CommentBox
              user={item.user}
              at={item.created_at}
              body={item.body}
              reactions={item.reactions}
              reacted={item.reacted}
              onReact={(content) =>
                void github
                  .reactToIssue(repo.full_name, number, content)
                  .then(load, fail)
              }
              onEdit={(body) =>
                github.editIssue(repo.full_name, number, { body }).then(load)
              }
            />
            {item.timeline.map((e) =>
              e.type === "comment" ? (
                <CommentBox
                  key={`c${e.id}`}
                  user={e.user}
                  at={e.at}
                  body={e.body}
                  edited={e.edited}
                  reactions={e.reactions}
                  reacted={e.reacted}
                  onReact={(content) =>
                    void github
                      .reactToComment(repo.full_name, e.id, content)
                      .then(load, fail)
                  }
                  onEdit={(body) =>
                    github.editComment(repo.full_name, e.id, body).then(load)
                  }
                  onDelete={() =>
                    void github
                      .deleteComment(repo.full_name, e.id)
                      .then(load, fail)
                  }
                />
              ) : (
                <ReviewBox
                  key={`r${e.id}`}
                  review={e}
                  repo={repo.full_name}
                  number={number}
                  onDone={load}
                />
              ),
            )}
            {item.is_pull && (
              <MergeBox
                item={item}
                repo={repo.full_name}
                actor={actor}
                onDone={load}
              />
            )}
            <Reply
              item={item}
              repo={repo.full_name}
              actor={actor}
              onDone={load}
              fail={fail}
            />
          </div>
          <aside className="space-y-4 text-sm">
            <AssignBox
              item={item}
              repo={repo.full_name}
              actor={actor}
              onDone={load}
            />
            <div>
              <div className="mb-1 font-semibold">Labels</div>
              <LabelPicker
                repo={repo}
                value={item.labels}
                onChange={(labels) =>
                  void github
                    .editIssue(repo.full_name, number, { labels })
                    .then(load, fail)
                }
              />
            </div>
            {item.is_pull && (
              <ReviewersBox item={item} repo={repo.full_name} onDone={load} />
            )}
            {item.is_pull && item.state === "open" && !item.draft && (
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-sky-600 hover:underline"
                onClick={() =>
                  void github
                    .editIssue(repo.full_name, number, { draft: true })
                    .then(load, fail)
                }
              >
                Convert to draft
              </button>
            )}
          </aside>
        </div>
      )}

      {item.is_pull && tab === "commits" && (
        <div className="divide-y rounded-md border text-sm">
          {(item.commits ?? []).map((c) => (
            <CommitRow
              key={c.sha}
              commit={c}
              onOpen={() => navigate(`/${repo.full_name}/commit/${c.sha}`)}
            />
          ))}
        </div>
      )}

      {item.is_pull && tab === "checks" && (
        <div className="divide-y rounded-md border text-sm">
          {(item.checks ?? []).length === 0 && (
            <div className="p-4 text-muted-foreground">
              No checks on the head commit.
            </div>
          )}
          {(item.checks ?? []).map((c) => (
            <button
              key={c.run}
              type="button"
              className="flex w-full items-center gap-2 px-4 py-2 text-left hover:bg-accent"
              onClick={() => navigate(`/${repo.full_name}/actions`)}
            >
              <RunIcon status={c.status} conclusion={c.conclusion} /> {c.name}
              <span className="ml-auto text-xs text-muted-foreground">
                {c.conclusion ?? c.status} · {ago(c.at)}
              </span>
            </button>
          ))}
        </div>
      )}

      {item.is_pull && tab === "files" && (
        <div className="space-y-4">
          <ReviewSubmit
            item={item}
            repo={repo.full_name}
            actor={actor}
            drafts={drafts}
            onDone={() => {
              setDrafts([]);
              void load();
            }}
          />
          {(item.files ?? []).map((f) => (
            <FileDiff
              key={f.filename}
              file={f}
              threads={item.threads}
              onResolve={(thread, resolved) =>
                void github
                  .resolveThread(repo.full_name, number, thread, resolved)
                  .then(load, fail)
              }
              drafts={drafts}
              onComment={(d) => setDrafts((ds) => [...ds, d])}
            />
          ))}
        </div>
      )}
    </div>
  );
}

const REACTIONS: [string, string][] = [
  ["+1", "👍"],
  ["-1", "👎"],
  ["laugh", "😄"],
  ["hooray", "🎉"],
  ["confused", "😕"],
  ["heart", "❤️"],
  ["rocket", "🚀"],
  ["eyes", "👀"],
];

function ReactionBar({
  reactions,
  reacted,
  onReact,
}: {
  reactions: Record<string, number>;
  reacted: string[];
  onReact: (content: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-1 px-4 pb-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="icon-xs"
            title="Add a reaction"
            className="rounded-full"
          >
            <SmilePlus />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="flex w-auto gap-1 p-1">
          {REACTIONS.map(([content, emoji]) => (
            <button
              key={content}
              type="button"
              title={content}
              onClick={() => {
                setOpen(false);
                onReact(content);
              }}
              className={`rounded-md px-1.5 py-1 text-base hover:bg-accent ${reacted.includes(content) ? "bg-sky-500/15" : ""}`}
            >
              {emoji}
            </button>
          ))}
        </PopoverContent>
      </Popover>
      {REACTIONS.filter(([content]) => reactions[content]).map(
        ([content, emoji]) => (
          <button
            key={content}
            type="button"
            onClick={() => onReact(content)}
            className={`flex items-center gap-1 rounded-full border px-2 text-xs ${reacted.includes(content) ? "border-sky-500 bg-sky-500/10 text-sky-700 dark:text-sky-300" : "hover:bg-accent"}`}
          >
            {emoji} {reactions[content]}
          </button>
        ),
      )}
    </div>
  );
}

/** A comment (or an issue's description): its author or an admin edits or deletes it. */
function CommentBox({
  user,
  at,
  body,
  edited,
  reactions,
  reacted,
  onReact,
  onEdit,
  onDelete,
}: {
  user: string;
  at: string;
  body: string;
  edited?: boolean;
  reactions?: Record<string, number>;
  reacted?: string[];
  onReact?: (content: string) => void;
  onEdit?: (body: string) => Promise<unknown>;
  onDelete?: () => void;
}) {
  const viewer = useContext(ViewerContext);
  const mine = viewer.login === user || viewer.admin;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(body);
  return (
    <div className="flex gap-3">
      <Avatar login={user} />
      <div className="min-w-0 flex-1 overflow-hidden rounded-md border">
        <div className="flex items-center gap-1 border-b bg-muted/40 px-3 py-1.5 text-sm">
          <b>{user}</b>{" "}
          <span className="text-muted-foreground">
            commented {ago(at)}
            {edited ? " · edited" : ""}
          </span>
          {mine && (onEdit || onDelete) && !editing && (
            <span className="ml-auto flex gap-1">
              {onEdit && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="Edit comment"
                  onClick={() => {
                    setDraft(body);
                    setEditing(true);
                  }}
                >
                  <Pencil />
                </Button>
              )}
              {onDelete && (
                <Button
                  variant="ghost"
                  size="icon-xs"
                  title="Delete comment"
                  onClick={() => {
                    if (window.confirm("Delete this comment?")) onDelete();
                  }}
                >
                  <Trash2 />
                </Button>
              )}
            </span>
          )}
        </div>
        {editing ? (
          <div className="space-y-2 p-3">
            <MarkdownField
              value={draft}
              onValue={setDraft}
              rows={5}
              placeholder="Comment"
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                className="bg-emerald-600 text-white hover:bg-emerald-700"
                onClick={() =>
                  void onEdit?.(draft).then(
                    () => setEditing(false),
                    (e: Error) => toast.error(`GitHub said: ${e.message}`),
                  )
                }
              >
                Update comment
              </Button>
            </div>
          </div>
        ) : (
          <div className="px-4 py-2">
            <Markdown text={body} />
          </div>
        )}
        {onReact && !editing && (
          <ReactionBar
            reactions={reactions ?? {}}
            reacted={reacted ?? []}
            onReact={onReact}
          />
        )}
      </div>
    </div>
  );
}

/** An issue's title, editable by its author or an admin. */
function TitleEditor({
  item,
  repo,
  onDone,
}: {
  item: IssueDetail;
  repo: string;
  onDone: () => void;
}) {
  const viewer = useContext(ViewerContext);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  if (editing)
    return (
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void github.editIssue(repo, item.number, { title }).then(
            () => {
              setEditing(false);
              onDone();
            },
            (err: Error) => toast.error(`GitHub said: ${err.message}`),
          );
        }}
      >
        <Input
          autoFocus
          placeholder="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="text-lg"
        />
        <Button type="submit" size="sm" disabled={!title.trim()}>
          Save
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setEditing(false)}
        >
          Cancel
        </Button>
      </form>
    );
  return (
    <h1 className="flex items-start gap-2 text-2xl">
      <span>
        {item.title}{" "}
        <span className="text-muted-foreground">#{item.number}</span>
      </span>
      {(viewer.login === item.user || viewer.admin) && (
        <Button
          variant="outline"
          size="sm"
          className="ml-auto"
          onClick={() => {
            setTitle(item.title);
            setEditing(true);
          }}
        >
          Edit
        </Button>
      )}
    </h1>
  );
}

/** A markdown text box with GitHub's Write and Preview tabs (and @-mentions). */
function MarkdownField(props: React.ComponentProps<typeof MentionTextarea>) {
  const [preview, setPreview] = useState(false);
  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex gap-1 overflow-x-auto border-b bg-muted/40 px-2 pt-1 text-sm [scrollbar-width:none]">
        {[false, true].map((p) => (
          <button
            key={String(p)}
            type="button"
            onClick={() => setPreview(p)}
            className={`rounded-t-md px-3 py-1 ${preview === p ? "border border-b-0 bg-background font-semibold" : "text-muted-foreground"}`}
          >
            {p ? "Preview" : "Write"}
          </button>
        ))}
      </div>
      {preview ? (
        <div className="min-h-24 px-4 py-2">
          <Markdown text={props.value || "Nothing to preview"} />
        </div>
      ) : (
        <div className="p-2">
          <MentionTextarea {...props} />
        </div>
      )}
    </div>
  );
}

function CloneButton({ repo }: { repo: RepoDetails }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          size="sm"
          title="Clone this repository"
          className="bg-emerald-600 text-white hover:bg-emerald-700"
        >
          <Code2 /> Code <ChevronDown />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 space-y-2">
        <div className="text-sm font-semibold">Clone with HTTPS</div>
        <div className="flex items-center gap-2">
          <Input
            readOnly
            value={repo.clone_url}
            className="h-8 font-mono text-xs"
          />
          <Button
            variant="outline"
            size="icon-sm"
            title="Copy"
            onClick={() =>
              void navigator.clipboard
                .writeText(repo.clone_url)
                .then(() => toast.success("Copied"))
            }
          >
            <Copy />
          </Button>
        </div>
        <div className="text-xs text-muted-foreground">
          When git asks for a password, give it a GitHub token from{" "}
          <b>Your tokens</b> (in the menu under your name).
        </div>
      </PopoverContent>
    </Popover>
  );
}

function ReviewBox({
  review,
  repo,
  number,
  onDone,
}: {
  review: Extract<IssueDetail["timeline"][number], { type: "review" }>;
  repo: string;
  number: number;
  onDone: () => void;
}) {
  const verb =
    {
      APPROVED: "approved these changes",
      CHANGES_REQUESTED: "requested changes",
      COMMENTED: "reviewed",
      DISMISSED: "reviewed (dismissed)",
    }[review.state] ?? "reviewed";
  const [reply, setReply] = useState<{ id: number; text: string } | null>(null);
  const roots = review.comments.filter((c) => !c.in_reply_to);
  const replies = review.comments.filter((c) => c.in_reply_to);
  if (replies.length && !roots.length && !review.body) return null;
  return (
    <div className="flex gap-3">
      <Avatar login={review.user} />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2 text-sm">
          {review.state === "APPROVED" ? (
            <Check className="size-4 text-emerald-600" />
          ) : review.state === "CHANGES_REQUESTED" ? (
            <X className="size-4 text-red-600" />
          ) : (
            <MessageSquare className="size-4 text-muted-foreground" />
          )}
          <b>{review.user}</b>{" "}
          <span className="text-muted-foreground">
            {verb} {ago(review.at)}
          </span>
        </div>
        {review.body && (
          <div className="rounded-md border px-4 py-2">
            <Markdown text={review.body} />
          </div>
        )}
        {roots.map((c) => (
          <div key={c.id} className="rounded-md border">
            <div className="border-b bg-muted/40 px-3 py-1 font-mono text-xs">
              {c.path}
              {c.line ? `:${c.line}` : ""}
            </div>
            <div className="px-4 py-2">
              <Markdown text={c.body} />
            </div>
            {reply?.id === c.id ? (
              <div className="space-y-2 border-t p-2">
                <MentionTextarea
                  rows={2}
                  placeholder="Reply…"
                  value={reply.text}
                  onValue={(v) => setReply({ id: c.id, text: v })}
                />
                <div className="flex justify-end gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setReply(null)}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    disabled={!reply.text.trim()}
                    onClick={() =>
                      void github
                        .reply(repo, number, c.id, { body: reply.text })
                        .then(
                          () => {
                            setReply(null);
                            onDone();
                          },
                          (e) => toast.error((e as Error).message),
                        )
                    }
                  >
                    Reply
                  </Button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="w-full border-t px-3 py-1 text-left text-xs text-muted-foreground hover:bg-accent"
                onClick={() => setReply({ id: c.id, text: "" })}
              >
                Reply…
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Reply({
  item,
  repo,
  actor,
  onDone,
  fail,
}: {
  item: IssueDetail;
  repo: string;
  actor: string;
  onDone: () => void;
  fail: (e: unknown) => void;
}) {
  const [text, setText] = useState("");
  const send = () =>
    void github.comment(repo, item.number, { body: text }).then(() => {
      setText("");
      onDone();
    }, fail);
  const close = () =>
    void (
      text.trim()
        ? github.comment(repo, item.number, { body: text })
        : Promise.resolve()
    )
      .then(() =>
        github.editIssue(repo, item.number, {
          state: item.state === "open" ? "closed" : "open",
        }),
      )
      .then(() => {
        setText("");
        onDone();
      }, fail);
  return (
    <div className="flex gap-3">
      <Avatar login={actor} />
      <div className="flex-1 space-y-2">
        <MarkdownField
          value={text}
          onValue={setText}
          rows={4}
          placeholder={`Comment as ${actor} (markdown)`}
        />
        <div className="flex justify-end gap-2">
          {!item.merged && (
            <Button variant="outline" size="sm" onClick={close}>
              {item.state === "open"
                ? `Close ${item.is_pull ? "pull request" : "issue"}`
                : "Reopen"}
            </Button>
          )}
          <Button
            size="sm"
            disabled={!text.trim()}
            className="bg-emerald-600 text-white hover:bg-emerald-700"
            onClick={send}
          >
            Comment
          </Button>
        </div>
      </div>
    </div>
  );
}

/** A list of the workplace's people to pick some of (GitHub's sidebar gear). */
function PeoplePicker({
  title,
  selected,
  onToggle,
  exclude = [],
  max = 10,
}: {
  title: string;
  selected: string[];
  onToggle: (login: string) => void;
  exclude?: string[];
  max?: number;
}) {
  const people = useContext(MentionsContext);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const shown = people
    .filter((p) => !exclude.includes(p.login))
    .filter(
      (p) =>
        !q ||
        p.login.toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q),
    )
    .sort(
      (a, b) =>
        Number(selected.includes(b.login)) -
          Number(selected.includes(a.login)) || a.login.localeCompare(b.login),
    );
  return (
    <Popover
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        setFilter("");
      }}
    >
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon-xs" title={title}>
          <Settings />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-0">
        <div className="border-b p-2 text-xs font-semibold">{title}</div>
        <div className="border-b p-2">
          <Input
            autoFocus
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Type or choose a name"
            className="h-7 text-xs"
          />
        </div>
        <div className="max-h-72 overflow-y-auto py-1">
          {shown.map((p) => (
            <button
              key={p.login}
              type="button"
              onClick={() => onToggle(p.login)}
              disabled={!selected.includes(p.login) && selected.length >= max}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-accent disabled:opacity-50"
            >
              <Check
                className={`size-3.5 shrink-0 ${selected.includes(p.login) ? "" : "invisible"}`}
              />
              <PersonMark person={p} />
              <span className="font-semibold">{p.login}</span>
              <span className="truncate text-muted-foreground">{p.name}</span>
            </button>
          ))}
          {shown.length === 0 && (
            <div className="px-3 py-2 text-xs text-muted-foreground">
              No one matches.
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function PeopleList({
  logins,
  empty,
}: {
  logins: string[];
  empty: React.ReactNode;
}) {
  if (logins.length === 0)
    return <div className="text-muted-foreground">{empty}</div>;
  return (
    <div className="space-y-1">
      {logins.map((login) => (
        <div key={login} className="flex items-center gap-2">
          <Avatar login={login} />
          <span className="font-semibold">{login}</span>
        </div>
      ))}
    </div>
  );
}

const toggled = (list: string[], login: string) =>
  list.includes(login) ? list.filter((l) => l !== login) : [...list, login];

/** Assignees, as GitHub's sidebar has them. */
function AssignBox({
  item,
  repo,
  actor,
  onDone,
}: {
  item: IssueDetail;
  repo: string;
  actor: string;
  onDone: () => void;
}) {
  const save = (assignees: string[]) =>
    void github
      .editIssue(repo, item.number, { assignees })
      .then(onDone, (e) => toast.error((e as Error).message));
  return (
    <div>
      <div className="mb-1 flex items-center justify-between font-semibold">
        Assignees
        <PeoplePicker
          title={`Assign up to 10 people to this ${item.is_pull ? "pull request" : "issue"}`}
          selected={item.assignees}
          onToggle={(login) => save(toggled(item.assignees, login))}
        />
      </div>
      <PeopleList
        logins={item.assignees}
        empty={
          <>
            No one —{" "}
            <button
              type="button"
              className="hover:text-sky-600 hover:underline"
              onClick={() => save([actor])}
            >
              assign yourself
            </button>
          </>
        }
      />
    </div>
  );
}

/** A pull request's reviewers: whose review is requested. */
function ReviewersBox({
  item,
  repo,
  onDone,
}: {
  item: IssueDetail;
  repo: string;
  onDone: () => void;
}) {
  const requested = item.requested_reviewers ?? [];
  return (
    <div>
      <div className="mb-1 flex items-center justify-between font-semibold">
        Reviewers
        <PeoplePicker
          title="Request up to 10 reviewers"
          selected={requested}
          exclude={[item.user]}
          onToggle={(login) =>
            void github
              .setReviewers(repo, item.number, toggled(requested, login))
              .then(onDone, (e) => toast.error((e as Error).message))
          }
        />
      </div>
      <PeopleList logins={requested} empty="No reviews requested" />
    </div>
  );
}

function MergeBox({
  item,
  repo,
  actor,
  onDone,
}: {
  item: IssueDetail;
  repo: string;
  actor: string;
  onDone: () => void;
}) {
  const [method, setMethod] = useState("squash");
  const { admin } = useContext(ViewerContext);
  const [bypass, setBypass] = useState(false);
  if (item.merged || item.state === "closed")
    return (
      <div className="space-y-2">
        {item.merged && (
          <div className="flex items-center gap-2 rounded-md border border-violet-500/40 bg-violet-500/10 p-3 text-sm">
            <GitMerge className="size-4 text-violet-600" />{" "}
            <b>{item.merged_by}</b> merged this pull request.
          </div>
        )}
        <BranchBox item={item} repo={repo} onDone={onDone} />
      </div>
    );
  const state = item.mergeable_state;
  const unmet = item.unmet_checks ?? [];
  const message =
    {
      clean: "This branch has no conflicts with the base branch.",
      blocked:
        unmet.length > 0
          ? "Merging is blocked: required checks have not passed."
          : "Merging is blocked: an approving review is required.",
      dirty: "This branch has conflicts that must be resolved.",
      unstable: "Some checks were not successful.",
      draft: "This pull request is still a draft.",
    }[state ?? ""] ?? state;
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm">
      <div className="flex items-center gap-2">
        {state === "clean" ? (
          <Check className="size-4 text-emerald-600" />
        ) : (
          <X className="size-4 text-amber-600" />
        )}
        {message}
      </div>
      {unmet.length > 0 && (
        <ul className="space-y-1 pl-6 text-xs text-muted-foreground">
          {unmet.map((c) => (
            <li key={c.name}>
              <code>{c.name}</code> is {c.state}
            </li>
          ))}
        </ul>
      )}
      {(item.behind_by ?? 0) > 0 && state !== "dirty" && (
        <div className="flex items-center gap-2 rounded-md bg-muted/50 p-2">
          This branch is out-of-date with the base branch.
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() =>
              void github
                .updateBranch(repo, item.number)
                .then(onDone, (e) =>
                  toast.error(`GitHub said: ${(e as Error).message}`),
                )
            }
          >
            Update branch
          </Button>
        </div>
      )}
      {state === "draft" && (
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            void github
              .editIssue(repo, item.number, { draft: false })
              .then(onDone, (e) =>
                toast.error(`GitHub said: ${(e as Error).message}`),
              )
          }
        >
          Ready for review
        </Button>
      )}
      {state === "blocked" && admin && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={bypass}
            onChange={(e) => setBypass(e.target.checked)}
          />
          Merge without waiting for requirements to be met (bypass branch
          protections)
        </label>
      )}
      <div className="flex items-center gap-2">
        <NativeSelect
          value={method}
          onChange={(e) => setMethod(e.target.value)}
          className="w-44 text-sm"
        >
          <NativeSelectOption value="squash">
            Squash and merge
          </NativeSelectOption>
          <NativeSelectOption value="merge">
            Create a merge commit
          </NativeSelectOption>
          <NativeSelectOption value="rebase">
            Rebase and merge
          </NativeSelectOption>
        </NativeSelect>
        <Button
          size="sm"
          className="bg-emerald-600 text-white hover:bg-emerald-700"
          disabled={
            state === "dirty" ||
            state === "draft" ||
            (state === "blocked" && !bypass)
          }
          onClick={() =>
            void github
              .merge(repo, item.number, {
                method,
                bypass: state === "blocked" && bypass,
              })
              .then(onDone, (e) =>
                toast.error(`GitHub said: ${(e as Error).message}`),
              )
          }
        >
          <GitMerge /> Merge as {actor}
        </Button>
      </div>
    </div>
  );
}

function ReviewSubmit({
  item,
  repo,
  actor,
  drafts,
  onDone,
}: {
  item: IssueDetail;
  repo: string;
  actor: string;
  drafts: Draft[];
  onDone: () => void;
}) {
  const [event, setEvent] = useState("COMMENT");
  const [body, setBody] = useState("");
  return (
    <div className="space-y-2 rounded-md border p-3 text-sm">
      <div className="font-semibold">
        Review as {actor}{" "}
        {drafts.length > 0 && (
          <Badge variant="secondary">{drafts.length} pending comments</Badge>
        )}
      </div>
      <MarkdownField
        rows={3}
        value={body}
        onValue={setBody}
        placeholder="Leave a comment"
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {[
          ["COMMENT", "Comment"],
          ["APPROVE", "Approve"],
          ["REQUEST_CHANGES", "Request changes"],
        ].map(([v, l]) => (
          <label key={v} className="flex items-center gap-1 whitespace-nowrap">
            <input
              type="radio"
              checked={event === v}
              onChange={() => setEvent(v)}
            />{" "}
            {l}
          </label>
        ))}
        <Button
          size="sm"
          className="ml-auto bg-emerald-600 text-white hover:bg-emerald-700"
          onClick={() =>
            void github
              .review(repo, item.number, {
                event,
                body,
                comments: drafts,
              })
              .then(
                () => {
                  setBody("");
                  onDone();
                },
                (e) => toast.error(`GitHub said: ${(e as Error).message}`),
              )
          }
        >
          Submit review
        </Button>
      </div>
    </div>
  );
}

// -- actions ----------------------------------------------------------------------------

function RunIcon({
  status,
  conclusion,
}: {
  status: string;
  conclusion: string | null;
}) {
  if (status === "queued")
    return (
      <span className="size-3 shrink-0 rounded-full border-2 border-amber-500" />
    );
  if (status !== "completed")
    return (
      <span className="size-3 shrink-0 animate-pulse rounded-full bg-amber-500" />
    );
  if (conclusion === "success")
    return <Check className="size-4 shrink-0 text-emerald-600" />;
  if (conclusion === "skipped")
    return (
      <span className="size-4 shrink-0 text-center text-muted-foreground">
        –
      </span>
    );
  if (conclusion === "cancelled")
    return <X className="size-4 shrink-0 text-muted-foreground" />;
  return <X className="size-4 shrink-0 text-red-600" />;
}

function ActionsView({
  repo,
  version,
  navigate,
}: {
  repo: RepoDetails;
  version: number;
  navigate: (p: string) => void;
}) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [only, setOnly] = useState<number | null>(null);
  const [openRun, setOpenRun] = useState<number | null>(null);
  const load = useCallback(() => {
    void github.runs(repo.full_name).then((r) => setRuns(r.runs));
    void github
      .workflows(repo.full_name)
      .then((w) => setWorkflows(w.workflows));
  }, [repo.full_name]);
  useEffect(load, [load, version]);
  // Live logs: while anything runs, look again every moment.
  const running = runs.some((r) => r.status !== "completed");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(
      () => void github.runs(repo.full_name).then((r) => setRuns(r.runs)),
      1500,
    );
    return () => clearInterval(timer);
  }, [running, repo.full_name]);
  const fail = (e: unknown) =>
    toast.error(`GitHub said: ${(e as Error).message}`);
  const shown = runs.filter((r) => only === null || r.workflow_id === only);
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="space-y-1 text-sm">
        <div className="mb-2 font-semibold">Actions</div>
        <button
          type="button"
          onClick={() => setOnly(null)}
          className={`block w-full rounded-md px-2 py-1 text-left ${only === null ? "bg-accent font-semibold" : "hover:bg-accent"}`}
        >
          All workflows
        </button>
        {workflows.map((w) => (
          <button
            key={w.id}
            type="button"
            title={w.path}
            onClick={() => setOnly(w.id)}
            className={`block w-full truncate rounded-md px-2 py-1 text-left ${only === w.id ? "bg-accent font-semibold" : "hover:bg-accent"}`}
          >
            {w.name}
          </button>
        ))}
        {workflows.length === 0 && (
          <div className="px-2 text-xs text-muted-foreground">
            No workflows. Add one in <code>.github/workflows/</code>.
          </div>
        )}
      </aside>
      <div className="min-w-0 space-y-4">
        {only !== null && workflows.find((w) => w.id === only)?.dispatch && (
          <DispatchBox
            repo={repo}
            workflow={workflows.find((w) => w.id === only)!}
            onDone={load}
          />
        )}
        {workflows.length > 0 && (
          <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
            Workflows run on this machine: their steps run here, in a fresh
            checkout, with this machine&apos;s tools. Where a job asks to run (
            <code>runs-on</code>, containers, services) is not used, and actions
            other than checkout and setup-python/node are skipped.
          </div>
        )}
        <div className="divide-y rounded-md border text-sm">
          {shown.length === 0 && (
            <div className="p-4 text-muted-foreground">
              No workflow runs yet.
            </div>
          )}
          {shown.map((r) => (
            <div key={r.id}>
              <button
                type="button"
                className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-accent"
                onClick={() => setOpenRun(openRun === r.id ? null : r.id)}
              >
                <RunIcon status={r.status} conclusion={r.conclusion} />
                <div className="min-w-0">
                  <div className="truncate font-medium">
                    {r.title || r.head_sha.slice(0, 7)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {r.name} #{r.run_number}
                    {r.run_attempt > 1
                      ? ` (attempt ${r.run_attempt})`
                      : ""} · {r.event} on {r.head_branch} · {ago(r.at)}
                    {r.pull_requests.map((n) => (
                      <span key={n}>
                        {" "}
                        ·{" "}
                        <a
                          className="text-sky-600 hover:underline"
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/${repo.full_name}/pull/${n}`);
                          }}
                        >
                          #{n}
                        </a>
                      </span>
                    ))}
                  </div>
                </div>
                <span className="ml-auto text-xs text-muted-foreground">
                  {r.conclusion ?? r.status.replace("_", " ")}
                </span>
              </button>
              {openRun === r.id && (
                <RunDetail repo={repo} run={r} onDone={load} fail={fail} />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function RunDetail({
  repo,
  run,
  onDone,
  fail,
}: {
  repo: RepoDetails;
  run: WorkflowRun;
  onDone: () => void;
  fail: (e: unknown) => void;
}) {
  const [job, setJob] = useState(run.jobs[0]?.id ?? null);
  const current = run.jobs.find((j) => j.id === job) ?? run.jobs[0];
  const logEnd = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (current?.status === "in_progress")
      logEnd.current?.scrollIntoView({ block: "nearest" });
  }, [current?.log, current?.status]);
  return (
    <div className="space-y-2 bg-muted/20 px-4 py-3">
      <div className="flex flex-wrap gap-2">
        {run.status === "completed" ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void github.rerun(repo.full_name, run.id).then(onDone, fail)
            }
          >
            <RefreshCw /> Re-run
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              void github.cancelRun(repo.full_name, run.id).then(onDone, fail)
            }
          >
            <X /> Cancel run
          </Button>
        )}
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[200px_minmax(0,1fr)]">
        <div className="space-y-1 text-sm">
          {run.jobs.map((j) => (
            <button
              key={j.id}
              type="button"
              onClick={() => setJob(j.id)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left ${j.id === current?.id ? "bg-accent font-semibold" : "hover:bg-accent"}`}
            >
              <RunIcon status={j.status} conclusion={j.conclusion} />
              <span className="truncate">{j.name}</span>
            </button>
          ))}
        </div>
        {current && (
          <div className="min-w-0 space-y-2">
            {current.steps.length > 0 && (
              <div className="divide-y rounded-md border bg-background text-sm">
                {current.steps.map((st) => (
                  <div
                    key={st.number}
                    className="flex items-center gap-2 px-3 py-1"
                  >
                    <RunIcon status={st.status} conclusion={st.conclusion} />
                    <span className="truncate">{st.name}</span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {st.conclusion ?? st.status.replace("_", " ")}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <pre className="max-h-96 overflow-auto rounded-md border bg-background p-2 text-xs">
              {current.log ? (
                <LogLines log={current.log} />
              ) : (
                `${current.name}: ${current.status.replace("_", " ")}`
              )}
              <div ref={logEnd} />
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function DispatchBox({
  repo,
  workflow,
  onDone,
}: {
  repo: RepoDetails;
  workflow: WorkflowInfo;
  onDone: () => void;
}) {
  const [ref, setRef] = useState(repo.default_branch);
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm">
      This workflow has a <code>workflow_dispatch</code> trigger.
      <NativeSelect
        value={ref}
        onChange={(e) => setRef(e.target.value)}
        className="ml-auto w-48 text-sm"
      >
        {repo.branches.map((b) => (
          <NativeSelectOption key={b.name} value={b.name}>
            {b.name}
          </NativeSelectOption>
        ))}
      </NativeSelect>
      <Button
        size="sm"
        className="bg-emerald-600 text-white hover:bg-emerald-700"
        onClick={() =>
          void github.dispatch(repo.full_name, workflow.id, ref).then(
            () => {
              toast.success(`${workflow.name} is running on ${ref}`);
              onDone();
            },
            (e: Error) => toast.error(`GitHub said: ${e.message}`),
          )
        }
      >
        <Play /> Run workflow
      </Button>
    </div>
  );
}

function NewRepo({ onCreated }: { onCreated: (fullName: string) => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: "",
    description: "",
    private: false,
    auto_init: true,
    protect_default_branch: true,
  });
  if (!open)
    return (
      <div className="mb-4 flex justify-end">
        <Button size="sm" onClick={() => setOpen(true)}>
          New repository
        </Button>
      </div>
    );
  const check = (
    key: "private" | "auto_init" | "protect_default_branch",
    label: string,
  ) => (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={form[key]}
        onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
      />
      {label}
    </label>
  );
  return (
    <form
      className="mb-4 space-y-3 rounded-md border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        github.createRepo(form).then(
          ({ full_name }) => {
            setOpen(false);
            onCreated(full_name);
          },
          (err: Error) => toast.error(err.message),
        );
      }}
    >
      <div className="grid grid-cols-2 gap-3">
        <Input
          placeholder="Name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <Input
          placeholder="Description"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
      </div>
      <div className="flex flex-wrap gap-4">
        {check("auto_init", "Start with a README")}
        {check(
          "protect_default_branch",
          "Protect main (changes through pull requests)",
        )}
        {check("private", "Private")}
      </div>
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setOpen(false)}
        >
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={!form.name.trim()}>
          Create
        </Button>
      </div>
    </form>
  );
}

/** A repository without commits yet: how to put something in it (GitHub's quick setup). */
function EmptyRepo({ repo }: { repo: RepoDetails }) {
  const url = repo.clone_url;
  const steps = `# a new repository
git clone ${url}
cd ${repo.full_name.split("/")[1]}
echo "# ${repo.full_name.split("/")[1]}" > README.md
git add README.md
git commit -m "First commit"
git push -u origin ${repo.default_branch}

# or an existing one
git remote add origin ${url}
git push -u origin ${repo.default_branch}`;
  return (
    <div className="space-y-4 rounded-md border p-6">
      <div>
        <div className="font-semibold">Quick setup</div>
        <div className="text-sm text-muted-foreground">
          This repository is empty. Clone it at{" "}
          <code className="rounded bg-muted px-1">{url}</code>
        </div>
      </div>
      <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs">
        {steps}
      </pre>
      <div className="text-sm text-muted-foreground">
        Git asks for a username and password when you push: the password is a
        GitHub token from <b>Your tokens</b> (in the menu under your name).
      </div>
    </div>
  );
}

/** "<branch> had recent pushes": the signed-in person's branches pushed in the last hour, without a pull request. */
function RecentPushes({
  repo,
  version,
  navigate,
}: {
  repo: RepoDetails;
  version: number;
  navigate: (p: string) => void;
}) {
  const [pushes, setPushes] = useState<{ branch: string; at: string }[]>([]);
  useEffect(() => {
    void github.recentPushes(repo.full_name).then(
      (r) => setPushes(r.pushes),
      () => setPushes([]),
    );
  }, [repo.full_name, version]);
  if (pushes.length === 0) return null;
  return (
    <div className="mb-4 space-y-2">
      {pushes.map((p) => (
        <div
          key={p.branch}
          className="flex items-center gap-3 rounded-md border border-amber-400/60 bg-amber-100/60 px-4 py-2 text-sm dark:bg-amber-500/10"
        >
          <GitBranch className="size-4 text-muted-foreground" />
          <span>
            <code className="rounded bg-sky-500/10 px-1 text-sky-700 dark:text-sky-300">
              {p.branch}
            </code>{" "}
            had recent pushes {ago(p.at)}
          </span>
          <Button
            size="sm"
            className="ml-auto bg-emerald-600 text-white hover:bg-emerald-700"
            onClick={() =>
              navigate(
                `/${repo.full_name}/compare/${repo.default_branch}...${p.branch}`,
              )
            }
          >
            Compare &amp; pull request
          </Button>
        </div>
      ))}
    </div>
  );
}

/** Opening a pull request: pick the branches, see what they hold, write it up. */
function CompareView({
  repo,
  base,
  head,
  navigate,
}: {
  repo: RepoDetails;
  base: string;
  head?: string;
  navigate: (p: string) => void;
}) {
  const [compare, setCompare] = useState<Compare | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState(false);
  useEffect(() => {
    setCompare(null);
    if (!head) return;
    github.compare(repo.full_name, base, head).then(
      (c) => {
        setCompare(c);
        setTitle(
          c.commits.length === 1
            ? c.commits[0].subject
            : head
                .replace(/[-_/]+/g, " ")
                .replace(/^./, (x) => x.toUpperCase()),
        );
        setBody(
          c.commits.length === 1
            ? c.commits[0].message.split("\n").slice(1).join("\n").trim()
            : "",
        );
      },
      (e: Error) => toast.error(e.message),
    );
  }, [repo.full_name, base, head]);
  const go = (b: string, h?: string) =>
    navigate(`/${repo.full_name}/compare/${h ? `${b}...${h}` : ""}`);
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl">Compare changes</h1>
        <p className="text-sm text-muted-foreground">
          Choose two branches to see what changed, or to start a pull request.
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2 rounded-md border bg-muted/30 p-3 text-sm">
        base:
        <NativeSelect
          value={base}
          onChange={(e) => go(e.target.value, head)}
          className="w-48 text-sm"
        >
          {repo.branches.map((b) => (
            <NativeSelectOption key={b.name} value={b.name}>
              {b.name}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        ← compare:
        <NativeSelect
          value={head ?? ""}
          onChange={(e) => go(base, e.target.value)}
          className="w-48 text-sm"
        >
          <NativeSelectOption value="">Choose a branch</NativeSelectOption>
          {repo.branches
            .filter((b) => b.name !== base)
            .map((b) => (
              <NativeSelectOption key={b.name} value={b.name}>
                {b.name}
              </NativeSelectOption>
            ))}
        </NativeSelect>
        {compare && (
          <span
            className={compare.mergeable ? "text-emerald-600" : "text-red-600"}
          >
            {compare.mergeable
              ? "✓ Able to merge."
              : "✗ Can't automatically merge."}
          </span>
        )}
      </div>
      {compare && compare.commits.length === 0 && (
        <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
          {base} and {head} are identical: there is nothing to compare.
        </div>
      )}
      {compare && compare.existing !== null && (
        <div className="rounded-md border p-4 text-sm">
          A pull request from {head} into {base} is already open:{" "}
          <button
            type="button"
            className="text-sky-600 hover:underline"
            onClick={() =>
              navigate(`/${repo.full_name}/pull/${compare.existing}`)
            }
          >
            #{compare.existing}
          </button>
        </div>
      )}
      {compare && compare.commits.length > 0 && compare.existing === null && (
        <form
          className="space-y-3 rounded-md border p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void github
              .createPull(repo.full_name, {
                title,
                body,
                head: head!,
                base,
                draft,
              })
              .then(
                (pr) => navigate(`/${repo.full_name}/pull/${pr.number}`),
                (err: Error) => toast.error(err.message),
              );
          }}
        >
          <Input
            placeholder="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <MarkdownField
            rows={6}
            placeholder="Add a description (markdown)"
            value={body}
            onValue={setBody}
          />
          <div className="flex items-center justify-end gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft}
                onChange={(e) => setDraft(e.target.checked)}
              />
              Draft
            </label>
            <Button
              type="submit"
              disabled={!title.trim()}
              className="bg-emerald-600 text-white hover:bg-emerald-700"
            >
              {draft ? "Create draft pull request" : "Create pull request"}
            </Button>
          </div>
        </form>
      )}
      {compare && compare.commits.length > 0 && (
        <>
          <div className="text-sm text-muted-foreground">
            {compare.commits.length} commit
            {compare.commits.length === 1 ? "" : "s"} · {compare.files.length}{" "}
            file{compare.files.length === 1 ? "" : "s"} changed
          </div>
          <div className="divide-y rounded-md border text-sm">
            {compare.commits.map((c) => (
              <div key={c.sha} className="flex items-center gap-2 px-4 py-2">
                <span className="truncate">{c.subject}</span>
                <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground">
                  {c.sha.slice(0, 7)}
                </span>
              </div>
            ))}
          </div>
          {compare.files.map((f) => (
            <FileDiff key={f.filename} file={f} />
          ))}
        </>
      )}
    </div>
  );
}

/** After a pull request is merged or closed: delete its branch, or restore it. */
function BranchBox({
  item,
  repo,
  onDone,
}: {
  item: IssueDetail;
  repo: string;
  onDone: () => void;
}) {
  const head = item.head ?? "";
  const fail = (e: unknown) =>
    toast.error(`GitHub said: ${(e as Error).message}`);
  if (item.head_exists)
    return (
      <div className="flex items-center gap-3 rounded-md border p-3 text-sm">
        <span>
          {item.merged
            ? "Pull request successfully merged and closed. "
            : "Closed with unmerged commits. "}
          The <code className="rounded bg-muted px-1">{head}</code> branch can
          be safely deleted.
        </span>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={() =>
            void github.deleteBranch(repo, head).then(onDone, fail)
          }
        >
          Delete branch
        </Button>
      </div>
    );
  return (
    <div className="flex items-center gap-3 rounded-md border p-3 text-sm text-muted-foreground">
      <span>
        The <code className="rounded bg-muted px-1">{head}</code> branch was
        deleted.
      </span>
      {item.head_sha && (
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={() =>
            void github
              .restoreBranch(repo, head, item.head_sha!)
              .then(onDone, fail)
          }
        >
          Restore branch
        </Button>
      )}
    </div>
  );
}

// -- repository management ------------------------------------------------------

/** A protected branch's required checks: pull requests into it merge once they pass. */
function RequiredChecks({
  branch,
  repo,
  save,
}: {
  branch: string;
  repo: RepoDetails;
  save: (checks: string[], done: string) => void;
}) {
  const required = repo.required_checks[branch] ?? [];
  const [adding, setAdding] = useState("");
  const choices = repo.check_names.filter((n) => !required.includes(n));
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs">
      {required.length === 0 && (
        <span className="text-muted-foreground">No required checks</span>
      )}
      {required.map((name) => (
        <Badge key={name} variant="secondary" className="gap-1">
          {name}
          <button
            type="button"
            aria-label={`Stop requiring ${name}`}
            className="opacity-60 hover:opacity-100"
            onClick={() =>
              save(
                required.filter((n) => n !== name),
                `${name} is no longer required`,
              )
            }
          >
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      {choices.length > 0 && (
        <NativeSelect
          value={adding}
          onChange={(e) => {
            const name = e.target.value;
            setAdding("");
            if (name) save([...required, name], `${name} is required`);
          }}
          className="h-7 w-48 text-xs"
          aria-label={`Require a check on ${branch}`}
        >
          <NativeSelectOption value="">Require a check…</NativeSelectOption>
          {choices.map((n) => (
            <NativeSelectOption key={n} value={n}>
              {n}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      )}
    </div>
  );
}

function SettingsView({
  repo,
  navigate,
  onChanged,
}: {
  repo: RepoDetails;
  navigate: (p: string) => void;
  onChanged: () => void;
}) {
  const [description, setDescription] = useState(repo.description);
  const [defaultBranch, setDefaultBranch] = useState(repo.default_branch);
  const [newRule, setNewRule] = useState("");
  const [confirm, setConfirm] = useState("");
  const fail = (e: unknown) =>
    toast.error(`GitHub said: ${(e as Error).message}`);
  const save = (body: Parameters<typeof github.settings>[1], done = "Saved") =>
    void github.settings(repo.full_name, body).then(() => {
      toast.success(done);
      onChanged();
    }, fail);
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h2 className="border-b pb-2 text-lg">General</h2>
        <div className="space-y-1">
          <Label htmlFor="repo-description">Description</Label>
          <div className="flex gap-2">
            <Input
              id="repo-description"
              placeholder="A JSON API for tasks"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            <Button variant="outline" onClick={() => save({ description })}>
              Save
            </Button>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="repo-default">Default branch</Label>
          <div className="flex gap-2">
            <NativeSelect
              id="repo-default"
              value={defaultBranch}
              onChange={(e) => setDefaultBranch(e.target.value)}
              className="w-64"
            >
              {repo.branches.map((b) => (
                <NativeSelectOption key={b.name} value={b.name}>
                  {b.name}
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <Button
              variant="outline"
              disabled={defaultBranch === repo.default_branch}
              onClick={() =>
                save(
                  { default_branch: defaultBranch },
                  "Default branch changed",
                )
              }
            >
              Update
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            What clones check out, and where pull requests go by default.
          </p>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="border-b pb-2 text-lg">Branch protection</h2>
        <p className="text-sm text-muted-foreground">
          A protected branch changes only through pull requests, which need an
          approving review and its required checks to pass before they merge
          (an admin may bypass both), and it cannot be deleted.
        </p>
        <div className="divide-y rounded-md border text-sm">
          {repo.protected.length === 0 && (
            <div className="p-3 text-muted-foreground">
              No branch is protected.
            </div>
          )}
          {repo.protected.map((b) => (
            <div key={b} className="flex flex-wrap items-center gap-2 p-3">
              <Lock className="size-4 text-muted-foreground" />
              <code>{b}</code>
              <RequiredChecks
                branch={b}
                repo={repo}
                save={(checks, done) =>
                  save(
                    {
                      required_checks: {
                        ...repo.required_checks,
                        [b]: checks,
                      },
                    },
                    done,
                  )
                }
              />
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                onClick={() =>
                  save(
                    { protected: repo.protected.filter((x) => x !== b) },
                    `${b} is no longer protected`,
                  )
                }
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <NativeSelect
            value={newRule}
            onChange={(e) => setNewRule(e.target.value)}
            className="w-64"
          >
            <NativeSelectOption value="">
              Choose a branch to protect
            </NativeSelectOption>
            {repo.branches
              .filter((b) => !repo.protected.includes(b.name))
              .map((b) => (
                <NativeSelectOption key={b.name} value={b.name}>
                  {b.name}
                </NativeSelectOption>
              ))}
          </NativeSelect>
          <Button
            variant="outline"
            disabled={!newRule}
            onClick={() => {
              save(
                { protected: [...repo.protected, newRule] },
                `${newRule} is protected`,
              );
              setNewRule("");
            }}
          >
            Protect
          </Button>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="border-b pb-2 text-lg">Runners</h2>
        <label className="flex items-start gap-2 text-sm">
          <Checkbox
            className="mt-0.5"
            checked={repo.self_hosted}
            onCheckedChange={(v) =>
              save(
                { self_hosted: !!v },
                v
                  ? "Jobs may use the self-hosted runner"
                  : "Jobs may no longer use the self-hosted runner",
              )
            }
          />
          <span>
            Jobs may use the self-hosted runner
            <span className="block text-muted-foreground">
              Jobs with <code>runs-on: self-hosted</code> get Docker and what
              the runner was given, such as the factory&apos;s model sign-in.
              Anyone who can push here can use them, so allow only repositories
              you trust.
            </span>
          </span>
        </label>
      </section>

      <section className="space-y-3 rounded-md border border-destructive/50 p-4">
        <h2 className="text-lg text-destructive">Delete this repository</h2>
        <p className="text-sm text-muted-foreground">
          Its code, issues, pull requests and runs are gone for good. Type{" "}
          <b>{repo.full_name}</b> to confirm.
        </p>
        <div className="flex gap-2">
          <Input
            placeholder={repo.full_name}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          <Button
            variant="destructive"
            disabled={confirm !== repo.full_name}
            onClick={() =>
              void github.deleteRepo(repo.full_name, confirm).then(() => {
                toast.success(`${repo.full_name} was deleted`);
                onChanged();
                navigate("/github");
              }, fail)
            }
          >
            <Trash2 /> Delete this repository
          </Button>
        </div>
      </section>
    </div>
  );
}

function BranchesView({
  repo,
  version,
  navigate,
}: {
  repo: RepoDetails;
  version: number;
  navigate: (p: string) => void;
}) {
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const load = () =>
    void github.branches(repo.full_name).then((r) => setBranches(r.branches));
  useEffect(load, [repo.full_name, version]);
  if (!branches)
    return <div className="text-sm text-muted-foreground">Loading…</div>;
  return (
    <div className="space-y-3">
      <h1 className="text-xl">Branches</h1>
      <div className="divide-y rounded-md border text-sm">
        {branches.map((b) => (
          <div key={b.name} className="flex items-center gap-3 px-4 py-2">
            <div className="min-w-0 flex-1">
              <button
                type="button"
                className="font-mono text-sky-600 hover:underline"
                onClick={() => navigate(`/${repo.full_name}/tree/${b.name}`)}
              >
                {b.name}
              </button>
              {b.default && (
                <Badge variant="secondary" className="ml-2">
                  default
                </Badge>
              )}
              {b.protected && (
                <Lock className="ml-2 inline size-3.5 text-muted-foreground" />
              )}
              {b.commit && (
                <div className="truncate text-xs text-muted-foreground">
                  {b.commit.subject} · {b.commit.author} · {ago(b.commit.date)}
                </div>
              )}
            </div>
            {!b.default && (
              <span
                className="w-24 text-right text-xs text-muted-foreground"
                title="Behind | ahead of the default branch"
              >
                {b.behind} | {b.ahead}
              </span>
            )}
            <span className="w-20 text-right">
              {b.pull && (
                <button
                  type="button"
                  className="text-xs text-sky-600 hover:underline"
                  onClick={() =>
                    navigate(`/${repo.full_name}/pull/${b.pull!.number}`)
                  }
                >
                  #{b.pull.number}{" "}
                  {b.pull.merged
                    ? "merged"
                    : b.pull.state === "open"
                      ? b.pull.draft
                        ? "draft"
                        : "open"
                      : "closed"}
                </button>
              )}
            </span>
            {!b.default && !b.protected ? (
              <Button
                variant="ghost"
                size="icon-sm"
                title={`Delete ${b.name}`}
                onClick={() => {
                  if (window.confirm(`Delete ${b.name}?`))
                    void github
                      .deleteBranch(repo.full_name, b.name)
                      .then(load, (e: Error) =>
                        toast.error(`GitHub said: ${e.message}`),
                      );
                }}
              >
                <Trash2 />
              </Button>
            ) : (
              <span className="w-8" />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function LabelsView({
  repo,
  onChanged,
}: {
  repo: RepoDetails;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    color: "#0e8a16",
    description: "",
  });
  const fail = (e: unknown) =>
    toast.error(`GitHub said: ${(e as Error).message}`);
  const done = () => {
    setEditing(null);
    setForm({ name: "", color: "#0e8a16", description: "" });
    onChanged();
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const body = {
      name: form.name.trim(),
      color: form.color.replace("#", ""),
      description: form.description,
    };
    void (
      editing
        ? github.editLabel(repo.full_name, editing, body)
        : github.createLabel(repo.full_name, body)
    ).then(done, fail);
  };
  return (
    <div className="space-y-4">
      <h1 className="text-xl">Labels</h1>
      <form
        onSubmit={submit}
        className="flex flex-wrap items-end gap-2 rounded-md border p-3"
      >
        <div className="space-y-1">
          <Label htmlFor="label-name">
            {editing ? `Edit ${editing}` : "New label"}
          </Label>
          <Input
            id="label-name"
            value={form.name}
            placeholder="bug"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>
        <div className="min-w-64 flex-1 space-y-1">
          <Label htmlFor="label-description">Description</Label>
          <Input
            id="label-description"
            value={form.description}
            placeholder="Something isn't working"
            onChange={(e) => setForm({ ...form, description: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="label-color">Color</Label>
          <input
            id="label-color"
            type="color"
            value={form.color}
            onChange={(e) => setForm({ ...form, color: e.target.value })}
            className="h-9 w-14 rounded-md border"
          />
        </div>
        {editing && (
          <Button type="button" variant="ghost" onClick={done}>
            Cancel
          </Button>
        )}
        <Button
          type="submit"
          disabled={!form.name.trim()}
          className="bg-emerald-600 text-white hover:bg-emerald-700"
        >
          {editing ? "Save changes" : "Create label"}
        </Button>
      </form>
      <div className="divide-y rounded-md border text-sm">
        {repo.labels.length === 0 && (
          <div className="p-4 text-muted-foreground">No labels yet.</div>
        )}
        {repo.labels.map((l) => (
          <div key={l.name} className="flex items-center gap-3 px-4 py-2">
            <Labels names={[l.name]} colors={new Map([[l.name, l.color]])} />
            <span className="flex-1 truncate text-muted-foreground">
              {l.description}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setEditing(l.name);
                setForm({
                  name: l.name,
                  color: `#${l.color}`,
                  description: l.description ?? "",
                });
              }}
            >
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                if (
                  window.confirm(
                    `Delete ${l.name}? It comes off every issue and pull request.`,
                  )
                )
                  void github
                    .deleteLabel(repo.full_name, l.name)
                    .then(onChanged, fail);
              }}
            >
              Delete
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** A job's log as GitHub shows it: steps as headings, errors and warnings marked. */
function LogLines({ log }: { log: string }) {
  return (
    <>
      {log.split("\n").map((raw, i) => {
        const line = raw.replace(/^\S+Z /, "");
        if (line.startsWith("##[endgroup]")) return null;
        if (line.startsWith("##[group]"))
          return (
            <div key={i} className="mt-1 font-semibold">
              ▸ {line.slice("##[group]".length)}
            </div>
          );
        if (line.startsWith("##[error]"))
          return (
            <div key={i} className="text-red-600">
              Error: {line.slice("##[error]".length)}
            </div>
          );
        if (line.startsWith("##[warning]"))
          return (
            <div key={i} className="text-amber-600">
              Warning: {line.slice("##[warning]".length)}
            </div>
          );
        return <div key={i}>{line || " "}</div>;
      })}
    </>
  );
}
