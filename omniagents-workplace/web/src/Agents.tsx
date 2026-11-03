import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import { FileText, History, Plus, Trash2, Workflow } from "lucide-react";
import { toast } from "sonner";
import { AgentAvatar } from "@omniagents/chat-ui/components/omni-avatar/AgentAvatar";
import { Button } from "@omniagents/ui/button";
import { Switch } from "@omniagents/ui/switch";
import { factoryFetch } from "./factory-scope";
import { humanize } from "./Automations";

const CodeMirrorEditor = lazy(() => import("@omniagents/ui/code-editor").then(m => ({ default: m.CodeMirrorEditor })));

type Skill = { name: string; path: string };
type Definition = {
  key: string;
  kind: "agent" | "base";
  name: string;
  description: string;
  instructions: string | null;
  inline_instructions: boolean;
  skills_folder: string | null;
  skills: Skill[];
};
type Published = { commit: string; ok: boolean; at: number; detail?: string };
export type Tree = {
  agents: Definition[];
  bases: Definition[];
  skills: { folder: string; skills: Skill[] }[];
  automations: { name: string; path: string }[];
  repository: string;
  main: string;
  live: string | null;
  uncommitted: string[];
  require_review: boolean;
  published: Published | null;
};
type Commit = { sha: string; message: string; author: string; date: string };
type Problems = Record<string, string[]>;

/** The new file a "New skill" or "New automation" starts from. */
export function template(path: string) {
  const parts = path.split("/");
  if (parts[0] === "automations") {
    return `---\ndescription: what the agent does\ntriggers:\n  - provider: schedule\n    schedule:\n      name: ${parts[1]}\n      cron: "0 9 * * 1-5"\n---\n\nWhat the agent should do when this runs.\n`;
  }
  return `---\nname: ${parts[parts.length - 2]}\ndescription: When to use this skill, in one sentence.\n---\n\nThe steps, in order.\n`;
}

/** What a file is, in the words the list uses. */
export function label(tree: Tree, path: string) {
  const owner = [...tree.agents, ...tree.bases].find(d => d.instructions === path);
  if (owner) return `${owner.name}: instructions`;
  const parts = path.split("/");
  if (parts[0] === "automations") return `Automation: ${humanize(parts[1] ?? "")}`;
  return `Skill: ${parts[parts.length - 2]}`;
}

function useDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

async function json<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const error = new Error(typeof detail === "string" ? detail : detail?.detail || fallback) as Error & { problems?: Problems; paths?: string[] };
    if (detail?.problems) error.problems = detail.problems;
    if (detail?.paths) error.paths = detail.paths;
    throw error;
  }
  return body as T;
}

