import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { ArrowLeft, History, MoreHorizontal, Plus } from "lucide-react";
import { toast } from "sonner";
import { AgentAvatar } from "@omniagents/chat-ui/components/omni-avatar/AgentAvatar";
import { Button } from "@omniagents/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@omniagents/ui/dropdown-menu";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import { NativeSelect, NativeSelectOptGroup, NativeSelectOption } from "@omniagents/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@omniagents/ui/radio-group";
import { factoryFetch } from "./factory-scope";
import { API, json, loadTree, savedMessage, scopeLabel, send, type Definition, type DefinitionError, type EffectiveSkill, type Saved, type Tree } from "./definition-api";
import { VersionHistory } from "./VersionHistory";

const CodeMirrorEditor = lazy(() => import("@omniagents/ui/code-editor").then(m => ({ default: m.CodeMirrorEditor })));

export function useDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** A Markdown editor for one file, saved as a version of the factory. */
export function FileEditor({ path, label, onSaved }: { path: string; label: string; onSaved?: () => void }) {
  const dark = useDark();
  const [file, setFile] = useState<{ content: string; commit: string }>();
  const [draft, setDraft] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(false);
  const read = useCallback(async () => {
    try {
      const body = await factoryFetch(`${API}/file?path=${encodeURIComponent(path)}`).then(r => json<{ content: string; commit: string }>(r, "Unable to open"));
      setFile(body);
      setDraft(body.content);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to open");
    }
  }, [path]);
  useEffect(() => {
    void read();
  }, [read]);
  const changed = !!file && draft !== file.content;
  const save = async () => {
    if (!file || !changed || busy) return;
    setBusy(true);
    setProblems([]);
    try {
      const saved = await send<Saved>("/files", "PUT", { base_commit: file.commit, changes: [{ path, content: draft }] }, "Unable to save");
      const message = savedMessage(saved);
      toast[message.kind](message.text);
      if (!saved.pull_request) setFile({ content: draft, commit: saved.commit });
      onSaved?.();
    } catch (e) {
      const err = e as DefinitionError;
      if (err.problems) setProblems(Object.values(err.problems).flat());
      else toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  if (!file) return <p className="py-4 text-sm text-muted-foreground">Opening…</p>;
  return (
    <div className="flex flex-col gap-2">
      <div className="h-80 overflow-hidden rounded-md border">
        <Suspense fallback={<p className="p-3 text-sm text-muted-foreground">Loading the editor…</p>}>
          <CodeMirrorEditor value={draft} onChange={setDraft} onSave={() => void save()} path={path} dark={dark} ariaLabel={label} />
        </Suspense>
      </div>
      {problems.length > 0 && (
        <ul role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm">
          {problems.map(p => <li key={p}>{p}</li>)}
        </ul>
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy || !changed} onClick={() => void save()}>Save</Button>
        {changed && <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDraft(file.content)}>Discard changes</Button>}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setHistory(true)}><History />Version history</Button>
      </div>
      {history && <VersionHistory path={path} title={label} onClose={() => setHistory(false)} onRestored={() => { void read(); onSaved?.(); }} />}
    </div>
  );
}

/** Problems that stop the factory running what is saved; nothing when all is well. */
function Status({ tree }: { tree: Tree }) {
  if (tree.uncommitted.length)
    return (
      <p role="status" className="rounded-md bg-amber-500/10 px-3 py-2 text-sm">
        The running factory has changes that aren't saved in its repository ({tree.uncommitted.slice(0, 3).join(", ")}
        {tree.uncommitted.length > 3 ? ` and ${tree.uncommitted.length - 3} more` : ""}). Saving waits until they are committed.
      </p>
    );
  if (tree.published && !tree.published.ok && tree.published.commit === tree.main)
    return <p role="status" className="rounded-md bg-muted px-3 py-2 text-sm">{tree.published.detail}</p>;
  return null;
}

