// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { AgentsApp } from "./Agents";
import { scopeLabel, type Tree } from "./definition-api";

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
const factory = { kind: "factory" as const };
const tree: Tree = {
  agents: [
    {
      key: "foreman",
      kind: "agent",
      name: "Foreman",
      description: "Runs the line.",
      instructions: "agents/foreman/instructions.md",
      inline_instructions: false,
      skills_folder: "agents/foreman/skills",
      skills: [],
      extends: "base",
      chat: true,
      extends_chain: ["base"],
      effective_skills: [
        {
          name: "github",
          path: "agents/foreman/skills/github/SKILL.md",
          description: "Foreman's GitHub.",
          scope: { kind: "agent", agent: "foreman" },
          replaces: [{ path: "skills/github/SKILL.md", scope: factory }],
        },
        { name: "canvas", path: "skills/canvas/SKILL.md", description: "Draw.", scope: factory, replaces: [] },
      ],
    },
    { key: "implementer", kind: "agent", name: "Implementer", description: "", instructions: null, inline_instructions: true, skills_folder: "agents/implementer/skills", skills: [], extends: null, chat: false, extends_chain: [], effective_skills: [] },
  ],
  bases: [{ key: "base", kind: "base", name: "Base", description: "", instructions: "base/instructions.md", inline_instructions: false, skills_folder: "base/skills", skills: [], extends: null, agents: ["foreman", "implementer"] }],
  skills: [{ folder: "skills", skills: [{ name: "canvas", path: "skills/canvas/SKILL.md", description: "Draw." }] }],
  automations: [],
  repository: "acme/factory",
  main: MAIN,
  live: MAIN,
  uncommitted: [],
  require_review: false,
  published: null,
};

const settings = {
  key: "foreman",
  path: "agents/foreman/agent.yml",
  label: "",
  description: "Runs the line.",
  model: null,
  capabilities: ["shell", "web"],
  calls: ["implementer"],
  chat: true,
  chat_default: true,
  automation_default: true,
  inherited: { capabilities: ["shell"], model: "gpt-6-astra" },
  called_by: [],
  options: {
    models: [{ id: "gpt-6-astra", label: "GPT-6-Astra" }, { id: "gpt-x", label: "GPT-X" }],
    capabilities: [{ name: "shell", description: "" }, { name: "web", description: "" }, { name: "canvas", description: "" }],
    agents: [{ key: "implementer", name: "Implementer" }],
  },
};

let root: Root;
let node: HTMLDivElement;
let answers: Record<string, () => { status?: number; body: unknown }>;
const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
  const path = url.split("?")[0]!;
  const method = options?.method ?? "GET";
  // "PUT /definition/..." answers only that method; a bare path answers any.
  const key =
    Object.keys(answers).find(k => k.startsWith(method + " ") && path.endsWith(k.slice(method.length + 1))) ??
    Object.keys(answers).find(k => !k.includes(" ") && path.endsWith(k))!;
  const { status = 200, body } = answers[key]!();
  return { ok: status < 400, status, json: async () => body };
});
const button = (name: string) =>
  [...document.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent?.trim() === name || b.getAttribute("aria-label") === name)!;
