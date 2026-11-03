import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { ArrowLeft, History, MoreHorizontal, Plus } from "lucide-react";
import { toast } from "sonner";
import { AgentAvatar } from "@omniagents/chat-ui/components/omni-avatar/AgentAvatar";
import { Button } from "@omniagents/ui/button";
import { Checkbox } from "@omniagents/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@omniagents/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@omniagents/ui/dropdown-menu";
import { Input } from "@omniagents/ui/input";
import { Label } from "@omniagents/ui/label";
import { NativeSelect, NativeSelectOptGroup, NativeSelectOption } from "@omniagents/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@omniagents/ui/radio-group";
import { factoryFetch } from "./factory-scope";
import { API, json, loadTree, savedMessage, scopeLabel, send, type Definition, type DefinitionError, type EffectiveSkill, type Saved, type Tree } from "./definition-api";
import { VersionHistory } from "./VersionHistory";

/** A template's page: `/agents/template:<base>`. */
const TEMPLATE = "template:";

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
  const [adding, setAdding] = useState(false);
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
  // A template (a base agents extend) is `template:<key>`; anything else an agent.
  const template = agent?.startsWith(TEMPLATE) ? tree.bases.find(b => b.key === agent.slice(TEMPLATE.length)) : undefined;
  const current = template ?? tree.agents.find(a => a.key === agent) ?? tree.agents.find(a => a.chat) ?? tree.agents[0];
  if (!current) return <p className="p-6 text-sm">This factory has no agents.</p>;
  const name = (key: string) => tree.agents.find(a => a.key === key)?.name ?? key;
  const bases = (current.extends_chain ?? []).map(key => tree.bases.find(b => b.key === key)).filter((b): b is Definition => !!b);
  const sharedFolder = tree.skills[0]?.folder;
  const factorySkills: EffectiveSkill[] = tree.skills.flatMap(f => f.skills.map(s => ({ ...s, scope: { kind: "factory" as const }, replaces: [] })));
  const skills: EffectiveSkill[] = template
    ? [...template.skills.map(s => ({ ...s, scope: { kind: "base" as const, base: template.key, name: template.name }, replaces: [] })), ...factorySkills]
    : (current.effective_skills ?? []);
  const shared = (s: EffectiveSkill) => (template ? true : s.scope.kind !== "agent");
  // Where a new skill can go from this page, the first being the default.
  const homes: { label: string; folder: string }[] = template
    ? [
        ...(template.skills_folder ? [{ label: `Every agent built on ${template.name}`, folder: template.skills_folder }] : []),
        ...(sharedFolder && (template.agents?.length ?? 0) < tree.agents.length ? [{ label: "Every agent", folder: sharedFolder }] : []),
      ]
    : [
        ...(current.skills_folder ? [{ label: `Only ${current.name}`, folder: current.skills_folder }] : []),
        ...(sharedFolder ? [{ label: "Every agent", folder: sharedFolder }] : []),
      ];
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
          <NativeSelect aria-label="Agent" className="ml-auto" value={template ? TEMPLATE + template.key : current.key} onChange={e => navigate(`/agents/${encodeURIComponent(e.target.value)}`)}>
            <NativeSelectOptGroup label="In Chat">
              {chat.map(a => <NativeSelectOption key={a.key} value={a.key}>{a.name}</NativeSelectOption>)}
            </NativeSelectOptGroup>
            {others.length > 0 && (
              <NativeSelectOptGroup label="Other agents">
                {others.map(a => <NativeSelectOption key={a.key} value={a.key}>{a.name}</NativeSelectOption>)}
              </NativeSelectOptGroup>
            )}
            {tree.bases.length > 0 && (
              <NativeSelectOptGroup label="Templates">
                {tree.bases.map(b => <NativeSelectOption key={b.key} value={TEMPLATE + b.key}>{b.name} template</NativeSelectOption>)}
              </NativeSelectOptGroup>
            )}
          </NativeSelect>
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}><Plus />Add agent</Button>
        </div>
        {template ? (
          <div className="min-w-0">
            <h1 className="text-xl font-semibold">{template.name} template</h1>
            <p className="text-sm text-muted-foreground">
              What every agent built on it starts from: {(template.agents ?? []).map(name).join(", ") || "no agents yet"}. Changes apply to all of them.
            </p>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <AgentAvatar seed={current.key} size={40} />
            <div className="min-w-0">
              <h1 className="text-xl font-semibold">{current.name}</h1>
              {current.description && <p className="text-sm text-muted-foreground">{current.description}</p>}
            </div>
          </div>
        )}
        <Status tree={tree} />

        <section aria-label="Instructions" className="flex flex-col gap-2">
          <h2 className="font-semibold">Instructions</h2>
          {current.instructions ? (
            <FileEditor key={current.instructions} path={current.instructions} label={template ? `${template.name} template instructions` : `${current.name}'s instructions`} onSaved={() => void load()} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {current.inline_instructions ? "These instructions are in the agent's settings, which change with a release." : "This agent has no instructions of its own."}
            </p>
          )}
          {bases.map(base => (
            <p key={base.key} className="text-sm text-muted-foreground">
              {current.name} also follows the {base.name} template.{" "}
              <Button variant="link" size="sm" className="h-auto px-0" onClick={() => navigate(`/agents/${encodeURIComponent(TEMPLATE + base.key)}`)}>
                Edit the template
              </Button>
            </p>
          ))}
        </section>

        <section aria-label="Skills" className="flex flex-col gap-2">
          <div className="flex items-center">
            <h2 className="font-semibold">Skills</h2>
            <Button size="sm" variant="outline" className="ml-auto" onClick={() => setSkill("new")}><Plus />Add skill</Button>
          </div>
          {skills.length === 0 ? (
            <p className="text-sm text-muted-foreground">{template ? "No skills yet." : `${current.name} has no skills yet.`}</p>
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
                      {!template && s.scope.kind === "agent" && sharedFolder && (
                        <DropdownMenuItem onSelect={() => void move(s, sharedFolder, `Share skill ${s.name} with every agent`)}>Share with every agent</DropdownMenuItem>
                      )}
                      {!template && s.scope.kind !== "agent" && current.skills_folder && (
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
          owner={template ? `agents built on the ${template.name} template` : current.name}
          homes={homes}
          main={tree.main}
          agentName={name}
          onClose={() => setSkill(undefined)}
          onSaved={() => void load()}
        />
      )}
      {history && <VersionHistory path={history.path} title={`Skill: ${history.name}`} onClose={() => setHistory(undefined)} onRestored={() => void load()} />}
      {adding && (
        <AddAgentDialog
          templates={tree.bases}
          onClose={() => setAdding(false)}
          onAdded={key => {
            setAdding(false);
            navigate(`/agents/${encodeURIComponent(key)}`);
          }}
        />
      )}
    </div>
  );
}

