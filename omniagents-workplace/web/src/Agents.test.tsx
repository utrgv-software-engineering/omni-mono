// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { AgentsApp, label, template, type Tree } from "./Agents";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
vi.mock("@omniagents/chat-ui/components/omni-avatar/AgentAvatar", () => ({ AgentAvatar: () => <span data-avatar /> }));
// CodeMirror needs a real layout; a textarea stands in for it.
vi.mock("@omniagents/ui/code-editor", () => ({
  CodeMirrorEditor: ({ value, onChange, ariaLabel }: { value: string; onChange: (v: string) => void; ariaLabel: string }) => (
    <textarea aria-label={ariaLabel} value={value} onChange={e => onChange(e.target.value)} />
  ),
}));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });

const MAIN = "a".repeat(40);
const tree: Tree = {
  agents: [
    {
      key: "foreman",
      kind: "agent",
      name: "Foreman",
      description: "",
      instructions: "agents/foreman/instructions.md",
      inline_instructions: false,
      skills_folder: "agents/foreman/skills",
      skills: [{ name: "triage", path: "agents/foreman/skills/triage/SKILL.md" }],
    },
  ],
  bases: [
    { key: "base", kind: "base", name: "Base", description: "", instructions: "base/instructions.md", inline_instructions: false, skills_folder: "base/skills", skills: [] },
  ],
  skills: [{ folder: "skills", skills: [] }],
  automations: [{ name: "nightly-review", path: "automations/nightly-review/automation.md" }],
  repository: "acme/factory",
  main: MAIN,
  live: MAIN,
  uncommitted: [],
  require_review: false,
  published: null,
};

let root: Root;
let node: HTMLDivElement;
let answers: Record<string, () => { status?: number; body: unknown }>;
const fetcher = vi.fn(async (url: string, _options?: RequestInit) => {
  const key = Object.keys(answers).find(k => url.split("?")[0]!.endsWith(k))!;
  const { status = 200, body } = answers[key]!();
  return { ok: status < 400, status, json: async () => body };
});
const button = (name: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.trim() === name || b.getAttribute("aria-label") === name)!;
const render = async (path?: string) => {
  await act(async () => root.render(<AgentsApp path={path} navigate={() => {}} />));
  await act(async () => {});
};

beforeEach(() => {
  answers = {
    "/definition/file": () => ({ body: { path: "agents/foreman/instructions.md", content: "Before.\n", commit: MAIN } }),
    "/definition/files": () => ({ body: { commit: "b".repeat(40), published: true } }),
    "/definition/history": () => ({ body: { commits: [] } }),
    "/factory/definition": () => ({ body: tree }),
  };
  fetcher.mockClear();
  vi.mocked(toast.success).mockClear();
  vi.stubGlobal("fetch", fetcher);
  node = document.createElement("div");
  document.body.append(node);
  root = createRoot(node);
});
afterEach(() => {
  act(() => root.unmount());
  node.remove();
});

it("names files the way the list does, and starts new ones from a template", () => {
  expect(label(tree, "agents/foreman/instructions.md")).toBe("Foreman: instructions");
  expect(label(tree, "automations/nightly-review/automation.md")).toBe("Automation: Nightly review");
  expect(label(tree, "skills/notes/SKILL.md")).toBe("Skill: notes");
  expect(template("skills/notes/SKILL.md")).toContain("name: notes");
  expect(template("automations/weekly/automation.md")).toContain("name: weekly");
});

it("lists each agent's instructions and skills, the shared ones, and automations", async () => {
  await render();
  const nav = document.querySelector('nav[aria-label="Factory files"]')!;
  expect(nav.querySelector('section[aria-label="Foreman"]')!.textContent).toContain("Instructions");
  expect(nav.querySelector('section[aria-label="Foreman"]')!.textContent).toContain("triage");
  expect(nav.querySelector('section[aria-label="Shared by every agent"]')!.textContent).toContain("Base instructions");
  expect(nav.querySelector('section[aria-label="Automations"]')!.textContent).toContain("Nightly review");
});

it("saves an edit and says the factory uses it from its next run", async () => {
  await render("agents/foreman/instructions.md");
  const editor = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Foreman: instructions"]')!;
  expect(editor.value).toBe("Before.\n");
  expect(button("Save").disabled).toBe(true);
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(editor, "After.\n");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("Save").click());
  const save = fetcher.mock.calls.find(([url]) => url.includes("/definition/files"))!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({
    base_commit: MAIN,
    changes: [{ path: "agents/foreman/instructions.md", content: "After.\n" }],
  });
  expect(toast.success).toHaveBeenCalledWith("Saved. The factory uses it from its next run.");
});

it("shows what the factory says is wrong", async () => {
  answers["/definition/files"] = () => ({ status: 422, body: { detail: { problems: { x: ["Instructions are not a valid template"] } } } });
  await render("agents/foreman/instructions.md");
  const editor = document.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor, "{% if %}");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("Save").click());
  expect(document.querySelector('[role="alert"]')!.textContent).toContain("Instructions are not a valid template");
});

it("a new skill opens from its template, and review turns Save into Propose", async () => {
  answers["/definition/file"] = () => ({ status: 404, body: { detail: "No such file" } });
  answers["/factory/definition"] = () => ({ body: { ...tree, require_review: true } });
  await render("agents/foreman/skills/notes/SKILL.md");
  expect(document.querySelector("textarea")!.value).toContain("name: notes");
  expect(document.body.textContent).toContain("(new)");
  expect(button("Propose").disabled).toBe(false);
});

it("says when the running factory has changes that aren't committed", async () => {
  answers["/factory/definition"] = () => ({ body: { ...tree, uncommitted: ["project.yml"] } });
  await render();
  expect(document.querySelector('[role="status"]')!.textContent).toContain("project.yml");
});