/** An agent: what it's told and what it knows how to do. */
export function AgentsApp({ agent, navigate }: { agent?: string; navigate: (path: string) => void }) {
  const [tree, setTree] = useState<Tree>();
  const [error, setError] = useState("");
  const [skill, setSkill] = useState<EffectiveSkill | "new">();
  const [history, setHistory] = useState<EffectiveSkill>();
  const load = useCallback(async () => {
    try {
      setTree(await loadTree());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load the factory");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  if (error) return <div role="alert" className="p-6 text-sm">{error} <Button variant="ghost" onClick={() => void load()}>Retry</Button></div>;
  if (!tree) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>;
  const current = tree.agents.find(a => a.key === agent) ?? tree.agents.find(a => a.chat) ?? tree.agents[0];
  if (!current) return <p className="p-6 text-sm">This factory has no agents.</p>;
  const name = (key: string) => tree.agents.find(a => a.key === key)?.name ?? key;
  const bases = (current.extends_chain ?? []).map(key => tree.bases.find(b => b.key === key)).filter((b): b is Definition => !!b && !!b.instructions);
  const skills = current.effective_skills ?? [];
  const shared = (s: EffectiveSkill) => s.scope.kind !== "agent";
  const sharedFolder = tree.skills[0]?.folder;
  const move = async (s: EffectiveSkill, to: string, message: string) => {
    try {
      const saved = await send<Saved>("/skill/move", "POST", { base_commit: tree.main, path: s.path, to, message }, "Unable to move the skill");
      const result = savedMessage(saved);
      toast[result.kind](result.text);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to move the skill");
    }
  };
  const remove = async (s: EffectiveSkill) => {
    if (!window.confirm(`Delete the skill ${s.name}? ${shared(s) ? "Every agent that has it loses it. " : ""}Its history is kept.`)) return;
    try {
      const saved = await send<Saved>("/skill/delete", "POST", { base_commit: tree.main, path: s.path }, "Unable to delete");
      const result = savedMessage(saved, "Deleted.");
      toast[result.kind](result.text);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unable to delete");
    }
  };
  const chat = tree.agents.filter(a => a.chat);
  const others = tree.agents.filter(a => !a.chat);
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-6">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate("/personal")}><ArrowLeft />Chat</Button>
          <NativeSelect aria-label="Agent" className="ml-auto" value={current.key} onChange={e => navigate(`/agents/${encodeURIComponent(e.target.value)}`)}>
            <NativeSelectOptGroup label="In Chat">
              {chat.map(a => <NativeSelectOption key={a.key} value={a.key}>{a.name}</NativeSelectOption>)}
            </NativeSelectOptGroup>
            {others.length > 0 && (
              <NativeSelectOptGroup label="Other agents">
                {others.map(a => <NativeSelectOption key={a.key} value={a.key}>{a.name}</NativeSelectOption>)}
              </NativeSelectOptGroup>
            )}
          </NativeSelect>
        </div>
        <div className="flex items-center gap-3">
          <AgentAvatar seed={current.key} size={40} />
          <div className="min-w-0">
            <h1 className="text-xl font-semibold">{current.name}</h1>
            {current.description && <p className="text-sm text-muted-foreground">{current.description}</p>}
          </div>
        </div>
        <Status tree={tree} />

        <section aria-label="Instructions" className="flex flex-col gap-2">
          <h2 className="font-semibold">Instructions</h2>
          {current.instructions ? (
            <FileEditor key={current.instructions} path={current.instructions} label={`${current.name}'s instructions`} onSaved={() => void load()} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {current.inline_instructions ? "These instructions are in the agent's settings, which change with a release." : "This agent has no instructions of its own."}
            </p>
          )}
          {bases.map(base => (
            <details key={base.key} className="rounded-md border px-3 py-2">
              <summary className="cursor-pointer text-sm">{(base.agents?.length ?? 0) > 1 ? `Every agent built on ${base.name} also follows these instructions` : `${current.name} also follows ${base.name}'s instructions`}</summary>
              <p className="mt-2 text-sm text-muted-foreground">
                {(base.agents?.length ?? 0) > 1 ? `Changes apply to ${base.agents!.map(name).join(", ")}.` : ""}
              </p>
              <div className="mt-2">
                <FileEditor path={base.instructions!} label={`${base.name} instructions`} onSaved={() => void load()} />
              </div>
            </details>
          ))}
        </section>

        <section aria-label="Skills" className="flex flex-col gap-2">
          <div className="flex items-center">
            <h2 className="font-semibold">Skills</h2>
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => setSkill("new")}><Plus />Add skill</Button>
          </div>
          {skills.length === 0 ? (
            <p className="text-sm text-muted-foreground">{current.name} has no skills yet.</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {skills.map(s => (
                <li key={s.path} className="flex items-start gap-3 px-4 py-3">
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setSkill(s)}>
                    <span className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-sm font-medium hover:underline">{s.name}</span>
                      {shared(s) && <span className="text-xs text-muted-foreground">{scopeLabel(s.scope, name)}</span>}
                    </span>
                    {s.description && <span className="block text-sm text-muted-foreground">{s.description}</span>}
                    {s.replaces.map(r => (
                      <span key={r.path} className="block text-xs text-muted-foreground">
                        Replaces the skill {s.name} that {scopeLabel(r.scope, name).replace(/^Only /, "only ").replace(/^Every/, "every")} has
                      </span>
                    ))}
                  </button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label={`More for ${s.name}`}><MoreHorizontal /></Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {s.scope.kind === "agent" && sharedFolder && (
                        <DropdownMenuItem onSelect={() => void move(s, sharedFolder, `Share skill ${s.name} with every agent`)}>Share with every agent</DropdownMenuItem>
                      )}
                      {s.scope.kind !== "agent" && current.skills_folder && (
                        <DropdownMenuItem onSelect={() => void move(s, current.skills_folder!, `Make skill ${s.name} ${current.name}'s only`)}>Make it {current.name}'s only</DropdownMenuItem>
                      )}
                      <DropdownMenuItem onSelect={() => setHistory(s)}>Version history</DropdownMenuItem>
                      <DropdownMenuItem variant="destructive" onSelect={() => void remove(s)}>Delete</DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {skill && (
        <SkillDialog
          skill={skill === "new" ? undefined : skill}
          agent={current}
          sharedFolder={sharedFolder}
          main={tree.main}
          agentName={name}
          onClose={() => setSkill(undefined)}
          onSaved={() => void load()}
        />
      )}
      {history && <VersionHistory path={history.path} title={`Skill: ${history.name}`} onClose={() => setHistory(undefined)} onRestored={() => void load()} />}
    </div>
  );
}

/** A skill as its fields: its name, when to use it, and its steps. */
function SkillDialog({
  skill,
  agent,
  sharedFolder,
  main,
  agentName,
  onClose,
  onSaved,
}: {
  skill?: EffectiveSkill;
  agent: Definition;
  sharedFolder?: string;
  main: string;
  agentName: (key: string) => string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const dark = useDark();
  const [name, setName] = useState(skill?.name ?? "");
  const [scope, setScope] = useState<"agent" | "shared">("agent");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [body, setBody] = useState("");
  const [commit, setCommit] = useState(main);
  const [loaded, setLoaded] = useState(!skill);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!skill) return;
    void factoryFetch(`${API}/skill?path=${encodeURIComponent(skill.path)}`)
      .then(r => json<{ description: string; body: string; commit: string }>(r, "Unable to open the skill"))
      .then(found => {
        setDescription(found.description);
        setBody(found.body);
        setCommit(found.commit);
        setLoaded(true);
      })
      .catch(e => toast.error(e instanceof Error ? e.message : "Unable to open the skill"));
  }, [skill]);
  const folder = scope === "shared" ? sharedFolder : agent.skills_folder;
  const path = skill?.path ?? `${folder}/${name.trim()}/SKILL.md`;
  const save = async () => {
    setBusy(true);
    setProblems([]);
    try {
      const saved = await send<Saved>("/skill", "PUT", { base_commit: commit, path, description, body }, "Unable to save the skill");
      const message = savedMessage(saved);
      toast[message.kind](message.text);
      onSaved();
      onClose();
    } catch (e) {
      const err = e as DefinitionError;
      if (err.problems) setProblems(Object.values(err.problems).flat());
      else toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  const isShared = skill ? skill.scope.kind !== "agent" : scope === "shared";
  return (
    <Dialog open onOpenChange={open => !open && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{skill ? skill.name : "Add a skill"}</DialogTitle>
          <DialogDescription>
            {skill
              ? isShared
                ? `${scopeLabel(skill.scope, agentName)} uses this skill. Changes apply to all of them.`
                : `Only ${agent.name} has this skill.`
              : `A skill is a procedure ${agent.name} follows when the task calls for it.`}
          </DialogDescription>
        </DialogHeader>
        {!loaded ? (
          <p className="text-sm text-muted-foreground">Opening…</p>
        ) : (
          <div className="flex flex-col gap-4">
            {!skill && (
              <>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="skill-name">Name</Label>
                  <Input id="skill-name" value={name} placeholder="release-checklist" onChange={e => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} />
                </div>
                {sharedFolder && agent.skills_folder && (
                  <div className="flex flex-col gap-1.5">
                    <Label>Who has it</Label>
                    <RadioGroup value={scope} onValueChange={value => setScope(value as "agent" | "shared")} className="flex gap-4">
                      <label className="flex items-center gap-2 text-sm"><RadioGroupItem value="agent" />Only {agent.name}</label>
                      <label className="flex items-center gap-2 text-sm"><RadioGroupItem value="shared" />Every agent</label>
                    </RadioGroup>
                  </div>
                )}
              </>
            )}
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="skill-description">When to use it</Label>
              <Input id="skill-description" value={description} placeholder="When reviewing a release, before tagging it." onChange={e => setDescription(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Steps</Label>
              <div className="h-72 overflow-hidden rounded-md border">
                <Suspense fallback={<p className="p-3 text-sm text-muted-foreground">Loading the editor…</p>}>
                  <CodeMirrorEditor value={body} onChange={setBody} onSave={() => void save()} path="SKILL.md" dark={dark} ariaLabel="Steps" />
                </Suspense>
              </div>
            </div>
            {problems.length > 0 && (
              <ul role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm">
                {problems.map(p => <li key={p}>{p}</li>)}
              </ul>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={busy || !loaded || !description.trim() || (!skill && !name.trim())} onClick={() => void save()}>
            {skill ? "Save" : "Add skill"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