/** A new agent: its name, what it does, the template it builds on, and whether Chat lists it. */
function AddAgentDialog({ templates, onClose, onAdded }: { templates: Definition[]; onClose: () => void; onAdded: (key: string) => void }) {
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [template, setTemplate] = useState(templates[0]?.key ?? "");
  const [chat, setChat] = useState(true);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const add = async () => {
    setBusy(true);
    setProblems([]);
    try {
      const saved = await send<Saved & { agent: string }>(
        "/agent",
        "POST",
        { key, description, template: template || null, chat },
        "Unable to add the agent",
      );
      const message = savedMessage(saved, chat ? "Added. It's in Chat now." : "Added.");
      toast[message.kind](message.text);
      if (saved.pull_request) onClose();
      else onAdded(saved.agent);
    } catch (e) {
      const err = e as DefinitionError;
      if (err.problems) setProblems(Object.values(err.problems).flat());
      else toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={open => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add an agent</DialogTitle>
          <DialogDescription>Give it a name and say what it does. You can write its instructions and skills next.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="agent-name">Name</Label>
            <Input id="agent-name" value={key} placeholder="designer" onChange={e => setKey(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="agent-description">What it does</Label>
            <Input id="agent-description" value={description} placeholder="Turns requests into UI designs." onChange={e => setDescription(e.target.value)} />
          </div>
          {templates.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="agent-template">Builds on</Label>
              <NativeSelect id="agent-template" value={template} onChange={e => setTemplate(e.target.value)}>
                {templates.map(t => <NativeSelectOption key={t.key} value={t.key}>{t.name} template</NativeSelectOption>)}
                <NativeSelectOption value="">No template</NativeSelectOption>
              </NativeSelect>
            </div>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={chat} onCheckedChange={value => setChat(value === true)} />
            People can chat with it
          </label>
          {problems.length > 0 && (
            <ul role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm">
              {problems.map(p => <li key={p}>{p}</li>)}
            </ul>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={busy || !/^[a-z][a-z0-9_]*$/.test(key) || !description.trim()} onClick={() => void add()}>Add agent</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** A skill as its fields: its name, when to use it, and its steps. */
function SkillDialog({
  skill,
  owner,
  homes,
  main,
  agentName,
  onClose,
  onSaved,
}: {
  skill?: EffectiveSkill;
  /** Whose page it opened from, in words: "Foreman", "agents built on the Base template". */
  owner: string;
  /** Where a new skill can go: who has it, and its folder; the first is the default. */
  homes: { label: string; folder: string }[];
  main: string;
  agentName: (key: string) => string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const dark = useDark();
  const [name, setName] = useState(skill?.name ?? "");
  const [home, setHome] = useState(homes[0]?.folder ?? "");
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
  const path = skill?.path ?? `${home}/${name.trim()}/SKILL.md`;
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
  return (
    <Dialog open onOpenChange={open => !open && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{skill ? skill.name : "Add a skill"}</DialogTitle>
          <DialogDescription>
            {skill
              ? skill.scope.kind === "agent"
                ? `Only ${owner} has this skill.`
                : `${scopeLabel(skill.scope, agentName)} uses this skill. Changes apply to all of them.`
              : `A skill is a procedure an agent follows when the task calls for it.`}
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
                {homes.length > 1 && (
                  <div className="flex flex-col gap-1.5">
                    <Label>Who has it</Label>
                    <RadioGroup value={home} onValueChange={setHome} className="flex flex-wrap gap-4">
                      {homes.map(h => (
                        <label key={h.folder} className="flex items-center gap-2 text-sm"><RadioGroupItem value={h.folder} />{h.label}</label>
                      ))}
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