const type = async (element: HTMLTextAreaElement | HTMLInputElement, value: string) =>
  act(async () => {
    const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
const navigate = vi.fn();
const render = async (agent?: string) => {
  await act(async () => root.render(<AgentsApp key={agent} agent={agent} navigate={navigate} />));
  await act(async () => {});
  await act(async () => {});
};

beforeEach(() => {
  answers = {
    "/definition/file": () => ({ body: { content: "Before.\n", commit: MAIN } }),
    "/definition/files": () => ({ body: { commit: "b".repeat(40), published: true } }),
    "/definition/skill": () => ({ body: { description: "Foreman's GitHub.", body: "Steps.\n", commit: MAIN, published: true } }),
    "/definition/skill/move": () => ({ body: { commit: "c".repeat(40), published: true } }),
    "/factory/definition": () => ({ body: tree }),
    "/definition/agents/foreman": () => ({ body: settings }),
    "/definition/agents/implementer": () => ({ body: { ...settings, key: "implementer", path: "agents/implementer/agent.yml", chat: false, chat_default: false, automation_default: false } }),
  };
  fetcher.mockClear();
  navigate.mockClear();
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

it("says who has a skill in words", () => {
  const name = (key: string) => ({ foreman: "Foreman" })[key] ?? key;
  expect(scopeLabel({ kind: "agent", agent: "foreman" }, name)).toBe("Only Foreman");
  expect(scopeLabel({ kind: "base", base: "base", name: "Base" }, name)).toBe("Every agent built on Base");
  expect(scopeLabel(factory, name)).toBe("Every agent");
  expect(scopeLabel({ kind: "repository", repository: "webapp" }, name)).toBe("From webapp");
});

it("shows one agent: its instructions, the shared ones collapsed, and its skills with who has them", async () => {
  await render("foreman");
  expect(document.querySelector("h1")!.textContent).toBe("Foreman");
  expect(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Foreman\'s instructions"]')!.value).toBe("Before.\n");
  expect(document.body.textContent).toContain("Foreman also follows the Base template.");
  await act(async () => button("Edit the template").click());
  expect(navigate).toHaveBeenCalledWith("/agents/template%3Abase");
  const skills = document.querySelector('section[aria-label="Skills"]')!.textContent!;
  expect(skills).toContain("canvas");
  expect(skills).toContain("Every agent");
  expect(skills).toContain("Replaces the skill github that every agent has");
  // No file tree, no commit hashes, no review switch.
  expect(document.body.textContent).not.toContain(MAIN.slice(0, 7));
  expect(document.body.textContent).not.toContain("Require review");
});

it("saves instructions and says the next run uses them", async () => {
  await render("foreman");
  const editor = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Foreman\'s instructions"]')!;
  await type(editor, "After.\n");
  await act(async () => button("Save").click());
  const save = fetcher.mock.calls.find(([url]) => url.endsWith("/definition/files"))!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({
    base_commit: MAIN,
    changes: [{ path: "agents/foreman/instructions.md", content: "After.\n" }],
  });
  expect(toast.success).toHaveBeenCalledWith("Saved. The factory uses it from its next run.");
});

it("adds a skill for every agent from its fields", async () => {
  await render("foreman");
  await act(async () => button("Add skill").click());
  await type(document.querySelector<HTMLInputElement>("#skill-name")!, "Release Notes");
  expect(document.querySelector<HTMLInputElement>("#skill-name")!.value).toBe("release-notes");
  await act(async () => document.querySelector<HTMLButtonElement>('[role="radio"][value="skills"]')!.click());
  await type(document.querySelector<HTMLInputElement>("#skill-description")!, "When writing release notes.");
  await type(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Steps"]')!, "1. List the changes.");
  await act(async () => [...document.querySelectorAll<HTMLButtonElement>("button")].filter(b => b.textContent === "Add skill").pop()!.click());
  const save = fetcher.mock.calls.find(([url, options]) => url.endsWith("/definition/skill") && options?.method === "PUT")!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({
    base_commit: MAIN,
    path: "skills/release-notes/SKILL.md",
    description: "When writing release notes.",
    body: "1. List the changes.",
  });
});

it("a shared skill warns that changes apply to every agent", async () => {
  await render("foreman");
  const canvas = [...document.querySelectorAll<HTMLButtonElement>('section[aria-label="Skills"] button')].find(b => b.textContent?.startsWith("canvas"))!;
  await act(async () => canvas.click());
  await act(async () => {});
  expect(document.body.textContent).toContain("Every agent uses this skill. Changes apply to all of them.");
});

it("says when the running factory has changes that aren't saved, and nothing otherwise", async () => {
  await render("foreman");
  expect(document.querySelector('[role="status"]')).toBeNull();
  answers["/factory/definition"] = () => ({ body: { ...tree, uncommitted: ["project.yml"] } });
  await render("implementer");
  expect(document.querySelector('[role="status"]')!.textContent).toContain("project.yml");
  expect(document.body.textContent).toContain("These instructions are in the agent's settings");
});

it("edits a template on its own page, saying which agents it changes", async () => {
  await render("template:base");
  expect(document.querySelector("h1")!.textContent).toBe("Base template");
  expect(document.body.textContent).toContain("What every agent built on it starts from: Foreman, Implementer. Changes apply to all of them.");
  expect(document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Base template instructions"]')!.value).toBe("Before.\n");
  const reads = fetcher.mock.calls.filter(([url]) => url.includes("/definition/file?path=")).map(([url]) => url);
  expect(reads).toEqual([expect.stringContaining("path=base%2Finstructions.md"), expect.stringContaining("path=base%2Fagent.yml")]);
  expect(document.querySelector('section[aria-label="Settings"]')!.textContent).toContain("every agent built on this template starts with");
  const skills = document.querySelector('section[aria-label="Skills"]')!.textContent!;
  expect(skills).toContain("canvas");
  expect(document.querySelector<HTMLSelectElement>('select[aria-label="Agent"]')!.value).toBe("template:base");
});

it("adds an agent in Chat built on the template, then opens it", async () => {
  answers["/definition/agent"] = () => ({ body: { commit: "d".repeat(40), published: true, agent: "designer" } });
  await render("foreman");
  await act(async () => button("Add agent").click());
  await type(document.querySelector<HTMLInputElement>("#agent-name")!, "Designer");
  expect(document.querySelector<HTMLInputElement>("#agent-name")!.value).toBe("designer");
  await type(document.querySelector<HTMLInputElement>("#agent-description")!, "Turns requests into designs.");
  await act(async () => [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(b => b.textContent === "Add agent")!.click());
  const add = fetcher.mock.calls.find(([url]) => url.endsWith("/definition/agent"))!;
  expect(JSON.parse(add[1]!.body as string)).toEqual({ key: "designer", description: "Turns requests into designs.", template: "base", chat: true });
  expect(toast.success).toHaveBeenCalledWith("Added. It's in Chat now.");
  expect(navigate).toHaveBeenCalledWith("/agents/designer");
});

it("changes an agent's settings as fields, sending only what changed", async () => {
  answers["/definition/agents/foreman"] = () => ({ body: settings });
  await render("foreman");
  const section = document.querySelector('section[aria-label="Settings"]')!;
  expect(section.textContent).toContain("The template's (gpt-6-astra)");
  expect(section.textContent).toContain("shell · from the template");
  const model = section.querySelector<HTMLSelectElement>("#agent-model")!;
  await act(async () => {
    model.value = "gpt-x";
    model.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const canvas = [...section.querySelectorAll("label")].find(l => l.textContent?.startsWith("canvas"))!.querySelector<HTMLButtonElement>('[role="checkbox"]')!;
  await act(async () => canvas.click());
  const shell = [...section.querySelectorAll("label")].find(l => l.textContent?.startsWith("shell"))!.querySelector<HTMLButtonElement>('[role="checkbox"]')!;
  await act(async () => shell.click());
  answers["PUT /definition/agents/foreman"] = () => ({ body: { commit: "e".repeat(40), published: true } });
  await act(async () => button("Save settings").click());
  const save = fetcher.mock.calls.find(([url, options]) => url.endsWith("/definition/agents/foreman") && options?.method === "PUT")!;
  expect(JSON.parse(save[1]!.body as string)).toEqual({ model: "gpt-x", capabilities: ["web", "canvas"] }); // shell, from the template, off
  expect(toast.success).toHaveBeenCalledWith("Saved. It works this way from its next run.");
});

it("won't delete Chat's default agent, and deletes another", async () => {
  await render("foreman");
  expect(button("Delete agent").disabled).toBe(true);
  expect(document.body.textContent).toContain("Make another agent the default before deleting this one.");
  await render("implementer");
  vi.stubGlobal("confirm", () => true);
  answers["DELETE /definition/agents/implementer"] = () => ({ body: { commit: "f".repeat(40), published: true } });
  await act(async () => {});
  expect(button("Delete agent").disabled).toBe(false);
  await act(async () => button("Delete agent").click());
  expect(fetcher.mock.calls.some(([url, options]) => url.endsWith("/definition/agents/implementer") && options?.method === "DELETE")).toBe(true);
  expect(navigate).toHaveBeenCalledWith("/agents");
});