/** The factory's agents, skills and automations, edited as files and saved as commits. */
export function AgentsApp({ path, navigate }: { path?: string; navigate: (path: string) => void }) {
  const [tree, setTree] = useState<Tree>();
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      setTree(await json<Tree>(await factoryFetch("/workplace/api/factory/definition"), "Unable to load the factory"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load the factory");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const open = (next: string) => navigate(`/agents/${encodeURIComponent(next)}`);
  const create = (folder: string, kind: "skill" | "automation") => {
    const name = window.prompt(kind === "skill" ? "Name the skill (lowercase, with hyphens)" : "Name the automation (lowercase, with hyphens)");
    if (!name) return;
    open(kind === "skill" ? `${folder}/${name.trim()}/SKILL.md` : `automations/${name.trim()}/automation.md`);
  };
  const setReview = async (require_review: boolean) => {
    try {
      await json(
        await factoryFetch("/workplace/api/factory/definition/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ require_review }),
        }),
        "Unable to change the setting",
      );
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to change the setting");
    }
  };

  if (error) return <div role="alert" className="p-6 text-sm">{error} <Button variant="ghost" onClick={() => void load()}>Retry</Button></div>;
  if (!tree) return <p className="p-6 text-sm text-muted-foreground">Loading the factory…</p>;

  const item = (file: string, text: string, icon?: ReactNode) => (
    <li key={file}>
      <button
        type="button"
        onClick={() => open(file)}
        aria-current={file === path ? "page" : undefined}
        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent ${file === path ? "bg-accent font-medium" : ""}`}
      >
        {icon ?? <FileText className="size-4 shrink-0 text-muted-foreground" />}
        <span className="truncate">{text}</span>
      </button>
    </li>
  );
  const definition = (d: Definition) => (
    <section key={d.key} aria-label={d.name} className="flex flex-col gap-1">
      <h3 className="flex items-center gap-2 px-2 text-xs font-semibold text-muted-foreground">
        {d.kind === "agent" && <AgentAvatar seed={d.key} size={16} />}
        {d.name}
      </h3>
      <ul>
        {d.instructions && item(d.instructions, "Instructions")}
        {d.skills.map(s => item(s.path, s.name))}
        {d.skills_folder && (
          <li>
            <button type="button" onClick={() => create(d.skills_folder!, "skill")} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-accent">
              <Plus className="size-4" />New skill
            </button>
          </li>
        )}
      </ul>
    </section>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <nav aria-label="Factory files" className="flex max-h-[40vh] shrink-0 flex-col gap-4 overflow-y-auto border-b p-3 md:max-h-none md:w-72 md:border-r md:border-b-0">
        <div className="px-2">
          <h1 className="text-lg font-semibold">Agents</h1>
          <p className="text-xs text-muted-foreground">What each agent is told and knows how to do. Saving publishes to the factory.</p>
        </div>
        {tree.agents.map(definition)}
        {(tree.bases.length > 0 || tree.skills.some(s => s.skills.length)) && (
          <section aria-label="Shared by every agent" className="flex flex-col gap-1">
            <h3 className="px-2 text-xs font-semibold text-muted-foreground">Shared by every agent</h3>
            <ul>
              {tree.bases.flatMap(b => [
                ...(b.instructions ? [item(b.instructions, `${b.name} instructions`)] : []),
                ...b.skills.map(s => item(s.path, s.name)),
              ])}
              {tree.skills.flatMap(folder => folder.skills.map(s => item(s.path, s.name)))}
              {tree.skills[0] && (
                <li>
                  <button type="button" onClick={() => create(tree.skills[0]!.folder, "skill")} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-accent">
                    <Plus className="size-4" />New shared skill
                  </button>
                </li>
              )}
            </ul>
          </section>
        )}
        <section aria-label="Automations" className="flex flex-col gap-1">
          <h3 className="px-2 text-xs font-semibold text-muted-foreground">Automations</h3>
          <ul>
            {tree.automations.map(a => item(a.path, humanize(a.name), <Workflow className="size-4 shrink-0 text-muted-foreground" />))}
            <li>
              <button type="button" onClick={() => create("automations", "automation")} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-accent">
                <Plus className="size-4" />New automation
              </button>
            </li>
          </ul>
        </section>
        <div className="mt-auto flex items-center justify-between gap-2 border-t px-2 pt-3">
          <label htmlFor="require-review" className="text-sm">
            Require review
            <span className="block text-xs text-muted-foreground">Saves become pull requests</span>
          </label>
          <Switch id="require-review" checked={tree.require_review} onCheckedChange={value => void setReview(value)} />
        </div>
      </nav>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Status tree={tree} />
        {path ? (
          <FileEditor key={path} path={path} tree={tree} reload={load} navigate={navigate} />
        ) : (
          <p className="p-6 text-sm text-muted-foreground">Choose an agent's instructions, a skill or an automation.</p>
        )}
      </div>
    </div>
  );
}

/** Whether the factory runs what is saved, and why not when it doesn't. */
function Status({ tree }: { tree: Tree }) {
  if (tree.uncommitted.length)
    return (
      <p role="status" className="border-b bg-amber-500/10 px-4 py-2 text-sm">
        The running factory has changes that aren't in its repository ({tree.uncommitted.slice(0, 3).join(", ")}
        {tree.uncommitted.length > 3 ? ` and ${tree.uncommitted.length - 3} more` : ""}). Saving is paused until they are committed.
      </p>
    );
  if (tree.live && tree.live !== tree.main)
    return (
      <p role="status" className="border-b bg-muted px-4 py-2 text-sm text-muted-foreground">
        {tree.published && !tree.published.ok && tree.published.commit === tree.main
          ? tree.published.detail
          : `The factory runs ${tree.live.slice(0, 7)}; the latest saved version is ${tree.main.slice(0, 7)}.`}
      </p>
    );
  return null;
}

function FileEditor({ path, tree, reload, navigate }: { path: string; tree: Tree; reload: () => Promise<void>; navigate: (path: string) => void }) {
  const dark = useDark();
  const [file, setFile] = useState<{ content: string; commit: string; exists: boolean }>();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const read = useCallback(async () => {
    try {
      const response = await factoryFetch(`/workplace/api/factory/definition/file?path=${encodeURIComponent(path)}`);
      if (response.status === 404) {
        const content = template(path);
        setFile({ content: "", commit: tree.main, exists: false });
        setDraft(content);
      } else {
        const body = await json<{ content: string; commit: string }>(response, "Unable to open the file");
        setFile({ ...body, exists: true });
        setDraft(body.content);
      }
      setError("");
      setProblems([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to open the file");
    }
  }, [path, tree.main]);
  useEffect(() => {
    void read();
    // Only on opening the file: a save reloads the tree, not the draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  const changed = !!file && (draft !== file.content || !file.exists);
  useEffect(() => {
    if (!changed) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [changed]);

  async function save(content: string | null) {
    if (!file) return;
    setBusy(true);
    setProblems([]);
    try {
      const result = await json<{ commit: string; published: boolean; detail?: string; pull_request?: string }>(
        await factoryFetch("/workplace/api/factory/definition/files", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ base_commit: file.commit, changes: [{ path, content }] }),
        }),
        "Unable to save",
      );
      if (result.pull_request) {
        toast.success("Proposed for review", { action: { label: "Open", onClick: () => window.open(result.pull_request, "_blank") } });
      } else if (result.published) {
        toast.success(content === null ? "Deleted. The factory no longer has it." : "Saved. The factory uses it from its next run.");
      } else {
        toast.warning(result.detail || "Saved");
      }
      await reload();
      if (content === null) navigate("/agents");
      else setFile({ content, commit: result.pull_request ? file.commit : result.commit, exists: true });
    } catch (e) {
      const err = e as Error & { problems?: Problems; paths?: string[] };
      if (err.problems) setProblems(Object.values(err.problems).flat());
      else toast.error(err.paths?.length ? `${err.message}: ${err.paths.slice(0, 3).join(", ")}` : err.message);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p role="alert" className="p-6 text-sm">{error}</p>;
  if (!file) return <p className="p-6 text-sm text-muted-foreground">Opening…</p>;
  const removable = !tree.agents.concat(tree.bases).some(d => d.instructions === path);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold" title={path}>
          {label(tree, path)}
          {!file.exists && <span className="ml-2 font-normal text-muted-foreground">(new)</span>}
        </h2>
        {file.exists && (
          <Button variant="ghost" size="sm" aria-pressed={historyOpen} onClick={() => setHistoryOpen(o => !o)}>
            <History />History
          </Button>
        )}
        {file.exists && removable && (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => window.confirm(`Delete ${label(tree, path)}? Its history is kept.`) && void save(null)}
          >
            <Trash2 />Delete
          </Button>
        )}
        <Button size="sm" disabled={busy || !changed} onClick={() => void save(draft)}>
          {tree.require_review ? "Propose" : "Save"}
        </Button>
      </header>
      {problems.length > 0 && (
        <ul role="alert" className="border-b bg-destructive/10 px-4 py-2 text-sm">
          {problems.map(p => <li key={p}>{p}</li>)}
        </ul>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="min-h-0 min-w-0 flex-1">
          <Suspense fallback={<p className="p-4 text-sm text-muted-foreground">Loading the editor…</p>}>
            <CodeMirrorEditor
              value={draft}
              onChange={setDraft}
              onSave={() => changed && !busy && void save(draft)}
              path={path}
              dark={dark}
              ariaLabel={label(tree, path)}
            />
          </Suspense>
        </div>
        {historyOpen && (
          <FileHistory
            path={path}
            onRestored={async () => {
              await reload();
              await read();
            }}
          />
        )}
      </div>
    </div>
  );
}

function FileHistory({ path, onRestored }: { path: string; onRestored: () => Promise<void> }) {
  const [commits, setCommits] = useState<Commit[]>();
  const [shown, setShown] = useState<{ sha: string; diff: string }>();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void factoryFetch(`/workplace/api/factory/definition/history?path=${encodeURIComponent(path)}`)
      .then(r => json<{ commits: Commit[] }>(r, "Unable to load the history"))
      .then(body => setCommits(body.commits))
      .catch(e => toast.error(e instanceof Error ? e.message : "Unable to load the history"));
  }, [path]);
  const show = async (sha: string) => {
    try {
      const body = await json<{ diff: string }>(
        await factoryFetch(`/workplace/api/factory/definition/diff?path=${encodeURIComponent(path)}&commit=${sha}`),
        "Unable to load the change",
      );
      setShown({ sha, diff: body.diff });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to load the change");
    }
  };
  const restore = async (sha: string) => {
    if (!window.confirm(`Put this file back as it was at ${sha.slice(0, 7)}? This saves a new version.`)) return;
    setBusy(true);
    try {
      const result = await json<{ published: boolean; detail?: string; pull_request?: string }>(
        await factoryFetch("/workplace/api/factory/definition/restore", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, commit: sha }),
        }),
        "Unable to restore",
      );
      if (result.pull_request) toast.success("Proposed for review");
      else if (result.published) toast.success("Restored. The factory uses it from its next run.");
      else toast.warning(result.detail || "Restored");
      await onRestored();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to restore");
    } finally {
      setBusy(false);
    }
  };
  return (
    <aside aria-label="History" className="flex w-80 shrink-0 flex-col overflow-y-auto border-l">
      {!commits ? (
        <p className="p-3 text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ol className="divide-y">
          {commits.map((c, index) => (
            <li key={c.sha} className="flex flex-col gap-1 p-3 text-sm">
              <button type="button" className="text-left hover:underline" onClick={() => void show(c.sha)}>{c.message}</button>
              <span className="text-xs text-muted-foreground">
                {c.author} · {new Date(c.date).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
                {index === 0 && " · current"}
              </span>
              {shown?.sha === c.sha && (
                <>
                  <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs">{shown.diff || "No change to this file."}</pre>
                  {index > 0 && (
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => void restore(c.sha)}>Restore this version</Button>
                  )}
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
